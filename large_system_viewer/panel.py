"""Right-hand dock panel and the switch question."""

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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import settings
from .session import MODE_CONFORMERS, MODE_VIEW_ONLY

logger = logging.getLogger(__name__)

DOCK_OBJECT_NAME = "LargeSystemViewerDock"

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


class _WheelNeedsFocus:
    """Ignore the mouse wheel until the widget has focus.

    Scrolling the panel would otherwise change whichever spin box, combo box or slider
    passes under the pointer. An ignored wheel event goes on to the scroll area.
    """

    def _init_focus(self):
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class SpinBox(_WheelNeedsFocus, QSpinBox):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_focus()


class DoubleSpinBox(_WheelNeedsFocus, QDoubleSpinBox):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_focus()


class ComboBox(_WheelNeedsFocus, QComboBox):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_focus()


class Slider(_WheelNeedsFocus, QSlider):
    def __init__(self, *args):
        super().__init__(*args)
        self._init_focus()


# Keys restored by each group's Reset button.
GROUP_KEYS = {
    "structure": ("representation",),
    "frames": ("fps", "stride", "loop"),
    "appearance": (
        "inherit_host_appearance",
        *_INHERITED,
        "show_hydrogens",
        "show_water",
    ),
    "ribbon": ("ribbon_helix_width", "ribbon_coil_width", "ribbon_subdivide"),
    "box": ("show_box", "box_color", "box_line_width"),
    "switching": (
        "large_action",
        "switch_threshold",
        "restore_style",
        "host_link_limit",
    ),
}


