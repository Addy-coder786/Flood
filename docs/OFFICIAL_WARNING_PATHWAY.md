# Official warning pathway (DECISIONS D-46)

**FloodNet has no authorisation to issue public warnings.** It is a student prototype. Sources:
`docs/LIVE_OPERATIONS_RESEARCH.md` §6-§8 (labels [V] verified, [S] secondary, [U] unverified are kept from there).

## Four different activities

| Activity | Who may do it | What FloodNet does |
|---|---|---|
| 1. Consume official alerts | Anyone (the SACHET RSS feed is public [V per research agent]) | Reads the SACHET CAP RSS feed (`messaging/sachet.py`, ETag / If-None-Match) and shows items labelled "OFFICIAL (SACHET/NDMA)", separate from its own advisories. No live fetch is made unless `SACHET_RSS_URL` is configured and a dispatcher asks for a refresh |
| 2. Draft CAP messages | Anyone; a draft is not a warning | `alerts/cap.py` produces CAP 1.2 messages with `status=Draft` only (CAP 1.2 defines Draft as "not actionable") for an authority to review [V CAP 1.2] |
| 3. Own subscriber notifications | An organisation messaging people who opted in, under telecom and data-protection rules | FloodNet advisories to consenting, verified subscribers, labelled "student prototype, not an official warning"; SIMULATED by default. Real Indian SMS needs DLT registration (TRAI TCCCPR 2025 [V]); WhatsApp needs opt-in and approved templates [V/S]; DPDP consent, withdrawal and erasure apply [V PIB explainer] |
| 4. Official issuance | Alerting authorities only | **Not done.** FloodNet never originates official warnings, never contributes to SACHET and never uses cell broadcast |

## The authorised path

- **SACHET** is NDMA's CAP-based Integrated Alert System, developed and maintained by **C-DOT**; alerts come from
  **NDMA**, the **State/UT disaster management authorities (SDMAs)** and alert-generating agencies such as IMD, CWC and
  INCOIS [V] https://sachet.ndma.gov.in/ .
- **Cell broadcast** is government-only [S].
- **DoT**: named in the project brief as part of the pathway; its specific role was not examined in our research
  [UNVERIFIED].
- No documented route lets a non-government organisation originate or contribute alerts [U].

For FloodNet output to become an official warning, an authorised body (for Mumbai, for example the Maharashtra SDMA,
or an alert-generating agency) would have to review it, take responsibility for it and issue it through its own
channel (SACHET). Whether and how such a body would accept input from a tool like FloodNet is not established.

## What FloodNet does to avoid confusion

- Its own words only: "Flooding possible – plan ahead", "Flooding likely – avoid the area", "No longer active";
  never "alert", "warning", colour codes or an authority's level names (D-45).
- Every message and page carries "FloodNet advisory (student prototype, not an official warning)" and points to
  SACHET and BMC Disaster Management (https://dm.mcgm.gov.in/) for official information.
- Verified emergency contacts only: 112 (MHA ERSS) and 1916 (BMC Emergency Operation Centre).
