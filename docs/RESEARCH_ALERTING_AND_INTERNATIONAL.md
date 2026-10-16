# Research: alerting channels, official pathway, international practice, UX/QA (2026-10-11)

Second-pass research that checks `docs/LIVE_OPERATIONS_RESEARCH.md` (LOR), `docs/MESSAGING_AND_PUBLIC_ALERTS.md`
(MPA) and `docs/OFFICIAL_WARNING_PATHWAY.md` (OWP) against primary sources. **Everything decided here is a proposal**;
decisions belong in `docs/DECISIONS.md`. All URLs accessed **2026-10-11**.

Labels: **[V]** read in the primary source today; **[S]** secondary source, a search summary, or a primary document
mirrored by a third party; **[UNVERIFIED]** not established. Verdicts on prior research: **CONFIRMED**,
**CONTRADICTED**, **OUTDATED**, **REFINED** (correct but incomplete).

Not researched in this pass: Gupshup and Kaleyra pricing/APIs, Chinese primary sources (LOR §3 stands as written),
NIMS doctrine pages other than the swiftwater typing sheet (LOR §1 stands as written).

---

## 1. Indian SMS: TRAI TCCCPR and providers

### Verified facts

**Regulation (TCCCPR 2018, Second Amendment 2025)**: https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf (text extracted from the PDF) [V]
- Notified 12 Feb 2025. Most clauses in force 30 days after Gazette publication. Reg. 8, reg. 17, parts of reg. 20 and 21 in force after 60 days (cl. 1(3)).
- Header suffixes: "-P", "-S", "-T" and "-G" for Promotional, Service, Transactional and Government messages. Headers are 11-character alphanumeric strings (Schedule-I item 2).
- **Service Message** (cl. 2(bh)) has two parts. (i) To "its Customer or Subscriber", information about a product or service, including "safety or security of the product". Not promotional; **no Explicit Consent needed**. (ii) To facilitate an ongoing transaction after Explicit Consent, which lasts **seven days**.
- **Transactional** (cl. 2(bt)) now means a response to a customer-initiated transaction **within thirty minutes** (OTPs and similar). FloodNet advisories are not transactional.
- **Government Message** (cl. 2(za)) means a message sent "on the directions of" the Central or State Government, a body established under the Constitution, or a TRAI-authorised agency, through DLT. No consent is required and recipients cannot block it.
- Content templates (Schedule-I item 4):
  - more than three variables only "in special circumstances" with recorded justification;
  - each variable pre-tagged for its purpose;
  - at least 30% fixed content;
  - one template linked to only one header;
  - templates unused for 90 days are auto-deactivated;
  - variables are limited to 30 characters, with contiguous variables allowed only after justification.
- Messages containing URLs, APKs, OTT links or **call-back numbers** are scrubbed against whitelisted data uploaded by the sender, with an undertaking that the links are not malicious (item 4(4)(f)).
- **Sender registration now requires physical verification of the entity, biometric authentication of the authorised person, and linking to a unique mobile number** (reg. 17, Schedule-I item 1(4)). This is a 60-day clause, so it was in force by about April 2025.
- URL whitelisting enforcement date: TRAI moved the start from 1 Sep 2024 to **1 Oct 2024** [S: search summary of TRAI Directions of 30 Aug 2024 and 28 Oct 2024; both PDFs are scanned images and could not be read]. TRAI PR 67/2024 (scanned, created 26 Sep 2024) carries the headline "TRAI Mandates Whitelisted URLs, APKS, or OTT links for SMS Traffic" [V headline only] https://trai.gov.in/notifications/press-release/trai-mandates-whitelisted-urls-apks-or-ott-links-sms-traffic-0

**DLT onboarding (Jio TrueConnect, operator guides)** [V]
- https://trueconnect.jio.com/assets/Trai_module/SampleFileDownloads/Entity_Registration_and_Login_(PE_and_TM_).pdf
  - Principal Entity (PE) registration needs the organisation's PAN/TAN and either a GSTN or another KYC document.
  - Rejections are listed for invalid email, invalid mobile, KYC mismatch and incorrect documents.
- https://trueconnect.jio.com/assets/Trai_module/SampleFileDownloads/Header_SMS_Registration.pdf
  - Header types are "Promotional" or Others ("Transactional, Service Explicit, Service Implicit").
  - **Temporary headers** of 15, 30, 45 or 90 days exist.
- https://trueconnect.jio.com/assets/Trai_module/SampleFileDownloads/PE-TM_User_Guide-Version1.1.pdf
  - A PE is "a company or individual"; "a business, sole proprietor, partnership firm, or government organization".
  - A PE must bind a chain of telemarketers (up to 5 per creation request).
