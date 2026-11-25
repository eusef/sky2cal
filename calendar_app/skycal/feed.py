"""Fetch iCalendar feeds and expand them into per-day events.

Only outbound HTTPS GETs to the configured feed URLs are made here (weather.py and alerts.py
make the frame's only other requests).
A failed fetch falls back to the last good copy on disk.
"""
from __future__ import annotations

import hashlib
import html
import pickle
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

MAX_FEED_BYTES = 20 * 1024 * 1024
MAX_NOTES = 1200
_BREAK = re.compile(r"<(?:br|/p|/div|/li)\s*/?>", re.I)    # one \s* only: two in a row backtrack badly
_TAG = re.compile(r"</?(?:a|b|i|u|p|br|div|span|ul|ol|li|html|body|head|strong|em|h[1-6]|font|table|tbody|tr|td|th|"
                  r"blockquote|pre|code|hr|img|small|sub|sup|s)\b[^<>]{0,2000}>", re.I)
MAX_TITLE = 300
MAX_COLOR = 40                       # characters of a COLOR value that are kept; render.py checks the rest


def plain(text: str, limit: int = MAX_NOTES) -> str:
    """Calendar text as plain text: HTML tags dropped (some calendars store notes as HTML; other
    text in angle brackets, such as an email address, is kept), entities decoded, blank lines
    and runs of spaces collapsed, cut to `limit` characters."""
    text = text[:20 * limit]             # bounds the work below, whatever the calendar sends
    if "<" in text and ">" in text:
        text = _TAG.sub("", _BREAK.sub("\n", text))
    lines = [" ".join(line.split()) for line in html.unescape(text).splitlines()]
    text = "\n".join(line for line in lines if line)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


@dataclass(frozen=True)
class Event:
    start: datetime  # tz-aware, local zone; midnight for all-day events
    end: datetime  # exclusive
    all_day: bool
    title: str
    cal: int  # index of the feed it came from
    location: str = ""
    description: str = ""  # plain text, at most MAX_NOTES characters
    color: str = ""        # the event's own COLOR (RFC 7986: a CSS colour name or #rrggbb), as written
    cal_color: str = ""    # the colour the feed gives its whole calendar (COLOR or X-APPLE-CALENDAR-COLOR)


def feed_key(url: str) -> str:
    """Short id of a feed address, the same as skyadmin.settings.feed_id(). The config keeps
    per-calendar colours under it (feed_colors), so the secret address is not repeated there."""
    return hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()[:16]


def _color(prop) -> str:
    """A COLOR-like property value as a short plain string ("" if absent)."""
    if isinstance(prop, list):
        prop = prop[0] if prop else ""
    return str(prop or "").strip()[:MAX_COLOR]


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
    cal_color = _color(calendar.get("COLOR")) or _color(calendar.get("X-APPLE-CALENDAR-COLOR"))
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
        title = " ".join(str(comp.get("SUMMARY", "")).split())[:MAX_TITLE] or "(no title)"   # always one line
        where, notes = comp.get("LOCATION", ""), comp.get("DESCRIPTION", "")
        where = plain(str(where[0] if isinstance(where, list) and where else where), 300)   # a repeated property
        notes = plain(str(notes[0] if isinstance(notes, list) and notes else notes))        # comes as a list
        tint = _color(comp.get("COLOR"))
        if not isinstance(start, datetime):  # all-day: plain dates, in the viewer's zone
            end = end_prop.dt if end_prop is not None else start + timedelta(days=1)
            if end <= start:
                end = start + timedelta(days=1)
            ev = Event(datetime.combine(start, time.min, tz), datetime.combine(end, time.min, tz), True, title, cal, where, notes, tint, cal_color)
        else:
            end = end_prop.dt if end_prop is not None else start
            if start.tzinfo is None:  # floating time: same wall clock everywhere
                start, end = start.replace(tzinfo=tz), end.replace(tzinfo=tz)
            ev = Event(start.astimezone(tz), end.astimezone(tz), False, title, cal, where, notes, tint, cal_color)
        if ev.start < hi and (ev.end > lo or ev.start >= lo):
            out.append(ev)
    return out


def read_feeds(urls: list, cache_dir: Path, first: date, last: date, tz: ZoneInfo) -> list[tuple]:
    """Fetch every feed and read its events for the days first..last. One entry per feed:
    (ics, saved_at, events, first, last), with None for whatever could not be had."""
    data = []
    for i, url in enumerate(urls):
        ics, saved_at, events = None, None, None
        try:
            ics, saved_at = fetch(url, cache_dir)
            if ics is not None:
                events = events_between(ics, first, last, tz, cal=i)
        except Exception as err:
            print(f"feed {i} not usable: {type(err).__name__}", file=sys.stderr)
        data.append((ics, saved_at, events, first, last))
    return data


def read_feeds_apart(urls: list, cache_dir: Path, first: date, last: date, tz: ZoneInfo, timeout: float = 600.0) -> list[tuple]:
    """read_feeds() in a process of its own. Reading a large calendar is seconds of plain
    Python, and in a thread that more than doubles the time of every picture drawn meanwhile
    (measured on the frame, 2026-11-19). Raises when the other process fails or takes too long.

    The addresses are secret, so they travel on its standard input, never on its command line.
    SIGUSR1 is ignored there first of all: `systemctl kill -s USR1 skycal` (start the screensaver)
    reaches every process of the service, and would otherwise end this one."""
    if not urls:
        return []
    job = pickle.dumps((list(urls), str(Path(cache_dir).resolve()), first, last, tz.key))
    code = "import signal; signal.signal(signal.SIGUSR1, signal.SIG_IGN); from skycal import feed; feed.serve()"
    done = subprocess.run([sys.executable, "-c", code], input=job, stdout=subprocess.PIPE, timeout=timeout, check=True,
                          cwd=Path(__file__).resolve().parent.parent)
    data = pickle.loads(done.stdout)
    if not isinstance(data, list) or len(data) != len(urls) or not all(isinstance(e, tuple) and len(e) == 5 for e in data):
        raise ValueError("unexpected answer")
    return data


def serve() -> None:
    """The other end of read_feeds_apart(): the job from standard input, its result to standard output."""
    out, sys.stdout = sys.stdout.buffer, sys.stderr          # nothing else may write into the result
    urls, cache_dir, first, last, zone = pickle.loads(sys.stdin.buffer.read())
    out.write(pickle.dumps(read_feeds(urls, Path(cache_dir), first, last, ZoneInfo(zone))))
    out.flush()


def on_day(events: list[Event], day: date, tz: ZoneInfo) -> list[Event]:
    """Events touching one local day: all-day first, then by start time."""
    lo = datetime.combine(day, time.min, tz)
    hi = lo + timedelta(days=1)
    hits = [e for e in events if e.start < hi and (e.end > lo or lo <= e.start)]
    return sorted(hits, key=lambda e: (not e.all_day, e.start, e.title))
