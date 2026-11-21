"""The card: everything known about one clip, as data. The gallery and the push read this."""

from __future__ import annotations

import os

import numpy as np

from . import __version__
from .render import plain
from .sequence import Sequence
from .ticket import Ticket
from .util import Unavailable, iso, parse_time, utcnow


def quality(seq: Sequence, min_frames: int = 24) -> dict:
    """Automatic checks on a loaded sequence. Raises Unavailable if it cannot make a clip."""
    # drop exact duplicate timestamps, keep order
    uniq, last = [], None
    for f in seq.frames:
        if last is None or f.time > last:
            uniq.append(f)
            last = f.time
    seq.frames[:] = uniq
    n = len(seq)
    if n < min_frames:
        raise Unavailable(f"only {n} usable frames (need {min_frames})")
    t = np.array([f.time.timestamp() for f in seq.frames])
    dt = np.diff(t)
    med = float(np.median(dt))
    means = np.array([float(np.nanmean(f.data[::4, ::4])) for f in seq.frames])
    rel = np.abs(np.diff(means)) / np.maximum(np.abs(means[:-1]), 1e-9)
    nan_frac = max(float(np.isnan(f.data[::4, ::4]).mean()) for f in seq.frames)
    return {
        "frames": n,
        "gaps": int((dt > 4 * med).sum()),
        "longest_gap_s": round(float(dt.max()), 1),
        "brightness_jump_median": round(float(np.median(rel)), 4),
        "brightness_jump_max": round(float(rel.max()), 3),
        "nan_fraction_max": round(nan_frac, 4),
    }


def _long_date(t) -> str:
    return f"{t.day} {t.strftime('%B %Y')}"


def caption(ticket: Ticket, seq: Sequence, info: dict) -> str:
    """Two or three factual sentences, built only from the catalogue entry and the data."""
    ev = ticket.event or {}
    w_as, h_as = seq.fov_arcsec
    w_mm = w_as * seq.km_per_arcsec / 1000.0
    span_min = seq.span_s / 60.0
    parts = []
    if ev.get("type") == "FL":
        peak = parse_time(ev["peak"])
        where = f" in active region {ev['ar']}" if ev.get("ar") else ""
        parts.append(f"{ev.get('goes_class') or 'A'} flare{where}, peaking at {peak:%H:%M} UT "
                     f"on {_long_date(peak)}.")
    elif ev.get("type") in ("FE", "CJ"):
        name = {"FE": "Filament eruption", "CJ": "Coronal jet"}[ev["type"]]
        t0, t1 = parse_time(ev["start"]), parse_time(ev["end"])
        where = f" in active region {ev['ar']}" if ev.get("ar") else ""
        parts.append(f"{name}{where}, reported from {t0:%H:%M} to {t1:%H:%M} UT on {_long_date(t0)}.")
    elif ev.get("type") == "OBS":
        t0 = seq.frames[0].time
        parts.append(f"High-cadence observation on {_long_date(t0)}, with Solar Orbiter "
                     f"{seq.dsun_au:.2f} au from the Sun.")
        if ev.get("soop"):
            parts.append(f"Observing plan: {ev['soop']}.")
    fov = (f"{w_as:.0f}″ field of view ({w_mm:.0f} Mm)" if abs(w_as - h_as) < 0.02 * w_as else
           f"{w_as:.0f}″ × {h_as:.0f}″ field of view")
    parts.append(f"{plain(seq.label).replace('_', ' ')}, {fov}, one frame every {seq.cadence_s:.0f} s.")
    parts.append(f"{span_min:.0f} minutes of the Sun in {info['duration_s']:.0f} seconds.")
    return " ".join(parts)


def build_card(ticket: Ticket, seq: Sequence, info: dict, source, qc: dict,
               request_id: str | None = None) -> dict:
    date = ticket.date
    w_as, h_as = seq.fov_arcsec
    to_mm = seq.km_per_arcsec / 1000.0
    run_id = os.environ.get("GITHUB_RUN_ID")
    repo = os.environ.get("GITHUB_REPOSITORY")
    card = {
        "id": date,
        "date": date,
        "title": ticket.title,
        "caption": caption(ticket, seq, info),
        "source": ticket.source,
        "source_name": source.name,
        "instrument": source.instrument,
        "label": plain(seq.label),
        "event": ticket.event,
        "reason": ticket.reason,
        "obs": {
            "observer": seq.observer,
            "dsun_au": round(seq.dsun_au, 3),
            "t_start": iso(seq.frames[0].time),
            "t_end": iso(seq.frames[-1].time),
            "frames": len(seq),
            "cadence_s": round(seq.cadence_s, 1),
            "span_min": round(seq.span_s / 60.0, 1),
            "fov_arcsec": [round(w_as, 1), round(h_as, 1)],
            "fov_mm": [round(w_as * to_mm, 1), round(h_as * to_mm, 1)],
            "km_per_px": round(seq.mm_per_px * 1000.0, 1),
            "center_hpc": list(seq.center_hpc) if seq.center_hpc else None,
            "roll_deg": seq.roll_deg,
        },
        "clip": {
            "file": f"clips/{date}.mp4",
            "poster": f"posters/{date}.jpg",
            "width": info["width"], "height": info["height"], "fps": info["fps"],
            "duration_s": info["duration_s"], "size_bytes": info["size_bytes"],
            "speedup": round(seq.span_s / max(info["duration_s"], 0.1)),
            "scaling": {"stretch": info["stretch"], "asinh_a": info["asinh_a"],
                        "flare_capped": info["flare_capped"], "vmin": info["vmin"],
                        "vmax": info["vmax"], "percentiles": list(seq.pct), "cmap": seq.cmap},
        },
        "links": source.links(ticket, seq),
        "recipe": {"ticket": f"tickets/{date}.json", "command": f"sundaily fetch {date}"},
        "qc": qc,
        "notes": seq.notes,
        "pipeline": {
            "version": __version__,
            "sha": (os.environ.get("GITHUB_SHA") or "")[:7] or None,
            "run_url": f"https://github.com/{repo}/actions/runs/{run_id}" if run_id and repo else None,
            "generated": iso(utcnow()),
        },
    }
    if request_id:
        card["request_id"] = request_id
    return card
