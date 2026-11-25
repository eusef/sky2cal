"""Settings page for the frame: python3 -m skyadmin

Served on the USB cable network (the frame is 192.168.77.1 on usb0, port 80). Holding the cable
is the authentication, so there is no login there. Plain HTML forms, no JavaScript.

Also served on Wi-Fi, port 8080 (the firewall opens it on wlan0 only), but only to a phone that
has scanned the QR code the calendar shows after a tap on its gear (skycal/remote.py): the code
in it works once, within ten minutes, while the card is on screen, and gives that browser a
session cookie good for an hour. Any other request on that port is turned away.

Rules kept throughout: every change is a POST carrying a per-process token; requests whose Host
is not the address they arrived on are refused (DNS rebinding); feed addresses and passphrases
are never logged or shown in full; system commands are argument lists, never a shell.
"""
import argparse
import hmac
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import tomllib
from collections import deque
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from zoneinfo import ZoneInfo, available_timezones

from skycal import remote as RM

from . import settings as S

WLAN = "wlan0"
MAX_BODY = 16 * 1024
SESSION_S = 3600             # how long a scanned QR code keeps a phone signed in
MAX_SESSIONS = 16
COOKIE = "skyadmin"

# Status messages shown after a redirect. Only these codes are accepted from the URL.
MESSAGES = {
    "saved": (True, "Saved. The calendar is restarting with the new settings."),
    "feed_added": (True, "Calendar added. The calendar is restarting."),
    "feed_removed": (True, "Calendar removed. The calendar is restarting."),
    "saved_no_restart": (False, "Saved, but the calendar did not restart. Try \"Restart the calendar\" at the bottom of this page."),
    "wifi_saved": (True, "Saved. The frame is now joining that network. This can take a minute. Reload this page to check the status."),
    "wifi_no_restart": (False, "Saved, but Wi-Fi did not restart. Try \"Restart the frame\" at the bottom of this page."),
    "scan_done": (True, "Search finished. The networks found are listed under Wi-Fi."),
    "scan_failed": (False, "The frame could not search for networks just now. Wait a few seconds and try again, or type the network name."),
    "restarted": (True, "The calendar has been restarted."),
    "restart_failed": (False, "The calendar did not restart. Try \"Restart the frame\"."),
    "rebooting": (True, "The frame is restarting. This page will work again in about a minute."),
    "bad_feed": (False, "That calendar address was not accepted. It must start with https:// and contain no spaces."),
    "dup_feed": (False, "That calendar is already in the list."),
    "too_many_feeds": (False, f"The list is full ({S.MAX_FEEDS} calendars). Remove one first."),
    "no_feed": (False, "That calendar was not found. It may already have been removed."),
    "color_saved": (True, "Colour saved. The calendar is restarting."),
    "bad_color": (False, "That colour was not accepted."),
    "bad_theme": (False, "Choose one of the listed styles."),
    "bad_tz": (False, "That time zone was not recognised. Use a name such as America/Los_Angeles or Europe/London."),
    "bad_weekday": (False, "Choose a day for the week to start on."),
    "bad_refresh": (False, f"The update interval must be a whole number from {S.REFRESH_MIN} to {S.REFRESH_MAX} minutes."),
    "bad_place": (False, f"The town was not accepted. Use a name of at most {S.MAX_PLACE_LEN} characters, or leave it empty."),
    "bad_units": (False, "Choose Fahrenheit or Celsius."),
    "bad_saver": (False, f"The screensaver time must be a whole number from 0 to {S.SAVER_MAX} minutes."),
    "bad_ssid": (False, "The network name must be between 1 and 32 characters."),
    "bad_pass": (False, "The Wi-Fi password must be 8 to 63 characters (letters, digits and common symbols)."),
    "need_pass": (False, "Type the Wi-Fi password, or tick the box if the network has no password (not both)."),
    "config_broken": (False, "The calendar settings file on the frame could not be read, so nothing was changed."),
    "write_failed": (False, "The change could not be saved on the frame. Nothing was changed."),
}

