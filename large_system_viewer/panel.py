"""Settings (a separate window or the right-hand panel), the toolbar and the switch question."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStyle,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import settings
from .session import MODE_CONFORMERS, MODE_VIEW_ONLY
from .wheel_guard import install_wheel_guard

logger = logging.getLogger(__name__)

DOCK_OBJECT_NAME = "LargeSystemViewerDock"
WINDOW_OBJECT_NAME = "LargeSystemViewerSettings"
TOOLBAR_OBJECT_NAME = "LargeSystemViewerToolBar"
TITLE = "Large System Viewer"

# Settings MoleditPy supplies while "Use MoleditPy appearance" is on.
_INHERITED = (
    "ball_atom_scale",
    "ball_bond_radius",
    "spacefill_scale",
    "licorice_radius",
    "bond_color_by_atom",
    "bond_color",
    "specular",
    "specular_power",
)

# Keys restored by each Reset button (one per tab).
GROUP_KEYS = {
    "structure": ("representation", "fps", "stride", "loop", "bounce"),
    "appearance": (
        "inherit_host_appearance",
        *_INHERITED,
        "show_hydrogens",
        "show_water",
        "atom_opacity",
        "ligand_style",
    ),
    "ribbon": (
        "ribbon_color_mode",
        "ribbon_helix_color",
        "ribbon_sheet_color",
        "ribbon_coil_color",
        "ribbon_uniform_color",
        "ribbon_opacity",
        "ribbon_helix_width",
        "ribbon_coil_width",
        "ribbon_subdivide",
    ),
    "box": ("show_box", "box_color", "box_line_width"),
    "behaviour": (
        "large_action",
        "switch_threshold",
        "restore_style",
        "host_link_limit",
        "panel_mode",
        "auto_open_panel",
        "show_toolbar",
        "toolbar_area",
    ),
}

_PLAYBACK_BUTTONS = (
    ("first", QStyle.StandardPixmap.SP_MediaSkipBackward, "First frame"),
    ("prev", QStyle.StandardPixmap.SP_MediaSeekBackward, "Previous frame"),
    ("play", QStyle.StandardPixmap.SP_MediaPlay, "Play / Pause"),
    ("next", QStyle.StandardPixmap.SP_MediaSeekForward, "Next frame"),
    ("last", QStyle.StandardPixmap.SP_MediaSkipForward, "Last frame"),
)


def ask_switch(parent, n_atoms, threshold):
    """(switch, remember) for a structure above the threshold. Yes is the default."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(TITLE)
    box.setText(
        f"This structure has {n_atoms:,} atoms (more than {threshold:,}).\n\n"
        "Switch to the Large System (Fast) style?"
    )
    box.setInformativeText(
        "The Fast style draws large systems and trajectories quickly. "
        "No keeps the current style."
    )
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.setDefaultButton(QMessageBox.StandardButton.Yes)
    remember = QCheckBox("Do not ask again")
    box.setCheckBox(remember)
    answer = box.exec() == QMessageBox.StandardButton.Yes
    return answer, remember.isChecked()


def _alive(widget):
    if widget is None:
        return False
    try:
        widget.objectName()
    except RuntimeError:  # the C++ object was deleted
        return False
    return True


# ------------------------------------------------------------------ containers


def show_panel(controller):
    """Show the settings in the container chosen by the panel_mode setting."""
    mode = controller.values.get("panel_mode", "window")
    container = controller.panel_container
    if not _alive(container) or getattr(container, "mode", None) != mode:
        close_panel(controller)
        container = _create_container(controller, mode)
    container.show()
    container.raise_()
    if mode == "window":
        container.activateWindow()
    controller.panel.sync()
    return container


def close_panel(controller):
    container = controller.panel_container
    controller.panel_container = None
    controller.panel = None
    if not _alive(container):
        return
    try:
        if getattr(container, "mode", None) == "dock":
            controller.mw.removeDockWidget(container)
        container.hide()
        container.deleteLater()
    except RuntimeError:
        pass


def panel_visible(controller):
    container = controller.panel_container
    return _alive(container) and container.isVisible()


def _create_container(controller, mode):
    mw = controller.mw
    panel = SettingsPanel(controller)
    if mode == "dock":
        container = QDockWidget(TITLE, mw)
        container.setObjectName(DOCK_OBJECT_NAME)
        container.setWidget(panel)
        container.setMinimumWidth(300)
        mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, container)
    else:
        container = SettingsWindow(mw)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(panel)
    container.mode = mode
    controller.panel_container = container
    controller.panel = panel
    return container


