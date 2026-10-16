"""Subscriber SMS/WhatsApp advisories and the public alert page (DECISIONS D-44, D-45, D-46).

  subscribers  consent records, encrypted numbers, opt-out (STOP) and DPDP erasure
  targeting    impact zone from the published forecast + closures; per-subscriber eligibility with a reason
  providers    provider interface: SIMULATED (default), Twilio and MSG91 adapters (UNVERIFIED live), status mapping,
               webhook signature checks, the live-sending gate
  alerts       alert lifecycle (review before any send), versions, cooldown, idempotent deliveries, callbacks
  public       the whitelisted public snapshot served at /alerts/<public_id>
  sachet       read-only consumer of the official SACHET (NDMA) CAP RSS feed

FloodNet is a student prototype, not an alerting authority. Design: docs/LIVE_OPERATIONS.md sections 6-7,
docs/MESSAGING_AND_PUBLIC_ALERTS.md.
"""
