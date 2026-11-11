"""Files the settings page edits: the calendar config (TOML) and the Wi-Fi network list.

Both are written atomically (temp file in the same directory, fsync, rename) with mode 0600.
Nothing in here logs or prints file contents: both files hold secrets.
"""
import datetime
import hashlib
import os
import re
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

KNOWN_KEYS = ("timezone", "first_weekday", "cache_dir", "feeds", "refresh_minutes",
              "touch_flip_x", "touch_flip_y")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
REFRESH_MIN, REFRESH_MAX = 5, 1440
MAX_FEEDS = 20
MAX_FEED_LEN = 2000


def atomic_write(path: Path, text: str, errors: str = "strict") -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors=errors) as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


# ---- calendar config (TOML) ----

def _toml_str(s: str) -> str:
    out = []
    for ch in s:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _toml_str(k)


def _toml_val(v) -> str:
    if isinstance(v, bool):  # before int: bool is an int
        return "true" if v else "false"
    if isinstance(v, str):
        return _toml_str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_toml_key(k)} = {_toml_val(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"cannot write a value of type {type(v).__name__}")


def _toml_table(name: str, d: dict, out: list) -> None:
    out.append("")
    out.append(f"[{name}]")
    for k, v in d.items():
        if not isinstance(v, dict):
            out.append(f"{_toml_key(k)} = {_toml_val(v)}")
    for k, v in d.items():
        if isinstance(v, dict):
            _toml_table(f"{name}.{_toml_key(k)}", v, out)


def dumps_config(cfg: dict) -> str:
    """Known keys first, then every other key the file already had (nothing is dropped)."""
    out = ["# Calendar settings. Written by the settings page (skyadmin); hand edits are kept,",
           "# comments are not. Feed addresses are secrets."]
    keys = [k for k in KNOWN_KEYS if k in cfg] + [k for k in cfg if k not in KNOWN_KEYS]
    for k in keys:
        v = cfg[k]
        if isinstance(v, dict):
            continue
        if k == "feeds" and isinstance(v, list) and v:
            out.append("feeds = [")
            out += [f"  {_toml_val(x)}," for x in v]
            out.append("]")
        else:
            out.append(f"{_toml_key(k)} = {_toml_val(v)}")
    for k in keys:
        if isinstance(cfg[k], dict):
            _toml_table(_toml_key(k), cfg[k], out)
    return "\n".join(out) + "\n"


def load_config(path: Path) -> dict:
    """Raises OSError or tomllib.TOMLDecodeError; a missing file is an empty config."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_config(path: Path, cfg: dict) -> None:
    text = dumps_config(cfg)
    if tomllib.loads(text) != cfg:  # never replace the file with something that reads differently
        raise ValueError("config does not survive a round trip")
    atomic_write(path, text)


def feed_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()[:16]


def mask_feed(url: str) -> str:
    """Scheme and host, then the last 4 characters. The rest of the address is the secret."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or "?"
        scheme = parts.scheme or "?"
    except ValueError:
        scheme, host = "?", "?"
    # The last 4 characters only when plenty of the address stays hidden.
    hidden = len(url) - len(scheme) - len(host) - 4
    return f"{scheme}://{host}/...{url[-4:] if hidden >= 16 else ''}"


def valid_feed(url: str) -> bool:
    if not (len("https://") < len(url) <= MAX_FEED_LEN) or not url.startswith("https://"):
        return False
    if any(not (0x21 <= ord(c) <= 0x7E) for c in url):
        return False
    try:
        return bool(urlsplit(url).hostname)
    except ValueError:
        return False


def valid_timezone(name: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*", name) or len(name) > 64:
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


# ---- Wi-Fi network list (wpa_supplicant) ----

DEFAULT_WPA_HEADER = "ctrl_interface=DIR=/run/wpa_supplicant GROUP=root\nupdate_config=0\ncountry=US\n"
_NET_START = re.compile(r"\s*network\s*=\s*\{\s*")
_NET_END = re.compile(r"\s*\}\s*")
_SSID_LINE = re.compile(r"\s*ssid\s*=\s*(.*?)\s*")


def valid_ssid(ssid: str) -> bool:
    return 1 <= len(ssid.encode("utf-8")) <= 32


def valid_passphrase(p: str) -> bool:
    return 8 <= len(p) <= 63 and all(0x20 <= ord(c) <= 0x7E for c in p)


def _block_ssid(lines: list) -> bytes | None:
    for line in lines:
        m = _SSID_LINE.fullmatch(line)
        if not m:
            continue
        v = m.group(1)
        if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
            return v[1:-1].encode("utf-8")
        try:
            return bytes.fromhex(v)
        except ValueError:
            return None
    return None


def network_block(ssid: str, passphrase: str | None) -> list:
    """The SSID goes in as hex and the key as the derived 256-bit PSK, so no text typed by the
    user reaches the file and the passphrase itself is never stored."""
    sb = ssid.encode("utf-8")
    lines = ["network={", f"\tssid={sb.hex()}"]
    if passphrase is None:
        lines.append("\tkey_mgmt=NONE")
    else:
        psk = hashlib.pbkdf2_hmac("sha1", passphrase.encode("ascii"), sb, 4096, 32)
        lines.append(f"\tpsk={psk.hex()}")
    lines.append("}")
    return lines


def merge_network(text: str, ssid: str, passphrase: str | None) -> str:
    """Replace the block for this SSID, or append one. Header lines and other networks stay."""
    want = ssid.encode("utf-8")
    new = network_block(ssid, passphrase)
    out, block, done = [], None, False
    for line in text.splitlines():
        if block is None:
            if _NET_START.fullmatch(line):
                block = [line]
            else:
                out.append(line)
            continue
        block.append(line)
        if _NET_END.fullmatch(line):
            if _block_ssid(block) == want:
                if not done:
                    out += new
                    done = True
            else:
                out += block
            block = None
    if block is not None:  # unterminated block: keep it as it is, closed, rather than lose it
        out += block + ["}"]
    if not done:
        if out and out[-1].strip():
            out.append("")
        out += new
    return "\n".join(out) + "\n"


def save_network(path: Path, ssid: str, passphrase: str | None) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
    except FileNotFoundError:
        text = DEFAULT_WPA_HEADER
    merged = merge_network(text, ssid, passphrase)
    atomic_write(path, merged, errors="surrogateescape")
