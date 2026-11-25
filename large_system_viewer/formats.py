"""Structure and trajectory readers: PDB, GRO, XYZ (multi-frame) and SDF.

Text trajectories are memory-mapped and indexed by frame start offset; a frame is
parsed only when it is shown.  Fixed-width coordinates (PDB, GRO) are converted with
numpy character arithmetic instead of a Python loop per line.
"""

import mmap
import os
import re
from collections import Counter
from dataclasses import dataclass

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a host dependency
    np = None

from . import elements
from .bonds import merge_bonds, perceive_bonds
from .system import SS_COIL, SS_HELIX, SS_SHEET, System

EXTENSIONS = (".pdb", ".ent", ".gro", ".xyz", ".sdf")
_CHUNK = 64 * 1024 * 1024
_MAX_CONECT_ATOMS = 99999


class FormatError(ValueError):
    """The file is not a structure this reader understands."""


def format_of(path):
    ext = os.path.splitext(path)[1].lower()
    return {".ent": "pdb"}.get(ext, ext.lstrip("."))


@dataclass
class Peek:
    n_atoms: int
    multi_frame: bool


# --------------------------------------------------------------------------- frames


class FrameSource:
    """Frames of one topology.

    read_frame(i) returns (float32 [N, 3] in Angstrom, box) where box is a 3x3 matrix
    whose rows are the cell vectors in Angstrom, or None when the file has no cell.
    """

    n_frames = 0
    n_atoms = 0
    default_box = None

    def read_frame(self, index):  # pragma: no cover - interface
        raise NotImplementedError

    def read(self, index):
        return self.read_frame(index)[0]

    @property
    def has_box(self):
        return self.default_box is not None

    def close(self):
        pass


class ArrayFrames(FrameSource):
    def __init__(self, frames, boxes=None, default_box=None):
        self.frames = np.ascontiguousarray(frames, dtype=np.float32)
        self.n_frames = int(self.frames.shape[0])
        self.n_atoms = int(self.frames.shape[1])
        self.boxes = boxes
        self.default_box = default_box
        if default_box is None and boxes:
            self.default_box = next((b for b in boxes if b is not None), None)

    def read_frame(self, index):
        box = self.boxes[index] if self.boxes else None
        return self.frames[index], box if box is not None else self.default_box


class _MappedFrames(FrameSource):
    def __init__(self, path, ranges, n_atoms, parse, default_box=None):
        self._file = open(path, "rb")  # noqa: SIM115 - kept open for the mmap
        self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self.ranges = ranges
        self.n_frames = len(ranges)
        self.n_atoms = n_atoms
        self._parse = parse
        self.default_box = default_box

    def read_frame(self, index):
        start, end = self.ranges[index]
        coords, box = self._parse(self._mm[start:end])
        if len(coords) != self.n_atoms:
            raise FormatError(
                f"frame {index + 1} has {len(coords)} atoms, expected {self.n_atoms}"
            )
        return coords.astype(np.float32), box if box is not None else self.default_box

    def close(self):
        try:
            self._mm.close()
        finally:
            self._file.close()


def _open_mmap(path):
    f = open(path, "rb")  # noqa: SIM115 - returned with the mmap
    try:
        if os.fstat(f.fileno()).st_size == 0:
            raise FormatError("the file is empty")
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    except Exception:
        f.close()
        raise
    return f, mm


# ------------------------------------------------------------------ fixed-width floats


def _line_starts(arr):
    nl = np.flatnonzero(arr == 10)
    starts = np.concatenate(([0], nl + 1))
    return starts[starts < len(arr)]


if np is not None:
    _DIGIT_VALUE = np.zeros(256, dtype=np.float64)
    _DIGIT_VALUE[48:58] = np.arange(10)
    _ALLOWED = np.zeros(256, dtype=bool)
    _ALLOWED[48:58] = True
    _ALLOWED[[32, 43, 45, 46]] = True  # space + - .


