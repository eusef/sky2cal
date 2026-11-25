"""Active weather alerts from the US National Weather Service (NOAA), api.weather.gov.

Off unless `weather_place` is set in the config. One kind of request leaves the frame: the
place's coordinates (from weather.locate(), which keeps them on disk) go to api.weather.gov
on every refresh, with a User-Agent that names the program only. The service covers the
United States; anywhere else it answers with an error and nothing is shown. A failed fetch
falls back to the last copy on disk, and an alert is never shown past its end, however old
the copy is.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .feed import _opener, plain

ALERTS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "skycal wall calendar"
MAX_BYTES = 4 * 1024 * 1024
MAX_ALERTS = 8
MAX_AGE = timedelta(hours=24)        # an older saved copy is not used
SEVERITY = ("Extreme", "Severe", "Moderate", "Minor", "Unknown")    # most serious first


@dataclass(frozen=True)
class Alert:
    event: str                   # "Wind Advisory"
    severity: str                # one of SEVERITY
    headline: str
    description: str             # plain text, as the forecast office wrote it
    instruction: str
    area: str
    sender: str
    onset: datetime | None       # tz-aware, local zone
    ends: datetime | None        # when the hazard ends, else when this message expires
    id: str = ""


def _when(text, tz: ZoneInfo) -> datetime | None:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return when.astimezone(tz) if when.tzinfo else None


def _text(value, limit: int) -> str:
    return plain(str(value or ""), limit)


def parse(raw: dict, tz: ZoneInfo) -> list[Alert]:
    """api.weather.gov GeoJSON -> alerts, most serious first. Test and exercise messages and
    cancellations are left out. Raises on anything that is not an alert list."""
    features = raw["features"]
    if not isinstance(features, list):
        raise ValueError("not an alert list")
    out = []
    for f in features:
        p = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(p, dict):
            continue
        if str(p.get("status", "Actual")) != "Actual" or str(p.get("messageType", "Alert")) == "Cancel":
            continue
        event = _text(p.get("event"), 80)
        if not event:
            continue
        severity = str(p.get("severity") or "Unknown")
        out.append(Alert(
            event=event,
            severity=severity if severity in SEVERITY else "Unknown",
            headline=_text(p.get("headline"), 300),
            description=_text(p.get("description"), 2500),
            instruction=_text(p.get("instruction"), 1200),
            area=_text(p.get("areaDesc"), 300),
            sender=_text(p.get("senderName"), 100),
            onset=_when(p.get("onset") or p.get("effective"), tz),
            ends=_when(p.get("ends"), tz) or _when(p.get("expires"), tz),
            id=str(p.get("id") or "")[:200]))
    out.sort(key=lambda a: (SEVERITY.index(a.severity), a.onset or datetime.max.replace(tzinfo=tz), a.event))
    seen, unique = set(), []
    for a in out:                    # one office often sends the same alert for neighbouring zones
        key = (a.event, a.ends, a.headline)
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique[:MAX_ALERTS]


def active(alerts: list[Alert] | None, now: datetime) -> list[Alert]:
    """The alerts that have not ended by `now`."""
    return [a for a in alerts or () if a.ends is None or a.ends > now]


def fetch(lat: float, lon: float, tz: ZoneInfo, cache_dir: Path, timeout: float = 20.0) -> tuple[list[Alert] | None, bool]:
    """(alerts, fresh). Falls back to the saved copy for the same point, if it is not older
    than MAX_AGE; (None, False) when there is nothing to go by."""
    saved = cache_dir / "alerts.json"
    point = f"{lat:.4f},{lon:.4f}"
    now = datetime.now(tz)
    try:
        req = urllib.request.Request(f"{ALERTS_URL}?point={point}",
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
        with _opener.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("answer too large")
        raw = json.loads(body)
        alerts = parse(raw, tz)
    except Exception:
        try:
            old = json.loads(saved.read_text())
            if old["point"] == point and now - datetime.fromisoformat(old["fetched"]) <= MAX_AGE:
                return parse(old["raw"], tz), False
        except Exception:
            pass
        return None, False
    try:                             # a copy on disk is only for when the next fetch fails
        tmp = saved.with_suffix(".tmp")
        tmp.write_text(json.dumps({"point": point, "fetched": now.isoformat(), "raw": raw}))
        tmp.replace(saved)
    except OSError:
        pass
    return alerts, True


def until(alert: Alert, now: datetime) -> str:
    """"until 4p", "until 6a Thu", "until Nov 30", or "" when the end is not known."""
    if alert.ends is None:
        return ""
    end = alert.ends.astimezone(now.tzinfo)
    hour = end.hour % 12 or 12
    clock = f"{hour}{'a' if end.hour < 12 else 'p'}" if end.minute == 0 else f"{hour}:{end.minute:02d}{'a' if end.hour < 12 else 'p'}"
    days = (end.date() - now.date()).days
    if days <= 0:
        return f"until {clock}"
    if days < 7:
        return f"until {clock} {end:%a}"
    return f"until {end:%b} {end.day}"