CSS = """
:root{color-scheme:light dark}
body{font:17px/1.45 system-ui,sans-serif;max-width:40em;margin:0 auto;padding:1em}
h1{font-size:1.5em}h2{font-size:1.2em;margin:0 0 .6em}
section{border:1px solid #8886;border-radius:10px;padding:1em;margin:1em 0}
label{display:block;margin:.7em 0 .2em}
label.c{display:flex;gap:.5em;align-items:center}
input[type=text],input[type=password],input[type=url],input[type=number],select{font:inherit;width:100%;box-sizing:border-box;padding:.45em}
input[type=checkbox]{width:1.2em;height:1.2em}
button,a.b{font:inherit;display:inline-block;margin:.8em .4em 0 0;padding:.5em 1em;border:1px solid #8889;border-radius:8px;background:#8882;color:inherit;text-decoration:none;cursor:pointer}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.3em .5em .3em 0;vertical-align:top}
th{font-weight:normal;opacity:.75;width:42%}
ul{padding-left:1.2em}li{margin:.3em 0}li form{display:inline}li button{margin:0 0 0 .6em;padding:.2em .7em}
.msg{padding:.8em 1em;border-radius:10px;border:2px solid #2a8a2a}.msg.bad{border-color:#c33}
.hint{opacity:.75;font-size:.9em;margin:.2em 0}code{word-break:break-all}
"""


def log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


class System:
    """Runs system commands; in dry-run mode they are logged and answered with canned output."""

    CANNED = {
        ("iw", "dev", WLAN, "scan"): "BSS 00:11:22:33:44:55(on wlan0)\n\tSSID: Home Network\n"
                                     "BSS 00:11:22:33:44:66(on wlan0)\n\tSSID: Caf\\xc3\\xa9 <Guest>\n"
                                     "BSS 00:11:22:33:44:77(on wlan0)\n\tSSID: \nBSS 00:11:22:33:44:88(on wlan0)\n\tSSID: Home Network\n",
        ("iw", "dev", WLAN, "link"): "Connected to 00:11:22:33:44:55 (on wlan0)\n\tSSID: Home Network\n\tsignal: -52 dBm\n",
        ("ip", "-4", "-o", "addr", "show", "dev", WLAN): "3: wlan0    inet 192.0.2.10/24 brd 192.0.2.255 scope global dynamic wlan0\n",
    }

    def __init__(self, dry_run: bool):
        self.dry_run = dry_run

    def run(self, *cmd: str, timeout: float = 30) -> tuple[bool, str]:
        if self.dry_run:
            log("dry-run, not executed: " + " ".join(cmd))
            return True, self.CANNED.get(cmd, "")
        try:
            p = subprocess.run(list(cmd), stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"command failed: {' '.join(cmd)}: {type(e).__name__}")
            return False, ""
        if p.returncode != 0:
            log(f"command failed: {' '.join(cmd)}: exit {p.returncode}")
        return p.returncode == 0, p.stdout.decode("utf-8", "replace")


def iw_unescape(s: str) -> str:
    """iw prints bytes outside printable ASCII as \\xNN and a backslash as \\\\."""
    raw = re.sub(rb"\\(?:x([0-9a-fA-F]{2})|(\\))",
                 lambda m: bytes([int(m.group(1), 16)]) if m.group(1) else b"\\",
                 s.encode("utf-8", "replace"))
    return raw.decode("utf-8", "replace")


def parse_ssids(text: str) -> list:
    found = []
    for line in text.splitlines():
        m = re.fullmatch(r"\s+SSID: (.*)", line)
        if m:
            name = iw_unescape(m.group(1))
            if name.strip("\x00 ") and S.valid_ssid(name) and name not in found:
                found.append(name)
    return found


