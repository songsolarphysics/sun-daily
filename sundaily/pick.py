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


MAX_REQUEST_NIGHTS = 2


def load_request_state(state_dir: Path) -> dict:
    path = Path(state_dir) / "requests_state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}


def note_request_failure(state_dir: Path, request_id: str, why: str) -> None:
    """Count a night on which a request could not be served."""
    state = load_request_state(state_dir)
    entry = state.setdefault(request_id, {"nights": 0})
    entry["nights"] += 1
    entry["last_error"] = why[:300]
    if entry["nights"] >= MAX_REQUEST_NIGHTS:
        entry["given_up"] = True
    path = Path(state_dir) / "requests_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def pending_request(requests_cfg: dict, history: list[dict], state: dict | None = None) -> dict | None:
    """First request in config/requests.yaml that is neither shown yet nor given up on."""
    done = {c.get("request_id") for c in history if c.get("request_id")}
    state = state or {}
    for req in (requests_cfg or {}).get("requests") or []:
        rid = req.get("id")
        if rid and rid not in done and not state.get(rid, {}).get("given_up"):
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
