"""Event and observation catalogues the nightly pick draws from.

* HEK (Heliophysics Event Knowledgebase): flares and other solar events.
* SOAR (Solar Orbiter Archive) TAP: which Solar Orbiter files exist, used to build a list of
  continuous EUI/HRI high-cadence sequences.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .util import Unavailable, daterange_months, http_json, iso, log, parse_time, utcnow

HEK_URL = "https://www.lmsal.com/hek/her"
SOAR_TAP = "https://soar.esac.esa.int/soar-sl-tap/tap/sync"
SOAR_DATA = "https://soar.esac.esa.int/soar-sl-tap/data"

GOES_BASE = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}


# ----------------------------------------------------------------------------- GOES class

def goes_flux(cls: str) -> float:
    """'M5.2' -> 5.2e-5 W m^-2. Returns 0 for anything unparsable."""
    cls = (cls or "").strip().upper()
    if not cls or cls[0] not in GOES_BASE:
        return 0.0
    try:
        return GOES_BASE[cls[0]] * float(cls[1:] or 1.0)
    except ValueError:
        return 0.0


# ----------------------------------------------------------------------------- HEK

def hek_search(event_type: str, t0: datetime, t1: datetime, filters=(), page_size: int = 200,
               max_pages: int = 8) -> list[dict]:
    """Raw HEK records of one event type whose time range overlaps [t0, t1].

    ``filters`` is a sequence of (parameter, operator, value), e.g.
    ("frm_name", "=", "SSW Latest Events").
    """
    params = {
        "cosec": "2", "cmd": "search", "type": "column",
        "event_type": event_type.lower(),
        "event_starttime": iso(t0), "event_endtime": iso(t1),
        "event_coordsys": "helioprojective",
        "x1": "-5000", "x2": "5000", "y1": "-5000", "y2": "5000",
        "result_limit": str(page_size),
    }
    for i, (name, op, value) in enumerate(filters):
        params[f"param{i}"] = name
        params[f"op{i}"] = op
        params[f"value{i}"] = value
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        params["page"] = str(page)
        js = http_json(HEK_URL, params=params, timeout=120)
        out.extend(js.get("result", []))
        if not js.get("overmax"):
            break
    return out


def _num(value, default=None):
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def normalise_hek(rec: dict) -> dict | None:
    """Reduce a HEK record to the fields the pipeline uses. None if it lacks time or position."""
    try:
        start = parse_time(rec["event_starttime"])
        end = parse_time(rec["event_endtime"])
    except (KeyError, ValueError):
        return None
    peak = rec.get("event_peaktime")
    try:
        peak = parse_time(peak) if peak else start + (end - start) / 2
    except ValueError:
        peak = start + (end - start) / 2
    x, y = _num(rec.get("hpc_x")), _num(rec.get("hpc_y"))
    if x is None or y is None:
        return None
    ar = rec.get("ar_noaanum")
    try:
        ar = int(ar) if ar not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        ar = None
    if ar is not None and ar < 10000 and start.year >= 2003:
        ar += 10000                  # catalogues often drop the leading 1 of NOAA numbers
    etype = (rec.get("event_type") or "").upper()
    return {
        "type": etype,
        "catalog": f"HEK / {rec.get('frm_name', '').strip()}",
        "id": (rec.get("SOL_standard") or rec.get("sol_standard") or rec.get("kb_archivid") or
               f"{etype}:{iso(start)}"),
        "start": iso(start), "peak": iso(peak), "end": iso(end),
        "goes_class": (rec.get("fl_goescls") or "").strip() or None,
        "ar": ar,
        "hpc_x": round(x, 1), "hpc_y": round(y, 1),
        "hgs_lon": _num(rec.get("hgs_x")), "hgs_lat": _num(rec.get("hgs_y")),
        "description": (rec.get("event_description") or "").strip()[:300] or None,
    }


def hek_flares(t0: datetime, t1: datetime, min_class: str = "M1.0",
               frm: str | None = "SSW Latest Events") -> list[dict]:
    """Flares at or above ``min_class`` with a usable position, strongest first.

    ``frm`` restricts the search to one feature-recognition method; "SSW Latest Events"
    carries flare positions. None searches every catalogue.
    """
    filters = [("fl_goescls", ">=", min_class)]
    if frm:
        filters.append(("frm_name", "=", frm))
    recs = hek_search("fl", t0, t1, filters)
    out, seen = [], set()
    fmin = goes_flux(min_class)
    for rec in recs:
        ev = normalise_hek(rec)
        if not ev or ev["id"] in seen:
            continue
        if goes_flux(ev["goes_class"]) < fmin:
            continue
        r = math.hypot(ev["hpc_x"], ev["hpc_y"])
        if r < 1.0 or r > 1150:      # (0, 0) means "position unknown" in this catalogue
            continue
        seen.add(ev["id"])
        out.append(ev)
    out.sort(key=lambda e: -goes_flux(e["goes_class"]))
    return out


# ----------------------------------------------------------------------------- SOAR

def soar_query(adql: str, timeout: float = 240) -> list[dict]:
    """Run an ADQL query against the SOAR TAP service and return rows as dicts."""
    # The service wants the query un-form-encoded (same approach as sunpy's SOAR client).
    payload = "&".join(f"{k}={v}" for k, v in
                       {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql}.items())
    js = http_json(SOAR_TAP, params=payload, timeout=timeout)
    try:
        names = [m["name"] for m in js["metadata"]]
        return [dict(zip(names, row)) for row in js["data"]]
    except (KeyError, TypeError) as err:
        raise Unavailable(f"unexpected SOAR response: {str(js)[:200]}") from err


def soar_file_url(data_item_id: str) -> str:
    return (f"{SOAR_DATA}?retrieval_type=LAST_PRODUCT&product_type=SCIENCE"
            f"&data_item_id={data_item_id}")


HRI_EUV_DESCRIPTORS = ("eui-hrieuv174-image", "eui-hrieuvopn-image")
_ROW_CAPS = {2000, 5000, 10000, 20000, 50000, 100000}


def soar_files(descriptors, t0: datetime, t1: datetime, level: str = "L2", _depth: int = 0) -> list[dict]:
    """Files of the given descriptors with begin_time in [t0, t1), oldest first."""
    desc = " OR ".join(f"descriptor='{d}'" for d in descriptors)
    adql = ("SELECT begin_time, data_item_id, filename, filesize, descriptor, soop_name "
            f"FROM v_sc_data_item WHERE level='{level}' AND ({desc}) "
            f"AND begin_time>='{t0:%Y-%m-%d %H:%M:%S}' AND begin_time<'{t1:%Y-%m-%d %H:%M:%S}'")
    rows = soar_query(adql)
    if len(rows) in _ROW_CAPS and _depth < 6 and (t1 - t0) > timedelta(hours=1):
        mid = t0 + (t1 - t0) / 2          # the service probably truncated: split the interval
        return soar_files(descriptors, t0, mid, level, _depth + 1) + \
            soar_files(descriptors, mid, t1, level, _depth + 1)
    seen, out = set(), []
    for row in rows:
        if row["data_item_id"] in seen or not row.get("begin_time"):
            continue
        seen.add(row["data_item_id"])
        row["time"] = parse_time(row["begin_time"])
        out.append(row)
    out.sort(key=lambda r: r["time"])
    return out


def find_sequences(times: list[datetime], max_gap_s: float = 60, min_frames: int = 60,
                   max_cadence_s: float = 30, min_span_s: float = 480) -> list[dict]:
    """Split a sorted list of exposure times into continuous high-cadence runs."""
    if len(times) < min_frames:
        return []
    t = np.array([x.timestamp() for x in times])
    cuts = np.where(np.diff(t) > max_gap_s)[0] + 1
    out = []
    for seg in np.split(np.arange(len(t)), cuts):
        if len(seg) < min_frames:
            continue
        ts = t[seg]
        cadence = float(np.median(np.diff(ts)))
        span = float(ts[-1] - ts[0])
        if cadence <= max_cadence_s and span >= min_span_s:
            out.append({"start": iso(times[seg[0]]), "end": iso(times[seg[-1]]),
                        "n": int(len(seg)), "cadence_s": round(cadence, 2)})
    return out


def build_eui_hri_index(cache: Path, first: datetime | None = None, max_age_days: float = 45,
                        time_budget_s: float = 900) -> list[dict]:
    """List of continuous EUI/HRI_EUV sequences in the archive, cached as JSON.

    The scan goes month by month and resumes where it stopped, so a slow archive only delays the
    index, it never blocks a run. Recent months are rescanned because data are released late.
    """
    import time as _time

    cache = Path(cache)
    state = {"scanned_to": None, "built": None, "sequences": []}
    if cache.exists():
        try:
            state = json.loads(cache.read_text())
        except ValueError:
            pass
    now = utcnow()
    first = first or datetime(2020, 5, 1, tzinfo=now.tzinfo)
    built = parse_time(state["built"]) if state.get("built") else None
    fresh = built is not None and (now - built).days < max_age_days
    scanned_to = parse_time(state["scanned_to"]) if state.get("scanned_to") else first
    complete = scanned_to >= now - timedelta(days=31)
    if fresh and complete:
        return state["sequences"]
    if complete and not fresh:
        # periodic refresh: data are released months late, so rescan the last 15 months
        y, m = now.year, now.month - 15
        while m <= 0:
            y, m = y - 1, m + 12
        scanned_to = datetime(y, m, 1, tzinfo=now.tzinfo)

    t_begin = _time.time()
    for m0, m1 in daterange_months(scanned_to, now):
        if _time.time() - t_begin > time_budget_s:
            log.info("EUI index: time budget used, will resume from %s next run", iso(m0))
            break
        try:
            rows = soar_files(HRI_EUV_DESCRIPTORS, m0, min(m1, now))
        except Unavailable as err:
            log.warning("EUI index: %s failed (%s); stopping scan here", m0.strftime("%Y-%m"), err)
            break
        by_desc: dict[str, list] = {}
        for row in rows:
            by_desc.setdefault(row["descriptor"], []).append(row["time"])
        found = []
        for desc, times in by_desc.items():
            for seq in find_sequences(times):
                seq["descriptor"] = desc
                found.append(seq)
        # replace whatever was known for this month (a month may be scanned more than once)
        state["sequences"] = [s for s in state["sequences"]
                              if not (m0 <= parse_time(s["start"]) < m1)] + found
        state["scanned_to"] = iso(min(m1, now))
        log.info("EUI index: %s  %5d files  %2d sequences", m0.strftime("%Y-%m"), len(rows), len(found))
    else:
        state["built"] = iso(now)
    state["sequences"].sort(key=lambda s: s["start"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(state, indent=1) + "\n")
    return state["sequences"]
