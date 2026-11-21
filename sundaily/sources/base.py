"""Interface every data source implements.

A source knows three things: how to propose something worth showing (from an event or
observation catalogue), how to download the science files for it, and how to turn those files
into a Sequence. Adding an instrument means adding one subclass and one line in pool.yaml.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from ..sequence import Sequence
from ..ticket import Ticket


class Source:
    key: str = ""                # registry key, used in pool.yaml and tickets
    name: str = ""               # display name
    instrument: str = ""         # "SDO/AIA"
    blurb: str = ""              # one line for the source panel
    requires_env: tuple[str, ...] = ()

    def __init__(self, cfg: dict | None = None, state_dir: Path | None = None):
        self.cfg = cfg or {}
        self.state_dir = Path(state_dir) if state_dir else Path("state")

    # -- availability -----------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        missing = [v for v in self.requires_env if not os.environ.get(v, "").strip()]
        if missing:
            return False, f"needs {', '.join(missing)}"
        return True, ""

    # -- the three steps --------------------------------------------------------
    def propose(self, rng: random.Random, seen: set[str], override: str | None = None) -> list[Ticket]:
        """Candidate tickets, best first. ``override`` forces a time or event id."""
        raise NotImplementedError

    def fetch(self, ticket: Ticket, workdir: Path, max_bytes: float) -> list[Path]:
        """Download the science files for a ticket into workdir."""
        raise NotImplementedError

    def load(self, ticket: Ticket, files: list[Path]) -> Sequence:
        """Read the files into an exposure-normalised, display-ready Sequence."""
        raise NotImplementedError

    # -- card helpers -----------------------------------------------------------
    def tag(self, ticket: Ticket, seq: Sequence) -> str:
        """Short grey tag drawn in the bottom-right corner of the clip."""
        return ""

    def links(self, ticket: Ticket, seq: Sequence) -> dict:
        return {}
