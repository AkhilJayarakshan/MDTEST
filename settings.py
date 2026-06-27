import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".mdaq_settings.json"

WD_CHANNELS = {
    "S":    list(range(0, 7)) + list(range(8, 15)) + list(range(16, 23)) + list(range(24, 31)) + [47],
    "M":    list(range(0, 7)) + list(range(8, 15)) + list(range(16, 23)) + list(range(24, 31)) + [47],
    "L":    list(range(0, 32)) + [47],
    "XL":   list(range(0, 32)) + [47],
    "XXL":  list(range(0, 32)) + [47],
    "XXXL": list(range(0, 36)) + [47],
    "Full": list(range(0, 48)),
}


def load_settings() -> dict:
    defaults = {"download_path": str(Path.home() / "MDAQ_Data")}
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE) as f:
                return {**defaults, **json.load(f)}
        except Exception:
            pass
    return defaults


def save_settings(s: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)
