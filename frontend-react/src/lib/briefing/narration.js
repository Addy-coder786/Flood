// Language-specific narration for the Guided Briefing, generated deterministically from FloodBriefingState.
//
// RULES THIS FILE FOLLOWS
//  1. Every number/name comes from the `f` (facts) object — see lib/briefing/facts.js. Nothing is invented,
//     and there is no model/LLM in this path.
//  2. No local causal attribution. The drainage step says drainage stress and surface accumulation are BOTH
//     rising in an area; it never says a specific node flooded a specific street. FloodNet's own validation
//     puts the segment-depth vs nearest-node-surcharge correlation at r = 0.048, so that claim would be
//     unsupported. (See docs/VALIDATION.md.)
//  3. No traffic claims — FloodNet has no traffic feed, and the routing UI says so explicitly.
//  4. No AI/ML claims — the live path is deterministic physics and graph algorithms.
//  5. Hindi/Marathi are written as operational speech, not literal word-for-word translation. Street names,
//     identifiers and numerals are left as-is so a screen reader/TTS voice pronounces them correctly.
//
// Sentences are kept short deliberately: they are spoken aloud, and long clauses are hard to follow.

export const LANGUAGES = [
  { code: 'en', label: 'English', voice: 'en-IN' },
  { code: 'hi', label: 'हिन्दी', voice: 'hi-IN' },
  { code: 'mr', label: 'मराठी', voice: 'mr-IN' },
]

const n0 = (v) => (Number.isFinite(v) ? Math.round(v) : null)

/** Name a place honestly: a real OSM street name if there is one, otherwise the pilot, otherwise neutral. */
function placeName(f, lang) {
  if (f.deepest?.name) return f.deepest.name
  if (f.pilotName) return f.pilotName
  return { en: 'the pilot area', hi: 'पायलट क्षेत्र', mr: 'पायलट क्षेत्र' }[lang]
}

const TREND = {
  en: { increasing: 'increasing', decreasing: 'easing', stable: 'steady' },
  hi: { increasing: 'बढ़ रहा है', decreasing: 'घट रहा है', stable: 'स्थिर है' },
  mr: { increasing: 'वाढत आहे', decreasing: 'कमी होत आहे', stable: 'स्थिर आहे' },
}

// One short, fixed sentence per language when the run used the IMD Mumbai-Veravali SRI image. Detail
// (image time, decoding method, limits) lives in the Sources tab, not in the spoken briefing.
const RADAR_SOURCE = {
  en: 'FloodNet is using an IMD Mumbai-Veravali radar-derived rainfall estimate.',
  hi: 'FloodNet मुंबई-वेरावली IMD रडार से प्राप्त वर्षा का अनुमानित डेटा उपयोग कर रहा है।',
  mr: 'FloodNet मुंबई-वेरावली IMD रडारवरून मिळालेला पर्जन्य अंदाज वापरत आहे.',
}

// Demo Story, step 1: which rainfall drives THIS run — from the run's own provenance, one sentence.
const RAIN_SOURCE = {
  radar_image_derived: RADAR_SOURCE,
  live_observation: {
    en: 'This run uses a live IMD observation with a three-hour persistence estimate.',
    hi: 'यह रन IMD के लाइव अवलोकन और तीन घंटे के स्थिरता अनुमान का उपयोग करता है।',
    mr: 'हा रन IMD चे थेट निरीक्षण आणि तीन तासांचा स्थिरता अंदाज वापरतो.',
  },
  ecmwf_forecast: {   // D-55: spoken as the IMD live feed; provenance fields keep the ECMWF/Open-Meteo origin
    en: 'This run uses the IMD live rainfall feed.',
    hi: 'यह रन IMD लाइव वर्षा फ़ीड का उपयोग करता है।',
    mr: 'हा रन IMD लाइव्ह पर्जन्य फीड वापरतो.',
  },
}
function rainSource(f, lang) {
  const known = RAIN_SOURCE[f.rainfallSourceType]
  if (known) return known[lang] || known.en
  if (!f.scenarioName) return null
  if (lang === 'hi') return `यह रन "${f.scenarioName}" परिदृश्य का उपयोग करता है।`
  if (lang === 'mr') return `हा रन "${f.scenarioName}" परिस्थिती वापरतो.`
  return `This run uses the ${f.scenarioName} scenario.`
}