def parse_fixed(arr, starts, width, count):
    """Parse `count` fixed-width decimal fields beginning at each offset in `starts`."""
    m = len(starts)
    if m == 0:
        return np.empty((0, count), dtype=np.float64)
    span = width * count
    padded = np.concatenate([arr, np.full(span, 32, dtype=np.uint8)])
    chars = padded[np.asarray(starts)[:, None] + np.arange(span)]
    fields = chars.reshape(m * count, width)
    j = np.arange(width)

    dots = fields == 46
    first = int(dots[0].argmax())
    if dots[0, first] and dots[:, first].all() and int(dots.sum()) == len(fields):
        # Every field has its decimal point in the same column: one matrix product.
        weights = np.where(j < first, 10.0 ** (first - j - 1), 10.0 ** (first - j))
        weights[first] = 0.0
        values = _DIGIT_VALUE[fields] @ weights
    else:
        has_dot = dots.any(axis=1)
        dot_pos = np.where(has_dot, dots.argmax(axis=1), width)[:, None]
        exponent = np.where(j < dot_pos, dot_pos - j - 1, dot_pos - j)
        values = (_DIGIT_VALUE[fields] * np.power(10.0, exponent)).sum(axis=1)
    negative = (fields == 45).any(axis=1)
    values[negative] *= -1.0
    values = values.reshape(m, count)

    bad_rows = np.flatnonzero(~_ALLOWED[chars].all(axis=1))
    for r in bad_rows.tolist():
        for k in range(count):
            raw = bytes(chars[r, k * width : (k + 1) * width])
            if b"\n" in raw or b"\r" in raw:
                raise FormatError(
                    "a coordinate line is shorter than the fixed-width fields"
                )
            text = raw.decode("latin-1").strip()
            try:
                values[r, k] = float(text)
            except ValueError as exc:
                raise FormatError(f"cannot read coordinate {text!r}") from exc
    return values


# ----------------------------------------------------------------------------- cell


def cell_from_parameters(a, b, c, alpha, beta, gamma):
    """Cell vectors (rows) from lengths and angles in degrees; a along x, b in xy."""
    al, be, ga = np.radians([alpha, beta, gamma])
    va = np.array([a, 0.0, 0.0])
    vb = np.array([b * np.cos(ga), b * np.sin(ga), 0.0])
    cx = c * np.cos(be)
    cy = c * (np.cos(al) - np.cos(be) * np.cos(ga)) / np.sin(ga)
    cz = np.sqrt(max(c * c - cx * cx - cy * cy, 0.0))
    return np.array([va, vb, [cx, cy, cz]])


def _valid_cell(box):
    if box is None:
        return None
    box = np.asarray(box, dtype=np.float64)
    if box.shape != (3, 3) or not np.isfinite(box).all():
        return None
    if abs(np.linalg.det(box)) < 1e-6:
        return None
    return box


# ------------------------------------------------------------------------------ PDB

_ATOM = np.frombuffer(b"ATOM", dtype=np.uint8) if np is not None else None
_HETATM = np.frombuffer(b"HETATM", dtype=np.uint8) if np is not None else None
_MODEL_RE = re.compile(rb"^MODEL", re.MULTILINE)
_END_RE = re.compile(rb"^END[ \t\r]*$", re.MULTILINE)
_ATOMLINE_RE = re.compile(rb"^(?:ATOM  |HETATM)", re.MULTILINE)
_CONECT_RE = re.compile(rb"^CONECT.*$", re.MULTILINE)
_SS_RE = re.compile(rb"^(?:HELIX |SHEET ).*$", re.MULTILINE)
_CRYST1_RE = re.compile(rb"^CRYST1.*$", re.MULTILINE)


def _pdb_cell(data):
    match = _CRYST1_RE.search(data)
    if not match:
        return None
    text = match.group(0).decode("latin-1")
    try:
        params = [
            float(text[6:15]),
            float(text[15:24]),
            float(text[24:33]),
            float(text[33:40]),
            float(text[40:47]),
            float(text[47:54]),
        ]
    except ValueError:
        return None
    # 1 x 1 x 1 cells are placeholders written for structures without a lattice.
    if params[0] <= 1.0 and params[1] <= 1.0 and params[2] <= 1.0:
        return None
    return _valid_cell(cell_from_parameters(*params))


def _pdb_atom_starts(arr):
    starts = _line_starts(arr)
    padded = np.concatenate([arr, np.zeros(8, dtype=np.uint8)])
    head = padded[starts[:, None] + np.arange(6)]
    is_atom = (head[:, :4] == _ATOM).all(axis=1)
    is_het = (head == _HETATM).all(axis=1)
    keep = is_atom | is_het
    return starts[keep]


