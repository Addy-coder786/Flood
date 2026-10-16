// HOW FLOODNET WORKS — the single source of truth for the technology story.
//
// One list drives three surfaces so they can never tell different stories:
//   • LandingPage   "How FloodNet Works" section (read-only cards)
//   • StoryStrip    the dashboard pipeline strip; clicking a stage spotlights the REAL feature behind it
//   • Demo Story    the spoken technical walkthrough (GuidedBriefing engine, mode = 'story')
//
// RULES (checked by src/lib/story/stages.test.mjs):
//   1. Every stage maps to a capability that exists in the backend today. No stage for tide, traffic, ML,
//      or an IMD radar nowcast — FloodNet does not have them.
//   2. Every line is one short sentence. Limits and methodology live in the Sources tab and docs/.
//   3. Wording follows docs/TECHNOLOGY_EXPLAINER.md: "radar-derived estimate", never "IMD radar QPE";
//      "flood-aware routing", never "best"/"guaranteed safe".
//   4. `focus` only names things the dashboard really renders (data-tour anchors, real tabs, real layers).
//
// Pure data + pure helpers only: no React, no DOM, so it runs under `node --test`.

export const RIGHT_TABS = ['overview', 'alerts', 'why', 'routing', 'provenance']
export const MAP_LAYERS = ['streets', 'depth', 'hotspots', 'route', 'roads', 'drainage', 'terrain']

export const PIPELINE_STAGES = [
  {
    id: 'rain',
    glyph: '☔',
    chip: 'RAIN',
    title: { en: 'Rain', hi: 'वर्षा', mr: 'पाऊस' },
    line: {
      en: 'IMD observations and radar-derived rainfall estimates.',
      hi: 'IMD के अवलोकन और रडार से प्राप्त वर्षा के अनुमान।',
      mr: 'IMD ची निरीक्षणे आणि रडारवरून मिळालेले पर्जन्य अंदाज.',
    },
    badge: { text: 'IMD · ECMWF · 2005 replay', tag: 'REAL' },
    focus: { target: ['[data-tour="rainfall-source"]', '[data-tour="left-panel"]'] },
  },
  {
    id: 'spatial',
    glyph: '▦',
    chip: 'SPATIAL RAIN',
    title: { en: 'Spatial rainfall', hi: 'स्थानिक वर्षा', mr: 'अवकाशीय पाऊस' },
    line: {
      en: "Rainfall is represented on the model's working grid.",
      hi: 'वर्षा को मॉडल की कार्यशील ग्रिड पर दर्शाया जाता है।',
      mr: 'पाऊस मॉडेलच्या कार्यरत ग्रिडवर मांडला जातो.',
    },
    badge: { text: 'Field [T, ny, nx]', tag: 'ESTIMATED' },
    focus: { target: ['[data-tour="timeline"]'] },
  },
  {
    id: 'runoff',
    glyph: '◢',
    chip: 'RUNOFF',
    title: { en: 'Runoff', hi: 'अपवाह', mr: 'अपधाव' },
    line: {
      en: 'Urban surfaces convert rainfall into surface runoff.',
      hi: 'शहरी सतहें वर्षा को सतही अपवाह में बदलती हैं।',
      mr: 'शहरी पृष्ठभाग पावसाचे पृष्ठीय अपधावात रूपांतर करतात.',
    },
    badge: { text: 'OSM buildings · per-cell C', tag: 'ESTIMATED' },
    focus: { target: ['[data-tour="map"]'], layersOn: ['terrain'] },
  },
  {
    id: 'surface',
    glyph: '≋',
    chip: '2D FLOW',
    title: { en: '2D surface flow', hi: '2D सतही प्रवाह', mr: '2D पृष्ठीय प्रवाह' },
    line: {
      en: 'The terrain model determines where surface water can move and accumulate.',
      hi: 'भू-भाग मॉडल तय करता है कि सतही पानी कहाँ बह सकता है और कहाँ जमा हो सकता है।',
      mr: 'भूभाग मॉडेल ठरवते की पृष्ठभागावरील पाणी कुठे वाहू शकते आणि कुठे साचू शकते.',
    },
    badge: { text: 'MCGM DTM · 10 m grid', tag: 'REAL' },
    // Opens the 3D view of the SAME DEM the solver runs on (components/Terrain3D) with the modelled water on it.
    focus: { target: ['[data-tour="terrain-3d"]', '[data-tour="map"]'], layersOn: ['depth'], mode3d: true },
  },
  {
    id: 'drainage',
    glyph: '⌬',
    chip: 'DRAINAGE',
    title: { en: 'Drainage network', hi: 'ड्रेनेज नेटवर्क', mr: 'ड्रेनेज नेटवर्क' },
    line: {
      en: 'Underground capacity is simulated alongside surface flow.',
      hi: 'भूमिगत क्षमता का अनुकरण सतही प्रवाह के साथ-साथ होता है।',
      mr: 'भूमिगत क्षमतेचे अनुकरण पृष्ठीय प्रवाहासोबत केले जाते.',
    },
    badge: { text: 'MCGM drains · capacity est.', tag: 'REAL' },
    focus: { tab: 'why', target: ['[data-tour="panel-why"]', '#rtab-why'], layersOn: ['drainage'] },
  },
  {
    id: 'depth',
    glyph: '▤',
    chip: 'FLOOD DEPTH',
    title: { en: 'Flood depth', hi: 'बाढ़ की गहराई', mr: 'पुराची खोली' },
    line: {
      en: 'FloodNet produces street-level depth estimates in centimetres.',
      hi: 'FloodNet सड़क-स्तर पर गहराई का अनुमान सेंटीमीटर में देता है।',
      mr: 'FloodNet रस्त्याच्या पातळीवरील खोलीचा अंदाज सेंटीमीटरमध्ये देतो.',
    },
    badge: { text: '0–180 min forecast', tag: 'ESTIMATED' },
    focus: { tab: 'overview', target: ['[data-tour="map"]'], layersOn: ['streets'], play: true },
  },
  {
    id: 'action',
    glyph: '➜',
    chip: 'ACTION',
    title: { en: 'Action', hi: 'कार्रवाई', mr: 'कृती' },
    line: {
      en: 'Alerts and flood-aware routing turn predictions into decisions.',
      hi: 'अलर्ट और बाढ़-सजग मार्ग पूर्वानुमान को निर्णय में बदलते हैं।',
      mr: 'अलर्ट आणि पूर-सजग मार्ग अंदाजांचे निर्णयात रूपांतर करतात.',
    },
    badge: { text: 'Alerts · lower flood-risk route', tag: 'ESTIMATED' },
    focus: { tab: 'alerts', target: ['[data-tour="alert-top"]', '[data-tour="panel-alerts"]', '#rtab-alerts'] },
  },
]

