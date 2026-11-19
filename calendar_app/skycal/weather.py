"""Hourly weather for today and tomorrow, from Open-Meteo (https, no account or key).

Off unless `weather_place` is set in the config. Two kinds of request leave the frame:
the place name goes to geocoding-api.open-meteo.com once (the answer is kept on disk), and
its coordinates go to api.open-meteo.com on every refresh. A place written as
"latitude, longitude" skips the first. A failed fetch falls back to the last copy on disk.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .feed import _opener

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_BYTES = 2 * 1024 * 1024
MAX_AGE = timedelta(hours=12)        # an older saved forecast is not shown
MAX_NAME = 60                        # characters of a place name that are kept
MAX_HOURS, MAX_DAYS = 96, 4          # more than was asked for is not kept
TEMP_RANGE = (-130.0, 150.0)         # anything outside is not a temperature, in either unit
_COORDS = re.compile(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*")
_US_STATES = dict(zip(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
    "RI SC SD TN TX UT VT VA WA WV WI WY DC".split(),
    ("Alabama,Alaska,Arizona,Arkansas,California,Colorado,Connecticut,Delaware,Florida,Georgia,Hawaii,Idaho,Illinois,"
     "Indiana,Iowa,Kansas,Kentucky,Louisiana,Maine,Maryland,Massachusetts,Michigan,Minnesota,Mississippi,Missouri,"
     "Montana,Nebraska,Nevada,New Hampshire,New Jersey,New Mexico,New York,North Carolina,North Dakota,Ohio,Oklahoma,"
     "Oregon,Pennsylvania,Rhode Island,South Carolina,South Dakota,Tennessee,Texas,Utah,Vermont,Virginia,Washington,"
     "West Virginia,Wisconsin,Wyoming,District of Columbia").split(",")))

_COUNTRY_NAMES = {"uk": "united kingdom", "usa": "united states", "u.s.": "united states", "u.s.a.": "united states",
                  "england": "england", "america": "united states"}

# WMO weather codes -> (icon name for render.weather_icon, short description)
_CODES = {
    0: ("clear", "Clear"), 1: ("clear", "Mostly clear"), 2: ("partly", "Partly cloudy"), 3: ("cloud", "Cloudy"),
    45: ("fog", "Fog"), 48: ("fog", "Freezing fog"),
    51: ("rain", "Light drizzle"), 53: ("rain", "Drizzle"), 55: ("rain", "Heavy drizzle"),
    56: ("rain", "Freezing drizzle"), 57: ("rain", "Freezing drizzle"),
    61: ("rain", "Light rain"), 63: ("rain", "Rain"), 65: ("rain", "Heavy rain"),
    66: ("rain", "Freezing rain"), 67: ("rain", "Freezing rain"),
    71: ("snow", "Light snow"), 73: ("snow", "Snow"), 75: ("snow", "Heavy snow"), 77: ("snow", "Snow grains"),
    80: ("rain", "Light showers"), 81: ("rain", "Showers"), 82: ("rain", "Heavy showers"),
    85: ("snow", "Snow showers"), 86: ("snow", "Heavy snow showers"),
    95: ("storm", "Thunderstorm"), 96: ("storm", "Thunderstorm, hail"), 99: ("storm", "Thunderstorm, hail"),
}


def describe(code: int) -> tuple[str, str]:
    """(icon name, description) of a WMO weather code."""
    return _CODES.get(code, ("cloud", "Unknown"))


@dataclass(frozen=True)
class Hour:
    time: datetime               # tz-aware, start of the hour
    temp: float
    code: int
    precip: int | None           # chance of precipitation, percent
    is_day: bool


@dataclass(frozen=True)
class Day:
    high: float
    low: float
    code: int
    sunrise: datetime | None
    sunset: datetime | None


@dataclass(frozen=True)
class Weather:
    place: str                   # name to show
    unit: str                    # "F" or "C"
    fetched: datetime            # tz-aware
    hours: tuple[Hour, ...]
    days: dict                   # date -> Day

    def at(self, when: datetime) -> Hour | None:
        """The hour containing `when`, if the forecast covers it."""
        for h in self.hours:
            if h.time <= when < h.time + timedelta(hours=1):
                return h
        return None

    def on(self, day: date) -> list[Hour]:
        return [h for h in self.hours if h.time.date() == day]


def _sane(value) -> bool:
    return value is not None and TEMP_RANGE[0] <= float(value) <= TEMP_RANGE[1]      # false for NaN too


def _get(url: str, params: dict, timeout: float) -> dict:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "skycal"})
    with _opener.open(req, timeout=timeout) as resp:       # refuses a redirect away from https
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("response too large")
    return json.loads(data)


def pick_place(results: list, hint: str) -> dict | None:
    """The first geocoding result whose region or country is the text after the comma (a US
    state may be abbreviated); with no such text, the first result; else None."""
    hint = hint.strip().lower()
    if not hint:
        return results[0] if results else None
    parts = [p.strip() for p in hint.split(",") if p.strip()]         # "OR, USA": the nearest part first
    for part in parts:
        for wanted in (_US_STATES.get(part.upper(), "").lower(), _COUNTRY_NAMES.get(part, part)):   # "CA": California before Canada
            for r in results:
                if wanted and wanted in (str(r.get(k, "")).lower() for k in ("admin1", "country", "country_code")):
                    return r
    return None


def locate(place: str, cache_dir: Path, timeout: float = 20.0) -> tuple[float, float, str]:
    """(latitude, longitude, name to show) for a town, "Town, Region", a postal code or "lat, lon"."""
    m = _COORDS.fullmatch(place)
    if m and abs(float(m[1])) <= 90 and abs(float(m[2])) <= 180:
        return float(m[1]), float(m[2]), ""
    saved = cache_dir / "place.json"
    try:
        hit = json.loads(saved.read_text())
        if hit["query"] == place and abs(float(hit["latitude"])) <= 90 and abs(float(hit["longitude"])) <= 180:
            return float(hit["latitude"]), float(hit["longitude"]), str(hit["name"])[:MAX_NAME]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    name, _, hint = place.partition(",")
    found = _get(GEOCODE_URL, {"name": name.strip(), "count": 20, "language": "en", "format": "json"}, timeout)
    r = pick_place(found.get("results") or [], hint)
    if r is None:
        raise LookupError("place not found")
    shown = " ".join(", ".join(str(r[k]) for k in ("name", "admin1") if r.get(k)).split())[:MAX_NAME]
    hit = {"query": place, "latitude": float(r["latitude"]), "longitude": float(r["longitude"]), "name": shown}
    if not (abs(hit["latitude"]) <= 90 and abs(hit["longitude"]) <= 180):            # false for NaN too
        raise ValueError("coordinates out of range")
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = saved.with_suffix(".tmp")
    tmp.write_text(json.dumps(hit))
    tmp.replace(saved)
    return hit["latitude"], hit["longitude"], shown


def parse(raw: dict, place: str, unit: str, fetched: datetime, tz: ZoneInfo) -> Weather:
    """Open-Meteo forecast JSON -> Weather. Raises on anything unexpected."""
    def local(text: str) -> datetime:
        return datetime.fromisoformat(text).replace(tzinfo=tz)

    h = raw["hourly"]
    precip = h.get("precipitation_probability") or [None] * len(h["time"])
    hours = tuple(
        Hour(local(t), float(temp), int(code), None if p is None else int(p), bool(day))
        for t, temp, code, p, day in zip(h["time"][:MAX_HOURS], h["temperature_2m"], h["weather_code"], precip, h["is_day"])
        if _sane(temp) and code is not None)
    if not hours:
        raise ValueError("no hourly data")
    d = raw["daily"]
    days = {}
    for i, t in enumerate(d["time"][:MAX_DAYS]):
        if not (_sane(d["temperature_2m_max"][i]) and _sane(d["temperature_2m_min"][i])):
            continue
        days[date.fromisoformat(t)] = Day(
            float(d["temperature_2m_max"][i]), float(d["temperature_2m_min"][i]), int(d["weather_code"][i] or 0),
            local(d["sunrise"][i]) if d["sunrise"][i] else None, local(d["sunset"][i]) if d["sunset"][i] else None)
    return Weather(" ".join(place.split())[:MAX_NAME], unit, fetched, hours, days)


def fetch(place: str, units: str, tz: ZoneInfo, cache_dir: Path, timeout: float = 20.0) -> tuple[Weather | None, bool]:
    """(weather, fresh). Falls back to the saved forecast, if it is for the same place and units
    and not older than MAX_AGE; (None, False) when there is nothing to show."""
    unit = "C" if units == "celsius" else "F"
    saved = cache_dir / "weather.json"
    now = datetime.now(tz)
    try:
        lat, lon, shown = locate(place, cache_dir, timeout)
        raw = _get(FORECAST_URL, {
            "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}",
            "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset",
            "temperature_unit": "celsius" if unit == "C" else "fahrenheit",
            "timezone": tz.key, "forecast_days": 3}, timeout)
        weather = parse(raw, shown or place, unit, now, tz)
        tmp = saved.with_suffix(".tmp")
        tmp.write_text(json.dumps({"query": place, "unit": unit, "name": weather.place,
                                   "fetched": now.isoformat(), "raw": raw}))
        tmp.replace(saved)
        return weather, True
    except Exception:
        try:
            old = json.loads(saved.read_text())
            fetched = datetime.fromisoformat(old["fetched"]).astimezone(tz)
            if old["query"] == place and old["unit"] == unit and now - fetched <= MAX_AGE:
                return parse(old["raw"], str(old["name"]), unit, fetched, tz), False
        except Exception:
            pass
        return None, False
