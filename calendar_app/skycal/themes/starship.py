"""Starship: a 1980s-90s TV starship console look. Pure black, the screen framed by thick
pastel "elbow" bars (a bar that sweeps into a sidebar round a large inner curve), pill
buttons with right-aligned condensed capitals, small number codes in the bar segments.

Labels the theme writes itself are drawn in Antonio (tall, condensed, capitals); text from the
calendar or the weather service is drawn in the system font (it has every glyph) unless
Antonio covers every letter of it. Curves are drawn from small antialiased corner masks that
are made once; the bars and codes that never change are drawn once per screen and copied.
"""
from __future__ import annotations

import itertools
from datetime import date, datetime, time, timedelta
from functools import cache, lru_cache

from PIL import Image, ImageDraw

from .. import render as R
from ..feed import Event, on_day
from ..render import CLOSE, H, NAV, W, _mix, _temp, _text, fit, fmt_time, fmt_until, fmt_when, wrap
from ..weather import Weather, describe
from .original import Original

BLACK = (0, 0, 0)
ORANGE = (255, 153, 0)
PEACH = (255, 160, 110)
TAN = (214, 166, 120)
GOLD = (255, 204, 110)
LAVENDER = (204, 153, 204)
PERI = (150, 150, 255)
SKY = (153, 204, 255)
SALMON = (220, 110, 100)
INK = (240, 236, 228)
MUTED = (160, 150, 180)

S = 60                     # width of the sidebar
CX0 = 72                   # left edge of the day columns
EDGES = [CX0 + round(i * (W - CX0) / 7) for i in range(8)]
HEAD_Y = (106, 146)        # the day buttons
ALERT_TOP = 606            # where the alert elbow starts up the sidebar

# ---- antialiased shapes ----

_SS = 4


@cache
def _quarter(r: int, corner: str, inner: bool = False) -> Image.Image:
    """An r x r mask of a quarter disc for the `corner` ("tl", "tr", "bl", "br") of a shape;
    `inner`: the fillet that fills the inside corner of an elbow instead."""
    big = Image.new("L", (r * _SS, r * _SS), 0)
    ImageDraw.Draw(big).ellipse((0, 0, 2 * r * _SS - 1, 2 * r * _SS - 1), fill=255)
    m = big.resize((r, r), Image.Resampling.BOX)
    if inner:
        m = m.point(lambda v: 255 - v)
    if corner[1] == "r":
        m = m.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if corner[0] == "b":
        m = m.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return m


def _rect(d: ImageDraw.ImageDraw, x0, y0, x1, y1, fill) -> None:
    """A rectangle covering x0 <= x < x1, y0 <= y < y1 (nothing when empty)."""
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    if x1 > x0 and y1 > y0:
        d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=fill)


