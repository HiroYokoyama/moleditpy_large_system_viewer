"""Element table: symbols, atomic numbers, covalent radii and element guessing.

Pure Python so the readers can be tested without RDKit.
"""

SYMBOLS = (
    "X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
    "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
    "Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl "
    "Mc Lv Ts Og"
).split()

ATOMIC_NUMBER = {s.upper(): z for z, s in enumerate(SYMBOLS)}

# Cordero et al., Dalton Trans. 2008, 2832 (sp3 carbon, low-spin metals).
_COVALENT = {
    "H": 0.31, "He": 0.28, "Li": 1.28, "Be": 0.96, "B": 0.84, "C": 0.76, "N": 0.71,
    "O": 0.66, "F": 0.57, "Ne": 0.58, "Na": 1.66, "Mg": 1.41, "Al": 1.21, "Si": 1.11,
    "P": 1.07, "S": 1.05, "Cl": 1.02, "Ar": 1.06, "K": 2.03, "Ca": 1.76, "Sc": 1.70,
    "Ti": 1.60, "V": 1.53, "Cr": 1.39, "Mn": 1.39, "Fe": 1.32, "Co": 1.26, "Ni": 1.24,
    "Cu": 1.32, "Zn": 1.22, "Ga": 1.22, "Ge": 1.20, "As": 1.19, "Se": 1.20, "Br": 1.20,
    "Kr": 1.16, "Rb": 2.20, "Sr": 1.95, "Y": 1.90, "Zr": 1.75, "Nb": 1.64, "Mo": 1.54,
    "Tc": 1.47, "Ru": 1.46, "Rh": 1.42, "Pd": 1.39, "Ag": 1.45, "Cd": 1.44, "In": 1.42,
    "Sn": 1.39, "Sb": 1.39, "Te": 1.38, "I": 1.39, "Xe": 1.40, "Cs": 2.44, "Ba": 2.15,
    "La": 2.07, "Ce": 2.04, "Pr": 2.03, "Nd": 2.01, "Pm": 1.99, "Sm": 1.98, "Eu": 1.98,
    "Gd": 1.96, "Tb": 1.94, "Dy": 1.92, "Ho": 1.92, "Er": 1.89, "Tm": 1.90, "Yb": 1.87,
    "Lu": 1.87, "Hf": 1.75, "Ta": 1.70, "W": 1.62, "Re": 1.51, "Os": 1.44, "Ir": 1.41,
    "Pt": 1.36, "Au": 1.36, "Hg": 1.32, "Tl": 1.45, "Pb": 1.46, "Bi": 1.48, "Po": 1.40,
    "At": 1.50, "Rn": 1.50, "Fr": 2.60, "Ra": 2.21, "Ac": 2.15, "Th": 2.06, "Pa": 2.00,
    "U": 1.96, "Np": 1.90, "Pu": 1.87, "Am": 1.80, "Cm": 1.69,
}
_DEFAULT_COVALENT = 1.50

COVALENT_RADII = tuple(_COVALENT.get(s, _DEFAULT_COVALENT) for s in SYMBOLS)

# Free ions and noble gases: never bonded by distance (CONECT records still apply).
NON_BONDING = frozenset(
    ATOMIC_NUMBER[s.upper()]
    for s in ("He", "Ne", "Ar", "Kr", "Xe", "Rn", "Li", "Na", "K", "Rb", "Cs",
              "Mg", "Ca", "Sr", "Ba")
)

WATER_RESIDUES = frozenset(
    ("HOH", "WAT", "H2O", "SOL", "TIP3", "TIP4", "TIP5", "TIP", "SPC", "DOD", "T3P", "T4P")
)

# Residue names that are single-ion residues in MD force fields.
_ION_RESIDUES = {
    "NA": "Na", "NA+": "Na", "SOD": "Na", "CL": "Cl", "CL-": "Cl", "CLA": "Cl",
    "K": "K", "K+": "K", "POT": "K", "MG": "Mg", "MG2": "Mg", "CA": "Ca", "CAL": "Ca",
    "ZN": "Zn", "ZN2": "Zn", "LI": "Li", "RB": "Rb", "CS": "Cs", "BR": "Br", "IOD": "I",
    "F": "F",
}


def atomic_number(symbol):
    """Atomic number for an element symbol or number string; 0 when unknown."""
    text = str(symbol).strip()
    if not text:
        return 0
    if text.isdigit():
        z = int(text)
        return z if 0 <= z < len(SYMBOLS) else 0
    return ATOMIC_NUMBER.get(text.upper(), 0)


def symbol(z):
    """Element symbol for an atomic number; 'X' when out of range."""
    return SYMBOLS[z] if 0 <= z < len(SYMBOLS) else "X"


def _first_alpha(text):
    for ch in text:
        if ch.isalpha():
            return ch.upper()
    return ""


def guess_pdb_element(name4, resname="", hetatm=False):
    """Element of a PDB atom from its 4-column name (columns 13-16).

    Two-letter elements are left-justified in column 13; one-letter elements start in
    column 14.  Hydrogens with 4-character names ("HG11", "HD21") also start in column
    13, so a 4-character name starting with H is hydrogen, never mercury or the like.
    """
    name4 = (name4 or "").ljust(4)[:4]
    stripped = name4.strip()
    if not stripped:
        return "X"
    ion = _ION_RESIDUES.get(resname.strip().upper())
    if ion and stripped.upper().rstrip("+-0123456789") == ion.upper():
        return ion
    if name4[0].isdigit():
        return _first_alpha(name4[1:]) or "X"
    if name4[0] != " " and hetatm:
        candidate = name4[:2].strip()
        if (
            len(candidate) == 2
            and candidate.isalpha()
            and candidate.upper() in ATOMIC_NUMBER
            and not (candidate[0].upper() == "H" and len(stripped) == 4)
        ):
            return candidate[0].upper() + candidate[1].lower()
    return _first_alpha(stripped) or "X"


def guess_name_element(name, resname=""):
    """Element of an atom named without column conventions (GRO, mol2-like names)."""
    ion = _ION_RESIDUES.get(resname.strip().upper())
    stripped = name.strip()
    if ion and stripped.upper().rstrip("+-0123456789") in (ion.upper(), resname.strip().upper()):
        return ion
    letters = "".join(ch for ch in stripped if ch.isalpha())
    if not letters:
        return "X"
    if letters[:2].upper() in ("CL", "BR") and resname.strip().upper() not in _PROTEIN_RESIDUES:
        return letters[0].upper() + letters[1].lower()
    return letters[0].upper()


_PROTEIN_RESIDUES = frozenset(
    """ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL
    HID HIE HIP HSD HSE HSP CYX ASH GLH LYN NALA NGLY CALA""".split()
)
