// Shared metadata for the five control-centre tabs, used by BOTH guided features so neither grows its own
// copy: the Guided Briefing (spoken, en/hi/mr) and the Explore FloodNet tour (visual, English callouts).
//
// WHY THE TARGETS ARE ARRAYS
// Each tab's target is a PREFERENCE LIST, tried in order. The first entry is the most specific real element
// (the top alert card, the route result) and the last is always the tab panel's own content container, which
// always exists once the tab is open. So when there is no alert, or no route has been planned yet, the
// spotlight falls back to the real section instead of vanishing — and nothing is ever fabricated to give the
// tour something to point at.
//
// WHY THESE TARGETS ARE PANEL CONTENT, NOT NAV BUTTONS
// The brief is explicit: open the actual tab and highlight what is inside it. Every selector below resolves
// to content rendered by the real panel component (MetricsPanel, AlertsPanel, WhyFloodedPanel, RoutePlanner,
// ProvenancePanel) — never to the tab button in the tab strip.
//
// NARRATION RULES (identical to lib/briefing/narration.js, restated because this file also authors speech):
//  • Orientation only — these say what a section is FOR. They never quote a depth, a street, an alert
//    severity or a route, because those are situational values and belong to the data-driven steps.
//  • Why-flooded uses the established correlational wording. It never attributes a specific street's
//    flooding to a specific drain: FloodNet measures r = 0.048 between segment depth and nearest-node
//    surcharge, so that claim is unsupported (docs/VALIDATION.md).
//  • Route never claims traffic awareness — there is no traffic feed.

/** Right-panel tab keys, matching FloodNetContext's `activeRightTab` / App.jsx's RIGHT_TABS. */
export const TAB_KEYS = ['overview', 'alerts', 'why', 'routing', 'response', 'provenance']