- Documents, fees and timing [S]:
  - An authorisation letter and proof of the signatory's authority are required (Kaleyra help, 2021).
  - Approval time is quoted as "48-36 hours" (Kaleyra) and 3-5 business days (Exotel).
  - The fee is quoted as Rs 5,900 incl. GST per operator, with conflicting claims (Lucep, Infobip, Exotel guides).
  - An individual PAN is accepted only when it is used for a business (a third-party guide).

**MSG91** [V unless marked]
- Pricing, India, https://msg91.com/in/pricing/sms: ₹0.25 per SMS at 5,000; ₹0.20 at 16,500; ₹0.18 at 30,000; ₹0.17 from 60,000; ₹0.16 at 9,62,500; **+18% GST**. Sales quote "up to ₹0.13". **No free trial credits and no DLT mention on the pricing page.**
- DLT is mandatory for India:
  - A template in MSG91 carries the DLT Template ID, the approved sender ID and the exact approved text.
  - DLT variables are `{#var#}`, while MSG91 variables are `##name##`.
  - A "Test DLT" option sends to one number.
  - Sources: https://msg91.com/help/Template/how-to-create-flow-id-to-send-sms-via-api and https://msg91.com/help/dlt-registration-in-india/map-approved-dlt-template-id-with-respective-flow-id-on-msg91-panel
- Error 211 means "No DLT Template ID or Invalid Template ID". Error 203 means "PE ID missing or Header/Sender ID not approved". https://msg91.com/help/dlt-registration-in-india/dlt-debugging-checklist
- Send API: headers `authkey`, `accept`, `content-type`.
  - Optional `clientId`, `CRQID` and `UUID` "are returned in reports and webhooks". https://docs.msg91.com/sms/send-sms
  - The endpoint `POST https://control.msg91.com/api/v5/flow`, the body `{template_id, short_url, recipients:[{mobiles, VAR…}]}` and case-sensitive variable names are [S] (third-party mirror of the MSG91 docs).
  - "HTTP 200 with `type:error`" and "new authkeys have IP security on by default" are also from that mirror [S]. https://www.withone.ai/knowledge/msg91/conn_mod_def%3A%3AGNWEPfEWn3Q%3A%3AKr-zbEkdTQK82XTCFEhmhQ
- Webhook (New), https://msg91.com/help/webhook-new:
  - The endpoint must respond within **8 s**, otherwise MSG91 **retries up to 4 times** and duplicates are possible.
  - **Auto-pause: "If the configured webhook URL returns a 4XX and 5XX response, the webhook will be automatically paused."**
