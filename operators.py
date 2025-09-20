import bpy
import logging
from bpy.props import (
    BoolProperty,
    FloatProperty,
    PointerProperty,
)
from bpy.types import (
    Operator,
    Panel,
    PropertyGroup,
)
from math import isfinite, pi
from mathutils import Vector

logger = logging.getLogger(__name__)

# ============================================================
# Properties
# ============================================================

class HGProps(PropertyGroup):
    enabled: BoolProperty(
        name="Grid Snap Enabled",
        description="Force world grid snapping during G/R/S using the grid size below.",
        default=False,
    )
    grid_size: FloatProperty(
        name="Grid Size",
        description="Snap step for live G/R/S and for Quantize to Grid (world units).",
        default=1.0,
        min=1e-6,
    )
    override_hotkeys: BoolProperty(
        name="Override G/R/S",
        description="Use Grid Snap versions of Move/Rotate/Scale when pressing G/R/S",
        default=True,
        update=lambda self, ctx: _update_override_hotkeys(ctx),
    )

# ============================================================
# Helpers
# ============================================================

def _sync_viewport_grid(context):
    """Make the drawn 3D View grid match the step."""
    step = context.scene.hg.grid_size
    wm = bpy.context.window_manager
    for win in wm.windows:
        scr = win.screen
        for area in scr.areas:
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue
                ov = space.overlay
                ov.show_floor = True
                # Blender uses the overlay scale to define INCREMENT step.
                ov.grid_scale = step
                ov.grid_subdivisions = 8
    logger.info("[GridSnap] viewport grid synced: scale=%.6f", step)

def _apply_tool_snap(context):
    """
    Baseline: enable INCREMENT snapping and keep overlay in sync.
    (We toggle Absolute per-invoke in _invoke_translate.)
    """
    scene = getattr(context, "scene", None)
    if scene is None:
        logger.debug("Skip _apply_tool_snap: no scene on context yet")
        return

    ts = getattr(scene, "tool_settings", None)
    if ts is None:
        logger.debug("Skip _apply_tool_snap: no tool_settings yet")
        return

    ts.use_snap = True
    ts.snap_elements = {'INCREMENT'}
    if hasattr(ts, "snap_angle"):
        ts.snap_angle = 15.0 * pi / 180.0

    _sync_viewport_grid(context)
    logger.info("[GridSnap] snap baseline applied")

def _set_absolute_snap(context, enable: bool):
    ts = getattr(getattr(context, "scene", None), "tool_settings", None)
    if ts and hasattr(ts, "use_snap_grid_absolute"):
        ts.use_snap_grid_absolute = bool(enable)

def _round_to_step(x, s):
    if s <= 0 or not isfinite(x):
        return x
    return round(x / s) * s

def _quantize_vector_world(vec, step):
    return Vector((_round_to_step(vec.x, step),
                   _round_to_step(vec.y, step),
                   _round_to_step(vec.z, step)))

# ---------- on-grid detection (robust) ----------

def _remainder_to_grid(x, step):
    """Absolute distance from the nearest k*step."""
    if step <= 0:
        return 0.0
    return abs(x - round(x / step) * step)

def _vec_remainder_to_grid(v, step):
    return (
        _remainder_to_grid(v.x, step),
        _remainder_to_grid(v.y, step),
        _remainder_to_grid(v.z, step),
    )

def _selection_has_any_on_grid(context, step):
    """
    True if ANY selected element lies on the world grid within an epsilon
    that scales with the step. Also returns a max remainder for diagnostics.
    OBJECT mode: check object origins.
    EDIT_MESH: check selected verts' world coordinates.
    """
    obj = context.active_object
    if not obj:
        return False

    eps = max(1e-5, step * 1e-6)  # tolerant to float noise, scales with step
    max_r = 0.0
    any_on = False

    if context.mode == 'OBJECT':
        sel = context.selected_objects or [obj]
        for ob in sel:
            w = ob.matrix_world.translation
            rx, ry, rz = _vec_remainder_to_grid(w, step)
            max_r = max(max_r, rx, ry, rz)
            if rx <= eps and ry <= eps and rz <= eps:
                any_on = True
                break
        logger.debug("[GridSnap] OBJECT any_on=%s, max remainder=%.8f (eps=%.8f)", any_on, max_r, eps)
        return any_on

    if obj.type == 'MESH' and context.mode == 'EDIT_MESH':
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        mat = obj.matrix_world
        for v in bm.verts:
            if v.select:
                w = mat @ v.co
                rx, ry, rz = _vec_remainder_to_grid(w, step)
                max_r = max(max_r, rx, ry, rz)
                if rx <= eps and ry <= eps and rz <= eps:
                    any_on = True
                    break
        logger.debug("[GridSnap] EDIT_MESH any_on=%s, max remainder=%.8f (eps=%.8f)", any_on, max_r, eps)
        return any_on

    # Fallback: just check the active object's origin
    w = obj.matrix_world.translation
    rx, ry, rz = _vec_remainder_to_grid(w, step)
    max_r = max(rx, ry, rz)
    any_on = rx <= eps and ry <= eps and rz <= eps
    logger.debug("[GridSnap] FALLBACK any_on=%s, max remainder=%.8f (eps=%.8f)", any_on, max_r, eps)
    return any_on

