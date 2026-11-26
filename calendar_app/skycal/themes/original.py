"""The original look: light paper, blue accent, rounded cards. Every other theme is a subclass
of this one and replaces what it draws differently; what a subclass leaves alone looks as here.

Contract every theme keeps (frame.py relies on it):
- Pictures are W x H (1280 x 800) RGB.
- The header buttons are drawn inside render.NAV's boxes (and the weather screen's Back button
  inside render.CLOSE): the touch handler finds them there, whatever they look like.
- Tappable things drawn elsewhere go into `hits` as (x0, y0, x1, y1, target), see render.py.
- render_week() calls `pause()` (when given) after each of the seven days.
- Never draw on `base` (a week picture that is kept and shown again); draw on a copy.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PIL import Image, ImageDraw

from .. import alerts as al
from .. import render as R
from ..feed import Event, on_day
from ..render import CLOSE, H, NAV, W, _mix, _temp, _text, fit, fmt_time, fmt_until, fmt_when, wrap
from ..weather import Weather, describe


class Original:
    name = "original"
    label = "Original"

    HEADER_H = R.HEADER_H
    DAYHEAD_H = R.DAYHEAD_H
    PAD = R.PAD
    ALERT_H = 44                 # the band of weather alerts at the foot of the week
    GEAR_INK: tuple | None = None  # colour of the settings gear; the text colour when None

    BG = R.BG
    INK = R.INK
    MUTED = R.MUTED
    FAINT = R.FAINT
    RULE = R.RULE
    TODAY_BG = R.TODAY_BG
    ACCENT = R.ACCENT
    WARN = R.WARN
    CAL_COLORS = R.CAL_COLORS
    NIGHT_BG = R.NIGHT_BG
    NIGHT_INK = R.NIGHT_INK
    NIGHT_MUTED = R.NIGHT_MUTED
    ICON_COLORS = R.ICON_COLORS
    TEMP_LINE, TEMP_DOT = (235, 150, 60), (225, 120, 30)
    SEVERITY_COLORS = {"Extreme": (150, 20, 60), "Severe": (200, 40, 40), "Moderate": (220, 110, 20),
                       "Minor": (190, 140, 0), "Unknown": (110, 110, 120)}
    DIM = R.dim_table((24, 24, 28), 0.55)       # what is behind a card

    # ---- building blocks a theme is likely to change ----

    def f(self, size: int, bold: bool = False):
        """The font for text of `size` pixels."""
        return R.font(size, bold)

    def color(self, ev: Event) -> tuple:
        return R.event_color(ev, self.CAL_COLORS)

    def alert_color(self, severity: str) -> tuple:
        return self.SEVERITY_COLORS.get(severity, self.SEVERITY_COLORS["Unknown"])

    def canvas(self, screen: str) -> Image.Image:
        """A new picture for `screen` ("week", "weather" or "saver") with its background drawn."""
        return Image.new("RGB", (W, H), self.NIGHT_BG if screen == "saver" else self.BG)

    def icon(self, d: ImageDraw.ImageDraw, cx: float, cy: float, size: int, name: str, is_day: bool = True,
             bg: tuple | None = None, fade: bool = False) -> None:
        R.weather_icon(d, cx, cy, size, name, is_day, self.BG if bg is None else bg, fade, self.ICON_COLORS, self.FAINT)

    def button(self, d: ImageDraw.ImageDraw, box: tuple, label: str, size: int, lift: int = 0) -> None:
        x0, y0, x1, y1 = box
        d.rounded_rectangle(box, radius=12, fill=self.TODAY_BG, outline=self.RULE, width=2)
        _text(d, ((x0 + x1) // 2, (y0 + y1) // 2 - lift), label, font=self.f(size, True), fill=self.INK, anchor="mm")

    # ---- week ----

    def week_title(self, start: date) -> str:
        end = start + timedelta(days=6)
        if start.month == end.month:
            return f"{start:%B} {start.day} to {end.day}, {end.year}"
        if start.year == end.year:
            return f"{start:%b} {start.day} to {end:%b} {end.day}, {end.year}"
        return f"{start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year}"

    def header(self, d: ImageDraw.ImageDraw, start: date, now: datetime, note: str, stale: bool,
               weather: Weather | None) -> None:
        title = self.week_title(start)
        hh = self.HEADER_H
        _text(d, (20, hh // 2), title, font=self.f(34, True), fill=self.INK, anchor="lm")

        right = NAV["settings"][0] - 18
        if note:       # the clock above, the status below it
            room = right - (20 + d.textlength(title, font=self.f(34, True)) + 24)
            _text(d, (right, 27), fmt_time(now), font=self.f(24, True), fill=self.INK, anchor="rm")
            _text(d, (right, 54), fit(d, note, self.f(15), room), font=self.f(15), fill=self.WARN if stale else self.MUTED,
                  anchor="rm")
        else:
            _text(d, (right, hh // 2), fmt_time(now), font=self.f(28, True), fill=self.INK, anchor="rm")
        self.settings_button(d)
        self.weather_button(d, weather.at(now) if weather else None)
        self.button(d, NAV["prev"], "‹", 36, 4)
        self.button(d, NAV["today"], "Today", 22)
        self.button(d, NAV["next"], "›", 36, 4)

    def settings_button(self, d: ImageDraw.ImageDraw) -> None:
        """The gear that shows the QR code for the settings page: the style's button with a gear on it."""
        box = NAV["settings"]
        self.button(d, box, "", 22)
        R.gear_icon(d, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, 30, self.GEAR_INK or self.INK)

    def weather_button(self, d: ImageDraw.ImageDraw, hour) -> None:
        """The weather button: the current conditions when there is a forecast, else the word."""
        x0, y0, x1, y1 = NAV["weather"]
        if hour:
            d.rounded_rectangle((x0, y0, x1, y1), radius=12, fill=self.TODAY_BG, outline=self.RULE, width=2)
            self.icon(d, x0 + 44, (y0 + y1) // 2, 40, describe(hour.code)[0], hour.is_day, self.TODAY_BG)
            _text(d, (x0 + 80, (y0 + y1) // 2), _temp(hour.temp), font=self.f(26, True), fill=self.INK, anchor="lm")
        else:
            self.button(d, NAV["weather"], "Weather", 22)

    def day_head(self, d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, today: date) -> None:
        """Column background, weekday and date of one day."""
        is_today = day == today
        top = self.HEADER_H
        if is_today:
            d.rectangle((x0, top, x1, H), fill=self.TODAY_BG)
        cx = (x0 + x1) // 2
        _text(d, (cx, top + 20), f"{day:%a}".upper(), font=self.f(17, True), fill=self.ACCENT if is_today else self.MUTED,
              anchor="mm")
        if is_today:
            d.ellipse((cx - 23, top + 33, cx + 23, top + 79), fill=self.ACCENT)
        _text(d, (cx, top + 56), str(day.day), font=self.f(28, True),
              fill=(255, 255, 255) if is_today else (self.MUTED if day < today else self.INK), anchor="mm")

    def event_label(self, ev: Event, day: date, now: datetime) -> tuple[str, bool]:
        """(the line above an event's title in the week, whether the event is going on now)."""
        lo = datetime.combine(day, time.min, now.tzinfo)
        current = not ev.all_day and ev.start <= now < ev.end
        label = "" if ev.all_day else (fmt_time(ev.start) if ev.start >= lo else "continues")
        if current:
            label = f"Now, until {fmt_time(ev.end)}" if ev.end.date() == day else "Now"
        return label, current

    TITLE_SIZE, LABEL_SIZE, LINE_H, LABEL_H = 18, 15, 22, 19

    def plate_height(self, label: str, lines: list) -> int:
        return 10 + (self.LABEL_H if label else 0) + self.LINE_H * len(lines) + 6

    def plate(self, d: ImageDraw.ImageDraw, box: tuple, ev: Event, label: str, lines: list, over: bool, current: bool,
              back: tuple) -> None:
        """One event in the week. `over`: it has ended (drawn faded); `current`: going on now;
        `back`: the column's colour behind it."""
        left, y, right, y1 = box
        color = self.color(ev)
        if ev.all_day:
            d.rounded_rectangle(box, radius=8, fill=_mix(color, back, 0.6) if over else color)
            ink = sub = (255, 255, 255)
        else:
            tint = _mix(color, (255, 255, 255), 0.86)
            d.rounded_rectangle(box, radius=8, fill=_mix(tint, back, 0.5) if over else tint,
                                outline=color if current else None, width=2)
            d.rounded_rectangle((left, y, left + 5, y1), radius=2, fill=_mix(color, back, 0.6) if over else color)
            ink, sub = (self.MUTED, _mix(color, back, 0.45)) if over else (self.INK, color)
        ty = y + 7
        if label:
            _text(d, (left + 12, ty), label, font=self.f(self.LABEL_SIZE, True) if current else self.f(self.LABEL_SIZE), fill=sub)
            ty += self.LABEL_H
        for line in lines:
            _text(d, (left + 12, ty), line, font=self.f(self.TITLE_SIZE, True), fill=ink)
            ty += self.LINE_H

    def more(self, d: ImageDraw.ImageDraw, cx: int, bottom: int, count: int) -> None:
        _text(d, (cx, bottom - 16), f"+{count} more", font=self.f(16, True), fill=self.ACCENT, anchor="mm")

    def day(self, d: ImageDraw.ImageDraw, x0: int, x1: int, day: date, events: list[Event], now: datetime,
            hits: list | None, bottom: int = H) -> None:
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
            over = ev.end <= now and (ev.all_day or ev.start < now)        # finished: drawn faded
            label, current = self.event_label(ev, day, now)
            lines = wrap(d, ev.title, self.f(self.TITLE_SIZE, True), text_w, 3)
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

    def grid(self, d: ImageDraw.ImageDraw, edges: list, bottom: int) -> None:
        """The lines drawn over the finished columns."""
        for x in edges[1:-1]:
            d.line((x, self.HEADER_H, x, bottom), fill=self.RULE, width=1)
        d.line((0, self.HEADER_H + self.DAYHEAD_H, W, self.HEADER_H + self.DAYHEAD_H), fill=self.RULE, width=1)

    def header_rule(self, d: ImageDraw.ImageDraw) -> None:
        d.line((0, self.HEADER_H, W, self.HEADER_H), fill=self.RULE, width=2)

    def message_box(self, d: ImageDraw.ImageDraw, message: str) -> None:
        lines = wrap(d, message, self.f(24), 700, 4)
        half = 28 + 17 * len(lines)
        d.rounded_rectangle((W // 2 - 400, 460 - half, W // 2 + 400, 460 + half), radius=18, fill=self.TODAY_BG,
                            outline=self.RULE, width=2)
        for i, line in enumerate(lines):
            _text(d, (W // 2, 460 - 17 * (len(lines) - 1) + 34 * i), line, font=self.f(24), fill=self.INK, anchor="mm")

    def alert_text(self, alerts: list, now: datetime) -> str:
        first = alerts[0]
        text = f"{first.event} {al.until(first, now)}".strip()
        if len(alerts) > 1:
            text += f"  +{len(alerts) - 1} more"
        return text

    def alert_band(self, d: ImageDraw.ImageDraw, box: tuple, alerts: list, now: datetime) -> None:
        """The band at the foot of the week that names the most serious alert."""
        x0, y0, x1, y1 = box
        color = self.alert_color(alerts[0].severity)
        d.rectangle(box, fill=color)
        cy = (y0 + y1) // 2
        R.warn_icon(d, x0 + 34, cy, 26, (255, 255, 255), color)
        hint = "Tap for details"
        hint_w = d.textlength(hint, font=self.f(17))
        _text(d, (x0 + 60, cy), fit(d, self.alert_text(alerts, now), self.f(21, True), x1 - x0 - 100 - hint_w),
              font=self.f(21, True), fill=(255, 255, 255), anchor="lm")
        _text(d, (x1 - 20, cy), hint, font=self.f(17), fill=_mix(color, (255, 255, 255), 0.75), anchor="rm")

    def render_week(self, start: date, events: list[Event], now: datetime, note: str, stale: bool, hits: list | None,
                    weather: Weather | None, message: str, pause, alerts: list) -> Image.Image:
        tz = now.tzinfo
        img = self.canvas("week")
        d = ImageDraw.Draw(img)
        self.header(d, start, now, note, stale, weather)
        self.header_rule(d)
        bottom = H - self.ALERT_H if alerts else H
        edges = [round(i * W / 7) for i in range(8)]
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

    # ---- cards over the week ----

    def card(self, base: Image.Image, height: int, width: int = 820) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple]:
        """A copy of `base`, darkened, with an empty card in the middle: (image, draw, card box)."""
        img = base.point(self.DIM)
        d = ImageDraw.Draw(img)
        height = min(height, H - 40)
        box = ((W - width) // 2, (H - height) // 2, (W + width) // 2, (H + height) // 2)
        d.rounded_rectangle(box, radius=22, fill=self.TODAY_BG)
        return img, d, box

    def card_strip(self, d: ImageDraw.ImageDraw, box: tuple, color: tuple) -> None:
        """The colour strip down the left edge of a detail card."""
        x0, y0, x1, y1 = box
        d.rounded_rectangle((x0, y0, x0 + 44, y1), radius=22, fill=color)
        d.rectangle((x0 + 12, y0, x0 + 44, y1), fill=self.TODAY_BG)

    def card_hint(self, d: ImageDraw.ImageDraw, box: tuple, text: str) -> None:
        x0, y0, x1, y1 = box
        _text(d, ((x0 + x1) // 2, y1 - 28), text, font=self.f(16), fill=self.MUTED, anchor="mm")

    def render_detail(self, base: Image.Image, ev: Event, now: datetime) -> Image.Image:
        probe = ImageDraw.Draw(base)
        text_w = 820 - 46 - 36
        title_f, when_f, label_f, body_f = self.f(34, True), self.f(23), self.f(15, True), self.f(21)
        title = wrap(probe, ev.title, title_f, text_w, 3)
        when = wrap(probe, fmt_when(ev), when_f, text_w, 2)
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
            _text(d, (x, y), line, font=when_f, fill=self.INK)
            y += 30
        if until:
            _text(d, (x, y + 2), until, font=self.f(19, True), fill=self.MUTED if until == "Ended" else color)
            y += 30
        for label, lines in (("WHERE", where), ("NOTES", notes)):
            if lines:
                y += 22
                _text(d, (x, y), label, font=label_f, fill=self.MUTED)
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
        _text(d, (x0 + 36, y0 + 46), f"{day:%A}, {day:%B} {day.day}", font=self.f(30, True), fill=self.INK, anchor="lm")
        count = f"{len(everything)} event" + ("" if len(everything) == 1 else "s")
        _text(d, (x1 - 36, y0 + 46), count, font=self.f(18), fill=self.MUTED, anchor="rm")
        lo = datetime.combine(day, time.min, tz)
        y = y0 + 84
        if not todays:
            _text(d, (x0 + 36, y + row_h // 2), "Nothing on this day", font=self.f(21), fill=self.MUTED, anchor="lm")
        for i, ev in enumerate(todays[:rows]):
            if i == rows - 1 and len(todays) > rows:
                d.line((x0 + 30, y, x1 - 30, y), fill=self.RULE, width=1)
                _text(d, (x0 + 36, y + row_h // 2), f"Show {len(todays) - i} more", font=self.f(19, True), fill=self.ACCENT,
                      anchor="lm")
                if hits is not None:
                    hits.append((x0, y, x1, y + row_h, first + i))
                break
            color = self.color(ev)
            over = ev.end <= now and (ev.all_day or ev.start < now)
            d.line((x0 + 30, y, x1 - 30, y), fill=self.RULE, width=1)
            d.rounded_rectangle((x0 + 36, y + 9, x0 + 42, y + row_h - 9), radius=3,
                                fill=_mix(color, self.TODAY_BG, 0.6) if over else color)
            _text(d, (x0 + 58, y + row_h // 2), self.span(ev, lo), font=self.f(19), fill=self.MUTED if over else color,
                  anchor="lm")
            _text(d, (x0 + 250, y + row_h // 2), fit(d, ev.title, self.f(21, True), x1 - 36 - (x0 + 250)),
                  font=self.f(21, True), fill=self.MUTED if over else self.INK, anchor="lm")
            if hits is not None:
                hits.append((x0, y, x1, y + row_h, ev))
            y += row_h
        self.card_hint(d, box, "Tap an event for details, or outside to close")
        return img

    @staticmethod
    def span(ev: Event, lo: datetime) -> str:
        """When an event happens on the day that starts at `lo`, for the day list."""
        if ev.all_day:
            return "All day"
        if ev.start < lo:
            return f"until {fmt_time(ev.end)}" if ev.end <= lo + timedelta(days=1) else "All day"
        return fmt_time(ev.start) if ev.end <= ev.start else f"{fmt_time(ev.start)} to {fmt_time(ev.end)}"

    def alert_when(self, a, now: datetime) -> str:
        parts = []
        if a.onset and a.onset > now:
            parts.append(f"From {fmt_time(a.onset)}" + ("" if a.onset.date() == now.date() else f" {a.onset:%a}"))
        end = al.until(a, now)
        if end:
            parts.append(end if parts else end[0].upper() + end[1:])
        return " ".join(parts)

    def render_alerts(self, base: Image.Image, alerts: list, now: datetime, hits: list | None, index: int) -> Image.Image:
        a = alerts[index]
        width = 900
        probe = ImageDraw.Draw(base)
        text_w = width - 46 - 40
        title_f, when_f, label_f, body_f = self.f(32, True), self.f(22), self.f(15, True), self.f(19)
        color = self.alert_color(a.severity)
        title = wrap(probe, a.event, title_f, text_w, 2)
        when = self.alert_when(a, now)
        area = wrap(probe, a.area, body_f, text_w, 2) if a.area else []
        more = len(alerts) > 1
        fixed = 30 + 40 * len(title) + 8 + (30 if when else 0) + 30 + (24 * len(area) + 10 if area else 0) + 62 + (50 if more else 0)
        room = max(2, (H - 40 - fixed) // 26 - 2)
        what = wrap(probe, a.description or a.headline, body_f, text_w, max(1, room - (4 if a.instruction else 0)))
        todo = wrap(probe, a.instruction, body_f, text_w, max(1, room - len(what) - 2)) if a.instruction and room - len(what) >= 3 else []
        body = 24 + 26 * len(what) + (14 + 24 + 26 * len(todo) if todo else 0)
        img, d, box = self.card(base, fixed + body, width)
        x0, y0, x1, y1 = box
        self.card_strip(d, box, color)
        x, y = x0 + 46, y0 + 30
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=self.INK)
            y += 40
        y += 8
        if when:
            _text(d, (x, y), when, font=when_f, fill=self.INK)
            y += 30
        tag = f"{a.severity.upper()}  ·  {a.sender}" if a.sender else a.severity.upper()
        _text(d, (x, y + 2), fit(d, tag, self.f(16, True), text_w), font=self.f(16, True), fill=color)
        y += 30
        for line in area:
            _text(d, (x, y), line, font=body_f, fill=self.MUTED)
            y += 24
        y += 10 if area else 0
        for label, lines in (("WHAT", what), ("WHAT TO DO", todo)):
            if lines:
                y += 14 if label != "WHAT" else 0
                _text(d, (x, y), label, font=label_f, fill=self.MUTED)
                y += 24
                for line in lines:
                    _text(d, (x, y), line, font=body_f, fill=self.INK)
                    y += 26
        if more:
            ry = y1 - 62 - 46
            d.line((x0 + 30, ry, x1 - 30, ry), fill=self.RULE, width=1)
            nxt = (index + 1) % len(alerts)
            _text(d, (x, ry + 23), f"Next alert: {alerts[nxt].event}  ({nxt + 1} of {len(alerts)})", font=self.f(19, True),
                  fill=self.ACCENT, anchor="lm")
            if hits is not None:
                hits.append((x0, ry, x1, ry + 46, nxt))
        self.card_hint(d, box, "Tap anywhere else to close")
        return img

    # ---- weather ----

    def weather_day(self, d: ImageDraw.ImageDraw, top: int, name: str, day: date, weather: Weather, now: datetime,
                    lo_t: float, hi_t: float) -> None:
        hours = [h for h in weather.on(day) if h.time.hour >= min(6, now.hour if day == now.date() else 6)]
        _text(d, (24, top + 34), name, font=self.f(28, True), fill=self.INK, anchor="lm")
        name_w = d.textlength(name, font=self.f(28, True))
        _text(d, (24 + name_w + 14, top + 36), f"{day:%A}, {day:%B} {day.day}", font=self.f(20), fill=self.MUTED, anchor="lm")
        info = weather.days.get(day)
        if info:
            parts = [describe(info.code)[1], f"High {_temp(info.high)}", f"Low {_temp(info.low)}"]
            if info.sunrise and info.sunset:
                parts += [f"Sunrise {fmt_time(info.sunrise)}", f"Sunset {fmt_time(info.sunset)}"]
            _text(d, (W - 24, top + 36), "   ".join(parts), font=self.f(20), fill=self.INK, anchor="rm")
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
                back = self.TODAY_BG
                d.rounded_rectangle((cx - cw / 2 + 2, top + 62, cx + cw / 2 - 2, top + 340), radius=12, fill=self.TODAY_BG,
                                    outline=self.ACCENT, width=2)
            _text(d, (cx, top + 84), "Now" if current else fmt_time(h.time), font=self.f(17, current),
                  fill=self.ACCENT if current else (self.FAINT if past else self.MUTED), anchor="mm")
            self.icon(d, cx, top + 126, 44, describe(h.code)[0], h.is_day, back, fade=past)
            py = band_top + band_h - (h.temp - lo_t) / span * band_h
            points.append((cx, py, h, past))
            if h.precip is not None and h.precip >= 10 and not past:
                tone = self.ACCENT if h.precip >= 30 else self.MUTED
                d.polygon([(cx, top + 284), (cx - 5, top + 293), (cx + 5, top + 293)], fill=tone)
                d.ellipse((cx - 5, top + 288, cx + 5, top + 298), fill=tone)
                _text(d, (cx, top + 316), f"{h.precip}%", font=self.f(16, h.precip >= 50), fill=tone, anchor="mm")
        for (ax, ay, _, a_past), (bx, by, _, _) in zip(points, points[1:]):
            d.line((ax, ay, bx, by), fill=self.RULE if a_past else self.TEMP_LINE, width=3)
        for cx, py, h, past in points:
            d.ellipse((cx - 5, py - 5, cx + 5, py + 5), fill=self.FAINT if past else self.TEMP_DOT)
            _text(d, (cx, py - 20), _temp(h.temp), font=self.f(20, True), fill=self.FAINT if past else self.INK, anchor="mm")

    def alert_pill(self, d: ImageDraw.ImageDraw, right: int, cy: int, alerts: list) -> int:
        """A small tag naming the most serious alert, ending at x = `right`. Returns its left edge."""
        a = alerts[0]
        text = a.event + (f" +{len(alerts) - 1}" if len(alerts) > 1 else "")
        f = self.f(18, True)
        tw = min(d.textlength(text, font=f), 300)
        x0 = right - tw - 56
        color = self.alert_color(a.severity)
        d.rounded_rectangle((x0, cy - 20, right, cy + 20), radius=12, fill=color)
        R.warn_icon(d, x0 + 22, cy, 20, (255, 255, 255), color)
        _text(d, (x0 + 40, cy), fit(d, text, f, 300), font=f, fill=(255, 255, 255), anchor="lm")
        return int(x0)

    def render_weather(self, weather: Weather | None, now: datetime, message: str, alerts: list) -> Image.Image:
        img = self.canvas("weather")
        d = ImageDraw.Draw(img)
        hh = self.HEADER_H
        _text(d, (20, hh // 2), "Weather", font=self.f(34, True), fill=self.INK, anchor="lm")
        self.button(d, CLOSE, "Back", 22)
        self.header_rule(d)
        right = CLOSE[0] - 20
        if alerts:
            right = self.alert_pill(d, right, (CLOSE[1] + CLOSE[3]) // 2, alerts) - 16
        if weather is None:
            for i, line in enumerate(wrap(d, message or "No forecast yet", self.f(26), W - 200, 4)):
                _text(d, (W // 2, 340 + i * 40), line, font=self.f(26), fill=self.MUTED, anchor="mm")
            return img
        sub = f"{weather.place}, as of {fmt_time(weather.fetched)}" if weather.place else f"As of {fmt_time(weather.fetched)}"
        if now - weather.fetched > timedelta(hours=2):
            sub += f" on {weather.fetched:%b} {weather.fetched.day} (no newer forecast could be fetched)"
        x = 20 + d.textlength("Weather", font=self.f(34, True)) + 20
        _text(d, (x, hh // 2 + 4), fit(d, sub, self.f(20), right - x), font=self.f(20), fill=self.MUTED, anchor="lm")
        today = now.date()
        shown = [h.temp for day in (today, today + timedelta(days=1)) for h in weather.on(day)]
        lo_t, hi_t = (min(shown), max(shown)) if shown else (0.0, 1.0)
        self.weather_day(d, hh, "Today", today, weather, now, lo_t, hi_t)
        d.line((0, hh + 362, W, hh + 362), fill=self.RULE, width=1)
        self.weather_day(d, hh + 362, "Tomorrow", today + timedelta(days=1), weather, now, lo_t, hi_t)
        return img

    # ---- screensaver ----

    def saver_alert(self, d: ImageDraw.ImageDraw, cx: float, y: float, alerts: list, now: datetime) -> float:
        """One line naming the most serious alert; returns the y for what comes below it."""
        color = _mix(self.alert_color(alerts[0].severity), self.NIGHT_INK, 0.25)
        text = self.alert_text(alerts, now)
        f = self.f(30, True)
        tw = d.textlength(fit(d, text, f, 820), font=f)
        R.warn_icon(d, cx - tw / 2 - 26, y, 32, color, self.NIGHT_BG)
        _text(d, (cx + 22, y), fit(d, text, f, 820), font=f, fill=color, anchor="mm")
        return y + 62

    def render_saver(self, now: datetime, events: list[Event], weather: Weather | None, alerts: list) -> Image.Image:
        img = self.canvas("saver")
        d = ImageDraw.Draw(img)
        cx = W // 2 + ((now.minute * 7 + now.hour * 3) % 21 - 10) * 12
        top = 96 + ((now.minute * 11 + now.hour) % 9 - 4) * 12 - (30 if alerts else 0)

        clock, suffix = f"{now.hour % 12 or 12}:{now.minute:02d}", "am" if now.hour < 12 else "pm"
        clock_f, suffix_f = self.f(210, True), self.f(56, True)
        cw, sw = d.textlength(clock, font=clock_f), d.textlength(suffix, font=suffix_f)
        x = cx - (cw + 16 + sw) / 2
        _text(d, (x, top + 110), clock, font=clock_f, fill=self.NIGHT_INK, anchor="lm")
        _text(d, (x + cw + 16, top + 158), suffix, font=suffix_f, fill=self.NIGHT_MUTED, anchor="lm")
        _text(d, (cx, top + 262), f"{now:%A}, {now:%B} {now.day}", font=self.f(46), fill=self.NIGHT_INK, anchor="mm")

        y = top + 340
        hour = weather.at(now) if weather else None
        if hour:
            text = f"{_temp(hour.temp)}  {describe(hour.code)[1]}"
            info = weather.days.get(now.date())
            if info:
                text += f"   High {_temp(info.high)}  Low {_temp(info.low)}"
            tw = d.textlength(text, font=self.f(32))
            self.icon(d, cx - tw / 2 - 12, y, 56, describe(hour.code)[0], hour.is_day, self.NIGHT_BG)
            _text(d, (cx + 34, y), text, font=self.f(32), fill=self.NIGHT_MUTED, anchor="mm")
            y += 74
        if alerts:
            y = self.saver_alert(d, cx, y, alerts, now)

        for ev, lead in self.coming(now, events, 2 if alerts else 3):
            color = _mix(self.color(ev), self.NIGHT_INK, 0.35)
            lead_f, title_f = self.f(28, True), self.f(28)
            lw = d.textlength(lead, font=lead_f)
            title = fit(d, ev.title, title_f, 860 - lw - 18)
            x = cx - (lw + 18 + d.textlength(title, font=title_f)) / 2
            _text(d, (x, y), lead, font=lead_f, fill=color, anchor="lm")
            _text(d, (x + lw + 18, y), title, font=title_f, fill=self.NIGHT_MUTED, anchor="lm")
            y += 46
        return img

    # ---- settings QR code ----

    SETTINGS_STEPS = ("Point your phone's camera at the code and open the link.",
                      "Your phone must be on the same Wi-Fi as the frame.",
                      "The code works once, and only while it is on screen.")

    def render_settings(self, base: Image.Image, url: str | None, now: datetime, message: str) -> Image.Image:
        """A card with the QR code on the left and how to use it on the right; only words when
        there is no code to show. The code is always black on white, whatever the style."""
        probe = ImageDraw.Draw(base)
        width = 880
        qr = R.qr_image(url, 380) if url else None
        if url and qr is None:
            message = message or "The QR code cannot be drawn on this frame. Type this address on your phone instead:"
        title_f, body_f, small_f = self.f(30, True), self.f(21), self.f(17)
        left = 40 + (qr.width + 36 if qr else 6)
        text_w = width - left - 40
        title = wrap(probe, "Settings on your phone", title_f, text_w, 2)
        paras = [wrap(probe, t, body_f, text_w, 3) for t in (self.SETTINGS_STEPS if qr else (message,))]
        address = wrap(probe, url, small_f, text_w, 3) if url else []
        text_h = 40 * len(title) + 14 + sum(28 * len(p) + 14 for p in paras) + (10 + 22 * len(address) if address else 0)
        height = max(qr.height if qr else 0, text_h) + 40 + 70
        img, d, box = self.card(base, height, width)
        x0, y0, x1, y1 = box
        if qr:
            img.paste(qr, (x0 + 40, y0 + 40))
        x, y = x0 + left, y0 + 44
        for line in title:
            _text(d, (x, y), line, font=title_f, fill=self.INK)
            y += 40
        y += 14
        for para in paras:
            for line in para:
                _text(d, (x, y), line, font=body_f, fill=self.INK)
                y += 28
            y += 14
        y += 10
        for line in address:
            _text(d, (x, y), line, font=small_f, fill=self.MUTED)
            y += 22
        self.card_hint(d, box, "Tap anywhere to close")
        return img

    @staticmethod
    def coming(now: datetime, events: list[Event], most: int = 3) -> list[tuple[Event, str]]:
        """Up to `most` timed events still to come today or tomorrow, with "Now", "4p" or "Tomorrow 9a"."""
        tz = now.tzinfo
        found = []
        for day in (now.date(), now.date() + timedelta(days=1)):
            for ev in on_day(events, day, tz):
                if not ev.all_day and ev.end > now and ev.start.date() == day and len(found) < most:
                    found.append(ev)           # listed under the day it starts on, so never twice
        out = []
        for ev in found:
            if ev.start <= now:
                lead = "Now"
            else:
                lead = fmt_time(ev.start) if ev.start.date() == now.date() else f"Tomorrow {fmt_time(ev.start)}"
            out.append((ev, lead))
        return out
