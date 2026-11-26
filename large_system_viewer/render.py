"""VTK scene for the Fast style: impostor atoms, protein ribbons and the periodic cell.

Everything here is drawn into the host's PyVista plotter, so the host's background,
axes, camera and 3D image export keep working.
"""

import logging

try:
    import numpy as np
except ImportError:  # pragma: no cover - numpy is a host dependency
    np = None

from . import elements
from .settings import RIBBON_REPRESENTATIONS, hex_to_rgb

logger = logging.getLogger(__name__)

ACTOR_PREFIX = "large_system_viewer_"

# vtkProteinRibbonFilter ignores per-atom colours and paints its output itself:
# helix (255, 0, 128), sheet (255, 200, 0), everything else is coil.
_VTK_HELIX = (255, 0, 128)
_VTK_SHEET = (255, 200, 0)
CHAIN_PALETTE = (
    "#4E79A7",
    "#F28E2B",
    "#E15759",
    "#76B7B2",
    "#59A14F",
    "#EDC948",
    "#B07AA1",
    "#FF9DA7",
    "#9C755F",
    "#BAB0AC",
)
_N_ELEMENTS = 119

_BOX_EDGES = (
    (0, 1),
    (0, 2),
    (0, 4),
    (1, 3),
    (1, 5),
    (2, 3),
    (2, 6),
    (3, 7),
    (4, 5),
    (4, 6),
    (5, 7),
    (6, 7),
)


def _vtk():
    """Import the VTK pieces lazily: tests and the plugin metadata do not need them."""
    import vtkmodules.vtkDomainsChemistryOpenGL2
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
    from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
    from vtkmodules.vtkCommonCore import (
        vtkFloatArray,
        vtkIdTypeArray,
        vtkLookupTable,
        vtkPoints,
        vtkStringArray,
        vtkUnsignedCharArray,
        vtkUnsignedIntArray,
    )
    from vtkmodules.vtkCommonDataModel import (
        vtkCellArray,
        vtkDataSetAttributes,
        vtkMolecule,
        vtkPolyData,
    )
    from vtkmodules.vtkDomainsChemistry import vtkMoleculeMapper, vtkProteinRibbonFilter
    from vtkmodules.vtkFiltersCore import vtkAppendPolyData
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

    return {
        "vtkAppendPolyData": vtkAppendPolyData,
        "numpy_to_vtk": numpy_to_vtk,
        "vtk_to_numpy": vtk_to_numpy,
        "vtkIdTypeArray": vtkIdTypeArray,
        "vtkLookupTable": vtkLookupTable,
        "vtkPoints": vtkPoints,
        "vtkStringArray": vtkStringArray,
        "vtkUnsignedCharArray": vtkUnsignedCharArray,
        "vtkUnsignedIntArray": vtkUnsignedIntArray,
        "vtkFloatArray": vtkFloatArray,
        "vtkCellArray": vtkCellArray,
        "vtkDataSetAttributes": vtkDataSetAttributes,
        "vtkMolecule": vtkMolecule,
        "vtkPolyData": vtkPolyData,
        "vtkMoleculeMapper": vtkMoleculeMapper,
        "vtkProteinRibbonFilter": vtkProteinRibbonFilter,
        "vtkActor": vtkActor,
        "vtkPolyDataMapper": vtkPolyDataMapper,
    }


def vdw_radii_table():
    """Van der Waals radius per atomic number, the way the host computes it."""
    table = np.full(_N_ELEMENTS, 1.5)
    try:
        from rdkit import Chem

        pt = Chem.GetPeriodicTable()
        for z in range(1, _N_ELEMENTS):
            r = pt.GetRvdw(z)
            table[z] = r if r > 0.1 else 1.5
    except Exception:  # pragma: no cover - RDKit is a host dependency
        logger.debug("RDKit periodic table unavailable", exc_info=True)
    return table


