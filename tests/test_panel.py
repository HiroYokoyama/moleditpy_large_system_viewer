"""Settings panel, toolbar, wheel guard, reset buttons and ribbon colours."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")
np = pytest.importorskip("numpy")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QMainWindow,
    QSlider,
    QSpinBox,
)

from large_system_viewer import panel, render, settings


def _wheel(widget, up=True):
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(widget.mapToGlobal(QPoint(5, 5))),
        QPoint(0, 0),
        QPoint(0, 120 if up else -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)
    return event


class FakeController:
    def __init__(self, mw):
        self.values = dict(settings.DEFAULTS)
        self._mw = mw
        self.session = None
        self.playing = False
        self.fallback_reason = ""
        self.panel = None
        self.panel_container = None
        self.toolbar = None
        self.fast = False
        self.calls = []

    @property
    def mw(self):
        return self._mw

    def fast_active(self):
        return self.fast

    def set_value(self, key, value):
        self.values[key] = settings.coerce(key, value)
        self.calls.append((key, self.values[key]))

    def reset_keys(self, keys):
        for key in keys:
            self.values[key] = settings.DEFAULTS[key]
        self.calls.append(("reset", tuple(keys)))

    def reset_settings(self):
        self.reset_keys(tuple(settings.DEFAULTS))

    def toggle_play(self):
        pass

    def step(self, delta):
        pass

    def pause(self, commit=True):
        pass

    def show_frame(self, index, commit):
        pass

    def show_panel(self):
        panel.show_panel(self)

    def _sync_panel(self):
        pass


@pytest.fixture
def controller(qapp):
    mw = QMainWindow()
    c = FakeController(mw)
    yield c
    panel.close_panel(c)
    mw.deleteLater()


def test_value_fields_ignore_wheel_without_focus(controller):
    panel.show_panel(controller)
    p = controller.panel
    fields = (
        p.findChildren(QSpinBox)
        + p.findChildren(QDoubleSpinBox)
        + p.findChildren(QComboBox)
    )
    assert len(fields) > 15
    for field in fields:
        assert field.focusPolicy() == Qt.FocusPolicy.StrongFocus
    spin = next(w for w in p.findChildren(QDoubleSpinBox) if w.isEnabled() or True)
    before = spin.value()
    event = _wheel(spin)
    assert spin.value() == before
    assert not event.isAccepted()  # passed on to the scroll area
    combo = p.findChildren(QComboBox)[0]
    index = combo.currentIndex()
    _wheel(combo, up=False)
    assert combo.currentIndex() == index


def test_settings_are_tabs_in_a_window_or_dock(controller):
    window = panel.show_panel(controller)
    assert window.mode == "window" and window.isWindow()
    tabs = controller.panel.tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "Structure",
        "Appearance",
        "Ribbon",
        "Box",
        "Behaviour",
    ]
    controller.values["panel_mode"] = "dock"
    dock = panel.show_panel(controller)
    assert dock.mode == "dock" and not dock.isWindow()
    assert controller.panel_container is dock
    assert dock.widget() is controller.panel


def test_settings_widgets_write_values(controller):
    panel.show_panel(controller)
    p = controller.panel
    mode_combo = p._bound["ribbon_color_mode"][0]
    mode_combo.setCurrentIndex(mode_combo.findData("chain"))
    assert controller.values["ribbon_color_mode"] == "chain"
    assert not p._bound["ribbon_helix_color"][0].isEnabled()
    p._bound["auto_open_panel"][0].setChecked(False)
    assert controller.values["auto_open_panel"] is False


def test_group_keys_cover_every_setting():
    covered = [k for keys in panel.GROUP_KEYS.values() for k in keys]
    assert len(covered) == len(set(covered))
    assert set(covered) == set(settings.DEFAULTS)


def test_reset_button_restores_its_tab(controller):
    panel.show_panel(controller)
    controller.values.update(show_box=False, fps=33)
    controller.panel._reset_group("box")
    assert controller.values["show_box"] is True
    assert controller.values["fps"] == 33


def test_toolbar_follows_session_and_setting(controller):
    panel.sync_toolbar(controller)
    assert controller.toolbar is None  # nothing to show yet
    controller.fast = True
    panel.sync_toolbar(controller)
    bar = controller.toolbar
    assert bar is not None and not bar.isHidden()
    assert controller.mw.toolBarArea(bar) == Qt.ToolBarArea.BottomToolBarArea
    assert bar.findChild(QSlider).focusPolicy() == Qt.FocusPolicy.StrongFocus
    controller.values["show_toolbar"] = False
    panel.sync_toolbar(controller)
    assert bar.isHidden()


def test_reset_keys_restores_only_the_group(monkeypatch):
    from large_system_viewer import controller as ctl

    monkeypatch.setattr(settings, "save", lambda values, path=None: None)
    context = SimpleNamespace(
        get_main_window=lambda: object(), show_status_message=lambda *a: None
    )
    c = ctl.Controller(context, values=dict(settings.DEFAULTS))
    refreshed = []
    monkeypatch.setattr(c, "refresh", lambda: refreshed.append(1))
    monkeypatch.setattr(c, "_sync_panel", lambda: None)
    c.values.update(fps=30, show_box=False, box_line_width=5.0)
    c.reset_keys(panel.GROUP_KEYS["box"])
    assert c.values["show_box"] is True and c.values["box_line_width"] == 2.0
    assert c.values["fps"] == 30
    assert refreshed == [1]
    c.reset_settings()
    assert c.values == settings.DEFAULTS


def test_auto_open_follows_the_setting(monkeypatch):
    from large_system_viewer import controller as ctl

    shown = []
    monkeypatch.setattr(panel, "show_panel", lambda c: shown.append(c))
    monkeypatch.setattr(panel, "panel_visible", lambda c: False)
    context = SimpleNamespace(
        get_main_window=lambda: object(), show_status_message=lambda *a: None
    )
    c = ctl.Controller(context, values=dict(settings.DEFAULTS))
    monkeypatch.setattr(c, "_sync_panel", lambda: None)
    c.show_panel(auto=True)
    assert len(shown) == 1
    c.values["auto_open_panel"] = False
    c.show_panel(auto=True)
    assert len(shown) == 1
    c.show_panel()  # from the menu or the toolbar
    assert len(shown) == 2


def test_new_settings_are_validated():
    assert settings.coerce("ribbon_color_mode", "rainbow") == "secondary"
    assert settings.coerce("panel_mode", "dock") == "dock"
    assert settings.coerce("panel_mode", "floating") == "window"
    assert settings.coerce("ribbon_coil_color", "red") == "#DDDDDD"


def test_ribbon_rgb_modes():
    options = dict(
        settings.DEFAULTS,
        ribbon_helix_color="#00FF00",
        ribbon_sheet_color="#0000FF",
        ribbon_coil_color="#101010",
        ribbon_uniform_color="#FF0000",
    )
    vtk_rgb = np.array([[255, 0, 128], [255, 200, 0], [221, 221, 221]], np.uint8)
    colors = render.ribbon_color_options(options)
    assert render.ribbon_rgb(vtk_rgb, colors, 0).tolist() == [
        [0, 255, 0],
        [0, 0, 255],
        [16, 16, 16],
    ]
    colors = render.ribbon_color_options(dict(options, ribbon_color_mode="uniform"))
    assert render.ribbon_rgb(vtk_rgb, colors, 3).tolist() == [[255, 0, 0]] * 3
    colors = render.ribbon_color_options(dict(options, ribbon_color_mode="chain"))
    first = render.ribbon_rgb(vtk_rgb, colors, 0)
    second = render.ribbon_rgb(vtk_rgb, colors, 1)
    assert (first == first[0]).all() and (second == second[0]).all()
    assert first[0].tolist() != second[0].tolist()