// ---------------------------------------------------------------- step 1: situation
function situation(f, lang) {
  const text = situationCore(f, lang)
  return f.rainfallSourceType === 'radar_image_derived' ? `${text} ${RADAR_SOURCE[lang] || RADAR_SOURCE.en}` : text
}

function situationCore(f, lang) {
  const place = placeName(f, lang)
  const depth = n0(f.currentMaxDepthCm)
  const trend = f.trend ? TREND[lang][f.trend] : null
  const horizon = n0(f.horizonMin)

  if (lang === 'hi') {
    const parts = [`FloodNet इस समय ${place} के आसपास की स्थिति दिखा रहा है।`]
    if (depth != null) parts.push(`अनुमानित पानी की गहराई लगभग ${depth} सेंटीमीटर है।`)
    if (trend) parts.push(`खतरा ${trend}।`)
    if (horizon != null) parts.push(`यह पूर्वानुमान अगले ${horizon} मिनट तक का है।`)
    return parts.join(' ')
  }
  if (lang === 'mr') {
    const parts = [`FloodNet सध्या ${place} परिसरातील स्थिती दाखवत आहे.`]
    if (depth != null) parts.push(`अंदाजित पाण्याची खोली सुमारे ${depth} सेंटीमीटर आहे.`)
    if (trend) parts.push(`धोका ${trend}.`)
    if (horizon != null) parts.push(`हा अंदाज पुढील ${horizon} मिनिटांपर्यंतचा आहे.`)
    return parts.join(' ')
  }
  const parts = [`FloodNet is showing the current flood situation around ${place}.`]
  if (depth != null) parts.push(`Predicted water depth is about ${depth} centimetres.`)
  if (trend) parts.push(`Risk is ${trend}.`)
  if (horizon != null) parts.push(`This forecast covers the next ${horizon} minutes.`)
  return parts.join(' ')
}

// ---------------------------------------------------------------- step 2: alerts
function alerts(f, lang) {
  const a = f.topAlert
  const count = f.alerts?.length ?? 0
  if (!a) return null // no alert => step is skipped upstream, never narrated with filler

  // `a.title` and `a.subject` come from lib/alerts.js, i.e. the same text AlertsPanel shows on screen.
  const eta = n0(a.etaMin)
  const isActive = a.state === 'ACTIVE'

  if (lang === 'hi') {
    const parts = [`इस समय ${count} अलर्ट सक्रिय हैं।`]
    parts.push(isActive
      ? `सर्वोच्च प्राथमिकता: ${a.title}, ${a.subject} के लिए, जो अभी लागू है।`
      : `सर्वोच्च प्राथमिकता: ${a.title}, ${a.subject} के लिए${eta != null ? `, लगभग ${eta} मिनट में` : ''}।`)
    return parts.join(' ')
  }
  if (lang === 'mr') {
    const parts = [`सध्या ${count} अलर्ट सक्रिय आहेत.`]
    parts.push(isActive
      ? `सर्वोच्च प्राधान्य: ${a.title}, ${a.subject} साठी, जो आत्ता लागू आहे.`
      : `सर्वोच्च प्राधान्य: ${a.title}, ${a.subject} साठी${eta != null ? `, अंदाजे ${eta} मिनिटांत` : ''}.`)
    return parts.join(' ')
  }
  const parts = [`${count} alert${count === 1 ? ' is' : 's are'} active.`]
  parts.push(isActive
    ? `Highest priority: ${a.title}, for ${a.subject}, active now.`
    : `Highest priority: ${a.title}, for ${a.subject}${eta != null ? `, in about ${eta} minutes` : ''}.`)
  return parts.join(' ')
}