def parse_pdb_coords(block):
    arr = np.frombuffer(block, dtype=np.uint8)
    return parse_fixed(arr, _pdb_atom_starts(arr) + 30, 8, 3)


def parse_pdb_frame(block):
    return parse_pdb_coords(block), _pdb_cell(block)


def _pdb_frame_ranges(mm):
    size = len(mm)
    models = [m.start() for m in _MODEL_RE.finditer(mm)]
    if models:
        ranges = list(zip(models, models[1:] + [size]))
    else:
        ends = [m.end() for m in _END_RE.finditer(mm)]
        if len(ends) > 1:
            ranges = list(zip([0] + ends[:-1], ends))
        else:
            ranges = [(0, size)]
    return [r for r in ranges if _ATOMLINE_RE.search(mm, r[0], r[1])]


def _pdb_first_frame_end(mm):
    size = len(mm)
    first_model = _MODEL_RE.search(mm)
    start = first_model.start() if first_model else 0
    second_model = _MODEL_RE.search(mm, start + 1)
    candidates = [mm.find(b"\nENDMDL", start)]
    if second_model:
        candidates.append(second_model.start())
    candidates = [p for p in candidates if p >= 0]
    end_match = _END_RE.search(mm, start)
    if end_match:
        candidates.append(end_match.start())
    return start, (min(candidates) if candidates else size)


def peek_pdb(path):
    f, mm = _open_mmap(path)
    try:
        start, end = _pdb_first_frame_end(mm)
        n = len(_ATOMLINE_RE.findall(mm, start, end))
        rest = _ATOMLINE_RE.search(mm, end)
        return Peek(n, rest is not None)
    finally:
        mm.close()
        f.close()


def _pdb_topology(block, header):
    names, resnames, chains, resids, het, numbers, serials, icodes = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    for line in block.split(b"\n"):
        rec = line[:6]
        if rec != b"HETATM" and line[:4] != b"ATOM":
            continue
        text = line.decode("latin-1").rstrip("\r")
        is_het = rec == b"HETATM"
        name = text[12:16].ljust(4)
        resname = text[17:21].strip() or "UNK"
        element = text[76:78].strip()
        z = elements.atomic_number(element) if element.isalpha() else 0
        if z == 0:
            z = elements.atomic_number(
                elements.guess_pdb_element(name, resname, is_het)
            )
        try:
            serials.append(int(text[6:11]))
        except ValueError:
            serials.append(None)
        try:
            resids.append(int(text[22:26]))
        except ValueError:
            resids.append(0)
        names.append(name)
        resnames.append(resname)
        chains.append(text[21:22] or " ")
        icodes.append(text[26:27])
        het.append(is_het)
        numbers.append(z)
    n = len(numbers)
    ss = np.full(n, SS_COIL, dtype=np.uint8)
    chain_arr = np.array(chains, dtype="<U1") if n else np.empty(0, "<U1")
    resid_arr = np.array(resids, dtype=np.int32)
    for line in _SS_RE.findall(header):
        text = line.decode("latin-1")
        try:
            if text.startswith("HELIX"):
                chain, first, last, code = (
                    text[19],
                    int(text[21:25]),
                    int(text[33:37]),
                    SS_HELIX,
                )
            else:
                chain, first, last, code = (
                    text[21],
                    int(text[22:26]),
                    int(text[33:37]),
                    SS_SHEET,
                )
        except (IndexError, ValueError):
            continue
        mask = (chain_arr == chain) & (resid_arr >= first) & (resid_arr <= last)
        ss[mask] = code
    system = System(
        numbers=np.array(numbers, dtype=np.uint8),
        bonds=np.empty((0, 2), dtype=np.int32),
        names=names,
        resnames=resnames,
        resids=resid_arr,
        chains=chains,
        hetatm=np.array(het, dtype=bool),
        ss=ss,
    )
    return system, serials