class App:
    def __init__(self, args):
        self.bind, self.port = args.bind, args.port
        self.config, self.wpa_conf, self.state_dir = args.config, args.wpa_conf, args.state_dir
        self.sys = System(args.dry_run)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.scan = None  # list of names from the last search, or None before the first one
        self.hosts = {self.bind, f"{self.bind}:{self.port}"}
        self.origins = {f"http://{h}" for h in self.hosts if self.port == 80 or ":" in h}
        self._zones = None
        self.sessions: dict[str, float] = {}     # Wi-Fi session id -> when it ends (time.monotonic())
        self.used = deque(maxlen=64)             # codes already redeemed
        self.auth_lock = threading.Lock()

    # ---- Wi-Fi sign-in by QR code ----

    def redeem(self, code: str) -> str | None:
        """A new session id for a code the calendar is showing, not yet used and not too old; else None."""
        saved = RM.shown_code(self.state_dir)
        if saved is None or not code or len(code) > 64:
            return None
        shown_code, shown = saved
        if not hmac.compare_digest(code.encode(), shown_code.encode()) or not 0 <= time.time() - shown <= RM.CODE_S:
            return None
        with self.auth_lock:
            if shown_code in self.used:
                return None
            self.used.append(shown_code)
            now = time.monotonic()
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            while len(self.sessions) >= MAX_SESSIONS:
                del self.sessions[min(self.sessions, key=self.sessions.get)]
            sid = secrets.token_urlsafe(32)
            self.sessions[sid] = now + SESSION_S
            return sid

    def session_ok(self, sid: str) -> bool:
        with self.auth_lock:
            ends = self.sessions.get(sid) if sid else None
            return ends is not None and ends > time.monotonic()

    def zones(self) -> list:
        if self._zones is None:
            try:
                self._zones = sorted(z for z in available_timezones()
                                     if "/" in z and not z.startswith(("Etc/", "posix/", "right/", "SystemV/")))
            except Exception:
                self._zones = []
        return self._zones

    # ---- status ----

    def status_rows(self, cfg: dict) -> list:
        ok, link = self.sys.run("iw", "dev", WLAN, "link", timeout=10)
        names = parse_ssids(link) if ok else []
        if not ok:
            wifi = "Unknown (the Wi-Fi radio did not answer)"
        elif names and link.startswith("Connected"):
            wifi = f"Connected to {names[0]}"
        else:
            wifi = "Not connected"
        ok, addr = self.sys.run("ip", "-4", "-o", "addr", "show", "dev", WLAN, timeout=10)
        m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", addr) if ok else None
        newest = 0.0
        try:
            for root, _dirs, files in os.walk(self.state_dir):
                for name in files:
                    if name.startswith(RM.CODE_FILE):     # the QR code's file, not a calendar
                        continue
                    try:
                        newest = max(newest, os.stat(os.path.join(root, name)).st_mtime)
                    except OSError:
                        pass
        except OSError:
            pass
        if newest:
            try:
                tz = ZoneInfo(cfg.get("timezone", ""))
            except Exception:
                tz = None
            fetched = datetime.fromtimestamp(newest, tz).strftime("%A %d %B %Y, %H:%M")
        else:
            fetched = "Not yet"
        return [("Wi-Fi", wifi), ("Address on Wi-Fi", m.group(1) if m else "None"),
                ("Calendars last fetched", fetched), ("System version", f"Linux {os.uname().release}")]

    # ---- pages ----

    def hidden(self) -> str:
        return f'<input type="hidden" name="token" value="{escape(self.token)}">'

    def html(self, title: str, body: str) -> str:
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f"<title>{escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>")

    def page(self, msg: str, ssid: str) -> str:
        h = self.hidden()
        try:
            cfg, broken = S.load_config(self.config), False
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            cfg, broken = {}, True
        b = ["<h1>Calendar frame settings</h1>"]
        if msg in MESSAGES:
            good, text = MESSAGES[msg]
            b.append(f'<p class="msg{"" if good else " bad"}">{escape(text)}</p>')
        if broken:
            b.append(f'<p class="msg bad">{escape(MESSAGES["config_broken"][1])}</p>')

        b.append('<section id="status"><h2>Status</h2><table>')
        b += [f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in self.status_rows(cfg)]
        b.append('</table><a class="b" href="/">Check again</a></section>')

        b.append('<section id="wifi"><h2>Wi-Fi</h2>'
                 f'<form method="post" action="/wifi/scan">{h}<button>Search for networks</button></form>')
        if self.scan is not None:
            if self.scan:
                b.append('<p class="hint">Networks found. Choose one to fill in its name below.</p><ul>')
                b += [f'<li><a href="/?ssid={quote(n, safe="")}#wifi">{escape(n)}</a></li>' for n in self.scan]
                b.append("</ul>")
            else:
                b.append('<p class="hint">No networks were found. Try again, or type the name below.</p>')
        b.append(f'<form method="post" action="/wifi/join" autocomplete="off">{h}'
                 '<label for="ssid">Network name</label>'
                 f'<input type="text" id="ssid" name="ssid" maxlength="32" required value="{escape(ssid)}">'
                 '<label for="pw">Wi-Fi password</label>'
                 '<input type="password" id="pw" name="passphrase" maxlength="63" autocomplete="off">'
                 '<label class="c"><input type="checkbox" name="open" value="1"> This network has no password</label>'
                 '<p class="hint">The frame keeps the networks it already knows. Capital letters matter in both boxes.</p>'
                 "<button>Join this network</button></form></section>")

        feeds = [f for f in cfg.get("feeds", []) if isinstance(f, str)] if isinstance(cfg.get("feeds", []), list) else []
        b.append('<section id="calendar"><h2>Calendar</h2>')
        if feeds:
            b.append('<p class="hint">Calendars shown on the frame. Addresses are private, so only the start and end are shown.</p><ul>')
            colors = cfg.get("feed_colors", {}) if isinstance(cfg.get("feed_colors", {}), dict) else {}
            for f in feeds:
                fid = S.feed_id(f)
                chosen = colors.get(fid) if isinstance(colors.get(fid), str) and S.valid_color(colors.get(fid)) else ""
                state = "Colour on the frame" if chosen else "Colour: the style's own for this calendar"
                reset = (f'<form method="post" action="/feed/color">{h}<input type="hidden" name="id" value="{fid}">'
                         '<input type="hidden" name="color" value=""><button>Use the style\'s colour</button></form>' if chosen else "")
                b.append(f'<li><code>{escape(S.mask_feed(f))}</code>'
                         f'<form method="post" action="/feed/color">{h}<input type="hidden" name="id" value="{fid}">'
                         f'<label class="c">{state} <input type="color" name="color" value="{chosen or "#006edc"}"></label>'
                         f'<button>Set colour</button></form>{reset}'
                         f'<form method="post" action="/feed/remove">{h}'
                         f'<input type="hidden" name="id" value="{fid}"><button>Remove</button></form></li>')
            b.append("</ul>")
            b.append('<p class="hint">An event that has a colour of its own in the calendar keeps it. Google Calendar does '
                     'not put the colours you pick for single events into its iCalendar address, so those events show '
                     'in their calendar\'s colour.</p>')
        else:
            b.append('<p class="hint">No calendars yet. Add one below.</p>')
        b.append(f'<form method="post" action="/feed/add" autocomplete="off">{h}'
                 '<label for="url">Add a calendar (its iCalendar address, starting with https://)</label>'
                 '<input type="url" id="url" name="url" required maxlength="2000" autocomplete="off" placeholder="https://">'
                 '<p class="hint">For a Google calendar, this is "Secret address in iCal format" in the calendar\'s settings. Treat it like a password.</p>'
                 "<button>Add calendar</button></form><hr>")
        wd = cfg.get("first_weekday", 6)
        days = "".join(f'<option value="{i}"{" selected" if i == wd else ""}>{d}</option>'
                       for i, d in enumerate(S.WEEKDAYS))
        zones = "".join(f'<option value="{escape(z)}">' for z in self.zones())
        b.append(f'<form method="post" action="/calendar/save">{h}'
                 '<label for="tz">Time zone</label>'
                 f'<input type="text" id="tz" name="timezone" list="zones" required maxlength="64" value="{escape(str(cfg.get("timezone", "")))}">'
                 f'<datalist id="zones">{zones}</datalist>'
                 '<p class="hint">Region and city, for example America/Los_Angeles.</p>'
                 f'<label for="wd">The week starts on</label><select id="wd" name="first_weekday">{days}</select>'
                 '<label for="rm">Check the calendars for changes every (minutes)</label>'
                 f'<input type="number" id="rm" name="refresh_minutes" required min="{S.REFRESH_MIN}" max="{S.REFRESH_MAX}" '
                 f'value="{escape(str(cfg.get("refresh_minutes", 10)))}">'
                 "<button>Save calendar settings</button></form></section>")

        wu = cfg.get("weather_units", S.WEATHER_UNITS[0])
        units = "".join(f'<option value="{u}"{" selected" if u == wu else ""}>{u.capitalize()}</option>' for u in S.WEATHER_UNITS)
        b.append(f'<section id="weather"><h2>Weather</h2><form method="post" action="/weather/save">{h}'
                 '<label for="wp">Town for the weather forecast</label>'
                 f'<input type="text" id="wp" name="weather_place" maxlength="{S.MAX_PLACE_LEN}" '
                 f'value="{escape(str(cfg.get("weather_place", "")))}">'
                 '<p class="hint">A town and its state or country, for example "Springfield, Illinois", or a postal code. '
                 'Leave it empty to turn the weather off. The frame looks the place up at open-meteo.com '
                 'and fetches the forecast for it from there, giving the frame\'s time zone; nothing else is sent.</p>'
                 f'<label for="wu">Temperatures in</label><select id="wu" name="weather_units">{units}</select>'
                 "<button>Save weather settings</button></form></section>")

        fx = " checked" if cfg.get("touch_flip_x", True) else ""
        fy = " checked" if cfg.get("touch_flip_y", True) else ""
        current = cfg.get("theme", S.THEME_DEFAULT)
        styles = "".join(f'<option value="{escape(n)}"{" selected" if n == current else ""}>{escape(lb)}</option>'
                         for n, lb in S.THEMES)
        b.append(f'<section id="display"><h2>Display</h2><form method="post" action="/display/save">{h}'
                 f'<label for="th">Style</label><select id="th" name="theme">{styles}</select>'
                 '<label for="ss">Show the screensaver after this many minutes without a touch</label>'
                 f'<input type="number" id="ss" name="screensaver_minutes" required min="0" max="{S.SAVER_MAX}" '
                 f'value="{escape(str(cfg.get("screensaver_minutes", S.SAVER_DEFAULT)))}">'
                 '<p class="hint">The screensaver dims the screen and shows the time, the weather and what comes next. '
                 'A touch brings the calendar back. 0 turns it off.</p>'
                 '<p class="hint">Change these only if taps on the screen land in the wrong place. Both are normally ticked.</p>'
                 f'<label class="c"><input type="checkbox" name="touch_flip_x" value="1"{fx}> Mirror touch left to right</label>'
                 f'<label class="c"><input type="checkbox" name="touch_flip_y" value="1"{fy}> Mirror touch top to bottom</label>'
                 "<button>Save display settings</button></form></section>")

        b.append('<section id="maintenance"><h2>Maintenance</h2>'
                 '<a class="b" href="/confirm?a=restart">Restart the calendar</a>'
                 '<a class="b" href="/confirm?a=reboot">Restart the frame</a></section>')
        return self.html("Calendar frame settings", "".join(b))

    def confirm_page(self, action: str) -> str:
        q, yes = {"restart": ("Restart the calendar now? The screen goes blank for a few seconds.", "Yes, restart the calendar"),
                  "reboot": ("Restart the whole frame now? It takes about a minute to come back.", "Yes, restart the frame")}[action]
        return self.html("Please confirm",
                         f'<h1>Please confirm</h1><section><p>{escape(q)}</p>'
                         f'<form method="post" action="/do/{action}">{self.hidden()}<button>{escape(yes)}</button>'
                         '<a class="b" href="/#maintenance">No, go back</a></form></section>')

    # ---- actions: each returns (message code, page anchor) ----

    def _change_config(self, change, ok_code: str, anchor: str) -> tuple[str, str]:
        with self.lock:
            try:
                cfg = S.load_config(self.config)
            except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
                return "config_broken", anchor
            err = change(cfg)
            if err:
                return err, anchor
            try:
                S.save_config(self.config, cfg)
            except (OSError, ValueError, TypeError) as e:
                log(f"config write failed: {type(e).__name__}")
                return "write_failed", anchor
            ok, _ = self.sys.run("systemctl", "restart", "skycal.service", timeout=60)
            return (ok_code if ok else "saved_no_restart"), anchor

    def act_feed_add(self, form) -> tuple[str, str]:
        url = form("url").strip()

        def change(cfg):
            feeds = cfg.get("feeds", [])
            if not isinstance(feeds, list):
                return "config_broken"
            if not S.valid_feed(url):
                return "bad_feed"
            if url in feeds:
                return "dup_feed"
            if len(feeds) >= S.MAX_FEEDS:
                return "too_many_feeds"
            cfg["feeds"] = feeds + [url]
        return self._change_config(change, "feed_added", "calendar")

    def act_feed_remove(self, form) -> tuple[str, str]:
        fid = form("id")

        def change(cfg):
            feeds = cfg.get("feeds", [])
            if not isinstance(feeds, list):
                return "config_broken"
            keep = [f for f in feeds if not (isinstance(f, str) and hmac.compare_digest(S.feed_id(f).encode(), fid.encode()))]
            if len(keep) == len(feeds):
                return "no_feed"
            cfg["feeds"] = keep
            colors = cfg.get("feed_colors")
            if isinstance(colors, dict):
                colors.pop(fid, None)
                if not colors:
                    del cfg["feed_colors"]
        return self._change_config(change, "feed_removed", "calendar")

    def act_feed_color(self, form) -> tuple[str, str]:
        fid, color = form("id"), form("color").strip().lower()

        def change(cfg):
            feeds = cfg.get("feeds", [])
            if not isinstance(feeds, list) or not any(
                    isinstance(f, str) and hmac.compare_digest(S.feed_id(f).encode(), fid.encode()) for f in feeds):
                return "no_feed"
            if color and not S.valid_color(color):
                return "bad_color"
            colors = cfg.get("feed_colors", {})
            if not isinstance(colors, dict):
                colors = {}
            if color:
                colors[fid] = color
            else:
                colors.pop(fid, None)
            if colors:
                cfg["feed_colors"] = colors
            else:
                cfg.pop("feed_colors", None)
        return self._change_config(change, "color_saved", "calendar")

    def act_calendar_save(self, form) -> tuple[str, str]:
        tz, wd, rm = form("timezone").strip(), form("first_weekday"), form("refresh_minutes").strip()

        def change(cfg):
            if not S.valid_timezone(tz):
                return "bad_tz"
            if wd not in tuple("0123456"):
                return "bad_weekday"
            if not re.fullmatch(r"[0-9]{1,4}", rm) or not S.REFRESH_MIN <= int(rm) <= S.REFRESH_MAX:
                return "bad_refresh"
            cfg.update(timezone=tz, first_weekday=int(wd), refresh_minutes=int(rm))
        return self._change_config(change, "saved", "calendar")

    def act_weather_save(self, form) -> tuple[str, str]:
        place, units = " ".join(form("weather_place").split()), form("weather_units")

        def change(cfg):
            if not S.valid_place(place):
                return "bad_place"
            if units not in S.WEATHER_UNITS:
                return "bad_units"
            cfg.update(weather_place=place, weather_units=units)
        return self._change_config(change, "saved", "weather")

    def act_display_save(self, form) -> tuple[str, str]:
        ss, theme = form("screensaver_minutes").strip(), form("theme")

        def change(cfg):
            if not re.fullmatch(r"[0-9]{1,4}", ss) or int(ss) > S.SAVER_MAX:
                return "bad_saver"
            if not S.valid_theme(theme):
                return "bad_theme"
            cfg.update(touch_flip_x=form("touch_flip_x") == "1", touch_flip_y=form("touch_flip_y") == "1",
                       screensaver_minutes=int(ss), theme=theme)
        return self._change_config(change, "saved", "display")

    def act_wifi_scan(self, form) -> tuple[str, str]:
        with self.lock:
            ok, out = self.sys.run("iw", "dev", WLAN, "scan", timeout=25)
            if not ok:  # busy while the frame itself is searching: fall back to its latest results
                ok, out = self.sys.run("iw", "dev", WLAN, "scan", "dump", timeout=10)
            if not ok:
                return "scan_failed", "wifi"
            self.scan = parse_ssids(out)
            return "scan_done", "wifi"

    def act_wifi_join(self, form) -> tuple[str, str]:
        ssid, pw, is_open = form("ssid"), form("passphrase"), form("open") == "1"
        if not S.valid_ssid(ssid):
            return "bad_ssid", "wifi"
        if is_open == bool(pw):
            return "need_pass", "wifi"
        if not is_open and not S.valid_passphrase(pw):
            return "bad_pass", "wifi"
        with self.lock:
            try:
                S.save_network(self.wpa_conf, ssid, None if is_open else pw)
            except (OSError, ValueError) as e:
                log(f"wifi config write failed: {type(e).__name__}")
                return "write_failed", "wifi"
            ok, _ = self.sys.run("systemctl", "restart", f"wpa_supplicant@{WLAN}.service", timeout=60)
            return ("wifi_saved" if ok else "wifi_no_restart"), "wifi"

    def act_restart(self, form) -> tuple[str, str]:
        ok, _ = self.sys.run("systemctl", "restart", "skycal.service", timeout=60)
        return ("restarted" if ok else "restart_failed"), "maintenance"

    def act_reboot(self, form) -> tuple[str, str]:
        # A moment later, so the answer to this request still reaches the browser.
        t = threading.Timer(2.0, self.sys.run, args=("systemctl", "reboot"))
        t.daemon = True
        t.start()
        return "rebooting", ""

    POSTS = {"/wifi/scan": act_wifi_scan, "/wifi/join": act_wifi_join, "/feed/add": act_feed_add,
             "/feed/remove": act_feed_remove, "/feed/color": act_feed_color, "/calendar/save": act_calendar_save,
             "/weather/save": act_weather_save, "/display/save": act_display_save, "/do/restart": act_restart, "/do/reboot": act_reboot}


