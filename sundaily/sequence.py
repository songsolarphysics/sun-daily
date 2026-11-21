"""In-memory image sequence shared by every source: frames plus what is needed to draw them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass
class Frame:
    time: datetime               # aware UTC
    data: np.ndarray             # float32, 2-D, exposure-normalised


@dataclass
class Sequence:
    frames: list[Frame]
    label: str                   # stamp text, e.g. "SDO/AIA 171 Å"
    cmap: str                    # sunpy colormap name
    arcsec_per_px: float
    km_per_arcsec: float         # on the Sun, for this observer
    observer: str                # "SDO", "Solar Orbiter"
    dsun_au: float
    center_hpc: tuple[float, float] | None = None   # FOV centre at mid-sequence [arcsec]
    stretch: str = "sqrt"        # sqrt | asinh | log | linear
    pct: tuple[float, float] = (1.0, 99.9)
    roll_deg: float = 0.0        # residual rotation of solar north from image-up
    notes: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def shape(self) -> tuple[int, int]:
        return self.frames[0].data.shape

    @property
    def cadence_s(self) -> float:
        if len(self.frames) < 2:
            return 0.0
        dt = np.diff([f.time.timestamp() for f in self.frames])
        return float(np.median(dt))

    @property
    def span_s(self) -> float:
        return (self.frames[-1].time - self.frames[0].time).total_seconds()

    @property
    def fov_arcsec(self) -> tuple[float, float]:
        ny, nx = self.shape
        return nx * self.arcsec_per_px, ny * self.arcsec_per_px

    @property
    def mm_per_px(self) -> float:
        return self.arcsec_per_px * self.km_per_arcsec / 1000.0