def _pdb_conect(mm, serials):
    """Bonds and orders from CONECT records; empty when serials are unusable."""
    n = len(serials)
    if n == 0 or n > _MAX_CONECT_ATOMS or any(s is None for s in serials):
        return np.empty((0, 2), dtype=np.int32), {}
    index = {}
    for i, s in enumerate(serials):
        if s in index:
            return np.empty((0, 2), dtype=np.int32), {}
        index[s] = i
    orders = {}
    for line in _CONECT_RE.findall(mm):
        text = line.decode("latin-1").rstrip("\r")
        try:
            origin = index.get(int(text[6:11]))
        except ValueError:
            continue
        if origin is None:
            continue
        partners = Counter()
        for col in range(11, 31, 5):
            field = text[col : col + 5].strip()
            if not field:
                continue
            try:
                target = index.get(int(field))
            except ValueError:
                continue
            if target is not None and target != origin:
                partners[target] += 1
        for target, count in partners.items():
            key = (min(origin, target), max(origin, target))
            orders[key] = max(orders.get(key, 1), min(count, 3))
    bonds = np.array(sorted(orders), dtype=np.int32).reshape(-1, 2)
    return bonds, orders


def _assign_orders(bonds, orders):
    out = np.ones(len(bonds), dtype=np.uint8)
    if orders:
        for k, (a, b) in enumerate(bonds.tolist()):
            o = orders.get((a, b))
            if o:
                out[k] = o
    return out


def open_pdb(path):
    f, mm = _open_mmap(path)
    try:
        ranges = _pdb_frame_ranges(mm)
        if not ranges:
            raise FormatError("no ATOM or HETATM records")
        first_start, first_end = ranges[0]
        block = mm[first_start:first_end]
        header = mm[0:first_end]
        system, serials = _pdb_topology(block, header)
        coords = parse_pdb_coords(block).astype(np.float32)
        if len(coords) != system.n_atoms or system.n_atoms == 0:
            raise FormatError("could not read the atom records")
        conect, orders = _pdb_conect(mm, serials)
        default_box = _pdb_cell(header)
    finally:
        mm.close()
        f.close()
    system.bonds = merge_bonds(perceive_bonds(coords, system.numbers), conect)
    system.bond_orders = _assign_orders(system.bonds, orders)
    if len(ranges) == 1:
        return system, ArrayFrames(coords[None], default_box=default_box)
    return system, _MappedFrames(
        path, ranges, system.n_atoms, parse_pdb_frame, default_box
    )


# ------------------------------------------------------------- line-periodic formats


def _periodic_line_offsets(mm, period):
    """Byte offsets of every `period`-th line, for complete frames only."""
    size = len(mm)
    offsets = [0]
    line_no = 0
    for chunk_start in range(0, size, _CHUNK):
        block = mm[chunk_start : chunk_start + _CHUNK]
        nl = np.flatnonzero(np.frombuffer(block, dtype=np.uint8) == 10)
        if len(nl):
            line_index = line_no + 1 + np.arange(len(nl))
            hits = nl[(line_index % period) == 0] + chunk_start + 1
            offsets.extend(hits.tolist())
            line_no += len(nl)
    total_lines = line_no + (0 if mm[size - 1] == 10 else 1)
    n_frames = total_lines // period
    offsets = [o for o in offsets if o < size][:n_frames]
    ends = offsets[1:] + [size]
    return list(zip(offsets, ends))


def _first_lines(mm, count, start=0):
    lines = []
    pos = start
    size = len(mm)
    for _ in range(count):
        if pos >= size:
            break
        nl = mm.find(b"\n", pos)
        if nl < 0:
            nl = size
        lines.append(mm[pos:nl].decode("latin-1").rstrip("\r"))
        pos = nl + 1
    return lines, pos


def _has_content_after(mm, pos):
    return bool(mm[pos : pos + 4096].strip())


# ------------------------------------------------------------------------------ GRO


def _gro_width(line):
    first = line.find(".", 20)
    second = line.find(".", first + 1)
    if first < 0 or second < 0:
        raise FormatError("cannot find the GRO coordinate columns")
    return second - first


def gro_cell(line):
    """Cell from a GRO box line (nm): v1x v2y v3z [v1y v1z v2x v2z v3x v3y]."""
    try:
        v = [float(x) for x in line.split()]
    except ValueError:
        return None
    if len(v) == 3:
        box = np.diag(v)
    elif len(v) >= 9:
        box = np.array([[v[0], v[3], v[4]], [v[5], v[1], v[6]], [v[7], v[8], v[2]]])
    else:
        return None
    return _valid_cell(box * 10.0)


