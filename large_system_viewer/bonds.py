"""Distance-based bond perception on a uniform cell grid (numpy only)."""

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a host dependency
    np = None

from . import elements

TOLERANCE = 0.4  # Angstrom added to the sum of covalent radii
MIN_DISTANCE = 0.4  # closer pairs are overlapping atoms, not bonds

_HALF_SHELL = [
    (dx, dy, dz)
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    for dz in (-1, 0, 1)
    if (dx, dy, dz) > (0, 0, 0)
]


def _ranges(lo, counts):
    """Concatenate arange(lo[k], lo[k] + counts[k]) for every k without a Python loop."""
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    ends = np.cumsum(counts)
    starts = ends - counts
    return np.repeat(lo - starts, counts) + np.arange(total, dtype=np.int64)


def perceive_bonds(positions, numbers, tolerance=TOLERANCE):
    """Bonds [M, 2] (int32, i < j) for atoms closer than r_i + r_j + tolerance.

    Free ions and noble gases are never bonded, and each hydrogen keeps only its
    nearest partner.
    """
    pos = np.asarray(positions, dtype=np.float64)
    nums = np.asarray(numbers, dtype=np.int64)
    n = len(pos)
    if n < 2:
        return np.empty((0, 2), dtype=np.int32)

    radii_table = np.asarray(elements.COVALENT_RADII)
    radii = radii_table[np.clip(nums, 0, len(radii_table) - 1)]
    bondable = ~np.isin(nums, list(elements.NON_BONDING)) & (nums > 0)
    finite = np.isfinite(pos).all(axis=1)
    bondable &= finite
    if not bondable.any():
        return np.empty((0, 2), dtype=np.int32)

    cell = 2.0 * float(radii[bondable].max()) + tolerance
    origin = pos[finite].min(axis=0)
    grid = np.zeros((n, 3), dtype=np.int64)
    grid[finite] = np.floor((pos[finite] - origin) / cell).astype(np.int64)
    dims = grid.max(axis=0) + 1

    def key_of(g):
        return (g[:, 0] * dims[1] + g[:, 1]) * dims[2] + g[:, 2]

    atoms = np.flatnonzero(bondable)
    keys = key_of(grid[atoms])
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    sorted_atoms = atoms[order]

    pairs_i = []
    pairs_j = []
    for offset in [(0, 0, 0)] + _HALF_SHELL:
        nb = grid[atoms] + np.asarray(offset)
        inside = ((nb >= 0) & (nb < dims)).all(axis=1)
        src = atoms[inside]
        nk = key_of(nb[inside])
        lo = np.searchsorted(sorted_keys, nk, side="left")
        hi = np.searchsorted(sorted_keys, nk, side="right")
        counts = hi - lo
        ii = np.repeat(src, counts)
        jj = sorted_atoms[_ranges(lo, counts)]
        if offset == (0, 0, 0):
            keep = ii < jj
            ii, jj = ii[keep], jj[keep]
        if len(ii) == 0:
            continue
        d2 = ((pos[ii] - pos[jj]) ** 2).sum(axis=1)
        limit = radii[ii] + radii[jj] + tolerance
        keep = (d2 < limit * limit) & (d2 > MIN_DISTANCE * MIN_DISTANCE)
        pairs_i.append(np.minimum(ii[keep], jj[keep]))
        pairs_j.append(np.maximum(ii[keep], jj[keep]))

    if not pairs_i:
        return np.empty((0, 2), dtype=np.int32)
    i = np.concatenate(pairs_i)
    j = np.concatenate(pairs_j)
    if len(i) == 0:
        return np.empty((0, 2), dtype=np.int32)

    is_h = nums == 1
    involves_h = is_h[i] | is_h[j]
    if involves_h.any():
        d = np.sqrt(((pos[i] - pos[j]) ** 2).sum(axis=1))
        best = np.full(n, np.inf)
        np.minimum.at(best, i[is_h[i]], d[is_h[i]])
        np.minimum.at(best, j[is_h[j]], d[is_h[j]])
        ok_i = ~is_h[i] | (d <= best[i])
        ok_j = ~is_h[j] | (d <= best[j])
        keep = ok_i & ok_j
        i, j = i[keep], j[keep]

    out = np.stack([i, j], axis=1).astype(np.int32)
    out = np.unique(out, axis=0)
    return out


def merge_bonds(*bond_sets):
    """Union of bond arrays, normalized to i < j and deduplicated."""
    arrays = [
        np.asarray(b, dtype=np.int64).reshape(-1, 2) for b in bond_sets if b is not None
    ]
    if not arrays:
        return np.empty((0, 2), dtype=np.int32)
    allb = np.concatenate(arrays)
    if len(allb) == 0:
        return np.empty((0, 2), dtype=np.int32)
    allb = np.sort(allb, axis=1)
    allb = allb[allb[:, 0] != allb[:, 1]]
    return np.unique(allb, axis=0).astype(np.int32)
