"""Controller decisions with the host replaced by fakes."""

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from large_system_viewer import controller as ctl  # noqa: E402
from large_system_viewer import formats, host, settings  # noqa: E402


class FakeMol:
    def __init__(self, n):
        self.n = n

    def GetNumAtoms(self):  # noqa: N802 - RDKit API
        return self.n


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls = {"style": [], "native": [], "session": [], "asked": 0}
    state = {"style": "ball_and_stick"}

    def set_style(_mw, name):
        calls["style"].append(name)
        state["style"] = name

    monkeypatch.setattr(host, "current_style", lambda _mw: state["style"])
    monkeypatch.setattr(host, "set_style", set_style)
    monkeypatch.setattr(
        host, "native_open", lambda _mw, p: calls["native"].append(p) or True
    )
    monkeypatch.setattr(host, "check_unsaved", lambda _mw: True)
    monkeypatch.setattr(host, "is_headless", lambda: False)
    monkeypatch.setattr(settings, "save", lambda values, path=None: None)

    context = SimpleNamespace(
        get_main_window=lambda: object(), show_status_message=lambda *a: None
    )
    c = ctl.Controller(context, values=dict(settings.DEFAULTS, switch_threshold=10))
    monkeypatch.setattr(c, "show_panel", lambda: None)

    def fake_session(path, use_fast, declined):
        calls["session"].append((path, use_fast, declined))
        return True

    monkeypatch.setattr(c, "_open_session", fake_session)
    return c, calls, state, tmp_path


def _answer(monkeypatch, calls, answer, remember=False):
    import large_system_viewer.panel as panel

    def ask(_mw, _n, _t):
        calls["asked"] += 1
        return answer, remember

    monkeypatch.setattr(panel, "ask_switch", ask, raising=False)


@pytest.fixture(autouse=True)
def _panel_stub(monkeypatch):
    """panel.py needs PySide6; give the controller an importable stand-in when absent."""
    import sys
    import types

    try:
        import large_system_viewer.panel  # noqa: F401
    except ImportError:
        stub = types.ModuleType("large_system_viewer.panel")
        stub.ask_switch = lambda *a: (False, False)
        stub.show_panel = lambda c: None
        monkeypatch.setitem(sys.modules, "large_system_viewer.panel", stub)


def test_small_molecule_never_asks(env, monkeypatch):
    c, calls, _state, _ = env
    _answer(monkeypatch, calls, True)
    c.on_new_molecule(FakeMol(5))
    assert calls["asked"] == 0 and calls["style"] == []


def test_large_molecule_yes_switches(env, monkeypatch):
    c, calls, state, _ = env
    _answer(monkeypatch, calls, True)
    c.on_new_molecule(FakeMol(50))
    assert calls["asked"] == 1
    assert state["style"] == ctl.STYLE_NAME


def test_large_molecule_no_keeps_style_until_reset(env, monkeypatch):
    c, calls, state, _ = env
    _answer(monkeypatch, calls, False)
    c.on_new_molecule(FakeMol(50))
    c.on_new_molecule(FakeMol(60))
    assert calls["asked"] == 1  # declined for this document
    assert state["style"] == "ball_and_stick"
    c.document_reset()
    c.on_new_molecule(FakeMol(70))
    assert calls["asked"] == 2


def test_remembered_answer_updates_setting(env, monkeypatch):
    c, calls, _state, _ = env
    _answer(monkeypatch, calls, True, remember=True)
    c.on_new_molecule(FakeMol(50))
    assert c.values["large_action"] == "switch"


def test_action_switch_and_keep_do_not_ask(env, monkeypatch):
    c, calls, state, _ = env
    _answer(monkeypatch, calls, False)
    c.values["large_action"] = "keep"
    c.on_new_molecule(FakeMol(50))
    assert state["style"] == "ball_and_stick"
    c.document_reset()
    c.values["large_action"] = "switch"
    c.on_new_molecule(FakeMol(50))
    assert state["style"] == ctl.STYLE_NAME
    assert calls["asked"] == 0


def test_auto_switch_restores_previous_style(env, monkeypatch):
    c, calls, state, _ = env
    state["style"] = "cpk"
    _answer(monkeypatch, calls, True)
    c.on_new_molecule(FakeMol(50))
    assert state["style"] == ctl.STYLE_NAME
    c.on_new_molecule(FakeMol(3))
    assert state["style"] == "cpk"


def test_open_small_xyz_uses_host_loader(env):
    c, calls, _state, tmp = env
    path = tmp / "w.xyz"
    path.write_text("3\nw\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n", encoding="utf-8")
    assert c.open_file(str(path)) is True
    assert calls["native"] == [str(path)]
    assert c.drop_file(str(path)) is False  # keep the host's own drop behaviour
    assert calls["session"] == []


def test_open_trajectory_and_large_file(env, monkeypatch):
    c, calls, _state, tmp = env
    traj = tmp / "t.xyz"
    traj.write_text("3\nw\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n" * 2, encoding="utf-8")
    assert c.open_file(str(traj)) is True
    assert calls["session"][-1] == (str(traj), False, False)

    big = tmp / "big.xyz"
    big.write_text(
        "12\nw\n" + "O 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n" * 4, encoding="utf-8"
    )
    _answer(monkeypatch, calls, True)
    assert c.open_file(str(big)) is True
    assert calls["session"][-1] == (str(big), True, False)


def test_declined_large_single_xyz_goes_to_host_without_second_question(
    env, monkeypatch
):
    c, calls, _state, tmp = env
    big = tmp / "big.xyz"
    big.write_text(
        "12\nw\n" + "O 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n" * 4, encoding="utf-8"
    )
    _answer(monkeypatch, calls, False)
    monkeypatch.setattr(c, "_clear_skip_later", lambda: None)
    assert c.open_file(str(big)) is True
    assert calls["native"] == [str(big)]
    c.on_new_molecule(FakeMol(12))  # the host's loader delivers the molecule
    assert calls["asked"] == 1


def test_unknown_extension_declined(env):
    c, _calls, _state, _ = env
    assert c.open_file("x.cif") is False


def test_export_guard(env, monkeypatch):
    c, _calls, state, _ = env
    errors = []
    monkeypatch.setattr(c, "_error", errors.append)
    assert c.export_blocked() is False  # not in the Fast style
    state["style"] = ctl.STYLE_NAME
    c.scene = object()
    c.drawn_representation = "ribbon"
    assert c.export_blocked() is False  # ribbons are real meshes
    c.drawn_representation = "ball_and_stick"
    assert c.export_blocked() is True and errors


def test_set_value_coerces_and_saves(env, monkeypatch):
    c, _calls, _state, _ = env
    refreshed = []
    monkeypatch.setattr(c, "refresh", lambda: refreshed.append(1))
    c.set_value("fps", "500")
    assert c.values["fps"] == 120 and not refreshed
    c.set_value("show_box", False)
    assert refreshed == [1]
    c.set_value("not_a_key", 1)
    assert "not_a_key" not in c.values


def test_state_without_file_session(env):
    c, _calls, _state, _ = env
    assert c.state() == {}
    c.session = SimpleNamespace(path="/x/a.gro", index=3)
    assert c.state() == {
        "path": "/x/a.gro",
        "frame": 3,
        "representation": "ball_and_stick",
    }


def test_formats_peek_used_by_routing(tmp_path):
    path = tmp_path / "a.xyz"
    path.write_text("1\nx\nHe 0 0 0\n", encoding="utf-8")
    assert formats.peek(str(path)).n_atoms == 1
