"""Command line: `python -m sundaily <command>`.

run      pick, download, render: writes clip.mp4, poster.jpg, card.json, ticket.json, status.json
publish  copy a run's output into the gallery tree and re-index it
fetch    download the science files behind a published clip (to work on them yourself)
probe    check that the catalogues and archives answer as the code expects
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .util import Unavailable, http_json, iso, local_date, log, scrub, setup_logging, utcnow

GALLERY_URL = "https://songsolarphysics.github.io/sun-daily"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------- run

def cmd_run(args) -> int:
    from .card import build_card, quality
    from .pick import draw_order, load_history, load_yaml, pending_request, seen_events
    from .render import render_clip
    from .sources import make

    root = Path(args.root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    setup_logging(logfile=out / "run.log")
    settings = load_yaml(root / "config" / "settings.yaml", {})
    pool = load_yaml(root / "config" / "pool.yaml", {})
    requests_cfg = load_yaml(root / "config" / "requests.yaml", {})

    date = args.date or local_date(settings.get("timezone", "Europe/Berlin"))
    gallery = Path(args.gallery) if args.gallery else None
    state_dir = (gallery / "data") if gallery else (out / "state")
    history = load_history(gallery)
    seen = seen_events(history)
    seed = int(args.seed) if args.seed else int(date.replace("-", ""))
    rng = random.Random(seed)
    budget = settings.get("budget") or {}
    render_cfg = settings.get("render") or {}

    request = None
    override = args.event or None
    if args.source:
        order = [args.source]
    else:
        request = pending_request(requests_cfg, history)
        order = draw_order(pool, history, rng)
        if request:
            override = request.get("event") or override
            if request.get("source"):
                order = [request["source"]] + [k for k in order if k != request["source"]]
            log.info("request %s: %s", request.get("id"), {k: v for k, v in request.items() if k != "id"})
    log.info("sun-daily %s  date %s  seed %d  order %s", __version__, date, seed, order)

    attempts, status = [], {"ok": False, "date": date, "seed": seed, "started": iso(utcnow())}
    max_bytes = float(budget.get("max_download_gb", 2.5)) * 1e9
    for n_src, key in enumerate(order):
        try:
            src = make(key, settings, state_dir)
        except KeyError as err:
            attempts.append({"source": key, "result": "unknown source", "why": str(err)})
            continue
        ok, why = src.available()
        if not ok:
            log.info("%s skipped: %s", key, why)
            attempts.append({"source": key, "result": "skipped", "why": why})
            continue
        # a requested event only applies to the source it was requested for (the first one)
        this_override = override if (n_src == 0 or args.source) else None
        try:
            tickets = src.propose(rng, seen, this_override)
        except Unavailable as err:
            log.warning("%s: nothing to propose: %s", key, err)
            attempts.append({"source": key, "result": "no ticket", "why": str(err)})
            continue
        except Exception as err:  # noqa: BLE001
            log.exception("%s: propose crashed", key)
            attempts.append({"source": key, "result": "error", "why": f"{type(err).__name__}: {err}"})
            continue

        for n_try, ticket in enumerate(tickets[: int(budget.get("candidates_per_source", 2))]):
            ticket.date, ticket.seed = date, seed
            work = out / "work" / f"{key}-{n_try}"
            log.info("ticket: %s | %s -> %s | %s", ticket.title, ticket.t_start, ticket.t_end, ticket.params)
            t_begin = time.time()
            try:
                files = src.fetch(ticket, work, max_bytes)
                nbytes = sum(f.stat().st_size for f in files)
                t_fetch = time.time()
                seq = src.load(ticket, files)
                qc = quality(seq, int(budget.get("min_frames", 24)))
                info = render_clip(seq, out, tag=src.tag(ticket, seq),
                                   peak_time=(ticket.event or {}).get("peak"),
                                   size=int(render_cfg.get("size", 1080)),
                                   target_s=float(render_cfg.get("target_seconds", 14)),
                                   crf=int(render_cfg.get("crf", 21)),
                                   max_mbps=float(render_cfg.get("max_mbps", 6)),
                                   asinh_a=src.asinh_a)
                card = build_card(ticket, seq, info, src, qc,
                                  request_id=(request or {}).get("id") if n_src == 0 else None)
                card["download"] = {"files": len(files), "bytes": nbytes}
                card["timing_s"] = {"fetch": round(t_fetch - t_begin), "process": round(time.time() - t_fetch)}
                ticket.save(out / "ticket.json")
                (out / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n",
                                               encoding="utf-8")
                attempts.append({"source": key, "result": "ok", "title": ticket.title})
                status.update(ok=True, source=key, title=ticket.title, finished=iso(utcnow()),
                              attempts=attempts)
                (out / "status.json").write_text(json.dumps(status, indent=1) + "\n")
                log.info("done: %s", card["caption"])
                return 0
            except Unavailable as err:
                log.warning("ticket failed: %s", err)
                attempts.append({"source": key, "result": "failed", "title": ticket.title, "why": str(err)})
            except Exception as err:  # noqa: BLE001
                log.exception("ticket crashed")
                attempts.append({"source": key, "result": "error", "title": ticket.title,
                                 "why": f"{type(err).__name__}: {err}"})
            finally:
                if not args.keep_data:
                    shutil.rmtree(work, ignore_errors=True)

    status.update(finished=iso(utcnow()), attempts=attempts)
    (out / "status.json").write_text(json.dumps(status, indent=1) + "\n")
    log.error("no clip tonight: every candidate failed")
    return 2


# ----------------------------------------------------------------------------- publish

def cmd_publish(args) -> int:
    from .pick import load_yaml
    from .site import publish

    setup_logging()
    root = Path(args.root)
    settings = load_yaml(root / "config" / "settings.yaml", {})
    keep = int((settings.get("gallery") or {}).get("keep_clips", 0))
    publish(Path(args.out), Path(args.gallery), root / "site", keep_clips=keep)
    return 0


# ----------------------------------------------------------------------------- fetch

def cmd_fetch(args) -> int:
    """Download the files behind a clip: `sundaily fetch 2026-11-22`."""
    from .pick import load_yaml
    from .sources import make
    from .ticket import Ticket

    setup_logging()
    what = args.what
    if Path(what).exists():
        ticket = Ticket.load(Path(what))
    else:
        ticket = Ticket(**http_json(f"{args.gallery_url.rstrip('/')}/tickets/{what}.json"))
    settings = load_yaml(_repo_root() / "config" / "settings.yaml", {})
    dest = Path(args.dir) / (ticket.date or "ticket")
    src = make(ticket.source, settings, dest / ".state")
    ok, why = src.available()
    if not ok:
        log.error("%s %s", ticket.source, why)
        return 1
    files = src.fetch(ticket, dest, float(args.max_gb) * 1e9)
    ticket.save(dest / "ticket.json")
    log.info("%d files in %s", len(files), dest)
    return 0


# ----------------------------------------------------------------------------- probe

def cmd_probe(args) -> int:
    """Ask each catalogue and archive a small question and report what came back."""
    from collections import Counter
    from datetime import datetime, timedelta

    import requests

    from .catalogs import HRI_EUV_DESCRIPTORS, find_sequences, hek_flares, hek_search, soar_files, soar_query
    from .util import UTC

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    setup_logging(logfile=out / "probe.log")
    report: dict = {"when": iso(utcnow())}

    def step(name, fn):
        t0 = time.time()
        try:
            report[name] = fn()
            log.info("probe %-18s ok   %.1fs  %s", name, time.time() - t0, str(report[name])[:300])
        except Exception as err:  # noqa: BLE001
            report[name] = {"error": f"{type(err).__name__}: {err}"}
            log.warning("probe %-18s FAIL %.1fs  %s", name, time.time() - t0, report[name]["error"][:300])

    t0, t1 = datetime(2014, 2, 20, tzinfo=UTC), datetime(2014, 3, 1, tzinfo=UTC)

    def hek_frms():
        recs = hek_search("fl", t0, t1, [("fl_goescls", ">=", "M1.0")])
        keys = sorted(recs[0].keys()) if recs else []
        return {"records": len(recs), "frm_names": dict(Counter(r.get("frm_name") for r in recs)),
                "has_keys": [k for k in ("event_starttime", "event_peaktime", "fl_goescls", "ar_noaanum",
                                         "hpc_x", "hpc_y", "SOL_standard", "kb_archivid") if k in keys]}

    def hek_ssw():
        evs = hek_flares(t0, t1, "M1.0")
        return {"flares": len(evs), "first": evs[:3]}

    def hek_other():
        res = {}
        a, b = datetime(2013, 1, 1, tzinfo=UTC), datetime(2013, 3, 1, tzinfo=UTC)
        for et in ("fe", "cj", "er", "ce", "cw"):
            recs = hek_search(et, a, b, page_size=200, max_pages=1)
            res[et] = {"records": len(recs), "frm_names": dict(Counter(r.get("frm_name") for r in recs).most_common(5))}
        return res

    def soar_day():
        a, b = datetime(2022, 3, 30, tzinfo=UTC), datetime(2022, 3, 31, tzinfo=UTC)
        rows = soar_files(HRI_EUV_DESCRIPTORS, a, b)
        seqs = find_sequences([r["time"] for r in rows])
        return {"files": len(rows), "sample": {k: str(v) for k, v in rows[0].items()} if rows else None,
                "sequences": seqs[:6]}

    def soar_month():
        a, b = datetime(2022, 3, 1, tzinfo=UTC), datetime(2022, 4, 1, tzinfo=UTC)
        desc = " OR ".join(f"descriptor='{d}'" for d in HRI_EUV_DESCRIPTORS)
        rows = soar_query("SELECT begin_time FROM v_sc_data_item WHERE level='L2' AND "
                          f"({desc}) AND begin_time>='{a:%Y-%m-%d %H:%M:%S}' AND begin_time<'{b:%Y-%m-%d %H:%M:%S}'")
        return {"rows_in_one_query": len(rows)}

    def soar_descriptors():
        rows = soar_query("SELECT DISTINCT descriptor FROM v_sc_data_item WHERE instrument='EUI' AND level='L2'")
        return sorted(r["descriptor"] for r in rows)

    def aia_synoptic():
        url = ("https://jsoc1.stanford.edu/data/aia/synoptic/2014/02/25/H0000/AIA20140225_0040_0193.fits")
        r = requests.head(url, timeout=60, allow_redirects=True)
        return {"status": r.status_code, "bytes": r.headers.get("content-length")}

    def jsoc():
        email = os.environ.get("JSOC_EMAIL", "").strip()
        if not email:
            return {"email": False}
        import drms
        c = drms.Client(email=email)
        keys = c.query("aia.lev1_euv_12s[2014.02.25_00:40:00_TAI/1m][171]", key="T_OBS,EXPTIME,QUALITY")
        return {"email": True, "registered": bool(c.check_email(email)), "records": int(len(keys))}

    steps = {"hek_frms": hek_frms, "hek_ssw": hek_ssw, "hek_other": hek_other, "soar_day": soar_day,
             "soar_month": soar_month, "soar_descriptors": soar_descriptors,
             "aia_synoptic": aia_synoptic, "jsoc": jsoc}
    for name in (args.only.split(",") if args.only else steps):
        step(name, steps[name])
    (out / "probe.json").write_text(scrub(json.dumps(report, indent=1, default=str)) + "\n")
    return 0


# ----------------------------------------------------------------------------- entry

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sundaily", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="pick, download and render tonight's clip")
    r.add_argument("--root", default=str(_repo_root()), help="repository root (holds config/ and site/)")
    r.add_argument("--out", default="out")
    r.add_argument("--gallery", default="", help="checked-out gallery tree (history and caches)")
    r.add_argument("--date", default="", help="delivery date YYYY-MM-DD (default: today, local time)")
    r.add_argument("--source", default="", help="force a source key")
    r.add_argument("--event", default="", help="force an event/observation time, e.g. 2014-02-25T00:49")
    r.add_argument("--seed", default="")
    r.add_argument("--keep-data", action="store_true", help="keep the downloaded science files")
    r.set_defaults(fn=cmd_run)

    b = sub.add_parser("publish", help="copy a run's output into the gallery tree")
    b.add_argument("--root", default=str(_repo_root()))
    b.add_argument("--out", default="out")
    b.add_argument("--gallery", required=True)
    b.set_defaults(fn=cmd_publish)

    f = sub.add_parser("fetch", help="download the science files behind a published clip")
    f.add_argument("what", help="a date (YYYY-MM-DD) from the gallery, or a ticket.json path")
    f.add_argument("--dir", default="sun-daily-data")
    f.add_argument("--max-gb", default="10")
    f.add_argument("--gallery-url", default=GALLERY_URL)
    f.set_defaults(fn=cmd_fetch)

    q = sub.add_parser("probe", help="check catalogues and archives")
    q.add_argument("--out", default="out")
    q.add_argument("--only", default="", help="comma-separated probe names")
    q.set_defaults(fn=cmd_probe)

    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