class SettingsWindow(QWidget):
    """The settings as a tool window beside the 3D view rather than over its centre."""

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(f"{TITLE} Settings")
        self.resize(400, 620)
        self._positioned = False

    def showEvent(self, event):
        super().showEvent(event)
        # Only on first show, so a window the user has moved stays where it is.
        if not self._positioned:
            self._positioned = True
            try:
                self.move(*window_position(self.parentWidget(), self))
            except (RuntimeError, AttributeError) as exc:
                logger.warning(
                    "Large System Viewer: placing the settings window failed: %s", exc
                )


def window_position(main, window):
    """Top-left corner near the main window's right edge, kept on its screen."""
    frame = main.frameGeometry()
    width = window.width()
    height = window.height()
    x = frame.right() - width - 24
    y = frame.top() + 110
    screen = main.screen()
    if screen is not None:
        area = screen.availableGeometry()
        x = min(max(x, area.left()), area.right() - width)
        y = min(max(y, area.top()), area.bottom() - height)
    return x, y


# ------------------------------------------------------------------ shared logic


class _Playback:
    """Frame navigation shared by the settings and the toolbar."""

    def _make_buttons(self, add):
        style = self.style()
        slots = {
            "first": lambda: self._goto(0),
            "prev": lambda: self.controller.step(-1),
            "play": self.controller.toggle_play,
            "next": lambda: self.controller.step(1),
            "last": lambda: self._goto(-1),
        }
        self.buttons = {}
        for name, icon, tip in _PLAYBACK_BUTTONS:
            button = QToolButton()
            button.setIcon(style.standardIcon(icon))
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.clicked.connect(slots[name])
            add(button)
            self.buttons[name] = button

    def _make_slider(self):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.valueChanged.connect(self._slider_moved)
        slider.sliderReleased.connect(self._slider_released)
        self.slider = slider
        return slider

    def _make_representation(self):
        combo = QComboBox()
        for key, label in settings.REPRESENTATIONS:
            combo.addItem(label, key)
        combo.currentIndexChanged.connect(
            lambda _i: self._set("representation", combo.currentData())
        )
        self.representation = combo
        return combo

    def _set(self, key, value):
        if self._syncing:
            return
        try:
            self.controller.set_value(key, value)
        except Exception:
            logger.exception("Large System Viewer: setting %s failed", key)

    def _goto(self, index):
        s = self.controller.session
        if s is None:
            return
        self.controller.pause(commit=False)
        self.controller.show_frame(s.n_frames - 1 if index < 0 else index, commit=True)

    def _slider_moved(self, value):
        if self._syncing:
            return
        c = self.controller
        c.pause(commit=False)
        c.show_frame(value, commit=not self.slider.isSliderDown())

    def _slider_released(self):
        if self._syncing:
            return
        self.controller.show_frame(self.slider.value(), commit=True)

    def _sync_playback(self, n_frames):
        c = self.controller
        s = c.session
        can_play = s is not None and s.can_play
        for button in self.buttons.values():
            button.setEnabled(can_play)
        self.slider.setEnabled(can_play)
        self.slider.setMaximum(max(0, n_frames - 1))
        if not self.slider.isSliderDown():
            self.slider.setValue(s.index if s is not None else 0)
        icon = (
            QStyle.StandardPixmap.SP_MediaPause
            if c.playing
            else QStyle.StandardPixmap.SP_MediaPlay
        )
        self.buttons["play"].setIcon(self.style().standardIcon(icon))
        combo = self.representation
        combo.setCurrentIndex(max(0, combo.findData(c.values["representation"])))


# ------------------------------------------------------------------ toolbar