def ask_switch(parent, n_atoms, threshold):
    """(switch, remember) for a structure above the threshold. Yes is the default."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Large System Viewer")
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


def show_panel(controller):
    dock = controller.panel
    if dock is not None:
        try:
            dock.show()
            dock.raise_()
            dock.sync()
            return dock
        except RuntimeError:  # the C++ widget was deleted
            controller.panel = None
    mw = controller.mw
    dock = LargeSystemPanel(controller, mw)
    mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    controller.panel = dock
    dock.show()
    dock.sync()
    return dock


class LargeSystemPanel(QDockWidget):
    def __init__(self, controller, parent=None):
        super().__init__("Large System Viewer", parent)
        self.setObjectName(DOCK_OBJECT_NAME)
        self.controller = controller
        self._syncing = False
        self._bound = {}  # key -> (widget, setter)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(self._source_group())
        layout.addWidget(self._playback_group())
        layout.addWidget(self._display_group())
        layout.addWidget(self._ribbon_group())
        layout.addWidget(self._box_group())
        layout.addWidget(self._switch_group())
        reset = QPushButton("Reset All Settings")
        reset.setToolTip("Restore every Large System Viewer setting to its default")
        reset.clicked.connect(self._reset)
        layout.addWidget(reset)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.setWidget(scroll)
        self.setMinimumWidth(280)

    # ------------------------------------------------------------ groups

    def _source_group(self):
        group = QGroupBox("Structure")
        form = QVBoxLayout(group)
        self.info = QLabel("No structure")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addWidget(self.info)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #a05a00;")
        form.addWidget(self.note)
        row = QHBoxLayout()
        self.fast_button = QPushButton("Use Fast Style")
        self.fast_button.clicked.connect(self._toggle_fast)
        row.addWidget(self.fast_button)
        form.addLayout(row)
        rep_row = QFormLayout()
        self.representation = ComboBox()
        for key, label in settings.REPRESENTATIONS:
            self.representation.addItem(label, key)
        self.representation.currentIndexChanged.connect(
            lambda _i: self._set("representation", self.representation.currentData())
        )
        rep_row.addRow("Representation:", self.representation)
        form.addLayout(rep_row)
        form.addWidget(self._reset_button("structure"), 0, Qt.AlignmentFlag.AlignRight)
        self._bound["representation"] = (
            self.representation,
            lambda v: self.representation.setCurrentIndex(
                max(0, self.representation.findData(v))
            ),
        )
        return group

    def _playback_group(self):
        group = QGroupBox("Frames")
        self.playback_group = group
        box = QVBoxLayout(group)
        style = self.style()
        row = QHBoxLayout()
        self.buttons = {}
        for name, icon, tip, slot in (
            (
                "first",
                QStyle.StandardPixmap.SP_MediaSkipBackward,
                "First frame",
                lambda: self._goto(0),
            ),
            (
                "prev",
                QStyle.StandardPixmap.SP_MediaSeekBackward,
                "Previous frame",
                lambda: self.controller.step(-1),
            ),
            (
                "play",
                QStyle.StandardPixmap.SP_MediaPlay,
                "Play / Pause",
                self.controller.toggle_play,
            ),
            (
                "next",
                QStyle.StandardPixmap.SP_MediaSeekForward,
                "Next frame",
                lambda: self.controller.step(1),
            ),
            (
                "last",
                QStyle.StandardPixmap.SP_MediaSkipForward,
                "Last frame",
                lambda: self._goto(-1),
            ),
        ):
            button = QToolButton()
            button.setIcon(style.standardIcon(icon))
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.buttons[name] = button
        row.addStretch(1)
        box.addLayout(row)

        self.slider = Slider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self._slider_moved)
        self.slider.sliderReleased.connect(self._slider_released)
        box.addWidget(self.slider)

        frame_row = QHBoxLayout()
        frame_row.addWidget(QLabel("Frame:"))
        self.frame_spin = SpinBox()
        self.frame_spin.setMinimum(1)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.valueChanged.connect(self._frame_typed)
        frame_row.addWidget(self.frame_spin)
        self.frame_total = QLabel("/ 0")
        frame_row.addWidget(self.frame_total)
        frame_row.addStretch(1)
        box.addLayout(frame_row)

        form = QFormLayout()
        form.addRow("Frames per second:", self._spin("fps", 1, 120))
        form.addRow("Step (frames):", self._spin("stride", 1, 100000))
        form.addRow(self._check("loop", "Loop"))
        box.addLayout(form)
        box.addWidget(self._reset_button("frames"), 0, Qt.AlignmentFlag.AlignRight)
        return group

    def _display_group(self):
        group = QGroupBox("Appearance")
        form = QFormLayout(group)
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
        self._add_reset_row(form, "appearance")
        return group

    def _ribbon_group(self):
        group = QGroupBox("Ribbon")
        form = QFormLayout(group)
        form.addRow(
            "Helix/sheet width:", self._dspin("ribbon_helix_width", 0.05, 10.0, 0.1)
        )
        form.addRow("Coil width:", self._dspin("ribbon_coil_width", 0.05, 10.0, 0.05))
        form.addRow("Smoothness:", self._spin("ribbon_subdivide", 1, 100))
        self._add_reset_row(form, "ribbon")
        return group

    def _box_group(self):
        group = QGroupBox("Periodic Box")
        form = QFormLayout(group)
        form.addRow(self._check("show_box", "Show box"))
        form.addRow("Colour:", self._color("box_color"))
        form.addRow("Line width:", self._dspin("box_line_width", 0.5, 20.0, 0.5))
        self._add_reset_row(form, "box")
        return group

    def _switch_group(self):
        group = QGroupBox("Switching")
        form = QFormLayout(group)
        combo = ComboBox()
        for key, label in settings.LARGE_ACTIONS:
            combo.addItem(label, key)
        combo.currentIndexChanged.connect(
            lambda _i: self._set("large_action", combo.currentData())
        )
        self._bound["large_action"] = (
            combo,
            lambda v: combo.setCurrentIndex(max(0, combo.findData(v))),
        )
        form.addRow("Large structures:", combo)
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
        self._add_reset_row(form, "switching")
        return group

    # ---------------------------------------------------------- widgets

    def _spin(self, key, lo, hi):
        w = SpinBox()
        w.setRange(lo, hi)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda v: self._set(key, v))
        self._bound[key] = (w, lambda v: w.setValue(int(v)))
        return w

    def _dspin(self, key, lo, hi, step):
        w = DoubleSpinBox()
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

    def _color(self, key):
        w = QPushButton()
        w.setFixedWidth(80)

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

    # ---------------------------------------------------------- actions

    def _set(self, key, value):
        if self._syncing:
            return
        try:
            self.controller.set_value(key, value)
        except Exception:
            logger.exception("Large System Viewer: setting %s failed", key)
        if key == "inherit_host_appearance":
            self._update_enabled()

    def _reset_button(self, group):
        button = QPushButton("Reset")
        button.setToolTip("Restore the defaults of this group")
        button.clicked.connect(lambda: self._reset_group(group))
        return button

    def _add_reset_row(self, form, group):
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._reset_button(group))
        form.addRow(row)

    def _reset_group(self, group):
        self.controller.reset_keys(GROUP_KEYS[group])

    def _reset(self):
        self.controller.reset_settings()

    def _toggle_fast(self):
        c = self.controller
        if c.fast_active():
            c.restore_style()
        else:
            c.switch_to_fast(auto=False)
        self.sync()

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
        if self.slider.isSliderDown():
            c.pause(commit=False)
            c.show_frame(value, commit=False)
        else:
            c.pause(commit=False)
            c.show_frame(value, commit=True)

    def _slider_released(self):
        if self._syncing:
            return
        self.controller.show_frame(self.slider.value(), commit=True)

    def _frame_typed(self, value):
        if self._syncing:
            return
        self.controller.pause(commit=False)
        self.controller.show_frame(value - 1, commit=True)

    # ------------------------------------------------------------- sync

    def _update_enabled(self):
        inherit = bool(self.controller.values.get("inherit_host_appearance"))
        for key in _INHERITED:
            widget = self._bound.get(key, (None,))[0]
            if widget is not None:
                widget.setEnabled(not inherit)
                widget.setToolTip(
                    "Taken from MoleditPy's 3D settings" if inherit else ""
                )

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
            self.fast_button.setText("Use Previous Style" if fast else "Use Fast Style")
            self.fast_button.setEnabled(
                not (fast and s is not None and s.mode == MODE_VIEW_ONLY)
            )
            if s is None:
                self.info.setText("No structure")
                n_frames = 0
            else:
                mode = {
                    MODE_VIEW_ONLY: "view-only",
                    MODE_CONFORMERS: "conformers",
                }.get(s.mode, "linked to MoleditPy")
                box = s.box()
                cell = "" if box is None else "\nPeriodic box: yes"
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

            can_play = s is not None and s.can_play
            for button in self.buttons.values():
                button.setEnabled(can_play)
            self.slider.setEnabled(can_play)
            self.frame_spin.setEnabled(can_play)
            self.slider.setMaximum(max(0, n_frames - 1))
            self.frame_spin.setMaximum(max(1, n_frames))
            index = s.index if s is not None else 0
            if not self.slider.isSliderDown():
                self.slider.setValue(index)
            self.frame_spin.setValue(index + 1)
            self.frame_total.setText(f"/ {n_frames:,}")
            icon = (
                QStyle.StandardPixmap.SP_MediaPause
                if c.playing
                else QStyle.StandardPixmap.SP_MediaPlay
            )
            self.buttons["play"].setIcon(self.style().standardIcon(icon))
        finally:
            self._syncing = False
