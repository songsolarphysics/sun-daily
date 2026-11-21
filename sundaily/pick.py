"""The nightly draw: which source goes first, honouring the pool, requests and recent history."""

from __future__ import annotations

import json
import random
from pathlib import Path

import yaml

from .util import log


def load_yaml(path: Path, default):
    path = Path(path)
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text(encoding="utf-8")) or default


def load_history(gallery: Path | None) -> list[dict]:
    """Cards already published, newest first."""
    if not gallery:
        return []
    path = Path(gallery) / "data" / "cards.json"
    if not path.exists():
        return []
    try:
        cards = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("cards.json is unreadable; continuing without history")
        return []
    return sorted(cards, key=lambda c: c.get("date", ""), reverse=True)


def seen_events(history: list[dict]) -> set[str]:
    return {c.get("event", {}).get("id") for c in history if c.get("event", {}).get("id")}


def pending_request(requests_cfg: dict, history: list[dict]) -> dict | None:
    """First request in config/requests.yaml that no published card has fulfilled yet."""
    done = {c.get("request_id") for c in history if c.get("request_id")}
    for req in (requests_cfg or {}).get("requests") or []:
        if req.get("id") and req["id"] not in done:
            return req
    return None


def draw_order(pool: dict, history: list[dict], rng: random.Random, recent: int = 4,
               damping: float = 0.4) -> list[str]:
    """Enabled sources in the order they should be tried.

    A weighted draw without replacement. A source's weight is damped for each time it appeared
    in the last ``recent`` clips, so the week stays varied without forbidding repeats.
    """
    entries = {k: v for k, v in (pool.get("sources") or {}).items()
               if v.get("enabled", True) and float(v.get("weight", 1)) > 0}
    last = [c.get("source") for c in history[:recent]]
    weights = {k: float(v.get("weight", 1)) * damping ** last.count(k) for k, v in entries.items()}
    order = []
    while weights:
        keys = list(weights)
        k = rng.choices(keys, [weights[x] for x in keys])[0]
        order.append(k)
        del weights[k]
    return order
