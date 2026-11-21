"""SDO/AIA sources.

aia_closeup   full-resolution tracked cut-outs around an event, staged by the JSOC export
              system (needs a registered JSOC e-mail in the JSOC_EMAIL environment variable).
aia_synoptic  the public 1k x 1k, 2-minute "synoptic" level-1.5 series: the whole disk for a
              few hours around a large event. Needs no account.
"""

from __future__ import annotations

import math
import os
import random
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import numpy as np

from ..catalogs import goes_flux, hek_flares
from ..sequence import Frame, Sequence
from ..ticket import Ticket
from ..util import UTC, Unavailable, download_all, iso, last_image_hdu, log, parse_time, utcnow
from .base import Source

SDO_START = datetime(2010, 6, 1, tzinfo=UTC)
ARCSEC_KM = math.pi / 648000.0 / 1000.0        # metres of distance -> km per arcsec


# ----------------------------------------------------------------------------- event draw

def draw_flares(rng: random.Random, seen: set[str], min_class: str, n: int = 3,
                window_days: int = 30, tries: int = 12) -> list[dict]:
    """Up to n flares from random windows of the SDO era, weighted towards larger events."""
    end = utcnow() - timedelta(days=20)
    span = (end - SDO_START).days - window_days
    frms = ["SSW Latest Events"] * (tries // 2) + [None] * (tries - tries // 2)
    for attempt, frm in enumerate(frms, 1):
        t0 = SDO_START + timedelta(days=rng.uniform(0, span))
        try:
            events = hek_flares(t0, t0 + timedelta(days=window_days), min_class, frm=frm)
        except Unavailable as err:
            log.warning("HEK query failed (%s)", err)
            continue
        events = [e for e in events if e["id"] not in seen]
        log.info("HEK draw %d: %s +%dd  %d flares >= %s%s", attempt, t0.strftime("%Y-%m-%d"),
                 window_days, len(events), min_class, "" if frm else " (any catalogue)")
        if not events:
            continue
        fmin = goes_flux(min_class)
        weights = [1.0 + 1.5 * math.log10(max(goes_flux(e["goes_class"]) / fmin, 1.0)) for e in events]
        picked = []
        while events and len(picked) < n:
            i = rng.choices(range(len(events)), weights)[0]
            picked.append(events.pop(i))
            weights.pop(i)
        return picked
    raise Unavailable(f"no flares >= {min_class} found in HEK after {tries} draws")


def flare_near(when: datetime) -> dict:
    """The flare closest to a given time (for a requested event)."""
    events = hek_flares(when - timedelta(hours=8), when + timedelta(hours=8), "B1.0", frm=None)
    if not events:
        raise Unavailable(f"no HEK flare with a position within 8 h of {iso(when)}")
    return min(events, key=lambda e: abs((parse_time(e["peak"]) - when).total_seconds()))


def flare_title(ev: dict) -> str:
    title = f"{ev.get('goes_class') or 'Solar'} flare"
    return f"{title} in AR {ev['ar']}" if ev.get("ar") else title


def weighted_choice(rng: random.Random, table: dict):
    keys = list(table)
    return rng.choices(keys, [float(table[k]) for k in keys])[0]


def _read_aia(path: Path):
    """(time, exposure-normalised float32 image, header) from an AIA FITS file, or None."""
    from astropy.io import fits

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(path) as hdul:
            hdu = last_image_hdu(hdul)
            if hdu is None:
                return None
            hdr = hdu.header
            data = np.asarray(hdu.data, dtype=np.float32)
    exptime = float(hdr.get("EXPTIME") or 0.0)
    tobs = hdr.get("T_OBS") or hdr.get("DATE-OBS")
    if exptime <= 0.01 or not tobs:
        return None
    data[data < -30000] = np.nan
    if np.isfinite(data).mean() < 0.5:
        return None
    return parse_time(tobs), data / exptime, hdr


class _AiaBase(Source):
    instrument = "SDO/AIA"
    asinh_a = 0.02

    def _sequence(self, ticket: Ticket, files: list[Path], pct) -> Sequence:
        frames, hdr0 = [], None
        for path in sorted(files):
            try:
                got = _read_aia(path)
            except Exception as err:  # noqa: BLE001 - a damaged file must not sink the night
                log.warning("unreadable %s: %s", path.name, err)
                continue
            if got is None:
                continue
            t, data, hdr = got
            if frames and data.shape != frames[0].data.shape:
                continue
            frames.append(Frame(t, data))
            hdr0 = hdr0 or hdr
        if not frames:
            raise Unavailable("no usable AIA frames")
        frames.sort(key=lambda f: f.time)
        wl = int(ticket.params["wavelength"])
        dsun = float(hdr0.get("DSUN_OBS") or 1.496e11)
        return Sequence(
            frames=frames, label=f"AIA {wl} Å", cmap=f"sdoaia{wl}",
            arcsec_per_px=abs(float(hdr0.get("CDELT1") or 0.6)),
            km_per_arcsec=dsun * ARCSEC_KM, observer="SDO", dsun_au=dsun / 1.495978707e11,
            center_hpc=tuple(ticket.params.get("center_hpc") or (0.0, 0.0)),
            stretch="asinh", pct=pct,
        )

    def tag(self, ticket: Ticket, seq: Sequence) -> str:
        ev = ticket.event or {}
        parts = [f"{ev['goes_class']} flare"] if ev.get("goes_class") else []
        if ev.get("ar"):
            parts.append(f"AR {ev['ar']}")
        return " · ".join(parts)

    def links(self, ticket: Ticket, seq: Sequence) -> dict:
        t = parse_time((ticket.event or {}).get("peak") or ticket.t_start)
        day = f"{t.day} {t.strftime('%B %Y')}"
        q = f'full:("{t.year} {t.strftime("%B")} {t.day}" OR "{day}" OR "SOL{t:%Y-%m-%d}") abs:(flare OR eruption OR filament OR jet)'
        return {
            "ads": "https://ui.adsabs.harvard.edu/search/q=" + quote(q) + "&sort=citation_count%20desc",
            "solarmonitor": f"https://solarmonitor.org/?date={t:%Y%m%d}",
        }


# ----------------------------------------------------------------------------- close-up

class AiaCloseup(_AiaBase):
    key = "aia_closeup"
    name = "SDO/AIA close-up"
    blurb = "Full-resolution tracked cut-out around a flare, 12-24 s cadence (JSOC export)."
    requires_env = ("JSOC_EMAIL",)

    def propose(self, rng, seen, override=None):
        cfg = self.cfg
        if override:
            events = [flare_near(parse_time(override))]
        else:
            events = draw_flares(rng, seen, cfg.get("min_class", "M1.0"), n=3)
        return [self._ticket(ev, rng, bool(override)) for ev in events]

    def _ticket(self, ev: dict, rng, requested: bool) -> Ticket:
        cfg = self.cfg
        start, peak, end = (parse_time(ev[k]) for k in ("start", "peak", "end"))
        t0 = start - timedelta(minutes=12)
        t1 = max(end, peak + timedelta(minutes=20)) + timedelta(minutes=12)
        max_min = float(cfg.get("max_minutes", 75))
        if (t1 - t0).total_seconds() > max_min * 60:
            t0 = peak - timedelta(minutes=max_min / 3)
            t1 = peak + timedelta(minutes=2 * max_min / 3)
        wl = int(weighted_choice(rng, cfg.get("channels", {171: 1})))
        base = 24 if wl in (1600, 1700) else 12
        nmax = int(cfg.get("max_frames", 300))
        cadence = base * math.ceil((t1 - t0).total_seconds() / (nmax * base))
        r = math.hypot(ev["hpc_x"], ev["hpc_y"])
        return Ticket(
            source=self.key, title=flare_title(ev), t_start=iso(t0), t_end=iso(t1), event=ev,
            params={"wavelength": wl, "cadence_s": cadence,
                    "fov_arcsec": float(cfg.get("fov_arcsec", 480)),
                    "center_hpc": [ev["hpc_x"], ev["hpc_y"]], "t_ref": ev["peak"],
                    "tracking": bool(r < 900)},
            reason=("requested event" if requested else
                    f"drawn from HEK flares >= {cfg.get('min_class', 'M1.0')} in a random 30-day window"),
        )

    def fetch(self, ticket, workdir, max_bytes):
        import astropy.units as u
        from astropy.coordinates import SkyCoord
        from astropy.time import Time
        from sunpy.net import Fido
        from sunpy.net import attrs as a

        p = ticket.params
        email = os.environ["JSOC_EMAIL"].strip()
        wl, fov = int(p["wavelength"]), float(p["fov_arcsec"])
        cx, cy = p["center_hpc"]
        tref = Time(p["t_ref"])
        corner = SkyCoord((cx - fov / 2) * u.arcsec, (cy - fov / 2) * u.arcsec, obstime=tref,
                          observer="earth", frame="helioprojective")
        cutout = a.jsoc.Cutout(corner, width=fov * u.arcsec, height=fov * u.arcsec,
                               tracking=bool(p.get("tracking", True)), register=True)
        series = "aia.lev1_uv_24s" if wl in (1600, 1700) else "aia.lev1_euv_12s"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                query = Fido.search(a.Time(ticket.t_start, ticket.t_end), a.Wavelength(wl * u.angstrom),
                                    a.Sample(int(p["cadence_s"]) * u.s), a.jsoc.Series(series),
                                    a.jsoc.Notify(email), a.jsoc.Segment("image"), cutout)
            except Exception as err:  # noqa: BLE001
                raise Unavailable(f"JSOC search failed: {type(err).__name__}: {err}") from err
            n = len(query[0]) if len(query) else 0
            log.info("JSOC: %d records for %s %d Å, exporting %.0f\" cut-outs", n, series, wl, fov)
            if n < 10:
                raise Unavailable(f"JSOC has only {n} records for this window")
            workdir.mkdir(parents=True, exist_ok=True)
            try:
                files = Fido.fetch(query, path=str(workdir / "{file}"), progress=False,
                                   overwrite=False, timeout=float(self.cfg.get("export_timeout_s", 2400)))
                for _ in range(2):
                    if not getattr(files, "errors", None):
                        break
                    log.warning("JSOC: retrying %d failed downloads", len(files.errors))
                    files = Fido.fetch(files, path=str(workdir / "{file}"), progress=False)
            except Exception as err:  # noqa: BLE001
                raise Unavailable(f"JSOC export failed: {type(err).__name__}: {err}") from err
        return sorted(workdir.glob("*.fits"))

    def load(self, ticket, files):
        return self._sequence(ticket, files, pct=(1.0, 99.95))


# ----------------------------------------------------------------------------- synoptic

class AiaSynoptic(_AiaBase):
    key = "aia_synoptic"
    name = "SDO/AIA full disk"
    blurb = "The whole disk for three hours around a large flare, 2-minute cadence, 1k images."
    URL = ("https://jsoc1.stanford.edu/data/aia/synoptic/{t:%Y}/{t:%m}/{t:%d}/H{t:%H}00/"
           "AIA{t:%Y%m%d}_{t:%H%M}_{wl:04d}.fits")

    def propose(self, rng, seen, override=None):
        cfg = self.cfg
        if override:
            events = [flare_near(parse_time(override))]
        else:
            events = draw_flares(rng, seen, cfg.get("min_class", "M5.0"), n=3, window_days=60)
        out = []
        for ev in events:
            peak = parse_time(ev["peak"])
            t0 = peak - timedelta(minutes=float(cfg.get("minutes_before", 40)))
            t1 = peak + timedelta(minutes=float(cfg.get("minutes_after", 140)))
            wl = int(weighted_choice(rng, cfg.get("channels", {193: 1})))
            out.append(Ticket(
                source=self.key, title=flare_title(ev) + ", full disk", t_start=iso(t0), t_end=iso(t1),
                event=ev, params={"wavelength": wl, "cadence_s": 120, "center_hpc": [0.0, 0.0]},
                reason=("requested event" if override else
                        f"drawn from HEK flares >= {cfg.get('min_class', 'M5.0')} in a random 60-day window"),
            ))
        return out

    def fetch(self, ticket, workdir, max_bytes):
        t0, t1 = parse_time(ticket.t_start), parse_time(ticket.t_end)
        t = t0.replace(second=0, microsecond=0) + timedelta(minutes=t0.minute % 2)
        wl = int(ticket.params["wavelength"])
        items = []
        while t <= t1:
            url = self.URL.format(t=t, wl=wl)
            items.append((url, workdir / url.rsplit("/", 1)[1]))
            t += timedelta(minutes=2)
        return download_all(items, max_bytes=max_bytes, workers=6)

    def load(self, ticket, files):
        return self._sequence(ticket, files, pct=(0.5, 99.95))

    def tag(self, ticket, seq):
        base = super().tag(ticket, seq)
        return f"{base} · full disk" if base else "full disk"
