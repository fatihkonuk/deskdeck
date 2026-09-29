"""Reads host/config.toml (see config.toml.example)."""
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import layout

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.toml"

MEDIA = ("prev", "playpause", "next", "vol_down", "vol_up", "mute")
# Exactly one per button: action type → key in the config
ACTIONS = ("media", "app", "open", "osascript", "shortcut")


@dataclass(frozen=True)
class DeviceConfig:
    host: str
    port: int
    token: str
    ip: str | None  # fixed IP; when set, mDNS is not used


@dataclass(frozen=True)
class ButtonSpec:
    action: str  # one of ACTIONS
    arg: str
    label: str = ""
    icon: str = ""  # emoji/symbol or image path; if empty: the app icon (app) or just the label

    @property
    def name(self) -> str:
        return self.label or self.arg


Page = tuple[ButtonSpec | None, ...]  # length layout.BUTTON_COUNT, None for an empty cell

# used when config.toml has no [[pages]]
DEFAULT_PAGES: tuple[Page, ...] = ((
    ButtonSpec("media", "prev"), ButtonSpec("media", "playpause"), ButtonSpec("media", "next"), None,
    ButtonSpec("media", "vol_down"), ButtonSpec("media", "vol_up"), ButtonSpec("media", "mute"), None,
),)


def _read(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def load(path: Path = DEFAULT_PATH) -> DeviceConfig:
    if not path.exists():
        raise SystemExit(f"{path} not found. Copy config.toml.example and fill in the token.")
    dev = _read(path)["device"]
    return DeviceConfig(host=dev.get("host", "deskdeck.local"), port=int(dev.get("port", 7788)),
                        token=dev["token"], ip=dev.get("ip") or None)


def _button(raw: object, where: str) -> ButtonSpec | None:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: must be a table, e.g. {{ app = \"Safari\" }} ({{}} for an empty cell)")
    if not raw:
        return None
    found = [k for k in ACTIONS if k in raw]
    if len(found) != 1:
        raise ValueError(f"{where}: exactly one action required ({', '.join(ACTIONS)}), found: {found}")
    action = found[0]
    arg = raw[action]
    if not isinstance(arg, str) or not arg:
        raise ValueError(f"{where}: '{action}' must be a non-empty string")
    if action == "media" and arg not in MEDIA:
        raise ValueError(f"{where}: media must be one of: {', '.join(MEDIA)}")
    unknown = set(raw) - {action, "label", "icon"}
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {sorted(unknown)}")
    return ButtonSpec(action, arg, str(raw.get("label", "")), str(raw.get("icon", "")))


def load_pages(path: Path = DEFAULT_PATH) -> tuple[Page, ...]:
    """Reads the [[pages]] tables. Raises ValueError naming the page/button if the content is invalid
    (tomllib.TOMLDecodeError is a ValueError too) and OSError if the file cannot be read."""
    pages = _read(path).get("pages")
    if not pages:
        return DEFAULT_PAGES
    if not isinstance(pages, list):
        raise ValueError("'pages' must be an array of tables: write [[pages]], not [pages]")
    out = []
    for pi, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ValueError(f"page {pi + 1}: must be a table")
        buttons = page.get("buttons", [])
        if not isinstance(buttons, list):
            raise ValueError(f"page {pi + 1}: 'buttons' must be an array")
        if len(buttons) > layout.BUTTON_COUNT:
            raise ValueError(f"page {pi + 1}: at most {layout.BUTTON_COUNT} buttons")
        specs = [_button(b, f"page {pi + 1}, button {bi + 1}") for bi, b in enumerate(buttons)]
        out.append(tuple(specs + [None] * (layout.BUTTON_COUNT - len(specs))))
    return tuple(out)