# ============================================================
# Transform invokers
# ============================================================

def _invoke_translate(context):
    """
    If ANY selected element is on-grid:
        - turn OFF Absolute (pure relative increments) so on-grid verts stay locked.
    Else:
        - turn ON Absolute to correct onto the grid at the start.
    """
    step = context.scene.hg.grid_size
    has_on_grid = _selection_has_any_on_grid(context, step)

    # Toggle absolute BEFORE invoking translate.
    _set_absolute_snap(context, enable=not has_on_grid)
    logger.debug("[GridSnap] Translate: has_on_grid=%s -> use_abs=%s",
                 has_on_grid, not has_on_grid)

    op = bpy.ops.transform.translate
    kw = dict(
        snap=True,
        use_proportional_edit=False,
        snap_elements={'INCREMENT'},
        snap_target='CLOSEST',
    )
    return op('INVOKE_DEFAULT', **kw)

def _invoke_rotate(context):
    op = bpy.ops.transform.rotate
    kw = dict(
        snap=True,
        use_proportional_edit=False,
        snap_elements={'INCREMENT'},
        snap_target='CLOSEST',
    )
    return op('INVOKE_DEFAULT', **kw)

def _invoke_scale(context):
    op = bpy.ops.transform.resize
    kw = dict(
        snap=True,
        use_proportional_edit=False,
        snap_elements={'INCREMENT'},
        snap_target='CLOSEST',
    )
    return op('INVOKE_DEFAULT', **kw)

# ============================================================
# Operators (G/R/S wrappers)
# ============================================================

class HG_OT_move(Operator):
    bl_idname = "hg.move"
    bl_label = "Move (Grid Snap)"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        hg = context.scene.hg
        if not hg.enabled:
            return bpy.ops.transform.translate('INVOKE_DEFAULT')
        _apply_tool_snap(context)
        return _invoke_translate(context)

class HG_OT_rotate(Operator):
    bl_idname = "hg.rotate"
    bl_label = "Rotate (Grid Snap)"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        hg = context.scene.hg
        if not hg.enabled:
            return bpy.ops.transform.rotate('INVOKE_DEFAULT')
        _apply_tool_snap(context)
        return _invoke_rotate(context)

class HG_OT_scale(Operator):
    bl_idname = "hg.scale"
    bl_label = "Scale (Grid Snap)"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        hg = context.scene.hg
        if not hg.enabled:
            return bpy.ops.transform.resize('INVOKE_DEFAULT')
        _apply_tool_snap(context)
        return _invoke_scale(context)

# ============================================================
# Quantize (object & mesh edit)
# ============================================================

class HG_OT_quantize_to_grid(Operator):
    bl_idname = "hg.quantize_to_grid"
    bl_label = "Snap Selection to Grid"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        step = context.scene.hg.grid_size
        obj = context.active_object
        if not obj:
            return {'CANCELLED'}

        if context.mode == 'OBJECT':
            for ob in context.selected_objects:
                ob.location = _quantize_vector_world(ob.location, step)
            return {'FINISHED'}

        if obj.type == 'MESH' and context.mode == 'EDIT_MESH':
            import bmesh
            bm = bmesh.from_edit_mesh(obj.data)
            mat = obj.matrix_world
            inv = mat.inverted()
            for v in bm.verts:
                if v.select:
                    world = mat @ v.co
                    v.co = inv @ _quantize_vector_world(world, step)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            return {'FINISHED'}

        return {'CANCELLED'}

# ============================================================
# Grid step (½ and ×2)
# ============================================================

class HG_OT_grid_step_down(Operator):
    bl_idname = "hg.grid_step_down"
    bl_label = "Grid: Half"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        hg = context.scene.hg
        hg.grid_size = max(1e-6, hg.grid_size * 0.5)
        _sync_viewport_grid(context)
        _apply_tool_snap(context)
        self.report({'INFO'}, f"Grid {hg.grid_size:g}")
        return {'FINISHED'}