def _gro_parser(width, n_atoms):
    def parse(block):
        arr = np.frombuffer(block, dtype=np.uint8)
        all_starts = _line_starts(arr)
        starts = all_starts[2 : 2 + n_atoms]
        coords = parse_fixed(arr, starts + 20, width, 3) * 10.0
        box = None
        if len(all_starts) > 2 + n_atoms:
            box_start = int(all_starts[2 + n_atoms])
            box_end = block.find(b"\n", box_start)
            if box_end < 0:
                box_end = len(block)
            box = gro_cell(block[box_start:box_end].decode("latin-1"))
        return coords, box

    return parse


def peek_gro(path):
    f, mm = _open_mmap(path)
    try:
        head, _ = _first_lines(mm, 2)
        try:
            n = int(head[1].split()[0])
        except (IndexError, ValueError) as exc:
            raise FormatError("line 2 of a GRO file must be the atom count") from exc
        if n > 100000:
            return Peek(n, True)
        _, end = _first_lines(mm, n + 3)
        return Peek(n, _has_content_after(mm, end))
    finally:
        mm.close()
        f.close()


def open_gro(path):
    f, mm = _open_mmap(path)
    try:
        head, _ = _first_lines(mm, 3)
        try:
            n = int(head[1].split()[0])
        except (IndexError, ValueError) as exc:
            raise FormatError("line 2 of a GRO file must be the atom count") from exc
        if n <= 0:
            raise FormatError("the GRO file has no atoms")
        width = _gro_width(head[2])
        ranges = _periodic_line_offsets(mm, n + 3)
        if not ranges:
            raise FormatError("the GRO frame is incomplete")
        lines, _ = _first_lines(mm, n + 2)
        block = mm[ranges[0][0] : ranges[0][1]]
    finally:
        mm.close()
        f.close()
    parse = _gro_parser(width, n)
    coords, box = parse(block)
    coords = coords.astype(np.float32)
    names, resnames, resids, numbers = [], [], [], []
    for line in lines[2 : 2 + n]:
        try:
            resids.append(int(line[0:5]))
        except ValueError:
            resids.append(0)
        resname = line[5:10].strip()
        name = line[10:15].strip()
        resnames.append(resname)
        names.append(name)
        numbers.append(
            elements.atomic_number(elements.guess_name_element(name, resname))
        )
    protein = elements._PROTEIN_RESIDUES
    het = np.array([r.upper() not in protein for r in resnames], dtype=bool)
    system = System(
        numbers=np.array(numbers, dtype=np.uint8),
        bonds=perceive_bonds(coords, numbers),
        names=[(" " + nm).ljust(4) if len(nm) < 4 else nm[:4] for nm in names],
        resnames=resnames,
        resids=np.array(resids, dtype=np.int32),
        chains=["A"] * n,
        hetatm=het,
        ss=np.full(n, SS_COIL, dtype=np.uint8),
    )
    system.bond_orders = np.ones(len(system.bonds), dtype=np.uint8)
    if len(ranges) == 1:
        return system, ArrayFrames(coords[None], default_box=box)
    return system, _MappedFrames(path, ranges, n, parse, box)


# ------------------------------------------------------------------------------ XYZ


_LATTICE_RE = re.compile(r'Lattice\s*=\s*"([^"]*)"', re.IGNORECASE)


def xyz_cell(comment):
    """Cell from an extended-XYZ comment line (Lattice="ax ay az bx by bz cx cy cz")."""
    match = _LATTICE_RE.search(comment or "")
    if not match:
        return None
    try:
        v = [float(x) for x in match.group(1).split()]
    except ValueError:
        return None
    if len(v) != 9:
        return None
    return _valid_cell(np.array(v).reshape(3, 3))


def _xyz_parser(n_atoms):
    def parse(block):
        lines = block.split(b"\n", n_atoms + 2)
        body = lines[2 : 2 + n_atoms]
        box = xyz_cell(lines[1].decode("latin-1")) if len(lines) > 1 else None
        tokens = b" ".join(body).split()
        if len(tokens) == 4 * n_atoms:
            table = np.array(tokens, dtype="S").reshape(n_atoms, 4)[:, 1:]
            return table.astype(np.float64), box
        rows = [line.split()[1:4] for line in body]
        return np.array(rows, dtype=np.float64), box

    return parse


