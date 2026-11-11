"""Render one week to a PNG (development and test entry point).

Usage: python -m skycal --config config.toml --png out.png [--week-offset N] [--now ISO]
"""
from __future__ import annotations

import argparse
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .feed import events_between, fetch
from .render import fmt_time, render_week, week_start


def main() -> None:
    ap = argparse.ArgumentParser(prog="skycal")
    ap.add_argument("--config", type=Path, default=Path("config.toml"))
    ap.add_argument("--png", type=Path, required=True)
    ap.add_argument("--week-offset", type=int, default=0, help="0 = this week, -1 = last week, 1 = next week")
    ap.add_argument("--now", help="override the current time (ISO 8601), for testing")
    args = ap.parse_args()

    cfg = tomllib.loads(args.config.read_text())
    tz = ZoneInfo(cfg["timezone"])
    now = datetime.now(tz)
    if args.now:
        given = datetime.fromisoformat(args.now)
        now = given.replace(tzinfo=tz) if given.tzinfo is None else given.astimezone(tz)
    start = week_start(now.date(), cfg.get("first_weekday", 6)) + timedelta(weeks=args.week_offset)
    cache = Path(cfg.get("cache_dir", "cache"))

    events, oldest, missing = [], None, 0
    for i, url in enumerate(cfg["feeds"]):
        ics, saved_at = fetch(url, cache)
        try:
            if ics is None:
                raise ValueError("no data")
            events += events_between(ics, start, start + timedelta(days=6), tz, cal=i)
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
    render_week(start, events, now, note, stale=note is not None).save(args.png)
    print(f"{args.png}: week of {start}, {len(events)} events, {note or 'fresh'}")


if __name__ == "__main__":
    main()
