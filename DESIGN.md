# Large System Viewer Design

Status: v0.1.0 (2026-11-26)

A MoleditPy plugin that displays large systems (proteins, solvated MD boxes) and plays
trajectories without the cost of the standard 3D styles. It needs no changes to the host
and no extra pip packages. It supports the pip-installed MoleditPy only; the frozen
exe/app builds are out of scope.

## 1. Background and approach

The host's standard styles expand every atom and bond into real meshes (`glyph()` +
`tube()`). A 9,000-atom water box takes 8.8 s to draw, 25 s to redraw and +1.4 GB of
memory (software GL), and every frame change is a full rebuild.

VTK already ships impostor molecule rendering (`vtkMoleculeMapper` →
`vtkOpenGLMoleculeMapper`, spheres and cylinders drawn in shaders) and protein ribbons
(`vtkProteinRibbonFilter`). The plugin puts these into the host's existing 3D view
(the PyVista plotter).

Measured on an Intel integrated GPU, water box, ball-and-stick, 1280x800:

| Atoms | Build | First draw | Frame update | Rotation frame |
|---|---|---|---|---|
| 9,000 | 0.01 s | 0.02 s | 13 ms | 6 ms |
| 100,000 | 0.17 s | 0.05 s | 50 ms | 11 ms |
| 1,000,000 | 1.5 s | 0.35 s | 313 ms | 48 ms |

A ribbon for a 2,501-atom protein takes 0.03 s. Building an RDKit molecule of 100,000
atoms takes 0.4 s.

Decisions:

- No custom GL and no Rust. Only existing VTK components and numpy.
- The 3D widget is not replaced. The plugin draws into the host's plotter, so 3D image
  export, background and camera keep working.
- Frames are not written into the host molecule while playing. They are committed only
  when playback pauses or the slider is released.

## 2. Layout

```
large_system_viewer/
  __init__.py   metadata, initialize(): openers, drop handler, 3D style, panel, session state
  elements.py   element table: symbol <-> atomic number, covalent radii, element guessing
  formats.py    PDB / GRO / XYZ / SDF readers: frame index and lazy frame reading
  bonds.py      distance-based bond perception (numpy cell grid)
  system.py     System (topology) and conversion to and from RDKit molecules
  render.py     VTK scene: impostor atoms, ribbons, position-only updates
  session.py    the displayed system and its frames, committing a frame to the host
  host.py       every touch point with host internals (loading, styles, draw wrapper)
  panel.py      right-hand dock panel (representation, playback, settings)
  settings.py   plugin settings (settings.json)
```

Dependencies on host internals are confined to `host.py`, so a host refactor has one
place to fix.

## 3. Data flow

### 3.1 Opening files

Handled extensions: `.pdb` `.ent` `.gro` `.xyz` `.sdf`, through `register_file_opener`
(priority 10) and `register_drop_handler` (priority 10).

1. Count the atoms of the first frame and index the frame start offsets of the whole file.
2. Route:
   - Single frame at or below the auto-switch threshold:
     - `.xyz` `.sdf`: return `False` so the host loader takes it (drag and drop, command
       line). When opened from the plugin's own Import menu entry, call the host loader
       directly.
     - `.pdb` `.ent`: read with RDKit's PDB parser and hand it to the host's 3D Viewer
       Mode (bond orders come from CONECT records).
     - `.gro`: read with the plugin's reader and hand it to the host.
   - Otherwise (large, or more than one frame): the plugin starts a session.
3. Session modes:
   - **Hybrid** (atoms at or below the host-link limit): build an RDKit molecule from the
     first frame and hand it to the host's 3D Viewer Mode. Measurement, export and other
     plugins work on the current frame.
   - **View-only** (above the limit): the host molecule stays empty and the plugin draws
     into the plotter directly.

An SDF file is treated as frames only when every record has the same element sequence;
otherwise the host loads it.

### 3.2 Existing structures (style switching)