def _xyz_count(line):
    try:
        return int(line.split()[0])
    except (IndexError, ValueError) as exc:
        raise FormatError("line 1 of an XYZ file must be the atom count") from exc


def peek_xyz(path):
    f, mm = _open_mmap(path)
    try:
        head, _ = _first_lines(mm, 1)
        n = _xyz_count(head[0])
        if n > 100000:
            return Peek(n, True)
        _, end = _first_lines(mm, n + 2)
        return Peek(n, _has_content_after(mm, end))
    finally:
        mm.close()
        f.close()


def open_xyz(path):
    f, mm = _open_mmap(path)
    try:
        head, _ = _first_lines(mm, 1)
        n = _xyz_count(head[0])
        if n <= 0:
            raise FormatError("the XYZ file has no atoms")
        ranges = _periodic_line_offsets(mm, n + 2)
        valid = []
        for start, end in ranges:
            line, _ = _first_lines(mm, 1, start)
            try:
                if _xyz_count(line[0]) != n:
                    break
            except FormatError:
                break
            valid.append((start, end))
        if not valid:
            raise FormatError("the XYZ frame is incomplete")
        lines, _ = _first_lines(mm, n + 2)
        block = mm[valid[0][0] : valid[0][1]]
    finally:
        mm.close()
        f.close()
    parse = _xyz_parser(n)
    try:
        coords, box = parse(block)
        coords = coords.astype(np.float32)
    except ValueError as exc:
        raise FormatError(f"cannot read the XYZ coordinates: {exc}") from exc
    numbers = [
        elements.atomic_number(line.split()[0]) if line.split() else 0
        for line in lines[2 : 2 + n]
    ]
    system = System(
        numbers=np.array(numbers, dtype=np.uint8),
        bonds=perceive_bonds(coords, numbers),
    )
    system.bond_orders = np.ones(len(system.bonds), dtype=np.uint8)
    if len(valid) == 1:
        return system, ArrayFrames(coords[None], default_box=box)
    return system, _MappedFrames(path, valid, n, parse, box)


# ------------------------------------------------------------------------------ SDF


def _sdf_records(path):
    from rdkit import Chem

    supplier = Chem.ForwardSDMolSupplier(path, removeHs=False, sanitize=False)
    for mol in supplier:
        if mol is not None:
            mol.UpdatePropertyCache(strict=False)
            yield mol


def peek_sdf(path):
    from rdkit import Chem

    with open(path, "rb") as fh:
        data = fh.read()
    first, sep, rest = data.partition(b"$$$$")
    mol = Chem.MolFromMolBlock(first.decode("latin-1"), sanitize=False, removeHs=False)
    if mol is None:
        raise FormatError("cannot read the first SDF record")
    return Peek(mol.GetNumAtoms(), bool(sep) and bool(rest.strip()))


def open_sdf(path):
    from .system import from_rdkit

    records = iter(_sdf_records(path))
    first = next(records, None)
    if first is None:
        raise FormatError("no readable SDF record")
    system, frames = from_rdkit(first)
    frames = [frames[0]]
    for mol in records:
        nums = np.fromiter((a.GetAtomicNum() for a in mol.GetAtoms()), np.uint8)
        if len(nums) != system.n_atoms or not np.array_equal(nums, system.numbers):
            raise FormatError("the SDF records are different molecules, not frames")
        frames.append(mol.GetConformer().GetPositions().astype(np.float32))
    system.extra["rdkit_mol"] = first
    return system, ArrayFrames(np.stack(frames))


# --------------------------------------------------------------------------- dispatch

_PEEK = {"pdb": peek_pdb, "gro": peek_gro, "xyz": peek_xyz, "sdf": peek_sdf}
_OPEN = {"pdb": open_pdb, "gro": open_gro, "xyz": open_xyz, "sdf": open_sdf}


def supported(path):
    return os.path.splitext(path)[1].lower() in EXTENSIONS


def peek(path):
    fmt = format_of(path)
    if fmt not in _PEEK:
        raise FormatError(f"unsupported file type: {fmt}")
    return _PEEK[fmt](path)


def open_structure(path):
    """(System, FrameSource) for a supported file."""
    fmt = format_of(path)
    if fmt not in _OPEN:
        raise FormatError(f"unsupported file type: {fmt}")
    return _OPEN[fmt](path)
