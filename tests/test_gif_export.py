"""GIF export: encoding, the controller loop and the Save GIF buttons."""

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

from large_system_viewer import controller as ctl
from large_system_viewer import gif_export, host, settings


def _shot(value, size=(8, 6), alpha=255):
    img = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    img[..., 0] = value
    img[..., 3] = alpha
    img[0, 0, 3] = 0  # one background pixel
    return img


@pytest.mark.parametrize("transparent", [True, False])
@pytest.mark.parametrize("use_hq", [True, False])
def test_encode_writes_every_frame(tmp_path, transparent, use_hq):
    frames = [
        gif_export.convert_frame(Image.fromarray(_shot(v)), transparent, use_hq)
        for v in (0, 120, 240)
    ]
    path = tmp_path / "a.gif"
    gif_export.save_gif(frames, str(path), fps=10)
    with Image.open(path) as gif:
        assert gif.n_frames == 3
        assert gif.info.get("loop") == 0
        assert gif.info.get("duration") == 100
        if transparent and use_hq:
            index = gif.info.get("transparency")
            assert index is not None
            assert gif.getpixel((0, 0)) == index
            assert gif.getpixel((1, 1)) != index


def test_hq_uses_an_adaptive_palette():
    frame = gif_export.convert_frame(Image.fromarray(_shot(77)), False, True)
    assert frame.mode == "P"


def test_export_indices_follow_the_step():
    assert gif_export.export_indices(5, 1) == [0, 1, 2, 3, 4]
    assert gif_export.export_indices(5, 2) == [0, 2, 4]
    assert gif_export.export_indices(5, 0) == [0, 1, 2, 3, 4]


def test_save_gif_refuses_nothing(tmp_path):
    with pytest.raises(ValueError):
        gif_export.save_gif([], str(tmp_path / "x.gif"), 10)


class FakePlotter:
    def __init__(self, c):
        self.c = c
        self.shots = []

    def render(self):
        pass

    def screenshot(self, transparent_background, return_img):
        self.shots.append((self.c.session.index, transparent_background))
        return _shot(self.c.session.index * 40)


@pytest.fixture
def ctrl(monkeypatch):
    import sys
    import types

    try:
        import large_system_viewer.panel  # noqa: F401
    except ImportError:
        stub = types.ModuleType("large_system_viewer.panel")
        stub.sync_toolbar = lambda c: None
        monkeypatch.setitem(sys.modules, "large_system_viewer.panel", stub)
    context = SimpleNamespace(
        get_main_window=lambda: object(), show_status_message=lambda *a: None
    )
    c = ctl.Controller(context, values=dict(settings.DEFAULTS))
    frames = [np.full((2, 3), float(i), dtype=np.float32) for i in range(5)]
    c.session = SimpleNamespace(
        n_frames=5, index=2, can_play=True, mode="view_only", detached=False
    )
    shown = []

    def show_frame(index, commit):
        c.session.index = index
        shown.append((index, commit))

    monkeypatch.setattr(c, "show_frame", show_frame)
    plotter = FakePlotter(c)
    monkeypatch.setattr(host, "plotter", lambda _mw: plotter)
    return c, plotter, shown, frames


def test_controller_captures_every_step_and_restores(ctrl, tmp_path):
    c, plotter, shown, _ = ctrl
    path = tmp_path / "t.gif"
    seen = []
    ok = c.export_gif(
        str(path), 12, 2, True, True, lambda d, t: seen.append((d, t)) or True
    )
    assert ok
    assert [i for i, _t in plotter.shots] == [0, 2, 4]
    assert all(t for _i, t in plotter.shots)
    assert shown[-1] == (2, True)  # back on the frame shown before
    assert seen[-1] == (3, 3)
    with Image.open(path) as gif:
        assert gif.n_frames == 3


def test_controller_cancel_writes_nothing(ctrl, tmp_path):
    c, plotter, shown, _ = ctrl
    path = tmp_path / "c.gif"
    ok = c.export_gif(str(path), 10, 1, False, False, lambda d, t: d < 2)
    assert ok is False
    assert not path.exists()
    assert len(plotter.shots) == 2
    assert shown[-1] == (2, True)


def test_controller_needs_frames(ctrl, tmp_path):
    c, _plotter, _shown, _ = ctrl
    c.session.can_play = False
    with pytest.raises(RuntimeError):
        c.export_gif(str(tmp_path / "n.gif"), 10, 1, False, False)
