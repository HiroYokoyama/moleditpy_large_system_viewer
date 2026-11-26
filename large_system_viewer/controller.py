"""Coordinates files, the Fast style, frames and the panel."""

import logging
import os
import time

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a host dependency
    np = None

from . import formats, host, settings
from .session import MODE_CONFORMERS, MODE_HYBRID, MODE_VIEW_ONLY, Session

logger = logging.getLogger(__name__)

STYLE_NAME = "Large System (Fast)"

_DISPLAY_KEYS = frozenset(
    (
        "representation",
        "ribbon_color_mode",
        "ribbon_opacity",
        "atom_opacity",
        "ligand_style",
        "ribbon_helix_color",
        "ribbon_sheet_color",
        "ribbon_coil_color",
        "ribbon_uniform_color",
        "inherit_host_appearance",
        "ball_atom_scale",
        "ball_bond_radius",
        "spacefill_scale",
        "licorice_radius",
        "bond_color_by_atom",
        "bond_color",
        "specular",
        "specular_power",
        "show_hydrogens",
        "show_water",
        "ribbon_helix_width",
        "ribbon_coil_width",
        "ribbon_subdivide",
        "show_box",
        "box_color",
        "box_line_width",
    )
)


class Controller:
    def __init__(self, context, values=None):
        self.context = context
        self.values = values if values is not None else settings.load()
        self.session = None
        self.scene = None
        self.panel = None  # SettingsPanel
        self.panel_container = None  # its window or dock
        self.toolbar = None
        self.drawn_representation = None
        self.fallback_reason = ""
        self._previous_style = None
        self._auto_switched = False
        self._declined = False
        self._skip_prompt = False
        self._pending_restore = None
        self._loading = False
        self._playing = False
        self._direction = 1  # +1 forward, -1 backward (bounce)
        self._vdw = None

    # ------------------------------------------------------------------ basics

    @property
    def mw(self):
        return self.context.get_main_window()

    def status(self, message, timeout=4000):
        try:
            self.context.show_status_message(message, timeout)
        except Exception:
            logger.debug("status message failed", exc_info=True)

    def fast_active(self):
        return host.current_style(self.mw) == STYLE_NAME

    @property
    def playing(self):
        return self._playing

    def _sync_panel(self):
        if self.panel is not None:
            try:
                self.panel.sync()
            except RuntimeError:  # deleted widget
                self.panel = None
        try:
            from .panel import sync_toolbar

            sync_toolbar(self)
        except RuntimeError:
            self.toolbar = None
        except Exception:
            logger.debug("toolbar update failed", exc_info=True)

    # --------------------------------------------------------------- settings

    def set_value(self, key, value):
        if key not in settings.DEFAULTS:
            return
        value = settings.coerce(key, value)
        if self.values.get(key) == value:
            return
        self.values[key] = value
        settings.save(self.values)
        if key in _DISPLAY_KEYS:
            self.refresh()
        if key == "panel_mode":
            self._later(self._move_panel)
        elif key in ("show_toolbar", "toolbar_area"):
            self._sync_panel()

    def reset_keys(self, keys):
        """Restore the given settings to their defaults and redraw once."""
        changed = [
            k
            for k in keys
            if k in settings.DEFAULTS and self.values.get(k) != settings.DEFAULTS[k]
        ]
        for key in changed:
            self.values[key] = settings.DEFAULTS[key]
        if changed:
            settings.save(self.values)
            if any(k in _DISPLAY_KEYS for k in changed):
                self.refresh()
        self._sync_panel()

    def reset_settings(self):
        self.reset_keys(tuple(settings.DEFAULTS))

    def _options(self, representation):
        mw = self.mw
        if self._vdw is None:
            from .render import vdw_radii_table

            self._vdw = vdw_radii_table()
        from .render import color_table

        opts = dict(self.values)
        opts["representation"] = representation
        # Ribbon + Ligands draws its ligands in the chosen ligand style.
        look_style = (
            self.values.get("ligand_style", "ball_and_stick")
            if representation in settings.RIBBON_REPRESENTATIONS
            else representation
        )
        opts["look"] = settings.appearance(self.values, host.settings(mw), look_style)
        opts["vdw"] = self._vdw
        opts["colors"] = color_table(host.cpk_colors(mw))
        return opts

    # ---------------------------------------------------------------- drawing

    def draw(self, mw, mol):
        """Style callback registered with register_3d_style."""
        v3d = host.view3d(mw)
        if getattr(v3d, "_drawing_3d", False):
            return
        v3d._drawing_3d = True
        try:
            self._draw(mol)
        finally:
            v3d._drawing_3d = False
        self._sync_panel()

    def _draw(self, mol):
        s = self.session
        if mol is None:
            if s is not None and s.mode == MODE_VIEW_ONLY:
                self._render(s.positions(), s.box(), reset_camera=False)
            else:
                camera = host.begin_scene(self.mw)
                self.scene = None
                host.end_scene(self.mw, camera)
            return
        if s is None or s.mol is not mol:
            self._adopt_molecule(mol)
            s = self.session
            if self.panel is None:
                self._later(lambda: self.show_panel(auto=True))
        elif (
            not s.detached
            and s.mode != MODE_VIEW_ONLY
            and not s.host_matches_commit(mol)
        ):
            s.detached = True
            self.pause(commit=False)
            self.status("Coordinates were edited: playback is off for this structure.")
        positions = (
            mol.GetConformer().GetPositions().astype(np.float32)
            if mol.GetNumConformers()
            else s.positions()
        )
        box = None if s.detached else s.box(s.committed_index)
        self._render(positions, box, reset_camera=False)
        host.set_picking_positions(self.mw, positions)

    def _adopt_molecule(self, mol):
        """Start a conformer session for a molecule that did not come from a file."""
        from .system import from_rdkit

        system, frames = from_rdkit(mol)
        self._close_session()
        self.session = Session(
            system, formats.ArrayFrames(frames), MODE_CONFORMERS, mol=mol
        )

    def _render(self, positions, box, reset_camera):
        from .render import FastScene

        s = self.session
        mw = self.mw
        camera = host.begin_scene(mw)
        self.scene = FastScene(host.plotter(mw))
        try:
            self.drawn_representation = self.scene.build(
                s.system, positions, box, self._options(self.values["representation"])
            )
            self.fallback_reason = self.scene.fallback_reason
        except Exception:
            logger.exception("Large System Viewer: drawing failed")
            self.status(
                "Large System Viewer could not draw this structure (see the log)."
            )
        host.end_scene(mw, camera, reset_camera=reset_camera)

    def refresh(self):
        """Redraw after a display setting changed."""
        if not self.fast_active() or self.session is None:
            return
        if self.session.mode == MODE_VIEW_ONLY:
            self._render(
                self.session.positions(), self.session.box(), reset_camera=False
            )
            self._sync_panel()
        else:
            host.redraw(self.mw)

    # ------------------------------------------------------------- frames

    def show_frame(self, index, commit):
        s = self.session
        if s is None or s.n_frames == 0:
            return
        if s.detached and index != s.index:
            return
        index = max(0, min(int(index), s.n_frames - 1))
        s.index = index
        try:
            positions, box = s.frame(index)
        except Exception:
            logger.exception("Large System Viewer: cannot read frame %d", index + 1)
            self.pause(commit=False)
            self.status(f"Cannot read frame {index + 1} (see the log).")
            return
        if self.fast_active():
            if self.scene is not None and self.scene.is_attached():
                self.scene.update(positions, box)
                host.plotter(self.mw).render()
            elif s.mode == MODE_VIEW_ONLY:
                self._render(positions, box, reset_camera=False)
            else:
                commit = True
        elif s.mode != MODE_VIEW_ONLY:
            commit = True
        if commit:
            self.commit()
        self._sync_panel()

    def commit(self):
        """Write the shown frame into the host molecule (no undo step, not 'modified')."""
        s = self.session
        if s is None or s.mode == MODE_VIEW_ONLY or s.mol is None or s.detached:
            return
        positions = s.positions()
        host.store_positions(self.mw, s.mol, positions)
        s.committed_index = s.index
        if not self.fast_active():
            host.redraw(self.mw)

    def play(self):
        s = self.session
        if s is None or not s.can_play or self._playing:
            return
        self._playing = True
        host.set_interaction_enabled(self.mw, False)
        if s.index == 0:
            self._direction = 1
        if s.index >= s.n_frames - 1 and not self.values["loop"]:
            self._direction = 1
            self.show_frame(0, commit=False)
        self._schedule(0)
        self._sync_panel()

    def pause(self, commit=True):
        was_playing = self._playing
        self._playing = False
        if was_playing:
            host.set_interaction_enabled(
                self.mw,
                self.session is not None and self.session.mode != MODE_VIEW_ONLY,
            )
            if commit:
                self.commit()
        self._sync_panel()

    def toggle_play(self):
        if self._playing:
            self.pause()
        else:
            self.play()

    def step(self, delta):
        s = self.session
        if s is None:
            return
        self.pause(commit=False)
        self.show_frame(s.index + delta, commit=True)

    def _schedule(self, delay_ms):
        from PySide6.QtCore import QTimer

        QTimer.singleShot(max(0, int(delay_ms)), self._tick)

    def _tick(self):
        s = self.session
        if not self._playing or s is None:
            return
        started = time.perf_counter()
        nxt = self._next_index(s.index, s.n_frames)
        if nxt is None:
            # The end: the last frame, or the first after playing there and back.
            last = 0 if self.values.get("bounce") else s.n_frames - 1
            self.show_frame(last, commit=False)
            self.pause()
            return
        self.show_frame(nxt, commit=False)
        if not self._playing:
            return
        elapsed = (time.perf_counter() - started) * 1000.0
        self._schedule(1000.0 / float(self.values["fps"]) - elapsed)

    def _next_index(self, index, n_frames):
        """Next frame while playing, or None to stop at the end."""
        stride = int(self.values["stride"])
        if not self.values.get("bounce"):
            self._direction = 1
        nxt = index + stride * self._direction
        if self.values.get("bounce") and n_frames > 1:
            if nxt >= n_frames:
                self._direction = -1
                nxt = max(0, index - stride)
            elif nxt < 0:
                self._direction = 1
                if not self.values["loop"]:
                    return None  # once there and back
                nxt = min(n_frames - 1, index + stride)
            return nxt
        if nxt >= n_frames:
            return 0 if self.values["loop"] else None
        return nxt

    # ------------------------------------------------------------- the hook

    def on_new_molecule(self, mol):
        """Host received a new current molecule (any loader, undo, other plugins)."""
        if self._loading:
            return
        pending, self._pending_restore = self._pending_restore, None
        if pending is not None:
            self._apply_restore(pending, mol)
        if self.session is not None and self.session.mol is mol:
            return
        mw = self.mw
        n = mol.GetNumAtoms()
        threshold = int(self.values["switch_threshold"])
        if self.fast_active():
            if (
                n <= threshold
                and self._auto_switched
                and self.values["restore_style"]
                and self._previous_style
            ):
                self._auto_switched = False
                host.set_style(mw, self._previous_style)
            return
        if n <= threshold or self._declined:
            return
        if self._skip_prompt:
            self._skip_prompt = False
            return
        if self.decide_switch(n):
            self.switch_to_fast(auto=True)
        else:
            self._declined = True

    def decide_switch(self, n_atoms):
        """True to use the Fast style for a large structure (asks when configured to)."""
        action = self.values["large_action"]
        if action == "switch":
            return True
        if action == "keep" or host.is_headless():
            return False
        from .panel import ask_switch

        answer, remember = ask_switch(
            self.mw, n_atoms, int(self.values["switch_threshold"])
        )
        if remember:
            self.set_value("large_action", "switch" if answer else "keep")
            self._sync_panel()
        return answer

    def switch_to_fast(self, auto=False):
        mw = self.mw
        style = host.current_style(mw)
        if style != STYLE_NAME:
            self._previous_style = style
            self._auto_switched = auto
            host.set_style(mw, STYLE_NAME)
        self.show_panel(auto=True)

    def restore_style(self):
        previous = self._previous_style
        if not previous or previous == STYLE_NAME:
            previous = "ball_and_stick"
        self._auto_switched = False
        host.set_style(self.mw, previous)
        self._sync_panel()

    def export_blocked(self):
        """True (after telling the user) when a mesh export would miss the atoms."""
        if (
            not self.fast_active()
            or self.scene is None
            or self.drawn_representation == "ribbon"
        ):
            return False
        self._error(
            "STL/OBJ export needs real meshes, which the Large System (Fast) style does "
            "not create for atoms and bonds. Switch to a standard 3D style to export, "
            "or use the Ribbon representation to export the ribbon alone. "
            "3D image export (PNG) works in the Fast style."
        )
        return True

    def document_reset(self):
        if self._loading:
            return
        self.pause(commit=False)
        self._declined = False
        self._close_session()
        self._sync_panel()

    def _close_session(self):
        if self.session is not None:
            self.session.close()
        self.session = None
        self.scene = None

    # --------------------------------------------------------------- files

    def drop_file(self, path):
        """Drop handler: small .xyz/.sdf/.mol keep the host's own drop behaviour."""
        return self.open_file(path, dropped=True)

    def open_file(self, path, dropped=False):
        """File opener and drop handler.

        The Import menu does not fall back to the host when an opener returns False,
        so the small .xyz/.sdf case calls the host's own loader instead of declining.
        """
        mw = self.mw
        if not formats.supported(path):
            return False
        try:
            peek = formats.peek(path)
        except Exception as exc:  # noqa: BLE001 - malformed or unusual file: let the host try it
            logger.info("Large System Viewer: %s not recognised: %s", path, exc)
            if dropped and host.native_open_supported(path):
                return False
            if host.native_open(mw, path):
                return True
            self._error(f"Cannot open {os.path.basename(path)}: {exc}")
            return True

        large = peek.n_atoms > int(self.values["switch_threshold"])
        if not large and not peek.multi_frame:
            if dropped and host.native_open_supported(path):
                return False
            if host.native_open(mw, path):
                return True
            return self._open_small(path)

        if large:
            use_fast = self.fast_active() or self.decide_switch(peek.n_atoms)
        else:
            use_fast = self.fast_active()
        if (
            large
            and not use_fast
            and not peek.multi_frame
            and host.native_open_supported(path)
        ):
            # The host loads it under the current style; do not ask again on arrival.
            self._skip_prompt = True
            self._clear_skip_later()
            if dropped:
                return False
            host.native_open(mw, path)
            return True
        if not host.check_unsaved(mw):
            return True
        return self._open_session(path, use_fast, declined=large and not use_fast)

    def _clear_skip_later(self):
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, self._clear_skip)
        except ImportError:
            self._skip_prompt = False

    def _clear_skip(self):
        self._skip_prompt = False

    def _open_small(self, path):
        mw = self.mw
        if not host.check_unsaved(mw):
            return True
        try:
            mol = self._small_molecule(path)
        except Exception as exc:
            logger.exception("Large System Viewer: cannot read %s", path)
            self._error(f"Cannot open {os.path.basename(path)}:\n{exc}")
            return True
        self._loading = True
        try:
            host.clear_document(mw)
            host.load_molecule(mw, mol)
        finally:
            self._loading = False
        host.finish_file(mw, path)
        self._reset_view_later()
        self.status(f"Loaded {os.path.basename(path)} ({mol.GetNumAtoms()} atoms)")
        return True

    def _small_molecule(self, path):
        from .system import SS_PROP, to_rdkit

        system, frames = formats.open_structure(path)
        if formats.format_of(path) == "pdb":
            from rdkit import Chem

            mol = Chem.MolFromPDBFile(path, removeHs=False, sanitize=False)
            if mol is not None and mol.GetNumAtoms() == system.n_atoms:
                try:
                    Chem.SanitizeMol(mol)
                except Exception:  # noqa: BLE001 - keep RDKit's unsanitized molecule
                    mol.UpdatePropertyCache(strict=False)
                if system.ss is not None:
                    for atom, code in zip(mol.GetAtoms(), system.ss.tolist()):
                        atom.SetIntProp(SS_PROP, int(code))
                return mol
        return to_rdkit(system, frames.read(0))

    def _open_session(self, path, use_fast, declined):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        mw = self.mw
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            started = time.perf_counter()
            system, frames = formats.open_structure(path)
            mode = (
                MODE_VIEW_ONLY
                if (use_fast and system.n_atoms > int(self.values["host_link_limit"]))
                else MODE_HYBRID
            )
            mol = None
            if mode == MODE_HYBRID:
                mol = self._session_molecule(system, frames)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            logger.exception("Large System Viewer: cannot read %s", path)
            self._error(f"Cannot open {os.path.basename(path)}:\n{exc}")
            return True

        self._loading = True
        try:
            self.pause(commit=False)
            host.clear_document(mw)
            self._close_session()
            self._declined = declined
            self.session = Session(system, frames, mode, path=path, mol=mol)
            if use_fast and not self.fast_active():
                self._previous_style = host.current_style(mw)
                self._auto_switched = True
                host.set_style(mw, STYLE_NAME)
            if mode == MODE_HYBRID:
                host.load_molecule(mw, mol)
            else:
                host.enter_viewer_mode(mw, features=False)
                self._render(
                    self.session.positions(), self.session.box(), reset_camera=True
                )
                host.set_picking_positions(mw, None)
        finally:
            self._loading = False
            QApplication.restoreOverrideCursor()
        host.finish_file(mw, path)
        self._reset_view_later()
        self.show_panel(auto=True)
        seconds = time.perf_counter() - started
        extra = (
            " (view-only: too large for MoleditPy tools)"
            if mode == MODE_VIEW_ONLY
            else ""
        )
        self.status(
            f"Loaded {os.path.basename(path)}: {system.n_atoms} atoms, "
            f"{frames.n_frames} frame(s) in {seconds:.1f} s{extra}",
            8000,
        )
        return True

    @staticmethod
    def _session_molecule(system, frames):
        from rdkit import Chem

        from .system import to_rdkit

        first = system.extra.get("rdkit_mol")
        if first is not None:
            mol = Chem.Mol(first)
            if mol.GetNumConformers() > 1:
                conf = Chem.Conformer(mol.GetConformer())
                mol.RemoveAllConformers()
                mol.AddConformer(conf, assignId=True)
            return mol
        return to_rdkit(system, frames.read(0))

    @staticmethod
    def _later(fn, delay_ms=0):
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(delay_ms, fn)
        except ImportError:
            pass

    def _reset_view_later(self):
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(50, lambda: host.reset_view(self.mw))
        except ImportError:
            pass

    def _error(self, text):
        try:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self.mw, "Large System Viewer", text)
        except Exception:
            logger.exception("Large System Viewer: %s", text)

    # ------------------------------------------------------------- panel

    def show_panel(self, auto=False):
        """Open the settings. Automatic openings follow the auto_open_panel setting."""
        from .panel import panel_visible, show_panel

        if auto and not self.values.get("auto_open_panel", True):
            self._sync_panel()
            return
        if auto and panel_visible(self):
            self._sync_panel()
            return
        show_panel(self)
        self._sync_panel()

    def _move_panel(self):
        """Reopen visible settings in the container chosen by panel_mode."""
        from .panel import panel_visible

        if panel_visible(self):
            self.show_panel()

    # ------------------------------------------------------- persistence

    def state(self):
        s = self.session
        if s is None or not s.path:
            return {}
        return {
            "path": s.path,
            "frame": int(s.index),
            "representation": self.values["representation"],
        }

    def restore(self, data):
        """Project load handler.

        MoleditPy calls load handlers before it sets the project's molecule, so the
        state is kept until that molecule arrives (on_new_molecule) and dropped once
        the load has finished.
        """
        if not isinstance(data, dict) or not isinstance(data.get("path"), str):
            return
        self._pending_restore = data
        self._later(self._drop_pending_restore)

    def _drop_pending_restore(self):
        self._pending_restore = None

    def _apply_restore(self, data, mol):
        """Reattach a file session when the file still exists and the atom count matches."""
        path = data["path"]
        if not os.path.isfile(path):
            return False
        try:
            system, frames = formats.open_structure(path)
        except Exception:
            logger.warning("Large System Viewer: cannot reopen %s", path, exc_info=True)
            return False
        if system.n_atoms != mol.GetNumAtoms():
            frames.close()
            return False
        self._close_session()
        self.session = Session(system, frames, MODE_HYBRID, path=path, mol=mol)
        frame = data.get("frame", 0)
        if isinstance(frame, int) and 0 <= frame < frames.n_frames:
            self.session.index = frame
            self.session.committed_index = frame
        rep = data.get("representation")
        if rep in dict(settings.REPRESENTATIONS):
            self.values["representation"] = rep
        if not self.session.host_matches_commit(mol):
            self.session.detached = True
        self._later(self._sync_panel)
        return True

    def shutdown(self):
        from .panel import close_panel

        self.pause(commit=False)
        self._close_session()
        close_panel(self)
        if self.toolbar is not None:
            try:
                self.mw.removeToolBar(self.toolbar)
                self.toolbar.deleteLater()
            except RuntimeError:
                pass
            self.toolbar = None
        host.uninstall_molecule_hook(self.mw)
        host.uninstall_export_guard(self.mw)
