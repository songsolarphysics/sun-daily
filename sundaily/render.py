"""Turn a Sequence into an MP4 clip and a poster frame.

Frames are drawn with numpy + Pillow (no matplotlib figure per frame), so the picture is the
data at a fixed scaling and the only things added are a stamp (instrument, wavelength, time),
a scale bar and one short tag. Text follows the house rule for dark images: white for what has
to be read, one grey for what supports it, each with a dark halo.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .process import global_limits, to_unit
from .sequence import Sequence
from .util import log, parse_time

WHITE = (255, 255, 255, 255)
GREY = (0xC3, 0xC2, 0xB7, 255)      # ink2 on dark surfaces
HALO = (0, 0, 0, 158)               # black at 62 %
FONT_FAMILIES = ("Helvetica Neue", "Helvetica", "Arial", "Liberation Sans", "DejaVu Sans")
NICE_MM = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000)


@lru_cache(maxsize=None)
def _font_path() -> str | None:
    from matplotlib import font_manager as fm
    for fam in FONT_FAMILIES:
        try:
            return fm.findfont(fm.FontProperties(family=fam), fallback_to_default=False)
        except Exception:  # noqa: BLE001
            continue
    return None


def _font(size: int) -> ImageFont.FreeTypeFont:
    path = _font_path()
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


@lru_cache(maxsize=None)
def _lut(name: str, n: int = 1024) -> np.ndarray:
    import matplotlib
    import sunpy.visualization.colormaps as sunpy_cm
    cmap = sunpy_cm.cmlist.get(name) or matplotlib.colormaps[name]
    return (cmap(np.linspace(0, 1, n))[:, :3] * 255 + 0.5).astype(np.uint8)


# ----------------------------------------------------------------------------- text

def _runs(text: str) -> list[tuple[str, bool]]:
    """Split 'HRI_{EUV} 174 Å' into [('HRI', False), ('EUV', True), (' 174 Å', False)]."""
    out, i = [], 0
    while i < len(text):
        j = text.find("_{", i)
        if j < 0:
            out.append((text[i:], False))
            break
        if j > i:
            out.append((text[i:j], False))
        k = text.find("}", j)
        out.append((text[j + 2:k], True))
        i = k + 1
    return out


def plain(text: str) -> str:
    return "".join(("_" + s) if sub else s for s, sub in _runs(text))


def _draw_text(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int, fill,
               align: str = "left") -> None:
    """Draw haloed text with its baseline at y. Supports _{subscript} runs."""
    font, sub = _font(size), _font(max(8, round(size * 0.72)))
    runs = _runs(text)
    width = sum(draw.textlength(s, font=sub if is_sub else font) for s, is_sub in runs)
    if align == "right":
        x -= width
    stroke = max(2, round(size * 0.09))
    for s, is_sub in runs:
        f = sub if is_sub else font
        dy = round(size * 0.22) if is_sub else 0
        draw.text((x, y + dy), s, font=f, fill=fill, anchor="ls",
                  stroke_width=stroke, stroke_fill=HALO)
        x += draw.textlength(s, font=f)


def _scale_bar(mm_per_px: float, width_px: int) -> tuple[int, int]:
    """(length in Mm, length in pixels): the largest round length within ~20 % of the width."""
    target = 0.20 * width_px * mm_per_px
    mm = max([v for v in NICE_MM if v <= target] or [NICE_MM[0]])
    return mm, max(2, round(mm / mm_per_px))


# ----------------------------------------------------------------------------- main

def render_clip(seq: Sequence, out_dir: Path, tag: str = "", peak_time: str | None = None,
                size: int = 1080, target_s: float = 14.0, crf: int = 21,
                max_mbps: float = 6.0, asinh_a: float = 0.03) -> dict:
    """Write clip.mp4 and poster.jpg into out_dir; return what was made."""
    import imageio_ffmpeg

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clip, poster = out_dir / "clip.mp4", out_dir / "poster.jpg"

    ny, nx = seq.shape
    k = size / max(ny, nx)
    W, H = int(round(nx * k / 2)) * 2, int(round(ny * k / 2)) * 2
    resample = Image.Resampling.LANCZOS if k < 1 else Image.Resampling.BICUBIC

    n = len(seq)
    fps = int(min(30, max(8, round(n / target_s))))
    vmin, vmax = global_limits(seq.frames, seq.pct, transient_ratio=seq.transient_ratio)
    lut = _lut(seq.cmap)
    nlut = len(lut) - 1

    # static layer: scale bar, its label, the tag
    m = round(0.024 * min(W, H))
    big, small = round(0.026 * H), round(0.022 * H)
    static = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(static)
    mm, bar_px = _scale_bar(seq.mm_per_px / k, W)
    bar_h = max(3, round(0.004 * H))
    y_bar = H - m - bar_h
    d.rectangle([m - 2, y_bar - 2, m + bar_px + 2, y_bar + bar_h + 2], fill=HALO)
    d.rectangle([m, y_bar, m + bar_px, y_bar + bar_h], fill=WHITE)
    _draw_text(d, m, y_bar - round(0.5 * small), f"{mm} Mm", small, WHITE)
    if tag:
        _draw_text(d, W - m, H - m, tag, small, GREY, align="right")

    if peak_time:
        tp = parse_time(peak_time).timestamp()
        i_poster = int(np.argmin([abs(f.time.timestamp() - tp) for f in seq.frames]))
    else:
        i_poster = n // 2

    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-an", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
           "-maxrate", f"{max_mbps}M", "-bufsize", f"{2 * max_mbps}M",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(clip)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i, fr in enumerate(seq.frames):
            unit = to_unit(fr.data, vmin, vmax, seq.stretch, asinh_a)
            img = Image.fromarray(np.ascontiguousarray(unit[::-1]))      # FITS row 0 is the bottom
            if (W, H) != (nx, ny):
                img = img.resize((W, H), resample)
            idx = (np.clip(np.asarray(img), 0.0, 1.0) * nlut + 0.5).astype(np.intp)
            frame = Image.fromarray(lut[idx], "RGB").convert("RGBA")
            layer = static.copy()
            _draw_text(ImageDraw.Draw(layer), W - m, m + big,
                       f"{seq.label}  {fr.time:%Y-%m-%d %H:%M:%S} UT", big, WHITE, align="right")
            frame.alpha_composite(layer)
            rgb = frame.convert("RGB")
            if i == i_poster:
                rgb.save(poster, quality=90)
            proc.stdin.write(rgb.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err[-500:]}")
    finally:
        if proc.poll() is None:
            proc.kill()

    info = {
        "width": W, "height": H, "fps": fps, "frames": n,
        "duration_s": round(n / fps, 2),
        "size_bytes": clip.stat().st_size,
        "vmin": round(vmin, 4), "vmax": round(vmax, 4), "stretch": seq.stretch,
        "scale_bar_mm": mm, "poster_frame": i_poster,
    }
    log.info("rendered %dx%d, %d frames at %d fps = %.1fs, %.1f MB", W, H, n, fps,
             info["duration_s"], info["size_bytes"] / 1e6)
    return info
