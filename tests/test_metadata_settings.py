"""Metadata, settings and element tables: pure Python, no host dependencies."""

import ast
import json
import os

from large_system_viewer import elements, settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _metadata():
    with open(
        os.path.join(ROOT, "large_system_viewer", "__init__.py"), encoding="utf-8"
    ) as fh:
        tree = ast.parse(fh.read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.startswith("PLUGIN_"):
                out[name] = ast.literal_eval(node.value)
    return out


def test_metadata_complete():
    meta = _metadata()
    assert meta["PLUGIN_NAME"] == "Large System Viewer"
    assert meta["PLUGIN_VERSION"].count(".") == 2
    assert meta["PLUGIN_DEPENDENCIES"] == []
    assert meta["PLUGIN_OPTIONAL_DEPENDENCIES"] == []
    assert meta["PLUGIN_CATEGORY"] == "Visualization"
    assert meta["PLUGIN_SUPPORTED_MOLEDITPY_VERSION"].startswith(">=5")


def test_no_module_level_run():
    with open(
        os.path.join(ROOT, "large_system_viewer", "__init__.py"), encoding="utf-8"
    ) as fh:
        tree = ast.parse(fh.read())
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "initialize" in names
    assert "run" not in names and "autorun" not in names


def test_coerce_types_and_limits():
    assert settings.coerce("fps", "30") == 30
    assert settings.coerce("fps", 1000) == 120
    assert settings.coerce("ball_atom_scale", 0) == 0.05
    assert settings.coerce("representation", "nope") == "ball_and_stick"
    assert settings.coerce("large_action", "keep") == "keep"
    assert settings.coerce("large_action", "maybe") == "ask"
    assert settings.coerce("box_color", "red") == settings.DEFAULTS["box_color"]
    assert settings.coerce("show_box", 0) is False


def test_load_save_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    values = dict(settings.DEFAULTS, fps=25, representation="ribbon", show_box=False)
    settings.save(values, str(path))
    loaded = settings.load(str(path))
    assert loaded["fps"] == 25
    assert loaded["representation"] == "ribbon"
    assert loaded["show_box"] is False


def test_load_ignores_bad_files(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert settings.load(str(path)) == settings.DEFAULTS
    path.write_text(json.dumps({"fps": "x", "unknown": 1}), encoding="utf-8")
    loaded = settings.load(str(path))
    assert loaded["fps"] == settings.DEFAULTS["fps"]
    assert "unknown" not in loaded


def test_appearance_inherits_host_values():
    host = {
        "ball_stick_atom_scale": 1.5,
        "ball_stick_bond_radius": 0.2,
        "ball_stick_use_cpk_bond_color": True,
        "cpk_atom_scale": 0.8,
        "stick_bond_radius": 0.25,
        "specular": 0.6,
        "lighting_enabled": False,
    }
    values = dict(settings.DEFAULTS)
    ball = settings.appearance(values, host, "ball_and_stick")
    assert ball["atom_scale"] == 1.5 and ball["bond_radius"] == 0.2
    assert ball["bond_by_atom"] is True and ball["specular"] == 0.6
    assert ball["lighting"] is False
    assert settings.appearance(values, host, "spacefill")["atom_scale"] == 0.8
    assert settings.appearance(values, host, "licorice")["bond_radius"] == 0.25


def test_appearance_own_values_when_not_inheriting():
    host = {"ball_stick_atom_scale": 1.5, "specular": 0.6}
    values = dict(settings.DEFAULTS, inherit_host_appearance=False, ball_atom_scale=0.7)
    look = settings.appearance(values, host, "ball_and_stick")
    assert look["atom_scale"] == 0.7
    assert look["specular"] == settings.DEFAULTS["specular"]
    assert look["bond_color"] == settings.hex_to_rgb(settings.DEFAULTS["bond_color"])


def test_element_guessing():
    assert elements.atomic_number("Cl") == 17
    assert elements.symbol(8) == "O"
    assert elements.guess_pdb_element(" CA ", "ALA") == "C"
    assert elements.guess_pdb_element("CA  ", "CA", hetatm=True) == "Ca"
    assert elements.guess_name_element("OW", "SOL") == "O"
    assert elements.guess_name_element("HW1", "SOL") == "H"
