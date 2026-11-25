"""Every touch point with MoleditPy internals.

The plugin reaches past PluginContext only here, so a host refactor has one place to
fix.  Each helper tolerates a missing attribute and logs instead of raising.
"""

import logging
import os
import sys

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a host dependency
    np = None

_HOOK_ATTR = "_large_system_viewer_set_molecule"


def _call(obj, name, *args, **kwargs):
    fn = getattr(obj, name, None)
    if fn is None:
        logging.debug("Large System Viewer: host has no %s", name)
        return None
    return fn(*args, **kwargs)


def view3d(mw):
    return getattr(mw, "view_3d_manager", None)


def plotter(mw):
    v3d = view3d(mw)
    return getattr(v3d, "plotter", None) if v3d is not None else None


def is_headless():
    return (
        os.environ.get("MOLEDITPY_HEADLESS") == "1"
        or os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    )


# ------------------------------------------------------------------- settings


def settings(mw):
    init = getattr(mw, "init_manager", None)
    values = getattr(init, "settings", None)
    if isinstance(values, dict):
        return values
    getter = getattr(mw, "get_settings", None)
    return getter() if getter else {}


def cpk_colors(mw):
    """The host's live CPK table (symbol -> [r, g, b]), including user overrides."""
    v3d = view3d(mw)
    mod = sys.modules.get(type(v3d).__module__) if v3d is not None else None
    colors = getattr(mod, "CPK_COLORS_PV", None)
    if not colors:
        try:
            from moleditpy.utils.constants import CPK_COLORS_PV as colors  # noqa: N811
        except ImportError:
            colors = {}
    return colors


# ---------------------------------------------------------------------- style


def current_style(mw):
    return getattr(view3d(mw), "current_3d_style", None)


def set_style(mw, name):
    v3d = view3d(mw)
    if v3d is not None:
        v3d.set_3d_style(name)


def current_molecule(mw):
    return getattr(view3d(mw), "current_mol", None)


def redraw(mw):
    v3d = view3d(mw)
    mol = getattr(v3d, "current_mol", None)
    if v3d is not None:
        v3d.draw_molecule_3d(mol)


# ---------------------------------------------------------------- molecule hook


def install_molecule_hook(mw, after_set):
    """Call after_set(mol) whenever the host receives a new current molecule.

    Wraps MainWindow.set_current_molecule, the mediator every loader, undo step and
    plugin (PluginContext.current_molecule) goes through before drawing.  It does not
    touch draw_molecule_3d, which other plugins (CIF Viewer) wrap and restore.
    """
    if mw is None or getattr(mw, _HOOK_ATTR, None) is not None:
        return
    original = getattr(mw, "set_current_molecule", None)
    if original is None:
        return

    def hooked(mol):
        previous = current_molecule(mw)
        original(mol)
        if mol is not None and mol is not previous:
            try:
                after_set(mol)
            except Exception:  # never break the host's loader
                logging.exception("Large System Viewer: molecule hook failed")

    setattr(mw, _HOOK_ATTR, original)
    mw.set_current_molecule = hooked


def uninstall_molecule_hook(mw):
    original = getattr(mw, _HOOK_ATTR, None)
    if original is not None:
        mw.set_current_molecule = original
        setattr(mw, _HOOK_ATTR, None)


def set_molecule_quietly(mw, mol):
    """Set the current molecule without going through the hook."""
    original = getattr(mw, _HOOK_ATTR, None) or getattr(mw, "set_current_molecule")
    original(mol)


# -------------------------------------------------------------------- drawing


def begin_scene(mw):
    """Clear the plotter the way the host's own styles do. Returns the camera state."""
    v3d = view3d(mw)
    p = plotter(mw)
    edit_3d = getattr(mw, "edit_3d_manager", None)
    if edit_3d is not None and hasattr(edit_3d, "clear_measurement_selection"):
        try:
            edit_3d.clear_measurement_selection()
        except (AttributeError, RuntimeError):
            logging.debug("clear_measurement_selection failed", exc_info=True)
    color_map = getattr(v3d, "_3d_color_map", None)
    if isinstance(color_map, dict):
        color_map.clear()
    camera = p.camera_position
    axes = getattr(v3d, "axes_actor", None)
    if axes is not None:
        try:
            p.remove_actor(axes)
        except (AttributeError, RuntimeError, TypeError):
            logging.debug("axes removal failed", exc_info=True)
        v3d.axes_actor = None
    p.clear()
    host = settings(mw)
    p.set_background(host.get("background_color", "#919191"))
    if host.get("lighting_enabled", True):
        try:
            import pyvista as pv

            p.add_light(pv.Light(position=(1, 1, 2), light_type="cameralight",
                                 intensity=host.get("light_intensity", 1.2)))
        except Exception:
            logging.debug("light setup failed", exc_info=True)
    return camera


