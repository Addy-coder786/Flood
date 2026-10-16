// Guided-briefing voice: neural TTS first, browser speech only as a fallback.
//
// PIPELINE POSITION — this layer converts, it never authors:
//     real FloodNet state -> facts.js -> narration.js -> [voice.js] -> audio -> guided UI action
// It receives sentences that were already built from real simulation values and hands them to a voice. It
// cannot change a depth, a street name, an alert or a route, and there is no model in this path.
//
// PREFERRED PATH — POST /api/tts (Google Cloud Text-to-Speech, proxied by the backend so the API key stays
// server-side; see backend/floodnet/tts.py). Returns MP3, played through a single <audio> element.
//
// FALLBACK — browser SpeechSynthesis (lib/briefing/speech.js). Used only when the neural service is
// unconfigured or fails. The fallback never changes the visual briefing's behaviour.
//
// The whole module degrades to "no audio at all" without breaking the briefing: `speak()` always resolves,
// and it reports which mode actually produced sound so the UI can show an honest voice state.
import { apiUrl } from '../../api/client.js'
import { speak as browserSpeak, cancelSpeech, pauseSpeech, resumeSpeech, speechAvailable, hasVoiceFor } from './speech.js'

export const VOICE_MODE = {
  NEURAL: 'neural',
  BROWSER: 'browser',
  NONE: 'none',
  // The requested LANGUAGE specifically has no voice: the neural service has none for it and the OS has
  // none installed either. Distinct from NONE (no speech capability at all) because the honest message to
  // the user is different, and because the one thing we must never do is read Hindi/Marathi text aloud in
  // an English voice and call it Hindi/Marathi.
  LANG_UNAVAILABLE: 'lang-unavailable',
}

// Per-session cache so a sentence repeated inside one briefing (or on Back/replay) is synthesized once.
// Deliberately session-scoped and small: `reset()` revokes every blob URL, so this never grows into a
// permanent audio library.
const cache = new Map() // `${lang}|${text}` -> objectURL
let currentAudio = null
let neuralDisabledForSession = false

/** Ask the backend whether a neural voice is configured. Never throws. */
export async function neuralStatus() {
  try {
    const res = await fetch(apiUrl('/api/tts/status'))
    if (!res.ok) return { available: false, reason: `status ${res.status}` }
    return await res.json()
  } catch {
    return { available: false, reason: 'backend unreachable' }
  }
}

async function synthesizeNeural(text, lang) {
  const key = `${lang}|${text}`
  const cached = cache.get(key)
  if (cached) return cached

  const res = await fetch(apiUrl('/api/tts'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, lang }),
  })
  if (!res.ok) {
    // 503 = not configured / upstream failed. Stop retrying for the rest of this session rather than
    // paying a failed round-trip before every single sentence.
    if (res.status === 503) neuralDisabledForSession = true
    throw new Error(`tts ${res.status}`)
  }
  const blob = await res.blob()
  if (!blob || blob.size === 0) throw new Error('empty audio')
  const url = URL.createObjectURL(blob)
  cache.set(key, url)
  return url
}

function playUrl(url) {
  return new Promise((resolve) => {
    let settled = false
    const done = (ok) => {
      if (settled) return
      settled = true
      resolve(ok)
    }
    try {
      const audio = new Audio(url)
      currentAudio = audio
      audio.onended = () => done(true)
      audio.onerror = () => done(false)
      // Autoplay policies can reject playback that was not user-initiated. The briefing IS started by a
      // click, so this normally succeeds; if it is rejected we fail over rather than hang.
      audio.play().catch(() => done(false))
    } catch {
      done(false)
    }
  })
}

/**
 * Speak one sentence. Resolves when audio finishes (or immediately if nothing could speak).
 * Returns the VOICE_MODE that actually produced sound, so the caller can display an honest state.
 */
export async function speak(text, { lang = 'en', langTag = 'en-IN' } = {}) {
  if (!text) return VOICE_MODE.NONE

  if (!neuralDisabledForSession) {
    try {
      const url = await synthesizeNeural(text, lang)
      const ok = await playUrl(url)
      if (ok) return VOICE_MODE.NEURAL
      // Audio element failed (decode/autoplay). Fall through to browser speech for this sentence.
    } catch {
      /* fall through to the browser voice */
    }
  }

  if (speechAvailable()) {
    // browserSpeak() now refuses when no voice exists for `langTag`, rather than letting the platform
    // default (English) read the text. Distinguish that case from "speech is broken" so the UI can say
    // which actually happened.
    const ok = await browserSpeak(text, langTag)
    if (ok) return VOICE_MODE.BROWSER
    return (await hasVoiceFor(langTag)) ? VOICE_MODE.NONE : VOICE_MODE.LANG_UNAVAILABLE
  }
  return VOICE_MODE.NONE
}

export function pause() {
  try { currentAudio?.pause() } catch { /* best-effort */ }
  pauseSpeech()
}

export function resume() {
  try { currentAudio?.play?.().catch(() => {}) } catch { /* best-effort */ }
  resumeSpeech()
}

/** Stop everything immediately — used by Exit, by Next/Back, and when a new briefing starts. */
export function cancel() {
  try {
    if (currentAudio) {
      currentAudio.pause()
      currentAudio.onended = null
      currentAudio.onerror = null
      currentAudio.src = ''
    }
  } catch {
    /* best-effort */
  }
  currentAudio = null
  cancelSpeech()
}

/** Drop the session cache and release every blob URL. Called when a briefing ends. */
export function reset() {
  cancel()
  for (const url of cache.values()) {
    try { URL.revokeObjectURL(url) } catch { /* best-effort */ }
  }
  cache.clear()
  neuralDisabledForSession = false
}
