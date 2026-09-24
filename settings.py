"""Persistent local relay address."""

import ipaddress
import json
import os
from pathlib import Path
import sys
import tempfile

from relay_config import RELAY_HOST
from audio_devices import valid_identity


def settings_path():
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "Frincoms" / "settings.json"


def valid_relay_host(host):
    if not isinstance(host, str) or not host or host != host.strip():
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if len(host) > 253:
        return False
    return all(
        0 < len(label) <= 63
        and label[0].isascii() and label[0].isalnum()
        and label[-1].isascii() and label[-1].isalnum()
        and all(char.isascii() and (char.isalnum() or char == "-") for char in label)
        for label in host.split(".")
    )


def load_settings(path=None):
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"relay_host": RELAY_HOST, "input_device": None, "output_device": None}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Settings file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Settings must be an object")
    host = data.get("relay_host", RELAY_HOST)
    if not valid_relay_host(host):
        raise ValueError("Settings contain an invalid relay address")
    if not valid_identity(data.get("input_device")) or not valid_identity(data.get("output_device")):
        raise ValueError("Settings contain an invalid audio device")
    # An older version may have stored friends and a host ID. They are no
    # longer used; keep them on disk until the user next saves the address.
    return {"relay_host": host, "input_device": data.get("input_device"), "output_device": data.get("output_device")}


def save_settings(data, path=None):
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".settings-", delete=False
        ) as file:
            temporary = Path(file.name)
            if sys.platform != "win32":
                os.chmod(temporary, 0o600)
            json.dump(data, file, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
