"""Premium sports: the look of a big racing or football game's menus. A saturated blue-to-violet
header band, a dark charcoal-to-deep-blue body, bold italic condensed numerals and headings,
chamfered and slanted panels, and one volt-lime accent for "today", "now" and what can be tapped.

Display type is Barlow Condensed and Barlow (bundled); text a user typed (event titles,
locations, notes) is drawn in the system font, which has the glyphs of any language.
Backgrounds, gradients and antialiased shape masks are built once and kept (with a little noise
so the 16-bit panel does not band); drawing a screen only pastes them."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PIL import Image, ImageChops, ImageDraw

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

_FILES = {
    "black": "BarlowCondensed-BlackItalic.ttf",     # big numerals and headings
    "xb": "BarlowCondensed-ExtraBoldItalic.ttf",    # labels, buttons, tags
    "cb": "BarlowCondensed-Bold.ttf",               # small upright labels
    "cs": "BarlowCondensed-SemiBold.ttf",
    "bm": "Barlow-Medium.ttf",                      # running text the app writes
    "bs": "Barlow-SemiBold.ttf",
    "bb": "Barlow-Bold.ttf",
}
_BOX = getattr(Image, "Resampling", Image).BOX
_SS = 4                                             # supersampling of the shape masks
_shapes: dict = {}                                  # kept masks and pictures, keyed by what they are
_SHAPES_MAX = 400


def df(size: int, kind: str = "xb"):
    """A bundled display font."""
    return R.font_file(_FILES[kind], size)


def latin(text: str) -> bool:
    """Whether the bundled Barlow fonts have every letter of `text` (Latin-1 and Latin Extended-A)."""
    return all(ord(c) < 0x180 or c in "‘’“”–—…‹›•·°" for c in text)


def pick(text: str, size: int, kind: str = "bm", bold: bool = False):
    """Barlow for `text` when it has the glyphs, else the system font."""
    return df(size, kind) if latin(text) else R.font(size, bold)


def lum(c: tuple) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def lift(c: tuple, least: float = 150) -> tuple:
    """`c` lightened just enough to read on the dark body."""
    v = lum(c)
    return c if v >= least else _mix(c, (255, 255, 255), (least - v) / (255 - v))


def ink_on(c: tuple) -> tuple:
    return (12, 14, 24) if lum(c) > 165 else (255, 255, 255)


def _kept(key, build):
    got = _shapes.get(key)
    if got is None:
        if len(_shapes) >= _SHAPES_MAX:
            for k in [k for k in _shapes if k[0] in ("chamfer", "inner", "para")]:
                del _shapes[k]
        got = _shapes[key] = build()
    return got


def _poly_mask(w: int, h: int, points: list) -> Image.Image:
    big = Image.new("L", (w * _SS, h * _SS), 0)
    ImageDraw.Draw(big).polygon([(x * _SS, y * _SS) for x, y in points], fill=255)
    return big.resize((w, h), _BOX)


def chamfer(w: int, h: int, cut: float, inset: float = 0) -> Image.Image:
    """Mask of a w x h panel with its top-left and bottom-right corners cut at 45 degrees."""
    def build():
        o, c = inset, max(cut - 0.586 * inset, 0)
        x0, y0, x1, y1 = o, o, w - o, h - o
        return _poly_mask(w, h, [(x0 + c, y0), (x1, y0), (x1, y1 - c), (x1 - c, y1), (x0, y1), (x0, y0 + c)])
    return _kept(("chamfer", w, h, cut, inset), build)


def inner(w: int, h: int, cut: float, inset: float, stripe: int) -> Image.Image:
    """The chamfered panel inset by `inset` and with `stripe` pixels left free on its left."""
    def build():
        shifted = Image.new("L", (w, h), 0)
        shifted.paste(chamfer(w, h, cut), (stripe, 0))
        return ImageChops.multiply(chamfer(w, h, cut, inset), shifted)
    return _kept(("inner", w, h, cut, inset, stripe), build)


def para(w: int, h: int, slant: float, inset: float = 0) -> Image.Image:
    """Mask of a parallelogram leaning right, `slant` pixels across its height."""
    def build():
        o, k = inset, inset * slant / max(h, 1) + inset
        return _poly_mask(w, h, [(slant + k, o), (w - o, o), (w - slant - k, h - o), (o, h - o)])
    return _kept(("para", w, h, slant, inset), build)


def corner(cut: int, which: str) -> Image.Image:
    """An antialiased cut-off corner: 0 outside the panel, 255 inside."""
    def build():
        pts = [(cut, 0), (cut, cut), (0, cut)] if which == "tl" else [(0, 0), (cut, 0), (0, cut)]
        return _poly_mask(cut, cut, pts)
    return _kept(("corner", cut, which), build)


def chevron(right: bool) -> Image.Image:
    """Mask of a bold arrow head, 20 x 28, pointing right or left."""
    def build():
        pts = [(2, 0), (10, 0), (20, 14), (10, 28), (2, 28), (12, 14)]
        return _poly_mask(20, 28, pts if right else [(20 - x, y) for x, y in pts])
    return _kept(("chevron", right), build)


def gradient(size: tuple, top: tuple, bottom: tuple, horizontal: bool = False) -> Image.Image:
    """A smooth gradient picture (image operations only)."""
    ramp = Image.linear_gradient("L")
    if horizontal:
        ramp = ramp.rotate(90, expand=True)            # dark (first colour) on the left
    ramp = ramp.resize(size, Image.BILINEAR)
    return Image.merge("RGB", [ramp.point(lambda v, a=a, b=b: int(a + (b - a) * v / 255 + 0.5))
                               for a, b in zip(top, bottom)])


def dither(img: Image.Image, sigma: float = 2.6) -> Image.Image:
    """`img` with fine noise added so the 5-6-5 panel shows grain rather than bands."""
    noise = Image.effect_noise(img.size, sigma)
    return Image.merge("RGB", [ImageChops.add(ch, noise, 1.0, -128) for ch in img.split()])


def paste(d: ImageDraw.ImageDraw, fill, xy: tuple, mask: Image.Image) -> None:
    d._image.paste(fill, (int(xy[0]), int(xy[1])), mask)


class Sports(Original):
    name = "sports"
    label = "Premium sports"

    BG = (17, 24, 44)                 # middle of the body gradient: what past events fade toward
    BODY_TOP, BODY_BOTTOM = (22, 25, 36), (11, 23, 56)
    HEAD_STRIP = (11, 14, 26)         # behind the weekday names
    TODAY_BG = (32, 40, 64)
    LIGHTEN = (9, 13, 26)             # how much the today column is lifted, per channel
    CARD_TOP, CARD_BOTTOM = (34, 43, 72), (17, 21, 38)
    CARD_EDGE = (72, 88, 140)
    BAND_LEFT, BAND_RIGHT = (112, 34, 196), (10, 64, 214)
    INK = (246, 248, 252)
    MUTED = (142, 154, 182)
    FAINT = (80, 90, 116)
    RULE = (38, 48, 76)
    ACCENT = (196, 255, 46)           # volt lime
    ACCENT_INK = (12, 16, 24)
    WARN = (255, 228, 120)
    BUTTON, BUTTON_EDGE = (10, 18, 56), (104, 124, 226)
    CAL_COLORS = [(30, 136, 255), (40, 196, 120), (255, 112, 60), (168, 96, 255), (255, 64, 128), (120, 136, 160)]
    NIGHT_BG = (0, 0, 0)
    NIGHT_INK = (168, 174, 196)
    NIGHT_MUTED = (98, 106, 130)
    NIGHT_ACCENT = (26, 42, 98)
    ICON_COLORS = ((255, 186, 40), (150, 160, 205), (164, 172, 190), (70, 150, 255), (130, 185, 245), (255, 196, 40))
    TEMP_LINE, TEMP_DOT = (60, 200, 255), (255, 255, 255)
    RAIN = (90, 170, 255)
    SEVERITY_COLORS = {"Extreme": (172, 12, 56), "Severe": (214, 30, 40), "Moderate": (232, 108, 12),
                       "Minor": (204, 150, 0), "Unknown": (90, 98, 124)}
    DIM = R.dim_table((4, 6, 16), 0.68)

    CUT = 10                          # chamfer of the event plates
    SLANT = 12                        # lean of buttons and tags

    # ---- kept pictures ----

    def _background(self, screen: str) -> Image.Image:
        def build():
            hh = self.HEADER_H
            img = gradient((W, H), self.BODY_TOP, self.BODY_BOTTOM)
            # a faint diagonal sheen across the body, like stadium light
            sheen = gradient((W, H), (0, 0, 0), (255, 255, 255), horizontal=True).convert("L")
            sheen = sheen.rotate(-28, resample=Image.BILINEAR, expand=False, fillcolor=0)
            sheen = sheen.point(lambda v: max(0, 18 - abs(v - 150) // 3))
            img = Image.composite(Image.new("RGB", (W, H), (70, 96, 170)), img, sheen)
            if screen == "week":
                img.paste(self.HEAD_STRIP, (0, hh, W, hh + self.DAYHEAD_H))
            band = gradient((W, hh), self.BAND_LEFT, self.BAND_RIGHT, horizontal=True)
            streaks = Image.new("L", (W, hh), 0)
            sd = ImageDraw.Draw(streaks)
            for x, w, a in ((520, 90, 22), (626, 26, 16), (668, 12, 12), (1210, 120, 14)):
                sd.polygon([(x + 40, 0), (x + 40 + w, 0), (x + w, hh), (x, hh)], fill=a)
            band = Image.composite(Image.new("RGB", (W, hh), (255, 255, 255)), band, streaks)
            # darker at the foot of the band, for depth
            shade = Image.linear_gradient("L").resize((W, hh)).point(lambda v: v * 70 // 255)
            band = Image.composite(Image.new("RGB", (W, hh), (6, 8, 30)), band, shade)
            img.paste(band, (0, 0))
            return dither(img)
        return _kept(("bg", screen), build)

    def _card_texture(self) -> Image.Image:
        return _kept(("cardtex",), lambda: dither(gradient((960, H), self.CARD_TOP, self.CARD_BOTTOM)))

    def _lighter(self) -> list:
        return _kept(("lighter",), lambda: [min(255, v + n) for n in self.LIGHTEN for v in range(256)])

    # ---- building blocks ----

    def canvas(self, screen: str) -> Image.Image:
        if screen == "saver":
            return Image.new("RGB", (W, H), self.NIGHT_BG)
        return self._background("week" if screen == "week" else "weather").copy()

    def tag(self, d: ImageDraw.ImageDraw, x: float, cy: float, text: str, font, fill: tuple, ink: tuple,
            h: int = 30, pad: int = 14) -> int:
        """A slanted tag with `text` from x; returns its right edge."""
        tw = int(d.textlength(text, font=font))
        w = tw + 2 * pad + self.SLANT // 2
        slant = round(self.SLANT * h / 52)
        paste(d, fill, (x, cy - h // 2), para(w, h, slant))
        _text(d, (x + w / 2, cy), text, font=font, fill=ink, anchor="mm")
        return int(x + w)

    def plaque(self, d: ImageDraw.ImageDraw, box: tuple, fill: tuple, edge: tuple | None, slant: int | None = None) -> None:
        """A slanted panel filling `box`, with a 2 px edge when `edge` is given."""
        x0, y0, x1, y1 = (int(v) for v in box)
        w, h = x1 - x0, y1 - y0
        s = self.SLANT if slant is None else slant
        if edge is None:
            paste(d, fill, (x0, y0), para(w, h, s))
        else:
            paste(d, edge, (x0, y0), para(w, h, s))
            paste(d, fill, (x0, y0), para(w, h, s, 2))

    def button(self, d: ImageDraw.ImageDraw, box: tuple, label: str, size: int, lift: int = 0, hot: bool = False) -> None:
        x0, y0, x1, y1 = box
        self.plaque(d, box, self.ACCENT if hot else self.BUTTON, None if hot else self.BUTTON_EDGE)
        cy = (y0 + y1) // 2
        if label in ("‹", "›"):
            paste(d, self.INK, ((x0 + x1) // 2 - 9, cy - 14), chevron(label == "›"))
        else:
            _text(d, ((x0 + x1) // 2, cy), label.upper(), font=df(size + 4, "xb"),
                  fill=self.ACCENT_INK if hot else self.INK, anchor="mm")

    # ---- week ----

    def header(self, d: ImageDraw.ImageDraw, start: date, now: datetime, note: str, stale: bool,
               weather: Weather | None) -> None:
        title = self.week_title(start).upper()
        hh = self.HEADER_H
        title_f = df(44, "black")
        _text(d, (24, hh // 2 + 1), title, font=title_f, fill=self.INK, anchor="lm")
        right = NAV["weather"][0] - 20
        if note:
            room = right - (24 + d.textlength(title, font=title_f) + 24)
            _text(d, (right, 28), fmt_time(now).upper(), font=df(32, "black"), fill=self.INK, anchor="rm")
            note_f = df(17, "cs")
            _text(d, (right, 56), fit(d, note, note_f, room), font=note_f,
                  fill=self.WARN if stale else (206, 212, 246), anchor="rm")
        else:
            _text(d, (right, hh // 2 + 1), fmt_time(now).upper(), font=df(40, "black"), fill=self.INK, anchor="rm")
        self.weather_button(d, weather.at(now) if weather else None)
        self.button(d, NAV["prev"], "‹", 36)
        self.button(d, NAV["today"], "Today", 22, hot=True)
        self.button(d, NAV["next"], "›", 36)

    def weather_button(self, d: ImageDraw.ImageDraw, hour) -> None:
        x0, y0, x1, y1 = NAV["weather"]
        if hour:
            self.plaque(d, NAV["weather"], self.BUTTON, self.BUTTON_EDGE)
            cy = (y0 + y1) // 2
            self.icon(d, x0 + 50, cy, 38, describe(hour.code)[0], hour.is_day, self.BUTTON)
            _text(d, (x0 + 84, cy + 1), _temp(hour.temp), font=df(34, "black"), fill=self.INK, anchor="lm")
        else:
            self.button(d, NAV["weather"], "Weather", 22)

    def header_rule(self, d: ImageDraw.ImageDraw) -> None:
        d.rectangle((0, self.HEADER_H - 3, W, self.HEADER_H - 1), fill=self.ACCENT)

    def day_head(self, d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, today: date) -> None:
        is_today = day == today
        top = self.HEADER_H
        cx = (x0 + x1) // 2
        if is_today:
            img = d._image
            col = (x0 + 1, top, x1, H)
            img.paste(img.crop(col).point(self._lighter()), col[:2])
            d.rectangle((x0 + 1, top, x1 - 1, top + 3), fill=self.ACCENT)
        _text(d, (cx, top + 21), f"{day:%a}".upper(), font=df(19, "xb"),
              fill=self.ACCENT if is_today else self.MUTED, anchor="mm")
        num = str(day.day)
        num_f = df(44, "black")
        if is_today:
            w = int(d.textlength(num, font=num_f)) + 34
            paste(d, self.ACCENT, (cx - w // 2, top + 37), para(w, 40, 10))
        _text(d, (cx, top + 58), num, font=num_f,
              fill=self.ACCENT_INK if is_today else (self.MUTED if day < today else self.INK), anchor="mm")

    def plate(self, d: ImageDraw.ImageDraw, box: tuple, ev: Event, label: str, lines: list, over: bool, current: bool,
              back: tuple) -> None:
        left, y, right, y1 = box
        w, h = right - left, y1 - y
        color = self.color(ev)
        cut = self.CUT
        if ev.all_day:
            fill = _mix(color, back, 0.6) if over else color
            paste(d, fill, (left, y), chamfer(w, h, cut))
            ink = ink_on(color)
            ink = sub = _mix(ink, fill, 0.35) if over else ink
        else:
            if over:
                stripe, tint = _mix(color, back, 0.6), _mix(color, back, 0.88)
            else:
                stripe, tint = color, _mix(color, back, 0.66 if current else 0.72)
            paste(d, stripe, (left, y), chamfer(w, h, cut))
            paste(d, tint, (left, y), inner(w, h, cut, 2, 6) if current else inner(w, h, cut, 0, 5))
            ink = self.MUTED if over else self.INK
            sub = _mix(lift(color), back, 0.45) if over else lift(color, 160)
        ty = y + 7
        if label:
            _text(d, (left + 12, ty), label.upper(), font=df(self.LABEL_SIZE + 2, "xb" if current else "cb"),
                  fill=self.ACCENT if current and not ev.all_day else sub)
            ty += self.LABEL_H
        for line in lines:
            _text(d, (left + 12, ty), line, font=self.f(self.TITLE_SIZE, True), fill=ink)
            ty += self.LINE_H

    def more(self, d: ImageDraw.ImageDraw, cx: int, bottom: int, count: int) -> None:
        text = f"+{count} MORE"
        f = df(19, "xb")
        w = int(d.textlength(text, font=f)) + 30
        paste(d, self.ACCENT, (cx - w // 2, bottom - 29), para(w, 26, 7))
        _text(d, (cx, bottom - 16), text, font=f, fill=self.ACCENT_INK, anchor="mm")

    def grid(self, d: ImageDraw.ImageDraw, edges: list, bottom: int) -> None:
        for x in edges[1:-1]:
            d.line((x, self.HEADER_H, x, bottom), fill=self.RULE, width=1)
        y = self.HEADER_H + self.DAYHEAD_H
        d.line((0, y, W, y), fill=self.RULE, width=1)

    def message_box(self, d: ImageDraw.ImageDraw, message: str) -> None:
        f = pick(message, 25, "bm", False)
        lines = wrap(d, message, f, 700, 4)
        half = 30 + 17 * len(lines)
        x0, y0, x1, y1 = W // 2 - 420, 460 - half, W // 2 + 420, 460 + half
        paste(d, self.ACCENT, (x0, y0), chamfer(x1 - x0, y1 - y0, 22))
        paste(d, (20, 28, 50), (x0, y0), inner(x1 - x0, y1 - y0, 22, 2, 8))
        for i, line in enumerate(lines):
            _text(d, (W // 2 + 4, 460 - 17 * (len(lines) - 1) + 34 * i), line, font=f, fill=self.INK, anchor="mm")

    def alert_band(self, d: ImageDraw.ImageDraw, box: tuple, alerts: list, now: datetime) -> None:
        x0, y0, x1, y1 = box
        color = self.alert_color(alerts[0].severity)
        h = y1 - y0
        d.rectangle(box, fill=color)
        d.rectangle((x0, y0, x1, y0 + 1), fill=_mix(color, (255, 255, 255), 0.45))
        dark = _mix(color, (0, 0, 0), 0.38)
        paste(d, dark, (x0, y0 + 2), para(96, h - 2, 22))
        d.rectangle((x0, y0 + 2, x0 + 40, y1), fill=dark)
        cy = (y0 + y1) // 2
        R.warn_icon(d, x0 + 38, cy + 1, 26, (255, 255, 255), dark)
        hint, hint_f = "TAP FOR DETAILS  ›", df(19, "cb")
        hint_w = d.textlength(hint, font=hint_f)
        text = self.alert_text(alerts, now).upper()
        f = pick(text, 25, "xb", True)
        _text(d, (x0 + 106, cy), fit(d, text, f, x1 - x0 - 150 - hint_w), font=f, fill=(255, 255, 255), anchor="lm")
        _text(d, (x1 - 22, cy), hint, font=hint_f, fill=_mix(color, (255, 255, 255), 0.8), anchor="rm")

    # ---- cards over the week ----

    def card(self, base: Image.Image, height: int, width: int = 820) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple]:
        img = base.point(self.DIM)
        height = min(height, H - 40)
        box = ((W - width) // 2, (H - height) // 2, (W + width) // 2, (H + height) // 2)
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        mask = Image.new("L", (w, h), 255)
        c = 26
        mask.paste(corner(c, "tl"), (0, 0))
        mask.paste(corner(c, "br"), (w - c, h - c))
        img.paste(self._card_texture().crop((0, 0, w, h)), (x0, y0), mask)
        self._card_mask = mask
        d = ImageDraw.Draw(img)
        edge, hi = self.CARD_EDGE, _mix(self.CARD_TOP, (255, 255, 255), 0.22)
        d.line([(x0, y1 - 1), (x0, y0 + c), (x0 + c, y0)], fill=edge, width=1)
        d.line([(x1 - 1, y0), (x1 - 1, y1 - c), (x1 - c, y1 - 1), (x0, y1 - 1)], fill=edge, width=1)
        d.rectangle((x0 + c, y0, x1 - 1, y0 + 1), fill=hi)
        return img, d, box

    def card_strip(self, d: ImageDraw.ImageDraw, box: tuple, color: tuple) -> None:
        x0, y0, x1, y1 = box
        mask = getattr(self, "_card_mask", None)
        strip = mask.crop((0, 0, 12, y1 - y0)) if mask is not None else None
        d._image.paste(color, (x0, y0), strip)
        # a slanted flash of the colour at the top right, like a team stripe
        paste(d, color, (x1 - 150, y0), para(84, 10, 6))
        paste(d, _mix(color, self.CARD_TOP, 0.5), (x1 - 72, y0), para(40, 10, 6))

    def card_hint(self, d: ImageDraw.ImageDraw, box: tuple, text: str) -> None:
        x0, y0, x1, y1 = box
        _text(d, ((x0 + x1) // 2, y1 - 28), text.upper(), font=df(18, "cb"), fill=self.MUTED, anchor="mm")

    def section_label(self, d: ImageDraw.ImageDraw, x: float, y: float, text: str, color: tuple | None = None) -> None:
        """A section label: a small slanted bar and the word."""
        color = color or self.ACCENT
        paste(d, color, (x, y + 3), para(10, 14, 4))
        _text(d, (x + 16, y), text, font=df(18, "xb"), fill=color)

    def render_detail(self, base: Image.Image, ev: Event, now: datetime) -> Image.Image:
        probe = ImageDraw.Draw(base)
        text_w = 820 - 46 - 36
        title_f, body_f = self.f(34, True), self.f(21)
        when_text = fmt_when(ev)
        when_f = pick(when_text, 24, "bm")
        title = wrap(probe, ev.title, title_f, text_w, 3)
        when = wrap(probe, when_text, when_f, text_w, 2)
        until = fmt_until(ev, now)
        where = wrap(probe, ev.location, body_f, text_w, 3) if ev.location else []
        fixed = 30 + 42 * len(title) + 10 + 30 * len(when) + (30 if until else 0) + (22 + 24 + 28 * len(where) if where else 0) + 62
        room = (H - 40 - fixed - 46) // 28
        notes = wrap(probe, ev.description, body_f, text_w, max(1, min(12, room))) if ev.description else []
        img, d, box = self.card(base, fixed + (22 + 24 + 28 * len(notes) if notes else 0))
        x0, y0, x1, y1 = box
        color = self.color(ev)
        self.card_strip(d, box, color)

        x, y = x0 + 46, y0 + 30
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=self.INK)
            y += 42
        y += 10
        for line in when:
            _text(d, (x, y), line, font=when_f, fill=(214, 222, 240))
            y += 30
        if until:
            ended = until == "Ended"
            live = not ev.all_day and ev.start <= now < ev.end
            _text(d, (x, y + 1), until.upper(), font=df(22, "xb"),
                  fill=self.MUTED if ended else (self.ACCENT if live else lift(color, 165)))
            y += 30
        for label, lines in (("WHERE", where), ("NOTES", notes)):
            if lines:
                y += 22
                self.section_label(d, x, y - 2, label)
                y += 24
                for line in lines:
                    _text(d, (x, y), line, font=body_f, fill=self.INK)
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
        x0, y0, x1, y1 = box
        heading = f"{day:%A}  {day:%B} {day.day}".upper()
        _text(d, (x0 + 36, y0 + 46), heading, font=df(40, "black"),
              fill=self.ACCENT if day == now.date() else self.INK, anchor="lm")
        count = f"{len(everything)} EVENT" + ("" if len(everything) == 1 else "S")
        self.tag(d, x1 - 36 - (d.textlength(count, font=df(18, "xb")) + 28 + 6), y0 + 46, count, df(18, "xb"),
                 self.BUTTON, self.MUTED, 28)
        lo = datetime.combine(day, time.min, tz)
        y = y0 + 84
        span_f = df(22, "cb")
        if not todays:
            _text(d, (x0 + 36, y + row_h // 2), "Nothing on this day", font=df(23, "bm"), fill=self.MUTED, anchor="lm")
        for i, ev in enumerate(todays[:rows]):
            if i == rows - 1 and len(todays) > rows:
                d.line((x0 + 30, y, x1 - 30, y), fill=self.RULE, width=1)
                self.tag(d, x0 + 36, y + row_h // 2, f"SHOW {len(todays) - i} MORE", df(20, "xb"), self.ACCENT,
                         self.ACCENT_INK, 30)
                if hits is not None:
                    hits.append((x0, y, x1, y + row_h, first + i))
                break
            color = self.color(ev)
            over = ev.end <= now and (ev.all_day or ev.start < now)
            current = not ev.all_day and ev.start <= now < ev.end
            d.line((x0 + 30, y, x1 - 30, y), fill=self.RULE, width=1)
            paste(d, _mix(color, self.CARD_TOP, 0.6) if over else color, (x0 + 34, y + 9), para(10, row_h - 18, 4))
            _text(d, (x0 + 58, y + row_h // 2), self.span(ev, lo).upper(), font=df(22, "xb") if current else span_f,
                  fill=self.MUTED if over else (self.ACCENT if current else lift(color, 165)), anchor="lm")
            title_f = self.f(21, True)
            _text(d, (x0 + 250, y + row_h // 2), fit(d, ev.title, title_f, x1 - 36 - (x0 + 250)),
                  font=title_f, fill=self.MUTED if over else self.INK, anchor="lm")
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
        event = (a.event or "").upper()
        title_f = pick(event, 38, "black", True)
        when = self.alert_when(a, now)
        when_f = pick(when, 23, "bm")
        body_text = a.description or a.headline or ""
        body_f = pick(body_text + (a.instruction or "") + (a.area or ""), 20, "bm")
        color = self.alert_color(a.severity)
        title = wrap(probe, event, title_f, text_w, 2)
        area = wrap(probe, a.area, body_f, text_w, 2) if a.area else []
        more = len(alerts) > 1
        fixed = 30 + 40 * len(title) + 8 + (30 if when else 0) + 30 + (24 * len(area) + 10 if area else 0) + 62 + (50 if more else 0)
        room = max(2, (H - 40 - fixed) // 26 - 2)
        what = wrap(probe, body_text, body_f, text_w, max(1, room - (4 if a.instruction else 0)))
        todo = wrap(probe, a.instruction, body_f, text_w, max(1, room - len(what) - 2)) if a.instruction and room - len(what) >= 3 else []
        body = 24 + 26 * len(what) + (14 + 24 + 26 * len(todo) if todo else 0)
        img, d, box = self.card(base, fixed + body, width)
        x0, y0, x1, y1 = box
        self.card_strip(d, box, color)
        x, y = x0 + 46, y0 + 30
        for line in title:
            _text(d, (x, y - 2), line, font=title_f, fill=self.INK)
            y += 40
        y += 8
        if when:
            _text(d, (x, y), when, font=when_f, fill=(214, 222, 240))
            y += 30
        sev_f = df(17, "xb")
        right = self.tag(d, x - 4, y + 12, a.severity.upper(), sev_f, color, (255, 255, 255), 24, 10)
        if a.sender:
            sender_f = pick(a.sender, 17, "cb")
            _text(d, (right + 12, y + 12), fit(d, a.sender, sender_f, x1 - 40 - right - 12), font=sender_f,
                  fill=self.MUTED, anchor="lm")
        y += 30
        for line in area:
            _text(d, (x, y), line, font=body_f, fill=self.MUTED)
            y += 24
        y += 10 if area else 0
        for label, lines in (("WHAT", what), ("WHAT TO DO", todo)):
            if lines:
                y += 14 if label != "WHAT" else 0
                self.section_label(d, x, y - 2, label, lift(color, 170) if label == "WHAT" else None)
                y += 24
                for line in lines:
                    _text(d, (x, y), line, font=body_f, fill=self.INK)
                    y += 26
        if more:
            ry = y1 - 62 - 46
            d.line((x0 + 30, ry, x1 - 30, ry), fill=self.RULE, width=1)
            nxt = (index + 1) % len(alerts)
            text = f"NEXT ALERT  {nxt + 1}/{len(alerts)}"
            right = self.tag(d, x - 4, ry + 23, text, df(19, "xb"), self.ACCENT, self.ACCENT_INK, 30)
            name_f = pick(alerts[nxt].event, 21, "bs")
            _text(d, (right + 14, ry + 23), fit(d, alerts[nxt].event, name_f, x1 - 40 - right - 14), font=name_f,
                  fill=self.INK, anchor="lm")
            if hits is not None:
                hits.append((x0, ry, x1, ry + 46, nxt))
        self.card_hint(d, box, "Tap anywhere else to close")
        return img

    # ---- weather ----

    def weather_day(self, d: ImageDraw.ImageDraw, top: int, name: str, day: date, weather: Weather, now: datetime,
                    lo_t: float, hi_t: float) -> None:
        hours = [h for h in weather.on(day) if h.time.hour >= min(6, now.hour if day == now.date() else 6)]
        right = self.tag(d, 20, top + 34, name.upper(), df(28, "black"), self.ACCENT, self.ACCENT_INK, 38, 16)
        _text(d, (right + 16, top + 35), f"{day:%A}  {day:%B} {day.day}".upper(), font=df(22, "cb"), fill=self.MUTED,
              anchor="lm")
        info = weather.days.get(day)
        if info:
            parts = [describe(info.code)[1], f"High {_temp(info.high)}", f"Low {_temp(info.low)}"]
            if info.sunrise and info.sunset:
                parts += [f"Sunrise {fmt_time(info.sunrise)}", f"Sunset {fmt_time(info.sunset)}"]
            _text(d, (W - 24, top + 35), "   ".join(parts), font=df(20, "bm"), fill=(214, 222, 240), anchor="rm")
        if not hours:
            _text(d, (W // 2, top + 190), "No forecast for these hours", font=df(24, "bm"), fill=self.MUTED, anchor="mm")
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
                back = self.TODAY_BG
                bw = int(cw) - 4
                paste(d, self.ACCENT, (cx - bw / 2, top + 62), chamfer(bw, 278, 12))
                paste(d, back, (cx - bw / 2, top + 62), chamfer(bw, 278, 12, 2))
            _text(d, (cx, top + 84), "NOW" if current else fmt_time(h.time).upper(),
                  font=df(19, "xb" if current else "cb"),
                  fill=self.ACCENT if current else (self.FAINT if past else self.MUTED), anchor="mm")
            self.icon(d, cx, top + 126, 44, describe(h.code)[0], h.is_day, back, fade=past)
            py = band_top + band_h - (h.temp - lo_t) / span * band_h
            points.append((cx, py, h, past))
            if h.precip is not None and h.precip >= 10 and not past:
                tone = self.RAIN if h.precip >= 30 else self.MUTED
                d.polygon([(cx, top + 284), (cx - 5, top + 293), (cx + 5, top + 293)], fill=tone)
                d.ellipse((cx - 5, top + 288, cx + 5, top + 298), fill=tone)
                _text(d, (cx, top + 316), f"{h.precip}%", font=df(18, "xb" if h.precip >= 50 else "cb"), fill=tone,
                      anchor="mm")
        for (ax, ay, _, a_past), (bx, by, _, _) in zip(points, points[1:]):
            d.line((ax, ay, bx, by), fill=self.RULE if a_past else self.TEMP_LINE, width=4)
        for cx, py, h, past in points:
            d.ellipse((cx - 5, py - 5, cx + 5, py + 5), fill=self.FAINT if past else self.TEMP_DOT,
                      outline=None if past else self.TEMP_LINE, width=2)
            _text(d, (cx, py - 21), _temp(h.temp), font=df(24, "black"), fill=self.FAINT if past else self.INK,
                  anchor="mm")

    def alert_pill(self, d: ImageDraw.ImageDraw, right: int, cy: int, alerts: list) -> int:
        a = alerts[0]
        text = (a.event + (f" +{len(alerts) - 1}" if len(alerts) > 1 else "")).upper()
        f = pick(text, 22, "xb", True)
        text = fit(d, text, f, 300)
        tw = int(d.textlength(text, font=f))
        x0 = right - tw - 70
        color = self.alert_color(a.severity)
        dark = _mix(color, (0, 0, 0), 0.38)
        paste(d, color, (x0, cy - 22), para(right - x0, 44, 10))
        paste(d, dark, (x0, cy - 22), para(52, 44, 10))
        R.warn_icon(d, x0 + 25, cy + 1, 22, (255, 255, 255), dark)
        _text(d, (x0 + 56, cy), text, font=f, fill=(255, 255, 255), anchor="lm")
        return int(x0)

    def render_weather(self, weather: Weather | None, now: datetime, message: str, alerts: list) -> Image.Image:
        img = self.canvas("weather")
        d = ImageDraw.Draw(img)
        hh = self.HEADER_H
        title_f = df(44, "black")
        _text(d, (24, hh // 2 + 1), "WEATHER", font=title_f, fill=self.INK, anchor="lm")
        self.button(d, CLOSE, "Back", 22, hot=True)
        self.header_rule(d)
        right = CLOSE[0] - 20
        if alerts:
            right = self.alert_pill(d, right, (CLOSE[1] + CLOSE[3]) // 2, alerts) - 16
        if weather is None:
            text = message or "No forecast yet"
            f = pick(text, 28, "bm")
            for i, line in enumerate(wrap(d, text, f, W - 200, 4)):
                _text(d, (W // 2, 340 + i * 42), line, font=f, fill=self.MUTED, anchor="mm")
            return img
        sub = f"{weather.place}, as of {fmt_time(weather.fetched)}" if weather.place else f"As of {fmt_time(weather.fetched)}"
        if now - weather.fetched > timedelta(hours=2):
            sub += f" on {weather.fetched:%b} {weather.fetched.day} (no newer forecast could be fetched)"
        x = 24 + d.textlength("WEATHER", font=title_f) + 22
        sub_f = pick(sub, 21, "bs")
        _text(d, (x, hh // 2 + 3), fit(d, sub, sub_f, right - x), font=sub_f, fill=(214, 220, 250), anchor="lm")
        today = now.date()
        shown = [h.temp for day in (today, today + timedelta(days=1)) for h in weather.on(day)]
        lo_t, hi_t = (min(shown), max(shown)) if shown else (0.0, 1.0)
        self.weather_day(d, hh, "Today", today, weather, now, lo_t, hi_t)
        d.line((0, hh + 362, W, hh + 362), fill=self.RULE, width=2)
        self.weather_day(d, hh + 362, "Tomorrow", today + timedelta(days=1), weather, now, lo_t, hi_t)
        return img

    # ---- screensaver ----

    def saver_alert(self, d: ImageDraw.ImageDraw, cx: float, y: float, alerts: list, now: datetime) -> float:
        color = _mix(self.alert_color(alerts[0].severity), self.NIGHT_INK, 0.2)
        color = _mix(color, (0, 0, 0), 0.25)
        text = self.alert_text(alerts, now).upper()
        f = pick(text, 34, "xb", True)
        text = fit(d, text, f, 820)
        tw = d.textlength(text, font=f)
        R.warn_icon(d, cx - tw / 2 - 26, y, 32, color, self.NIGHT_BG)
        _text(d, (cx + 22, y), text, font=f, fill=color, anchor="mm")
        return y + 62

    def render_saver(self, now: datetime, events: list[Event], weather: Weather | None, alerts: list) -> Image.Image:
        img = self.canvas("saver")
        d = ImageDraw.Draw(img)
        cx = W // 2 + ((now.minute * 7 + now.hour * 3) % 21 - 10) * 12
        top = 96 + ((now.minute * 11 + now.hour) % 9 - 4) * 12 - (30 if alerts else 0)

        clock, suffix = f"{now.hour % 12 or 12}:{now.minute:02d}", "AM" if now.hour < 12 else "PM"
        clock_f, suffix_f = df(236, "black"), df(60, "xb")
        cw, sw = d.textlength(clock, font=clock_f), d.textlength(suffix, font=suffix_f)
        x = cx - (cw + 18 + sw) / 2
        _text(d, (x, top + 104), clock, font=clock_f, fill=self.NIGHT_INK, anchor="lm")
        _text(d, (x + cw + 18, top + 160), suffix, font=suffix_f, fill=self.NIGHT_MUTED, anchor="lm")
        paste(d, self.NIGHT_ACCENT, (x + 10, top + 214), para(int(cw + sw + 8), 8, 6))
        _text(d, (cx, top + 262), f"{now:%A}  {now:%B} {now.day}".upper(), font=df(50, "xb"), fill=self.NIGHT_INK,
              anchor="mm")

        y = top + 340
        hour = weather.at(now) if weather else None
        if hour:
            text = f"{_temp(hour.temp)}  {describe(hour.code)[1]}"
            info = weather.days.get(now.date())
            if info:
                text += f"   High {_temp(info.high)}  Low {_temp(info.low)}"
            f = df(34, "bm")
            tw = d.textlength(text, font=f)
            self.icon(d, cx - tw / 2 - 12, y, 56, describe(hour.code)[0], hour.is_day, self.NIGHT_BG)
            _text(d, (cx + 34, y), text, font=f, fill=self.NIGHT_MUTED, anchor="mm")
            y += 74
        if alerts:
            y = self.saver_alert(d, cx, y, alerts, now)

        lead_f, title_f = df(32, "xb"), self.f(28)
        for ev, lead in self.coming(now, events, 2 if alerts else 3):
            color = _mix(lift(self.color(ev), 120), self.NIGHT_INK, 0.3)
            lead = lead.upper()
            lw = d.textlength(lead, font=lead_f)
            title = fit(d, ev.title, title_f, 860 - lw - 18)
            x = cx - (lw + 18 + d.textlength(title, font=title_f)) / 2
            _text(d, (x, y), lead, font=lead_f, fill=color, anchor="lm")
            _text(d, (x + lw + 18, y), title, font=title_f, fill=self.NIGHT_MUTED, anchor="lm")
            y += 46
        return img
