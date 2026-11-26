"""Opening the settings page from a phone on the same Wi-Fi.

A tap on the gear in the calendar's top bar shows a QR code for
http://<the frame's Wi-Fi address>:8080/scan?c=<code>. The code is random, is written to
CODE_FILE in the calendar's state folder while the card is on screen, and is withdrawn when
the card closes. The settings page (skyadmin) reads that file: the code lets in one phone,
once, and only within CODE_S of being shown. Everything else that reaches port 8080 is turned
away, so being on the Wi-Fi is not enough; seeing the frame is.

Shared by skycal (shows the code) and skyadmin (checks it); nothing here imports Pillow.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import time
from pathlib import Path

WIFI_PORT = 8080                 # skyadmin's port on Wi-Fi; the firewall opens it on wlan0 only
CODE_FILE = "settings-code"      # in the calendar's state folder (/var/lib/skycal on the frame)
CODE_S = 600                     # a code shown longer ago than this is refused
CODE_BYTES = 16


def new_code(state_dir: Path) -> str:
    """A fresh code, saved for skyadmin (readable by the owner only); replaces any earlier one."""
    code = secrets.token_urlsafe(CODE_BYTES)
    path = Path(state_dir) / CODE_FILE
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"code": code, "shown": time.time()}, f)
    tmp.replace(path)
    return code


def drop_code(state_dir: Path) -> None:
    """Withdraw the code on show, if any."""
    try:
        (Path(state_dir) / CODE_FILE).unlink()
    except FileNotFoundError:
        pass


def shown_code(state_dir: Path) -> tuple[str, float] | None:
    """(code, when it was shown) as saved by new_code(), or None."""
    try:
        saved = json.loads((Path(state_dir) / CODE_FILE).read_text())
        code, shown = saved["code"], float(saved["shown"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (code, shown) if isinstance(code, str) and code else None


def wifi_address() -> str | None:
    """The frame's IPv4 address on Wi-Fi, or None when it has none. Wi-Fi is the frame's only way
    out (the USB cable network has no route beyond the computer), so this is the address the
    kernel would send from to a far address. Connecting a UDP socket sends nothing; it only asks
    the routing table. (Not `ip addr`: the calendar's sandbox has no netlink.)"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))         # TEST-NET-1: routed like any far address, never used
            address = s.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith(("0.", "127.", "192.168.77.")) else address


def settings_url(address: str, code: str) -> str:
    return f"http://{address}:{WIFI_PORT}/scan?c={code}"
