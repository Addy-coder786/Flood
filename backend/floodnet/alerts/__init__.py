"""Alert drafting for FloodNet.

This package turns a completed `SimulationResult` into a **draft** message in the OASIS Common Alerting
Protocol (CAP) v1.2 format.

WHAT THIS IS NOT
----------------
FloodNet is not a designated alerting authority. Under India's disaster-management arrangements, public
warnings are issued by authorised agencies (NDMA/SDMA/IMD/CWC and the like) through their own systems --
NDMA's SACHET/CAP platform being the national aggregator. FloodNet has no SACHET credential, no
integration path, no phone numbers and no cell-broadcast access, and nothing in this package sends
anything anywhere. It produces a *file* that an authorised officer can read, edit and, if they judge it
warranted, issue through their own system under their own authority.

Every message this package emits therefore carries CAP `status = Draft` -- which the specification itself
defines as "A preliminary template or draft, not actionable in its current form" -- plus an explicit
machine-readable marker parameter and an `<code>` flag saying the same thing.
"""
from __future__ import annotations

from .cap import CAP_NS, CapAlert, build_cap_alert

__all__ = ["CAP_NS", "CapAlert", "build_cap_alert"]