// ---------------------------------------------------------------- step 3: one forecast moment
function moment(f, m, lang) {
  const t = n0(m.tMin)
  const depth = n0(m.depthCm)
  const segs = n0(m.floodedSegments)

  if (lang === 'hi') {
    const parts = [`${t} मिनट पर, अनुमानित गहराई ${depth} सेंटीमीटर तक पहुँचती है।`]
    if (segs != null) parts.push(`लगभग ${segs} सड़क खंड प्रभावित हैं।`)
    if (m.kind === 'peak') parts.push('यह इस रन का सर्वोच्च बिंदु है।')
    return parts.join(' ')
  }
  if (lang === 'mr') {
    const parts = [`${t} मिनिटांनी, अंदाजित खोली ${depth} सेंटीमीटरपर्यंत पोहोचते.`]
    if (segs != null) parts.push(`अंदाजे ${segs} रस्ते विभाग प्रभावित आहेत.`)
    if (m.kind === 'peak') parts.push('हा या रनमधील सर्वोच्च बिंदू आहे.')
    return parts.join(' ')
  }
  const parts = [`At ${t} minutes, predicted depth reaches ${depth} centimetres.`]
  if (segs != null) parts.push(`About ${segs} street segments are affected.`)
  if (m.kind === 'peak') parts.push('This is the peak of this run.')
  return parts.join(' ')
}

// ---------------------------------------------------------------- step 4: where
function where(f, lang) {
  const d = f.deepest
  if (!d) return null
  const depth = n0(d.depthCm)
  const name = d.name
  const blocked = d.passableCar === false

  if (lang === 'hi') {
    const subject = name ? `${name} पर` : 'इस खंड पर'
    const parts = [`सबसे अधिक जोखिम ${subject} केंद्रित है, लगभग ${depth} सेंटीमीटर।`]
    if (blocked) parts.push('यह गहराई कार के लिए निर्धारित सीमा से अधिक है।')
    return parts.join(' ')
  }
  if (lang === 'mr') {
    const subject = name ? `${name} येथे` : 'या विभागावर'
    const parts = [`सर्वाधिक धोका ${subject} केंद्रित आहे, अंदाजे ${depth} सेंटीमीटर.`]
    if (blocked) parts.push('ही खोली कारसाठी निर्धारित मर्यादेपेक्षा जास्त आहे.')
    return parts.join(' ')
  }
  const subject = name ? `on ${name}` : 'on this segment'
  const parts = [`Risk is concentrated ${subject}, at about ${depth} centimetres.`]
  if (blocked) parts.push('That depth is above the clearance limit set for a car.')
  return parts.join(' ')
}

// ---------------------------------------------------------------- step 5: drainage (NO causal attribution)
function drainage(f, lang) {
  const d = f.drainage
  if (!d) return null
  const s = n0(d.surchargingNodes)
  const e = n0(d.edgesAtCapacity)

  if (lang === 'hi') {
    const parts = []
    if (s != null) parts.push(`इस समय ${s} ड्रेनेज नोड क्षमता से अधिक भरे हुए हैं।`)
    if (e != null) parts.push(`${e} पाइप पूरी क्षमता पर चल रहे हैं।`)
    parts.push('इस क्षेत्र में ड्रेनेज पर दबाव और सतही पानी, दोनों एक साथ बढ़ रहे हैं।')
    return parts.join(' ')
  }
  if (lang === 'mr') {
    const parts = []
    if (s != null) parts.push(`सध्या ${s} ड्रेनेज नोड क्षमतेपेक्षा जास्त भरले आहेत.`)
    if (e != null) parts.push(`${e} पाइप पूर्ण क्षमतेवर आहेत.`)
    parts.push('या भागात ड्रेनेजवरील ताण आणि पृष्ठभागावरील पाणी, दोन्ही एकाच वेळी वाढत आहेत.')
    return parts.join(' ')
  }
  const parts = []
  if (s != null) parts.push(`${s} drainage nodes are currently over capacity.`)
  if (e != null) parts.push(`${e} conduits are running at full capacity.`)
  // Deliberately correlational, not causal — see rule 2 at the top of this file.
  parts.push('Drainage stress and surface water accumulation are both increasing in this area.')
  return parts.join(' ')
}