class LargeSystemToolBar(_Playback, QToolBar):
    """Representation, frames and a button for the settings."""

    def __init__(self, controller, parent=None):
        super().__init__(f"{TITLE} Toolbar", parent)
        self.setObjectName(TOOLBAR_OBJECT_NAME)
        self.controller = controller
        self._syncing = False
        self.addWidget(QLabel(" Large System: "))
        self.addWidget(self._make_representation())
        self.addSeparator()
        self._make_buttons(self.addWidget)
        slider = self._make_slider()
        slider.setMinimumWidth(240)
        self.addWidget(slider)
        self.frame_label = QLabel("")
        self.frame_label.setMinimumWidth(90)
        self.addWidget(self.frame_label)
        self.addSeparator()
        settings_button = QToolButton()
        settings_button.setText("Settings...")
        settings_button.setToolTip("Large System Viewer settings")
        # A lambda, so clicked(bool) does not arrive as show_panel's auto argument.
        settings_button.clicked.connect(lambda: controller.show_panel())
        self.addWidget(settings_button)
        install_wheel_guard(self)

    def sync(self):
        c = self.controller
        self._syncing = True
        try:
            s = c.session
            n_frames = 0 if s is None else s.n_frames
            self._sync_playback(n_frames)
            self.frame_label.setText(
                "" if s is None else f" Frame {s.index + 1:,} / {n_frames:,}"
            )
        finally:
            self._syncing = False


def sync_toolbar(controller):
    """Create, update, show or hide the toolbar."""
    want = bool(controller.values.get("show_toolbar", True)) and (
        controller.session is not None or controller.fast_active()
    )
    bar = controller.toolbar
    if not _alive(bar):
        bar = None
        controller.toolbar = None
    area = (
        Qt.ToolBarArea.TopToolBarArea
        if controller.values.get("toolbar_area") == "top"
        else Qt.ToolBarArea.BottomToolBarArea
    )
    mw = controller.mw
    if bar is None:
        if not want:
            return
        bar = LargeSystemToolBar(controller, mw)
        mw.addToolBar(area, bar)
        bar.area = area
        controller.toolbar = bar
    elif getattr(bar, "area", None) != area:
        mw.addToolBar(area, bar)  # moves it
        bar.area = area
    bar.sync()
    bar.setVisible(want)


# ------------------------------------------------------------------ settings


