"""Arrival-time road accessibility (MISC-04 Phase 1). See docs/ROAD_ACCESS.md.

events.py         timestamped road events (confirmed closures / reopenings) with no-future-information filtering
road_status.py    USABLE / AT_RISK / UNCERTAIN / CLOSED / UNKNOWN for one road segment over one time window
arrival_router.py arrival-aware routing and the current-status-only baseline it is compared against
"""