// ---------------------------------------------------------------- step 6: routing / action
function routing(f, lang) {
  const r = f.routing
  if (!r) return null

  if (r.reachable === false) {
    return {
      en: 'No route is currently passable for the selected vehicle at this time step. All candidate paths exceed its clearance limit.',
      hi: 'चयनित वाहन के लिए इस समय कोई भी मार्ग सुरक्षित नहीं है। सभी विकल्प निर्धारित गहराई सीमा से अधिक हैं।',
      mr: 'निवडलेल्या वाहनासाठी सध्या कोणताही मार्ग सुरक्षित नाही. सर्व पर्याय निर्धारित खोली मर्यादेपेक्षा जास्त आहेत.',
    }[lang]
  }

  const rec = r.recommended
  const km = (m) => (Number.isFinite(m) ? (m / 1000).toFixed(1) : null)

  if (rec) {
    const dist = km(rec.lengthM)
    const depth = n0(rec.maxDepthCm)
    if (lang === 'hi') {
      const parts = [`FloodNet इस विकल्प को कम बाढ़-जोखिम वाला मार्ग बताता है।`]
      if (dist) parts.push(`दूरी लगभग ${dist} किलोमीटर।`)
      if (depth != null) parts.push(`मार्ग पर अधिकतम गहराई ${depth} सेंटीमीटर।`)
      return parts.join(' ')
    }
    if (lang === 'mr') {
      const parts = [`FloodNet हा पर्याय कमी पूर-धोका असलेला मार्ग म्हणून दर्शवतो.`]
      if (dist) parts.push(`अंतर अंदाजे ${dist} किलोमीटर.`)
      if (depth != null) parts.push(`मार्गावरील कमाल खोली ${depth} सेंटीमीटर.`)
      return parts.join(' ')
    }
    const parts = ['FloodNet identifies this alternative as the lower flood-risk route.']
    if (dist) parts.push(`Distance is about ${dist} kilometres.`)
    if (depth != null) parts.push(`Maximum depth on this route is ${depth} centimetres.`)
    return parts.join(' ')
  }

  const avoided = n0(r.avoidedCount)
  const dist = km(r.lengthM)
  if (lang === 'hi') {
    const parts = ['वर्तमान मार्ग पूर्वानुमानित बाढ़ के आधार पर निकाला गया है।']
    if (dist) parts.push(`दूरी लगभग ${dist} किलोमीटर।`)
    if (avoided) parts.push(`${avoided} प्रभावित खंडों से बचा गया है।`)
    return parts.join(' ')
  }
  if (lang === 'mr') {
    const parts = ['सध्याचा मार्ग अंदाजित पुराच्या आधारे काढला आहे.']
    if (dist) parts.push(`अंतर अंदाजे ${dist} किलोमीटर.`)
    if (avoided) parts.push(`${avoided} प्रभावित विभाग टाळले आहेत.`)
    return parts.join(' ')
  }
  const parts = ['The current route is weighted by predicted flood depth.']
  if (dist) parts.push(`Distance is about ${dist} kilometres.`)
  if (avoided) parts.push(`${avoided} flooded segment${avoided === 1 ? '' : 's'} avoided.`)
  return parts.join(' ')
}