def _rrect(d: ImageDraw.ImageDraw, box, fill, r: int, corners: str = "tl tr bl br") -> None:
    """A rectangle (half-open box) with the named corners rounded, antialiased."""
    x0, y0, x1, y1 = (round(v) for v in box)
    r = int(min(r, (x1 - x0) // 2, (y1 - y0) // 2))
    if r <= 0:
        _rect(d, x0, y0, x1, y1, fill)
        return
    _rect(d, x0, y0 + r, x1, y1 - r, fill)
    _rect(d, x0 + r, y0, x1 - r, y0 + r, fill)
    _rect(d, x0 + r, y1 - r, x1 - r, y1, fill)
    for c, cx, cy in (("tl", x0, y0), ("tr", x1 - r, y0), ("bl", x0, y1 - r), ("br", x1 - r, y1 - r)):
        if c in corners:
            d.bitmap((cx, cy), _quarter(r, c), fill=fill)
        else:
            _rect(d, cx, cy, cx + r, cy + r, fill)


def _pill(d, box, fill) -> None:
    _rrect(d, box, fill, 999)


def _elbow(d: ImageDraw.ImageDraw, box, side: int, arm: int, ro: int, ri: int, fill, corner: str) -> None:
    """A sidebar `side` wide and a bar `arm` thick meeting at `corner` of `box` (half-open),
    with an outer radius `ro` (at most `side`) and an inner fillet `ri`."""
    X0, Y0, X1, Y1 = box
    w, h = X1 - X0, Y1 - Y0
    fx, fy = corner[1] == "r", corner[0] == "b"

    def rect(a, b, c, e):
        gx0, gx1 = (X1 - c, X1 - a) if fx else (X0 + a, X0 + c)
        gy0, gy1 = (Y1 - e, Y1 - b) if fy else (Y0 + b, Y0 + e)
        _rect(d, gx0, gy0, gx1, gy1, fill)

    def blit(a, b, s, inner):
        d.bitmap((X1 - a - s if fx else X0 + a, Y1 - b - s if fy else Y0 + b), _quarter(s, corner, inner), fill=fill)

    rect(0, ro, side, h)
    rect(ro, 0, side, ro)
    rect(side, 0, w, arm)
    blit(0, 0, ro, False)
    if ri:
        blit(side, arm, ri, True)


# ---- fonts and colours ----

def A(size: int, weight: int = 500):
    """The console's condensed capitals: Antonio from 28 px up; below that its hinting spaces
    letters unevenly, so the sturdier Barlow Condensed is used there."""
    if size >= 28:
        return R.font_file("Antonio-VF.ttf", size, weight)
    return R.font_file("BarlowCondensed-Bold.ttf" if weight >= 650 else "BarlowCondensed-SemiBold.ttf", size)


@lru_cache(maxsize=8)
def _notdef(path: str) -> bytes:
    return bytes(R.font_file(path.rsplit("/", 1)[-1], 40).getmask("\uffff"))


@lru_cache(maxsize=8192)
def _covered(path: str, ch: str) -> bool:
    return ch.isspace() or bytes(R.font_file(path.rsplit("/", 1)[-1], 40).getmask(ch)) != _notdef(path)


def label_font(text: str, size: int, weight: int = 500):
    """A(size, weight) when it has every letter of `text`, else the system font at a matching size."""
    f = A(size, weight)
    if all(_covered(str(f.path), c) for c in text):
        return f
    return R.font(max(10, round(size * 0.8)), weight >= 600)


def _lum(c: tuple) -> float:
    r, g, b = (v / 255 for v in c[:3])
    return 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2


def _lift(c: tuple, least: float) -> tuple:
    """`c` mixed toward white just enough to reach luminance `least` (its hue kept)."""
    out = c
    for k in range(1, 11):
        if _lum(out) >= least:
            break
        out = _mix(c, (255, 255, 255), 0.08 * k)
    return out


def soft(c: tuple) -> tuple:
    """An event colour as the console shows it on black: a touch paler, and lifted if dark."""
    return _lift(_mix(c, (255, 255, 255), 0.12), 0.1)


def ink_on(fill: tuple) -> tuple:
    return BLACK if _lum(fill) > 0.2 else (250, 246, 240)


def _code(*parts: int) -> str:
    return "-".join(f"{p:02d}" for p in parts)


class Starship(Original):
    name = "starship"
    label = "Starship (TNG-inspired)"
    GEAR_INK = BLACK             # the gear is drawn like the style's button labels

    DAYHEAD_H = 72               # day buttons: events start at 76 + 72 + 6
    ALERT_H = 44

    BG = BLACK
    INK = INK
    MUTED = MUTED
    FAINT = (84, 80, 100)
    RULE = (44, 42, 58)
    TODAY_BG = (26, 18, 6)       # behind today's column
    ACCENT = ORANGE
    WARN = (255, 120, 90)
    CAL_COLORS = [PERI, ORANGE, LAVENDER, (110, 200, 150), SALMON, SKY]
    NIGHT_BG = BLACK
    NIGHT_INK = (190, 132, 72)
    NIGHT_MUTED = (120, 106, 140)
    ICON_COLORS = ((255, 175, 60), (150, 150, 205), (170, 170, 190), (110, 150, 255), (150, 190, 255), (255, 195, 70))
    TEMP_LINE, TEMP_DOT = PEACH, ORANGE
    SEVERITY_COLORS = {"Extreme": (225, 45, 80), "Severe": (235, 65, 50), "Moderate": (255, 140, 40),
                       "Minor": (240, 190, 50), "Unknown": (150, 150, 170)}
    DIM = R.dim_table(BLACK, 0.62)

    TITLE_SIZE, LABEL_SIZE, LINE_H, LABEL_H = 18, 18, 22, 21

    _frames: dict = {}

    # ---- the static frame ----

    def _frame(self, kind: str) -> Image.Image:
        if kind not in self._frames:
            img = Image.new("RGB", (W, H), BLACK)
            d = ImageDraw.Draw(img)
            if kind in ("week", "week_alerts", "weather"):
                self._upper(d, 600 if kind != "weather" else 470, 824 if kind != "weather" else 700)
                self._lower(d, kind)
            self._frames[kind] = img
        return self._frames[kind].copy()

    def _upper(self, d, arm_end: int, bar_end: int) -> None:
        """The elbow in the top left that the title sits in."""
        _elbow(d, (0, 0, arm_end, 72), S, 12, 30, 10, PEACH, "bl")
        mid = arm_end + 6 + (bar_end - arm_end - 12) // 2
        _rect(d, arm_end + 6, 60, mid, 72, LAVENDER)
        _rrect(d, (mid + 6, 60, bar_end, 72), PERI, 6, "tr br")

    def _lower(self, d, kind: str) -> None:
        """The elbow over the columns and the sidebar with its segments."""
        _elbow(d, (0, 80, EDGES[3] - 4, 300), S, 20, 40, 14, ORANGE, "tl")
        segs = [(EDGES[3] + 2, EDGES[5] - 4, TAN, "21-4471"), (EDGES[5] + 2, EDGES[6] - 4, LAVENDER, "06"),
                (EDGES[6] + 2, W, PERI, "88-109")]
        for i, (a, b, color, code) in enumerate(segs):
            if i == len(segs) - 1:
                _rrect(d, (a, 80, b, 100), color, 10, "tr br")
            else:
                _rect(d, a, 80, b, 100, color)
            _text(d, (b - 12, 90), code, font=A(14, 600), fill=BLACK, anchor="rm")
        side = [(306, 420, LAVENDER, "04-127"), (426, 468, SALMON, "88"), (474, 600, TAN, "19-3301")]
        if kind == "week":
            side.append((606, H, PERI, "2216"))
        if kind == "weather":
            side.append((606, H, PERI, "2216"))
        _text(d, (S - 8, 286), "03-1185", font=A(15, 600), fill=BLACK, anchor="rs")
        for y0, y1, color, code in side:
            _rect(d, 0, y0, S, y1, color)
            _text(d, (S - 8, min(y1, H) - 10), code, font=A(15, 600), fill=BLACK, anchor="rs")

    def canvas(self, screen: str) -> Image.Image:
        if screen == "saver":
            return Image.new("RGB", (W, H), BLACK)
        return self._frame("weather" if screen == "weather" else "week")

    # ---- buttons ----

    def button(self, d, box, label: str, size: int, lift: int = 0, color: tuple = PERI) -> None:
        x0, y0, x1, y1 = box
        _pill(d, (x0, y0, x1 + 1, y1 + 1), color)
        cy = (y0 + y1) // 2
        if label in ("‹", "›"):        # a chevron from shapes: crisp at any size
            cx, s = (x0 + x1) // 2, 9
            k = -1 if label == "‹" else 1
            d.line([(cx - k * s * 0.6, cy - s), (cx + k * s * 0.6, cy), (cx - k * s * 0.6, cy + s)], fill=BLACK, width=5,
                   joint="curve")
            return
        _text(d, (x1 - (y1 - y0) // 2 - 2, y1 - 9), label.upper(), font=A(size, 500), fill=BLACK, anchor="rs")

    def weather_button(self, d, hour) -> None:
        x0, y0, x1, y1 = NAV["weather"]
        cy = (y0 + y1) // 2
        _rrect(d, (x0, y0, x0 + 18, y1 + 1), LAVENDER, 26, "tl bl")
        _rrect(d, (x1 - 17, y0, x1 + 1, y1 + 1), LAVENDER, 26, "tr br")
        if hour:
            self.icon(d, x0 + 54, cy, 38, describe(hour.code)[0], hour.is_day, BLACK)
            _text(d, (x1 - 28, cy + 1), _temp(hour.temp), font=A(34, 500), fill=PEACH, anchor="rm")
        else:
            _text(d, (x1 - 28, cy + 1), "WEATHER", font=A(24, 600), fill=PEACH, anchor="rm")

    # ---- week ----

    def header(self, d, start: date, now: datetime, note: str, stale: bool, weather: Weather | None) -> None:
        _text(d, (S - 8, 10), f"{start.isocalendar()[1]:02d}", font=A(22, 700), fill=BLACK, anchor="ra")
        right = NAV["settings"][0] - 14
        clock = fmt_time(now).upper()
        cf = A(34 if note else 40, 500)
        clock_w = d.textlength(clock, font=cf)
        tf = A(40, 600)
        title = fit(d, self.week_title(start).upper(), tf, right - 78 - clock_w - 30)
        _text(d, (78, 31), title, font=tf, fill=ORANGE, anchor="lm")
        if note:
            room = right - (78 + d.textlength(title, font=tf) + 24)
            nf = A(17, 500)
            _text(d, (right, 22), clock, font=cf, fill=GOLD, anchor="rm")
            _text(d, (right, 50), fit(d, note.upper(), nf, room), font=nf, fill=self.WARN if stale else MUTED, anchor="rm")
        else:
            _text(d, (right, 31), clock, font=cf, fill=GOLD, anchor="rm")
        self.settings_button(d)
        self.weather_button(d, weather.at(now) if weather else None)
        self.button(d, NAV["prev"], "‹", 36)
        self.button(d, NAV["today"], "Today", 28, color=ORANGE)
        self.button(d, NAV["next"], "›", 36)

    def header_rule(self, d) -> None:
        pass

    def grid(self, d, edges, bottom) -> None:
        pass

    def day_head(self, d, x0: int, x1: int, day: date, today: date) -> None:
        y0, y1 = HEAD_Y
        if day == today:
            fill, ink = ORANGE, BLACK
        elif day < today:
            fill, ink = (46, 42, 62), (150, 142, 180)
        else:
            fill, ink = (LAVENDER if day.weekday() >= 5 else PERI), BLACK
        _pill(d, (x0 + 5, y0, x1 - 5, y1), fill)
        num = str(day.day)
        nf = A(30, 700)
        right = x1 - 5 - 18
        _text(d, (right, y1 - 9), num, font=nf, fill=ink, anchor="rs")
        _text(d, (right - d.textlength(num, font=nf) - 8, y1 - 9), f"{day:%a}".upper(), font=A(20, 500), fill=ink,
              anchor="rs")

    def plate_height(self, label: str, lines: list) -> int:
        return 8 + (self.LABEL_H if label else 0) + self.LINE_H * len(lines) + 6

    def plate(self, d, box, ev: Event, label: str, lines: list, over: bool, current: bool, back: tuple) -> None:
        left, y, right, y1 = box
        color = soft(self.color(ev))
        tf = self.f(self.TITLE_SIZE, True)
        if ev.all_day:
            fill = _mix(color, back, 0.6) if over else color
            ink = (150, 148, 156) if over else ink_on(fill)
            _rrect(d, box, fill, 18 if len(lines) == 1 else 14)
            tx = left + 14
        else:
            cap = _mix(color, back, 0.5) if over else color
            body = (16, 18, 24) if over else _mix(color, BLACK, 0.55 if current else 0.8)
            _rrect(d, box, cap, 10)
            if current:
                _rrect(d, (left + 8, y + 2, right - 2, y1 - 2), body, 8, "tr br")
            else:
                _rrect(d, (left + 8, y, right, y1), body, 10, "tr br")
            ink = (128, 126, 136) if over else INK
            tx = left + 16
        ty = y + 6
        if label:
            sub = (100, 96, 112) if over else _lift(color, 0.35)
            _text(d, (tx, ty), label.upper(), font=A(self.LABEL_SIZE, 700 if current else 500), fill=sub)
            ty += self.LABEL_H
        for line in lines:
            _text(d, (tx, ty), line, font=tf, fill=ink)
            ty += self.LINE_H

    def more(self, d, cx: int, bottom: int, count: int) -> None:
        x0, x1 = cx - 80, cx + 80
        _pill(d, (x0, bottom - 28, x1, bottom - 4), LAVENDER)
        _text(d, (x1 - 14, bottom - 16), f"+{count} MORE", font=A(17, 600), fill=BLACK, anchor="rm")

    def day(self, d, x0: int, x1: int, day: date, events: list[Event], now: datetime, hits: list | None,
            bottom: int = H) -> None:
        today = now.date()
        top = self.HEADER_H + self.DAYHEAD_H
        back = BLACK
        if day == today:
            back = self.TODAY_BG
            _rect(d, x0 + 1, top, x1 - 1, bottom, back)
        self.day_head(d, x0, x1, day, today)
        if hits is not None and events:
            hits.append((x0, self.HEADER_H, x1, top, day))
        y = top + 6
        left, right = x0 + 5, x1 - 5
        text_w = right - left - 16 - 8
        tf = self.f(self.TITLE_SIZE, True)
        for i, ev in enumerate(events):
            over = ev.end <= now and (ev.all_day or ev.start < now)
            label, current = self.event_label(ev, day, now)
            lines = wrap(d, ev.title, tf, text_w, 3)
            h = self.plate_height(label, lines)
            if y + h > bottom - 32 and i < len(events) - 1 or y + h > bottom - 4:
                self.more(d, (x0 + x1) // 2, bottom, len(events) - i)
                if hits is not None:
                    hits.append((x0, y, x1, bottom, day))
                break
            self.plate(d, (left, y, right, y + h), ev, label, lines, over, current, back)
            if hits is not None:
                hits.append((left, y, right, y + h, ev))
            y += h + 6

    def render_week(self, start: date, events: list[Event], now: datetime, note: str, stale: bool, hits: list | None,
                    weather: Weather | None, message: str, pause, alerts: list) -> Image.Image:
        tz = now.tzinfo
        img = self._frame("week_alerts" if alerts else "week")
        d = ImageDraw.Draw(img)
        self.header(d, start, now, note, stale, weather)
        bottom = H - self.ALERT_H if alerts else H
        for i in range(7):
            day = start + timedelta(days=i)
            self.day(d, EDGES[i], EDGES[i + 1], day, on_day(events, day, tz), now, hits, bottom - 8 if alerts else bottom)
            if pause:
                pause()
        if alerts:
            self.alert_band(d, (0, bottom, W, H), alerts, now)
            if hits is not None:
                hits.append((0, bottom, W, H, "alerts"))
        if message:
            self.message_box(d, message)
        return img

    def message_box(self, d, message: str) -> None:
        f = self.f(24)
        lines = wrap(d, message, f, 640, 4)
        cx = (CX0 + W) // 2
        half = 40 + 17 * len(lines)
        box = (cx - 400, 470 - half, cx + 400, 470 + half)
        _rrect(d, box, BLACK, 28, "tl bl")
        self.card_strip(d, box, ORANGE)
        for i, line in enumerate(lines):
            _text(d, (cx + 20, 470 - 17 * (len(lines) - 1) + 34 * i), line, font=f, fill=INK, anchor="mm")

    def alert_band(self, d, box: tuple, alerts: list, now: datetime) -> None:
        x0, y0, x1, y1 = box
        color = self.alert_color(alerts[0].severity)
        cy = (y0 + y1) // 2
        split = x1 - 196
        _elbow(d, (x0, ALERT_TOP, split, y1), S, y1 - y0, 40, 14, color, "bl")
        _rrect(d, (split + 6, y0, x1, y1), color, 22, "tr br")
        sev = str(alerts[0].severity or "").upper()
        _text(d, (S - 8, ALERT_TOP + 10), fit(d, sev, A(15, 600), S - 12), font=A(15, 600), fill=BLACK, anchor="ra")
        R.warn_icon(d, S + 34, cy, 28, BLACK, color)
        text = self.alert_text(alerts, now).upper()
        f = label_font(text, 28, 600)
        _text(d, (S + 62, cy + 1), fit(d, text, f, split - 20 - (S + 62)), font=f, fill=BLACK, anchor="lm")
        _text(d, (x1 - 24, cy + 1), "TAP FOR DETAILS", font=A(20, 600), fill=BLACK, anchor="rm")

    # ---- cards over the week ----

    def card(self, base: Image.Image, height: int, width: int = 820):
        img = base.point(self.DIM)
        d = ImageDraw.Draw(img)
        height = min(height, H - 40)
        x0 = (CX0 + W - width) // 2
        box = (x0, (H - height) // 2, x0 + width, (H + height) // 2)
        _rrect(d, box, BLACK, 28, "tl bl")
        _rect(d, box[2] - 10, box[1], box[2], box[3], BLACK)
        return img, d, box

    def card_strip(self, d, box: tuple, color: tuple) -> None:
        """The bracket of elbows down the left of a card, in `color`."""
        x0, y0, x1, y1 = box
        split = y0 + max(60, int((y1 - y0) * 0.6))
        dark = _mix(color, BLACK, 0.4)
        _elbow(d, (x0, y0, x1 - 156, split), 30, 18, 28, 12, color, "tl")
        _rrect(d, (x1 - 150, y0, x1, y0 + 18), color, 9, "tr br")
        _text(d, (x1 - 14, y0 + 9), _code(y1 - y0, (x0 * 7) % 97), font=A(13, 600), fill=ink_on(color), anchor="rm")
        _elbow(d, (x0, split + 6, x1 - 226, y1), 30, 10, 24, 10, dark, "bl")
        _rrect(d, (x1 - 220, y1 - 10, x1, y1), dark, 5, "tr br")

    def card_hint(self, d, box: tuple, text: str) -> None:
        x1, y1 = box[2], box[3]
        _text(d, (x1 - 24, y1 - 30), text.upper(), font=A(18, 500), fill=MUTED, anchor="rm")

    def _tag(self, d, x: int, y: int, label: str, color: tuple = LAVENDER) -> None:
        f = A(16, 600)
        w = int(d.textlength(label, font=f)) + 40
        _pill(d, (x, y, x + w, y + 22), color)
        _text(d, (x + w - 12, y + 11), label, font=f, fill=BLACK, anchor="rm")

    def render_detail(self, base: Image.Image, ev: Event, now: datetime) -> Image.Image:
        probe = ImageDraw.Draw(base)
        text_w = 820 - 60 - 40
        title_f, when_f, body_f = self.f(34, True), A(28, 500), self.f(21)
        title = wrap(probe, ev.title, title_f, text_w, 3)
        when_txt = fmt_when(ev).upper()
        when_f = label_font(when_txt, 28, 500)
        when = wrap(probe, when_txt, when_f, text_w, 2)
        until = fmt_until(ev, now)
        where = wrap(probe, ev.location, body_f, text_w, 3) if ev.location else []
        fixed = 38 + 42 * len(title) + 12 + 34 * len(when) + (32 if until else 0) + (20 + 30 + 28 * len(where) if where else 0) + 66
        room = (H - 40 - fixed - 50) // 28
        notes = wrap(probe, ev.description, body_f, text_w, max(1, min(12, room))) if ev.description else []
        img, d, box = self.card(base, fixed + (20 + 30 + 28 * len(notes) if notes else 0))
        x0, y0 = box[0], box[1]
        color = soft(self.color(ev))
        self.card_strip(d, box, color)
        x, y = x0 + 60, y0 + 38
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=INK)
            y += 42
        y += 12
        for line in when:
            _text(d, (x, y), line, font=when_f, fill=GOLD)
            y += 34
        if until:
            _text(d, (x, y + 2), until.upper(), font=A(21, 700), fill=MUTED if until == "Ended" else _lift(color, 0.35))
            y += 32
        for label, lines in (("WHERE", where), ("NOTES", notes)):
            if lines:
                y += 20
                self._tag(d, x, y, label)
                y += 30
                for line in lines:
                    _text(d, (x, y), line, font=body_f, fill=INK)
                    y += 28
        self.card_hint(d, box, "Tap anywhere to close")
        return img

    def render_day(self, base: Image.Image, day: date, events: list[Event], now: datetime, hits: list | None,
                   first: int) -> Image.Image:
        tz = now.tzinfo
        everything = on_day(events, day, tz)
        first = first if 0 < first < len(everything) else 0
        todays = everything[first:]
        row_h, rows = 46, min(len(todays), R.DAY_ROWS)
        img, d, box = self.card(base, 92 + row_h * max(rows, 1) + 60)
        x0, y0, x1 = box[0], box[1], box[2]
        self.card_strip(d, box, TAN)
        _text(d, (x0 + 60, y0 + 54), f"{day:%A}, {day:%B} {day.day}".upper(), font=A(34, 600), fill=ORANGE, anchor="lm")
        count = f"{len(everything)} EVENT" + ("" if len(everything) == 1 else "S")
        _text(d, (x1 - 36, y0 + 56), count, font=A(20, 500), fill=LAVENDER, anchor="rm")
        lo = datetime.combine(day, time.min, tz)
        y = y0 + 92
        tf, sf = self.f(21, True), A(21, 500)
        if not todays:
            _text(d, (x0 + 60, y + row_h // 2), "NOTHING ON THIS DAY", font=A(22, 500), fill=MUTED, anchor="lm")
        for i, ev in enumerate(todays[:rows]):
            if i == rows - 1 and len(todays) > rows:
                _text(d, (x0 + 60, y + row_h // 2), f"SHOW {len(todays) - i} MORE", font=A(22, 600), fill=ORANGE, anchor="lm")
                if hits is not None:
                    hits.append((x0, y, x1, y + row_h, first + i))
                break
            color = soft(self.color(ev))
            over = ev.end <= now and (ev.all_day or ev.start < now)
            if i:
                _rect(d, x0 + 60, y, x1 - 30, y + 1, self.RULE)
            _pill(d, (x0 + 60, y + 9, x0 + 68, y + row_h - 9), _mix(color, BLACK, 0.5) if over else color)
            _text(d, (x0 + 80, y + row_h // 2 + 1), self.span(ev, lo).upper(), font=sf,
                  fill=self.FAINT if over else _lift(color, 0.35), anchor="lm")
            _text(d, (x0 + 250, y + row_h // 2), fit(d, ev.title, tf, x1 - 36 - (x0 + 250)), font=tf,
                  fill=(128, 126, 136) if over else INK, anchor="lm")
            if hits is not None:
                hits.append((x0, y, x1, y + row_h, ev))
            y += row_h
        self.card_hint(d, box, "Tap an event for details, or outside to close")
        return img

    def render_alerts(self, base: Image.Image, alerts: list, now: datetime, hits: list | None, index: int) -> Image.Image:
        a = alerts[index]
        width = 900
        probe = ImageDraw.Draw(base)
        text_w = width - 60 - 40
        title_txt = a.event.upper()
        title_f, when_f, body_f = label_font(title_txt, 38, 600), A(28, 500), self.f(19)
        color = self.alert_color(a.severity)
        title = wrap(probe, title_txt, title_f, text_w, 2)
        when = self.alert_when(a, now).upper()
        area = wrap(probe, a.area, body_f, text_w, 2) if a.area else []
        more = len(alerts) > 1
        fixed = 38 + 44 * len(title) + 8 + (38 if when else 0) + 30 + (24 * len(area) + 10 if area else 0) + 66 + (52 if more else 0)
        room = max(2, (H - 40 - fixed) // 26 - 2)
        what = wrap(probe, a.description or a.headline, body_f, text_w, max(1, room - (4 if a.instruction else 0)))
        todo = wrap(probe, a.instruction, body_f, text_w, max(1, room - len(what) - 2)) if a.instruction and room - len(what) >= 3 else []
        body = 30 + 26 * len(what) + (16 + 30 + 26 * len(todo) if todo else 0)
        img, d, box = self.card(base, fixed + body, width)
        x0, y0, x1, y1 = box
        self.card_strip(d, box, color)
        x, y = x0 + 60, y0 + 38
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=_lift(color, 0.3))
            y += 44
        y += 8
        if when:
            _text(d, (x, y), fit(d, when, when_f, text_w), font=when_f, fill=GOLD)
            y += 38
        tag = f"{a.severity.upper()}  ·  {a.sender.upper()}" if a.sender else a.severity.upper()
        tag_f = label_font(tag, 18, 600)
        _text(d, (x, y + 2), fit(d, tag, tag_f, text_w), font=tag_f, fill=color)
        y += 30
        for line in area:
            _text(d, (x, y), line, font=body_f, fill=MUTED)
            y += 24
        y += 10 if area else 0
        for label, lines in (("WHAT", what), ("WHAT TO DO", todo)):
            if lines:
                y += 16 if label != "WHAT" else 0
                self._tag(d, x, y, label)
                y += 30
                for line in lines:
                    _text(d, (x, y), line, font=body_f, fill=INK)
                    y += 26
        if more:
            ry = y1 - 66 - 48
            _rect(d, x0 + 60, ry, x1 - 30, ry + 1, self.RULE)
            nxt = (index + 1) % len(alerts)
            text = f"NEXT ALERT: {alerts[nxt].event.upper()}  ({nxt + 1} OF {len(alerts)})"
            nf = label_font(text, 22, 600)
            _text(d, (x, ry + 25), fit(d, text, nf, text_w - 60), font=nf, fill=ORANGE, anchor="lm")
            cx, cy, s = x1 - 48, ry + 25, 8
            d.line([(cx - s * 0.6, cy - s), (cx + s * 0.6, cy), (cx - s * 0.6, cy + s)], fill=ORANGE, width=4, joint="curve")
            if hits is not None:
                hits.append((x0, ry, x1, ry + 48, nxt))
        self.card_hint(d, box, "Tap anywhere else to close")
        return img

    # ---- weather ----

    def weather_day(self, d, top: int, name: str, day: date, weather: Weather, now: datetime,
                    lo_t: float, hi_t: float) -> None:
        hours = [h for h in weather.on(day) if h.time.hour >= min(6, now.hour if day == now.date() else 6)]
        left, right = CX0 + 4, W - 12
        nf = A(34, 600)
        _text(d, (left + 4, top + 46), name.upper(), font=nf, fill=ORANGE, anchor="ls")
        name_w = d.textlength(name.upper(), font=nf)
        _text(d, (left + 4 + name_w + 14, top + 46), f"{day:%A}, {day:%B} {day.day}".upper(), font=A(22, 500),
              fill=LAVENDER, anchor="ls")
        info = weather.days.get(day)
        if info:
            parts = [describe(info.code)[1], f"High {_temp(info.high)}", f"Low {_temp(info.low)}"]
            if info.sunrise and info.sunset:
                parts += [f"Sunrise {fmt_time(info.sunrise)}", f"Sunset {fmt_time(info.sunset)}"]
            text = "    ".join(parts).upper()
            _text(d, (right, top + 46), text, font=label_font(text, 22, 500), fill=TAN, anchor="rs")
        if not hours:
            _text(d, ((left + right) // 2, top + 190), "NO FORECAST FOR THESE HOURS", font=A(24, 500), fill=MUTED,
                  anchor="mm")
            return
        cw = (right - left) / len(hours)
        span = max(hi_t - lo_t, 1.0)
        band_top, band_h = top + 172, 66
        points = []
        for i, h in enumerate(hours):
            cx = left + cw * (i + 0.5)
            past = h.time + timedelta(hours=1) <= now
            current = h.time <= now < h.time + timedelta(hours=1)
            back = BLACK
            if current:
                back = self.TODAY_BG
                bx = (cx - cw / 2 + 2, top + 60, cx + cw / 2 - 2, top + 318)
                _rrect(d, bx, ORANGE, 14)
                _rrect(d, (bx[0] + 2, bx[1] + 2, bx[2] - 2, bx[3] - 2), back, 12)
            _text(d, (cx, top + 82), "NOW" if current else fmt_time(h.time).upper(), font=A(19, 700 if current else 500),
                  fill=ORANGE if current else (self.FAINT if past else MUTED), anchor="mm")
            self.icon(d, cx, top + 122, 44, describe(h.code)[0], h.is_day, back, fade=past)
            py = band_top + band_h - (h.temp - lo_t) / span * band_h
            points.append((cx, py, h, past))
            if h.precip is not None and h.precip >= 10 and not past:
                tone = SKY if h.precip >= 30 else MUTED
                d.polygon([(cx, top + 262), (cx - 5, top + 271), (cx + 5, top + 271)], fill=tone)
                d.ellipse((cx - 5, top + 266, cx + 5, top + 276), fill=tone)
                _text(d, (cx, top + 296), f"{h.precip}%", font=A(19, 700 if h.precip >= 50 else 500), fill=tone, anchor="mm")
        for (ax, ay, _, a_past), (bx, by, _, _) in itertools.pairwise(points):
            d.line((ax, ay, bx, by), fill=self.RULE if a_past else self.TEMP_LINE, width=3)
        for cx, py, h, past in points:
            d.ellipse((cx - 5, py - 5, cx + 5, py + 5), fill=self.FAINT if past else self.TEMP_DOT)
            _text(d, (cx, py - 20), _temp(h.temp), font=A(22, 600), fill=self.FAINT if past else INK, anchor="mm")

    def alert_pill(self, d, right: int, cy: int, alerts: list) -> int:
        a = alerts[0]
        text = (a.event + (f" +{len(alerts) - 1}" if len(alerts) > 1 else "")).upper()
        f = label_font(text, 22, 600)
        tw = min(d.textlength(text, font=f), 300)
        x0 = int(right - tw - 76)
        color = self.alert_color(a.severity)
        _pill(d, (x0, cy - 26, right, cy + 27), color)
        R.warn_icon(d, x0 + 32, cy, 24, BLACK, color)
        _text(d, (right - 20, cy + 1), fit(d, text, f, 300), font=f, fill=BLACK, anchor="rm")
        return x0

    def render_weather(self, weather: Weather | None, now: datetime, message: str, alerts: list) -> Image.Image:
        img = self.canvas("weather")
        d = ImageDraw.Draw(img)
        _text(d, (S - 8, 10), f"{now.timetuple().tm_yday:03d}", font=A(22, 700), fill=BLACK, anchor="ra")
        tf = A(40, 600)
        _text(d, (78, 31), "WEATHER", font=tf, fill=ORANGE, anchor="lm")
        self.button(d, CLOSE, "Back", 28, color=ORANGE)
        right = CLOSE[0] - 14
        if alerts:
            right = self.alert_pill(d, right, (CLOSE[1] + CLOSE[3]) // 2, alerts) - 14
        if weather is None:
            f = self.f(26)
            for i, line in enumerate(wrap(d, message or "No forecast yet", f, 900, 4)):
                _text(d, ((CX0 + W) // 2, 300 + i * 40), line, font=f, fill=MUTED, anchor="mm")
            return img
        _rect(d, CX0, 434, EDGES[4] - 4, 444, TAN)            # between today and tomorrow
        _rect(d, EDGES[4] + 2, 434, EDGES[6] - 4, 444, LAVENDER)
        _rrect(d, (EDGES[6] + 2, 434, W, 444), PERI, 5, "tr br")
        sub = f"{weather.place}, as of {fmt_time(weather.fetched)}" if weather.place else f"As of {fmt_time(weather.fetched)}"
        if now - weather.fetched > timedelta(hours=2):
            sub += f" on {weather.fetched:%b} {weather.fetched.day} (no newer forecast could be fetched)"
        x = 78 + d.textlength("WEATHER", font=tf) + 20
        sf = self.f(19)
        _text(d, (x, 33), fit(d, sub, sf, right - x), font=sf, fill=MUTED, anchor="lm")
        today = now.date()
        shown = [h.temp for day in (today, today + timedelta(days=1)) for h in weather.on(day)]
        lo_t, hi_t = (min(shown), max(shown)) if shown else (0.0, 1.0)
        self.weather_day(d, 100, "Today", today, weather, now, lo_t, hi_t)
        self.weather_day(d, 452, "Tomorrow", today + timedelta(days=1), weather, now, lo_t, hi_t)
        return img

    # ---- screensaver ----

    def _bracket(self, d, x0: int, y0: int, x1: int, y1: int, color: tuple) -> None:
        """A dim pair of elbows either side of the screensaver's block."""
        mid = (y0 + y1) // 2
        for corner_t, corner_b, bx0, bx1 in (("tl", "bl", x0, x0 + 90), ("tr", "br", x1 - 90, x1)):
            _elbow(d, (bx0, y0, bx1, mid - 3), 14, 8, 14, 8, color, corner_t)
            _elbow(d, (bx0, mid + 3, bx1, y1), 14, 8, 14, 8, color, corner_b)

    def saver_alert(self, d, cx: float, y: float, alerts: list, now: datetime) -> float:
        color = _mix(self.alert_color(alerts[0].severity), BLACK, 0.3)
        text = self.alert_text(alerts, now).upper()
        f = label_font(text, 32, 600)
        text = fit(d, text, f, 780)
        tw = d.textlength(text, font=f)
        R.warn_icon(d, cx - tw / 2 - 24, y, 30, color, BLACK)
        _text(d, (cx + 18, y), text, font=f, fill=color, anchor="mm")
        return y + 58

    def render_saver(self, now: datetime, events: list[Event], weather: Weather | None, alerts: list) -> Image.Image:
        img = self.canvas("saver")
        d = ImageDraw.Draw(img)
        cx = W // 2 + ((now.minute * 7 + now.hour * 3) % 21 - 10) * 12
        top = 96 + ((now.minute * 11 + now.hour) % 9 - 4) * 12 - (30 if alerts else 0)

        clock, suffix = f"{now.hour % 12 or 12}:{now.minute:02d}", "AM" if now.hour < 12 else "PM"
        clock_f, suffix_f = A(210, 300), A(54, 500)
        cw, sw = d.textlength(clock, font=clock_f), d.textlength(suffix, font=suffix_f)
        x = cx - (cw + 16 + sw) / 2
        _text(d, (x, top + 196), clock, font=clock_f, fill=self.NIGHT_INK, anchor="ls")
        _text(d, (x + cw + 16, top + 196), suffix, font=suffix_f, fill=self.NIGHT_MUTED, anchor="ls")
        _text(d, (cx, top + 262), f"{now:%A}, {now:%B} {now.day}".upper(), font=A(46, 400), fill=(150, 120, 90),
              anchor="mm")

        y = top + 340
        hour = weather.at(now) if weather else None
        if hour:
            text = f"{_temp(hour.temp)}  {describe(hour.code)[1]}"
            info = weather.days.get(now.date())
            if info:
                text += f"    High {_temp(info.high)}  Low {_temp(info.low)}"
            text = text.upper()
            f = label_font(text, 34, 400)
            tw = d.textlength(text, font=f)
            self.icon(d, cx - tw / 2 - 12, y, 52, describe(hour.code)[0], hour.is_day, BLACK, fade=True)
            _text(d, (cx + 30, y), text, font=f, fill=self.NIGHT_MUTED, anchor="mm")
            y += 70
        if alerts:
            y = self.saver_alert(d, cx, y, alerts, now)

        lead_f, title_f = A(30, 600), self.f(28)
        for ev, lead in self.coming(now, events, 2 if alerts else 3):
            color = _mix(soft(self.color(ev)), BLACK, 0.35)
            lead = lead.upper()
            lw = d.textlength(lead, font=lead_f)
            title = fit(d, ev.title, title_f, 820 - lw - 18)
            x = cx - (lw + 18 + d.textlength(title, font=title_f)) / 2
            _text(d, (x, y), lead, font=lead_f, fill=color, anchor="lm")
            _text(d, (x + lw + 18, y), title, font=title_f, fill=self.NIGHT_MUTED, anchor="lm")
            y += 46
        self._bracket(d, int(cx - 470), int(top + 10), int(cx + 470), int(max(y, top + 300) - 10), (58, 38, 20))
        return img
