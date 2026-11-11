"""Fetch iCalendar feeds and expand them into per-day events.

The frame only ever makes outbound HTTPS GETs to the configured feed URLs.
A failed fetch falls back to the last good copy on disk.
"""
from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

MAX_FEED_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class Event:
    start: datetime  # tz-aware, local zone; midnight for all-day events
    end: datetime  # exclusive
    all_day: bool
    title: str
    cal: int  # index of the feed it came from


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise ValueError("refusing redirect to a non-https URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnlyRedirects)


def _validate(data: bytes) -> None:
    """Raise unless the feed parses and expands, so a bad download never replaces a good cache."""
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("feed too large")
    today = date.today()
    events_between(data, today - timedelta(days=7), today + timedelta(days=28), ZoneInfo("UTC"))


def fetch(url: str, cache_dir: Path, timeout: float = 20.0) -> tuple[bytes | None, datetime | None]:
    """Return (ics bytes, saved_at). saved_at is None when the data was fetched just now;
    otherwise it is when the cached copy was saved. (None, None) if there is no data at all."""
    if not url.startswith("https://"):
        raise ValueError("feed URLs must be https")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".ics")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "skycal"})
        with _opener.open(req, timeout=timeout) as resp:
            data = resp.read(MAX_FEED_BYTES + 1)
        _validate(data)
        tmp = cache.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(cache)
        return data, None
    except Exception:
        if cache.exists():
            return cache.read_bytes(), datetime.fromtimestamp(cache.stat().st_mtime).astimezone()
        return None, None


def events_between(ics: bytes, first: date, last: date, tz: ZoneInfo, cal: int = 0) -> list[Event]:
    """Events (recurrences expanded) overlapping the local days first..last inclusive."""
    calendar = icalendar.Calendar.from_ical(ics)
    lo = datetime.combine(first, time.min, tz)
    hi = datetime.combine(last + timedelta(days=1), time.min, tz)
    out = []
    for comp in recurring_ical_events.of(calendar).between(lo, hi):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if "DTSTART" not in comp:
            continue
        start = comp["DTSTART"].dt
        end_prop = comp.get("DTEND")
        title = str(comp.get("SUMMARY", "")).strip() or "(no title)"
        if not isinstance(start, datetime):  # all-day: plain dates, in the viewer's zone
            end = end_prop.dt if end_prop is not None else start + timedelta(days=1)
            if end <= start:
                end = start + timedelta(days=1)
            ev = Event(datetime.combine(start, time.min, tz), datetime.combine(end, time.min, tz), True, title, cal)
        else:
            end = end_prop.dt if end_prop is not None else start
            if start.tzinfo is None:  # floating time: same wall clock everywhere
                start, end = start.replace(tzinfo=tz), end.replace(tzinfo=tz)
            ev = Event(start.astimezone(tz), end.astimezone(tz), False, title, cal)
        if ev.start < hi and (ev.end > lo or ev.start >= lo):
            out.append(ev)
    return out


def on_day(events: list[Event], day: date, tz: ZoneInfo) -> list[Event]:
    """Events touching one local day: all-day first, then by start time."""
    lo = datetime.combine(day, time.min, tz)
    hi = lo + timedelta(days=1)
    hits = [e for e in events if e.start < hi and (e.end > lo or lo <= e.start)]
    return sorted(hits, key=lambda e: (not e.all_day, e.start, e.title))