def end_scene(mw, camera, reset_camera=False):
    v3d = view3d(mw)
    p = plotter(mw)
    if reset_camera:
        p.reset_camera()
    elif camera is not None:
        p.camera_position = camera
    try:
        v3d.apply_3d_settings(redraw=False)
    except Exception:
        logging.debug("apply_3d_settings failed", exc_info=True)
    p.render()


def store_positions(mw, mol, positions):
    """Write a frame into the host molecule and its picking coordinates."""
    pos = np.asarray(positions, dtype=np.float64)
    if mol is not None and mol.GetNumConformers():
        mol.GetConformer().SetPositions(pos)
    v3d = view3d(mw)
    if v3d is not None:
        v3d.atom_positions_3d = pos.copy()


def set_picking_positions(mw, positions):
    v3d = view3d(mw)
    if v3d is not None:
        v3d.atom_positions_3d = None if positions is None else np.asarray(positions, np.float64).copy()


# ------------------------------------------------------------------ documents


def check_unsaved(mw):
    state = getattr(mw, "state_manager", None)
    if state is None:
        return True
    return bool(state.check_unsaved_changes())


def clear_document(mw):
    edit = getattr(mw, "edit_actions_manager", None)
    if edit is not None:
        edit.clear_all(skip_check=True)


def finish_file(mw, path):
    _call(mw, "set_current_file_path", path)
    edit = getattr(mw, "edit_actions_manager", None)
    if edit is not None:
        _call(edit, "reset_history")
    _call(mw, "set_has_unsaved_changes", False)
    state = getattr(mw, "state_manager", None)
    if state is not None:
        _call(state, "update_window_title")


def enter_viewer_mode(mw, features=True):
    ui = getattr(mw, "ui_manager", None)
    if ui is None:
        return
    _call(ui, "enter_3d_viewer_mode")
    _call(ui, "enable_3d_features", features)


def load_molecule(mw, mol):
    """Hand a molecule to the host's 3D Viewer Mode (the XYZ/MOL loader sequence)."""
    set_molecule_quietly(mw, mol)
    _call(mw, "set_atom_id_to_rdkit_idx_map", {})
    if hasattr(mw, "is_xyz_derived"):
        mw.is_xyz_derived = False
    view3d(mw).draw_molecule_3d(mol)
    enter_viewer_mode(mw, True)
    v3d = view3d(mw)
    _call(v3d, "update_atom_id_menu_text")
    _call(v3d, "update_atom_id_menu_state")


def native_open(mw, path):
    """Open a file with the host's own loader. True when the host knows the type."""
    io = getattr(mw, "io_manager", None)
    ext = os.path.splitext(path)[1].lower()
    if io is None:
        return False
    if ext == ".xyz":
        io.load_xyz_for_3d_viewing(file_path=path)
        return True
    if ext in (".sdf", ".mol"):
        io.load_mol_file_for_3d_viewing(file_path=path)
        return True
    return False


def reset_view(mw):
    p = plotter(mw)
    if p is None:
        return
    try:
        p.view_isometric()
        p.reset_camera()
        p.render()
    except Exception:
        logging.debug("reset view failed", exc_info=True)


# --------------------------------------------------------------- interaction


def stop_interaction_modes(mw):
    """Leave measurement and 3D-edit modes, as the host does on a style change."""
    edit_3d = getattr(mw, "edit_3d_manager", None)
    init = getattr(mw, "init_manager", None)
    ui = getattr(mw, "ui_manager", None)
    if edit_3d is None or init is None:
        return
    if getattr(edit_3d, "measurement_mode", False):
        init.measurement_action.setChecked(False)
        edit_3d.toggle_measurement_mode(False)
    if getattr(edit_3d, "is_3d_edit_mode", False):
        init.edit_3d_action.setChecked(False)
        if ui is not None:
            ui.toggle_3d_edit_mode(False)


def set_interaction_enabled(mw, enabled):
    """Enable or disable the 3D Select and 3D Drag actions."""
    init = getattr(mw, "init_manager", None)
    if init is None:
        return
    if not enabled:
        stop_interaction_modes(mw)
    for name in ("measurement_action", "edit_3d_action"):
        action = getattr(init, name, None)
        if action is not None:
            action.setEnabled(bool(enabled))