- SMS webhook, https://msg91.com/help/webhook-new/how-to-receive-sms-delivery-reports-via-webhook-new:
  - JSON only, **one event per recipient**, and users can add **custom headers**.
  - Status codes: **0 Sent, 1 Delivered, 2 Failed, 9 NDNC (promotional only), 16 and 25 Rejected, 17 Blocked number, 20 Country code blocked**.
  - Fields include `requestId`, `telNum` (**the recipient's number**), `CRQID`, `DLT_TE_ID`, `failureReason` and `credit` (the sample shows "0.25").
- MSG91's own explanation of "Sent": "the reports are not updated … when we don't receive DLR from the operator end" [V] https://msg91.com/guide/exact-information-of-the-delivery-report-status

**Twilio SMS to India**: https://www.twilio.com/en-us/guidelines/in/sms [V]
- Two gateways:
  - **"International: No DLT registration is required."** It uses ILDO connections and random short codes 5NNNN–5NNNNNNNN, and the sender ID is overwritten.
  - **Domestic**: DLT company and sender-ID registration first, then a Twilio registration. Provisioning takes **10 business days**, and the header is prefixed by operators (e.g. "VM-TWILIO").
- **"Sending messages with shortened URL is not allowed."** Political, religious, loan and similar content is prohibited.
- Message length is 160 characters; UCS-2 is supported.
- Price: **$0.0832** per message for "International Numbers" and "Alphanumeric Sender IDs". The page does not separate inbound from outbound, and carrier fees may apply. https://www.twilio.com/en-us/sms/pricing/in
- Trial, https://www.twilio.com/docs/usage/trials:
  - 30 days, 100 free SMS and 100 free WhatsApp messages.
  - SMS only to the **sign-up country**, **up to 5 verified recipients**.
  - **"Custom message bodies are not supported"** for SMS. WhatsApp is limited to pre-defined templates.
  - After upgrade, a fresh 100 messages are granted.
- Status lifecycle:
  - Initial `queued` (or `accepted`/`scheduled` with a Messaging Service); no callback is sent for the initial status.
  - Then `sent`/`failed`, then `delivered`/`undelivered`, with `read` for WhatsApp/RCS.
  - **Callbacks can arrive out of order.** The endpoint must answer 200.
  - Sources: https://www.twilio.com/docs/messaging/guides/outbound-message-status-in-status-callbacks and https://www.twilio.com/docs/messaging/guides/track-outbound-message-status

**Fast2SMS "Quick SMS"** (vendor claims) [V vendor pages]
- No DLT needed; "uses international gateway"; **each message is manually approved by their team** within working hours; spam or personal content is refused.
- Vendor's justification: "TRAI guidelines are not applicable outside India".
- Price stated as **INR 7** and as **INR 5** per SMS on the same page.
- Sources: https://www.fast2sms.com/help/?p=16895 , https://www.fast2sms.com/quick-sms-without-dlt

**Segment sizes**: GSM-7 is 160 characters (153 per part). **UCS-2 (Hindi/Marathi) is 70 characters (67 per part)** [V] https://www.twilio.com/docs/glossary/what-sms-character-limit

### Verdicts on prior research
- LOR §6 "commercial communication from an unregistered sender is unsolicited; -P/-S/-T/-G; whitelisting": **CONFIRMED**.
- LOR §6 "A subscription alert fits Service-Implicit": **REFINED**. The 2025 text no longer uses the words "implicit/explicit" in the definition. Clause (bh)(i) (customer or subscriber, product-safety information, no explicit consent) is the closest fit. Operator portals still label header types "Service Implicit/Explicit". The operator decides at template approval.
- LOR §6 / MPA "DLT not achievable during a hackathon": **CONFIRMED and strengthened**. Physical verification plus biometric authentication of the authorised person is now mandatory.
- LOR §6 "Twilio: India domestic SMS needs DLT": **REFINED / partly CONTRADICTED**. That is true for the domestic route only. Twilio documents an **international route with no DLT** at $0.0832 per SMS with a random numeric sender. MPA's "Real Indian SMS: BLOCKED" is therefore too absolute for Twilio.
- LOR §6 "Twilio trial … do not support custom bodies": **CONFIRMED**.
- LOR §6 MSG91 status codes: **CONFIRMED**, plus code 20. "No webhook signature documented": **CONFIRMED**, but MSG91 supports **custom headers**, which makes our shared-secret header workable.

### Assumptions
- A student team has no registered legal entity and no GST registration, so it cannot be a Principal Entity in the hackathon window.
- A college could register as PE, but that would take days to weeks [UNVERIFIED].
- An international-route SMS to an Indian handset is delivered in reasonable time. Twilio documents the route, but we have not tested delivery [UNVERIFIED].

### Proposed for FloodNet
1. Keep SIMULATED as the default. Present Indian DLT SMS as **BLOCKED (entity registration + biometric verification + header + templates + URL whitelist)**.
2. If a live SMS demo is wanted, the only realistic path is **Twilio, upgraded (not trial) account, international route**. That means one allow-listed consenting number of a team member, a custom body with a full (unshortened) URL, and expected cost of about $0.0832 per message. The UI and slides must label it "international route, not DLT, random sender".
3. **Do not use Fast2SMS Quick SMS.** Manual approval during working hours makes it unusable for time-critical messages, and its legal basis is only the vendor's own reading of TRAI.
4. MSG91 adapter fixes:
   - Send `CRQID` = our delivery id, so DLRs correlate without relying on `requestId`.
   - Pass variables with the exact template names; there must be no `VAR1` fallback in live mode.
   - Treat HTTP 200 with `type:error` as REJECTED. The current code already requires `type == success`.
5. MSG91 webhook handler:
   - Return **200** for every request that carries the correct secret, even when the payload is unparseable or the message is unknown. Today it returns 422, which would auto-pause the webhook.
   - Never log `telNum`.
   - Accept the per-recipient flat JSON of Webhook (New).
   - The legacy `data=[{requestId, report:[{status}]}]` form nests `status` inside `report`, which the current parser does not read.
6. Template drafting for when DLT exists: at most 3 variables of at most 30 characters each, at least 30% fixed text, and the public base URL and 112/1916 whitelisted. English should stay at 160 GSM-7 characters or fewer. Hindi/Marathi texts cost 70 characters per segment, so budget at most 3 segments.

### Unresolved
- Whether a free public-safety advisory from a non-commercial sender is "commercial communication" at all. TCCCPR is framed around commercial communication, and only the operator or TRAI can answer.
- Whether a `*.vercel.app` URL can be whitelisted.
- The current DLT fee (sources conflict).
- Whether a Twilio account registered in India can use the international route (the trial restricts SMS to the sign-up country; upgraded-account behaviour is unverified).

---

## 2. WhatsApp Business Platform

### Verified facts
- **Per-message pricing since 1 Jul 2025.** Meta charges only when a template message is *delivered*. Non-template messages are free since 1 Nov 2024, *until* the change below. Utility templates inside an open 24-h customer service window (CSW) were free from 1 Jul 2025. Meta changes prices only on the 1st day of a quarter.
  - Source: https://developers.facebook.com/docs/whatsapp/pricing (page "Updated: Jul 1, 2026") [V]
- **From 1 Oct 2026** (that is, now), Meta charges **all service (non-template) messages** and **utility templates sent inside an open CSW**. Service rates equal the utility/authentication rates by market, with no volume tiers. Accounts without a payment method by 30 Sep 2026 have service messages stopped.
  - Source: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages ("Updated: Aug 25, 2026") [V]
  - **Prior research does not mention this. It is OUTDATED for any cost reasoning.**
- **India rates (INR, per delivered message, ex-tax)**: **Marketing ₹0.8631; Utility ₹0.1150; Authentication ₹0.1150; Authentication-international ₹2.4971; Service ₹0.1150** (effective 1 Oct 2026).
  - Source [S, Meta INR rate-card PDF mirrored by a third party]: https://xobito.com/downloads/whatsapp-rate-cards-october-2026/whatsapp-inr-rates-and-volume-tiers-oct-2026.pdf
  - The January 2026 card shows the same marketing and utility rates, with Auth-intl ₹2.3000 [S]: https://www.gupshup.ai/resources/wp-content/uploads/2025/12/INR_Jan2026.pdf
  - Meta's own CSV could not be fetched (TLS error). A free tier of "1,000 service messages per number per month" appears only in BSP blogs [UNVERIFIED].
- WhatsApp's own description of the categories: utility messages are "either specific to or requested by a user … or critical to a user (like a tornado warning)" [V] https://whatsappbusiness.com/products/platform-pricing/
  - Category is assigned by Meta at template review. The business accepts the category's charge at time of use [V pricing page].
- Opt-in (policy since Nov 2024): opt-in may be general, not WhatsApp-specific, if local law is met. It must state that the person is opting in and **name the business**. Users must be able to opt out per category [V] https://developers.facebook.com/docs/whatsapp/overview/getting-opt-in
- Messaging limits: new business portfolios can reach **250 unique users per rolling 24 h outside a CSW**. This scales to 2,000 after business verification or 2,000 delivered high-quality messages in 30 days [V] https://developers.facebook.com/docs/whatsapp/messaging-limits/
- New portfolios are capped at 2 registered numbers [V] https://developers.facebook.com/docs/whatsapp/cloud-api/phone-numbers/
- The Cloud API free test number is limited to "up to 5 verified recipients" [S] (Conferbot summary citing Meta docs).
- Webhooks:
  - Outgoing statuses are `sent`, `delivered`, `read`, with errors in `statuses[].errors`. Up to three status webhooks arrive per message [V] https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components/
  - **Non-200 responses are retried "with decreasing frequency … for up to 7 days"**, so duplicates occur [V] https://developers.facebook.com/docs/whatsapp/cloud-api/guides/set-up-webhooks/
  - The `failed` status and the `X-Hub-Signature-256` HMAC-SHA256 header over the raw body remain [S] (the AsyncAPI mirror and a StackOverflow answer show that the *raw* bytes must be hashed).
- **Twilio WhatsApp**: pricing is a Twilio fee of **$0.005 per message (inbound or outbound) plus Meta's fee**, and **$0.001 per failed message** [V] https://www.twilio.com/en-us/whatsapp/pricing
- **Twilio WhatsApp Sandbox** [V] https://www.twilio.com/docs/whatsapp/sandbox
  - Testing only, on the shared number +14155238886.
  - Only users who sent `join <word>` can receive; others fail with error 63015.
  - Throughput is 1 message per 3 s, and the **session expires 3 days after joining**.
  - Business-initiated messages may use **only 3 pre-approved templates** (appointment, order, verification code); custom templates are impossible.
  - **Sending `join` opens a 24-h CSW, inside which free-form text works.**
  - The Sandbox lives in the *legacy* Console. Trial accounts in the new Console use "Try out WhatsApp", which offers pre-defined content only.

### Verdicts on prior research
- LOR §6 "opt-in required, opt-outs respected": **CONFIRMED** (policy text refined).
- LOR §6 statuses and signature: **CONFIRMED** at [S]/[V] level as above.
- MPA "WhatsApp production BLOCKED (business verification + templates)": **REFINED**. Business verification is not needed to *start* (250/day limit), but approved templates are needed to message outside a CSW.

### Contradiction with the current implementation
- `TwilioProvider.send` posts a free-form `Body` for WhatsApp. That only works **inside an open 24-h CSW**. For FloodNet's use case (a business-initiated alert hours or days after sign-up), production needs an approved **utility template** (Twilio `ContentSid` + `ContentVariables`). The Sandbox offers only the 3 generic templates.
- **Demo consequence:** a live WhatsApp demo works only if the recipient joined the Sandbox, or messaged us, less than 24 h earlier, and within 3 days of joining.

### Assumptions
- A flood advisory to a subscriber who opted in would be categorised by Meta as utility, not marketing. WhatsApp's own example ("tornado warning") supports this, but Meta decides per template.

### Proposed for FloodNet
1. Demo script: a team phone sends `join …` to the Sandbox on the demo day, then FloodNet sends a free-form advisory inside the CSW. Label it "Twilio Sandbox, test only".
2. Add a template path (`ContentSid`) behind a flag for the case "outside CSW", and record "template required" as the reason when it is missing. **Never fall back to free-form.**
3. Webhook handlers must return 200 quickly for authenticated requests (Meta retries for 7 days, MSG91 auto-pauses, Twilio expects 200). The existing de-duplication on (provider, id, status) is correct.
4. Cost line for slides: utility ₹0.115 per delivered message (Meta, ex-GST) plus $0.005 if sent through Twilio.

### Unresolved
- Whether Meta would classify a FloodNet advisory template as utility.
- The 1,000-free-service-messages claim.
- Whether `TWILIO_WHATSAPP_FROM` is expected to include the `whatsapp:` prefix. The code adds it to `To` only, so this must be checked against `.env.example`; values were not read.

---

## 3. Government pathway (SACHET, cell broadcast, DoT/C-DOT)

### Verified facts
- SACHET is NDMA's CAP-based Integrated Alert System. "The alert will be published on the RSS feed as well, and agencies (News agencies etc.) subscribed for the RSS feed will get alerts and they can further disseminate/broadcast the warning to the public" [V] https://sachet.ndma.gov.in/
- **Maharashtra RSS feed** (fetched today) [V] https://sachet.ndma.gov.in/cap_public_website/rss/rss_maharashtra.xml
  - RSS 2.0, titled "Maharashtra: CAP Disaster Alert Feeds", `copyright` "public domain", 10 items.
  - Each item `link` points to the full CAP XML (`…/FetchXMLFile?identifier=<id>`).
  - `author` names the issuer, e.g. "controlroom@ndma.gov.in (IMD Mumbai)", with category "Met".
  - The latest item was pubDate 5 Oct 2026, in Marathi.
- HTTP behaviour (curl, today) [V]:
  - `GET` returns 200 with `ETag: W/"…"`, `Last-Modified` and `Cache-Control: no-store`.
  - **A conditional GET with `If-None-Match` returns 304.**
  - **`HEAD` returns 403** (WAF).
  - A request with a browser User-Agent did not complete, and DNS resolution was intermittent.
- SACHET operates in **all 36 States/UTs** and sends SMS to "mobiles of citizens affected in a particular geo-targeted area". It has been "used by disaster management authorities to issue over 6,899 crore SMS alerts in more than 19 Indian languages".
  - Cell Broadcast (CB) is being implemented "in addition to SMS"; C-DOT develops it. Nationwide CB testing took 2–4 weeks from 30 Jun 2025 (PIB, 30 Jun 2025) [V] https://www.pib.gov.in/PressReleasePage.aspx?PRID=2140843
- **Cell Broadcast System formally launched 2 May 2026** by the Minister of Communications. It was developed by C-DOT under DoT with NDMA and MHA, and is "Integrated with the CAP-based SACHET platform".
  - Alerts are geo-targeted to cell towers or clusters, reach roaming users, "cannot be disabled by users", and are read aloud on supported handsets.
  - Pan-India trials are complete, with training in all States/UTs (PIB, 2 May 2026) [V] https://www.pib.gov.in/PressReleasePage.aspx?PRID=2257499
  - Earlier regional CB tests were held in 2023 (e.g. Gujarat on 16 Oct 2023, Tamil Nadu on 20 Oct 2023) [S, PIB search results].
- TRAI 2025: a "Government Message" (-G) is one sent on the directions of government or constitutional bodies through DLT, cannot be blocked and needs no consent [V, §1].

### Verdicts on prior research
- OWP / LOR §7 "SACHET … C-DOT … RSS public domain … ETag/If-None-Match": **CONFIRMED** (now tested directly, not only "per research agent").
- OWP "Cell broadcast is government-only [S]": **REFINED to [V]**. PIB describes CBS as a system "that enables authorities to broadcast", integrated with SACHET. No route for non-government originators is described.
- OWP "DoT role [UNVERIFIED]": **RESOLVED**. DoT (through C-DOT) built and operates the CB and SACHET technology. NDMA and the SDMAs are the users/originators.
- OWP "No documented route for an NGO to originate": **still UNVERIFIED**. Nothing found either way.

### Proposed for FloodNet
1. Hard-code nothing, but document `https://sachet.ndma.gov.in/cap_public_website/rss/rss_maharashtra.xml` as the verified default for `SACHET_RSS_URL`.
2. Never use `HEAD`. Keep the conditional GET.
3. Show the item `author` (the issuing agency) on every OFFICIAL item.
4. Optionally fetch the item's CAP XML (`FetchXMLFile`) for `area` and `expires`. This needs the same XML hardening as the RSS parser, and should be read-only and rate-limited.
5. The pathway wording in OWP should add: "Official geo-targeted alerts in India reach phones through SACHET SMS and, since 2 May 2026, Cell Broadcast. Both are operated for NDMA/SDMAs. FloodNet can only consume, draft (CAP `status=Draft`) and send its own opt-in advisories."
6. For Mumbai, the feed already carries IMD Mumbai nowcasts. Showing them next to FloodNet's model output is the most defensible "official pathway" demo.

### Unresolved
- Whether SACHET has a documented integration guide for CAP XML consumers, and any rate-limit policy.
- Whether the WAF blocks cloud-hosting IPs (Vercel or Render) as it blocked HEAD.
- Whether CB messages appear in the RSS feed.

---

## 4. International resource coordination

### Verified facts
- FEMA RTLT **Swiftwater/Flood Search and Rescue Team** (8-508-1020) [V] https://rtlt.preptoolkit.fema.gov/Public/Resource/ViewFile/8-508-1020?type=Pdf
  - Category is Search and Rescue; Kind is Team.
  - Minimum personnel 16/12/6 for Types 1/2/3; "Type 1 is the highest qualification level"; each type builds on the one below.
  - Ordering specifications ask the requester to state:
    - whether **night operations** are necessary;
    - the **animal rescue** type;
    - **HAZMAT/contamination** potential;
    - **self-sufficiency** duration;
    - additional transport (boats, trailers) and aviation support.
  - The team can transport rescued people **by water only**. "Requestor acquires secondary land or air transport separately".
  - Type 3 capability includes paddle and powerboat operations and BLS-level medical care.
  - Equipment includes marine-band radio, a handheld GPS and a waterproof phone per boat crew.
- LOR §1 (NIMS 3rd ed., resource status Assigned/Available/Out of Service, the six resource-management steps): **CONFIRMED by prior [V]; not re-fetched**.
- SOBO-WEB [V] https://www.bousai.go.jp/taisaku/soboweb/index.html
  - Operating since April 2024, used by about 1,900 bodies.
  - Aggregates information based on the EEI (Essential Elements of Information, defined in FY2023) and shares disaster information **as geospatial information**.
  - The Cabinet Office aims to complete the disaster digital platform by December of FY2025.
  - LOR §2: **CONFIRMED**.
- China (LOR §3): not re-checked in this pass.

### Proposed for FloodNet (small registry additions; no compliance claim)
- Add these registry fields:
  - `capabilities[]` from a fixed list: `boat_paddle`, `boat_powered`, `high_clearance_vehicle`, `bls_medical`, `animal_rescue`, `night_ops`, `hazmat_contaminated_water`, `rope_rescue`;
  - `min_personnel`;
  - `self_sufficiency_hours`;
  - `comms[]` (`radio`, `marine_radio`, `gps`, `phone`).
- Add a rule that a water-rescue unit's trip **must have an onward land-transport leg**, following the RTLT note.
- Add "Typed by FloodNet (inspired by FEMA RTLT), not NIMS-certified" to the registry footer.
- Need requests carry `night_ops_required` and `animal_rescue_type`, mirroring the RTLT ordering specifications.
- Every shared record keeps source, observation time, receipt time and a correction link (already adopted, D-32), in line with SOBO-WEB's EEI-based sharing.

### Unresolved
- No Indian equivalent typing (NDRF/SDRF flood rescue team composition) was found in this pass [UNVERIFIED].

---

## 5. UX and QA

### Verified facts
- WCAG 2.2 (W3C Recommendation; the TR page is dated 12 Dec 2024) [V] https://www.w3.org/TR/WCAG22/
  - 1.4.1 Use of Color (A).
  - 1.4.3 Contrast at least **4.5:1**, or **3:1** for large text (AA).
  - 1.4.10 Reflow at **320 CSS px** without two-dimensional scrolling (AA).
  - 1.4.11 Non-text contrast **3:1** (AA).
  - 1.4.12 Text Spacing (AA).
  - 1.4.13 Content on Hover/Focus (AA).
  - 2.2.2 Pause, Stop, Hide for auto-moving or updating content lasting **more than 5 s** (A).
  - 2.4.7 Focus Visible (AA).
  - **2.4.11 Focus Not Obscured (Minimum), AA**: a focused component is "not entirely hidden due to author-created content".
  - 2.4.13 Focus Appearance is **AAA**.
  - **2.3.3 Animation from Interactions is AAA, not AA.**
- 2.5.8 Target Size (Minimum), AA: **at least 24×24 CSS px**. The exceptions are Spacing (a 24-px circle that does not intersect another target), Equivalent, Inline, User-agent control and Essential. **Map pins fall under Essential** (from the Understanding doc) [V] https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
- 2.4.11: sticky headers and footers and non-modal overlays are the typical failures. **CSS `scroll-padding` (technique C43)** is a sufficient technique [V] https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html
- `prefers-reduced-motion` is technique C39 for SC 2.3.3 [V] https://www.w3.org/WAI/WCAG22/Techniques/css/C39
- Core Web Vitals "good": **LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1, at the 75th percentile, segmented mobile/desktop** [V] https://web.dev/articles/vitals
- Animation durations: most should be **100–500 ms**; simple feedback about 100 ms; substantial changes such as a modal **200–300 ms**; 400 ms is "very slow"; exits are shorter than entrances (NN/g) [V] https://www.nngroup.com/articles/animation-duration/
- Status indicators (IBM Carbon) [V] https://carbondesignsystem.com/patterns/status-indicator-pattern/
  - Do not rely on colour alone. Use at least two of colour, shape and symbol, plus a text label.
  - Keep **3:1** contrast between status colours and against the background.
  - A grouped indicator takes the highest-attention colour.
- Warning content research:
  - A content analysis of 6,080 US WEAs found only **8.5% complete**, meaning they did not include all five of hazard, location, source, guidance and time. Source and time were missing most often, and 360-character messages were more often complete [V abstract]. Olson et al. 2023, *J. Contingencies Crisis Manag.*, https://consensus.app/papers/details/0a9a1ea2b23a5b34bc73c72acd80f50c/
  - Longer messages reduced milling (Wood et al. 2018, *Environment and Behavior*) [V abstract]. https://consensus.app/papers/details/91a33e3eba3559fd933342573e4ba682/
  - **Counter-evidence:** a 90-character message was not less effective than 360 characters; personalisation and hazard experience mattered more (Carlson et al. 2024, N = 481) [V abstract]. https://consensus.app/papers/details/b495e9ae16dc58269c601f61da178977/
  - Protective-action guidance in **both text and graphic** improved understanding and deciding (Fischer et al. 2023, *Weather, Climate, and Society*) [V abstract]. https://consensus.app/papers/details/2a0d9b3e67c45e80a3a06cc4a96a7854/
  - Time and action indications increased understanding and intention to act (Dallo et al. 2022, *IJDRR*, N = 601) [V abstract]. https://consensus.app/papers/details/0eed783247f55518b5bf8e871330caf3/
  - German respondents preferred bullet-point actions (85%) and maps (n = 131, exploratory) (Lindenlaub et al. 2024, *IJDRR*) [V abstract]. https://consensus.app/papers/details/3d76210401c25cd2a5d22a19bc86540d/

### Verdicts on prior research
- LOR §8 "WCAG 2.2: targets ≥ 24×24, no colour-only meaning, reflow at 320, reduced motion": **CONFIRMED with a correction**. Reduced motion is **AAA (2.3.3)**, not an AA requirement. It is still worth doing, but must not be claimed as "AA compliance".
- LOR §8 Sutton et al. "complete warnings name source, hazard, location, action, time": **CONFIRMED** by Olson et al. 2023. The message-length question is now **contested** (Carlson 2024), which makes the SMS-length trade-off a judgement call, not settled science.
- GOV.UK check-for-flooding: **not re-fetched** (LOR §8 [V] stands).

### Measurable acceptance criteria (PROPOSED; thresholds marked * are our choices, not from a source)

**Public alert page (`/alerts/<id>`, mobile)**

| ID | Criterion | Test |
|---|---|---|
| UX-01 | LCP ≤ 2.5 s, CLS ≤ 0.1 (web.dev), measured in a Lighthouse mobile lab run (simulated slow 4G, 4× CPU) | Lighthouse CI |
| UX-02 | Total Blocking Time ≤ 200 ms* (lab proxy for INP ≤ 200 ms) | Lighthouse CI |
| UX-03 | JS transferred for this route ≤ 100 KB gzip*. No map library or tiles before the text content renders | Playwright network log: sum of `script` responses |
| UX-04 | Total transfer ≤ 300 KB* on first load; page readable with JS disabled or with the API failing (shows "Could not load; see SACHET / call 112") | Playwright |
| UX-05 | The page shows, as text: **source** (FloodNet, student prototype), **hazard**, **location in words**, **protective action**, **issued time**, **next update or expiry**, status (active or "No longer active" with time), links to SACHET and BMC, and 112/1916 | DOM assertions on fixture alerts |
| UX-06 | Text contrast ≥ 4.5:1 (≥ 3:1 for ≥ 24 px, or ≥ 18.66 px bold) in light and dark themes | axe-core `color-contrast` = 0 violations |
| UX-07 | Every interactive target ≥ 24×24 CSS px or meets the spacing exception. Primary actions (call 112/1916, share) ≥ 44×44* | Playwright bounding boxes |
| UX-08 | No horizontal scroll at a 320 px viewport (`scrollWidth ≤ clientWidth`) and at 200% zoom | Playwright |
| UX-09 | Text-spacing override (line 1.5, paragraph 2, letter 0.12, word 0.16 em) causes no clipped content | Playwright with injected CSS, then screenshot diff and overflow check |
| UX-10 | Open Graph title and description contain no time or severity (WhatsApp caches previews; LOR §8 [S]) | Unit test on the HTML head |

**Operator dashboard (map + panels)**

| ID | Criterion | Test |
|---|---|---|
| UX-11 | axe-core: 0 serious or critical violations on each of the six pages | axe in Playwright |
| UX-12 | Every status chip has a text label plus an icon or shape. Chip colours have ≥ 3:1 against the background and against each other | Visual review plus contrast script |
| UX-13 | Every live data panel shows source, "as of" time and age. The age switches to a STALE state at the D-32 thresholds | Fixture with stale timestamps |
| UX-14 | Delivery counts show ACCEPTED, SENT, DELIVERED, FAILED/REJECTED and UNKNOWN separately. No UI string calls ACCEPTED or SENT "delivered" | Unit test plus copy grep |
| UX-15 | Tab through every page: the focus indicator is visible (2.4.7). The focused element is never entirely hidden by sticky headers, drawers or the map overlay (2.4.11): the intersection with sticky elements is below 100% of its rectangle | Playwright focus walk |
| UX-16 | Transitions are 100–300 ms (≤ 400 ms maximum). Under `prefers-reduced-motion: reduce`, non-essential transforms and animations are disabled (C39; 2.3.3 AAA, so best effort) | Playwright `emulateMedia({reducedMotion:'reduce'})` plus computed-style check |
| UX-17 | Anything auto-moving or auto-updating for more than 5 s (pulsing markers, tickers) has a pause control or stops (2.2.2) | Manual plus Playwright |
| UX-18 | New-alert, approval and send-result messages are announced through `role="status"` or `aria-live` (4.1.3) | DOM assertion |
| UX-19 | Map: every pin is also reachable as a list item with the same information (2.5.8 Equivalent and keyboard access) | Playwright |

**Message content**

| ID | Criterion | Test |
|---|---|---|
| UX-20 | English SMS ≤ 160 GSM-7 characters. Hindi/Marathi (UCS-2) ≤ 3 segments (≤ 201 characters)*. Each message includes source, area, action, time and link | Unit test on rendered texts |
| UX-21 | No shortened URLs in any SMS (Twilio India rule; DLT whitelisting) | Unit test |

### Unresolved
- No primary research was found on "freshness indicator" thresholds for crisis dashboards. The staleness tiers stay our own policy (D-32).
- The JS and transfer budgets (*) are engineering choices that need a baseline measurement of the current build.

---

## Summary of changes to prior evidence

| Prior claim | Verdict |
|---|---|
| Real Indian SMS BLOCKED (MPA) | **Refined**: DLT route blocked; Twilio international route (no DLT, ~$0.0832, random sender, upgraded account) is possible |
| Twilio trial: verified numbers only, no custom bodies | Confirmed (also: sign-up country only, 5 recipients, 30 days) |
| MSG91 statuses; no webhook signature | Confirmed; plus auto-pause on 4xx/5xx and custom headers |
| WhatsApp costs (implicitly free service window) | **Outdated**: service and in-window utility are charged from 1 Oct 2026 |
| WhatsApp free-form send works for alerts | **Contradicted** outside a 24-h CSW; templates are needed |
| SACHET RSS public domain, ETag | Confirmed by direct test (HEAD is blocked) |
| Cell broadcast government-only [S] | Confirmed [V]; CBS launched 2 May 2026, integrated with SACHET |
| DoT role unverified | Resolved: DoT/C-DOT build and operate SACHET and CBS for NDMA/SDMAs |
| WCAG "reduced motion" as a requirement | Corrected: SC 2.3.3 is AAA |
| Sutton: complete warnings | Confirmed (Olson 2023); message length now contested (Carlson 2024) |
