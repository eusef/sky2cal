"""The looks the calendar can be drawn in, picked with `theme = "<name>"` in the config.

THEMES is read by the settings page too, so this file imports nothing heavy: a theme's module
is loaded only when it is used."""
from __future__ import annotations

import importlib
import sys

# name -> (label on the settings page, module, class)
THEMES = {
    "original": ("Original", "original", "Original"),
    "tactical": ("Tactical HUD", "tactical", "Tactical"),
    "sports": ("Premium sports", "sports", "Sports"),
    "fantasy": ("Dark fantasy", "fantasy", "Fantasy"),
    "neon": ("Cyberpunk neon", "neon", "Neon"),
    "starship": ("Starship (TNG-inspired)", "starship", "Starship"),
}
DEFAULT = "original"


def labels() -> list[tuple[str, str]]:
    """(name, label) of every theme, in the order the settings page lists them."""
    return [(name, entry[0]) for name, entry in THEMES.items()]


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
