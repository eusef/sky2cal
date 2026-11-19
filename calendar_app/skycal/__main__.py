"""Render one screen to a PNG (development and test entry point).

Usage: python -m skycal --config config.toml --png out.png [--screen week|detail|day|weather|saver]
                        [--week-offset N] [--now ISO] [--day YYYY-MM-DD] [--pick N]

--screen detail shows event number --pick (default 0) of --day (default today); --screen day
shows that day's list.
"""
from __future__ import annotations

import argparse
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import weather as wx
from .feed import events_between, fetch, on_day
from .render import fmt_time, render_day, render_detail, render_saver, render_weather, render_week, week_start


def main() -> None:
    ap = argparse.ArgumentParser(prog="skycal")
    ap.add_argument("--config", type=Path, default=Path("config.toml"))
    ap.add_argument("--png", type=Path, required=True)
    ap.add_argument("--screen", choices=("week", "detail", "day", "weather", "saver"), default="week")
    ap.add_argument("--week-offset", type=int, default=0, help="0 = this week, -1 = last week, 1 = next week")
    ap.add_argument("--now", help="override the current time (ISO 8601), for testing")
    ap.add_argument("--day", help="the day for --screen detail and day (default: today)")
    ap.add_argument("--pick", type=int, default=0, help="which of that day's events --screen detail shows")
    args = ap.parse_args()

    cfg = tomllib.loads(args.config.read_text())
    tz = ZoneInfo(cfg["timezone"])
    now = datetime.now(tz)
    if args.now:
        given = datetime.fromisoformat(args.now)
        now = given.replace(tzinfo=tz) if given.tzinfo is None else given.astimezone(tz)
    start = week_start(now.date(), cfg.get("first_weekday", 6)) + timedelta(weeks=args.week_offset)
    cache = Path(cfg.get("cache_dir", "cache"))
    day = date.fromisoformat(args.day) if args.day else now.date()
    first, last = min(start, day, now.date()), max(start + timedelta(days=6), day, now.date() + timedelta(days=1))

    events, oldest, missing = [], None, 0
    for i, url in enumerate(cfg["feeds"]):
        ics, saved_at = fetch(url, cache)
        try:
            if ics is None:
                raise ValueError("no data")
            events += events_between(ics, first, last, tz, cal=i)
        except Exception:
            missing += 1
            continue
        if saved_at is not None:
            oldest = min(oldest, saved_at) if oldest else saved_at
    note = None
    if missing:
        note = "No calendar data yet" if missing == len(cfg["feeds"]) else "Some calendars unavailable"
    elif oldest:
        saved = oldest.astimezone(tz)
        note = f"Offline, saved {saved:%b} {saved.day} {fmt_time(saved)}"

    place = str(cfg.get("weather_place", "")).strip()
    forecast = wx.fetch(place, str(cfg.get("weather_units", "fahrenheit")), tz, cache)[0] if place else None

    img = render_week(start, events, now, note, stale=note is not None, weather=forecast)
    if args.screen == "weather":
        img = render_weather(forecast, now, "" if place else "Type your town on the settings page to see the weather here.")
    elif args.screen == "saver":
        img = render_saver(now, events, forecast)
    elif args.screen == "day":
        img = render_day(img, day, events, now)
    elif args.screen == "detail":
        todays = on_day(events, day, tz)
        if not todays:
            raise SystemExit(f"no events on {day}")
        img = render_detail(img, todays[args.pick % len(todays)], now)
    img.save(args.png)
    print(f"{args.png}: {args.screen}, week of {start}, {len(events)} events, {note or 'fresh'}")


if __name__ == "__main__":
    main()
