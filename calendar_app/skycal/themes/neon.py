"""Cyberpunk neon: near-black violet, electric yellow as the signature accent, magenta and cyan
highlights, cut-corner panels, thin technical rules with tick marks, faint scanlines and a small
cyan/magenta offset on the large headings. The decoration stays in the frame; events keep the
colour of their calendar, lifted so it glows on black.

Fonts: Orbitron (wide display face) for headings and numbers, Chakra Petch for labels. User text
(titles, places, notes, alert names) uses Chakra Petch only when it has every character of the
string, else the system font, and wraps with the same font it is drawn with.

Backgrounds, gradients, scanlines, hazard stripes and glow masks are built once with Pillow's C
operations and kept; drawing a screen only copies and pastes them.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .. import render as R
from ..feed import Event, on_day
from ..render import (
    CLOSE,
    NAV,
    H,
    W,
    _mix,
    _temp,
    _text,
    fit,
    fmt_time,
    fmt_until,
    fmt_when,
    wrap,
)
from ..weather import Weather, describe
from .original import Original

# Flat colours sit on the RGB565 grid (red and blue multiples of 8, green of 4) so the panel
# shows them as drawn.
BLACK = (8, 4, 16)
PANEL = (24, 16, 40)
PANEL_HI = (40, 28, 64)
EDGE = (88, 64, 136)
YELLOW = (248, 236, 16)
CYAN = (0, 236, 248)
MAGENTA = (248, 40, 160)
GHOST_C = (0, 132, 152)          # the offset copies behind big headings
GHOST_M = (168, 24, 112)
DARK_INK = (8, 4, 16)
WHITE = (248, 244, 252)

DISPLAY = "Orbitron-VF.ttf"
_BODY = {False: "ChakraPetch-Medium.ttf", True: "ChakraPetch-Bold.ttf"}
_SEMI = "ChakraPetch-SemiBold.ttf"

_kept: dict = {}                 # built pictures and masks, by what they were built from


def _keep(key, build):
    if key not in _kept:
        _kept[key] = build()
    return _kept[key]


# ---- fonts and the characters they have ----

_missing: dict = {}              # font file -> the mask of a character it does not have
_has: dict = {}                  # (font file, character) -> whether the font draws it
_covers_kept: dict = {}          # (font file, text) -> whether the font draws all of it


def _has_char(name: str, ch: str) -> bool:
    key = (name, ch)
    if key not in _has:
        f = R.font_file(name, 20)
        if name not in _missing:
            m, off = f.getmask2("￿")
            _missing[name] = (m.size, bytes(m), off)
        try:
            m, off = f.getmask2(ch)
            _has[key] = ch.isspace() or (m.size, bytes(m), off) != _missing[name]
        except (RuntimeError, OSError, ValueError):     # not drawable alone (a control character)
            _has[key] = False
    return _has[key]


def covers(name: str, text: str) -> bool:
    key = (name, text)
    if key not in _covers_kept:
        if len(_covers_kept) > 4000:
            _covers_kept.clear()
        _covers_kept[key] = all(_has_char(name, ch) for ch in set(text))
    return _covers_kept[key]


@lru_cache(maxsize=512)
def glow(color: tuple) -> tuple:
    """`color` lifted so it reads on near-black, keeping its hue: scaled up, then (only when
    that is not enough, as for pure blue) mixed toward white."""
    r, g, b = color[:3]
    top = max(r, g, b)
    if top < 24:                                  # black: a neutral light grey-violet
        return (176, 168, 200)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    want = 128
    if lum >= want:
        return (r, g, b)
    k = min(want / max(lum, 1), 255 / top)
    r, g, b = (min(255, int(c * k)) for c in (r, g, b))
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum < want:
        t = (want - lum) / (255 - lum)
        r, g, b = (int(c + (255 - c) * t) for c in (r, g, b))
    return (r, g, b)


def _rel(c: tuple) -> float:
    """Relative luminance (WCAG)."""
    def ch(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


@lru_cache(maxsize=256)
def ink_on(fill: tuple) -> tuple:
    """Dark or light text, whichever has the higher contrast on `fill`."""
    lum = _rel(fill)
    return DARK_INK if (lum + 0.05) / (_rel(DARK_INK) + 0.05) > (_rel(WHITE) + 0.05) / (lum + 0.05) else WHITE


# ---- shapes ----

def cut(box: tuple, c: int, tl: bool = False, tr: bool = True, br: bool = False, bl: bool = True) -> list:
    """The corners of `box` with the chosen corners cut off at 45 degrees by `c` pixels."""
    x0, y0, x1, y1 = box
    pts = []
    pts += [(x0, y0 + c), (x0 + c, y0)] if tl else [(x0, y0)]
    pts += [(x1 - c, y0), (x1, y0 + c)] if tr else [(x1, y0)]
    pts += [(x1, y1 - c), (x1 - c, y1)] if br else [(x1, y1)]
    pts += [(x0 + c, y1), (x0, y1 - c)] if bl else [(x0, y1)]
    return pts


def brackets(d: ImageDraw.ImageDraw, box: tuple, size: int, fill: tuple, width: int = 2, which: str = "tl br") -> None:
    """Corner brackets just outside `box`."""
    x0, y0, x1, y1 = box
    if "tl" in which:
        d.line((x0, y0 + size, x0, y0, x0 + size, y0), fill=fill, width=width)
    if "tr" in which:
        d.line((x1 - size, y0, x1, y0, x1, y0 + size), fill=fill, width=width)
    if "br" in which:
        d.line((x1, y1 - size, x1, y1, x1 - size, y1), fill=fill, width=width)
    if "bl" in which:
        d.line((x0 + size, y1, x0, y1, x0, y1 - size), fill=fill, width=width)


def _backdrop(top: tuple, bottom: tuple, lines: int) -> Image.Image:
    """A W x H vertical gradient with a little noise (so the 16-bit panel does not band) and a
    scanline darkened by `lines` (0..255) every third row."""
    ramp = Image.linear_gradient("L").resize((W, H), Image.BILINEAR)
    img = Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), ramp)
    noise = Image.effect_noise((W, H), 3.0)
    img = ImageChops.add(img, Image.merge("RGB", (noise, noise, noise)), 1.0, -128)
    if lines:
        strip = Image.new("L", (1, H))
        strip.putdata([255 - lines if y % 3 == 2 else 255 for y in range(H)])
        shade = strip.resize((W, H), Image.NEAREST)
        img = ImageChops.multiply(img, Image.merge("RGB", (shade, shade, shade)))
    return img


def _hazard(w: int, h: int, color: tuple) -> Image.Image:
    """Diagonal stripes, `color` and near-black."""
    img = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(img)
    step = 16
    for x in range(-h, w + h, step):
        d.polygon([(x, h), (x + h, 0), (x + h + step // 2, 0), (x + step // 2, h)], fill=DARK_INK)
    return img


_GLOW_PAD = 10


def _glow_mask(w: int, h: int) -> Image.Image:
    g = _GLOW_PAD
    m = Image.new("L", (w + 2 * g, h + 2 * g), 0)
    ImageDraw.Draw(m).rectangle((g - 1, g - 1, g + w, g + h), outline=200, width=4)
    return m.filter(ImageFilter.GaussianBlur(4))


class Neon(Original):
    name = "neon"
    label = "Cyberpunk neon"

    BG = BLACK
    INK = (240, 236, 248)
    MUTED = (156, 144, 188)
    FAINT = (100, 88, 128)
    RULE = (52, 36, 84)
    TODAY_BG = (24, 14, 44)
    ACCENT = YELLOW
    WARN = (255, 88, 112)
    CAL_COLORS = [(0, 220, 248), (248, 64, 176), (64, 236, 128), (255, 144, 32), (168, 112, 255), (136, 168, 216)]
    NIGHT_BG = (0, 0, 0)
    NIGHT_INK = (168, 160, 192)
    NIGHT_MUTED = (104, 96, 128)
    ICON_COLORS = ((248, 216, 24), (184, 176, 224), (132, 124, 168), (0, 200, 248), (176, 220, 248), (248, 236, 16))
    TEMP_LINE, TEMP_DOT = YELLOW, YELLOW
    SEVERITY_COLORS = {"Extreme": (232, 16, 72), "Severe": (240, 40, 40), "Moderate": (255, 120, 0),
                       "Minor": (255, 184, 0), "Unknown": (128, 116, 152)}
    DIM = R.dim_table((4, 2, 10), 0.62)

    TITLE_SIZE, LABEL_SIZE, LINE_H, LABEL_H = 18, 15, 22, 19

    # ---- fonts ----

    def f(self, size: int, bold: bool = False):
        """Chakra Petch, for labels the app writes (English)."""
        return R.font_file(_BODY[bold], size)

    def semi(self, size: int):
        return R.font_file(_SEMI, size)

    def disp(self, size: int, weight: int = 800):
        """Orbitron, for headings and numbers (Latin, digits, punctuation)."""
        return R.font_file(DISPLAY, size, weight)

    def u(self, text: str, size: int, bold: bool = False):
        """The font for user text: Chakra Petch when it has every character, else the system font."""
        return self.f(size, bold) if covers(_BODY[bold], text or "") else R.font(size, bold)

    def color(self, ev: Event) -> tuple:
        return glow(R.event_color(ev, self.CAL_COLORS))

    def glitch(self, d: ImageDraw.ImageDraw, xy: tuple, text: str, font, fill: tuple, anchor: str = "lm",
               off: int = 2, ghosts: tuple = (GHOST_C, GHOST_M)) -> None:
        """A heading with a cyan copy shifted left and a magenta one shifted right behind it."""
        x, y = int(xy[0]), int(xy[1])
        _text(d, (x - off, y), text, font=font, fill=ghosts[0], anchor=anchor)
        _text(d, (x + off, y), text, font=font, fill=ghosts[1], anchor=anchor)
        _text(d, (x, y), text, font=font, fill=fill, anchor=anchor)

    # ---- canvas and building blocks ----

    def canvas(self, screen: str) -> Image.Image:
        if screen == "saver":
            return Image.new("RGB", (W, H), self.NIGHT_BG)
        return _keep(("bg",), lambda: _backdrop((22, 10, 40), (6, 4, 12), 22)).copy()

    def icon(self, d, cx, cy, size, name, is_day=True, bg=None, fade=False) -> None:
        R.weather_icon(d, cx, cy, size, name, is_day, self.BG if bg is None else bg, fade, self.ICON_COLORS, self.FAINT)

    def panel(self, d: ImageDraw.ImageDraw, box: tuple, c: int = 10, fill: tuple = PANEL, outline: tuple = EDGE,
              width: int = 2) -> None:
        d.polygon(cut(box, c), fill=fill, outline=outline, width=width)

    def chevron(self, d: ImageDraw.ImageDraw, cx: int, cy: int, size: int, left: bool, fill: tuple) -> None:
        s, t = size, max(4, size // 3)
        k = -1 if left else 1
        d.polygon([(cx - k * s // 2, cy - s), (cx - k * s // 2 + k * t, cy - s), (cx + k * s // 2 + k * t // 2, cy),
                   (cx - k * s // 2 + k * t, cy + s), (cx - k * s // 2, cy + s), (cx + k * s // 2 - k * t // 2, cy)],
                  fill=fill)

    def button(self, d: ImageDraw.ImageDraw, box: tuple, label: str, size: int, lift: int = 0) -> None:
        x0, y0, x1, y1 = box
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        primary = label == "Today"
        if primary:
            d.polygon(cut(box, 10), fill=YELLOW)
        else:
            self.panel(d, box)
            d.line((x0, y1 - 10, x0 + 10, y1), fill=YELLOW, width=3)
        if label in ("‹", "›"):
            self.chevron(d, cx, cy, 11, label == "‹", self.INK)
            return
        _text(d, (cx, cy), label.upper(), font=self.f(21, True), fill=DARK_INK if primary else self.INK, anchor="mm")

    # ---- week ----

    def header(self, d: ImageDraw.ImageDraw, start: date, now: datetime, note: str, stale: bool,
               weather: Weather | None) -> None:
        hh = self.HEADER_H
        title = self.week_title(start).upper()
        tf = self.disp(28, 800)
        d.polygon([(20, 14), (28, 14), (24, 62), (16, 62)], fill=YELLOW)
        week_no = (start + timedelta(days=1)).isocalendar()[1]
        _text(d, (40, 18), f"WEEK {week_no:02d}  //  SCHEDULE", font=self.f(13, True), fill=YELLOW, anchor="lm")
        self.glitch(d, (40, 47), title, tf, self.INK)

        right = NAV["settings"][0] - 18
        if note:
            room = right - (40 + d.textlength(title, font=tf) + 24)
            _text(d, (right, 26), fmt_time(now), font=self.disp(24, 700), fill=YELLOW, anchor="rm")
            nf = self.u(note, 15, True)
            _text(d, (right, 55), fit(d, note, nf, room), font=nf, fill=self.WARN if stale else self.MUTED, anchor="rm")
        else:
            _text(d, (right, hh // 2), fmt_time(now), font=self.disp(28, 700), fill=YELLOW, anchor="rm")
        self.settings_button(d)
        self.weather_button(d, weather.at(now) if weather else None)
        self.button(d, NAV["prev"], "‹", 36, 4)
        self.button(d, NAV["today"], "Today", 22)
        self.button(d, NAV["next"], "›", 36, 4)

    def weather_button(self, d: ImageDraw.ImageDraw, hour) -> None:
        x0, y0, _x1, y1 = NAV["weather"]
        if hour:
            self.panel(d, NAV["weather"])
            d.line((x0, y1 - 10, x0 + 10, y1), fill=YELLOW, width=3)
            self.icon(d, x0 + 42, (y0 + y1) // 2, 38, describe(hour.code)[0], hour.is_day, PANEL)
            _text(d, (x0 + 78, (y0 + y1) // 2), _temp(hour.temp), font=self.disp(24, 700), fill=self.INK, anchor="lm")
        else:
            self.button(d, NAV["weather"], "Weather", 22)

    def header_rule(self, d: ImageDraw.ImageDraw) -> None:
        y = self.HEADER_H
        d.line((0, y, W, y), fill=EDGE, width=1)
        for x in range(160, 820, 16):
            d.line((x, y - (5 if x % 80 == 0 else 2), x, y - 1), fill=self.RULE if x % 80 else EDGE)
        d.rectangle((0, y - 1, 140, y + 1), fill=YELLOW)
        d.polygon([(140, y - 1), (146, y - 1), (149, y + 1), (140, y + 1)], fill=YELLOW)
        d.rectangle((W - 60, y - 1, W, y), fill=MAGENTA)

    def _today_column(self, x0: int, x1: int, bottom: int) -> Image.Image:
        def build():
            bg = _keep(("bg",), lambda: _backdrop((22, 10, 40), (6, 4, 12), 22))
            box = (x0, self.HEADER_H + 1, x1, bottom)
            col = Image.blend(bg.crop(box), Image.new("RGB", (x1 - x0, box[3] - box[1]), (56, 28, 96)), 0.3)
            dd = ImageDraw.Draw(col)
            dd.line((0, 0, 0, col.height), fill=(96, 64, 152))
            dd.line((col.width - 1, 0, col.width - 1, col.height), fill=(96, 64, 152))
            dd.rectangle((0, 0, col.width, 2), fill=YELLOW)
            return col
        return _keep(("today", x0, x1, bottom), build)

    def day_head(self, d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, today: date) -> None:
        is_today = day == today
        top = self.HEADER_H
        cx = (x0 + x1) // 2
        if is_today:
            img = getattr(d, "_image", None)
            bottom = self._bottom
            if img is not None:
                img.paste(self._today_column(x0, x1, bottom), (x0, top + 1))
            else:
                d.rectangle((x0, top + 1, x1, bottom), fill=self.TODAY_BG)
        past = day < today
        wd = f"{day:%a}".upper()
        _text(d, (cx, top + 21), wd, font=self.f(16, True),
              fill=YELLOW if is_today else (self.FAINT if past else self.MUTED), anchor="mm")
        num = str(day.day)
        nf = self.disp(28, 800)
        if is_today:
            tw = d.textlength(num, font=nf)
            half = max(26, int(tw / 2) + 14)
            d.polygon(cut((cx - half, top + 37, cx + half, top + 75), 8), fill=YELLOW)
            _text(d, (cx, top + 56), num, font=nf, fill=DARK_INK, anchor="mm")
        else:
            _text(d, (cx, top + 56), num, font=nf, fill=self.FAINT if past else self.INK, anchor="mm")

    def day(self, d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, events: list[Event], now: datetime,
            hits: list | None, bottom: int = H) -> None:
        # Original.day() with the title font chosen per title (user text); hits and "+N more" as there.
        self._bottom = bottom
        today = now.date()
        is_today = day == today
        self.day_head(d, x0, x1, day, today)
        if hits is not None and events:
            hits.append((x0, self.HEADER_H, x1, self.HEADER_H + self.DAYHEAD_H, day))
        pad = self.PAD
        y = self.HEADER_H + self.DAYHEAD_H + pad
        left, right = x0 + pad, x1 - pad
        text_w = right - left - 16
        back = self.TODAY_BG if is_today else self.BG
        for i, ev in enumerate(events):
            over = ev.end <= now and (ev.all_day or ev.start < now)
            label, current = self.event_label(ev, day, now)
            lines = wrap(d, ev.title, self.u(ev.title, self.TITLE_SIZE, True), text_w, 3)
            h = self.plate_height(label, lines)
            if y + h > bottom - 30 and i < len(events) - 1 or y + h > bottom - 4:
                self.more(d, (x0 + x1) // 2, bottom, len(events) - i)
                if hits is not None:
                    hits.append((x0, y, x1, bottom, day))
                break
            self.plate(d, (left, y, right, y + h), ev, label, lines, over, current, back)
            if hits is not None:
                hits.append((left, y, right, y + h, ev))
            y += h + pad

    _bottom = H

    def plate(self, d: ImageDraw.ImageDraw, box: tuple, ev: Event, label: str, lines: list, over: bool, current: bool,
              back: tuple) -> None:
        left, y, right, y1 = box
        color = self.color(ev)
        tf = self.u(ev.title, self.TITLE_SIZE, True)
        if current:
            img = getattr(d, "_image", None)
            if img is not None:
                mask = _keep(("glow", right - left, y1 - y), lambda: _glow_mask(right - left, y1 - y))
                g = _GLOW_PAD
                img.paste(color, (left - g, y - g, left - g + mask.width, y - g + mask.height), mask)
        if ev.all_day:
            fill = _mix(color, back, 0.72) if over else _mix(color, BLACK, 0.12)
            d.polygon(cut(box, 8, tr=True, bl=False), fill=fill)
            ink = _mix(self.INK, back, 0.45) if over else ink_on(fill)
            sub = ink
        else:
            fill = _mix(color, back, 0.92) if over else _mix(color, BLACK, 0.80)
            d.polygon(cut(box, 8, tr=True, bl=False), fill=fill,
                      outline=_mix(color, WHITE, 0.25) if current else None, width=2)
            d.rectangle((left, y, left + 3, y1), fill=_mix(color, back, 0.6) if over else color)
            ink = (128, 116, 156) if over else self.INK
            sub = _mix(color, back, 0.5) if over else color
        ty = y + 7
        if label:
            _text(d, (left + 12, ty), label, font=self.f(self.LABEL_SIZE, True) if current else self.semi(self.LABEL_SIZE),
                  fill=sub)
            ty += self.LABEL_H
        for line in lines:
            _text(d, (left + 12, ty), line, font=tf, fill=ink)
            ty += self.LINE_H

    def more(self, d: ImageDraw.ImageDraw, cx: int, bottom: int, count: int) -> None:
        text = f"+{count} MORE"
        mf = self.f(16, True)
        tw = int(d.textlength(text, font=mf))
        y = bottom - 16
        d.line((cx - tw // 2 - 22, y, cx - tw // 2 - 8, y), fill=YELLOW, width=2)
        d.line((cx + tw // 2 + 8, y, cx + tw // 2 + 22, y), fill=YELLOW, width=2)
        _text(d, (cx, y), text, font=mf, fill=YELLOW, anchor="mm")

    def grid(self, d: ImageDraw.ImageDraw, edges: list, bottom: int) -> None:
        top, mid = self.HEADER_H, self.HEADER_H + self.DAYHEAD_H
        d.line((0, mid, W, mid), fill=self.RULE, width=1)
        for x in edges[1:-1]:
            d.line((x, top + 1, x, bottom), fill=self.RULE, width=1)
            d.line((x - 4, mid, x + 4, mid), fill=EDGE)
            d.line((x, mid - 4, x, mid + 4), fill=EDGE)

    def message_box(self, d: ImageDraw.ImageDraw, message: str) -> None:
        mf = self.u(message, 24)
        lines = wrap(d, message, mf, 700, 4)
        half = 34 + 17 * len(lines)
        box = (W // 2 - 400, 460 - half, W // 2 + 400, 460 + half)
        self.panel(d, box, 16, PANEL, YELLOW, 2)
        brackets(d, (box[0] - 8, box[1] - 8, box[2] + 8, box[3] + 8), 18, YELLOW, 2, "tl br")
        _text(d, (box[0] + 28, box[1] + 18), "SYSTEM  //  NOTICE", font=self.f(13, True), fill=YELLOW, anchor="lm")
        for i, line in enumerate(lines):
            _text(d, (W // 2, 470 - 17 * (len(lines) - 1) + 34 * i), line, font=mf, fill=self.INK, anchor="mm")

    def alert_band(self, d: ImageDraw.ImageDraw, box: tuple, alerts: list, now: datetime) -> None:
        x0, y0, x1, y1 = box
        color = self.alert_color(alerts[0].severity)
        ink = ink_on(color)
        d.rectangle(box, fill=color)
        img = getattr(d, "_image", None)
        if img is not None:
            img.paste(_keep(("hazard", 48, y1 - y0, color), lambda: _hazard(48, y1 - y0, color)), (x0, y0))
        d.line((x0, y0, x1, y0), fill=_mix(color, WHITE, 0.5), width=2)
        cy = (y0 + y1) // 2
        R.warn_icon(d, x0 + 78, cy, 26, ink, color)
        hint = "TAP FOR DETAILS"
        hf = self.f(16, True)
        hint_w = d.textlength(hint, font=hf)
        text = self.alert_text(alerts, now)
        tf = self.u(text, 21, True)
        _text(d, (x0 + 104, cy), fit(d, text, tf, x1 - x0 - 104 - hint_w - 70), font=tf, fill=ink, anchor="lm")
        _text(d, (x1 - 44, cy), hint, font=hf, fill=ink, anchor="rm")
        self.chevron(d, x1 - 26, cy, 7, False, ink)

    # ---- cards over the week ----

    def card(self, base: Image.Image, height: int, width: int = 820) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple]:
        img = base.point(self.DIM)
        d = ImageDraw.Draw(img)
        height = min(height, H - 40)
        box = ((W - width) // 2, (H - height) // 2, (W + width) // 2, (H + height) // 2)
        x0, y0, x1, y1 = box
        d.polygon(cut(box, 18, tl=False, tr=True, br=False, bl=True), fill=PANEL, outline=EDGE, width=2)
        brackets(d, (x0 - 7, y0 - 7, x1 + 7, y1 + 7), 22, YELLOW, 2, "tl br")
        for x in range(x1 - 150, x1 - 30, 10):
            d.line((x, y0 + 8, x, y0 + (14 if x % 50 == 0 else 11)), fill=EDGE)
        return img, d, box

    def card_strip(self, d: ImageDraw.ImageDraw, box: tuple, color: tuple) -> None:
        x0, y0, _x1, y1 = box
        d.polygon([(x0 + 14, y0 + 26), (x0 + 22, y0 + 26), (x0 + 22, y1 - 40), (x0 + 14, y1 - 48)], fill=color)
        d.rectangle((x0 + 14, y1 - 34, x0 + 22, y1 - 30), fill=color)

    def card_hint(self, d: ImageDraw.ImageDraw, box: tuple, text: str) -> None:
        x0, _y0, x1, y1 = box
        hf = self.f(15, True)
        t = text.upper()
        tw = int(d.textlength(t, font=hf))
        cx, y = (x0 + x1) // 2, y1 - 28
        d.line((cx - tw // 2 - 60, y, cx - tw // 2 - 14, y), fill=self.RULE, width=1)
        d.line((cx + tw // 2 + 14, y, cx + tw // 2 + 60, y), fill=self.RULE, width=1)
        _text(d, (cx, y), t, font=hf, fill=self.MUTED, anchor="mm")

    def section(self, d: ImageDraw.ImageDraw, xy: tuple, text: str) -> None:
        """A small yellow section label with a slash mark in front."""
        x, y = xy
        d.polygon([(x + 4, y + 2), (x + 8, y + 2), (x + 4, y + 14), (x, y + 14)], fill=YELLOW)
        _text(d, (x + 14, y), text, font=self.f(15, True), fill=YELLOW)

    def render_detail(self, base: Image.Image, ev: Event, now: datetime) -> Image.Image:
        probe = ImageDraw.Draw(base)
        text_w = 820 - 46 - 36
        title_f = self.u(ev.title, 34, True)
        when_f = self.f(23)
        body_f = self.u(ev.location or "", 21)
        notes_f = self.u(ev.description or "", 21)
        title = wrap(probe, ev.title, title_f, text_w, 3)
        when = wrap(probe, fmt_when(ev), when_f, text_w, 2)
        until = fmt_until(ev, now)
        where = wrap(probe, ev.location, body_f, text_w, 3) if ev.location else []
        fixed = 30 + 42 * len(title) + 10 + 30 * len(when) + (30 if until else 0) + (22 + 24 + 28 * len(where) if where else 0) + 62
        room = (H - 40 - fixed - 46) // 28
        notes = wrap(probe, ev.description, notes_f, text_w, max(1, min(12, room))) if ev.description else []
        img, d, box = self.card(base, fixed + (22 + 24 + 28 * len(notes) if notes else 0))
        x0, y0, _x1, _y1 = box
        color = self.color(ev)
        self.card_strip(d, box, color)

        x, y = x0 + 46, y0 + 30
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=self.INK)
            y += 42
        d.line((x, y + 2, x + 64, y + 2), fill=color, width=2)
        y += 10
        for line in when:
            _text(d, (x, y), line, font=when_f, fill=self.INK)
            y += 30
        if until:
            _text(d, (x, y + 2), until.upper(), font=self.f(18, True), fill=self.MUTED if until == "Ended" else color)
            y += 30
        for name, lines, lf in (("WHERE", where, body_f), ("NOTES", notes, notes_f)):
            if lines:
                y += 22
                self.section(d, (x, y), name)
                y += 24
                for line in lines:
                    _text(d, (x, y), line, font=lf, fill=self.INK)
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
        img, d, box = self.card(base, 84 + row_h * max(rows, 1) + 56)
        x0, y0, x1, _y1 = box
        self.glitch(d, (x0 + 36, y0 + 44), f"{day:%A}, {day:%B} {day.day}".upper(), self.disp(24, 800), self.INK)
        count = f"{len(everything)} EVENT" + ("" if len(everything) == 1 else "S")
        _text(d, (x1 - 36, y0 + 46), count, font=self.f(16, True), fill=YELLOW, anchor="rm")
        lo = datetime.combine(day, time.min, tz)
        y = y0 + 84
        d.line((x0 + 30, y, x1 - 30, y), fill=EDGE, width=1)
        if not todays:
            _text(d, (x0 + 36, y + row_h // 2), "Nothing on this day", font=self.f(21), fill=self.MUTED, anchor="lm")
        for i, ev in enumerate(todays[:rows]):
            if i == rows - 1 and len(todays) > rows:
                if i:
                    d.line((x0 + 30, y, x1 - 30, y), fill=self.RULE, width=1)
                _text(d, (x0 + 36, y + row_h // 2), f"SHOW {len(todays) - i} MORE", font=self.f(19, True), fill=YELLOW,
                      anchor="lm")
                self.chevron(d, x0 + 36 + int(d.textlength(f"SHOW {len(todays) - i} MORE", font=self.f(19, True))) + 16,
                             y + row_h // 2, 7, False, YELLOW)
                if hits is not None:
                    hits.append((x0, y, x1, y + row_h, first + i))
                break
            color = self.color(ev)
            over = ev.end <= now and (ev.all_day or ev.start < now)
            current = not ev.all_day and ev.start <= now < ev.end
            if i:
                d.line((x0 + 30, y, x1 - 30, y), fill=self.RULE, width=1)
            if current:
                d.rectangle((x0 + 30, y + 3, x1 - 30, y + row_h - 3), fill=PANEL_HI)
            d.polygon([(x0 + 36, y + 11), (x0 + 42, y + 11), (x0 + 42, y + row_h - 15), (x0 + 36, y + row_h - 9)],
                      fill=_mix(color, PANEL, 0.6) if over else color)
            _text(d, (x0 + 58, y + row_h // 2), self.span(ev, lo), font=self.semi(19),
                  fill=self.FAINT if over else color, anchor="lm")
            tf = self.u(ev.title, 21, True)
            _text(d, (x0 + 250, y + row_h // 2), fit(d, ev.title, tf, x1 - 36 - (x0 + 250)),
                  font=tf, fill=self.FAINT if over else self.INK, anchor="lm")
            if hits is not None:
                hits.append((x0, y, x1, y + row_h, ev))
            y += row_h
        self.card_hint(d, box, "Tap an event for details, or outside to close")
        return img

    def render_alerts(self, base: Image.Image, alerts: list, now: datetime, hits: list | None, index: int) -> Image.Image:
        a = alerts[index]
        width = 900
        probe = ImageDraw.Draw(base)
        text_w = width - 46 - 40
        title_f = self.u(a.event, 32, True)
        when_f, area_f = self.f(22), self.u(a.area or "", 19)
        what_text = a.description or a.headline
        what_f, todo_f = self.u(what_text or "", 19), self.u(a.instruction or "", 19)
        color = self.alert_color(a.severity)
        title = wrap(probe, a.event, title_f, text_w, 2)
        when = self.alert_when(a, now)
        area = wrap(probe, a.area, area_f, text_w, 2) if a.area else []
        more = len(alerts) > 1
        fixed = 30 + 40 * len(title) + 8 + (30 if when else 0) + 30 + (24 * len(area) + 10 if area else 0) + 62 + (50 if more else 0)
        room = max(2, (H - 40 - fixed) // 26 - 2)
        what = wrap(probe, what_text, what_f, text_w, max(1, room - (4 if a.instruction else 0)))
        todo = wrap(probe, a.instruction, todo_f, text_w, max(1, room - len(what) - 2)) if a.instruction and room - len(what) >= 3 else []
        body = 24 + 26 * len(what) + (14 + 24 + 26 * len(todo) if todo else 0)
        img, d, box = self.card(base, fixed + body, width)
        x0, y0, x1, y1 = box
        d.polygon([(x0 + 14, y0 + 26), (x0 + 22, y0 + 26), (x0 + 22, y1 - 40), (x0 + 14, y1 - 48)], fill=color)
        img.paste(_keep(("hazard", 8, 40, color), lambda: _hazard(8, 40, color)), (x0 + 14, y0 + 26))
        x, y = x0 + 46, y0 + 30
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=self.INK)
            y += 40
        y += 8
        if when:
            _text(d, (x, y), when, font=when_f, fill=self.INK)
            y += 30
        tag = a.severity.upper()
        tf = self.f(16, True)
        tag_w = int(d.textlength(tag, font=tf))
        d.polygon(cut((x, y, x + tag_w + 20, y + 24), 6), fill=color)
        _text(d, (x + 10, y + 12), tag, font=tf, fill=ink_on(color), anchor="lm")
        if a.sender:
            sf = self.u(a.sender, 16, True)
            _text(d, (x + tag_w + 34, y + 12), fit(d, a.sender, sf, text_w - tag_w - 34), font=sf, fill=self.MUTED,
                  anchor="lm")
        y += 30
        for line in area:
            _text(d, (x, y), line, font=area_f, fill=self.MUTED)
            y += 24
        y += 10 if area else 0
        for name, lines, lf in (("WHAT", what, what_f), ("WHAT TO DO", todo, todo_f)):
            if lines:
                y += 14 if name != "WHAT" else 0
                self.section(d, (x, y), name)
                y += 24
                for line in lines:
                    _text(d, (x, y), line, font=lf, fill=self.INK)
                    y += 26
        if more:
            ry = y1 - 62 - 46
            d.line((x0 + 30, ry, x1 - 30, ry), fill=EDGE, width=1)
            nxt = (index + 1) % len(alerts)
            text = f"Next alert: {alerts[nxt].event}  ({nxt + 1} of {len(alerts)})"
            nf = self.u(text, 19, True)
            _text(d, (x, ry + 23), fit(d, text, nf, text_w - 30), font=nf, fill=YELLOW, anchor="lm")
            self.chevron(d, x1 - 44, ry + 23, 8, False, YELLOW)
            if hits is not None:
                hits.append((x0, ry, x1, ry + 46, nxt))
        self.card_hint(d, box, "Tap anywhere else to close")
        return img

    # ---- weather ----

    def weather_day(self, d: ImageDraw.ImageDraw, top: int, name: str, day: date, weather: Weather, now: datetime,
                    lo_t: float, hi_t: float) -> None:
        hours = [h for h in weather.on(day) if h.time.hour >= min(6, now.hour if day == now.date() else 6)]
        name = name.upper()
        nf = self.disp(24, 800)
        d.polygon([(24, top + 22), (30, top + 22), (27, top + 46), (21, top + 46)], fill=YELLOW)
        _text(d, (40, top + 34), name, font=nf, fill=YELLOW, anchor="lm")
        name_w = d.textlength(name, font=nf)
        _text(d, (40 + name_w + 16, top + 35), f"{day:%A}, {day:%B} {day.day}", font=self.f(20), fill=self.MUTED, anchor="lm")
        info = weather.days.get(day)
        if info:
            parts = [describe(info.code)[1], f"High {_temp(info.high)}", f"Low {_temp(info.low)}"]
            if info.sunrise and info.sunset:
                parts += [f"Sunrise {fmt_time(info.sunrise)}", f"Sunset {fmt_time(info.sunset)}"]
            _text(d, (W - 24, top + 35), "   ".join(parts), font=self.f(20, True), fill=self.INK, anchor="rm")
        if not hours:
            _text(d, (W // 2, top + 190), "No forecast for these hours", font=self.f(22), fill=self.MUTED, anchor="mm")
            return
        cw = (W - 40) / len(hours)
        span = max(hi_t - lo_t, 1.0)
        band_top, band_h = top + 176, 74
        points = []
        for i, h in enumerate(hours):
            cx = 20 + cw * (i + 0.5)
            past = h.time + timedelta(hours=1) <= now
            current = h.time <= now < h.time + timedelta(hours=1)
            back = self.BG
            if current:
                back = PANEL
                d.polygon(cut((int(cx - cw / 2 + 2), top + 62, int(cx + cw / 2 - 2), top + 340), 10),
                          fill=PANEL, outline=CYAN, width=2)
            _text(d, (cx, top + 84), "NOW" if current else fmt_time(h.time), font=self.f(17, True),
                  fill=CYAN if current else (self.FAINT if past else self.MUTED), anchor="mm")
            self.icon(d, cx, top + 126, 44, describe(h.code)[0], h.is_day, back, fade=past)
            py = band_top + band_h - (h.temp - lo_t) / span * band_h
            points.append((cx, py, h, past))
            if h.precip is not None and h.precip >= 10 and not past:
                tone = CYAN if h.precip >= 30 else self.MUTED
                d.polygon([(cx, top + 284), (cx - 5, top + 293), (cx + 5, top + 293)], fill=tone)
                d.ellipse((cx - 5, top + 288, cx + 5, top + 298), fill=tone)
                _text(d, (cx, top + 316), f"{h.precip}%", font=self.f(16, h.precip >= 50), fill=tone, anchor="mm")
        for (ax, ay, _, a_past), (bx, by, _, _) in zip(points, points[1:]):
            d.line((ax, ay, bx, by), fill=self.RULE if a_past else self.TEMP_LINE, width=3)
        for cx, py, h, past in points:
            d.rectangle((cx - 4, py - 4, cx + 4, py + 4), fill=self.FAINT if past else self.TEMP_DOT)
            _text(d, (cx, py - 20), _temp(h.temp), font=self.f(20, True), fill=self.FAINT if past else self.INK, anchor="mm")

    def alert_pill(self, d: ImageDraw.ImageDraw, right: int, cy: int, alerts: list) -> int:
        a = alerts[0]
        text = a.event + (f" +{len(alerts) - 1}" if len(alerts) > 1 else "")
        f = self.u(text, 18, True)
        tw = min(d.textlength(text, font=f), 300)
        x0 = int(right - tw - 56)
        color = self.alert_color(a.severity)
        ink = ink_on(color)
        d.polygon(cut((x0, cy - 20, right, cy + 20), 8), fill=color)
        R.warn_icon(d, x0 + 22, cy, 20, ink, color)
        _text(d, (x0 + 40, cy), fit(d, text, f, 300), font=f, fill=ink, anchor="lm")
        return x0

    def render_weather(self, weather: Weather | None, now: datetime, message: str, alerts: list) -> Image.Image:
        img = self.canvas("weather")
        d = ImageDraw.Draw(img)
        hh = self.HEADER_H
        d.polygon([(20, 14), (28, 14), (24, 62), (16, 62)], fill=YELLOW)
        tf = self.disp(30, 800)
        _text(d, (40, 18), "ATMOSPHERICS  //  48H", font=self.f(13, True), fill=YELLOW, anchor="lm")
        self.glitch(d, (40, 47), "WEATHER", tf, self.INK)
        self.button(d, CLOSE, "Back", 22)
        self.header_rule(d)
        right = CLOSE[0] - 20
        if alerts:
            right = self.alert_pill(d, right, (CLOSE[1] + CLOSE[3]) // 2, alerts) - 16
        if weather is None:
            mf = self.u(message or "No forecast yet", 26)
            for i, line in enumerate(wrap(d, message or "No forecast yet", mf, W - 200, 4)):
                _text(d, (W // 2, 340 + i * 40), line, font=mf, fill=self.MUTED, anchor="mm")
            return img
        sub = f"{weather.place}, as of {fmt_time(weather.fetched)}" if weather.place else f"As of {fmt_time(weather.fetched)}"
        if now - weather.fetched > timedelta(hours=2):
            sub += f" on {weather.fetched:%b} {weather.fetched.day} (no newer forecast could be fetched)"
        x = 40 + d.textlength("WEATHER", font=tf) + 24
        sf = self.u(sub, 20)
        _text(d, (x, 49), fit(d, sub, sf, right - x), font=sf, fill=self.MUTED, anchor="lm")
        today = now.date()
        shown = [h.temp for day in (today, today + timedelta(days=1)) for h in weather.on(day)]
        lo_t, hi_t = (min(shown), max(shown)) if shown else (0.0, 1.0)
        self.weather_day(d, hh, "Today", today, weather, now, lo_t, hi_t)
        y = hh + 362
        d.line((20, y, W - 20, y), fill=self.RULE, width=1)
        for tx in range(20, W - 19, 40):
            d.line((tx, y - 3, tx, y + 3), fill=EDGE)
        d.rectangle((20, y - 1, 100, y + 1), fill=MAGENTA)
        self.weather_day(d, y, "Tomorrow", today + timedelta(days=1), weather, now, lo_t, hi_t)
        return img

    # ---- screensaver ----

    def saver_alert(self, d: ImageDraw.ImageDraw, cx: float, y: float, alerts: list, now: datetime) -> float:
        color = _mix(self.alert_color(alerts[0].severity), (0, 0, 0), 0.35)
        text = self.alert_text(alerts, now)
        f = self.u(text, 30, True)
        shown = fit(d, text, f, 820)
        tw = d.textlength(shown, font=f)
        R.warn_icon(d, cx - tw / 2 - 26, y, 32, color, self.NIGHT_BG)
        _text(d, (cx + 22, y), shown, font=f, fill=color, anchor="mm")
        return y + 62

    def render_saver(self, now: datetime, events: list[Event], weather: Weather | None, alerts: list) -> Image.Image:
        img = self.canvas("saver")
        d = ImageDraw.Draw(img)
        cx = W // 2 + ((now.minute * 7 + now.hour * 3) % 21 - 10) * 12
        top = 96 + ((now.minute * 11 + now.hour) % 9 - 4) * 12 - (30 if alerts else 0)

        clock, suffix = f"{now.hour % 12 or 12}:{now.minute:02d}", "AM" if now.hour < 12 else "PM"
        clock_f, suffix_f = self.disp(150, 700), self.f(44, True)
        cw, sw = d.textlength(clock, font=clock_f), d.textlength(suffix, font=suffix_f)
        x = int(cx - (cw + 18 + sw) / 2)
        self.glitch(d, (x, top + 90), clock, clock_f, self.NIGHT_INK, off=3, ghosts=((0, 44, 56), (64, 0, 40)))
        _text(d, (int(x + cw + 18), top + 128), suffix, font=suffix_f, fill=self.NIGHT_MUTED, anchor="lm")
        dim_y = (64, 60, 8)
        bx0, bx1 = int(cx - 330), int(cx + 330)
        brackets(d, (bx0, top - 6, bx1, top + 246), 20, dim_y, 2, "tl tr bl br")
        _text(d, (bx0 + 30, top - 6), "LOCAL TIME", font=self.f(14, True), fill=dim_y, anchor="lm")
        _text(d, (cx, top + 210), f"{now:%A}, {now:%B} {now.day}".upper(), font=self.f(40, True), fill=self.NIGHT_INK,
              anchor="mm")

        y = top + 300
        hour = weather.at(now) if weather else None
        if hour:
            text = f"{_temp(hour.temp)}  {describe(hour.code)[1]}"
            info = weather.days.get(now.date())
            if info:
                text += f"   High {_temp(info.high)}  Low {_temp(info.low)}"
            tw = d.textlength(text, font=self.f(32))
            self.icon(d, cx - tw / 2 - 12, y, 56, describe(hour.code)[0], hour.is_day, self.NIGHT_BG, fade=True)
            _text(d, (cx + 34, y), text, font=self.f(32), fill=self.NIGHT_MUTED, anchor="mm")
            y += 74
        if alerts:
            y = self.saver_alert(d, cx, y, alerts, now)

        for ev, lead in self.coming(now, events, 2 if alerts else 3):
            color = _mix(self.color(ev), (0, 0, 0), 0.4)
            lead_f, title_f = self.f(28, True), self.u(ev.title, 28)
            lw = d.textlength(lead, font=lead_f)
            title = fit(d, ev.title, title_f, 860 - lw - 18)
            x = cx - (lw + 18 + d.textlength(title, font=title_f)) / 2
            _text(d, (x, y), lead, font=lead_f, fill=color, anchor="lm")
            _text(d, (x + lw + 18, y), title, font=title_f, fill=self.NIGHT_MUTED, anchor="lm")
            y += 46
        return img
