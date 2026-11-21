"""Offline end-to-end test: synthetic FITS files through load -> check -> render -> card -> publish.

No network. Run with `python tests/test_offline.py` or pytest.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits
from scipy.ndimage import gaussian_filter
from scipy.ndimage import shift as ndshift

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sundaily.card import build_card, quality  # noqa: E402
from sundaily.render import render_clip  # noqa: E402
from sundaily.site import publish  # noqa: E402
from sundaily.sources.aia import AiaCloseup  # noqa: E402
from sundaily.sources.eui import EuiHri  # noqa: E402
from sundaily.ticket import Ticket  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _scene(rng, n):
    base = np.clip(gaussian_filter(rng.random((n, n)), 5) - 0.5, 0, None) ** 1.5 * 5000
    knots = gaussian_filter((rng.random((n, n)) > 0.9993).astype(float), 9) * 4e5
    return (base + knots + 15).astype(np.float32)


def make_aia(folder: Path, n=40, size=400):
    rng = np.random.default_rng(1)
    scene = _scene(rng, size)
    t0 = datetime(2014, 2, 25, 0, 30, tzinfo=UTC)
    for i in range(n):
        flare = 1 + 300 * np.exp(-((i - n * 0.4) / 4.0) ** 2)
        exptime = 0.2 if (flare > 20 and i % 2) else 2.0     # automatic exposure control
        img = scene * exptime + rng.normal(0, 3, scene.shape)
        img[150:250, 150:250] *= flare                       # the flare kernel
        if img.max() > 16383:                                # saturation and column bleeding
            cols = np.where((img > 16383).any(axis=0))[0]
            img[:, cols] = np.maximum(img[:, cols], 16383.0)
            img = np.minimum(img, 16383.0)
        hdr = fits.Header()
        t = t0 + timedelta(seconds=24 * i)
        hdr["T_OBS"] = t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z"
        hdr["EXPTIME"], hdr["WAVELNTH"], hdr["CDELT1"] = exptime, 171, 0.6
        hdr["DSUN_OBS"] = 1.48e11
        hdul = fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU(img.astype(np.float32), hdr)])
        hdul.writeto(folder / f"aia.lev1_euv_12s.{t:%Y-%m-%dT%H%M%S}Z.171.image.fits", overwrite=True)


def make_eui(folder: Path, n=36, size=640):
    rng = np.random.default_rng(2)
    scene = _scene(rng, size)
    walk = np.cumsum(rng.normal(0, 1.0, (n, 2)), axis=0)
    t0 = datetime(2022, 3, 30, 17, 20, 4, tzinfo=UTC)
    for i in range(n):
        img = ndshift(scene * (1 + 0.1 * np.sin(i / 6)), (walk[i, 1], walk[i, 0]), order=1, mode="nearest")
        hdr = fits.Header()
        t = t0 + timedelta(seconds=5 * i)
        hdr["DATE-AVG"] = t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        hdr["BUNIT"], hdr["XPOSURE"] = "DN/s", 1.65
        hdr["CDELT1"], hdr["CDELT2"] = 0.492, 0.492
        hdr["CTYPE1"], hdr["CTYPE2"] = "HPLN-TAN", "HPLT-TAN"
        hdr["CUNIT1"], hdr["CUNIT2"] = "arcsec", "arcsec"
        hdr["CRPIX1"], hdr["CRPIX2"] = size / 2, size / 2
        hdr["CRVAL1"], hdr["CRVAL2"] = 250.0, -310.0
        hdr["DSUN_OBS"], hdr["CROTA"] = 0.323 * 1.495978707e11, 0.4
        hdul = fits.HDUList([fits.PrimaryHDU(), fits.CompImageHDU((img + rng.normal(0, 2, img.shape)).astype(np.float32), hdr)])
        hdul.writeto(folder / f"solo_L2_eui-hrieuv174-image_{t:%Y%m%dT%H%M%S}000_V01.fits", overwrite=True)
    return walk


def _finish(src, ticket, files, out: Path, gallery: Path):
    seq = src.load(ticket, files)
    qc = quality(seq, 24)
    info = render_clip(seq, out, tag=src.tag(ticket, seq), peak_time=ticket.event.get("peak"), size=720)
    card = build_card(ticket, seq, info, src, qc)
    ticket.save(out / "ticket.json")
    (out / "card.json").write_text(json.dumps(card, indent=1))
    publish(out, gallery, ROOT / "site")
    return seq, card


def test_pipeline(tmp: Path | None = None):
    tmp = Path(tmp or tempfile.mkdtemp())
    gallery = tmp / "gallery"

    d = tmp / "aia"
    d.mkdir(parents=True, exist_ok=True)
    make_aia(d)
    ev = {"type": "FL", "id": "SOL2014-02-25T00:39:00L000C000", "goes_class": "X4.9", "ar": 11990,
          "start": "2014-02-25T00:39:00", "peak": "2014-02-25T00:49:00", "end": "2014-02-25T01:03:00",
          "hpc_x": -915.0, "hpc_y": -220.0}
    t = Ticket("aia_closeup", "X4.9 flare in AR 11990", "2014-02-25T00:30:00", "2014-02-25T00:46:00", ev,
               {"wavelength": 171, "cadence_s": 24, "fov_arcsec": 240, "center_hpc": [-915, -220]},
               "test", "2026-11-21")
    seq, card = _finish(AiaCloseup({}, tmp / "state"), t, sorted(d.glob("*.fits")), tmp / "out1", gallery)
    exp = card["notes"]["exposures"]
    assert exp["short_exposures"] > 0 and exp["long_frames_composited"] > 0, exp
    assert len(seq) == 40 - exp["short_exposures"], (len(seq), exp)
    # the flare kernel must come from the short exposures: far above the long-exposure ceiling
    assert max(float(f.data.max()) for f in seq.frames) > 16383 / 2.0 * 2, "kernel still clipped"
    assert card["clip"]["width"] == 720 and (gallery / "clips" / "2026-11-21.mp4").stat().st_size > 20000
    # the flare must not set the scaling: the quiet scene has to stay well above black
    assert card["clip"]["scaling"]["vmax"] < 60000, card["clip"]["scaling"]

    d = tmp / "eui"
    d.mkdir(parents=True, exist_ok=True)
    walk = make_eui(d)
    ev = {"type": "OBS", "id": "eui_hri:2022-03-30T17:20:04", "peak": "2022-03-30T17:21:30",
          "start": "2022-03-30T17:20:04", "end": "2022-03-30T17:23:00"}
    t = Ticket("eui_hri", "EUI/HRI 174 Å sequence, 5 s cadence", "2022-03-30T17:20:04", "2022-03-30T17:23:00",
               ev, {"descriptor": "eui-hrieuv174-image", "stride": 1}, "test", "2026-11-22")
    seq, card = _finish(EuiHri({"work_side": 640}, tmp / "state"), t, sorted(d.glob("*.fits")), tmp / "out2", gallery)
    stab = card["notes"]["stabilisation"]
    drift = walk[-1] - walk[0]
    assert abs(stab["total_drift_px"][0] - drift[0]) < 0.6 and abs(stab["total_drift_px"][1] - drift[1]) < 0.6, (stab, drift)
    assert card["qc"]["nan_fraction_max"] == 0.0
    assert abs(card["obs"]["center_hpc"][0] - 250) < 2 and abs(card["obs"]["dsun_au"] - 0.323) < 1e-3

    cards = json.loads((gallery / "data" / "cards.json").read_text())
    assert [c["date"] for c in cards] == ["2026-11-22", "2026-11-21"]
    print("ok:", tmp)
    for c in cards:
        print(" ", c["date"], "|", c["caption"])
    return tmp


if __name__ == "__main__":
    test_pipeline(sys.argv[1] if len(sys.argv) > 1 else None)
