"""Image processing shared by the sources: resampling, de-jittering, one scaling per sequence."""

from __future__ import annotations

import numpy as np

from .util import log
from .vendor.solar_align import ch_alignoffset, ch_shift_sub


# ----------------------------------------------------------------------------- resampling

def bin_to(data: np.ndarray, max_side: int) -> tuple[np.ndarray, int]:
    """Block-average by an integer factor so the longer side is <= max_side (NaN-aware)."""
    ny, nx = data.shape
    k = int(np.ceil(max(ny, nx) / max_side))
    if k <= 1:
        return data.astype(np.float32, copy=False), 1
    ny2, nx2 = (ny // k) * k, (nx // k) * k
    blocks = data[:ny2, :nx2].reshape(ny2 // k, k, nx2 // k, k)
    with np.errstate(invalid="ignore"):
        out = np.nanmean(blocks, axis=(1, 3))
    return out.astype(np.float32), k


# ----------------------------------------------------------------------------- de-jitter

class Stabilizer:
    """Remove frame-to-frame pointing jitter from a sequence fed one frame at a time.

    Offsets are measured between consecutive frames with solar_align's FFT cross-correlation
    (``ch_alignoffset``) on the central part of the image and accumulated into a trajectory;
    every frame is then resampled once, from its original data, by its accumulated offset.
    Consecutive-frame matching follows the evolving Sun, and the slow drift it also removes
    (solar rotation across the field of view) is what a viewer wants removed anyway.
    """

    def __init__(self, crop: int = 1024, max_step_px: float = 60.0):
        self.crop = crop
        self.max_step = max_step_px
        self._prev = None
        self._prev_aligned = None
        self.track = []          # accumulated (dx, dy) per frame, in native pixels
        self.residual = []       # offset left between consecutive frames after the shift
        self.rejected = 0

    def _core(self, data: np.ndarray) -> np.ndarray:
        ny, nx = data.shape
        c = min(self.crop, ny, nx)
        y0, x0 = (ny - c) // 2, (nx - c) // 2
        core = data[y0:y0 + c, x0:x0 + c].astype(np.float32)
        return np.sqrt(np.clip(core, 0, None))     # compress bright kernels before correlating

    def step(self, data: np.ndarray) -> np.ndarray:
        """Feed the next native-resolution frame; returns it shifted onto the first frame."""
        core = self._core(data)
        if self._prev is None:
            self.track.append((0.0, 0.0))
        else:
            try:
                (dx, dy), _ = ch_alignoffset(core, self._prev, compute_cor=False)
            except Exception as err:  # noqa: BLE001
                log.warning("alignment failed on frame %d: %s", len(self.track), err)
                dx = dy = 0.0
            if not np.isfinite(dx + dy) or max(abs(dx), abs(dy)) > self.max_step:
                self.rejected += 1
                dx = dy = 0.0
            px, py = self.track[-1]
            self.track.append((px + dx, py + dy))
        self._prev = core
        tx, ty = self.track[-1]
        if tx == 0.0 and ty == 0.0:
            out = data.astype(np.float32, copy=False)
        else:
            out = ch_shift_sub(data.astype(np.float32), -tx, -ty, order=1, cval=np.nan)
        # check: what offset is left between this frame and the previous one, once shifted?
        aligned = self._core(out)
        if self._prev_aligned is not None:
            try:
                (rx, ry), _ = ch_alignoffset(aligned, self._prev_aligned, compute_cor=False)
                if np.isfinite(rx + ry):
                    self.residual.append((rx, ry))
            except Exception:  # noqa: BLE001
                pass
        self._prev_aligned = aligned
        return out

    def valid_box(self, shape: tuple[int, int], scale: int = 1) -> tuple[slice, slice]:
        """Region (in pixels binned by ``scale``) that holds data in every stabilised frame."""
        ny, nx = shape
        tr = np.array(self.track)
        # a frame shifted by -t has NaN where x - (-t) falls outside the image
        left = int(np.ceil(max(0.0, (-tr[:, 0]).max()))) + 1
        right = int(np.ceil(max(0.0, tr[:, 0].max()))) + 1
        bottom = int(np.ceil(max(0.0, (-tr[:, 1]).max()))) + 1
        top = int(np.ceil(max(0.0, tr[:, 1].max()))) + 1
        s = max(1, scale)
        ys = slice(int(np.ceil(bottom / s)), int(np.floor((ny - top) / s)))
        xs = slice(int(np.ceil(left / s)), int(np.floor((nx - right) / s)))
        return ys, xs

    def summary(self) -> dict:
        tr = np.array(self.track)
        step = np.diff(tr, axis=0) if len(tr) > 1 else np.zeros((1, 2))
        return {
            "frames": int(len(tr)),
            "rms_step_px": round(float(np.sqrt((step ** 2).sum(axis=1).mean())), 3),
            "total_drift_px": [round(float(tr[-1, 0]), 1), round(float(tr[-1, 1]), 1)],
            "residual_rms_step_px": (round(float(np.sqrt((np.array(self.residual) ** 2).sum(axis=1).mean())), 3)
                                     if self.residual else None),
            "rejected_steps": int(self.rejected),
        }


# ----------------------------------------------------------------------------- scaling

def global_limits(frames, pct=(1.0, 99.9), max_frames: int = 30, step: int = 3,
                  transient_ratio: float | None = None) -> tuple[float, float]:
    """One (vmin, vmax) for the whole sequence, from percentiles of a pixel sample.

    A flare can be thousands of times brighter than the region it happens in; percentiles
    over the whole sequence would then leave everything else black. With ``transient_ratio``
    the upper limit is capped at that multiple of the quiet-time bright level (the lower
    quartile, over frames, of each frame's 99.5th percentile), so the flare core saturates
    and the rest of the scene stays visible.
    """
    idx = np.unique(np.linspace(0, len(frames) - 1, min(max_frames, len(frames))).astype(int))
    sample = np.concatenate([frames[i].data[::step, ::step].ravel() for i in idx])
    sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(sample, pct)
    if not hi > lo:
        hi = lo + 1.0
    if transient_ratio:
        every = max(1, len(frames) // 80)
        with np.errstate(invalid="ignore"):
            per_frame = np.array([np.nanpercentile(f.data[::2 * step, ::2 * step], 99.5)
                                  for f in frames[::every]])
        quiet = float(np.nanpercentile(per_frame, 25))
        if quiet > lo and hi > lo + transient_ratio * (quiet - lo):
            log.info("scaling: flare-dominated sequence, vmax %.0f -> %.0f (quiet bright level %.0f)",
                     hi, lo + transient_ratio * (quiet - lo), quiet)
            hi = lo + transient_ratio * (quiet - lo)
    return float(lo), float(hi)


def to_unit(data: np.ndarray, vmin: float, vmax: float, stretch: str = "sqrt",
            a: float = 0.03) -> np.ndarray:
    """Map data to [0, 1] with a fixed stretch. NaN becomes 0 (black)."""
    x = (np.nan_to_num(data, nan=vmin) - vmin) / (vmax - vmin)
    np.clip(x, 0.0, 1.0, out=x)
    if stretch == "sqrt":
        np.sqrt(x, out=x)
    elif stretch == "asinh":
        x = np.arcsinh(x / a) / np.arcsinh(1.0 / a)
    elif stretch == "log":
        x = np.log1p(x / a) / np.log1p(1.0 / a)
    elif stretch != "linear":
        raise ValueError(f"unknown stretch {stretch!r}")
    return x.astype(np.float32, copy=False)