def ribbon_color_options(options):
    """Colour mode and uint8 colours for the ribbon from the settings."""

    def rgb(key):
        return np.array(
            [round(255 * c) for c in hex_to_rgb(options.get(key))], dtype=np.uint8
        )

    return {
        "mode": options.get("ribbon_color_mode", "secondary"),
        "helix": rgb("ribbon_helix_color"),
        "sheet": rgb("ribbon_sheet_color"),
        "coil": rgb("ribbon_coil_color"),
        "uniform": rgb("ribbon_uniform_color"),
        "palette": [
            np.array([round(255 * c) for c in hex_to_rgb(h)], dtype=np.uint8)
            for h in CHAIN_PALETTE
        ],
    }


def ribbon_rgb(vtk_rgb, colors, chain_number):
    """Colours for ribbon points painted by vtkProteinRibbonFilter."""
    out = np.empty_like(vtk_rgb)
    mode = colors["mode"]
    if mode == "uniform":
        out[:] = colors["uniform"]
    elif mode == "chain":
        palette = colors["palette"]
        out[:] = palette[chain_number % len(palette)]
    else:
        out[:] = colors["coil"]
        out[(vtk_rgb == _VTK_HELIX).all(axis=1)] = colors["helix"]
        out[(vtk_rgb == _VTK_SHEET).all(axis=1)] = colors["sheet"]
    return out


def color_table(cpk_colors):
    """[119, 3] RGB per atomic number from a symbol -> (r, g, b) mapping."""
    table = np.full((_N_ELEMENTS, 3), 0.5)
    for z in range(1, _N_ELEMENTS):
        rgb = cpk_colors.get(elements.symbol(z)) if cpk_colors else None
        if rgb is not None and len(rgb) >= 3:
            table[z] = [float(c) for c in rgb[:3]]
    table[0] = [1.0, 0.4, 0.7]
    return table


def atom_radii(numbers, look, vdw):
    numbers = np.clip(np.asarray(numbers, dtype=np.int64), 0, _N_ELEMENTS - 1)
    mode = look.get("atom_mode")
    if mode == "constant":
        return np.full(len(numbers), look["atom_scale"], dtype=np.float32)
    if mode == "vdw":
        return (vdw[numbers] * look["atom_scale"]).astype(np.float32)
    return (vdw[numbers] * 0.3 * look["atom_scale"]).astype(np.float32)


def secondary_structure_bounds(ss, chains, resids):
    """Begin/end flags of each secondary-structure segment, per atom."""
    n = len(ss)
    begin = np.zeros(n, dtype=np.uint8)
    end = np.zeros(n, dtype=np.uint8)
    if n == 0:
        return begin, end
    chain_arr = np.asarray(chains)
    res_arr = np.asarray(resids)
    new_res = np.ones(n, dtype=bool)
    new_res[1:] = (res_arr[1:] != res_arr[:-1]) | (chain_arr[1:] != chain_arr[:-1])
    res_ids = np.cumsum(new_res) - 1
    res_ss = ss[new_res]
    res_chain = chain_arr[new_res]
    nr = len(res_ss)
    starts = np.ones(nr, dtype=bool)
    starts[1:] = (res_ss[1:] != res_ss[:-1]) | (res_chain[1:] != res_chain[:-1])
    ends = np.ones(nr, dtype=bool)
    ends[:-1] = starts[1:]
    begin[:] = starts[res_ids]
    end[:] = ends[res_ids]
    return begin, end


def cell_corners(box):
    box = np.asarray(box, dtype=np.float64)
    a, b, c = box
    corners = []
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                corners.append(i * a + j * b + k * c)
    return np.array(corners)


