"""Find PortAudio devices by name rather than unstable device indexes."""

import sounddevice as sd


def choices(direction):
    """Return (label, persistent identity, current index) for a direction."""
    if direction not in ("input", "output"):
        raise ValueError("Unknown audio direction")
    hostapis = sd.query_hostapis()
    seen = {}
    result = [("System default", None, None)]
    for index, device in enumerate(sd.query_devices()):
        if device[f"max_{direction}_channels"] < 1:
            continue
        name = device["name"]
        api = hostapis[device["hostapi"]]["name"]
        key = (name, api)
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        identity = {"name": name, "hostapi": api, "occurrence": occurrence}
        label = f"{name} ({api})" + (f" #{occurrence + 1}" if occurrence else "")
        result.append((label, identity, index))
    return result


def resolve(direction, identity):
    if identity is None:
        return None
    for _, saved, index in choices(direction):
        if saved == identity:
            return index
    raise ValueError(f"Selected {direction} device is unavailable. Choose another device and refresh the list.")


def valid_identity(identity):
    return identity is None or (
        isinstance(identity, dict)
        and isinstance(identity.get("name"), str)
        and bool(identity["name"])
        and isinstance(identity.get("hostapi"), str)
        and bool(identity["hostapi"])
        and type(identity.get("occurrence")) is int
        and identity["occurrence"] >= 0
    )
