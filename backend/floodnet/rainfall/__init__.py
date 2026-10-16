"""Rainfall provider abstraction (source of rainfall input, separate from the simulation engine).

See `floodnet.rainfall.provider` for the interface and the wired providers.
"""
from __future__ import annotations

from .provider import (
    RainfallProvider,
    RainfallSourceMeta,
    ScenarioProvider,
    HistoricalReplayProvider,
    ExternalNowcastProvider,
    list_providers,
    get_source_meta,
    provider_status,
)

__all__ = [
    "RainfallProvider",
    "RainfallSourceMeta",
    "ScenarioProvider",
    "HistoricalReplayProvider",
    "ExternalNowcastProvider",
    "list_providers",
    "get_source_meta",
    "provider_status",
]
