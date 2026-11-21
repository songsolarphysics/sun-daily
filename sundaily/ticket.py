"""A ticket is one night's work order: which source, which event, which data window."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Ticket:
    source: str                      # registry key, e.g. "aia_closeup"
    title: str                       # short human title, e.g. "X4.9 flare in AR 11990"
    t_start: str                     # data window start, ISO UTC
    t_end: str                       # data window end, ISO UTC
    event: dict = field(default_factory=dict)    # catalogue entry behind the pick
    params: dict = field(default_factory=dict)   # source-specific request parameters
    reason: str = ""                 # one line: why this was drawn
    date: str = ""                   # delivery date YYYY-MM-DD (filled by the runner)
    seed: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def save(self, path: Path) -> None:
        Path(path).write_text(self.to_json() + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Ticket":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def event_key(self) -> str:
        """Identity used to avoid showing the same event twice."""
        ev = self.event or {}
        return ev.get("id") or f"{self.source}:{self.t_start}"
