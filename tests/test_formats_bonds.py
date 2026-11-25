"""File readers, bond perception, RDKit conversion and the frame session."""

import pytest

np = pytest.importorskip("numpy")

from large_system_viewer import bonds, formats  # noqa: E402
from large_system_viewer.session import MODE_HYBRID, Session  # noqa: E402

WATER = [("OW", 0.0, 0.0, 0.0), ("HW1", 0.096, 0.0, 0.0), ("HW2", -0.024, 0.093, 0.0)]


def _write_gro(path, n_frames=3, n_waters=2, triclinic_last=False):
    lines = []
    for f in range(n_frames):
        lines += [f"frame {f}", f"{3 * n_waters:5d}"]
        k = 0
        for w in range(n_waters):
            for name, x, y, z in WATER:
                k += 1
                lines.append(
                    f"{w + 1:5d}{'SOL':<5s}{name:>5s}{k:5d}"
                    f"{x + 0.5 * w:8.3f}{y:8.3f}{z + 0.1 * f:8.3f}"
                )
        if triclinic_last and f == n_frames - 1:
            lines.append(
                "   3.00000   3.00000   3.00000   0.0   0.0   1.0   0.0   0.5   0.5"
            )
        else:
            lines.append("   3.00000   3.00000   3.00000")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


PDB = """\
HEADER    TEST MODEL WORDS IN THE HEADER
CRYST1   20.000   30.000   40.000  90.00  90.00  90.00 P 1           1
MODEL        1
ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O
HETATM    5 NA    NA A 101      10.000  10.000  10.000  1.00  0.00          NA
ENDMDL
MODEL        2
ATOM      1  N   ALA A   1       0.100   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.558   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.109   1.420   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.351   2.390   0.000  1.00  0.00           O
HETATM    5 NA    NA A 101      10.100  10.000  10.000  1.00  0.00          NA
ENDMDL
END
"""


def test_gro_trajectory_and_box(tmp_path):
    path = _write_gro(tmp_path / "w.gro", triclinic_last=True)
    peek = formats.peek(path)
    assert peek.n_atoms == 6 and peek.multi_frame
    system, frames = formats.open_structure(path)
    assert system.numbers.tolist() == [8, 1, 1, 8, 1, 1]
    assert sorted(map(tuple, system.bonds.tolist())) == [(0, 1), (0, 2), (3, 4), (3, 5)]
    assert frames.n_frames == 3
    pos, box = frames.read_frame(1)
    assert pos.dtype == np.float32
    assert np.allclose(pos[0], [0.0, 0.0, 1.0], atol=1e-4)  # nm -> Angstrom
    assert np.allclose(np.diag(box), [30.0, 30.0, 30.0])
    _, tri = frames.read_frame(2)
    assert np.allclose(tri[1], [10.0, 30.0, 0.0])
    frames.close()


def test_pdb_models_cell_and_elements(tmp_path):
    path = tmp_path / "t.pdb"
    path.write_text(PDB, encoding="utf-8")
    peek = formats.peek(str(path))
    assert peek.n_atoms == 5 and peek.multi_frame
    system, frames = formats.open_structure(str(path))
    assert system.numbers.tolist() == [7, 6, 6, 8, 11]
    assert system.hetatm.tolist() == [False] * 4 + [True]
    assert frames.n_frames == 2
    pos, box = frames.read_frame(1)
    assert np.allclose(pos[0], [0.1, 0.0, 0.0], atol=1e-4)
    assert np.allclose(np.diag(box), [20.0, 30.0, 40.0], atol=1e-3)
    # Na+ is a free ion: never bonded
    assert 4 not in system.bonds.ravel().tolist()
    frames.close()


