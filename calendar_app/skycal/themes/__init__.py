"""The looks the calendar can be drawn in, picked with `theme = "<name>"` in the config, or on
the frame itself from the gear's card (saved in CHOICE_FILE, see chosen()).

THEMES is read by the settings page too, so this file imports nothing heavy: a theme's module
is loaded only when it is used."""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

# name -> (label on the settings page, module, class, label on the frame's own style buttons)
THEMES = {
    "original": ("Original", "original", "Original", "Original"),
    "tactical": ("Tactical HUD", "tactical", "Tactical", "Tactical"),
    "sports": ("Premium sports", "sports", "Sports", "Sports"),
    "fantasy": ("Dark fantasy", "fantasy", "Fantasy", "Fantasy"),
    "neon": ("Cyberpunk neon", "neon", "Neon", "Neon"),
    "starship": ("Starship (TNG-inspired)", "starship", "Starship", "Starship"),
}
DEFAULT = "original"
CHOICE_FILE = "theme-choice"     # in the calendar's state folder (/var/lib/skycal on the frame)


def labels() -> list[tuple[str, str]]:
    """(name, label) of every theme, in the order the settings page lists them."""
    return [(name, entry[0]) for name, entry in THEMES.items()]


def buttons() -> list[tuple[str, str]]:
    """(name, short label) of every theme, in the order the frame's style buttons show them."""
    return [(name, entry[3]) for name, entry in THEMES.items()]


def chosen(state_dir: Path, configured: str) -> str:
    """The theme to draw in. A theme picked on the frame (choose()) wins for as long as the
    config still names the theme it was picked over; once the config names another (the
    settings page saved one, or a hand edit), the config's wins and the pick is dropped, so an
    edit back to the old theme does not bring it back."""
    configured = str(configured or DEFAULT)
    path = Path(state_dir) / CHOICE_FILE
    try:
        saved = json.loads(path.read_text())
        name, over = saved["theme"], saved["over"]
    except (OSError, ValueError, KeyError, TypeError):
        return configured
    if isinstance(name, str) and name in THEMES and over == configured:
        return name
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return configured


def choose(state_dir: Path, name: str, configured: str) -> None:
    """Keep `name` as the frame's own pick, over the config's `configured`. Raises OSError."""
    path = Path(state_dir) / CHOICE_FILE
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"theme": name, "over": str(configured or DEFAULT)}, f)
    tmp.replace(path)


def forget(state_dir: Path) -> None:
    """Drop the frame's own pick, so the config's theme is used. Raises OSError."""
    (Path(state_dir) / CHOICE_FILE).unlink(missing_ok=True)


def make(name: str):
    """An instance of the theme `name`; the original when the name is unknown or its module
    cannot be loaded (a theme must never keep the calendar from showing)."""
    entry = THEMES.get(str(name or DEFAULT))
    if entry is not None:
        try:
            module = importlib.import_module(f"{__name__}.{entry[1]}")
            return getattr(module, entry[2])()
        except Exception as err:
            print(f"theme {name!r} not usable: {type(err).__name__}: {err}", file=sys.stderr)
    from .original import Original
    return Original()