class Handler(BaseHTTPRequestHandler):
    app: App
    wifi = False                 # True on the Wi-Fi listener: every request needs a QR code session
    server_version = "skyadmin"
    sys_version = ""
    timeout = 15  # seconds a client may stall before its connection is dropped

    def log_message(self, format, *args):  # the default logs the request line; keep queries out
        pass

    def end_headers(self):  # on every response, including the error pages http.server makes itself
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", location: str = "",
              cookie: str = "") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        log(f"{self.command} {urlsplit(self.path).path[:60]!r} {code}")

    def _refuse(self, code: int, text: str) -> None:
        self.close_connection = True
        self._send(code, text + "\n", "text/plain; charset=utf-8")

    def _here(self) -> str:
        """host:port this request arrived on (the Wi-Fi listener answers on whatever address the frame has)."""
        return f"{self.connection.getsockname()[0]}:{self.server.server_address[1]}"

    def _host_ok(self) -> bool:
        if self.wifi:
            if self.headers.get("Host", "") == self._here():
                return True
            self._refuse(403, "Forbidden")
            return False
        if self.headers.get("Host", "") in self.app.hosts:
            return True
        self._refuse(403, "Forbidden: open this page as http://" + self.app.bind)
        return False

    def _signed_in(self) -> bool:
        """Always on the USB cable; on Wi-Fi, only with the cookie of a scanned QR code."""
        if not self.wifi:
            return True
        sid = ""
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                sid = value
        if self.app.session_ok(sid):
            return True
        self._turn_away("This page opens from the frame.")
        return False

    def _turn_away(self, why: str) -> None:
        self.close_connection = True
        self._send(403, self.app.html("Frame settings", (
            f"<main><h1>Frame settings</h1><p>{escape(why)}</p>"
            "<p>Tap the gear at the top of the calendar on the frame, then scan the code it shows "
            "with this phone's camera.</p></main>")))

    def _scan(self, q: dict) -> None:
        sid = self.app.redeem(q.get("c", [""])[0])
        if sid is None:
            return self._turn_away("That code has expired or has already been used.")
        self._send(303, "", location="/", cookie=f"{COOKIE}={sid}; Path=/; Max-Age={SESSION_S}; HttpOnly; SameSite=Lax")

    def do_GET(self):
        if not self._host_ok():
            return
        url = urlsplit(self.path)
        try:
            q = parse_qs(url.query, max_num_fields=5, errors="strict")
        except ValueError:
            return self._refuse(400, "Bad request")
        if self.wifi and url.path == "/scan":        # GET only: a HEAD must not use up the code
            return self._scan(q) if self.command == "GET" else self._refuse(405, "Method not allowed")
        if not self._signed_in():
            return
        if url.path == "/":
            ssid = q.get("ssid", [""])[0]
            return self._send(200, self.app.page(q.get("m", [""])[0], ssid if S.valid_ssid(ssid) else ""))
        if url.path == "/confirm" and q.get("a", [""])[0] in ("restart", "reboot"):
            return self._send(200, self.app.confirm_page(q["a"][0]))
        self._refuse(404, "Not found")

    do_HEAD = do_GET

    def do_POST(self):
        if not self._host_ok():
            return
        # Browsers send "Origin: null" on form posts from a page with Referrer-Policy: no-referrer
        # (as this one has), so null carries no information; the Host and token checks still apply.
        if not self._signed_in():
            return
        origin = self.headers.get("Origin")
        origins = {f"http://{self._here()}"} if self.wifi else self.app.origins
        if origin not in (None, "null") and origin not in origins:
            return self._refuse(403, "Forbidden")
        action = App.POSTS.get(urlsplit(self.path).path)
        if action is None:
            return self._refuse(404, "Not found")
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
            return self._refuse(415, "Unsupported form type")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._refuse(411, "Length required")
        if not 0 <= length <= MAX_BODY:
            return self._refuse(413, "Request too large")
        try:
            body = self.rfile.read(length)
            fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=20, errors="strict")
        except (OSError, ValueError):
            return self._refuse(400, "Bad request")
        if len(body) != length:
            return self._refuse(400, "Bad request")
        if not hmac.compare_digest(fields.get("token", [""])[0].encode(), self.app.token.encode()):
            return self._refuse(403, "This page is out of date. Go back, reload it and try again.")
        code, anchor = action(self.app, lambda name: fields.get(name, [""])[0])
        self._send(303, "", location=f"/?m={code}" + (f"#{anchor}" if anchor else ""))


