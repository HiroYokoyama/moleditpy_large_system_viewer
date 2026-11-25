"""Plugin settings stored in settings.json beside the package."""

import json
import logging
import os

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

REPRESENTATIONS = (
    ("ball_and_stick", "Ball and Stick"),
    ("spacefill", "Spacefill"),
    ("licorice", "Licorice"),
    ("ribbon", "Ribbon"),
    ("ribbon_ligands", "Ribbon + Ligands"),
)
RIBBON_REPRESENTATIONS = ("ribbon", "ribbon_ligands")

LARGE_ACTIONS = (
    ("ask", "Ask"),
    ("switch", "Always switch to the Fast style"),
    ("keep", "Keep the current style"),
)

DEFAULTS = {
    # Switching
    "large_action": "ask",  # large structure under another style: ask / switch / keep
    "switch_threshold": 2000,  # atoms
    "restore_style": True,  # return to the previous style for a small structure
    "host_link_limit": 50000,  # atoms; larger files open view-only
    # Display
    "representation": "ball_and_stick",
    "inherit_host_appearance": True,  # radii, bond colour and lighting from MoleditPy
    "ball_atom_scale": 1.0,
    "ball_bond_radius": 0.1,
    "spacefill_scale": 1.0,
    "licorice_radius": 0.15,
    "bond_color_by_atom": False,
    "bond_color": "#7F7F7F",
    "specular": 0.2,
    "specular_power": 20.0,
    "show_hydrogens": True,
    "show_water": True,
    # Ribbon
    "ribbon_helix_width": 1.3,
    "ribbon_coil_width": 0.3,
    "ribbon_subdivide": 20,
    # Periodic cell
    "show_box": True,
    "box_color": "#FFC107",
    "box_line_width": 2.0,
    # Playback
    "fps": 10,
    "stride": 1,
    "loop": True,
}

_LIMITS = {
    "switch_threshold": (1, 100_000_000),
    "host_link_limit": (1, 100_000_000),
    "ball_atom_scale": (0.05, 10.0),
    "ball_bond_radius": (0.01, 2.0),
    "spacefill_scale": (0.05, 10.0),
    "licorice_radius": (0.01, 2.0),
    "specular": (0.0, 1.0),
    "specular_power": (1.0, 200.0),
    "ribbon_helix_width": (0.05, 10.0),
    "ribbon_coil_width": (0.05, 10.0),
    "ribbon_subdivide": (1, 100),
    "box_line_width": (0.5, 20.0),
    "fps": (1, 120),
    "stride": (1, 100000),
}


def coerce(key, value):
    """Value converted to the type of the default and clamped; the default when invalid."""
    default = DEFAULTS[key]
    try:
        if isinstance(default, bool):
            out = bool(value)
        elif isinstance(default, int):
            out = int(value)
        elif isinstance(default, float):
            out = float(value)
        else:
            out = str(value)
    except (TypeError, ValueError):
        return default
    if key in _LIMITS:
        lo, hi = _LIMITS[key]
        out = type(default)(min(max(out, lo), hi))
    if key == "representation" and out not in dict(REPRESENTATIONS):
        return default
    if key == "large_action" and out not in dict(LARGE_ACTIONS):
        return default
    if key.endswith("_color") and not (out.startswith("#") and len(out) == 7):
        return default
    return out


def load(path=SETTINGS_FILE):
    values = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
    except FileNotFoundError:
        return values
    except (OSError, ValueError) as exc:
        logging.warning("Large System Viewer: ignoring unreadable settings: %s", exc)
        return values
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in DEFAULTS:
                values[key] = coerce(key, value)
    return values


def save(values, path=SETTINGS_FILE):
    data = {k: values[k] for k in DEFAULTS if k in values}
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError as exc:
        logging.warning("Large System Viewer: could not save settings: %s", exc)


def hex_to_rgb(color):
    try:
        h = str(color).lstrip("#")
        return tuple(int(h[k:k + 2], 16) / 255.0 for k in (0, 2, 4))
    except (TypeError, ValueError):
        return (0.5, 0.5, 0.5)


def appearance(values, host_settings, representation):
    """Radii, bond and lighting parameters for a representation.

    With `inherit_host_appearance` the values come from MoleditPy's own 3D settings
    (ball_stick_*, cpk_atom_scale, stick_bond_radius, specular, ...), so the Fast style
    looks like the standard style of the same name.
    """
    host = host_settings or {}
    inherit = values.get("inherit_host_appearance", True)

    def pick(host_key, own_key, fallback):
        if inherit and host_key in host:
            return host[host_key]
        return values.get(own_key, fallback)

    out = {
        "specular": float(pick("specular", "specular", 0.2)),
        "specular_power": float(pick("specular_power", "specular_power", 20.0)),
        "lighting": bool(host.get("lighting_enabled", True)),
    }
    if representation == "spacefill":
        out.update(atom_mode="vdw", atom_scale=float(pick("cpk_atom_scale", "spacefill_scale", 1.0)),
                   bond_radius=0.0, bond_by_atom=True, draw_bonds=False)
    elif representation == "licorice":
        r = float(pick("stick_bond_radius", "licorice_radius", 0.15))
        out.update(atom_mode="constant", atom_scale=r, bond_radius=r, bond_by_atom=True,
                   draw_bonds=True)
    else:
        by_atom = pick("ball_stick_use_cpk_bond_color", "bond_color_by_atom", False)
        out.update(
            atom_mode="ball",
            atom_scale=float(pick("ball_stick_atom_scale", "ball_atom_scale", 1.0)),
            bond_radius=float(pick("ball_stick_bond_radius", "ball_bond_radius", 0.1)),
            bond_by_atom=bool(by_atom),
            bond_color=hex_to_rgb(pick("ball_stick_bond_color", "bond_color", "#7F7F7F")),
            draw_bonds=True,
        )
    out.setdefault("bond_color", hex_to_rgb(values.get("bond_color", "#7F7F7F")))
    return out
