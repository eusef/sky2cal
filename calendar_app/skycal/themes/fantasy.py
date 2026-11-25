"""Dark fantasy: charcoal under a warm vignette, aged-gold rules and corner ornaments drawn from
shapes, engraved capitals (Cinzel) for headings, parchment-ivory EB Garamond for what people
typed, ember for "today" and "now".

Everything costly (the backgrounds, the card panels, the anti-aliased ornaments) is built once
and kept in the module-level caches below; a screen only copies and pastes them. Smooth dark
gradients get a little monochrome noise when they are built, so that the 16-bit panel shows a
fine grain instead of bands.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

from .. import render as R
from ..feed import Event, on_day
from ..render import CLOSE, H, NAV, W, _mix, _temp, _text, fit, fmt_time, fmt_until, fmt_when, wrap
from ..weather import Weather, describe
from .original import Original

CINZEL, GARAMOND = "Cinzel-VF.ttf", "EBGaramond-VF.ttf"

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GOLD = (208, 168, 96)
GOLD_HI = (240, 208, 144)
GOLD_DIM = (136, 108, 64)
GOLD_DARK = (72, 56, 32)
EMBER = (240, 128, 48)
PLATE = (28, 22, 16)            # an event's plate before its colour is mixed in
PANEL = (24, 20, 16)            # cards and buttons
PAST = (20, 16, 12)             # what a finished event fades toward

_CANVAS: dict = {}              # screen -> background picture
_PATCH: dict = {}               # (x0, x1, bottom) -> the glowing column of today
_PANEL: dict = {}               # card width -> (tall panel, its mask)
_MASKS: dict = {}               # ornament key -> anti-aliased "L" mask


# ---- colours ----

def _lum(c: tuple) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


@lru_cache(maxsize=512)
def _lift(c: tuple, floor: float) -> tuple:
    """`c` lightened just enough that its luminance reaches `floor` (a dark colour stays its hue)."""
    lum = _lum(c)
    if lum >= floor:
        return c
    return _mix(c, WHITE, (floor - lum) / (255 - lum))


@lru_cache(maxsize=512)
def _sink(c: tuple, ceiling: float) -> tuple:
    """`c` darkened just enough that its luminance stays under `ceiling`."""
    lum = _lum(c)
    if lum <= ceiling:
        return c
    return _mix(c, BLACK, 1 - ceiling / lum)


# ---- pictures built once ----

def _grain(size: tuple, spread: int = 4) -> Image.Image:
    """Monochrome noise as an RGB picture with values 0..2*spread (subtract `spread` after adding)."""
    n = Image.effect_noise(size, spread * 1.2).point(lambda v: max(0, min(2 * spread, v - 128 + spread)))
    return Image.merge("RGB", (n, n, n))


def _dither(img: Image.Image, spread: int = 4) -> Image.Image:
    return ImageChops.add(img, _grain(img.size, spread), 1.0, -spread)


def _vignette(size: tuple, centre: tuple, edge: tuple, cy: float = 0.42, power: float = 1.5) -> Image.Image:
    """A warm glow in the middle fading to `edge` at the sides, with grain."""
    w, h = size
    g = Image.radial_gradient("L").resize((int(w * 1.25), int(h * 1.7)), Image.Resampling.BILINEAR)
    left, top = (g.width - w) // 2, int(g.height / 2 - h * cy)
    g = g.crop((left, top, left + w, top + h)).point(lambda v: min(255, int(255 * (v / 255) ** power * 1.15)))
    img = Image.composite(Image.new("RGB", size, edge), Image.new("RGB", size, centre), g)
    return _dither(img)


def _mask(key: tuple, size: tuple, draw) -> Image.Image:
    """An anti-aliased "L" mask of `size`, drawn by draw(ImageDraw, scale) at 4x and scaled down."""
    m = _MASKS.get(key)
    if m is None:
        big = Image.new("L", (size[0] * 4, size[1] * 4), 0)
        draw(ImageDraw.Draw(big), 4)
        m = _MASKS[key] = big.resize(size, Image.Resampling.LANCZOS)
    return m


def _stamp(d: ImageDraw.ImageDraw, cx: float, cy: float, m: Image.Image, fill: tuple) -> None:
    d.bitmap((round(cx - m.width / 2), round(cy - m.height / 2)), m, fill=fill)


def _diamond(w: int, h: int, hollow: bool = False) -> Image.Image:
    def draw(d, s):
        pts = [(w * s / 2, 0), (w * s - 1, h * s / 2), (w * s / 2, h * s - 1), (0, h * s / 2)]
        d.polygon(pts, fill=255)
        if hollow:
            k = 2.2 * s
            d.polygon([(w * s / 2, k * 1.6), (w * s - 1 - k * 1.6, h * s / 2), (w * s / 2, h * s - 1 - k * 1.6),
                       (k * 1.6, h * s / 2)], fill=0)
            r = min(w, h) * s * 0.13
            d.ellipse((w * s / 2 - r, h * s / 2 - r, w * s / 2 + r, h * s / 2 + r), fill=255)
    return _mask(("diamond", w, h, hollow), (w, h), draw)


def _blade(length: int, flip: bool) -> Image.Image:
    """A line that tapers from 3 px at one end to a point, with a small bead near the thin end."""
    h = 9

    def draw(d, s):
        L, m = length * s, h * s / 2
        d.polygon([(0, m - 1.5 * s), (L * 0.9, m - 0.3 * s), (L * 0.9, m + 0.3 * s), (0, m + 1.5 * s)], fill=255)
        r = 1.8 * s
        d.ellipse((L - 2 * r - s, m - r, L - s, m + r), fill=255)
    m = _mask(("blade", length), (length, h), draw)
    return m.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if flip else m


@lru_cache(maxsize=64)
def _rule_mask(width: int, gem: int = 14) -> Image.Image:
    """A filigree rule: a hollow diamond in the middle and two tapering blades."""
    h = max(gem, 9)
    m = Image.new("L", (width, h), 0)
    side = (width - gem) // 2 - 3
    blade = _blade(side, False)
    m.paste(blade.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (0, (h - 9) // 2))
    m.paste(blade, (width - side, (h - 9) // 2))
    m.paste(_diamond(gem, gem, True), ((width - gem) // 2, (h - gem) // 2))
    return m


def _corner(size: int) -> Image.Image:
    """The top-left corner ornament of a frame: an L of two tapering arms, a curl and a gem."""
    def draw(d, s):
        S = size * s
        w = 2.2 * s
        d.polygon([(0, 0), (S, 0), (S, 0.6 * s), (0, w * 1.6)], fill=255)
        d.polygon([(0, 0), (0, S), (0.6 * s, S), (w * 1.6, 0)], fill=255)
        d.arc((5 * s, 5 * s, 0.62 * S, 0.62 * S), 180, 270, fill=255, width=int(1.6 * s))
        g = 0.2 * S
        c = 0.26 * S
        d.polygon([(c, c - g / 2), (c + g / 2, c), (c, c + g / 2), (c - g / 2, c)], fill=255)
    return _mask(("corner", size), (size, size), draw)


def _corners(img_or_draw, box: tuple, size: int, fill: tuple, inset: int = 0) -> None:
    """The corner ornament at the four corners of `box` (on a picture: paste; on a draw: bitmap)."""
    x0, y0, x1, y1 = (round(v) for v in box)
    m = _corner(size)
    spots = ((m, x0 + inset, y0 + inset),
             (m.transpose(Image.Transpose.FLIP_LEFT_RIGHT), x1 - inset - size, y0 + inset),
             (m.transpose(Image.Transpose.FLIP_TOP_BOTTOM), x0 + inset, y1 - inset - size),
             (m.transpose(Image.Transpose.ROTATE_180), x1 - inset - size, y1 - inset - size))
    for mm, x, y in spots:
        if isinstance(img_or_draw, Image.Image):
            img_or_draw.paste(fill, (x, y, x + size, y + size), mm)
        else:
            img_or_draw.bitmap((x, y), mm, fill=fill)


def _arrow(w: int, h: int, left: bool) -> Image.Image:
    def draw(d, s):
        W_, H_ = w * s, h * s
        d.polygon([(0, H_ / 2), (W_ * 0.72, 0), (W_ * 0.52, H_ / 2), (W_ * 0.72, H_)], fill=255)
        d.polygon([(W_ * 0.62, H_ / 2), (W_, H_ * 0.3), (W_ * 0.9, H_ / 2), (W_, H_ * 0.7)], fill=255)
    m = _mask(("arrow", w, h), (w, h), draw)
    return m if left else m.transpose(Image.Transpose.FLIP_LEFT_RIGHT)


def _chamfer(box: tuple, k: float) -> list:
    x0, y0, x1, y1 = box
    return [(x0 + k, y0), (x1 - k, y0), (x1, y0 + k), (x1, y1 - k), (x1 - k, y1), (x0 + k, y1), (x0, y1 - k), (x0, y0 + k)]


def _plaque(d: ImageDraw.ImageDraw, box: tuple, k: float, fill: tuple, outline: tuple | None = None,
            width: int = 1) -> None:
    """A filled box with cut corners and an outline `width` thick."""
    if outline is not None:
        d.polygon(_chamfer(box, k), fill=outline)
        x0, y0, x1, y1 = box
        box = (x0 + width, y0 + width, x1 - width, y1 - width)
        k = max(1.0, k - width * 0.4)
    d.polygon(_chamfer(box, k), fill=fill)


class Fantasy(Original):
    name = "fantasy"
    label = "Dark fantasy"
    GEAR_INK = GOLD_HI             # the gear is drawn like the style's button labels

    BG = (16, 12, 8)
    INK = (240, 228, 200)
    MUTED = (168, 152, 120)
    FAINT = (104, 92, 72)
    RULE = (88, 68, 40)
    TODAY_BG = PANEL
    ACCENT = EMBER
    WARN = (240, 112, 56)
    CAL_COLORS = [(72, 128, 216), (64, 168, 104), (224, 120, 48), (160, 104, 216), (216, 64, 80), (136, 144, 160)]
    NIGHT_BG = BLACK
    NIGHT_INK = (148, 122, 80)
    NIGHT_MUTED = (100, 88, 68)
    ICON_COLORS = ((236, 176, 72), (216, 204, 176), (150, 142, 132), (104, 152, 208), (204, 212, 228), (244, 140, 52))
    TEMP_LINE, TEMP_DOT = (216, 112, 44), GOLD
    SEVERITY_COLORS = {"Extreme": (168, 20, 48), "Severe": (192, 36, 28), "Moderate": (216, 108, 24),
                       "Minor": (200, 150, 24), "Unknown": (112, 104, 96)}
    DIM = R.dim_table((6, 4, 2), 0.62)

    TITLE_SIZE, LINE_H, LABEL_H = 20, 24, 20      # f(20, True) is 22 px Garamond

    # ---- fonts ----

    def f(self, size: int, bold: bool = False):
        """EB Garamond (it has accents, Cyrillic, Greek, … ‹ › °) a little larger than asked,
        since its letters are small for their size."""
        return R.font_file(GARAMOND, round(size * 1.12), 700 if bold else 500)

    @staticmethod
    def g(px: int, weight: int = 600):
        """EB Garamond at exactly `px`."""
        return R.font_file(GARAMOND, px, weight)

    @staticmethod
    def cap(px: int, weight: int = 700):
        """Cinzel: capitals only, Latin only; for the theme's own words, times and numbers."""
        return R.font_file(CINZEL, px, weight)

    def engraved(self, d, xy, text, font, fill, anchor="lm") -> None:
        """Text cut into the surface: a dark shadow below, a faint warm edge above."""
        x, y = xy
        _text(d, (x, y + 2), text, font=font, fill=(4, 3, 2), anchor=anchor)
        _text(d, (x, y - 1), text, font=font, fill=_mix(fill, BLACK, 0.55), anchor=anchor)
        _text(d, (x, y), text, font=font, fill=fill, anchor=anchor)

    # ---- backgrounds ----

    def canvas(self, screen: str) -> Image.Image:
        if screen == "saver":
            return Image.new("RGB", (W, H), self.NIGHT_BG)
        img = _CANVAS.get(screen)
        if img is None:
            img = _vignette((W, H), (44, 32, 22), (8, 6, 4))
            hh = self.HEADER_H
            band = Image.blend(img.crop((0, 0, W, hh)), Image.new("RGB", (W, hh), (4, 3, 2)), 0.45)
            img.paste(band, (0, 0))
            d = ImageDraw.Draw(img)
            d.line((0, hh - 4, W, hh - 4), fill=(64, 50, 30), width=1)
            d.line((0, hh - 1, W, hh - 1), fill=GOLD_DIM, width=2)
            d.line((0, hh + 1, W, hh + 1), fill=(4, 3, 2), width=1)
            _stamp(d, W // 2, hh, _rule_mask(300, 16), GOLD)
            if screen == "week":
                top, bot = hh + 2, hh + self.DAYHEAD_H
                band = Image.blend(img.crop((0, top, W, bot)), Image.new("RGB", (W, bot - top), (4, 3, 2)), 0.25)
                img.paste(band, (0, top))
            _CANVAS[screen] = img
        return img.copy()

    def today_patch(self, base: Image.Image, x0: int, x1: int, bottom: int) -> Image.Image:
        """The column of today: an ember glow from the top, gold edges."""
        key = (x0, x1, bottom)
        patch = _PATCH.get(key)
        if patch is None:
            top = self.HEADER_H + 2
            w, h = x1 - x0, bottom - top
            ramp = Image.linear_gradient("L").resize((1, h)).point(lambda v: int(150 * (1 - v / 255) ** 2.2 + 26))
            patch = Image.composite(Image.new("RGB", (w, h), (120, 54, 16)), base.crop((x0, top, x1, bottom)),
                                    ramp.resize((w, h)))
            patch = _dither(patch, 3)
            d = ImageDraw.Draw(patch)
            d.line((0, 0, w, 0), fill=EMBER, width=2)
            d.line((1, 0, 1, h), fill=GOLD_DARK, width=1)
            d.line((w - 2, 0, w - 2, h), fill=GOLD_DARK, width=1)
            _PATCH[key] = patch
        return patch

    # ---- header ----

    def button_face(self, d: ImageDraw.ImageDraw, box: tuple) -> None:
        x0, y0, x1, y1 = box
        _plaque(d, box, 9, (30, 24, 18), GOLD_DIM, 2)
        d.line((x0 + 9, y0 + 3, x1 - 9, y0 + 3), fill=(68, 54, 34), width=1)
        d.line((x0 + 9, y1 - 3, x1 - 9, y1 - 3), fill=(12, 9, 6), width=1)
        _stamp(d, (x0 + x1) / 2, y1 - 1, _diamond(8, 8), GOLD)

    def button(self, d: ImageDraw.ImageDraw, box: tuple, label: str, size: int, lift: int = 0) -> None:
        x0, y0, x1, y1 = box
        self.button_face(d, box)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if label in ("‹", "›"):
            _stamp(d, cx, cy, _arrow(24, 24, label == "‹"), GOLD_HI)
            return
        self.engraved(d, (cx, cy), label.upper(), self.cap(19, 700), GOLD_HI, "mm")

    def weather_button(self, d: ImageDraw.ImageDraw, hour) -> None:
        x0, y0, x1, y1 = NAV["weather"]
        if hour:
            self.button_face(d, NAV["weather"])
            self.icon(d, x0 + 44, (y0 + y1) // 2, 38, describe(hour.code)[0], hour.is_day, (30, 24, 18))
            self.engraved(d, (x0 + 80, (y0 + y1) // 2 + 1), _temp(hour.temp), self.cap(26, 700), self.INK, "lm")
        else:
            self.button(d, NAV["weather"], "Weather", 22)

    def header(self, d: ImageDraw.ImageDraw, start: date, now: datetime, note: str, stale: bool,
               weather: Weather | None) -> None:
        hh = self.HEADER_H
        right = NAV["settings"][0] - 20
        title_f = self.cap(32, 700)
        clock = fmt_time(now).upper()
        title = fit(d, self.week_title(start).upper(), title_f, 560)
        self.engraved(d, (24, hh // 2 - 1), title, title_f, GOLD_HI)
        if note:
            room = right - (24 + d.textlength(title, font=title_f) + 28)
            self.engraved(d, (right, 26), clock, self.cap(26, 700), self.INK, "rm")
            _text(d, (right, 55), fit(d, note, self.g(17, 600), room), font=self.g(17, 600),
                  fill=self.WARN if stale else self.MUTED, anchor="rm")
        else:
            self.engraved(d, (right, hh // 2), clock, self.cap(30, 700), self.INK, "rm")
        self.settings_button(d)
        self.weather_button(d, weather.at(now) if weather else None)
        self.button(d, NAV["prev"], "‹", 36)
        self.button(d, NAV["today"], "Today", 22)
        self.button(d, NAV["next"], "›", 36)

    def header_rule(self, d: ImageDraw.ImageDraw) -> None:
        pass                    # part of the canvas

    # ---- week ----

    def day_head(self, d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, today: date) -> None:
        top = self.HEADER_H
        cx = (x0 + x1) // 2
        is_today = day == today
        past = day < today
        _text(d, (cx, top + 21), f"{day:%a}".upper(), font=self.cap(17, 700),
              fill=EMBER if is_today else (self.FAINT if past else GOLD_DIM), anchor="mm")
        num_f = self.cap(36, 700)
        if is_today:
            nw = d.textlength(str(day.day), font=num_f)
            for side in (-1, 1):
                _stamp(d, cx + side * (nw / 2 + 14), top + 57, _diamond(9, 13), EMBER)
            _stamp(d, cx, top + 79, _rule_mask(100, 9), EMBER)
            self.engraved(d, (cx, top + 56), str(day.day), num_f, GOLD_HI, "mm")
        else:
            self.engraved(d, (cx, top + 56), str(day.day), num_f, self.MUTED if past else self.INK, "mm")

    def plate_height(self, label: str, lines: list) -> int:
        return 9 + (self.LABEL_H if label else 0) + self.LINE_H * len(lines) + 8

    def plate(self, d: ImageDraw.ImageDraw, box: tuple, ev: Event, label: str, lines: list, over: bool, current: bool,
              back: tuple) -> None:
        left, y, right, y1 = box
        color = self.color(ev)
        bright = _lift(color, 150)                 # for text and outlines in the event's colour
        bar = _lift(color, 80)
        title_f = self.f(self.TITLE_SIZE, True)
        if ev.all_day:
            fill = _sink(_lift(_mix(color, PLATE, 0.3), 46), 84)
            edge = _lift(color, 110)
            if over:
                fill, edge = _mix(fill, PAST, 0.6), _mix(edge, PAST, 0.6)
            _plaque(d, box, 6, fill, edge, 1)
            d.line((left + 6, y + 2, right - 6, y + 2), fill=_mix(edge, fill, 0.5), width=1)
            ink, sub = (self.MUTED, self.FAINT) if over else (WHITE, bright)
        else:
            fill = _mix(PLATE, bar, 0.2 if current else 0.13)
            if over:
                fill, bar, bright = _mix(fill, PAST, 0.5), _mix(bar, PAST, 0.55), _mix(bright, PAST, 0.5)
            if current:
                _plaque(d, (left - 1, y - 1, right + 1, y1 + 1), 7, fill, bright, 2)
            else:
                _plaque(d, box, 6, fill, _mix(bar, fill, 0.55), 1)
            d.rectangle((left + 4, y + 7, left + 7, y1 - 7), fill=bar)
            _stamp(d, left + 5.5, y + 6, _diamond(8, 8), bar)
            _stamp(d, left + 5.5, y1 - 6, _diamond(8, 8), bar)
            ink, sub = ((150, 136, 110), bright) if over else (self.INK, bright)
        ty = y + 9
        if label:
            label_f = self.cap(15, 800 if current else 700)
            _text(d, (left + 13, ty), fit(d, label.upper(), label_f, right - left - 16), font=label_f, fill=sub)
            ty += self.LABEL_H
        for line in lines:
            _text(d, (left + 13, ty - 4), line, font=title_f, fill=ink)
            ty += self.LINE_H

    def more(self, d: ImageDraw.ImageDraw, cx: int, bottom: int, count: int) -> None:
        f = self.cap(16, 800)
        text = f"+{count} MORE"
        tw = d.textlength(text, font=f)
        _text(d, (cx, bottom - 16), text, font=f, fill=GOLD_HI, anchor="mm")
        for side in (-1, 1):
            _stamp(d, cx + side * (tw / 2 + 10), bottom - 16, _diamond(8, 8), EMBER)

    def grid(self, d: ImageDraw.ImageDraw, edges: list, bottom: int) -> None:
        y = self.HEADER_H + self.DAYHEAD_H
        d.line((0, y, W, y), fill=GOLD_DIM, width=1)
        d.line((0, y + 1, W, y + 1), fill=(4, 3, 2), width=1)
        for x in edges[1:-1]:
            d.line((x, self.HEADER_H + 14, x, bottom - 10), fill=(58, 44, 28), width=1)
            _stamp(d, x, y, _diamond(9, 9), GOLD)

    def render_week(self, start: date, events: list[Event], now: datetime, note: str, stale: bool, hits: list | None,
                    weather: Weather | None, message: str, pause, alerts: list) -> Image.Image:
        tz = now.tzinfo
        img = self.canvas("week")
        bottom = H - self.ALERT_H if alerts else H
        edges = [round(i * W / 7) for i in range(8)]
        k = (now.date() - start).days
        if 0 <= k < 7:
            img.paste(self.today_patch(_CANVAS["week"], edges[k], edges[k + 1], bottom),
                      (edges[k], self.HEADER_H + 2))
        d = ImageDraw.Draw(img)
        self.header(d, start, now, note, stale, weather)
        self.header_rule(d)
        for i in range(7):
            day = start + timedelta(days=i)
            self.day(d, edges[i], edges[i + 1], day, on_day(events, day, tz), now, hits, bottom)
            if pause:
                pause()
        self.grid(d, edges, bottom)
        if alerts:
            self.alert_band(d, (0, bottom, W, H), alerts, now)
            if hits is not None:
                hits.append((0, bottom, W, H, "alerts"))
        if message:
            self.message_box(d, message)
        return img

    def message_box(self, d: ImageDraw.ImageDraw, message: str) -> None:
        f = self.g(27, 600)
        lines = wrap(d, message, f, 680, 4)
        half = 40 + 18 * len(lines)
        box = (W // 2 - 400, 470 - half, W // 2 + 400, 470 + half)
        _plaque(d, box, 14, PANEL, GOLD_DIM, 2)
        _corners(d, box, 30, GOLD, 6)
        for i, line in enumerate(lines):
            _text(d, (W // 2, 470 - 18 * (len(lines) - 1) + 36 * i), line, font=f, fill=self.INK, anchor="mm")

    def alert_band(self, d: ImageDraw.ImageDraw, box: tuple, alerts: list, now: datetime) -> None:
        x0, y0, x1, y1 = box
        color = self.alert_color(alerts[0].severity)
        deep = _mix(color, BLACK, 0.3)
        d.rectangle(box, fill=deep)
        d.rectangle((x0, y0 + (y1 - y0) // 2, x1, y1), fill=_mix(color, BLACK, 0.42))
        d.line((x0, y0, x1, y0), fill=_mix(color, GOLD_HI, 0.5), width=2)
        d.line((x0, y0 + 2, x1, y0 + 2), fill=(10, 6, 4), width=1)
        cy = (y0 + y1) // 2 + 1
        R.warn_icon(d, x0 + 34, cy, 26, GOLD_HI, deep)
        hint = "TAP FOR DETAILS"
        hint_f = self.cap(15, 700)
        hint_w = d.textlength(hint, font=hint_f)
        f = self.g(25, 700)
        _text(d, (x0 + 62, cy - 1), fit(d, self.alert_text(alerts, now), f, x1 - x0 - 120 - hint_w), font=f,
              fill=WHITE, anchor="lm")
        _text(d, (x1 - 40, cy), hint, font=hint_f, fill=_mix(color, WHITE, 0.7), anchor="rm")
        _stamp(d, x1 - 22, cy, _arrow(12, 14, False), GOLD_HI)

    # ---- cards over the week ----

    def panel(self, width: int) -> tuple:
        """A card panel as tall as a card can be, and its mask (cut corners)."""
        got = _PANEL.get(width)
        if got is None:
            h = H - 40
            # lit from the middle across, the same all the way down, so a crop of any height matches its foot
            ramp = Image.radial_gradient("L").crop((0, 128, 256, 129)).resize((width, h), Image.Resampling.BILINEAR)
            img = _dither(Image.composite(Image.new("RGB", (width, h), (16, 12, 9)),
                                          Image.new("RGB", (width, h), (38, 30, 21)), ramp.point(lambda v: v * 3 // 4)))
            d = ImageDraw.Draw(img)
            box = (0, 0, width - 1, h - 1)
            d.polygon(_chamfer(box, 16), outline=GOLD_DIM)
            d.polygon(_chamfer((1, 1, width - 2, h - 2), 15.5), outline=(10, 8, 6))
            d.polygon(_chamfer((7, 7, width - 8, h - 8), 11), outline=(84, 66, 40))
            mask = Image.new("L", (width, h), 0)
            ImageDraw.Draw(mask).polygon(_chamfer(box, 16), fill=255)
            got = _PANEL[width] = (img, mask)
        return got

    CAP = 70        # rows at the foot of a panel that hold its lower corners

    def card(self, base: Image.Image, height: int, width: int = 820) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple]:
        img = base.point(self.DIM)
        height = max(min(height, H - 40), 2 * self.CAP)
        box = ((W - width) // 2, (H - height) // 2, (W + width) // 2, (H + height) // 2)
        x0, y0, x1, y1 = box
        panel, mask = self.panel(width)
        cut = height - self.CAP
        img.paste(panel.crop((0, 0, width, cut)), (x0, y0), mask.crop((0, 0, width, cut)))
        ph = panel.height
        img.paste(panel.crop((0, ph - self.CAP, width, ph)), (x0, y1 - self.CAP), mask.crop((0, ph - self.CAP, width, ph)))
        d = ImageDraw.Draw(img)
        _corners(d, (x0, y0, x1 - 1, y1 - 1), 40, GOLD, 4)
        return img, d, box

    def card_strip(self, d: ImageDraw.ImageDraw, box: tuple, color: tuple) -> None:
        x0, y0, x1, y1 = box
        bar = _lift(color, 80)
        d.rectangle((x0 + 20, y0 + 44, x0 + 25, y1 - 44), fill=bar)
        _stamp(d, x0 + 22.5, y0 + 40, _diamond(14, 18), bar)
        _stamp(d, x0 + 22.5, y1 - 40, _diamond(14, 18), bar)

    def card_hint(self, d: ImageDraw.ImageDraw, box: tuple, text: str) -> None:
        x0, y0, x1, y1 = box
        f = self.cap(15, 700)
        text = text.upper()
        tw = d.textlength(text, font=f)
        cx, cy = (x0 + x1) / 2, y1 - 30
        _text(d, (cx, cy), text, font=f, fill=self.MUTED, anchor="mm")
        for side in (-1, 1):
            _stamp(d, cx + side * (tw / 2 + 34), cy, _blade(48, side < 0), GOLD_DIM)

    def section_label(self, d: ImageDraw.ImageDraw, x: int, y: int, text: str, right: int) -> None:
        """A section heading in a card: gold capitals and a rule that fades out to the right."""
        f = self.cap(16, 800)
        _text(d, (x, y), text, font=f, fill=GOLD)
        tw = int(d.textlength(text, font=f))
        _stamp(d, x + tw + 16, y + 8, _diamond(7, 7), GOLD_DIM)
        d.line((x + tw + 24, y + 8, right, y + 8), fill=(70, 56, 34), width=1)

    def render_detail(self, base: Image.Image, ev: Event, now: datetime) -> Image.Image:
        probe = ImageDraw.Draw(base)
        width = 820
        text_w = width - 60 - 44
        title_f, when_f, body_f = self.g(38, 700), self.g(26, 600), self.g(24, 500)
        title = wrap(probe, ev.title, title_f, text_w, 3)
        when = wrap(probe, fmt_when(ev), when_f, text_w, 2)
        until = fmt_until(ev, now)
        where = wrap(probe, ev.location, body_f, text_w, 3) if ev.location else []
        fixed = 40 + 44 * len(title) + 12 + 32 * len(when) + (32 if until else 0) + (24 + 28 + 30 * len(where) if where else 0) + 74
        room = (H - 40 - fixed - 52) // 30
        notes = wrap(probe, ev.description, body_f, text_w, max(1, min(12, room))) if ev.description else []
        img, d, box = self.card(base, fixed + (24 + 28 + 30 * len(notes) if notes else 0), width)
        x0, y0, x1, y1 = box
        color = self.color(ev)
        self.card_strip(d, box, color)
        x, y = x0 + 60, y0 + 40
        for line in title:
            _text(d, (x, y - 6), line, font=title_f, fill=self.INK)
            y += 44
        y += 12
        for line in when:
            _text(d, (x, y - 3), line, font=when_f, fill=self.INK)
            y += 32
        if until:
            _text(d, (x, y + 4), until.upper(), font=self.cap(17, 800),
                  fill=self.MUTED if until == "Ended" else _lift(color, 150))
            y += 32
        for label, lines in (("WHERE", where), ("NOTES", notes)):
            if lines:
                y += 24
                self.section_label(d, x, y, label, x1 - 44)
                y += 28
                for line in lines:
                    _text(d, (x, y - 3), line, font=body_f, fill=self.INK)
                    y += 30
        self.card_hint(d, box, "Tap anywhere to close")
        return img

    def render_day(self, base: Image.Image, day: date, events: list[Event], now: datetime, hits: list | None,
                   first: int) -> Image.Image:
        tz = now.tzinfo
        everything = on_day(events, day, tz)
        first = first if 0 < first < len(everything) else 0
        todays = everything[first:]
        row_h, rows = 46, min(len(todays), R.DAY_ROWS)
        img, d, box = self.card(base, 92 + row_h * max(rows, 1) + 64)
        x0, y0, x1, y1 = box
        self.engraved(d, (x0 + 44, y0 + 50), f"{day:%A}, {day:%B} {day.day}".upper(), self.cap(28, 700), GOLD_HI)
        count = f"{len(everything)} event" + ("" if len(everything) == 1 else "s")
        _text(d, (x1 - 44, y0 + 50), count.upper(), font=self.cap(16, 700), fill=self.MUTED, anchor="rm")
        lo = datetime.combine(day, time.min, tz)
        y = y0 + 90
        span_f, title_f = self.cap(17, 700), self.g(25, 700)
        rule = (66, 52, 32)
        if not todays:
            _text(d, (x0 + 44, y + row_h // 2), "Nothing on this day", font=self.g(25, 500), fill=self.MUTED, anchor="lm")
        for i, ev in enumerate(todays[:rows]):
            if i == rows - 1 and len(todays) > rows:
                d.line((x0 + 36, y, x1 - 36, y), fill=rule, width=1)
                _text(d, (x0 + 44, y + row_h // 2), f"SHOW {len(todays) - i} MORE", font=self.cap(18, 800), fill=GOLD_HI,
                      anchor="lm")
                if hits is not None:
                    hits.append((x0, y, x1, y + row_h, first + i))
                break
            color = self.color(ev)
            over = ev.end <= now and (ev.all_day or ev.start < now)
            bar = _lift(color, 80)
            d.line((x0 + 36, y, x1 - 36, y), fill=rule, width=1)
            d.rectangle((x0 + 44, y + 10, x0 + 49, y + row_h - 10), fill=_mix(bar, PANEL, 0.6) if over else bar)
            _text(d, (x0 + 62, y + row_h // 2), self.span(ev, lo).upper(), font=span_f,
                  fill=self.FAINT if over else _lift(color, 150), anchor="lm")
            _text(d, (x0 + 250, y + row_h // 2 - 2), fit(d, ev.title, title_f, x1 - 44 - (x0 + 250)), font=title_f,
                  fill=self.FAINT if over else self.INK, anchor="lm")
            if hits is not None:
                hits.append((x0, y, x1, y + row_h, ev))
            y += row_h
        self.card_hint(d, box, "Tap an event for details, or outside to close")
        return img

    def render_alerts(self, base: Image.Image, alerts: list, now: datetime, hits: list | None, index: int) -> Image.Image:
        a = alerts[index]
        width = 900
        probe = ImageDraw.Draw(base)
        text_w = width - 60 - 44
        title_f, when_f, body_f = self.g(36, 700), self.g(25, 600), self.g(22, 500)
        color = self.alert_color(a.severity)
        title = wrap(probe, a.event, title_f, text_w, 2)
        when = self.alert_when(a, now)
        area = wrap(probe, a.area, body_f, text_w, 2) if a.area else []
        more = len(alerts) > 1
        fixed = 40 + 42 * len(title) + 8 + (32 if when else 0) + 32 + (26 * len(area) + 10 if area else 0) + 74 + (52 if more else 0)
        room = max(2, (H - 40 - fixed) // 27 - 2)
        what = wrap(probe, a.description or a.headline, body_f, text_w, max(1, room - (4 if a.instruction else 0)))
        todo = wrap(probe, a.instruction, body_f, text_w, max(1, room - len(what) - 2)) if a.instruction and room - len(what) >= 3 else []
        body = 28 + 27 * len(what) + (14 + 28 + 27 * len(todo) if todo else 0)
        img, d, box = self.card(base, fixed + body, width)
        x0, y0, x1, y1 = box
        self.card_strip(d, box, color)
        x, y = x0 + 60, y0 + 40
        for line in title:
            _text(d, (x, y - 6), line, font=title_f, fill=self.INK)
            y += 42
        y += 8
        if when:
            _text(d, (x, y - 3), when, font=when_f, fill=self.INK)
            y += 32
        tag = f"{a.severity.upper()}  ·  {a.sender.upper()}" if a.sender else a.severity.upper()
        tag_f = self.cap(16, 800)
        tag = fit(d, tag, tag_f, text_w - 30)
        R.warn_icon(d, x + 10, y + 11, 20, _lift(color, 120), PANEL)
        _text(d, (x + 28, y + 3), tag, font=tag_f, fill=_lift(color, 120))
        y += 32
        for line in area:
            _text(d, (x, y - 3), line, font=body_f, fill=self.MUTED)
            y += 26
        y += 10 if area else 0
        for label, lines in (("WHAT", what), ("WHAT TO DO", todo)):
            if lines:
                y += 14 if label != "WHAT" else 0
                self.section_label(d, x, y, label, x1 - 44)
                y += 28
                for line in lines:
                    _text(d, (x, y - 3), line, font=body_f, fill=self.INK)
                    y += 27
        if more:
            ry = y1 - 74 - 48
            d.line((x0 + 36, ry, x1 - 36, ry), fill=(84, 66, 40), width=1)
            nxt = (index + 1) % len(alerts)
            f = self.g(24, 700)
            lead = f"NEXT ALERT  ({nxt + 1} OF {len(alerts)})"
            lw = d.textlength(lead, font=self.cap(16, 800))
            _text(d, (x, ry + 24), lead, font=self.cap(16, 800), fill=GOLD, anchor="lm")
            _text(d, (x + lw + 20, ry + 23), fit(d, alerts[nxt].event, f, text_w - lw - 60), font=f, fill=GOLD_HI,
                  anchor="lm")
            _stamp(d, x1 - 50, ry + 24, _arrow(14, 16, False), GOLD_HI)
            if hits is not None:
                hits.append((x0, ry, x1, ry + 48, nxt))
        self.card_hint(d, box, "Tap anywhere else to close")
        return img

    # ---- weather ----

    def icon(self, d: ImageDraw.ImageDraw, cx: float, cy: float, size: int, name: str, is_day: bool = True,
             bg: tuple | None = None, fade: bool = False) -> None:
        if bg is None:            # the grained background: match the moon's cut-out to what is there
            im = getattr(d, "_image", None)
            bg = im.getpixel((int(cx), int(cy))) if im is not None and 0 <= cx < W and 0 <= cy < H else self.BG
        R.weather_icon(d, cx, cy, size, name, is_day, bg, fade, self.ICON_COLORS, self.FAINT)

    def weather_day(self, d: ImageDraw.ImageDraw, top: int, name: str, day: date, weather: Weather, now: datetime,
                    lo_t: float, hi_t: float) -> None:
        hours = [h for h in weather.on(day) if h.time.hour >= min(6, now.hour if day == now.date() else 6)]
        name_f = self.cap(28, 700)
        self.engraved(d, (24, top + 36), name.upper(), name_f, GOLD_HI)
        name_w = d.textlength(name.upper(), font=name_f)
        _text(d, (24 + name_w + 18, top + 37), f"{day:%A}, {day:%B} {day.day}".upper(), font=self.cap(17, 700),
              fill=self.MUTED, anchor="lm")
        info = weather.days.get(day)
        if info:
            parts = [describe(info.code)[1], f"High {_temp(info.high)}", f"Low {_temp(info.low)}"]
            if info.sunrise and info.sunset:
                parts += [f"Sunrise {fmt_time(info.sunrise)}", f"Sunset {fmt_time(info.sunset)}"]
            _text(d, (W - 24, top + 36), "   ·   ".join(parts), font=self.g(23, 600), fill=self.INK, anchor="rm")
        if not hours:
            _text(d, (W // 2, top + 190), "No forecast for these hours", font=self.g(25, 500), fill=self.MUTED, anchor="mm")
            return
        cw = (W - 40) / len(hours)
        span = max(hi_t - lo_t, 1.0)
        band_top, band_h = top + 180, 70
        points = []
        hour_f, now_f, temp_f, pct_f = self.cap(16, 700), self.cap(17, 800), self.cap(19, 700), self.cap(15, 700)
        for i, h in enumerate(hours):
            cx = 20 + cw * (i + 0.5)
            past = h.time + timedelta(hours=1) <= now
            current = h.time <= now < h.time + timedelta(hours=1)
            back = None
            if current:
                back = (34, 24, 16)
                box = (cx - cw / 2 + 2, top + 64, cx + cw / 2 - 2, top + 340)
                _plaque(d, box, 8, back, EMBER, 2)
                _stamp(d, cx, top + 64, _diamond(10, 10), GOLD_HI)
            _text(d, (cx, top + 86), "NOW" if current else fmt_time(h.time).upper(), font=now_f if current else hour_f,
                  fill=EMBER if current else (self.FAINT if past else self.MUTED), anchor="mm")
            self.icon(d, cx, top + 128, 44, describe(h.code)[0], h.is_day, back, fade=past)
            py = band_top + band_h - (h.temp - lo_t) / span * band_h
            points.append((cx, py, h, past))
            if h.precip is not None and h.precip >= 10 and not past:
                tone = (120, 168, 224) if h.precip >= 30 else self.MUTED
                d.polygon([(cx, top + 284), (cx - 5, top + 293), (cx + 5, top + 293)], fill=tone)
                d.ellipse((cx - 5, top + 288, cx + 5, top + 298), fill=tone)
                _text(d, (cx, top + 316), f"{h.precip}%", font=pct_f, fill=tone, anchor="mm")
        for (ax, ay, _, a_past), (bx, by, _, _) in zip(points, points[1:]):
            d.line((ax, ay, bx, by), fill=(60, 48, 34) if a_past else self.TEMP_LINE, width=3)
        for cx, py, h, past in points:
            _stamp(d, cx, py, _diamond(12, 12), self.FAINT if past else self.TEMP_DOT)
            _text(d, (cx, py - 20), _temp(h.temp), font=temp_f, fill=self.FAINT if past else self.INK, anchor="mm")

    def alert_pill(self, d: ImageDraw.ImageDraw, right: int, cy: int, alerts: list) -> int:
        a = alerts[0]
        text = a.event + (f" +{len(alerts) - 1}" if len(alerts) > 1 else "")
        f = self.g(22, 700)
        text = fit(d, text, f, 300)
        tw = d.textlength(text, font=f)
        x0 = right - tw - 58
        color = self.alert_color(a.severity)
        deep = _mix(color, BLACK, 0.25)
        _plaque(d, (x0, cy - 21, right, cy + 21), 9, deep, _mix(color, GOLD_HI, 0.5), 2)
        R.warn_icon(d, x0 + 24, cy, 22, GOLD_HI, deep)
        _text(d, (x0 + 44, cy - 1), text, font=f, fill=WHITE, anchor="lm")
        return int(x0)

    def render_weather(self, weather: Weather | None, now: datetime, message: str, alerts: list) -> Image.Image:
        img = self.canvas("weather")
        d = ImageDraw.Draw(img)
        hh = self.HEADER_H
        title_f = self.cap(32, 700)
        self.engraved(d, (24, hh // 2 - 1), "WEATHER", title_f, GOLD_HI)
        self.button(d, CLOSE, "Back", 22)
        right = CLOSE[0] - 20
        if alerts:
            right = self.alert_pill(d, right, (CLOSE[1] + CLOSE[3]) // 2, alerts) - 16
        if weather is None:
            for i, line in enumerate(wrap(d, message or "No forecast yet", self.g(29, 500), W - 200, 4)):
                _text(d, (W // 2, 340 + i * 42), line, font=self.g(29, 500), fill=self.MUTED, anchor="mm")
            return img
        sub = f"{weather.place}, as of {fmt_time(weather.fetched)}" if weather.place else f"As of {fmt_time(weather.fetched)}"
        if now - weather.fetched > timedelta(hours=2):
            sub += f" on {weather.fetched:%b} {weather.fetched.day} (no newer forecast could be fetched)"
        x = 24 + d.textlength("WEATHER", font=title_f) + 24
        _text(d, (x, hh // 2), fit(d, sub, self.g(23, 500), right - x), font=self.g(23, 500), fill=self.MUTED, anchor="lm")
        today = now.date()
        shown = [h.temp for day in (today, today + timedelta(days=1)) for h in weather.on(day)]
        lo_t, hi_t = (min(shown), max(shown)) if shown else (0.0, 1.0)
        self.weather_day(d, hh, "Today", today, weather, now, lo_t, hi_t)
        y = hh + 362
        d.line((40, y, W - 40, y), fill=(70, 54, 32), width=1)
        _stamp(d, W // 2, y, _rule_mask(240, 12), GOLD_DIM)
        self.weather_day(d, y, "Tomorrow", today + timedelta(days=1), weather, now, lo_t, hi_t)
        return img

    # ---- screensaver ----

    def saver_alert(self, d: ImageDraw.ImageDraw, cx: float, y: float, alerts: list, now: datetime) -> float:
        color = _mix(self.alert_color(alerts[0].severity), BLACK, 0.2)
        f = self.g(32, 700)
        text = fit(d, self.alert_text(alerts, now), f, 820)
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
        clock_f, suffix_f = self.cap(190, 600), self.cap(50, 700)
        cw, sw = d.textlength(clock, font=clock_f), d.textlength(suffix, font=suffix_f)
        x = cx - (cw + 18 + sw) / 2
        _text(d, (x, top + 112), clock, font=clock_f, fill=self.NIGHT_INK, anchor="lm")
        _text(d, (x + cw + 18, top + 160), suffix, font=suffix_f, fill=self.NIGHT_MUTED, anchor="lm")
        _stamp(d, cx, top + 232, _rule_mask(420, 14), (64, 50, 30))
        _text(d, (cx, top + 278), f"{now:%A}, {now:%B} {now.day}".upper(), font=self.cap(40, 600), fill=self.NIGHT_INK,
              anchor="mm")

        y = top + 350
        hour = weather.at(now) if weather else None
        if hour:
            text = f"{_temp(hour.temp)}  {describe(hour.code)[1]}"
            info = weather.days.get(now.date())
            if info:
                text += f"   High {_temp(info.high)}  Low {_temp(info.low)}"
            f = self.g(34, 600)
            tw = d.textlength(text, font=f)
            R.weather_icon(d, cx - tw / 2 - 12, y, 56, describe(hour.code)[0], hour.is_day, self.NIGHT_BG, False,
                           tuple(_mix(c, BLACK, 0.45) for c in self.ICON_COLORS), self.FAINT)
            _text(d, (cx + 34, y), text, font=f, fill=self.NIGHT_MUTED, anchor="mm")
            y += 74
        if alerts:
            y = self.saver_alert(d, cx, y, alerts, now)

        lead_f, title_f = self.cap(28, 700), self.g(32, 600)
        for ev, lead in self.coming(now, events, 2 if alerts else 3):
            color = _mix(_lift(self.color(ev), 120), BLACK, 0.4)
            lead = lead.upper()
            lw = d.textlength(lead, font=lead_f)
            title = fit(d, ev.title, title_f, 860 - lw - 18)
            x = cx - (lw + 18 + d.textlength(title, font=title_f)) / 2
            _text(d, (x, y), lead, font=lead_f, fill=color, anchor="lm")
            _text(d, (x + lw + 18, y - 2), title, font=title_f, fill=self.NIGHT_MUTED, anchor="lm")
            y += 48
        return img
