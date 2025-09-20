bl_info = {
    "name": "Simple Grid Snap",
    "author": "Mikko Ankkala (kumikumi)",
    "version": (0, 2, 1),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Grid Snap",
    "description": "Forces absolute world-grid snapping during transforms and adds quantize-to-grid tools.",
    "warning": "",
    "category": "3D View",
}

import bpy
import logging
import importlib
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
if not logger.handlers:
    h = logging.StreamHandler(stream=sys.stdout)
    fmt = logging.Formatter("[GridSnap] %(levelname)s: %(message)s")
    h.setFormatter(fmt)
    logger.addHandler(h)
logger.setLevel(logging.DEBUG)

def _report_popup(message, level={'INFO'}):
    def draw(self, context):
        for line in message.splitlines():
            self.layout.label(text=line)
    bpy.context.window_manager.popup_menu(draw, title="Grid Snap", icon='INFO')

def get_addon_modules():
    addon_dir = Path(__file__).parent
    module_files = [f.stem for f in addon_dir.glob("*.py") if f.stem != "__init__"]
    logger.debug(f"Discovered modules: {module_files}")
    return module_files

classes = []
_loaded_modules = []

def register():
    logger.info("Registering Grid Snap (package=%r, file=%r)", __package__, __file__)

    # 1) Import all modules once
    for module_name in get_addon_modules():
        full_module_name = f"{__package__}.{module_name}"
        if full_module_name not in sys.modules:
            logger.debug(f"import {full_module_name}")
            importlib.import_module(full_module_name)
        _loaded_modules.append(full_module_name)

    # 2) Reload them (hot reload)
    for full_module_name in list(_loaded_modules):
        logger.debug(f"reload {full_module_name}")
        importlib.reload(sys.modules[full_module_name])

    # 3) Collect classes
    classes.clear()
    for full_module_name in _loaded_modules:
        module = sys.modules.get(full_module_name)
        if not module:
            logger.warning(f"Module missing after reload: {full_module_name}")
            continue
        if hasattr(module, 'classes'):
            logger.debug(f"Collecting classes from {full_module_name}: {len(module.classes)} found")
            classes.extend(module.classes)
        else:
            logger.debug(f"No `classes` attr in {full_module_name}")

    # 4) Register classes
    for cls in classes:
        logger.debug(f"register_class({cls.__name__})")
        bpy.utils.register_class(cls)

    # 5) Register properties + deferred init
    try:
        from . import operators
        if hasattr(operators, "register_properties"):
            logger.debug("operators.register_properties()")
            operators.register_properties()
        else:
            logger.debug("operators.register_properties missing")

        # Defer any grid/toolsettings touching until Blender leaves _RestrictContext.
        if hasattr(operators, "init_after_register_async"):
            logger.debug("operators.init_after_register_async()")
            operators.init_after_register_async()
    except Exception as ex:
        logger.exception("Failed to register properties: %s", ex)
        _report_popup(f"Property registration failed:\n{ex}")
        raise

    _report_popup("Grid Snap enabled.\nCheck the System Console for details.\nN-panel: Grid Snap.")

def unregister():
    logger.info("Unregistering Grid Snap")

    try:
        from . import operators
        if hasattr(operators, "unregister_properties"):
            operators.unregister_properties()
    except Exception as ex:
        logger.warning("Property unregistration failed: %s", ex)

    for cls in reversed(classes):
        try:
            logger.debug(f"unregister_class({cls.__name__})")
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