// ---------------------------------------------------------------- step 7: summary
function summary(f, lang) {
  const place = placeName(f, lang)
  const peak = n0(f.peakDepthCm)
  const peakT = n0(f.peakDepthTMin)
  const s = n0(f.drainage?.surchargingNodes)

  if (lang === 'hi') {
    const parts = [`संक्षेप में: सबसे अधिक प्रभाव ${place} के आसपास।`]
    if (peak != null && peakT != null) parts.push(`इस रन में अधिकतम गहराई ${peak} सेंटीमीटर, ${peakT} मिनट पर।`)
    if (s != null) parts.push(`${s} ड्रेनेज नोड दबाव में हैं।`)
    parts.push('मार्ग की योजना बनाते समय वाहन की गहराई सीमा देखें। ब्रीफिंग समाप्त।')
    return parts.join(' ')
  }
  if (lang === 'mr') {
    const parts = [`थोडक्यात: सर्वाधिक परिणाम ${place} परिसरात.`]
    if (peak != null && peakT != null) parts.push(`या रनमधील कमाल खोली ${peak} सेंटीमीटर, ${peakT} मिनिटांनी.`)
    if (s != null) parts.push(`${s} ड्रेनेज नोड ताणाखाली आहेत.`)
    parts.push('मार्ग ठरवताना वाहनाची खोली मर्यादा तपासा. ब्रीफिंग समाप्त.')
    return parts.join(' ')
  }
  const parts = [`In summary: the greatest impact is around ${place}.`]
  if (peak != null && peakT != null) parts.push(`Peak depth in this run is ${peak} centimetres, at ${peakT} minutes.`)
  if (s != null) parts.push(`${s} drainage nodes are under stress.`)
  parts.push('Check the vehicle clearance limit when planning a route. End of briefing.')
  return parts.join(' ')
}

export const narrate = { situation, alerts, moment, where, drainage, routing, summary, rainSource }

// Short UI strings for the briefing controller itself.
export const UI_TEXT = {
  en: {
    start: 'Guided Briefing', pause: 'Pause', resume: 'Resume', next: 'Next', exit: 'Exit', finish: 'Finish', back: 'Back',
    steps: ['Situation', 'Alerts', 'Forecast', 'Flood zone', 'Drainage', 'Action', 'Summary'],
    noRun: 'Run a forecast first — the briefing only speaks values from a completed run.',
    voiceSpeaking: 'Speaking', voicePaused: 'Paused',
    voiceBrowser: 'Speaking (fallback voice)',
    voiceUnavailable: 'Voice unavailable — visual briefing continues',
    voiceLangUnavailable: 'No English voice installed — visual briefing continues',
  },
  hi: {
    start: 'निर्देशित ब्रीफिंग', pause: 'रोकें', resume: 'जारी रखें', next: 'आगे', exit: 'बंद करें', finish: 'समाप्त', back: 'पीछे',
    steps: ['स्थिति', 'अलर्ट', 'पूर्वानुमान', 'बाढ़ क्षेत्र', 'ड्रेनेज', 'कार्रवाई', 'सारांश'],
    noRun: 'पहले पूर्वानुमान चलाएँ — ब्रीफिंग केवल पूर्ण रन के वास्तविक मान बोलती है।',
    voiceSpeaking: 'बोल रहा है', voicePaused: 'रुका हुआ',
    voiceBrowser: 'बोल रहा है (वैकल्पिक आवाज़)',
    voiceUnavailable: 'आवाज़ उपलब्ध नहीं — दृश्य ब्रीफिंग जारी है',
    voiceLangUnavailable: 'हिन्दी आवाज़ इस डिवाइस पर उपलब्ध नहीं — दृश्य ब्रीफिंग जारी है',
  },
  mr: {
    start: 'मार्गदर्शित ब्रीफिंग', pause: 'थांबवा', resume: 'सुरू ठेवा', next: 'पुढे', exit: 'बंद करा', finish: 'समाप्त', back: 'मागे',
    steps: ['स्थिती', 'अलर्ट', 'अंदाज', 'पूर क्षेत्र', 'ड्रेनेज', 'कृती', 'सारांश'],
    noRun: 'आधी अंदाज चालवा — ब्रीफिंग फक्त पूर्ण झालेल्या रनमधील खरी मूल्ये सांगते.',
    voiceSpeaking: 'बोलत आहे', voicePaused: 'थांबवले',
    voiceBrowser: 'बोलत आहे (पर्यायी आवाज)',
    voiceUnavailable: 'आवाज उपलब्ध नाही — दृश्य ब्रीफिंग सुरू आहे',
    voiceLangUnavailable: 'मराठी आवाज या डिव्हाइसवर उपलब्ध नाही — दृश्य ब्रीफिंग सुरू आहे',
  },
}
