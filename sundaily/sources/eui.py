"""Solar Orbiter EUI sources, served by the Solar Orbiter Archive (SOAR). No account needed.

eui_hri   a continuous high-cadence HRI_EUV 174 Å sequence, drawn from an index of such
          sequences built from the archive's own file list, de-jittered with solar_align.
"""

from __future__ import annotations

import math
import re
import warnings
from datetime import timedelta
from pathlib import Path

import numpy as np

from ..catalogs import build_eui_hri_index, soar_file_url, soar_files
from ..process import Stabilizer, bin_to
from ..sequence import Frame, Sequence
from ..ticket import Ticket
from ..util import Unavailable, download_all, iso, last_image_hdu, log, parse_time
from .base import Source

ARCSEC_KM = math.pi / 648000.0 / 1000.0
AU_M = 1.495978707e11


def soop_topic(soop: str | None) -> str | None:
    """Science target in a Solar Orbiter observing plan name.

    'R_BOTH_HRES_HCAD_Nanoflares' -> 'Nanoflares'; 'L_SMALL_HRES_HCAD_Slow-Wind-Connection'
    -> 'Slow Wind Connection'.
    """
    if not soop:
        return None
    parts = re.split(r"[;,]", soop)[0].strip().split("_")
    last_tag = max((i for i, part in enumerate(parts) if part.endswith(("RES", "CAD"))), default=-1)
    topic = " ".join(parts[last_tag + 1:]).replace("-", " ").strip()
    return topic or None


def _read_eui(path: Path):
    """(time, DN/s float32 image, header) from an EUI level-2 FITS file, or None."""
    from astropy.io import fits

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(path) as hdul:
            hdu = last_image_hdu(hdul)
            if hdu is None:
                return None
            hdr = hdu.header
            data = np.asarray(hdu.data, dtype=np.float32)
    tobs = hdr.get("DATE-AVG") or hdr.get("DATE-OBS") or hdr.get("DATE-BEG")
    if not tobs:
        return None
    if "/s" not in str(hdr.get("BUNIT", "DN/s")):
        xpo = float(hdr.get("XPOSURE") or 0.0)
        if xpo <= 0:
            return None
        data = data / xpo
    if np.isfinite(data).mean() < 0.5 or float(np.nanmax(data)) <= 0:
        return None
    return parse_time(tobs), data, hdr


def _centre_hpc(hdr) -> tuple[float, float] | None:
    """Helioprojective coordinates [arcsec] of the image centre, from the header WCS."""
    try:
        from astropy.wcs import WCS
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            w = WCS(hdr)
            lon, lat = w.pixel_to_world_values((hdr["NAXIS1"] - 1) / 2, (hdr["NAXIS2"] - 1) / 2)
        lon = float(lon) - 360.0 if float(lon) > 180 else float(lon)
        return round(lon * 3600, 1), round(float(lat) * 3600, 1)
    except Exception:  # noqa: BLE001
        return None


