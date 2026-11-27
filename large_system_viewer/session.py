"""The displayed system and its frames."""

import logging
import os
from collections import OrderedDict

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a host dependency
    np = None

MODE_HYBRID = "hybrid"  # the host molecule is the committed frame
MODE_VIEW_ONLY = "view_only"  # too large for the host; drawn by the plugin alone
MODE_CONFORMERS = "conformers"  # an existing host molecule; conformers are the frames

logger = logging.getLogger(__name__)

_CACHE_FRAMES = 8


class Session:
    def __init__(self, system, frames, mode, path=None, mol=None):
        self.system = system
        self.frames = frames
        self.mode = mode
        self.path = path
        self.mol = mol  # host molecule (None in view-only mode)
        self.index = 0
        self.committed_index = 0
        self.detached = False
        self._cache = OrderedDict()

    @property
    def n_frames(self):
        return self.frames.n_frames

    @property
    def n_atoms(self):
        return self.system.n_atoms

    @property
    def title(self):
        if self.path:
            return os.path.basename(self.path)
        return "current molecule"

    @property
    def can_play(self):
        return self.n_frames > 1 and not self.detached

    def frame(self, index):
        """(positions float32 [N, 3], box or None) for a frame, cached."""
        index = int(index) % max(self.n_frames, 1)
        hit = self._cache.get(index)
        if hit is not None:
            self._cache.move_to_end(index)
            return hit
        value = self.frames.read_frame(index)
        self._cache[index] = value
        if len(self._cache) > _CACHE_FRAMES:
            self._cache.popitem(last=False)
        return value

    def frame_title(self, index=None):
        """The frame's own title from the file (XYZ comment, SDF name), or ""."""
        try:
            return self.frames.title(self.index if index is None else index)
        except Exception:
            logger.debug("reading the frame title failed", exc_info=True)
            return ""

    def positions(self, index=None):
        return self.frame(self.index if index is None else index)[0]

    def box(self, index=None):
        return self.frame(self.index if index is None else index)[1]

    def host_matches_commit(self, mol):
        """False when the host changed the coordinates since the last commit (3D edit)."""
        if mol is None or not mol.GetNumConformers():
            return True
        committed = self.positions(self.committed_index)
        current = mol.GetConformer().GetPositions()
        if current.shape != committed.shape:
            return False
        return bool(np.allclose(current, committed, atol=1e-3))

    def close(self):
        try:
            self.frames.close()
        except Exception:  # pragma: no cover - best effort
            logger.debug("closing frames failed", exc_info=True)
        self._cache.clear()