class WifiHandler(Handler):
    wifi = True


def main() -> None:
    ap = argparse.ArgumentParser(prog="skyadmin", description="Settings page for the frame, for the USB cable network "
                                 "and, after a QR code from the frame, Wi-Fi.")
    ap.add_argument("--bind", default="192.168.77.1", help="IPv4 address to listen on (also the only accepted Host)")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--config", type=Path, default=Path("/etc/skycal/config.toml"))
    ap.add_argument("--wpa-conf", type=Path, default=Path("/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"))
    ap.add_argument("--state-dir", type=Path, default=Path("/var/lib/skycal"))
    ap.add_argument("--wifi-bind", default="0.0.0.0", help="address of the listener for phones on Wi-Fi")
    ap.add_argument("--wifi-port", type=int, default=RM.WIFI_PORT, help="its port; 0 turns it off")
    ap.add_argument("--dry-run", action="store_true", help="log system commands instead of running them")
    args = ap.parse_args()
    Handler.app = App(args)
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    log(f"skyadmin listening on http://{args.bind}:{args.port}" + (" (dry run)" if args.dry_run else ""))
    if args.wifi_port:
        try:
            wifi = ThreadingHTTPServer((args.wifi_bind, args.wifi_port), WifiHandler)
        except OSError as err:
            log(f"no listener for Wi-Fi: {err}")
        else:
            threading.Thread(target=wifi.serve_forever, daemon=True).start()
            log(f"skyadmin listening for phones on Wi-Fi on port {args.wifi_port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
