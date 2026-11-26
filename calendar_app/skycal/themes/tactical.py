"""Tactical HUD: a sci-fi game menu. Deep navy-to-black background, condensed upper-case
labels (Rajdhani), thin cyan rules, chamfered panels with corner brackets, a lit "today"
column and event plates lit by their own colour.

Everything costly (gradients with dither, glows, stripes) is built once and kept; drawing a
screen only pastes kept masks with ImageDraw.bitmap() (an alpha blend in C) and draws text.
User text (titles, places, notes) is drawn in Barlow when Barlow has every letter of it, else
in the system font (DejaVu on the frame), so any language shows.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .. import alerts as al
from .. import render as R
from ..feed import Event, on_day
from ..render import CLOSE, H, NAV, W, _mix, _temp, _text, fit, fmt_time, fmt_until, fmt_when, wrap
from ..weather import Weather, describe
from .original import Original

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
DEEP = (6, 12, 22)                  # what event tints are mixed toward
BG_TOP, BG_BOT = (13, 24, 42), (3, 6, 12)
HEAD_TOP, HEAD_BOT = (16, 31, 54), (8, 17, 31)
PANEL_TOP, PANEL_BOT = (15, 29, 50), (6, 12, 23)
EDGE = (46, 96, 130)                # outlines of panels and buttons
GRID = (24, 42, 62)                 # lines between the day columns
BTN = (11, 26, 45)

_kept: dict = {}                    # (kind, ...) -> built image or mask


def _keep(key, build):
    img = _kept.get(key)
    if img is None:
        if len(_kept) > 300:        # heights of cards vary: never grow without end
            _kept.clear()
        img = _kept[key] = build()
    return img


def _ramp(size: tuple, a: int, b: int, horizontal: bool = False) -> Image.Image:
    """An L picture going from `a` (top or left) to `b`."""
    g = Image.linear_gradient("L")
    if horizontal:
        g = g.transpose(Image.Transpose.TRANSPOSE)          # dark at the left
    g = g.resize(size, Image.Resampling.BILINEAR)
    return g.point([round(a + (b - a) * v / 255) for v in range(256)])


def _gradient(size: tuple, top: tuple, bot: tuple, grain: float = 3.0) -> Image.Image:
    """A vertical RGB gradient with a little noise in it, so the 16-bit panel does not band."""
    v = Image.linear_gradient("L").resize(size, Image.Resampling.BILINEAR)
    img = Image.merge("RGB", [v.point([round(t + (b - t) * i / 255) for i in range(256)]) for t, b in zip(top, bot)])
    if grain:       # a tile of noise repeated (effect_noise over the whole picture is slow on the frame)
        tile = _keep(("grain", grain), lambda: Image.effect_noise((256, 256), grain).convert("L"))
        n = Image.new("L", size)
        for x in range(0, size[0], 256):
            for y in range(0, size[1], 256):
                n.paste(tile, (x, y))
        img = ImageChops.add(img, Image.merge("RGB", (n, n, n)), 1.0, -128)
    return img


def _glow(w: int, h: int, spread: int, strength: float) -> Image.Image:
    """Mask of a soft light around a w x h box; the box sits at (spread, spread)."""
    def build():
        m = Image.new("L", (w + 2 * spread, h + 2 * spread), 0)
        ImageDraw.Draw(m).rectangle((spread, spread, spread + w - 1, spread + h - 1), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(spread / 2))
        return m.point([round(v * strength) for v in range(256)])
    return _keep(("glow", w, h, spread, strength), build)


def _blob(w: int, h: int, strength: float) -> Image.Image:
    def build():
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).ellipse((w * 0.22, h * 0.22, w * 0.78, h * 0.78), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(min(w, h) / 6))
        return m.point([round(v * strength) for v in range(256)])
    return _keep(("blob", w, h, strength), build)


def _stripes(w: int, h: int, step: int = 14) -> Image.Image:
    def build():
        m = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(m)
        for x in range(-h, w + step, step):
            d.polygon([(x, h), (x + step // 2, h), (x + step // 2 + h, 0), (x + h, 0)], fill=255)
        return m
    return _keep(("stripes", w, h, step), build)


def _chamfer(box: tuple, cut: int) -> list:
    """Corners cut off at top right and bottom left."""
    x0, y0, x1, y1 = box
    return [(x0, y0), (x1 - cut, y0), (x1, y0 + cut), (x1, y1), (x0 + cut, y1), (x0, y1 - cut)]


def _brackets(d: ImageDraw.ImageDraw, box: tuple, color: tuple, length: int = 12, width: int = 2,
              corners: str = "tl br") -> None:
    x0, y0, x1, y1 = box
    k = width - 1
    if "tl" in corners:
        d.rectangle((x0, y0, x0 + length, y0 + k), fill=color)
        d.rectangle((x0, y0, x0 + k, y0 + length), fill=color)
    if "tr" in corners:
        d.rectangle((x1 - length, y0, x1, y0 + k), fill=color)
        d.rectangle((x1 - k, y0, x1, y0 + length), fill=color)
    if "bl" in corners:
        d.rectangle((x0, y1 - k, x0 + length, y1), fill=color)
        d.rectangle((x0, y1 - length, x0 + k, y1), fill=color)
    if "br" in corners:
        d.rectangle((x1 - length, y1 - k, x1, y1), fill=color)
        d.rectangle((x1 - k, y1 - length, x1, y1), fill=color)


_notdef: dict = {}
_has: dict = {}


def _covers(name: str, text: str) -> bool:
    """Whether the bundled font `name` has every letter of `text` (a missing one would be a box)."""
    key = (name, text)
    ok = _has.get(key)
    if ok is None:
        probe = R.font_file(name, 32)
        if name not in _notdef:
            _notdef[name] = (probe.getbbox("\U0010fffd"), probe.getlength("\U0010fffd"))
        ok = True
        for ch in set(text):
            if ch.isspace():
                continue
            ck = (name, ch)
            got = _has.get(ck)
            if got is None:
                got = _has[ck] = (probe.getbbox(ch), probe.getlength(ch)) != _notdef[name]
            if not got:
                ok = False
                break
        if len(_has) > 5000:
            _has.clear()
        _has[key] = ok
    return ok


class Tactical(Original):
    name = "tactical"
    label = "Tactical HUD"

    BG = (8, 15, 27)                 # middle of the gradient: what icons cut their moon out of
    INK = (226, 237, 246)
    MUTED = (126, 148, 170)
    FAINT = (64, 80, 98)
    RULE = (34, 60, 86)
    TODAY_BG = (12, 32, 54)
    ACCENT = (70, 216, 255)
    WARN = (255, 176, 64)
    CAL_COLORS = [(70, 160, 255), (60, 210, 130), (255, 140, 60), (176, 120, 255), (255, 84, 132), (150, 166, 190)]
    NIGHT_BG = (0, 0, 0)
    NIGHT_INK = (160, 184, 198)
    NIGHT_MUTED = (90, 110, 124)
    NIGHT_ACCENT = (20, 58, 72)
    ICON_COLORS = ((255, 192, 64), (172, 186, 222), (150, 166, 188), (72, 172, 255), (172, 216, 255), (255, 212, 64))
    TEMP_LINE, TEMP_DOT = (255, 168, 70), (255, 146, 40)
    SEVERITY_COLORS = {"Extreme": (236, 30, 72), "Severe": (240, 58, 48), "Moderate": (255, 128, 30),
                       "Minor": (240, 188, 40), "Unknown": (120, 136, 152)}
    DIM = R.dim_table((2, 5, 10), 0.72)

    TITLE_SIZE, LABEL_SIZE, LINE_H, LABEL_H = 18, 17, 22, 19

    # ---- fonts ----

    def f(self, size: int, bold: bool = False):
        """Rajdhani, for the theme's own labels (upper case, digits)."""
        return R.font_file("Rajdhani-Bold.ttf" if bold else "Rajdhani-SemiBold.ttf", size)

    def t(self, size: int, bold: bool, text: str):
        """Rajdhani if it has every letter of `text`, else the system font."""
        name = "Rajdhani-Bold.ttf" if bold else "Rajdhani-SemiBold.ttf"
        return R.font_file(name, size) if _covers(name, text) else R.font(size, bold)

    def u(self, size: int, bold: bool, text: str):
        """For user text: Barlow if it has every letter of `text`, else the system font."""
        name = "Barlow-Bold.ttf" if bold else "Barlow-SemiBold.ttf"
        return R.font_file(name, size) if _covers(name, text) else R.font(size, bold)

    def bg_at(self, y: float) -> tuple:
        return _mix(BG_TOP, BG_BOT, max(0.0, min(1.0, y / H)))

    @staticmethod
    def _ring(size: int) -> tuple[int, int]:
        """(diameter, room on each side) of the drawn degree sign for a font of `size`."""
        return max(5, round(size * 0.2)), max(1, round(size * 0.05))

    def deg_width(self, d: ImageDraw.ImageDraw, text: str, font) -> float:
        pieces = text.split("°")
        ring, gap = self._ring(font.size)
        return sum(d.textlength(p, font=font) for p in pieces) + (ring + 2 * gap) * (len(pieces) - 1)

    def deg(self, d: ImageDraw.ImageDraw, xy: tuple, text: str, font, fill: tuple, anchor: str = "mm") -> None:
        """_text() with every "°" drawn as a ring (Rajdhani's is tiny and set far from the digits)."""
        if "°" not in text:
            _text(d, xy, text, font=font, fill=fill, anchor=anchor)
            return
        x, y = xy
        total = self.deg_width(d, text, font)
        x = x - total / 2 if anchor[0] == "m" else (x - total if anchor[0] == "r" else x)
        va = "l" + anchor[1]
        top = y + font.getbbox("8", anchor=va)[1]
        ring, gap = self._ring(font.size)
        pieces = text.split("°")
        for i, piece in enumerate(pieces):
            if piece.strip():
                _text(d, (round(x), y), piece, font=font, fill=fill, anchor=va)
            x += d.textlength(piece, font=font)
            if i < len(pieces) - 1:
                d.ellipse((round(x + gap), top, round(x + gap) + ring, top + ring), outline=fill,
                          width=max(2, round(font.size / 13)))
                x += ring + 2 * gap

    # ---- backgrounds ----

    def canvas(self, screen: str) -> Image.Image:
        if screen == "saver":
            return Image.new("RGB", (W, H), self.NIGHT_BG)

        def build():
            img = _gradient((W, H), BG_TOP, BG_BOT)
            # a faint cold light from the upper left
            light = _blob(150, 90, 0.10).resize((1500, 900), Image.Resampling.BILINEAR)
            img.paste(Image.new("RGB", light.size, (40, 110, 160)), (-420, -200), light)
            img.paste(_gradient((W, self.HEADER_H), HEAD_TOP, HEAD_BOT), (0, 0))
            return img
        return _keep(("canvas",), build).copy()

    def panel(self, d: ImageDraw.ImageDraw, box: tuple, fill=BTN, edge=EDGE, accent=None, cut: int = 10,
              bracket: int = 10) -> None:
        d.polygon(_chamfer(box, cut), fill=fill, outline=edge)
        _brackets(d, box, accent or self.ACCENT, bracket, 2)

    def chevron(self, d: ImageDraw.ImageDraw, cx: float, cy: float, size: float, right: bool, color: tuple,
                width: int = 4) -> None:
        s = size / 2
        k = 1 if right else -1
        d.line([(cx - k * s / 2, cy - s), (cx + k * s / 2, cy), (cx - k * s / 2, cy + s)], fill=color, width=width,
               joint="curve")

    # ---- header ----

    def button(self, d: ImageDraw.ImageDraw, box: tuple, label: str, size: int, lift: int = 0) -> None:
        x0, y0, x1, y1 = box
        self.panel(d, box)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if label in ("‹", "›"):
            self.chevron(d, cx, cy, 20, label == "›", self.ACCENT)
            return
        _text(d, (cx, cy + 1), label.upper(), font=self.t(size + 2, True, label), fill=self.INK, anchor="mm")

    def weather_button(self, d: ImageDraw.ImageDraw, hour) -> None:
        x0, y0, x1, y1 = NAV["weather"]
        if hour:
            self.panel(d, NAV["weather"])
            self.icon(d, x0 + 44, (y0 + y1) // 2, 38, describe(hour.code)[0], hour.is_day, BTN)
            self.deg(d, (x0 + 80, (y0 + y1) // 2 + 1), _temp(hour.temp), self.f(32, True), self.INK, "lm")
        else:
            self.button(d, NAV["weather"], "Weather", 20)

    def title_block(self, d: ImageDraw.ImageDraw, caption: str, title: str) -> int:
        """The accent bar, the small caption and the big title at the left of a header; returns its right edge."""
        d.rectangle((20, 15, 23, 61), fill=self.ACCENT)
        _text(d, (36, 22), caption, font=self.f(16, True), fill=self.ACCENT, anchor="lm")
        tf = self.t(36, True, title)
        _text(d, (35, 50), title, font=tf, fill=self.INK, anchor="lm")
        return int(35 + d.textlength(title, font=tf))

    def header(self, d: ImageDraw.ImageDraw, start: date, now: datetime, note: str, stale: bool,
               weather: Weather | None) -> None:
        week = (start + timedelta(days=1)).isocalendar()[1]
        caption = f"WEEK {week:02d}" + ("  //  THIS WEEK" if start <= now.date() < start + timedelta(days=7) else "")
        end = self.title_block(d, caption, self.week_title(start).upper())
        right = NAV["settings"][0] - 18
        if note:
            room = right - end - 24
            self.clock(d, right, 26, 30, now)
            nf = self.t(17, True, note)
            _text(d, (right, 55), fit(d, note.upper(), nf, room), font=nf, fill=self.WARN if stale else self.MUTED,
                  anchor="rm")
        else:
            self.clock(d, right, self.HEADER_H // 2 + 1, 38, now)
        self.settings_button(d)
        self.weather_button(d, weather.at(now) if weather else None)
        self.button(d, NAV["prev"], "‹", 36, 4)
        self.button(d, NAV["today"], "Today", 20)
        self.button(d, NAV["next"], "›", 36, 4)

    def clock(self, d: ImageDraw.ImageDraw, right: int, cy: int, size: int, now: datetime) -> None:
        """"2:20 PM", ending at x = `right`, the AM/PM small."""
        suffix = "AM" if now.hour < 12 else "PM"
        sf = self.f(size // 2, True)
        sw = d.textlength(suffix, font=sf)
        _text(d, (right, cy + size // 5), suffix, font=sf, fill=self.ACCENT, anchor="rs")
        _text(d, (right - sw - 5, cy + size // 5), f"{now.hour % 12 or 12}:{now.minute:02d}", font=self.f(size, True),
              fill=self.INK, anchor="rs")

    def header_rule(self, d: ImageDraw.ImageDraw) -> None:
        hh = self.HEADER_H
        d.line((0, hh, W, hh), fill=(30, 64, 92), width=1)
        d.bitmap((0, hh - 1), _keep(("hr",), lambda: _ramp((520, 3), 255, 0, horizontal=True)), fill=self.ACCENT)

    # ---- week ----

    def _col_mask(self, w: int, h: int) -> Image.Image:
        def build():
            m = _ramp((w, h), 46, 8)
            top = Image.new("L", (w, h), 0)
            ImageDraw.Draw(top).rectangle((0, 0, w, 10), fill=255)
            top = top.filter(ImageFilter.GaussianBlur(8)).point([v * 0.55 for v in range(256)])
            edge = Image.new("L", (w, h), 0)
            ImageDraw.Draw(edge).rectangle((0, 0, w - 1, h - 1), outline=255, width=2)
            edge = edge.filter(ImageFilter.GaussianBlur(5)).point([v * 0.45 for v in range(256)])
            return ImageChops.add(ImageChops.lighter(m, edge), top)
        return _keep(("col", w, h), build)

    def day_head(self, d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, today: date) -> None:
        is_today = day == today
        top = self.HEADER_H
        cx = (x0 + x1) // 2
        if is_today:
            d.bitmap((x0, top + 1), self._col_mask(x1 - x0, H - top - 1), fill=self.ACCENT)
            d.rectangle((x0, top + 1, x1 - 1, top + 3), fill=self.ACCENT)
            d.bitmap((cx - 60, top + 22), _blob(120, 72, 0.55), fill=self.ACCENT)
            _brackets(d, (cx - 38, top + 34, cx + 38, top + 78), self.ACCENT, 9, 2, "tl tr bl br")
        weekday = f"{day:%a}".upper()
        if is_today:
            weekday = f"{weekday} · TODAY"
        tone = self.ACCENT if is_today else (self.FAINT if day < today else self.MUTED)
        _text(d, (cx, top + 21), weekday, font=self.t(18, True, weekday), fill=tone, anchor="mm")
        num = str(day.day) if day.day != 1 else f"{day:%b} 1".upper()
        _text(d, (cx, top + 57), num, font=self.t(40, True, num),
              fill=WHITE if is_today else (self.MUTED if day < today else self.INK), anchor="mm")

    def plate_height(self, label: str, lines: list) -> int:
        return 9 + (self.LABEL_H if label else 0) + self.LINE_H * len(lines) + 7

    def plate_colors(self, color: tuple, all_day: bool, over: bool, current: bool) -> tuple:
        """(fill, edge bar, outline, title ink, label ink) of a plate."""
        if over:
            return (_mix(color, DEEP, 0.86), _mix(color, DEEP, 0.55), _mix(color, DEEP, 0.78), self.MUTED,
                    _mix(color, self.MUTED, 0.55))
        if all_day:
            return (_mix(color, DEEP, 0.5), _mix(color, WHITE, 0.35), _mix(color, WHITE, 0.15), WHITE,
                    _mix(color, WHITE, 0.7))
        if current:
            return (_mix(color, DEEP, 0.62), _mix(color, WHITE, 0.3), _mix(color, WHITE, 0.3), WHITE,
                    _mix(color, WHITE, 0.55))
        return _mix(color, DEEP, 0.78), color, _mix(color, DEEP, 0.4), self.INK, _mix(color, WHITE, 0.4)

    def plate_glow(self, d: ImageDraw.ImageDraw, box: tuple, ev: Event, over: bool, current: bool) -> None:
        if over:
            return
        left, y, right, y1 = box
        g = 10 if current else 7
        d.bitmap((left - g, y - g), _glow(right - left + 1, y1 - y + 1, g, 0.75 if current else 0.38),
                 fill=self.color(ev))

    def plate(self, d: ImageDraw.ImageDraw, box: tuple, ev: Event, label: str, lines: list, over: bool, current: bool,
              back: tuple, fnt=None) -> None:
        left, y, right, y1 = box
        color = self.color(ev)
        fill, bar, outline, ink, sub = self.plate_colors(color, ev.all_day, over, current)
        d.polygon([(left, y), (right - 9, y), (right, y + 9), (right, y1), (left, y1)], fill=fill, outline=outline)
        if current:
            d.line([(left, y1), (right, y1), (right, y + 9), (right - 9, y)], fill=outline, width=2)
        d.rectangle((left, y, left + 3, y1), fill=bar)
        fnt = fnt or self.u(self.TITLE_SIZE, True, ev.title)
        ty = y + 6
        if label:
            if current:
                d.polygon([(right - 16, ty + 5), (right - 8, ty + 10), (right - 16, ty + 15)], fill=sub)
            label = label.upper()
            _text(d, (left + 12, ty), label, font=self.f(self.LABEL_SIZE, True), fill=sub)
            ty += self.LABEL_H
        for line in lines:
            _text(d, (left + 12, ty), line, font=fnt, fill=ink)
            ty += self.LINE_H

    def more(self, d: ImageDraw.ImageDraw, cx: int, bottom: int, count: int) -> None:
        text = f"+{count} MORE"
        f = self.f(18, True)
        half = d.textlength(text, font=f) / 2 + 14
        self.panel(d, (cx - half, bottom - 27, cx + half, bottom - 5), fill=BTN, edge=(52, 120, 150), cut=6, bracket=6)
        _text(d, (cx, bottom - 16), text, font=f, fill=self.ACCENT, anchor="mm")

    def day(self, d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, events: list[Event], now: datetime,
            hits: list | None, bottom: int = H) -> None:
        today = now.date()
        self.day_head(d, x0, x1, day, today)
        if hits is not None and events:
            hits.append((x0, self.HEADER_H, x1, self.HEADER_H + self.DAYHEAD_H, day))
        pad = self.PAD
        y = self.HEADER_H + self.DAYHEAD_H + pad + 2
        left, right = x0 + pad + 1, x1 - pad - 1
        text_w = right - left - 18
        back = self.TODAY_BG if day == today else self.BG
        placed = []
        for i, ev in enumerate(events):
            over = ev.end <= now and (ev.all_day or ev.start < now)
            label, current = self.event_label(ev, day, now)
            fnt = self.u(self.TITLE_SIZE, True, ev.title)
            lines = wrap(d, ev.title, fnt, text_w, 3)
            h = self.plate_height(label, lines)
            if y + h > bottom - 30 and i < len(events) - 1 or y + h > bottom - 4:
                self.more(d, (x0 + x1) // 2, bottom, len(events) - i)
                if hits is not None:
                    hits.append((x0, y, x1, bottom, day))
                break
            placed.append(((left, y, right, y + h), ev, label, lines, over, current, fnt))
            if hits is not None:
                hits.append((left, y, right, y + h, ev))
            y += h + pad
        for box, ev, _, _, over, current, _ in placed:       # all the light first, so none lies on a plate
            self.plate_glow(d, box, ev, over, current)
        for box, ev, label, lines, over, current, fnt in placed:
            self.plate(d, box, ev, label, lines, over, current, back, fnt)

    def grid(self, d: ImageDraw.ImageDraw, edges: list, bottom: int) -> None:
        top, mid = self.HEADER_H + 1, self.HEADER_H + self.DAYHEAD_H
        for x in edges[1:-1]:
            d.line((x, top + 10, x, bottom - 10), fill=GRID, width=1)
        d.line((0, mid, W, mid), fill=self.RULE, width=1)
        for x in edges[1:-1]:
            d.line((x - 5, mid, x + 5, mid), fill=(80, 150, 180), width=1)
            d.line((x, mid - 5, x, mid + 5), fill=(80, 150, 180), width=1)

    def message_box(self, d: ImageDraw.ImageDraw, message: str) -> None:
        f = self.u(24, False, message)
        lines = wrap(d, message, f, 700, 4)
        half = 40 + 17 * len(lines)
        box = (W // 2 - 400, 460 - half, W // 2 + 400, 460 + half)
        self.panel(d, box, fill=(9, 20, 36), cut=16, bracket=18)
        _text(d, (box[0] + 30, box[1] + 18), "SYSTEM MESSAGE", font=self.f(15, True), fill=self.ACCENT, anchor="lm")
        for i, line in enumerate(lines):
            _text(d, (W // 2, 470 - 17 * (len(lines) - 1) + 34 * i), line, font=f, fill=self.INK, anchor="mm")

    def alert_band(self, d: ImageDraw.ImageDraw, box: tuple, alerts: list, now: datetime) -> None:
        x0, y0, x1, y1 = box
        a = alerts[0]
        color = self.alert_color(a.severity)
        d.bitmap((x0, y0 - 16), _keep(("up", x1 - x0), lambda: _ramp((x1 - x0, 16), 0, 120)), fill=color)
        d.rectangle(box, fill=_mix(color, BLACK, 0.74))
        d.rectangle((x0, y0, x1, y0 + 1), fill=color)
        cy = (y0 + y1) // 2 + 1
        # the striped tab with the warning sign
        tab = 78
        d.polygon([(x0, y0), (x0 + tab, y0), (x0 + tab - 14, y1), (x0, y1)], fill=color)
        d.bitmap((x0, y0 + 2), _keep(("tabstripes", tab, y1 - y0 - 2), lambda: ImageChops.multiply(
            _stripes(tab, y1 - y0 - 2), self._tab_shape(tab, y1 - y0 - 2))), fill=_mix(color, BLACK, 0.3))
        R.warn_icon(d, x0 + 32, cy - 1, 28, WHITE, color)
        hint_f = self.f(17, True)
        hint = "TAP FOR DETAILS"
        hint_w = d.textlength(hint, font=hint_f)
        soft = _mix(color, WHITE, 0.6)
        self.chevron(d, x1 - 24, cy, 12, True, soft, 2)
        _text(d, (x1 - 36, cy), hint, font=hint_f, fill=soft, anchor="rm")
        x = x0 + tab + 10
        room = x1 - 36 - hint_w - 30 - x
        name = a.event.upper()
        nf = self.t(24, True, name)
        name = fit(d, name, nf, room)
        _text(d, (x, cy), name, font=nf, fill=WHITE, anchor="lm")
        x += d.textlength(name, font=nf) + 14
        rest = al.until(a, now).upper()
        if len(alerts) > 1:
            rest = f"{rest}   +{len(alerts) - 1} MORE".strip()
        if rest and x1 - 36 - hint_w - 30 - x > 60:
            rf = self.t(20, True, rest)
            _text(d, (x, cy), fit(d, rest, rf, x1 - 36 - hint_w - 30 - x), font=rf, fill=_mix(color, WHITE, 0.72), anchor="lm")

    @staticmethod
    def _tab_shape(w: int, h: int) -> Image.Image:
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).polygon([(0, 0), (w, 0), (w - 14, h), (0, h)], fill=255)
        return m

    # ---- cards ----

    def card(self, base: Image.Image, height: int, width: int = 820) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple]:
        img = base.point(self.DIM)
        height = min(height, H - 40)
        box = ((W - width) // 2, (H - height) // 2, (W + width) // 2, (H + height) // 2)
        x0, y0, x1, y1 = box
        w, h = x1 - x0 + 1, y1 - y0 + 1
        d = ImageDraw.Draw(img)
        d.bitmap((x0 - 24, y0 - 24), _glow(w, h, 24, 0.22), fill=self.ACCENT)
        tex = _keep(("panel", w), lambda: _gradient((w, H), PANEL_TOP, PANEL_BOT))

        def shape():
            m = Image.new("L", (w, h), 0)
            ImageDraw.Draw(m).polygon(_chamfer((0, 0, w - 1, h - 1), 22), fill=255)
            return m
        img.paste(tex.crop((0, 0, w, h)), (x0, y0), _keep(("shape", w, h), shape))
        d.polygon(_chamfer(box, 22), outline=EDGE)
        _brackets(d, (x0 - 4, y0 - 4, x1 + 4, y1 + 4), self.ACCENT, 26, 3)
        return img, d, box

    def card_strip(self, d: ImageDraw.ImageDraw, box: tuple, color: tuple) -> None:
        x0, y0, x1, y1 = box
        h = y1 - y0 - 26
        d.bitmap((x0 + 5, y0 + 1), _keep(("strip", h), lambda: _ramp((70, h), 70, 0, horizontal=True)), fill=color)
        d.rectangle((x0 + 1, y0 + 1, x0 + 6, y1 - 26), fill=color)

    def card_hint(self, d: ImageDraw.ImageDraw, box: tuple, text: str) -> None:
        x0, y0, x1, y1 = box
        f = self.t(16, True, text)
        text = text.upper()
        cx, cy = (x0 + x1) // 2, y1 - 26
        half = d.textlength(text, font=f) / 2
        d.line((cx - half - 60, cy, cx - half - 14, cy), fill=self.RULE, width=1)
        d.line((cx + half + 14, cy, cx + half + 60, cy), fill=self.RULE, width=1)
        _text(d, (cx, cy), text, font=f, fill=self.MUTED, anchor="mm")

    def section(self, d: ImageDraw.ImageDraw, x: int, y: int, right: int, label: str, color: tuple | None = None) -> None:
        """A small upper-case label with a thin rule running on to `right`."""
        f = self.f(16, True)
        _text(d, (x, y + 8), label, font=f, fill=color or self.ACCENT, anchor="lm")
        lx = x + d.textlength(label, font=f) + 12
        d.line((lx, y + 8, right, y + 8), fill=self.RULE, width=1)

    def render_detail(self, base: Image.Image, ev: Event, now: datetime) -> Image.Image:
        probe = ImageDraw.Draw(base)
        width = 820
        text_w = width - 46 - 40
        title_f = self.u(34, True, ev.title)
        when_s = fmt_when(ev)
        when_f = self.u(23, False, when_s)
        body_where = self.u(21, False, ev.location or "")
        body_notes = self.u(21, False, ev.description or "")
        title = wrap(probe, ev.title, title_f, text_w, 3)
        when = wrap(probe, when_s, when_f, text_w, 2)
        until = fmt_until(ev, now)
        where = wrap(probe, ev.location, body_where, text_w, 3) if ev.location else []
        fixed = (30 + 30 + 42 * len(title) + 10 + 30 * len(when) + (34 if until else 0)
                 + (22 + 26 + 28 * len(where) if where else 0) + 64)
        room = (H - 40 - fixed - 48) // 28
        notes = wrap(probe, ev.description, body_notes, text_w, max(1, min(12, room))) if ev.description else []
        img, d, box = self.card(base, fixed + (22 + 26 + 28 * len(notes) if notes else 0), width)
        x0, y0, x1, y1 = box
        color = self.color(ev)
        self.card_strip(d, box, color)
        x, y = x0 + 46, y0 + 28
        d.rectangle((x, y + 3, x + 10, y + 13), fill=color)
        _text(d, (x + 20, y + 8), "ALL-DAY EVENT" if ev.all_day else "EVENT", font=self.f(16, True),
              fill=_mix(color, WHITE, 0.45), anchor="lm")
        y += 32
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=self.INK)
            y += 42
        y += 10
        for line in when:
            _text(d, (x, y), line, font=when_f, fill=_mix(self.INK, self.MUTED, 0.3))
            y += 30
        if until:
            ended = until == "Ended"
            tone = self.MUTED if ended else _mix(color, WHITE, 0.4)
            uf = self.t(20, True, until)
            u = until.upper()
            tw = d.textlength(u, font=uf)
            self.panel(d, (x, y + 4, x + tw + 24, y + 30), fill=_mix(color, DEEP, 0.8) if not ended else BTN,
                       edge=_mix(tone, DEEP, 0.4), accent=tone, cut=6, bracket=5)
            _text(d, (x + 12, y + 18), u, font=uf, fill=tone, anchor="lm")
            y += 34
        for label, lines, bf in (("WHERE", where, body_where), ("NOTES", notes, body_notes)):
            if lines:
                y += 22
                self.section(d, x, y, x1 - 40, label)
                y += 26
                for line in lines:
                    _text(d, (x, y), line, font=bf, fill=self.INK)
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
        img, d, box = self.card(base, 92 + row_h * max(rows, 1) + 56)
        x0, y0, x1, y1 = box
        title = f"{day:%A}, {day:%B} {day.day}".upper()
        _text(d, (x0 + 36, y0 + 26), "DAY OVERVIEW" if day != now.date() else "TODAY", font=self.f(16, True),
              fill=self.ACCENT, anchor="lm")
        _text(d, (x0 + 36, y0 + 56), title, font=self.t(32, True, title), fill=self.INK, anchor="lm")
        count = f"{len(everything)} EVENT" + ("" if len(everything) == 1 else "S")
        _text(d, (x1 - 40, y0 + 58), count, font=self.f(19, True), fill=self.MUTED, anchor="rm")
        lo = datetime.combine(day, time.min, tz)
        y = y0 + 92
        if not todays:
            _text(d, (x0 + 36, y + row_h // 2), "NOTHING SCHEDULED", font=self.f(22, True), fill=self.MUTED, anchor="lm")
        for i, ev in enumerate(todays[:rows]):
            if i == rows - 1 and len(todays) > rows:
                d.line((x0 + 30, y, x1 - 30, y), fill=self.RULE, width=1)
                more = f"SHOW {len(todays) - i} MORE"
                mf = self.f(20, True)
                _text(d, (x0 + 36, y + row_h // 2), more, font=mf, fill=self.ACCENT, anchor="lm")
                self.chevron(d, x0 + 50 + d.textlength(more, font=mf), y + row_h // 2, 12, True, self.ACCENT, 2)
                if hits is not None:
                    hits.append((x0, y, x1, y + row_h, first + i))
                break
            color = self.color(ev)
            over = ev.end <= now and (ev.all_day or ev.start < now)
            current = not ev.all_day and ev.start <= now < ev.end
            if current:
                d.rectangle((x0 + 30, y + 1, x1 - 30, y + row_h - 1), fill=_mix(color, PANEL_TOP, 0.84))
                _brackets(d, (x0 + 30, y + 1, x1 - 30, y + row_h - 1), _mix(color, WHITE, 0.3), 8, 2, "tr br")
            d.line((x0 + 30, y, x1 - 30, y), fill=self.RULE, width=1)
            d.rectangle((x0 + 36, y + 9, x0 + 39, y + row_h - 9), fill=_mix(color, PANEL_BOT, 0.6) if over else color)
            span = self.span(ev, lo).upper()
            _text(d, (x0 + 56, y + row_h // 2 + 1), span, font=self.t(21, True, span),
                  fill=self.FAINT if over else _mix(color, WHITE, 0.4), anchor="lm")
            tf = self.u(21, True, ev.title)
            _text(d, (x0 + 250, y + row_h // 2), fit(d, ev.title, tf, x1 - 40 - (x0 + 250)), font=tf,
                  fill=self.MUTED if over else self.INK, anchor="lm")
            if hits is not None:
                hits.append((x0, y, x1, y + row_h, ev))
            y += row_h
        self.card_hint(d, box, "Tap an event for details, or outside to close")
        return img

    def render_alerts(self, base: Image.Image, alerts: list, now: datetime, hits: list | None, index: int) -> Image.Image:
        a = alerts[index]
        width = 900
        probe = ImageDraw.Draw(base)
        text_w = width - 46 - 44
        title_f = self.u(32, True, a.event)
        when = self.alert_when(a, now)
        when_f = self.t(22, True, when)
        color = self.alert_color(a.severity)
        title = wrap(probe, a.event, title_f, text_w, 2)
        area_f = self.u(19, False, a.area or "")
        area = wrap(probe, a.area, area_f, text_w, 2) if a.area else []
        more = len(alerts) > 1
        fixed = 40 + 40 * len(title) + 8 + (30 if when else 0) + 34 + (24 * len(area) + 10 if area else 0) + 62 + (50 if more else 0)
        room = max(2, (H - 40 - fixed) // 26 - 2)
        what_s = a.description or a.headline
        what_f = self.u(19, False, what_s or "")
        what = wrap(probe, what_s, what_f, text_w, max(1, room - (4 if a.instruction else 0)))
        todo_f = self.u(19, False, a.instruction or "")
        todo = wrap(probe, a.instruction, todo_f, text_w, max(1, room - len(what) - 2)) if a.instruction and room - len(what) >= 3 else []
        body = 26 + 26 * len(what) + (14 + 26 + 26 * len(todo) if todo else 0)
        img, d, box = self.card(base, fixed + body, width)
        x0, y0, x1, y1 = box
        self.card_strip(d, box, color)
        sw = x1 - x0 - 60
        d.bitmap((x0 + 30, y0 + 1), _keep(("astripes", sw), lambda: _stripes(sw, 8, 16)), fill=color)
        x, y = x0 + 46, y0 + 40
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=self.INK)
            y += 40
        y += 8
        if when:
            _text(d, (x, y), when.upper(), font=when_f, fill=_mix(self.INK, self.MUTED, 0.25))
            y += 30
        sev = a.severity.upper()
        sf = self.f(17, True)
        sev_w = d.textlength(sev, font=sf)
        d.polygon(_chamfer((x, y + 4, x + sev_w + 24, y + 28), 6), fill=color)
        _text(d, (x + 12, y + 16), sev, font=sf, fill=BLACK if a.severity == "Minor" else WHITE, anchor="lm")
        if a.sender:
            snd = a.sender.upper()
            snf = self.t(17, True, snd)
            _text(d, (x + sev_w + 38, y + 16), fit(d, snd, snf, text_w - sev_w - 40), font=snf, fill=self.MUTED, anchor="lm")
        y += 34
        for line in area:
            _text(d, (x, y), line, font=area_f, fill=self.MUTED)
            y += 24
        y += 10 if area else 0
        for label, lines, bf in (("WHAT", what, what_f), ("WHAT TO DO", todo, todo_f)):
            if lines:
                y += 14 if label != "WHAT" else 0
                self.section(d, x, y, x1 - 40, label, _mix(color, WHITE, 0.35))
                y += 26
                for line in lines:
                    _text(d, (x, y), line, font=bf, fill=self.INK)
                    y += 26
        if more:
            ry = y1 - 62 - 46
            d.line((x0 + 30, ry, x1 - 30, ry), fill=self.RULE, width=1)
            nxt = (index + 1) % len(alerts)
            lf = self.f(19, True)
            _text(d, (x, ry + 24), "NEXT ALERT", font=lf, fill=self.ACCENT, anchor="lm")
            lx = x + d.textlength("NEXT ALERT", font=lf) + 12
            self.chevron(d, lx, ry + 24, 12, True, self.ACCENT, 2)
            txt = f"{alerts[nxt].event}  ({nxt + 1} of {len(alerts)})"
            nf = self.u(19, True, txt)
            _text(d, (lx + 14, ry + 24), fit(d, txt, nf, x1 - 40 - lx - 14), font=nf,
                  fill=_mix(self.alert_color(alerts[nxt].severity), WHITE, 0.4), anchor="lm")
            if hits is not None:
                hits.append((x0, ry, x1, ry + 46, nxt))
        self.card_hint(d, box, "Tap anywhere else to close")
        return img

    # ---- weather ----

    def weather_day(self, d: ImageDraw.ImageDraw, top: int, name: str, day: date, weather: Weather, now: datetime,
                    lo_t: float, hi_t: float) -> None:
        hours = [h for h in weather.on(day) if h.time.hour >= min(6, now.hour if day == now.date() else 6)]
        nm = name.upper()
        d.rectangle((24, top + 22, 27, top + 48), fill=self.ACCENT)
        _text(d, (38, top + 36), nm, font=self.f(30, True), fill=self.INK, anchor="lm")
        name_w = d.textlength(nm, font=self.f(30, True))
        ds = f"{day:%A}, {day:%B} {day.day}".upper()
        _text(d, (38 + name_w + 16, top + 37), ds, font=self.t(20, True, ds), fill=self.MUTED, anchor="lm")
        info = weather.days.get(day)
        if info:
            parts = [describe(info.code)[1].upper(), f"HIGH {_temp(info.high)}", f"LOW {_temp(info.low)}"]
            if info.sunrise and info.sunset:
                parts += [f"SUNRISE {fmt_time(info.sunrise)}", f"SUNSET {fmt_time(info.sunset)}"]
            s = "   ".join(parts).upper()
            self.deg(d, (W - 24, top + 37), s, self.t(21, True, s), self.INK, "rm")
        if not hours:
            _text(d, (W // 2, top + 190), "NO FORECAST FOR THESE HOURS", font=self.f(22, True), fill=self.MUTED, anchor="mm")
            return
        cw = (W - 40) / len(hours)
        span = max(hi_t - lo_t, 1.0)
        band_top, band_h = top + 176, 74
        points = []
        for i, h in enumerate(hours):
            cx = 20 + cw * (i + 0.5)
            past = h.time + timedelta(hours=1) <= now
            current = h.time <= now < h.time + timedelta(hours=1)
            back = self.bg_at(top + 126)
            if current:
                bx = (round(cx - cw / 2 + 2), top + 62, round(cx + cw / 2 - 2), top + 340)
                d.bitmap((bx[0], bx[1]), self._col_mask(bx[2] - bx[0], bx[3] - bx[1]), fill=self.ACCENT)
                d.rectangle((bx[0], bx[1], bx[2], bx[1] + 2), fill=self.ACCENT)
                _brackets(d, bx, self.ACCENT, 10, 2, "tl tr bl br")
                back = _mix(back, self.ACCENT, 0.12)
            _text(d, (cx, top + 84), "NOW" if current else fmt_time(h.time).upper(), font=self.f(19, True),
                  fill=self.ACCENT if current else (self.FAINT if past else self.MUTED), anchor="mm")
            self.icon(d, cx, top + 126, 44, describe(h.code)[0], h.is_day, back, fade=past)
            py = band_top + band_h - (h.temp - lo_t) / span * band_h
            points.append((cx, py, h, past))
            if h.precip is not None and h.precip >= 10 and not past:
                tone = self.ICON_COLORS[3] if h.precip >= 30 else self.MUTED
                d.polygon([(cx, top + 284), (cx - 5, top + 293), (cx + 5, top + 293)], fill=tone)
                d.ellipse((cx - 5, top + 288, cx + 5, top + 298), fill=tone)
                _text(d, (cx, top + 316), f"{h.precip}%", font=self.f(18, True), fill=tone, anchor="mm")
        for (ax, ay, _, a_past), (bx, by, _, _) in zip(points, points[1:]):
            d.line((ax, ay, bx, by), fill=self.RULE if a_past else self.TEMP_LINE, width=3)
        for cx, py, h, past in points:
            if past:
                d.ellipse((cx - 4, py - 4, cx + 4, py + 4), fill=self.FAINT)
            else:
                d.ellipse((cx - 6, py - 6, cx + 6, py + 6), fill=_mix(self.TEMP_DOT, BLACK, 0.55))
                d.ellipse((cx - 4, py - 4, cx + 4, py + 4), fill=self.TEMP_DOT)
            self.deg(d, (cx, py - 21), _temp(h.temp), self.f(22, True), self.FAINT if past else self.INK, "mm")

    def alert_pill(self, d: ImageDraw.ImageDraw, right: int, cy: int, alerts: list) -> int:
        a = alerts[0]
        text = a.event.upper() + (f" +{len(alerts) - 1}" if len(alerts) > 1 else "")
        f = self.t(19, True, text)
        tw = min(d.textlength(text, font=f), 300)
        x0 = right - tw - 58
        color = self.alert_color(a.severity)
        box = (x0, cy - 20, right, cy + 20)
        d.bitmap((int(x0) - 10, cy - 30), _glow(int(right - x0) + 1, 41, 10, 0.45), fill=color)
        d.polygon(_chamfer(box, 8), fill=_mix(color, BLACK, 0.62), outline=color)
        R.warn_icon(d, x0 + 24, cy, 22, color, _mix(color, BLACK, 0.62))
        _text(d, (x0 + 44, cy + 1), fit(d, text, f, 300), font=f, fill=WHITE, anchor="lm")
        return int(x0)

    def render_weather(self, weather: Weather | None, now: datetime, message: str, alerts: list) -> Image.Image:
        img = self.canvas("weather")
        d = ImageDraw.Draw(img)
        hh = self.HEADER_H
        self.button(d, CLOSE, "Back", 20)
        self.header_rule(d)
        right = CLOSE[0] - 20
        if alerts:
            right = self.alert_pill(d, right, (CLOSE[1] + CLOSE[3]) // 2, alerts) - 18
        if weather is None:
            self.title_block(d, "CONDITIONS", "WEATHER")
            f = self.u(26, False, message or "")
            for i, line in enumerate(wrap(d, message or "No forecast yet", f, W - 200, 4)):
                _text(d, (W // 2, 340 + i * 40), line, font=f, fill=self.MUTED, anchor="mm")
            return img
        sub = f"{weather.place}  //  AS OF {fmt_time(weather.fetched)}" if weather.place else f"AS OF {fmt_time(weather.fetched)}"
        if now - weather.fetched > timedelta(hours=2):
            sub += f" ON {weather.fetched:%b} {weather.fetched.day} (NO NEWER FORECAST)".upper()
        end = self.title_block(d, "CONDITIONS", "WEATHER")
        sub = sub.upper()
        sf = self.t(20, True, sub)
        x = end + 24
        _text(d, (x, 51), fit(d, sub, sf, right - x), font=sf, fill=self.MUTED, anchor="lm")
        today = now.date()
        shown = [h.temp for day in (today, today + timedelta(days=1)) for h in weather.on(day)]
        lo_t, hi_t = (min(shown), max(shown)) if shown else (0.0, 1.0)
        self.weather_day(d, hh, "Today", today, weather, now, lo_t, hi_t)
        y = hh + 362
        d.line((0, y, W, y), fill=self.RULE, width=1)
        d.bitmap((0, y - 1), _keep(("hr",), lambda: _ramp((520, 3), 255, 0, horizontal=True)), fill=_mix(self.ACCENT, BLACK, 0.4))
        self.weather_day(d, y, "Tomorrow", today + timedelta(days=1), weather, now, lo_t, hi_t)
        return img

    # ---- screensaver ----

    def saver_alert(self, d: ImageDraw.ImageDraw, cx: float, y: float, alerts: list, now: datetime) -> float:
        color = _mix(self.alert_color(alerts[0].severity), BLACK, 0.28)
        text = self.alert_text(alerts, now).upper()
        f = self.t(32, True, text)
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
        clock_f, suffix_f = self.f(230, True), self.f(56, True)
        cw, sw = d.textlength(clock, font=clock_f), d.textlength(suffix, font=suffix_f)
        x = cx - (cw + 18 + sw) / 2
        _text(d, (x, top + 112), clock, font=clock_f, fill=self.NIGHT_INK, anchor="lm")
        _text(d, (x + cw + 18, top + 160), suffix, font=suffix_f, fill=self.NIGHT_MUTED, anchor="lm")
        ry = top + 222
        d.line((cx - 330, ry, cx + 330, ry), fill=self.NIGHT_ACCENT, width=1)
        for k in (-1, 1):
            d.line((cx + k * 330, ry - 7, cx + k * 330, ry + 7), fill=self.NIGHT_ACCENT, width=2)
        ds = f"{now:%A}  //  {now:%B} {now.day}".upper()
        _text(d, (cx, top + 264), ds, font=self.t(44, True, ds), fill=self.NIGHT_INK, anchor="mm")

        y = top + 340
        hour = weather.at(now) if weather else None
        if hour:
            text = f"{_temp(hour.temp)}  {describe(hour.code)[1]}".upper()
            info = weather.days.get(now.date())
            if info:
                text += f"    H {_temp(info.high)}  L {_temp(info.low)}"
            wf = self.t(34, True, text)
            tw = self.deg_width(d, text, wf)
            self.icon(d, cx - tw / 2 - 12, y, 56, describe(hour.code)[0], hour.is_day, self.NIGHT_BG)
            self.deg(d, (cx + 34, y), text, wf, self.NIGHT_MUTED, "mm")
            y += 74
        if alerts:
            y = self.saver_alert(d, cx, y, alerts, now)

        for ev, lead in self.coming(now, events, 2 if alerts else 3):
            color = _mix(_mix(self.color(ev), self.NIGHT_INK, 0.3), BLACK, 0.2)
            lead = lead.upper()
            lead_f = self.t(30, True, lead)
            title_f = self.u(28, False, ev.title)
            lw = d.textlength(lead, font=lead_f)
            title = fit(d, ev.title, title_f, 860 - lw - 18)
            x = cx - (lw + 18 + d.textlength(title, font=title_f)) / 2
            _text(d, (x, y), lead, font=lead_f, fill=color, anchor="lm")
            _text(d, (x + lw + 18, y), title, font=title_f, fill=self.NIGHT_MUTED, anchor="lm")
            y += 46
        return img