def test_xyz_frames_with_lattice(tmp_path):
    path = tmp_path / "t.xyz"
    with open(path, "w", encoding="utf-8") as fh:
        for f in range(4):
            fh.write(
                '3\nLattice="10 0 0 0 11 0 0 0 12" Properties=species:S:1:pos:R:3\n'
            )
            fh.write(f"O 0 0 {f}\nH 0.96 0 {f}\nH -0.24 0.93 {f}\n")
    system, frames = formats.open_structure(str(path))
    assert system.numbers.tolist() == [8, 1, 1]
    assert frames.n_frames == 4
    pos, box = frames.read_frame(3)
    assert np.allclose(pos[:, 2], 3.0)
    assert np.allclose(np.diag(box), [10.0, 11.0, 12.0])


def test_single_xyz_is_not_multi_frame(tmp_path):
    path = tmp_path / "one.xyz"
    path.write_text("3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n", encoding="utf-8")
    peek = formats.peek(str(path))
    assert peek.n_atoms == 3 and not peek.multi_frame


def test_unsupported_and_broken_files(tmp_path):
    assert not formats.supported("a.cif")
    with pytest.raises(formats.FormatError):
        formats.peek(str(tmp_path / "a.cif"))
    bad = tmp_path / "bad.xyz"
    bad.write_text("not a count\n", encoding="utf-8")
    with pytest.raises((formats.FormatError, ValueError)):
        formats.peek(str(bad))


def test_sdf_conformers_as_frames(tmp_path):
    Chem = pytest.importorskip("rdkit.Chem")
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    ids = AllChem.EmbedMultipleConfs(mol, 3, randomSeed=7)
    path = tmp_path / "c.sdf"
    writer = Chem.SDWriter(str(path))
    for cid in ids:
        writer.write(mol, confId=cid)
    writer.close()
    system, frames = formats.open_structure(str(path))
    assert system.n_atoms == mol.GetNumAtoms()
    assert frames.n_frames == 3
    assert np.allclose(
        frames.read(2), mol.GetConformer(ids[2]).GetPositions(), atol=1e-3
    )


def test_bond_perception_grid_matches_brute_force():
    rng = np.random.default_rng(3)
    pos = (rng.random((400, 3)) * 12).astype(np.float32)
    numbers = rng.choice([1, 6, 7, 8], size=400).astype(np.uint8)
    found = {tuple(sorted(b)) for b in bonds.perceive_bonds(pos, numbers).tolist()}
    from large_system_viewer.elements import COVALENT_RADII

    radius = np.array([COVALENT_RADII[z] for z in numbers])
    d = np.linalg.norm(pos[:, None] - pos[None], axis=-1)
    limit = radius[:, None] + radius[None] + bonds.TOLERANCE
    i, j = np.nonzero(np.triu((d < limit) & (d > bonds.MIN_DISTANCE), 1))
    brute = set(zip(i.tolist(), j.tolist()))
    assert found <= brute
    heavy = {(a, b) for a, b in brute if numbers[a] != 1 and numbers[b] != 1}
    assert heavy <= found


def test_rdkit_roundtrip_and_session(tmp_path):
    pytest.importorskip("rdkit")
    from large_system_viewer.system import from_rdkit, to_rdkit

    path = _write_gro(tmp_path / "w.gro")
    system, frames = formats.open_structure(path)
    mol = to_rdkit(system, frames.read(0))
    assert mol.GetNumAtoms() == 6 and mol.GetNumBonds() == 4
    back, back_frames = from_rdkit(mol)
    assert back.numbers.tolist() == system.numbers.tolist()
    assert np.allclose(back_frames[0], frames.read(0), atol=1e-4)

    session = Session(system, frames, MODE_HYBRID, path=path, mol=mol)
    assert session.can_play and session.title == "w.gro"
    assert session.host_matches_commit(mol)
    session.index = 2
    assert np.allclose(session.positions()[0], [0.0, 0.0, 2.0], atol=1e-4)
    mol.GetConformer().SetAtomPosition(0, (5.0, 5.0, 5.0))
    assert not session.host_matches_commit(mol)
    session.detached = True
    assert not session.can_play
    session.close()