class EuiHri(Source):
    key = "eui_hri"
    name = "Solar Orbiter EUI/HRI"
    instrument = "Solar Orbiter EUI/HRI_EUV"
    blurb = "A continuous high-cadence HRI_EUV 174 Å sequence from a remote-sensing window."

    def _index(self) -> list[dict]:
        cache = self.state_dir / "index" / "eui_hri_sequences.json"
        seqs = build_eui_hri_index(cache, time_budget_s=float(self.cfg.get("index_budget_s", 900)))
        if not seqs:
            raise Unavailable("the EUI/HRI sequence index is empty")
        return seqs

    def propose(self, rng, seen, override=None):
        cfg = self.cfg
        seqs = self._index()
        if override:
            when = parse_time(override)
            chosen = [min(seqs, key=lambda s: 0 if parse_time(s["start"]) <= when <= parse_time(s["end"])
                          else min(abs((parse_time(s["start"]) - when).total_seconds()),
                                   abs((parse_time(s["end"]) - when).total_seconds())))]
        else:
            fresh = [s for s in seqs if f"eui_hri:{s['start']}" not in seen] or seqs
            span = lambda s: (parse_time(s["end"]) - parse_time(s["start"])).total_seconds()  # noqa: E731
            weights = [min(span(s), 3600.0) ** 0.5 for s in fresh]
            chosen = []
            pool, w = list(fresh), list(weights)
            while pool and len(chosen) < 3:
                i = rng.choices(range(len(pool)), w)[0]
                chosen.append(pool.pop(i))
                w.pop(i)
        nmax = int(cfg.get("max_frames", 280))
        max_span = float(cfg.get("max_minutes", 45)) * 60
        out = []
        for s in chosen:
            t0, t1 = parse_time(s["start"]), parse_time(s["end"])
            if (t1 - t0).total_seconds() > max_span:
                if override:
                    t0 = max(t0, min(parse_time(override) - timedelta(seconds=max_span / 3),
                                     t1 - timedelta(seconds=max_span)))
                else:
                    t0 += timedelta(seconds=rng.uniform(0, (t1 - t0).total_seconds() - max_span))
                t1 = t0 + timedelta(seconds=max_span)
            n_window = (t1 - t0).total_seconds() / max(s["cadence_s"], 0.5)
            stride = max(1, math.ceil(n_window / nmax))
            out.append(Ticket(
                source=self.key,
                title=f"EUI/HRI 174 Å sequence, {s['cadence_s'] * stride:.0f} s cadence",
                t_start=iso(t0), t_end=iso(t1),
                event={"type": "OBS", "catalog": "Solar Orbiter Archive (SOAR)",
                       "id": f"eui_hri:{s['start']}", "start": s["start"], "end": s["end"],
                       "peak": iso(t0 + (t1 - t0) / 2), "frames_in_archive": s["n"],
                       "native_cadence_s": s["cadence_s"]},
                params={"descriptor": s["descriptor"], "stride": stride},
                reason=("requested time" if override else
                        f"drawn from {len(seqs)} continuous HRI_EUV sequences in the archive"),
            ))
        return out

    def fetch(self, ticket, workdir, max_bytes):
        t0, t1 = parse_time(ticket.t_start), parse_time(ticket.t_end)
        rows = soar_files([ticket.params["descriptor"]], t0, t1 + timedelta(seconds=1))
        rows = rows[::int(ticket.params.get("stride", 1))]
        if len(rows) < 10:
            raise Unavailable(f"SOAR lists only {len(rows)} files for this window")
        soop = next((r.get("soop_name") for r in rows if r.get("soop_name")), None)
        if soop:
            ticket.event["soop"] = soop
        total = sum(float(r.get("filesize") or 0) for r in rows)
        log.info("SOAR: %d files, %.0f MB listed", len(rows), total / 1e6)
        items = [(soar_file_url(r["data_item_id"]), workdir / r["filename"]) for r in rows]
        return download_all(items, max_bytes=max_bytes, workers=6, timeout=300)

    def load(self, ticket, files):
        work_side = int(self.cfg.get("work_side", 1200))
        stab = Stabilizer(crop=1024) if self.cfg.get("stabilise", True) else None
        frames, hdr0, shape0, k = [], None, None, 1
        for path in sorted(files):
            try:
                got = _read_eui(path)
            except Exception as err:  # noqa: BLE001
                log.warning("unreadable %s: %s", path.name, err)
                continue
            if got is None:
                continue
            t, data, hdr = got
            if shape0 is None:
                shape0, hdr0 = data.shape, hdr
            elif data.shape != shape0:
                continue
            if stab is not None:
                data = stab.step(data)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                small, k = bin_to(data, work_side)
            frames.append(Frame(t, small))
        if not frames:
            raise Unavailable("no usable EUI frames")
        notes = {}
        if stab is not None:
            ys, xs = stab.valid_box(shape0, k)
            for f in frames:
                f.data = f.data[ys, xs]
            notes["stabilisation"] = stab.summary()
            log.info("de-jitter: %s", notes["stabilisation"])
        mid = hdr0
        dsun = float(mid.get("DSUN_OBS") or AU_M)
        topic = soop_topic((ticket.event or {}).get("soop"))
        ticket.title = (f"{topic} campaign, seen from {dsun / AU_M:.2f} au" if topic else
                        f"The corona from {dsun / AU_M:.2f} au")
        return Sequence(
            frames=frames, label="HRI_{EUV} 174 Å", cmap="solar orbiterhri_euv174",
            arcsec_per_px=abs(float(mid.get("CDELT1") or 0.492)) * k,
            km_per_arcsec=dsun * ARCSEC_KM, observer="Solar Orbiter", dsun_au=dsun / AU_M,
            center_hpc=_centre_hpc(mid), stretch="asinh", pct=(1.0, 99.9), asinh_a=0.05,
            roll_deg=round(float(mid.get("CROTA") or 0.0), 2), notes=notes,
        )

    def tag(self, ticket, seq):
        return f"Solar Orbiter at {seq.dsun_au:.2f} au"

    def links(self, ticket, seq):
        return {"soar": "https://soar.esac.esa.int/soar/"}
