"""Draw the calendar's screens as 1280x800 landscape images: the week, one event's details,
one day's list, the hourly weather and the screensaver. Pure functions of their inputs; no I/O
except fonts.

The functions that take `hits` append the tappable areas they draw to it, as
(x0, y0, x1, y1, target): target is an Event, or a date for "show this whole day"."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .feed import Event, on_day
from .weather import Weather, describe

W, H = 1280, 800
HEADER_H = 76
DAYHEAD_H = 84
PAD = 6

BG = (250, 248, 244)
INK = (28, 28, 30)
MUTED = (120, 120, 128)
FAINT = (176, 174, 172)
RULE = (224, 220, 212)
TODAY_BG = (255, 255, 255)
ACCENT = (0, 110, 220)
WARN = (190, 70, 30)
CAL_COLORS = [(0, 110, 220), (30, 150, 90), (200, 90, 40), (140, 80, 190), (200, 50, 90), (90, 100, 120)]
NIGHT_BG = (6, 6, 8)
NIGHT_INK = (205, 205, 210)
NIGHT_MUTED = (130, 130, 140)

# Hit boxes of the week view's header buttons, shared with the touch handler: name -> (x0, y0, x1, y1).
NAV = {"weather": (836, 12, 998, 64), "prev": (1010, 12, 1074, 64), "today": (1082, 12, 1198, 64),
       "next": (1206, 12, 1270, 64)}
CLOSE = (1150, 12, 1270, 64)     # "Back" button of the weather screen

_FONT_DIRS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    if (size, bold) not in _cache:
        for regular, heavy in _FONT_DIRS:
            if Path(regular).exists():
                _cache[(size, bold)] = ImageFont.truetype(heavy if bold else regular, size)
                break
        else:
            raise RuntimeError("no font found; install fonts-dejavu-core")
    return _cache[(size, bold)]


def week_start(day: date, first_weekday: int = 6) -> date:
    """First day of the week containing `day`. first_weekday: Monday=0 .. Sunday=6."""
    return day - timedelta(days=(day.weekday() - first_weekday) % 7)


def fmt_time(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    suffix = "a" if dt.hour < 12 else "p"
    return f"{hour}{suffix}" if dt.minute == 0 else f"{hour}:{dt.minute:02d}{suffix}"


def fmt_when(ev: Event) -> str:
    """When an event happens, in words: day, and times unless it lasts all day."""
    def day(d: date) -> str:
        return f"{d:%A}, {d:%B} {d.day}"

    if ev.all_day:
        last = (ev.end - timedelta(days=1)).date()
        if last <= ev.start.date():
            return f"{day(ev.start.date())}, all day"
        return f"{day(ev.start.date())} to {day(last)}"
    if ev.end <= ev.start:
        return f"{day(ev.start.date())}, {fmt_time(ev.start)}"
    if ev.end.date() == ev.start.date() or ev.end == datetime.combine(ev.start.date() + timedelta(days=1), time.min, ev.start.tzinfo):
        return f"{day(ev.start.date())}, {fmt_time(ev.start)} to {fmt_time(ev.end)}"
    return f"{day(ev.start.date())} {fmt_time(ev.start)} to {day(ev.end.date())} {fmt_time(ev.end)}"


def fmt_until(ev: Event, now: datetime) -> str:
    """Where `now` is against a timed event: "In 2 h 15 min", "Now", "Ended"; "" for all-day events."""
    if ev.all_day:
        return ""
    if now >= ev.end and now > ev.start:
        return "Ended"
    if now >= ev.start:
        return "Now"
    minutes = int((ev.start - now).total_seconds() // 60) + 1
    if minutes < 60:
        return f"In {minutes} min"
    if minutes < 24 * 60:
        return f"In {minutes // 60} h {minutes % 60} min" if minutes % 60 else f"In {minutes // 60} h"
    days = (ev.start.date() - now.date()).days
    return "Tomorrow" if days == 1 else f"In {days} days"


def fit(draw: ImageDraw.ImageDraw, text: str, fnt, width: int) -> str:
    """`text` on one line, cut with an ellipsis if it is wider than `width`."""
    text = " ".join(text[:2000].split())
    if draw.textlength(text, font=fnt) <= width:
        return text
    lo, hi = 0, len(text)                  # the longest start that fits with the ellipsis, by halving
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + "…", font=fnt) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int, max_lines: int) -> list[str]:
    """Word-wrap `text`; line breaks in it are kept. The last line allowed ends in an ellipsis."""
    lines = []
    for para in text.splitlines() or [""]:
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=fnt) <= width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            while draw.textlength(word, font=fnt) > width and len(word) > 1:  # hard-break a long word
                lo, hi = 1, len(word) - 1          # the longest start of the word that fits, by halving
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if draw.textlength(word[:mid], font=fnt) <= width:
                        lo = mid
                    else:
                        hi = mid - 1
                lines.append(word[:lo])
                word = word[lo:]
                if len(lines) > max_lines:
                    break
            cur = word
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            break
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=fnt) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _mix(color: tuple, toward: tuple, amount: float) -> tuple:
    return tuple(int(c + (t - c) * amount) for c, t in zip(color, toward))


def _temp(value: float) -> str:
    return f"{round(value)}°"


# ---- weather pictures ----

def weather_icon(d: ImageDraw.ImageDraw, cx: int, cy: int, size: int, name: str, is_day: bool = True,
                 bg: tuple = BG, fade: bool = False) -> None:
    """One of weather.describe()'s icon names, drawn from shapes so it looks the same with any font."""
    s = size
    sun, moon, cloud, rain, snow, bolt = ((245, 170, 30), (128, 138, 180), (150, 156, 168), (40, 120, 220),
                                          (110, 165, 225), (240, 175, 20))
    if fade:
        sun = moon = cloud = rain = snow = bolt = _mix(FAINT, bg, 0.25)

    def light(x: float, y: float, k: float) -> None:
        if is_day:
            r = 0.22 * s * k
            d.ellipse((x - r, y - r, x + r, y + r), fill=sun)
            for i in range(8):
                a = i * math.pi / 4
                d.line((x + math.cos(a) * r * 1.45, y + math.sin(a) * r * 1.45,
                        x + math.cos(a) * r * 2.0, y + math.sin(a) * r * 2.0), fill=sun, width=max(2, s // 14))
        else:
            r = 0.3 * s * k
            d.ellipse((x - r, y - r, x + r, y + r), fill=moon)
            d.ellipse((x - r * 0.45, y - r * 1.25, x + r * 1.45, y + r * 0.65), fill=bg)

    def puff(y: float) -> None:
        d.rounded_rectangle((cx - 0.4 * s, y, cx + 0.4 * s, y + 0.26 * s), radius=0.13 * s, fill=cloud)
        d.ellipse((cx - 0.3 * s, y - 0.18 * s, cx + 0.08 * s, y + 0.2 * s), fill=cloud)
        d.ellipse((cx - 0.02 * s, y - 0.09 * s, cx + 0.28 * s, y + 0.2 * s), fill=cloud)

    if name == "clear":
        light(cx, cy, 1.0)
    elif name == "partly":
        light(cx + 0.18 * s, cy - 0.16 * s, 0.75)
        puff(cy - 0.02 * s)
    elif name == "fog":
        for i in range(4):
            y = cy - 0.27 * s + i * 0.18 * s
            inset = 0.1 * s if i % 2 else 0
            d.line((cx - 0.38 * s + inset, y, cx + 0.38 * s - inset, y), fill=cloud, width=max(2, s // 12))
    else:
        puff(cy - 0.2 * s)
        low = cy + 0.14 * s
        if name == "rain":
            for i in (-1, 0, 1):
                x = cx + i * 0.22 * s
                d.line((x + 0.05 * s, low, x - 0.05 * s, low + 0.24 * s), fill=rain, width=max(2, s // 12))
        elif name == "snow":
            r = max(2, s // 14)
            for i, dy in ((-1, 0.05), (0, 0.19), (1, 0.05)):
                x, y = cx + i * 0.22 * s, low + dy * s
                d.ellipse((x - r, y - r, x + r, y + r), fill=snow)
        elif name == "storm":
            d.polygon([(cx + 0.06 * s, low - 0.04 * s), (cx - 0.14 * s, low + 0.16 * s), (cx - 0.01 * s, low + 0.16 * s),
                       (cx - 0.08 * s, low + 0.36 * s), (cx + 0.16 * s, low + 0.1 * s), (cx + 0.03 * s, low + 0.1 * s)],
                      fill=bolt)


# ---- week ----

def _button(d: ImageDraw.ImageDraw, box: tuple, label: str, size: int, lift: int = 0) -> None:
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=12, fill=TODAY_BG, outline=RULE, width=2)
    d.text(((x0 + x1) // 2, (y0 + y1) // 2 - lift), label, font=font(size, True), fill=INK, anchor="mm")


def _header(d: ImageDraw.ImageDraw, start: date, now: datetime, note: str, stale: bool, weather: Weather | None) -> None:
    end = start + timedelta(days=6)
    if start.month == end.month:
        title = f"{start:%B} {start.day} to {end.day}, {end.year}"
    elif start.year == end.year:
        title = f"{start:%b} {start.day} to {end:%b} {end.day}, {end.year}"
    else:
        title = f"{start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year}"
    d.text((20, HEADER_H // 2), title, font=font(34, True), fill=INK, anchor="lm")

    right = NAV["weather"][0] - 18
    if note:       # the clock above, the status below it
        room = right - (20 + d.textlength(title, font=font(34, True)) + 24)
        d.text((right, 27), fmt_time(now), font=font(24, True), fill=INK, anchor="rm")
        d.text((right, 54), fit(d, note, font(15), room), font=font(15), fill=WARN if stale else MUTED, anchor="rm")
    else:
        d.text((right, HEADER_H // 2), fmt_time(now), font=font(28, True), fill=INK, anchor="rm")

    # The weather button: the current conditions when there is a forecast, else the word.
    x0, y0, x1, y1 = NAV["weather"]
    hour = weather.at(now) if weather else None
    if hour:
        d.rounded_rectangle((x0, y0, x1, y1), radius=12, fill=TODAY_BG, outline=RULE, width=2)
        weather_icon(d, x0 + 44, (y0 + y1) // 2, 40, describe(hour.code)[0], hour.is_day, TODAY_BG)
        d.text((x0 + 80, (y0 + y1) // 2), _temp(hour.temp), font=font(26, True), fill=INK, anchor="lm")
    else:
        _button(d, NAV["weather"], "Weather", 22)
    _button(d, NAV["prev"], "‹", 36, 4)
    _button(d, NAV["today"], "Today", 22)
    _button(d, NAV["next"], "›", 36, 4)


def _day(d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, events: list[Event], now: datetime, hits: list | None) -> None:
    today, tz = now.date(), now.tzinfo
    is_today = day == today
    if is_today:
        d.rectangle((x0, HEADER_H, x1, H), fill=TODAY_BG)
    cx = (x0 + x1) // 2
    d.text((cx, HEADER_H + 20), f"{day:%a}".upper(), font=font(17, True), fill=ACCENT if is_today else MUTED, anchor="mm")
    if is_today:
        d.ellipse((cx - 23, HEADER_H + 33, cx + 23, HEADER_H + 79), fill=ACCENT)
    d.text((cx, HEADER_H + 56), str(day.day), font=font(28, True),
           fill=(255, 255, 255) if is_today else (MUTED if day < today else INK), anchor="mm")
    if hits is not None and events:
        hits.append((x0, HEADER_H, x1, HEADER_H + DAYHEAD_H, day))

    y = HEADER_H + DAYHEAD_H + PAD
    left, right = x0 + PAD, x1 - PAD
    text_w = right - left - 16
    title_f, time_f = font(18, True), font(15)
    lo = datetime.combine(day, time.min, tz)
    back = TODAY_BG if is_today else BG
    for i, ev in enumerate(events):
        color = CAL_COLORS[ev.cal % len(CAL_COLORS)]
        over = ev.end <= now and (ev.all_day or ev.start < now)        # finished: drawn faded
        current = not ev.all_day and ev.start <= now < ev.end
        lines = wrap(d, ev.title, title_f, text_w, 3)
        label = "" if ev.all_day else (fmt_time(ev.start) if ev.start >= lo else "continues")
        if current:
            label = f"Now, until {fmt_time(ev.end)}" if ev.end.date() == day else "Now"
        h = 10 + (19 if label else 0) + 22 * len(lines) + 6
        if y + h > H - 30 and i < len(events) - 1 or y + h > H - 4:
            d.text((cx, H - 16), f"+{len(events) - i} more", font=font(16, True), fill=ACCENT, anchor="mm")
            if hits is not None:
                hits.append((x0, y, x1, H, day))
            break
        if ev.all_day:
            d.rounded_rectangle((left, y, right, y + h), radius=8, fill=_mix(color, back, 0.6) if over else color)
            ink = sub = (255, 255, 255)
        else:
            tint = _mix(color, (255, 255, 255), 0.86)
            d.rounded_rectangle((left, y, right, y + h), radius=8, fill=_mix(tint, back, 0.5) if over else tint,
                                outline=color if current else None, width=2)
            d.rounded_rectangle((left, y, left + 5, y + h), radius=2, fill=_mix(color, back, 0.6) if over else color)
            ink, sub = (MUTED, _mix(color, back, 0.45)) if over else (INK, color)
        ty = y + 7
        if label:
            d.text((left + 12, ty), label, font=font(15, True) if current else time_f, fill=sub)
            ty += 19
        for line in lines:
            d.text((left + 12, ty), line, font=title_f, fill=ink)
            ty += 22
        if hits is not None:
            hits.append((left, y, right, y + h, ev))
        y += h + PAD


def render_week(start: date, events: list[Event], now: datetime, note: str | None = None, stale: bool = False,
                hits: list | None = None, weather: Weather | None = None, message: str = "") -> Image.Image:
    """`start` is the first day shown; `now` (tz-aware) sets the clock and today's highlight.
    `note` is the status text under the clock; `stale` draws it as a warning. The weather
    button shows the current conditions when `weather` has them. `message` is written across
    the middle of the week (for a frame that has no calendars yet)."""
    tz = now.tzinfo
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _header(d, start, now, note or "", stale, weather)
    d.line((0, HEADER_H, W, HEADER_H), fill=RULE, width=2)
    edges = [round(i * W / 7) for i in range(8)]
    for i in range(7):
        day = start + timedelta(days=i)
        _day(d, edges[i], edges[i + 1], day, on_day(events, day, tz), now, hits)
    for x in edges[1:-1]:
        d.line((x, HEADER_H, x, H), fill=RULE, width=1)
    d.line((0, HEADER_H + DAYHEAD_H, W, HEADER_H + DAYHEAD_H), fill=RULE, width=1)
    if message:
        lines = wrap(d, message, font(24), 700, 4)
        half = 28 + 17 * len(lines)
        d.rounded_rectangle((W // 2 - 400, 460 - half, W // 2 + 400, 460 + half), radius=18, fill=TODAY_BG, outline=RULE, width=2)
        for i, line in enumerate(lines):
            d.text((W // 2, 460 - 17 * (len(lines) - 1) + 34 * i), line, font=font(24), fill=INK, anchor="mm")
    return img


# ---- cards over the week ----

def _card(base: Image.Image, height: int, width: int = 820) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple]:
    """A copy of `base`, darkened, with an empty white card in the middle: (image, draw, card box)."""
    img = Image.blend(base, Image.new("RGB", base.size, (24, 24, 28)), 0.55)
    d = ImageDraw.Draw(img)
    height = min(height, H - 40)
    box = ((W - width) // 2, (H - height) // 2, (W + width) // 2, (H + height) // 2)
    d.rounded_rectangle(box, radius=22, fill=TODAY_BG)
    return img, d, box


def render_detail(base: Image.Image, ev: Event, now: datetime) -> Image.Image:
    """One event's details on a card over `base`. A tap anywhere closes it."""
    probe = ImageDraw.Draw(base)
    text_w = 820 - 46 - 36
    title_f, when_f, label_f, body_f = font(34, True), font(23), font(15, True), font(21)
    title = wrap(probe, ev.title, title_f, text_w, 3)
    when = wrap(probe, fmt_when(ev), when_f, text_w, 2)
    until = fmt_until(ev, now)
    where = wrap(probe, ev.location, body_f, text_w, 3) if ev.location else []
    fixed = 30 + 42 * len(title) + 10 + 30 * len(when) + (30 if until else 0) + (22 + 24 + 28 * len(where) if where else 0) + 62
    room = (H - 40 - fixed - 46) // 28
    notes = wrap(probe, ev.description, body_f, text_w, max(1, min(12, room))) if ev.description else []
    img, d, (x0, y0, x1, y1) = _card(base, fixed + (22 + 24 + 28 * len(notes) if notes else 0))
    color = CAL_COLORS[ev.cal % len(CAL_COLORS)]
    d.rounded_rectangle((x0, y0, x0 + 44, y1), radius=22, fill=color)      # colour strip down the left edge
    d.rectangle((x0 + 12, y0, x0 + 44, y1), fill=TODAY_BG)

    x, y = x0 + 46, y0 + 30
    for line in title:
        d.text((x, y), line, font=title_f, fill=INK)
        y += 42
    y += 10
    for line in when:
        d.text((x, y), line, font=when_f, fill=INK)
        y += 30
    if until:
        d.text((x, y + 2), until, font=font(19, True), fill=MUTED if until == "Ended" else color)
        y += 30
    for label, lines in (("WHERE", where), ("NOTES", notes)):
        if lines:
            y += 22
            d.text((x, y), label, font=label_f, fill=MUTED)
            y += 24
            for line in lines:
                d.text((x, y), line, font=body_f, fill=INK)
                y += 28
    d.text(((x0 + x1) // 2, y1 - 28), "Tap anywhere to close", font=font(16), fill=MUTED, anchor="mm")
    return img


DAY_ROWS = 13     # rows of the day list; a longer day is shown in pages


def render_day(base: Image.Image, day: date, events: list[Event], now: datetime, hits: list | None = None,
               first: int = 0) -> Image.Image:
    """Every event of one day as a list on a card over `base` (the week view cuts long days short).
    A day with more than DAY_ROWS events is shown in pages, from event number `first`; its last
    row then leads to the next page (hit target: the number of that page's first event, an int)."""
    tz = now.tzinfo
    everything = on_day(events, day, tz)
    first = first if 0 < first < len(everything) else 0
    todays = everything[first:]
    row_h, rows = 46, min(len(todays), DAY_ROWS)
    img, d, (x0, y0, x1, y1) = _card(base, 84 + row_h * max(rows, 1) + 56)
    d.text((x0 + 36, y0 + 46), f"{day:%A}, {day:%B} {day.day}", font=font(30, True), fill=INK, anchor="lm")
    count = f"{len(everything)} event" + ("" if len(everything) == 1 else "s")
    d.text((x1 - 36, y0 + 46), count, font=font(18), fill=MUTED, anchor="rm")
    lo = datetime.combine(day, time.min, tz)
    y = y0 + 84
    if not todays:
        d.text((x0 + 36, y + row_h // 2), "Nothing on this day", font=font(21), fill=MUTED, anchor="lm")
    for i, ev in enumerate(todays[:rows]):
        if i == rows - 1 and len(todays) > rows:
            d.line((x0 + 30, y, x1 - 30, y), fill=RULE, width=1)
            d.text((x0 + 36, y + row_h // 2), f"Show {len(todays) - i} more", font=font(19, True), fill=ACCENT, anchor="lm")
            if hits is not None:
                hits.append((x0, y, x1, y + row_h, first + i))
            break
        color = CAL_COLORS[ev.cal % len(CAL_COLORS)]
        over = ev.end <= now and (ev.all_day or ev.start < now)
        d.line((x0 + 30, y, x1 - 30, y), fill=RULE, width=1)
        d.rounded_rectangle((x0 + 36, y + 9, x0 + 42, y + row_h - 9), radius=3, fill=_mix(color, TODAY_BG, 0.6) if over else color)
        if ev.all_day:
            span = "All day"
        elif ev.start < lo:
            span = f"until {fmt_time(ev.end)}" if ev.end <= lo + timedelta(days=1) else "All day"
        else:
            span = fmt_time(ev.start) if ev.end <= ev.start else f"{fmt_time(ev.start)} to {fmt_time(ev.end)}"
        d.text((x0 + 58, y + row_h // 2), span, font=font(19), fill=MUTED if over else color, anchor="lm")
        d.text((x0 + 250, y + row_h // 2), fit(d, ev.title, font(21, True), x1 - 36 - (x0 + 250)), font=font(21, True),
               fill=MUTED if over else INK, anchor="lm")
        if hits is not None:
            hits.append((x0, y, x1, y + row_h, ev))
        y += row_h
    d.text(((x0 + x1) // 2, y1 - 28), "Tap an event for details, or outside to close", font=font(16), fill=MUTED, anchor="mm")
    return img


# ---- weather ----

def _weather_day(d: ImageDraw.ImageDraw, top: int, name: str, day: date, weather: Weather, now: datetime,
                 lo_t: float, hi_t: float) -> None:
    hours = [h for h in weather.on(day) if h.time.hour >= min(6, now.hour if day == now.date() else 6)]
    d.text((24, top + 34), name, font=font(28, True), fill=INK, anchor="lm")
    name_w = d.textlength(name, font=font(28, True))
    d.text((24 + name_w + 14, top + 36), f"{day:%A}, {day:%B} {day.day}", font=font(20), fill=MUTED, anchor="lm")
    info = weather.days.get(day)
    if info:
        parts = [describe(info.code)[1], f"High {_temp(info.high)}", f"Low {_temp(info.low)}"]
        if info.sunrise and info.sunset:
            parts += [f"Sunrise {fmt_time(info.sunrise)}", f"Sunset {fmt_time(info.sunset)}"]
        d.text((W - 24, top + 36), "   ".join(parts), font=font(20), fill=INK, anchor="rm")
    if not hours:
        d.text((W // 2, top + 190), "No forecast for these hours", font=font(22), fill=MUTED, anchor="mm")
        return
    cw = (W - 40) / len(hours)
    span = max(hi_t - lo_t, 1.0)
    band_top, band_h = top + 176, 74
    points = []
    for i, h in enumerate(hours):
        cx = 20 + cw * (i + 0.5)
        past = h.time + timedelta(hours=1) <= now
        current = h.time <= now < h.time + timedelta(hours=1)
        back = BG
        if current:
            back = TODAY_BG
            d.rounded_rectangle((cx - cw / 2 + 2, top + 62, cx + cw / 2 - 2, top + 340), radius=12, fill=TODAY_BG,
                                outline=ACCENT, width=2)
        d.text((cx, top + 84), "Now" if current else fmt_time(h.time), font=font(17, current),
               fill=ACCENT if current else (FAINT if past else MUTED), anchor="mm")
        weather_icon(d, cx, top + 126, 44, describe(h.code)[0], h.is_day, back, fade=past)
        py = band_top + band_h - (h.temp - lo_t) / span * band_h
        points.append((cx, py, h, past))
        if h.precip is not None and h.precip >= 10 and not past:
            tone = ACCENT if h.precip >= 30 else MUTED
            d.polygon([(cx, top + 284), (cx - 5, top + 293), (cx + 5, top + 293)], fill=tone)
            d.ellipse((cx - 5, top + 288, cx + 5, top + 298), fill=tone)
            d.text((cx, top + 316), f"{h.precip}%", font=font(16, h.precip >= 50), fill=tone, anchor="mm")
    for (ax, ay, _, a_past), (bx, by, _, _) in zip(points, points[1:]):
        d.line((ax, ay, bx, by), fill=RULE if a_past else (235, 150, 60), width=3)
    for cx, py, h, past in points:
        d.ellipse((cx - 5, py - 5, cx + 5, py + 5), fill=FAINT if past else (225, 120, 30))
        d.text((cx, py - 20), _temp(h.temp), font=font(20, True), fill=FAINT if past else INK, anchor="mm")


def render_weather(weather: Weather | None, now: datetime, message: str = "") -> Image.Image:
    """Hour by hour for today and tomorrow. `message` is shown instead when there is no forecast."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((20, HEADER_H // 2), "Weather", font=font(34, True), fill=INK, anchor="lm")
    _button(d, CLOSE, "Back", 22)
    d.line((0, HEADER_H, W, HEADER_H), fill=RULE, width=2)
    if weather is None:
        for i, line in enumerate(wrap(d, message or "No forecast yet", font(26), W - 200, 4)):
            d.text((W // 2, 340 + i * 40), line, font=font(26), fill=MUTED, anchor="mm")
        return img
    sub = f"{weather.place}, as of {fmt_time(weather.fetched)}" if weather.place else f"As of {fmt_time(weather.fetched)}"
    if now - weather.fetched > timedelta(hours=2):
        sub += f" on {weather.fetched:%b} {weather.fetched.day} (no newer forecast could be fetched)"
    x = 20 + d.textlength("Weather", font=font(34, True)) + 20
    d.text((x, HEADER_H // 2 + 4), fit(d, sub, font(20), CLOSE[0] - 20 - x), font=font(20), fill=MUTED, anchor="lm")
    today = now.date()
    shown = [h.temp for day in (today, today + timedelta(days=1)) for h in weather.on(day)]
    lo_t, hi_t = (min(shown), max(shown)) if shown else (0.0, 1.0)
    _weather_day(d, HEADER_H, "Today", today, weather, now, lo_t, hi_t)
    d.line((0, HEADER_H + 362, W, HEADER_H + 362), fill=RULE, width=1)
    _weather_day(d, HEADER_H + 362, "Tomorrow", today + timedelta(days=1), weather, now, lo_t, hi_t)
    return img


# ---- screensaver ----

def render_saver(now: datetime, events: list[Event], weather: Weather | None) -> Image.Image:
    """Dark screen with the time, the date, the weather and what comes next. The block moves a
    little every minute so that nothing stays lit in one place."""
    img = Image.new("RGB", (W, H), NIGHT_BG)
    d = ImageDraw.Draw(img)
    cx = W // 2 + ((now.minute * 7 + now.hour * 3) % 21 - 10) * 12
    top = 96 + ((now.minute * 11 + now.hour) % 9 - 4) * 12

    clock, suffix = f"{now.hour % 12 or 12}:{now.minute:02d}", "am" if now.hour < 12 else "pm"
    clock_f, suffix_f = font(210, True), font(56, True)
    cw, sw = d.textlength(clock, font=clock_f), d.textlength(suffix, font=suffix_f)
    x = cx - (cw + 16 + sw) / 2
    d.text((x, top + 110), clock, font=clock_f, fill=NIGHT_INK, anchor="lm")
    d.text((x + cw + 16, top + 158), suffix, font=suffix_f, fill=NIGHT_MUTED, anchor="lm")
    d.text((cx, top + 262), f"{now:%A}, {now:%B} {now.day}", font=font(46), fill=NIGHT_INK, anchor="mm")

    y = top + 340
    hour = weather.at(now) if weather else None
    if hour:
        text = f"{_temp(hour.temp)}  {describe(hour.code)[1]}"
        info = weather.days.get(now.date())
        if info:
            text += f"   High {_temp(info.high)}  Low {_temp(info.low)}"
        tw = d.textlength(text, font=font(32))
        weather_icon(d, cx - tw / 2 - 12, y, 56, describe(hour.code)[0], hour.is_day, NIGHT_BG)
        d.text((cx + 34, y), text, font=font(32), fill=NIGHT_MUTED, anchor="mm")
        y += 74

    tz = now.tzinfo
    coming = []
    for day in (now.date(), now.date() + timedelta(days=1)):
        for ev in on_day(events, day, tz):
            if not ev.all_day and ev.end > now and ev.start.date() == day and len(coming) < 3:
                coming.append(ev)           # listed under the day it starts on, so never twice
    for ev in coming:
        if ev.start <= now:
            lead = "Now"
        else:
            lead = fmt_time(ev.start) if ev.start.date() == now.date() else f"Tomorrow {fmt_time(ev.start)}"
        color = _mix(CAL_COLORS[ev.cal % len(CAL_COLORS)], NIGHT_INK, 0.35)
        lead_f, title_f = font(28, True), font(28)
        lw = d.textlength(lead, font=lead_f)
        title = fit(d, ev.title, title_f, 860 - lw - 18)
        x = cx - (lw + 18 + d.textlength(title, font=title_f)) / 2
        d.text((x, y), lead, font=lead_f, fill=color, anchor="lm")
        d.text((x + lw + 18, y), title, font=title_f, fill=NIGHT_MUTED, anchor="lm")
        y += 46
    return img