export const TAB_STEPS = [
  {
    id: 'tab-overview',
    tab: 'overview',
    // MetricsPanel's own section; FloodedStreets sits below it in the same panel.
    target: ['[data-tour="panel-overview"]', '#rtab-overview'],
    title: { en: 'Overview', hi: 'ओवरव्यू', mr: 'ओव्हरव्ह्यू' },
    short: { en: 'Your overall flood situation.', hi: 'समग्र बाढ़ स्थिति।', mr: 'एकूण पूरस्थिती.' },
    narration: {
      en: 'Overview gives you the current situation at a glance — how deep the water gets, how many streets are affected, and how that is trending.',
      hi: 'ओवरव्यू में मौजूदा स्थिति एक नज़र में मिलती है — पानी कितना गहरा है, कितनी सड़कें प्रभावित हैं, और रुझान किस ओर है।',
      mr: 'ओव्हरव्ह्यूमध्ये सध्याची स्थिती एका दृष्टिक्षेपात मिळते — पाणी किती खोल आहे, किती रस्ते प्रभावित आहेत, आणि कल कोणत्या दिशेने आहे.',
    },
  },
  {
    id: 'tab-alerts',
    tab: 'alerts',
    // Prefer the top-priority alert card; fall back to the panel when there is no alert at all.
    target: ['[data-tour="alert-top"]', '[data-tour="panel-alerts"]', '#rtab-alerts'],
    title: { en: 'Alerts', hi: 'अलर्ट', mr: 'अलर्ट' },
    short: { en: 'Important warnings and conditions.', hi: 'ज़रूरी चेतावनियाँ।', mr: 'महत्त्वाच्या सूचना.' },
    narration: {
      en: 'Alerts shows the warnings that currently need attention, ordered by how serious and how soon they are.',
      hi: 'अलर्ट वे चेतावनियाँ दिखाता है जिन पर अभी ध्यान देना ज़रूरी है, गंभीरता और समय के क्रम में।',
      mr: 'अलर्ट सध्या लक्ष देण्याजोग्या सूचना दाखवते, गांभीर्य आणि वेळेनुसार क्रमाने.',
    },
  },
  {
    id: 'tab-why',
    tab: 'why',
    target: ['[data-tour="panel-why"]', '#rtab-why'],
    title: { en: 'Why Flooded', hi: 'क्यों भरा', mr: 'का भरले' },
    short: {
      en: 'What is contributing to the flooding.',
      hi: 'बाढ़ में क्या योगदान दे रहा है।',
      mr: 'पुरात काय योगदान देत आहे.',
    },
    // Correlational, never causal — see the narration rules at the top of this file.
    narration: {
      en: 'Why Flooded shows what is contributing here — the rainfall on this area, the terrain it collects in, and how loaded the nearby drainage is. It reports conditions that occur together, not a proven chain from one drain to one street.',
      hi: 'यह सेक्शन बताता है कि यहाँ क्या योगदान दे रहा है — इस क्षेत्र की बारिश, जिस ढलान में पानी जमा होता है, और पास की ड्रेनेज पर कितना भार है। यह साथ-साथ होने वाली स्थितियाँ बताता है, किसी एक ड्रेन से किसी एक सड़क तक सिद्ध कारण नहीं।',
      mr: 'हा विभाग येथे काय योगदान देत आहे ते दाखवतो — या भागातील पाऊस, पाणी साचणारा उतार, आणि जवळच्या ड्रेनेजवरील ताण. हे एकत्र घडणाऱ्या परिस्थिती दाखवते, एका ड्रेनपासून एका रस्त्यापर्यंतचे सिद्ध कारण नाही.',
    },
  },
  {
    id: 'tab-route',
    tab: 'routing',
    // Prefer a computed result; otherwise point at the real controls so the section is still explained.
    target: ['[data-tour="route-result"]', '[data-tour="route-controls"]', '[data-tour="panel-route"]', '#rtab-routing'],
    title: { en: 'Route', hi: 'रूट', mr: 'रूट' },
    short: {
      en: 'Flood-aware route comparison.',
      hi: 'बाढ़-जागरूक मार्ग तुलना।',
      mr: 'पूर-जागरूक मार्ग तुलना.',
    },
    // No traffic claim anywhere in these three strings.
    narration: {
      en: 'Route compares paths using predicted flood depth and the clearance limit of the vehicle you pick, and can suggest a lower flood-risk alternative. It uses flood depth only — there is no traffic data.',
      hi: 'रूट सेक्शन बाढ़ के जोखिम और चुने गए वाहन की गहराई सीमा को ध्यान में रखकर कम बाढ़-जोखिम वाला मार्ग चुनने में मदद करता है। इसमें केवल पानी की गहराई देखी जाती है, ट्रैफ़िक डेटा नहीं।',
      mr: 'रूट सेक्शन पूराचा धोका आणि निवडलेल्या वाहनाची खोली मर्यादा लक्षात घेऊन कमी पूर-जोखमीचा मार्ग निवडण्यात मदत करतो. यात फक्त पाण्याची खोली पाहिली जाते, वाहतूक डेटा नाही.',
    },
  },
  {
    id: 'tab-response',
    tab: 'response',
    target: ['[data-tour="panel-response"]', '#rtab-response'],
    title: { en: 'Response plan', hi: 'प्रतिक्रिया योजना', mr: 'प्रतिसाद योजना' },
    short: {
      en: 'Assign teams, vehicles and shelter places.',
      hi: 'टीम, वाहन और आश्रय स्थान सौंपें।',
      mr: 'पथके, वाहने आणि निवाऱ्यातील जागा नेमा.',
    },
    // Decision support, simulated numbers, unmet needs stated: never an order, never real headcounts.
    narration: {
      en: 'Response plan assigns rescue teams, vehicles and shelter places to settlements by priority, only to teams with the required skills, and says plainly which needs it could not meet and why. All numbers here are simulated demo data.',
      hi: 'प्रतिक्रिया योजना प्राथमिकता के अनुसार बचाव टीमें, वाहन और आश्रय स्थान बस्तियों को सौंपती है, केवल ज़रूरी क्षमता वाली टीमों को, और साफ़ बताती है कि कौन-सी ज़रूरतें पूरी नहीं हो सकीं और क्यों। यहाँ के सभी आँकड़े सिम्युलेटेड डेमो डेटा हैं।',
      mr: 'प्रतिसाद योजना प्राधान्यानुसार बचाव पथके, वाहने आणि निवाऱ्यातील जागा वस्त्यांना नेमते, फक्त आवश्यक क्षमता असलेल्या पथकांना, आणि कोणत्या गरजा पूर्ण झाल्या नाहीत व का, हे स्पष्ट सांगते. येथील सर्व आकडे सिम्युलेटेड डेमो डेटा आहेत.',
    },
  },
  {
    id: 'tab-sources',
    tab: 'provenance',
    target: ['[data-tour="panel-sources"]', '#rtab-provenance'],
    title: { en: 'Sources', hi: 'स्रोत', mr: 'स्रोत' },
    short: { en: 'Where the data comes from.', hi: 'डेटा कहाँ से आता है।', mr: 'डेटा कुठून येतो.' },
    narration: {
      en: 'Sources shows where each input comes from — rainfall, terrain, the drainage network and the road map — and whether a value is measured or estimated.',
      hi: 'स्रोत सेक्शन बताता है कि हर इनपुट कहाँ से आया है — बारिश, भूभाग, ड्रेनेज नेटवर्क और सड़क डेटा — और कोई मान मापा गया है या अनुमानित।',
      mr: 'स्रोत विभाग प्रत्येक इनपुट कुठून आला ते दाखवतो — पाऊस, भूभाग, ड्रेनेज नेटवर्क आणि रस्ते डेटा — आणि एखादे मूल्य मोजलेले आहे की अंदाजित.',
    },
  },
]

/**
 * Wait until one of `selectors` resolves to a laid-out element; resolve with that selector, or null.
 * Used after activating a tab so the spotlight measures content that has actually rendered rather than a
 * panel that is still mounting. Shared by both tours so neither re-implements the polling.
 */
export function waitForAnyTarget(selectors, timeoutMs = 1500) {
  const list = Array.isArray(selectors) ? selectors : [selectors]
  return new Promise((resolve) => {
    const started = Date.now()
    const tick = () => {
      for (const sel of list) {
        let el = null
        try { el = document.querySelector(sel) } catch { el = null }
        if (el) {
          const r = el.getBoundingClientRect()
          if (r.width || r.height) return resolve(sel)
        }
      }
      if (Date.now() - started > timeoutMs) return resolve(null)
      setTimeout(tick, 80)
    }
    tick()
  })
}