- `Large System (Fast)` is registered in the Style menu and can be chosen for any structure.
- `view_3d_manager.draw_molecule_3d` is wrapped so the atom count is checked no matter how
  the structure was loaded:
  - Above the threshold under a standard style, switch to Fast automatically and remember
    the previous style.
  - After an automatic switch, a small structure switches the style back. A style the user
    chose by hand is never switched back.
- A molecule with several conformers plays its conformers as frames.

### 3.3 Frames

| State | Host molecule | Drawing |
|---|---|---|
| Playing | untouched | overwrite the VTK coordinate array and `plotter.render()` |
| Paused / slider released | conformer coordinates and `atom_positions_3d` updated | normal redraw |

- A commit is a view change: it adds no undo step and does not mark the project modified.
- Measurement mode and 3D editing are switched off while playing.
- Playback also works under other styles. Each frame is then committed and drawn by the
  host (meant for small systems; the next frame waits for the draw, so nothing queues).
- If the host changes the coordinates while paused (3D editing, for example), the session
  detaches from the trajectory and playback is disabled.

## 4. Rendering

- Representations: Ball and Stick / Spacefill / Licorice / Ribbon / Ribbon + Ligands.
- Atoms and bonds use `vtkMoleculeMapper`. Colours come from the host's CPK table,
  including user overrides, through a lookup table.
- Ribbons feed `vtkProteinRibbonFilter` with point data built from PDB/GRO (atom names,
  residue numbers, chains, secondary structure, HETATM flags). Secondary structure comes
  from PDB HELIX / SHEET records; files without them (typical MD output) draw as coil.
- Ribbon + Ligands draws the protein as a ribbon and non-water HETATM as ball-and-stick.
- Position updates overwrite the `vtkMolecule` coordinate array with numpy and call
  `Modified()`. Ribbons are rebuilt for the new frame.
- Background, axes and projection are left to the host's `apply_3d_settings(redraw=False)`.

## 5. Reading

- Frame index: the file is memory-mapped. PDB frames start at `MODEL` records (or follow
  `END` records). XYZ and GRO count newlines with numpy in 64 MB chunks and keep only the
  frame boundaries, so huge trajectories are never loaded whole.
- Coordinate parsing: PDB and GRO coordinates are fixed width (`%8.3f`). The 24 bytes after
  each line start are gathered with numpy and converted arithmetically in one pass, with no
  Python loop per line. XYZ converts whitespace tokens in bulk.
- Elements: PDB columns 77-78, else guessed from the atom name. GRO guesses from the atom
  name and, for ions, the residue name.
- Bonds: distance test on the first frame (`d < r_i + r_j + 0.4 Å`, each hydrogen keeps only
  its nearest partner) plus PDB CONECT records. Bonds are not recomputed for later frames.
  Periodic boundaries are ignored.

## 6. UI (right-hand dock panel)

- Source: file name, atoms, frames, mode (hybrid / view-only / conformers).
- Display: representation, a button that switches to the Fast style.
- Playback: first / previous / play-pause / next / last, slider, frame number, fps, stride, loop.
- Settings: auto-switch (on/off, threshold), host-link atom limit.

The panel opens when a session starts and can be opened from the View menu.

## 7. Persistence

- Plugin settings live in `settings.json` beside the package.
- A project (`.pmeprj`) stores the session's file path, frame number and representation for
  sessions opened from a file. On load the session is restored when the file exists and the
  atom count matches. The trajectory itself is not stored.

## 8. Known limitations

- Atom labels (index, element symbol and so on), aromatic rings, bond-order display and
  chirality labels are not drawn in the Fast style.
- STL / OBJ export is empty: impostor rendering has no mesh.
- PDB serial numbers have five columns, so CONECT records of PDB files above 99,999 atoms are
  ignored and only the distance test is used.
- Bonds are fixed from the first frame. Bonds across periodic boundaries are not drawn.
- In view-only mode host measurement, MOL/XYZ export and other plugins are unavailable.
- Binary trajectories (XTC, DCD) and secondary-structure assignment for MD output (DSSP)
  are not supported.