class FastScene:
    """Actors of one system in the host plotter, updatable frame by frame."""

    def __init__(self, plotter):
        self.plotter = plotter
        self.actors = {}
        self._mol = None
        self._mol_index = None
        self._ribbons = []  # (points polydata, atom index, filter, chain number)
        self._ribbon_append = None
        self._ribbon_colors = None
        self._box_points = None
        self.representation = None
        self.fallback_reason = ""

    # ------------------------------------------------------------------ build

    def build(self, system, positions, box, options):
        """(Re)create every actor. Returns the representation actually drawn."""
        vtk = _vtk()
        self.remove()
        rep = options["representation"]
        self.fallback_reason = ""
        if rep in RIBBON_REPRESENTATIONS and not system.is_protein_capable():
            self.fallback_reason = "no protein residues for a ribbon"
            rep = "ball_and_stick"
        self.representation = rep

        visible = np.ones(system.n_atoms, dtype=bool)
        if not options.get("show_hydrogens", True):
            visible &= system.numbers != 1
        if not options.get("show_water", True):
            visible &= ~system.water_mask()

        if rep in RIBBON_REPRESENTATIONS:
            self._build_ribbon(vtk, system, positions, options)
            atom_mask = (
                visible & system.ligand_mask() if rep == "ribbon_ligands" else None
            )
        else:
            atom_mask = visible
        if atom_mask is not None and atom_mask.any():
            self._build_atoms(vtk, system, positions, atom_mask, options)
        if box is not None and options.get("show_box", True):
            self._build_box(vtk, box, options)
        return rep

    def _add(self, name, actor):
        self.actors[name] = actor
        try:
            self.plotter.add_actor(
                actor,
                reset_camera=False,
                name=ACTOR_PREFIX + name,
                pickable=False,
                render=False,
            )
        except TypeError:  # older PyVista without some keywords
            self.plotter.add_actor(actor, reset_camera=False, name=ACTOR_PREFIX + name)

    def _build_atoms(self, vtk, system, positions, mask, options):
        look = options["look"]
        index = np.flatnonzero(mask)
        full_to_sub = np.full(system.n_atoms, -1, dtype=np.int64)
        full_to_sub[index] = np.arange(len(index))

        pts = vtk["vtkPoints"]()
        pts.SetData(
            vtk["numpy_to_vtk"](
                np.ascontiguousarray(positions[index], dtype=np.float32), deep=True
            )
        )
        numbers = vtk["numpy_to_vtk"](
            system.numbers[index].astype(np.uint16), deep=True
        )
        numbers.SetName("Atomic Numbers")
        data = vtk["vtkDataSetAttributes"]()
        data.AddArray(numbers)
        mol = vtk["vtkMolecule"]()
        mol.Initialize(pts, data)
        radii = vtk["numpy_to_vtk"](
            atom_radii(system.numbers[index], look, options["vdw"]), deep=True
        )
        radii.SetName("radii")
        mol.GetVertexData().AddArray(radii)

        if look.get("draw_bonds", True) and len(system.bonds):
            sub = full_to_sub[system.bonds]
            keep = (sub >= 0).all(axis=1)
            orders = (
                system.bond_orders[keep] if system.bond_orders is not None else None
            )
            for k, (a, b) in enumerate(sub[keep].tolist()):
                mol.AppendBond(a, b, int(orders[k]) if orders is not None else 1)

        mapper = vtk["vtkMoleculeMapper"]()
        mapper.SetInputData(mol)
        mapper.UseBallAndStickSettings()
        mapper.SetAtomicRadiusTypeToCustomArrayRadius()
        mapper.SetAtomicRadiusArrayName("radii")
        mapper.SetAtomicRadiusScaleFactor(1.0)
        mapper.SetRenderBonds(bool(look.get("draw_bonds", True)))
        mapper.SetBondRadius(float(look.get("bond_radius", 0.1)))
        if look.get("bond_by_atom", True):
            mapper.SetBondColorModeToDiscreteByAtom()
        else:
            mapper.SetBondColorModeToSingleColor()
            mapper.SetBondColor(*[round(255 * c) for c in look["bond_color"]])
        lut = vtk["vtkLookupTable"]()
        lut.SetNumberOfTableValues(_N_ELEMENTS)
        lut.SetRange(0, _N_ELEMENTS - 1)
        for z, rgb in enumerate(options["colors"]):
            lut.SetTableValue(z, float(rgb[0]), float(rgb[1]), float(rgb[2]), 1.0)
        lut.Build()
        mapper.SetLookupTable(lut)

        actor = vtk["vtkActor"]()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetSpecular(look.get("specular", 0.2))
        prop.SetSpecularPower(look.get("specular_power", 20.0))
        prop.SetLighting(look.get("lighting", True))
        self._mol = mol
        self._mol_index = index
        self._add("atoms", actor)

    def _build_ribbon(self, vtk, system, positions, options):
        index = (
            np.flatnonzero(~system.hetatm)
            if system.hetatm is not None
            else np.arange(system.n_atoms)
        )
        chains = [system.chains[i] if system.chains else "A" for i in index.tolist()]
        # One ribbon per chain, so that each chain can have its own colour.
        order = list(dict.fromkeys(chains))
        chain_of = np.array([order.index(c) for c in chains], dtype=np.int64)
        self._ribbon_colors = ribbon_color_options(options)
        append = vtk["vtkAppendPolyData"]()
        for number in range(len(order)):
            sub = index[chain_of == number]
            if len(sub) == 0:
                continue
            poly = self._ribbon_input(vtk, system, positions, sub, options)
            ribbon = vtk["vtkProteinRibbonFilter"]()
            ribbon.SetInputData(poly)
            ribbon.SetDrawSmallMoleculesAsSpheres(False)
            ribbon.SetHelixWidth(float(options.get("ribbon_helix_width", 1.3)))
            ribbon.SetCoilWidth(float(options.get("ribbon_coil_width", 0.3)))
            ribbon.SetSubdivideFactor(int(options.get("ribbon_subdivide", 20)))
            ribbon.Update()
            self._paint_ribbon(vtk, ribbon, number)
            append.AddInputData(ribbon.GetOutput())
            self._ribbons.append((poly, sub, ribbon, number))
        append.Update()

        mapper = vtk["vtkPolyDataMapper"]()
        mapper.SetInputConnection(append.GetOutputPort())
        actor = vtk["vtkActor"]()
        actor.SetMapper(mapper)
        look = options["look"]
        actor.GetProperty().SetSpecular(look.get("specular", 0.2))
        actor.GetProperty().SetSpecularPower(look.get("specular_power", 20.0))
        self._ribbon_append = append
        self._add("ribbon", actor)

    def _ribbon_input(self, vtk, system, positions, index, options):
        """Atom polydata with the arrays vtkProteinRibbonFilter reads."""
        names = [system.names[i].strip() for i in index.tolist()]
        chains = [system.chains[i] if system.chains else "A" for i in index.tolist()]
        resids = system.resids[index]
        ss = (
            system.ss[index]
            if system.ss is not None
            else np.full(len(index), ord("c"), np.uint8)
        )
        begin, end = secondary_structure_bounds(ss, chains, resids)

        poly = vtk["vtkPolyData"]()
        pts = vtk["vtkPoints"]()
        pts.SetData(
            vtk["numpy_to_vtk"](
                np.ascontiguousarray(positions[index], dtype=np.float32), deep=True
            )
        )
        poly.SetPoints(pts)
        pd = poly.GetPointData()

        def add(arr, name):
            arr.SetName(name)
            pd.AddArray(arr)

        z = vtk["numpy_to_vtk"](
            system.numbers[index].astype(np.int64),
            deep=True,
            array_type=vtk["vtkIdTypeArray"]().GetDataType(),
        )
        add(z, "atom_type")
        names_arr = vtk["vtkStringArray"]()
        names_arr.SetNumberOfValues(len(names))
        for k, nm in enumerate(names):
            names_arr.SetValue(k, nm)
        add(names_arr, "atom_types")
        add(
            vtk["numpy_to_vtk"](
                resids.astype(np.int64),
                deep=True,
                array_type=vtk["vtkIdTypeArray"]().GetDataType(),
            ),
            "residue",
        )
        chain_codes = np.array([ord(c[:1] or " ") for c in chains], dtype=np.uint8)
        add(vtk["numpy_to_vtk"](chain_codes, deep=True), "chain")
        add(vtk["numpy_to_vtk"](ss.astype(np.uint8), deep=True), "secondary_structures")
        add(vtk["numpy_to_vtk"](begin, deep=True), "secondary_structures_begin")
        add(vtk["numpy_to_vtk"](end, deep=True), "secondary_structures_end")
        add(vtk["numpy_to_vtk"](np.zeros(len(index), np.uint8), deep=True), "ishetatm")
        add(vtk["numpy_to_vtk"](np.ones(len(index), np.uint32), deep=True), "model")
        rgb = (
            np.asarray(options["colors"])[
                np.clip(system.numbers[index], 0, _N_ELEMENTS - 1)
            ]
            * 255
        ).astype(np.uint8)
        add(vtk["numpy_to_vtk"](np.ascontiguousarray(rgb), deep=True), "rgb_colors")
        rad = np.repeat(
            options["vdw"][np.clip(system.numbers[index], 0, _N_ELEMENTS - 1)][:, None],
            3,
            axis=1,
        )
        add(
            vtk["numpy_to_vtk"](np.ascontiguousarray(rad, dtype=np.float32), deep=True),
            "radius",
        )
        return poly

    def _paint_ribbon(self, vtk, ribbon, chain_number):
        """Recolour the filter output in place (it is rebuilt on every update)."""
        scalars = ribbon.GetOutput().GetPointData().GetScalars()
        if scalars is None or scalars.GetNumberOfComponents() < 3:
            return
        rgb = vtk["vtk_to_numpy"](scalars)
        rgb[:, :3] = ribbon_rgb(rgb[:, :3], self._ribbon_colors, chain_number)
        scalars.Modified()

    def _build_box(self, vtk, box, options):
        poly = vtk["vtkPolyData"]()
        pts = vtk["vtkPoints"]()
        pts.SetData(
            vtk["numpy_to_vtk"](cell_corners(box).astype(np.float32), deep=True)
        )
        poly.SetPoints(pts)
        lines = vtk["vtkCellArray"]()
        for a, b in _BOX_EDGES:
            lines.InsertNextCell(2)
            lines.InsertCellPoint(a)
            lines.InsertCellPoint(b)
        poly.SetLines(lines)
        mapper = vtk["vtkPolyDataMapper"]()
        mapper.SetInputData(poly)
        actor = vtk["vtkActor"]()
        actor.SetMapper(mapper)
        prop = actor.GetProperty()
        prop.SetColor(*hex_to_rgb(options.get("box_color", "#FFC107")))
        prop.SetLineWidth(float(options.get("box_line_width", 2.0)))
        prop.SetLighting(False)
        self._box_points = poly
        self._add("box", actor)

    # ----------------------------------------------------------------- update

    def update(self, positions, box=None):
        """Move the existing actors to new coordinates without rebuilding them."""
        vtk = _vtk()
        if self._mol is not None:
            target = vtk["vtk_to_numpy"](self._mol.GetAtomicPositionArray().GetData())
            target[:] = positions[self._mol_index]
            self._mol.GetAtomicPositionArray().Modified()
            self._mol.Modified()
        for poly, index, ribbon, number in self._ribbons:
            target = vtk["vtk_to_numpy"](poly.GetPoints().GetData())
            target[:] = positions[index]
            poly.GetPoints().Modified()
            poly.Modified()
            ribbon.Update()
            self._paint_ribbon(vtk, ribbon, number)
        if self._ribbon_append is not None:
            self._ribbon_append.Modified()
            self._ribbon_append.Update()
        if self._box_points is not None and box is not None:
            target = vtk["vtk_to_numpy"](self._box_points.GetPoints().GetData())
            target[:] = cell_corners(box)
            self._box_points.GetPoints().Modified()
            self._box_points.Modified()

    # ----------------------------------------------------------------- state

    def is_attached(self):
        """False once the plotter was cleared by someone else."""
        if not self.actors:
            return False
        try:
            renderer = self.plotter.renderer
            return all(renderer.HasViewProp(a) for a in self.actors.values())
        except (AttributeError, RuntimeError):
            return False

    def remove(self):
        for name, actor in list(self.actors.items()):
            try:
                self.plotter.remove_actor(actor, reset_camera=False, render=False)
            except TypeError:
                self.plotter.remove_actor(actor)
            except (AttributeError, RuntimeError, KeyError):
                logger.debug("could not remove %s", name, exc_info=True)
        self.actors = {}
        self._mol = None
        self._mol_index = None
        self._ribbons = []
        self._ribbon_append = None
        self._ribbon_colors = None
        self._box_points = None
