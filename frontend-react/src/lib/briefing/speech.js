// Thin wrapper over the browser's built-in SpeechSynthesis. No TTS backend, no third-party dependency —
// the app had neither and the brief asked not to add one.
//
// Everything here degrades silently: if speech is unavailable, blocked by autoplay policy, or the requested
// voice is missing, `speak()` resolves immediately and the visual briefing continues unaffected. Speech is
// an enhancement to the guided briefing, never a requirement for it.

export function speechAvailable() {
  return typeof window !== 'undefined'
    && 'speechSynthesis' in window
    && typeof window.SpeechSynthesisUtterance === 'function'
}

/** Voices load asynchronously in most browsers; resolve once they are there (or give up quickly). */
function getVoices(timeoutMs = 1200) {
  return new Promise((resolve) => {
    if (!speechAvailable()) return resolve([])
    const existing = window.speechSynthesis.getVoices()
    if (existing?.length) return resolve(existing)
    let done = false
    const finish = () => {
      if (done) return
      done = true
      resolve(window.speechSynthesis.getVoices() || [])
    }
    window.speechSynthesis.addEventListener?.('voiceschanged', finish, { once: true })
    setTimeout(finish, timeoutMs)
  })
}

/**
 * Best available voice for a BCP-47 tag: exact match (hi-IN), then the same base language (hi-*), then
 * NOTHING.
 *
 * It deliberately does NOT fall back to a different language. Leaving `utterance.voice` unset makes the
 * browser read the text with its DEFAULT voice — which on most installs is English — so Hindi or Marathi
 * text would be spoken by an English voice while the UI claimed the selected language. That silent
 * substitution was a real defect; `speak()` below now refuses instead, and reports it to the caller.
 */
async function pickVoice(langTag) {
  const voices = await getVoices()
  if (!voices.length) return null
  const base = String(langTag).split('-')[0].toLowerCase()
  const norm = (v) => String(v?.lang || '').replace('_', '-').toLowerCase()
  return voices.find((v) => norm(v) === langTag.toLowerCase())
    || voices.find((v) => norm(v).startsWith(base + '-'))
    || voices.find((v) => norm(v) === base)
    || null
}

/** Is a voice for this language actually installed? Lets callers check before promising audio. */
export async function hasVoiceFor(langTag) {
  return Boolean(await pickVoice(langTag))
}

export function cancelSpeech() {
  if (!speechAvailable()) return
  try {
    window.speechSynthesis.cancel()
  } catch {
    /* nothing to do — cancelling is always best-effort */
  }
}

/**
 * Speak `text`, resolving when it finishes, is cancelled, or fails.
 * Resolves `true` if it actually spoke to completion, `false` otherwise, so a caller can decide whether to
 * fall back to a timed advance.
 */
export function speak(text, langTag = 'en-IN', { rate = 0.98, pitch = 1 } = {}) {
  if (!speechAvailable() || !text) return Promise.resolve(false)

  return new Promise((resolve) => {
    let settled = false
    const done = (ok) => {
      if (settled) return
      settled = true
      resolve(ok)
    }

    pickVoice(langTag).then((voice) => {
      if (settled) return
      // No voice for the requested language: refuse rather than let the browser read this text in its
      // default (usually English) voice. Returning false lets the caller report "voice unavailable for
      // this language" instead of playing English audio under a Hindi/Marathi label.
      if (!voice) {
        done(false)
        return
      }
      try {
        cancelSpeech() // a new utterance always replaces the previous one
        const u = new window.SpeechSynthesisUtterance(text)
        u.lang = langTag
        u.voice = voice
        u.rate = rate
        u.pitch = pitch
        u.onend = () => done(true)
        u.onerror = () => done(false)
        window.speechSynthesis.speak(u)

        // Safety net: some browsers never fire onend for long utterances, or silently drop the queue when
        // the tab loses focus. Estimate a generous ceiling from the text length so a briefing step can
        // never hang forever waiting on speech.
        const ceilingMs = Math.min(60000, 2500 + text.length * 95)
        setTimeout(() => done(false), ceilingMs)
      } catch {
        done(false)
      }
    }).catch(() => done(false))
  })
}

export function pauseSpeech() {
  if (!speechAvailable()) return
  try { window.speechSynthesis.pause() } catch { /* best-effort */ }
}

export function resumeSpeech() {
  if (!speechAvailable()) return
  try { window.speechSynthesis.resume() } catch { /* best-effort */ }
}
