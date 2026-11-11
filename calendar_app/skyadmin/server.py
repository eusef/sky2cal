"""Settings page for the frame: python3 -m skyadmin

Served on the USB cable network only (the frame is 192.168.77.1 on usb0). Holding the cable is
the authentication, so there is no login. Plain HTML forms, no JavaScript.

Rules kept throughout: every change is a POST carrying a per-process token; requests whose Host
is not the bind address are refused (DNS rebinding); feed addresses and passphrases are never
logged or shown in full; system commands are argument lists, never a shell.
"""
import argparse
import hmac
import os
import re
import secrets
import subprocess
import sys
import threading
import tomllib
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from zoneinfo import ZoneInfo, available_timezones

from . import settings as S

WLAN = "wlan0"
MAX_BODY = 16 * 1024

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
    "bad_tz": (False, "That time zone was not recognised. Use a name such as America/Los_Angeles or Europe/London."),
    "bad_weekday": (False, "Choose a day for the week to start on."),
    "bad_refresh": (False, f"The update interval must be a whole number from {S.REFRESH_MIN} to {S.REFRESH_MAX} minutes."),
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
            for f in feeds:
                b.append(f'<li><code>{escape(S.mask_feed(f))}</code><form method="post" action="/feed/remove">{h}'
                         f'<input type="hidden" name="id" value="{S.feed_id(f)}"><button>Remove</button></form></li>')
            b.append("</ul>")
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

        fx = " checked" if cfg.get("touch_flip_x", True) else ""
        fy = " checked" if cfg.get("touch_flip_y", True) else ""
        b.append(f'<section id="display"><h2>Display</h2><form method="post" action="/display/save">{h}'
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
        return self._change_config(change, "feed_removed", "calendar")

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

    def act_display_save(self, form) -> tuple[str, str]:
        def change(cfg):
            cfg.update(touch_flip_x=form("touch_flip_x") == "1", touch_flip_y=form("touch_flip_y") == "1")
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
             "/feed/remove": act_feed_remove, "/calendar/save": act_calendar_save,
             "/display/save": act_display_save, "/do/restart": act_restart, "/do/reboot": act_reboot}


class Handler(BaseHTTPRequestHandler):
    app: App
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

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", location: str = "") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        log(f"{self.command} {urlsplit(self.path).path[:60]!r} {code}")

    def _refuse(self, code: int, text: str) -> None:
        self.close_connection = True
        self._send(code, text + "\n", "text/plain; charset=utf-8")

    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") in self.app.hosts:
            return True
        self._refuse(403, "Forbidden: open this page as http://" + self.app.bind)
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        url = urlsplit(self.path)
        try:
            q = parse_qs(url.query, max_num_fields=5, errors="strict")
        except ValueError:
            return self._refuse(400, "Bad request")
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
        origin = self.headers.get("Origin")
        if origin not in (None, "null") and origin not in self.app.origins:
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


def main() -> None:
    ap = argparse.ArgumentParser(prog="skyadmin", description="Settings page for the frame, for the USB cable network.")
    ap.add_argument("--bind", default="192.168.77.1", help="IPv4 address to listen on (also the only accepted Host)")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--config", type=Path, default=Path("/etc/skycal/config.toml"))
    ap.add_argument("--wpa-conf", type=Path, default=Path("/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"))
    ap.add_argument("--state-dir", type=Path, default=Path("/var/lib/skycal"))
    ap.add_argument("--dry-run", action="store_true", help="log system commands instead of running them")
    args = ap.parse_args()
    Handler.app = App(args)
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer((args.bind, args.port), Handler)
    log(f"skyadmin listening on http://{args.bind}:{args.port}" + (" (dry run)" if args.dry_run else ""))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
