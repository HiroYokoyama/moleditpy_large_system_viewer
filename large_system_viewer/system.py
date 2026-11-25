"""System: the topology shared by every frame of a structure or trajectory."""

from dataclasses import dataclass, field
from typing import List, Optional

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a host dependency
    np = None

from . import elements

SS_COIL = ord("c")
SS_HELIX = ord("h")
SS_SHEET = ord("s")

SS_PROP = "_lsv_ss"


@dataclass
class System:
    """Atoms and bonds; coordinates live in the frame source."""

    numbers: "np.ndarray"  # uint8 [N]
    bonds: "np.ndarray"  # int32 [M, 2]
    bond_orders: Optional["np.ndarray"] = None  # uint8 [M], 1 when unknown
    names: Optional[List[str]] = None  # PDB/GRO atom names
    resnames: Optional[List[str]] = None
    resids: Optional["np.ndarray"] = None  # int32 [N]
    chains: Optional[List[str]] = None
    hetatm: Optional["np.ndarray"] = None  # bool [N]
    ss: Optional["np.ndarray"] = None  # uint8 [N] of SS_COIL / SS_HELIX / SS_SHEET
    extra: dict = field(default_factory=dict)

    @property
    def n_atoms(self):
        return int(len(self.numbers))

    @property
    def has_residues(self):
        return self.names is not None and self.resids is not None

    def is_protein_capable(self):
        """True when a ribbon can be drawn: residues with CA atoms."""
        if not self.has_residues:
            return False
        het = self.hetatm if self.hetatm is not None else np.zeros(self.n_atoms, bool)
        return any(n.strip() == "CA" and not h for n, h in zip(self.names, het))

    def water_mask(self):
        if self.resnames is None:
            return np.zeros(self.n_atoms, dtype=bool)
        return np.fromiter(
            (r.strip().upper() in elements.WATER_RESIDUES for r in self.resnames),
            dtype=bool,
            count=self.n_atoms,
        )

    def ligand_mask(self):
        """Non-water HETATM atoms (drawn as atoms next to a ribbon)."""
        if self.hetatm is None:
            return np.zeros(self.n_atoms, dtype=bool)
        return self.hetatm & ~self.water_mask()


def from_rdkit(mol):
    """System and frames [F, N, 3] (one per conformer) from an RDKit molecule."""
    n = mol.GetNumAtoms()
    numbers = np.fromiter((a.GetAtomicNum() for a in mol.GetAtoms()), np.uint8, n)
    m = mol.GetNumBonds()
    bonds = np.empty((m, 2), dtype=np.int32)
    orders = np.ones(m, dtype=np.uint8)
    for k, b in enumerate(mol.GetBonds()):
        bonds[k] = (b.GetBeginAtomIdx(), b.GetEndAtomIdx())
        t = b.GetBondTypeAsDouble()
        orders[k] = 2 if t == 2.0 else 3 if t == 3.0 else 1
    system = System(numbers=numbers, bonds=bonds, bond_orders=orders)

    infos = [a.GetPDBResidueInfo() for a in mol.GetAtoms()]
    if n and all(i is not None for i in infos):
        system.names = [i.GetName() for i in infos]
        system.resnames = [i.GetResidueName() for i in infos]
        system.resids = np.array([i.GetResidueNumber() for i in infos], dtype=np.int32)
        system.chains = [i.GetChainId() or " " for i in infos]
        system.hetatm = np.array([i.GetIsHeteroAtom() for i in infos], dtype=bool)
        system.ss = np.array(
            [a.GetIntProp(SS_PROP) if a.HasProp(SS_PROP) else SS_COIL for a in mol.GetAtoms()],
            dtype=np.uint8,
        )

    confs = list(mol.GetConformers())
    if confs:
        frames = np.stack([c.GetPositions() for c in confs]).astype(np.float32)
    else:
        frames = np.zeros((1, n, 3), dtype=np.float32)
    return system, frames


def to_rdkit(system, positions):
    """Unsanitized RDKit molecule of the system at the given positions."""
    from rdkit import Chem

    rw = Chem.RWMol()
    has_res = system.has_residues
    for i, z in enumerate(system.numbers.tolist()):
        atom = Chem.Atom(int(z))
        atom.SetNoImplicit(True)
        if has_res:
            info = Chem.AtomPDBResidueInfo(
                _pdb_name(system.names[i]),
                serialNumber=i + 1,
                residueName=system.resnames[i][:3] if system.resnames else "UNK",
                residueNumber=int(system.resids[i]),
                chainId=(system.chains[i] if system.chains else " ")[:1],
                isHeteroAtom=bool(system.hetatm[i]) if system.hetatm is not None else False,
            )
            atom.SetMonomerInfo(info)
            if system.ss is not None:
                atom.SetIntProp(SS_PROP, int(system.ss[i]))
        rw.AddAtom(atom)
    orders = system.bond_orders
    types = {1: Chem.BondType.SINGLE, 2: Chem.BondType.DOUBLE, 3: Chem.BondType.TRIPLE}
    for k, (a, b) in enumerate(system.bonds.tolist()):
        order = int(orders[k]) if orders is not None else 1
        rw.AddBond(int(a), int(b), types.get(order, Chem.BondType.SINGLE))
    conf = Chem.Conformer(system.n_atoms)
    conf.SetPositions(np.asarray(positions, dtype=np.float64))
    rw.AddConformer(conf, assignId=True)
    mol = rw.GetMol()
    mol.UpdatePropertyCache(strict=False)
    return mol


def _pdb_name(name):
    """Pad an atom name to the 4-column PDB convention."""
    name = name or ""
    if len(name) == 4:
        return name
    name = name.strip()[:4]
    if len(name) < 4 and not name[:1].isdigit():
        return (" " + name).ljust(4)
    return name.ljust(4)
