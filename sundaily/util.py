"""Small shared helpers: logging, time handling, HTTP with retries, bulk download."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("sundaily")

USER_AGENT = "sun-daily/0.1 (+https://github.com/songsolarphysics/sun-daily)"
UTC = timezone.utc


class Unavailable(RuntimeError):
    """A source or ticket cannot be served right now; the caller moves to a fallback."""


# ----------------------------------------------------------------------------- logging

def setup_logging(level: str = "INFO", logfile: str | os.PathLike | None = None) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if logfile:
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logfile, mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    # third-party chatter
    for noisy in ("sunpy", "parfive", "drms", "urllib3", "zeep", "matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def scrub(text: str) -> str:
    """Remove private values (the JSOC e-mail) from text that will be published."""
    email = os.environ.get("JSOC_EMAIL", "").strip()
    if email:
        text = text.replace(email, "<jsoc-email>")
        text = text.replace(email.replace("@", "%40"), "<jsoc-email>")
    return text


# ----------------------------------------------------------------------------- time

def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_time(value) -> datetime:
    """Parse an ISO-like time string (or datetime) into an aware UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    s = str(value).strip().replace("Z", "")
    s = s.replace("/", "-")
    s = re.sub(r"\s+", "T", s, count=1)
    if "." in s:  # trim sub-second digits to microseconds
        head, frac = s.split(".", 1)
        frac = re.sub(r"\D.*$", "", frac)[:6]
        s = f"{head}.{frac}" if frac else head
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"cannot parse time: {value!r}")


def iso(dt: datetime, seconds: bool = True) -> str:
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S" if seconds else "%Y-%m-%dT%H:%M")


def local_date(tz: str = "Europe/Berlin") -> str:
    """Today's date in the viewer's time zone: the day the clip is delivered."""
    return datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")


def daterange_months(start: datetime, end: datetime):
    """Yield (month_start, next_month_start) pairs covering [start, end)."""
    cur = datetime(start.year, start.month, 1, tzinfo=UTC)
    while cur < end:
        nxt = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1, tzinfo=UTC)
        yield cur, nxt
        cur = nxt


# ----------------------------------------------------------------------------- HTTP

def http_get(url: str, params=None, timeout: float = 60, retries: int = 4,
             backoff: float = 4.0, stream: bool = False) -> requests.Response:
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout, stream=stream,
                             headers={"User-Agent": USER_AGENT})
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status()
            return r
        except Exception as err:  # noqa: BLE001 - network errors come in many shapes
            last = err
            if attempt < retries:
                wait = backoff * attempt
                log.warning("GET %s failed (%s); retry %d/%d in %.0fs",
                            url.split("?")[0], type(err).__name__, attempt, retries - 1, wait)
                time.sleep(wait)
    raise Unavailable(f"GET {url.split('?')[0]} failed after {retries} tries: {last}")


def http_json(url: str, params=None, **kw):
    r = http_get(url, params=params, **kw)
    try:
        return r.json()
    except ValueError as err:
        raise Unavailable(f"{url.split('?')[0]} returned invalid JSON: {r.text[:200]!r}") from err


def download_all(items: list[tuple[str, Path]], max_bytes: float | None = None, workers: int = 6,
                 timeout: float = 180, retries: int = 3) -> list[Path]:
    """Download (url, destination) pairs in parallel.

    Existing non-empty files are kept. Returns the destinations that exist afterwards, in input
    order. Stops queuing new files once ``max_bytes`` has been written.
    """
    total = {"bytes": 0, "stop": False}
    lock = threading.Lock()

    def one(url: str, dest: Path) -> bool:
        if dest.exists() and dest.stat().st_size > 0:
            return True
        if total["stop"]:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        for attempt in range(1, retries + 1):
            try:
                with requests.get(url, stream=True, timeout=timeout,
                                  headers={"User-Agent": USER_AGENT}) as r:
                    r.raise_for_status()
                    n = 0
                    with open(part, "wb") as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
                            n += len(chunk)
                if n == 0:
                    raise OSError("empty response")
                part.replace(dest)
                with lock:
                    total["bytes"] += n
                    if max_bytes and total["bytes"] > max_bytes:
                        total["stop"] = True
                return True
            except Exception as err:  # noqa: BLE001
                part.unlink(missing_ok=True)
                if attempt == retries:
                    log.warning("download failed: %s (%s)", dest.name, err)
                else:
                    time.sleep(3 * attempt)
        return False

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        ok = list(pool.map(lambda it: one(*it), items))
    done = [dest for (url, dest), good in zip(items, ok) if good]
    log.info("downloaded %d/%d files, %.0f MB in %.0fs%s", len(done), len(items),
             total["bytes"] / 1e6, time.time() - t0,
             " (byte budget reached)" if total["stop"] else "")
    return done


# ----------------------------------------------------------------------------- misc

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fmt_mb(nbytes: float) -> str:
    return f"{nbytes / 1e6:.0f} MB" if nbytes < 1e9 else f"{nbytes / 1e9:.2f} GB"


def hours(h: float) -> timedelta:
    return timedelta(hours=h)


def minutes(m: float) -> timedelta:
    return timedelta(minutes=m)


def last_image_hdu(hdul):
    """Last HDU holding a 2-D image. (Index access: an HDUList loads lazily, so iterating it
    in reverse with reversed() sees nothing.)"""
    for i in range(len(hdul) - 1, -1, -1):
        hdu = hdul[i]
        if getattr(hdu, "data", None) is not None and hdu.data.ndim == 2:
            return hdu
    return None