class HG_OT_grid_step_up(Operator):
    bl_idname = "hg.grid_step_up"
    bl_label = "Grid: Double"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        hg = context.scene.hg
        hg.grid_size = min(1e6, hg.grid_size * 2.0)
        _sync_viewport_grid(context)
        _apply_tool_snap(context)
        self.report({'INFO'}, f"Grid {hg.grid_size:g}")
        return {'FINISHED'}

# ============================================================
# UI
# ============================================================

class HG_PT_panel(Panel):
    bl_label = "Grid Snap"
    bl_idname = "HG_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Grid Snap'

    def draw(self, context):
        props = context.scene.hg
        col = self.layout.column(align=True)
        col.prop(props, "enabled", toggle=True)

        row = col.row(align=True)
        row.prop(props, "grid_size")

        step_row = col.row(align=True)
        step_row.operator("hg.grid_step_down", text="½", icon='TRIA_LEFT')
        step_row.operator("hg.grid_step_up",   text="×2", icon='TRIA_RIGHT')

        col.prop(props, "override_hotkeys")
        col.separator()
        col.operator("hg.quantize_to_grid", icon='SNAP_ON')

def _menu_object(self, context):
    self.layout.separator()
    self.layout.label(text="Grid Snap")
    self.layout.operator("hg.move")
    self.layout.operator("hg.rotate")
    self.layout.operator("hg.scale")
    self.layout.operator("hg.quantize_to_grid")
    row = self.layout.row(align=True)
    row.operator("hg.grid_step_down")
    row.operator("hg.grid_step_up")

def _menu_mesh(self, context):
    self.layout.separator()
    self.layout.label(text="Grid Snap")
    self.layout.operator("hg.move")
    self.layout.operator("hg.rotate")
    self.layout.operator("hg.scale")
    self.layout.operator("hg.quantize_to_grid")
    row = self.layout.row(align=True)
    row.operator("hg.grid_step_down")
    row.operator("hg.grid_step_up")

# ============================================================
# Keymap
# ============================================================

addon_keymaps = []  # list[(km, kmi)]

def _register_keymap():
    wm = bpy.context.window_manager
    if not wm.keyconfigs.addon:
        return
    km = wm.keyconfigs.addon.keymaps.new(name='3D View', space_type='VIEW_3D')

    addon_keymaps.append((km, km.keymap_items.new("hg.move",   'G', 'PRESS')))
    addon_keymaps.append((km, km.keymap_items.new("hg.rotate", 'R', 'PRESS')))
    addon_keymaps.append((km, km.keymap_items.new("hg.scale",  'S', 'PRESS')))

    addon_keymaps.append((km, km.keymap_items.new("hg.grid_step_down", 'LEFT_BRACKET',  'PRESS')))
    addon_keymaps.append((km, km.keymap_items.new("hg.grid_step_up",   'RIGHT_BRACKET', 'PRESS')))

def _unregister_keymap():
    while addon_keymaps:
        km, kmi = addon_keymaps.pop()
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass

def _update_override_hotkeys(context):
    _unregister_keymap()
    scene = getattr(context, "scene", None)
    if scene and getattr(scene, "hg", None):
        if scene.hg.override_hotkeys:
            _register_keymap()

# ============================================================
# Registration
# ============================================================

def register_properties():
    bpy.types.Scene.hg = PointerProperty(type=HGProps)
    bpy.types.VIEW3D_MT_object.append(_menu_object)
    bpy.types.VIEW3D_MT_edit_mesh.append(_menu_mesh)

    scene = getattr(bpy.context, "scene", None)
    if scene and getattr(scene, "hg", None) and scene.hg.override_hotkeys:
        _register_keymap()

def unregister_properties():
    bpy.types.VIEW3D_MT_object.remove(_menu_object)
    bpy.types.VIEW3D_MT_edit_mesh.remove(_menu_mesh)
    _unregister_keymap()
    del bpy.types.Scene.hg

# --- Post-register init (deferred) ---
def init_after_register_async():
    def _tick():
        try:
            ctx = bpy.context
            if not getattr(ctx, "scene", None):
                return 0.2
            _sync_viewport_grid(ctx)
            _apply_tool_snap(ctx)
        except Exception as ex:
            logger.debug("Initial grid sync deferred: %s", ex)
            return 0.2
        return None
    bpy.app.timers.register(_tick, first_interval=0.1)

classes = (
    HGProps,
    HG_OT_move, HG_OT_rotate, HG_OT_scale,
    HG_OT_quantize_to_grid,
    HG_OT_grid_step_down, HG_OT_grid_step_up,
    HG_PT_panel,
)
