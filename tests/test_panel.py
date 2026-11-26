"""Panel widgets: the wheel needs focus, and the reset buttons restore defaults."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("numpy")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from large_system_viewer import panel, settings


def _wheel(widget, up=True):
    delta = QPoint(0, 120 if up else -120)
    event = QWheelEvent(
        QPointF(5, 5),
        QPointF(widget.mapToGlobal(QPoint(5, 5))),
        QPoint(0, 0),
        delta,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)
    return event


@pytest.mark.parametrize("make", [panel.SpinBox, panel.DoubleSpinBox])
def test_spin_boxes_ignore_wheel_without_focus(qapp, make):
    w = make()
    w.setRange(0, 100)
    w.setValue(10)
    w.show()
    event = _wheel(w)
    assert w.value() == 10
    assert not event.isAccepted()  # goes on to the scroll area
    w.setFocus()
    qapp.processEvents()
    if w.hasFocus():  # offscreen platforms may refuse focus
        _wheel(w)
        assert w.value() != 10
    w.close()


def test_combo_and_slider_ignore_wheel_without_focus(qapp):
    combo = panel.ComboBox()
    combo.addItems(["a", "b", "c"])
    combo.show()
    _wheel(combo, up=False)
    assert combo.currentIndex() == 0
    slider = panel.Slider(Qt.Orientation.Horizontal)
    slider.setRange(0, 10)
    slider.show()
    _wheel(slider)
    assert slider.value() == 0
    assert combo.focusPolicy() == Qt.FocusPolicy.StrongFocus
    combo.close()
    slider.close()


def test_group_keys_cover_every_setting():
    covered = {k for keys in panel.GROUP_KEYS.values() for k in keys}
    assert covered == set(settings.DEFAULTS)


def test_reset_keys_restores_only_the_group(monkeypatch):
    from large_system_viewer import controller as ctl

    monkeypatch.setattr(settings, "save", lambda values, path=None: None)
    context = SimpleNamespace(
        get_main_window=lambda: object(), show_status_message=lambda *a: None
    )
    c = ctl.Controller(context, values=dict(settings.DEFAULTS))
    refreshed = []
    monkeypatch.setattr(c, "refresh", lambda: refreshed.append(1))
    c.values.update(fps=30, show_box=False, box_line_width=5.0)
    c.reset_keys(panel.GROUP_KEYS["box"])
    assert c.values["show_box"] is True and c.values["box_line_width"] == 2.0
    assert c.values["fps"] == 30
    assert refreshed == [1]
    c.reset_settings()
    assert c.values == settings.DEFAULTS