class SettingsPanel(_Playback, QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._syncing = False
        self._bound = {}  # key -> (widget, setter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        for title, builder in (
            ("Structure", self._structure_tab),
            ("Appearance", self._appearance_tab),
            ("Ribbon", self._ribbon_tab),
            ("Box", self._box_tab),
            ("Behaviour", self._behaviour_tab),
        ):
            self.tabs.addTab(self._scrolled(builder()), title)
        layout.addWidget(self.tabs)
        reset = QPushButton("Reset All Settings")
        reset.setToolTip("Restore every Large System Viewer setting to its default")
        reset.clicked.connect(self._reset)
        layout.addWidget(reset)

    def _scrolled(self, body):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(body)
        install_wheel_guard(body)
        return scroll

    # ------------------------------------------------------------ tabs

    def _structure_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        info_group = QGroupBox("Structure")
        info = QVBoxLayout(info_group)
        self.info = QLabel("No structure")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(self.info)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #a05a00;")
        info.addWidget(self.note)
        form = QFormLayout()
        form.addRow("Representation:", self._make_representation())
        info.addLayout(form)
        box.addWidget(info_group)

        frames = QGroupBox("Frames")
        self.playback_group = frames
        fbox = QVBoxLayout(frames)
        row = QHBoxLayout()
        self._make_buttons(row.addWidget)
        row.addStretch(1)
        fbox.addLayout(row)
        fbox.addWidget(self._make_slider())
        frame_row = QHBoxLayout()
        frame_row.addWidget(QLabel("Frame:"))
        self.frame_spin = QSpinBox()
        self.frame_spin.setMinimum(1)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.valueChanged.connect(self._frame_typed)
        frame_row.addWidget(self.frame_spin)
        self.frame_total = QLabel("/ 0")
        frame_row.addWidget(self.frame_total)
        frame_row.addStretch(1)
        fbox.addLayout(frame_row)
        fform = QFormLayout()
        fform.addRow("Frames per second:", self._spin("fps", 1, 120))
        fform.addRow("Step (frames):", self._spin("stride", 1, 100000))
        fform.addRow(self._check("loop", "Loop"))
        fform.addRow(self._check("bounce", "Play back and forth"))
        fbox.addLayout(fform)
        box.addWidget(frames)
        self._add_reset(box, "structure")
        return page

    def _appearance_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        form = QFormLayout()
        form.addRow(
            self._check("inherit_host_appearance", "Use MoleditPy appearance settings")
        )
        form.addRow(
            "Ball atom scale:", self._dspin("ball_atom_scale", 0.05, 10.0, 0.05)
        )
        form.addRow(
            "Ball bond radius:", self._dspin("ball_bond_radius", 0.01, 2.0, 0.01)
        )
        form.addRow(
            "Spacefill scale:", self._dspin("spacefill_scale", 0.05, 10.0, 0.05)
        )
        form.addRow("Licorice radius:", self._dspin("licorice_radius", 0.01, 2.0, 0.01))
        form.addRow(self._check("bond_color_by_atom", "Colour bonds by atom"))
        form.addRow("Bond colour:", self._color("bond_color"))
        form.addRow("Specular:", self._dspin("specular", 0.0, 1.0, 0.05))
        form.addRow("Specular power:", self._dspin("specular_power", 1.0, 200.0, 1.0))
        form.addRow(self._check("show_hydrogens", "Show hydrogens"))
        form.addRow(self._check("show_water", "Show water"))
        form.addRow("Atom opacity:", self._dspin("atom_opacity", 0.05, 1.0, 0.05))
        form.addRow(
            "Ligands (Ribbon + Ligands):",
            self._choice("ligand_style", settings.LIGAND_STYLES),
        )
        box.addLayout(form)
        self._add_reset(box, "appearance")
        return page

    def _ribbon_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        colours = QGroupBox("Colour")
        form = QFormLayout(colours)
        form.addRow(
            "Colour by:", self._choice("ribbon_color_mode", settings.RIBBON_COLOR_MODES)
        )
        form.addRow("Helix:", self._color("ribbon_helix_color"))
        form.addRow("Sheet:", self._color("ribbon_sheet_color"))
        form.addRow("Coil:", self._color("ribbon_coil_color"))
        form.addRow("Single colour:", self._color("ribbon_uniform_color"))
        box.addWidget(colours)
        shape = QGroupBox("Shape")
        sform = QFormLayout(shape)
        sform.addRow(
            "Helix/sheet width:", self._dspin("ribbon_helix_width", 0.05, 10.0, 0.1)
        )
        sform.addRow("Coil width:", self._dspin("ribbon_coil_width", 0.05, 10.0, 0.05))
        sform.addRow("Smoothness:", self._spin("ribbon_subdivide", 1, 100))
        sform.addRow("Opacity:", self._dspin("ribbon_opacity", 0.05, 1.0, 0.05))
        box.addWidget(shape)
        self._add_reset(box, "ribbon")
        return page

    def _box_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        form = QFormLayout()
        form.addRow(self._check("show_box", "Show periodic box"))
        form.addRow("Colour:", self._color("box_color"))
        form.addRow("Line width:", self._dspin("box_line_width", 0.5, 20.0, 0.5))
        box.addLayout(form)
        self._add_reset(box, "box")
        return page

    def _behaviour_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        switching = QGroupBox("Large structures")
        form = QFormLayout(switching)
        form.addRow(
            "When opened:", self._choice("large_action", settings.LARGE_ACTIONS)
        )
        threshold = self._spin("switch_threshold", 1, 100_000_000)
        threshold.setSuffix(" atoms")
        threshold.setSingleStep(500)
        form.addRow("Large above:", threshold)
        form.addRow(
            self._check(
                "restore_style", "Restore the previous style for small structures"
            )
        )
        limit = self._spin("host_link_limit", 1, 100_000_000)
        limit.setSuffix(" atoms")
        limit.setSingleStep(10000)
        limit.setToolTip(
            "Files above this size open view-only: MoleditPy's measurement, editing and "
            "export tools are not available for them."
        )
        form.addRow("View-only above:", limit)
        box.addWidget(switching)

        windows = QGroupBox("Windows")
        wform = QFormLayout(windows)
        wform.addRow(
            "Show settings in:", self._choice("panel_mode", settings.PANEL_MODES)
        )
        wform.addRow(
            self._check(
                "auto_open_panel",
                "Open the settings when a large structure is opened",
            )
        )
        wform.addRow(self._check("show_toolbar", "Show the toolbar"))
        wform.addRow(
            "Toolbar at:", self._choice("toolbar_area", settings.TOOLBAR_AREAS)
        )
        box.addWidget(windows)
        self._add_reset(box, "behaviour")
        return page

    # ---------------------------------------------------------- widgets

    def _spin(self, key, lo, hi):
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda v: self._set(key, v))
        self._bound[key] = (w, lambda v: w.setValue(int(v)))
        return w

    def _dspin(self, key, lo, hi, step):
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setDecimals(2)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda v: self._set(key, v))
        self._bound[key] = (w, lambda v: w.setValue(float(v)))
        return w

    def _check(self, key, label):
        w = QCheckBox(label)
        w.toggled.connect(lambda v: self._set(key, v))
        self._bound[key] = (w, lambda v: w.setChecked(bool(v)))
        return w

    def _choice(self, key, choices):
        w = QComboBox()
        for value, label in choices:
            w.addItem(label, value)
        w.currentIndexChanged.connect(lambda _i: self._set(key, w.currentData()))
        self._bound[key] = (w, lambda v: w.setCurrentIndex(max(0, w.findData(v))))
        return w

    def _color(self, key):
        w = QPushButton()
        w.setFixedWidth(90)

        def paint(value):
            w.setText(value)
            w.setStyleSheet(f"background-color: {value};")

        def choose():
            color = QColorDialog.getColor(
                QColor(self.controller.values[key]), self, "Choose Colour"
            )
            if color.isValid():
                self._set(key, color.name().upper())
                paint(self.controller.values[key])

        w.clicked.connect(choose)
        self._bound[key] = (w, paint)
        return w

    def _add_reset(self, layout, group):
        row = QHBoxLayout()
        row.addStretch(1)
        button = QPushButton("Reset")
        button.setToolTip("Restore the defaults on this tab")
        button.clicked.connect(lambda: self._reset_group(group))
        row.addWidget(button)
        layout.addLayout(row)
        layout.addStretch(1)

    # ---------------------------------------------------------- actions

    def _set(self, key, value):
        super()._set(key, value)
        if key in ("inherit_host_appearance", "ribbon_color_mode"):
            self._update_enabled()

    def _reset_group(self, group):
        self.controller.reset_keys(GROUP_KEYS[group])

    def _reset(self):
        self.controller.reset_settings()

    def _frame_typed(self, value):
        if self._syncing:
            return
        self.controller.pause(commit=False)
        self.controller.show_frame(value - 1, commit=True)

    # ------------------------------------------------------------- sync

    def _update_enabled(self):
        values = self.controller.values
        inherit = bool(values.get("inherit_host_appearance"))
        for key in _INHERITED:
            widget = self._bound.get(key, (None,))[0]
            if widget is not None:
                widget.setEnabled(not inherit)
                widget.setToolTip(
                    "Taken from MoleditPy's 3D settings" if inherit else ""
                )
        mode = values.get("ribbon_color_mode")  # rainbow and chain need no colours
        for key in ("ribbon_helix_color", "ribbon_sheet_color", "ribbon_coil_color"):
            self._bound[key][0].setEnabled(mode == "secondary")
        self._bound["ribbon_uniform_color"][0].setEnabled(mode == "uniform")

    def sync(self):
        """Show the controller state without emitting changes."""
        c = self.controller
        self._syncing = True
        try:
            for key, (_widget, setter) in self._bound.items():
                setter(c.values[key])
            self._update_enabled()
            s = c.session
            fast = c.fast_active()
            if s is None:
                self.info.setText("No structure")
                n_frames = 0
            else:
                mode = {
                    MODE_VIEW_ONLY: "view-only",
                    MODE_CONFORMERS: "conformers",
                }.get(s.mode, "linked to MoleditPy")
                cell = "" if s.box() is None else "\nPeriodic box: yes"
                self.info.setText(
                    f"{s.title}\n{s.n_atoms:,} atoms, {s.n_frames:,} frame(s) ({mode}){cell}"
                )
                n_frames = s.n_frames
            notes = []
            if fast and c.fallback_reason:
                notes.append(c.fallback_reason)
            if s is not None and s.detached:
                notes.append(
                    "Coordinates were edited, so frames are off. Reopen the file to play it again."
                )
            self.note.setText("\n".join(notes))
            self.note.setVisible(bool(notes))
            self._sync_playback(n_frames)
            can_play = s is not None and s.can_play
            self.frame_spin.setEnabled(can_play)
            self.frame_spin.setMaximum(max(1, n_frames))
            self.frame_spin.setValue((s.index if s is not None else 0) + 1)
            self.frame_total.setText(f"/ {n_frames:,}")
        finally:
            self._syncing = False