// The compact dashboard header groups the seven stages into the four things an operator reads.
export const STORY_HEADER = [
  { label: 'RAINFALL', stages: ['rain', 'spatial'] },
  { label: 'FLOOD', stages: ['runoff', 'surface', 'depth'] },
  { label: 'DRAINAGE', stages: ['drainage'] },
  { label: 'ACTION', stages: ['action'] },
]

// Demo Story: a richer, spoken technical walkthrough. Fixed technology sentences come from the stages above;
// situational numbers are supplied at run time by lib/briefing/narration.js from the completed run only.
export const DEMO_STORY = [
  { id: 'story-rain', stage: 'rain', title: { en: 'Rainfall', hi: 'वर्षा', mr: 'पाऊस' } },
  { id: 'story-terrain', stage: 'surface', title: { en: 'Terrain', hi: 'भूभाग', mr: 'भूभाग' } },
  { id: 'story-runoff', stage: 'runoff', title: { en: 'Runoff', hi: 'अपवाह', mr: 'अपधाव' } },
  { id: 'story-drainage', stage: 'drainage', title: { en: 'Drainage', hi: 'ड्रेनेज', mr: 'ड्रेनेज' } },
  { id: 'story-flood', facts: 'moments', title: { en: 'Flood development', hi: 'बाढ़ का विकास', mr: 'पुराचा विकास' } },
  { id: 'story-why', facts: 'where', title: { en: 'Why flooded', hi: 'क्यों भरा', mr: 'का भरले' } },
  { id: 'story-alerts', facts: 'alerts', title: { en: 'Alerts', hi: 'अलर्ट', mr: 'अलर्ट' } },
  { id: 'story-route', facts: 'routing', title: { en: 'Lower flood-risk route', hi: 'कम बाढ़-जोखिम मार्ग', mr: 'कमी पूर-जोखमीचा मार्ग' } },
]

// Spoken when a briefing reaches the terrain: fixed wording, no elevation value is ever spoken.
export const TERRAIN_NARRATION = {
  en: 'FloodNet uses the terrain elevation to determine where surface water can move and accumulate.',
  hi: 'FloodNet भू-भाग की ऊंचाई का उपयोग यह निर्धारित करने के लिए करता है कि सतही पानी कहाँ बह सकता है और कहाँ जमा हो सकता है।',
  mr: 'FloodNet भूभागाची उंची वापरून पृष्ठभागावरील पाणी कुठे वाहू शकते आणि कुठे साचू शकते हे ठरवते.',
}

export const STORY_UI = {
  en: { start: 'Demo Story', how: 'How FloodNet works' },
  hi: { start: 'डेमो स्टोरी', how: 'FloodNet कैसे काम करता है' },
  mr: { start: 'डेमो स्टोरी', how: 'FloodNet कसे कार्य करते' },
}

export function stageById(id) {
  return PIPELINE_STAGES.find((s) => s.id === id) || null
}

/** Layers a stage needs switched ON that are currently off — so the caller can restore them afterwards. */
export function layersToEnable(stage, layers) {
  return (stage?.focus?.layersOn || []).filter((k) => !layers?.[k])
}

export function localized(obj, lang) {
  return obj?.[lang] ?? obj?.en ?? ''
}
