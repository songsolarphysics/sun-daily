"""Registry of data sources. A source enters the pool by being listed here and in pool.yaml."""

from __future__ import annotations

from pathlib import Path

from .aia import AiaCloseup, AiaSynoptic
from .base import Source
from .eui import EuiHri

REGISTRY: dict[str, type[Source]] = {cls.key: cls for cls in (AiaCloseup, AiaSynoptic, EuiHri)}


def make(key: str, settings: dict, state_dir: Path) -> Source:
    if key not in REGISTRY:
        raise KeyError(f"unknown source {key!r}; known: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[key]((settings.get("sources") or {}).get(key, {}), state_dir)
