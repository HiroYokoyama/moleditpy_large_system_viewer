"""Large System Viewer: fast display of proteins, MD boxes and trajectories in MoleditPy.

Opens PDB, GRO, XYZ and SDF files of any size, draws them with VTK's impostor molecule
mapper and protein ribbons, and plays multi-frame files.  See DESIGN.md in the
repository for the design.
"""

import logging

logger = logging.getLogger(__name__)

PLUGIN_NAME = "Large System Viewer"
PLUGIN_VERSION = "0.1.0"
PLUGIN_AUTHOR = "HiroYokoyama"
PLUGIN_DESCRIPTION = (
    "Fast display of large systems: proteins (ribbons), solvated MD boxes and "
    "multi-frame PDB/GRO/XYZ/SDF trajectories with playback, periodic boxes and a "
    "Large System (Fast) 3D style."
)
PLUGIN_CATEGORY = "Visualization"
PLUGIN_TAGS = ["Visualization", "File"]
PLUGIN_DEPENDENCIES = []
PLUGIN_OPTIONAL_DEPENDENCIES = []
PLUGIN_SUPPORTED_MOLEDITPY_VERSION = ">=5.0.0, <6.0.0"

EXTENSIONS = (".pdb", ".ent", ".gro", ".xyz", ".sdf")
OPENER_PRIORITY = 10

_controller = None


def initialize(context):
    global _controller
    try:
        from . import host
        from .controller import STYLE_NAME, Controller
    except ImportError as exc:  # numpy / host modules missing
        logger.error("Large System Viewer is not available: %s", exc)
        return

    controller = Controller(context)
    _controller = controller

    context.register_3d_style(STYLE_NAME, controller.draw)
    for ext in EXTENSIONS:
        context.register_file_opener(
            ext, controller.open_file, priority=OPENER_PRIORITY
        )
    context.register_drop_handler(controller.drop_file, priority=OPENER_PRIORITY)
    context.add_menu_action(
        "View/Large System Viewer Panel",
        controller.show_panel,
        text="Large System Viewer Panel",
    )
    context.register_save_handler(controller.state)
    context.register_load_handler(controller.restore)
    context.register_document_reset_handler(controller.document_reset)

    mw = context.get_main_window()
    host.install_molecule_hook(mw, controller.on_new_molecule)
    host.install_export_guard(mw, controller.export_blocked)
