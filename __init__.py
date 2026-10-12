bl_info = {
    "name": "In-Place Animation Tool",
    "author": "TIJEY Studio",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > In-Place",
    "description": "Convert Root Motion animations to In-Place animations",
    "category": "Animation",
    "doc_url": "https://github.com/tijeyoff",
    "tracker_url": "https://github.com/tijeyoff",
}

import os
import bpy
import mathutils
import bpy.utils.previews

custom_icons = None

# ------------------------------------------------------------------------
# Utility & Smart Motion Analysis
# ------------------------------------------------------------------------

def is_side_bone(bone_name):
    """Check if bone is a lateral side limb (.L, .R, _l, _r) which is never the root/hips."""
    if not bone_name:
        return False
    b_low = bone_name.lower()
    return (b_low.endswith('.l') or b_low.endswith('.r') or
            b_low.endswith('_l') or b_low.endswith('_r') or
            '.l.' in b_low or '.r.' in b_low)


def get_world_rotation_matrix(arm_obj, bone_name=None):
    """
    Returns the 3x3 orthonormal rotation matrix that transforms
    a translation vector from local coordinates into world coordinates.
    T_world = R @ v_local
    v_local = R.transposed() @ T_world

    This completely eliminates bone-axis cross coupling (such as vertical Metarig bones
    where local Z couples forward translation and height), ensuring the character's feet
    stay perfectly planted on the floor without any sinking or floating!
    """
    if not arm_obj:
        return mathutils.Matrix.Identity(3)

    # Armature object rotation in world space
    arm_rot = arm_obj.matrix_world.to_3x3().normalized()

    if not bone_name or not arm_obj.data or bone_name not in arm_obj.data.bones:
        return arm_rot

    # Bone rest matrix in armature space
    b_data = arm_obj.data.bones[bone_name]
    rest_rot = b_data.matrix_local.to_3x3()
    return arm_rot @ rest_rot


def get_bones_with_motion(arm_obj):
    """
    Scans active action curves and returns a dictionary of bones that actually
    have horizontal translation displacement in world terms.
    """
    if not arm_obj or arm_obj.type != 'ARMATURE' or not arm_obj.animation_data or not arm_obj.animation_data.action:
        return {}

    action = arm_obj.animation_data.action
    bone_curves = {}

    for fc in action.fcurves:
        if fc.data_path.startswith('pose.bones["') and '"].location' in fc.data_path:
            b_name = fc.data_path.split('pose.bones["')[1].split('"].location')[0]
            if not arm_obj.pose or b_name not in arm_obj.pose.bones:
                continue
            if b_name not in bone_curves:
                bone_curves[b_name] = {}
            bone_curves[b_name][fc.array_index] = fc

    bone_motion = {}
    for b_name, curves in bone_curves.items():
        frames = sorted(set(kp.co[0] for fc in curves.values() for kp in fc.keyframe_points))
        if len(frames) < 2:
            continue

        R = get_world_rotation_matrix(arm_obj, b_name)
        xs, ys = [], []
        for f in frames:
            v_loc = mathutils.Vector((
                curves[0].evaluate(f) if 0 in curves else 0.0,
                curves[1].evaluate(f) if 1 in curves else 0.0,
                curves[2].evaluate(f) if 2 in curves else 0.0,
            ))
            v_world = R @ v_loc
            xs.append(v_world.x)
            ys.append(v_world.y)

        rng_x = max(xs) - min(xs)
        tr_x = abs(xs[-1] - xs[0])
        rng_y = max(ys) - min(ys)
        tr_y = abs(ys[-1] - ys[0])
        h_max = max(rng_x, tr_x, rng_y, tr_y)

        if h_max > 0.005:
            bone_motion[b_name] = {
                'horiz_max': h_max,
                'max_disp': h_max,
            }

    return bone_motion


def find_root_or_hips_bone(arm_obj):
    """
    Intelligent detector:
    1. First analyzes the action curves for bones with actual translation displacement.
    2. Filters out side limbs (.L / .R).
    3. Prefers central bones (spine, root, hips, pelvis, torso).
    4. Falls back to rig structural hierarchy if no action curves exist.
    """
    if not arm_obj or arm_obj.type != 'ARMATURE' or not arm_obj.pose:
        return ""

    # 1. Action-driven detection (Data-driven, 100% accurate)
    motion_dict = get_bones_with_motion(arm_obj)
    if motion_dict:
        central_candidates = []
        side_candidates = []

        for b_name, data in motion_dict.items():
            if is_side_bone(b_name):
                side_candidates.append((b_name, data['horiz_max']))
            else:
                b_low = b_name.lower()
                bonus = 1.0
                if any(k in b_low for k in ["root", "hips", "pelvis", "spine", "torso", "master", "c_pos"]):
                    bonus = 2.0
                score = data['horiz_max'] * bonus
                central_candidates.append((b_name, score, data['horiz_max']))

        if central_candidates:
            central_candidates.sort(key=lambda x: x[1], reverse=True)
            return central_candidates[0][0]

        if side_candidates:
            side_candidates.sort(key=lambda x: x[1], reverse=True)
            return side_candidates[0][0]

    # 2. Structural hierarchy fallback (Rig naming conventions, strictly NO side bones!)
    pose_bones = arm_obj.pose.bones

    # Priority root names
    root_names = ["root", "root_motion", "b_root", "c_root", "master", "origin", "pelvis_root"]
    for b in pose_bones:
        if b.name.lower() in root_names:
            return b.name

    # Priority central hip / spine names
    hip_names = ["hips", "hip", "pelvis", "mixamorig:hips", "b_hips", "spine", "torso", "c_pos"]
    for b in pose_bones:
        if b.name.lower() in hip_names:
            return b.name

    # Fuzzy central search (ignoring .L / .R)
    for b in pose_bones:
        b_low = b.name.lower()
        if not is_side_bone(b.name):
            if any(k in b_low for k in ["hips", "pelvis", "root", "spine", "torso"]):
                return b.name

    # Unparented central bone
    unparented = [b.name for b in pose_bones if b.parent is None and not is_side_bone(b.name)]
    if unparented:
        return unparented[0]

    return pose_bones[0].name


def resolve_effective_target_bone(arm_obj, props):
    """
    Returns the bone that should actually be analyzed and modified.
    If target_scope is 'AUTO', always selects the best detected moving root/hips bone.
    If target_scope is 'BONE', respects user's bone unless empty or static.
    """
    if not arm_obj or arm_obj.type != 'ARMATURE':
        return ""

    best_detected = find_root_or_hips_bone(arm_obj)
    if props.target_scope == 'AUTO':
        return best_detected

    user_bone = props.bone_name
    if not user_bone or (arm_obj.pose and user_bone not in arm_obj.pose.bones):
        return best_detected

    motion_dict = get_bones_with_motion(arm_obj)
    user_motion = motion_dict.get(user_bone, {}).get('horiz_max', 0.0)
    best_motion = motion_dict.get(best_detected, {}).get('horiz_max', 0.0)

    if (is_side_bone(user_bone) or user_motion < 0.05) and best_motion > 0.05:
        return best_detected

    return user_bone


def analyze_motion(arm_obj, props):
    """
    Analyzes active action on armature in True World Space.
    Returns: (is_root_motion, stats_dict, target_bone_name)
    World-aligned: X=Side, Y=Forward, Z=Height
    """
    stats = {
        'X': {'travel': 0.0, 'range': 0.0},
        'Y': {'travel': 0.0, 'range': 0.0},
        'Z': {'travel': 0.0, 'range': 0.0},
        'has_curves': False,
        'horiz_max': 0.0
    }

    if not arm_obj or arm_obj.type != 'ARMATURE':
        return False, stats, ""

    anim_data = arm_obj.animation_data
    if not anim_data or not anim_data.action:
        return False, stats, ""

    action = anim_data.action
    target_bone = resolve_effective_target_bone(arm_obj, props)

    loc_curves = {}
    if target_bone and props.target_scope in {'AUTO', 'BONE', 'BOTH'}:
        b_path = f'pose.bones["{target_bone}"].location'
        for fc in action.fcurves:
            if fc.data_path == b_path and fc.array_index in (0, 1, 2):
                loc_curves[fc.array_index] = fc

    if not loc_curves and props.target_scope in {'OBJECT', 'BOTH', 'AUTO'}:
        for fc in action.fcurves:
            if fc.data_path == 'location' and fc.array_index in (0, 1, 2):
                loc_curves[fc.array_index] = fc

    if not loc_curves:
        return False, stats, target_bone

    stats['has_curves'] = True
    bone_name_for_mat = target_bone if (target_bone and arm_obj.data and target_bone in arm_obj.data.bones) else None
    R = get_world_rotation_matrix(arm_obj, bone_name_for_mat)

    frames = sorted(set(kp.co[0] for fc in loc_curves.values() for kp in fc.keyframe_points))
    if len(frames) < 2:
        return False, stats, target_bone

    xs, ys, zs = [], [], []
    for f in frames:
        v_local = mathutils.Vector((
            loc_curves[0].evaluate(f) if 0 in loc_curves else 0.0,
            loc_curves[1].evaluate(f) if 1 in loc_curves else 0.0,
            loc_curves[2].evaluate(f) if 2 in loc_curves else 0.0,
        ))
        v_world = R @ v_local
        xs.append(v_world.x)
        ys.append(v_world.y)
        zs.append(v_world.z)

    stats['X']['range'] = max(xs) - min(xs)
    stats['X']['travel'] = abs(xs[-1] - xs[0])

    stats['Y']['range'] = max(ys) - min(ys)
    stats['Y']['travel'] = abs(ys[-1] - ys[0])

    stats['Z']['range'] = max(zs) - min(zs)
    stats['Z']['travel'] = abs(zs[-1] - zs[0])

    horiz_travel = max(stats['X']['travel'], stats['Y']['travel'])
    horiz_range = max(stats['X']['range'], stats['Y']['range'])
    stats['horiz_max'] = max(horiz_travel, horiz_range)

    is_root_motion = (stats['horiz_max'] > 0.05)
    return is_root_motion, stats, target_bone


# ------------------------------------------------------------------------
# Property Group
# ------------------------------------------------------------------------

class InPlaceProperties(bpy.types.PropertyGroup):
    target_scope: bpy.props.EnumProperty(
        name="Target Scope",
        description="Choose what to modify",
        items=[
            ('AUTO', "Auto (Smart Detect)", "Automatically detects and neutralizes root motion on Bones and/or Object", 'AUTO', 0),
            ('BONE', "Target Bone", "Modify the target root/hips/spine bone", 'BONE_DATA', 1),
            ('OBJECT', "Armature Object", "Modify object-level location transform", 'OBJECT_DATA', 2),
            ('BOTH', "Both (Bone + Object)", "Neutralize both Bone and Armature Object movement", 'MOD_ARMATURE', 3),
        ],
        default='AUTO'
    )

    bone_name: bpy.props.StringProperty(
        name="Target Bone",
        description="The bone containing the root motion (e.g. spine, hips, root)",
        default=""
    )

    freeze_x: bpy.props.BoolProperty(
        name="Freeze X (Side)",
        description="Lock lateral (side-to-side) displacement in World coordinates",
        default=True
    )

    freeze_y: bpy.props.BoolProperty(
        name="Freeze Y (Forward)",
        description="Lock forward / backward displacement in World coordinates",
        default=True
    )

    freeze_z: bpy.props.BoolProperty(
        name="Freeze Z (Height)",
        description="Lock vertical displacement (leave UNCHECKED to preserve sitting down, jumping, and keep feet planted on the floor!)",
        default=False
    )

    freeze_rot_z: bpy.props.BoolProperty(
        name="Freeze Z Rotation (Yaw)",
        description="Keep character facing original heading (prevents turning while in-place)",
        default=False
    )

    method: bpy.props.EnumProperty(
        name="Method",
        description="How to neutralize motion",
        items=[
            ('FREEZE', "Absolute Lock (Freeze)", "Locks selected axis to fixed anchor (Recommended for game engines)", 'LOCKED', 0),
            ('DRIFT', "Remove Drift (Keep Sway)", "Cancels out net forward speed while preserving subtle hip oscillation", 'IPO_BEZIER', 1),
        ],
        default='FREEZE'
    )

    anchor: bpy.props.EnumProperty(
        name="Anchor Position",
        description="Anchor coordinate for the in-place motion",
        items=[
            ('ORIGIN', "Origin (0, 0, 0)", "Centers character to coordinate origin (0, 0, 0) - Game Engine Ready", 'EMPTY_AXIS', 0),
            ('START', "First Frame Position", "Locks character to its starting location in the 3D scene", 'RESTRICT_SELECT_OFF', 1),
        ],
        default='ORIGIN'
    )

    create_backup: bpy.props.BoolProperty(
        name="Backup Action",
        description="Duplicate original action before making modifications",
        default=True
    )

    last_backup_action: bpy.props.StringProperty(
        name="Last Backup Action",
        default=""
    )


# ------------------------------------------------------------------------
# Operators
# ------------------------------------------------------------------------

class INPLACE_OT_make_in_place(bpy.types.Operator):
    """Converts the active animation to In-Place"""
    bl_idname = "inplace.make_in_place"
    bl_label = "Make In-Place"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE' and obj.animation_data and obj.animation_data.action

    def execute(self, context):
        obj = context.active_object
        anim_data = obj.animation_data
        action = anim_data.action
        props = context.scene.inplace_props

        # Determine effective target bone
        target_bone = resolve_effective_target_bone(obj, props)
        props.bone_name = target_bone

        # Backup original action
        if props.create_backup:
            backup = action.copy()
            backup.name = f"{action.name}_Original_Backup"
            backup.use_fake_user = True
            props.last_backup_action = backup.name

        targets = []
        if props.target_scope in {'AUTO', 'BONE', 'BOTH'} and target_bone:
            targets.append(('BONE', target_bone))
        if props.target_scope in {'OBJECT', 'BOTH', 'AUTO'}:
            has_obj_curves = any(c.data_path == 'location' for c in action.fcurves)
            if has_obj_curves:
                targets.append(('OBJECT', None))

        if not targets:
            self.report({'ERROR'}, "No valid targets found to modify.")
            return {'CANCELLED'}

        curves_modified = 0

        for t_type, t_name in targets:
            if t_type == 'BONE':
                loc_path = f'pose.bones["{t_name}"].location'
                rot_path_euler = f'pose.bones["{t_name}"].rotation_euler'
                rot_path_quat = f'pose.bones["{t_name}"].rotation_quaternion'
                R = get_world_rotation_matrix(obj, t_name)
            else:
                loc_path = 'location'
                rot_path_euler = 'rotation_euler'
                rot_path_quat = 'rotation_quaternion'
                R = get_world_rotation_matrix(obj, None)

            R_inv = R.transposed()

            # Find or prepare location fcurves for 0, 1, 2
            fcs = {idx: next((c for c in action.fcurves if c.data_path == loc_path and c.array_index == idx), None) for idx in range(3)}
            existing_fcs = [fc for fc in fcs.values() if fc is not None]
            if not existing_fcs:
                continue

            # Find all unique keyframe frames across any location channel
            frames = sorted(set(kp.co[0] for fc in existing_fcs for kp in fc.keyframe_points))
            if not frames:
                continue

            # Ensure all 3 channels exist so projected 3D space is preserved without clipping
            for idx in range(3):
                if fcs[idx] is None:
                    fcs[idx] = action.fcurves.new(data_path=loc_path, index=idx)

            f_start = frames[0]
            f_end = frames[-1]
            span = f_end - f_start

            loc_start = mathutils.Vector((fcs[0].evaluate(f_start), fcs[1].evaluate(f_start), fcs[2].evaluate(f_start)))
            T_start = R @ loc_start

            loc_end = mathutils.Vector((fcs[0].evaluate(f_end), fcs[1].evaluate(f_end), fcs[2].evaluate(f_end)))
            T_end = R @ loc_end

            delta_world = T_end - T_start

            if props.anchor == 'ORIGIN':
                anchor_x = 0.0
                anchor_y = 0.0
                anchor_z = 0.0
            else:  # START
                anchor_x = T_start.x
                anchor_y = T_start.y
                anchor_z = T_start.z

            for f in frames:
                loc_orig = mathutils.Vector((fcs[0].evaluate(f), fcs[1].evaluate(f), fcs[2].evaluate(f)))
                T_curr = R @ loc_orig

                if props.method == 'FREEZE':
                    new_tx = anchor_x if props.freeze_x else T_curr.x
                    new_ty = anchor_y if props.freeze_y else T_curr.y
                    new_tz = anchor_z if props.freeze_z else T_curr.z
                elif props.method == 'DRIFT':
                    factor = (f - f_start) / span if span != 0 else 0.0
                    new_tx = (T_curr.x - factor * delta_world.x) if props.freeze_x else T_curr.x
                    new_ty = (T_curr.y - factor * delta_world.y) if props.freeze_y else T_curr.y
                    new_tz = (T_curr.z - factor * delta_world.z) if props.freeze_z else T_curr.z
                    if props.anchor == 'ORIGIN':
                        if props.freeze_x:
                            new_tx -= T_start.x
                        if props.freeze_y:
                            new_ty -= T_start.y
                        if props.freeze_z:
                            new_tz -= T_start.z

                T_new = mathutils.Vector((new_tx, new_ty, new_tz))
                loc_new = R_inv @ T_new

                for idx in range(3):
                    fc = fcs[idx]
                    val = loc_new[idx]
                    kp = next((k for k in fc.keyframe_points if abs(k.co[0] - f) < 0.001), None)
                    if kp:
                        diff = val - kp.co[1]
                        kp.co[1] = val
                        kp.handle_left[1] += diff
                        kp.handle_right[1] += diff
                    else:
                        fc.keyframe_points.insert(f, val)

            for idx in range(3):
                fcs[idx].update()
            curves_modified += 3

            # Optional: Freeze Z Rotation (Yaw)
            if props.freeze_rot_z:
                fc_rot = next((c for c in action.fcurves if c.data_path == rot_path_euler and c.array_index == 2), None)
                if fc_rot and len(fc_rot.keyframe_points) > 0:
                    anchor_rot = 0.0 if props.anchor == 'ORIGIN' else fc_rot.keyframe_points[0].co[1]
                    for kp in fc_rot.keyframe_points:
                        diff = anchor_rot - kp.co[1]
                        kp.co[1] = anchor_rot
                        kp.handle_left[1] += diff
                        kp.handle_right[1] += diff
                    fc_rot.update()
                    curves_modified += 1

        obj.update_tag()
        context.view_layer.update()

        target_str = f"bone '{target_bone}'" if target_bone else "object"
        self.report({'INFO'}, f"Animation converted to In-Place on {target_str} ({curves_modified} channels updated)!")
        return {'FINISHED'}


class INPLACE_OT_auto_detect_bone(bpy.types.Operator):
    """Automatically detect the root or hips bone"""
    bl_idname = "inplace.auto_detect_bone"
    bl_label = "Auto Detect Root/Hips"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        obj = context.active_object
        detected = find_root_or_hips_bone(obj)
        if detected:
            context.scene.inplace_props.bone_name = detected
            self.report({'INFO'}, f"Detected target bone: '{detected}'")
        else:
            self.report({'WARNING'}, "No suitable root or hips bone found.")
        return {'FINISHED'}


class INPLACE_OT_pick_active_bone(bpy.types.Operator):
    """Use the currently selected pose bone as target"""
    bl_idname = "inplace.pick_active_bone"
    bl_label = "Use Active Bone"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_pose_bone is not None

    def execute(self, context):
        bone = context.active_pose_bone
        context.scene.inplace_props.bone_name = bone.name
        self.report({'INFO'}, f"Selected bone: '{bone.name}'")
        return {'FINISHED'}


class INPLACE_OT_set_target_bone(bpy.types.Operator):
    """Set target bone directly from suggestions"""
    bl_idname = "inplace.set_target_bone"
    bl_label = "Select Bone"
    bl_options = {'REGISTER', 'UNDO'}

    target_bone: bpy.props.StringProperty(name="Bone Name")

    def execute(self, context):
        context.scene.inplace_props.bone_name = self.target_bone
        context.scene.inplace_props.target_scope = 'BONE'
        self.report({'INFO'}, f"Target bone set to '{self.target_bone}'")
        return {'FINISHED'}


class INPLACE_OT_restore_backup(bpy.types.Operator):
    """Restore the previous backup action"""
    bl_idname = "inplace.restore_backup"
    bl_label = "Restore Original Action"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.inplace_props
        return bool(props.last_backup_action and props.last_backup_action in bpy.data.actions)

    def execute(self, context):
        obj = context.active_object
        props = context.scene.inplace_props
        backup_act = bpy.data.actions.get(props.last_backup_action)
        if backup_act and obj and obj.animation_data:
            obj.animation_data.action = backup_act
            self.report({'INFO'}, f"Restored original action: '{backup_act.name}'")
            return {'FINISHED'}
        self.report({'WARNING'}, "Backup action not found.")
        return {'CANCELLED'}


class INPLACE_OT_extract_root_bone(bpy.types.Operator):
    """Extract forward movement into a ground-level Root bone (for Unreal / Unity root motion)"""
    bl_idname = "inplace.extract_root_bone"
    bl_label = "Extract Motion to Root Bone"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE' and obj.animation_data and obj.animation_data.action

    def execute(self, context):
        obj = context.active_object
        props = context.scene.inplace_props
        action = obj.animation_data.action

        target_bone = resolve_effective_target_bone(obj, props)
        if not target_bone:
            self.report({'ERROR'}, "Please specify a target bone (e.g. Hips or Spine).")
            return {'CANCELLED'}

        R = get_world_rotation_matrix(obj, target_bone)
        R_inv = R.transposed()

        loc_path = f'pose.bones["{target_bone}"].location'
        fcs = {idx: next((c for c in action.fcurves if c.data_path == loc_path and c.array_index == idx), None) for idx in range(3)}
        existing_fcs = [fc for fc in fcs.values() if fc is not None]
        if not existing_fcs:
            self.report({'ERROR'}, f"No location animation on bone '{target_bone}'.")
            return {'CANCELLED'}

        frames = sorted(set(kp.co[0] for fc in existing_fcs for kp in fc.keyframe_points))

        # Ensure a root bone exists
        root_name = "root"
        if root_name not in obj.pose.bones:
            curr_mode = obj.mode
            bpy.ops.object.mode_set(mode='EDIT')
            edit_bones = obj.data.edit_bones

            new_root = edit_bones.new(root_name)
            new_root.head = (0.0, 0.0, 0.0)
            new_root.tail = (0.0, 0.2, 0.0)

            # Parent target bone to root
            if target_bone in edit_bones:
                edit_bones[target_bone].parent = new_root

            bpy.ops.object.mode_set(mode=curr_mode)

        # Prepare root curves (X=0, Y=1)
        root_path = f'pose.bones["{root_name}"].location'
        root_fc_x = next((c for c in action.fcurves if c.data_path == root_path and c.array_index == 0), None)
        if not root_fc_x:
            root_fc_x = action.fcurves.new(data_path=root_path, index=0)

        root_fc_y = next((c for c in action.fcurves if c.data_path == root_path and c.array_index == 1), None)
        if not root_fc_y:
            root_fc_y = action.fcurves.new(data_path=root_path, index=1)

        root_fc_x.keyframe_points.clear()
        root_fc_y.keyframe_points.clear()

        # Transfer horizontal motion to Root, and zero out horizontal motion on target bone
        for f in frames:
            loc_orig = mathutils.Vector((
                fcs[0].evaluate(f) if fcs[0] else 0.0,
                fcs[1].evaluate(f) if fcs[1] else 0.0,
                fcs[2].evaluate(f) if fcs[2] else 0.0,
            ))
            T_world = R @ loc_orig

            # Key root bone with true horizontal displacement
            root_fc_x.keyframe_points.insert(f, T_world.x)
            root_fc_y.keyframe_points.insert(f, T_world.y)

            # Neutralize horizontal translation on target bone, PRESERVING Z!
            T_target = mathutils.Vector((0.0, 0.0, T_world.z))
            loc_target = R_inv @ T_target

            for idx in range(3):
                fc = fcs[idx]
                if fc:
                    val = loc_target[idx]
                    kp = next((k for k in fc.keyframe_points if abs(k.co[0] - f) < 0.001), None)
                    if kp:
                        diff = val - kp.co[1]
                        kp.co[1] = val
                        kp.handle_left[1] += diff
                        kp.handle_right[1] += diff
                    else:
                        fc.keyframe_points.insert(f, val)

        root_fc_x.update()
        root_fc_y.update()
        for idx in range(3):
            if fcs[idx]:
                fcs[idx].update()

        obj.update_tag()
        context.view_layer.update()
        self.report({'INFO'}, f"Root motion extracted to '{root_name}' bone!")
        return {'FINISHED'}


# ------------------------------------------------------------------------
# UI Panels
# ------------------------------------------------------------------------

class INPLACE_PT_main_panel(bpy.types.Panel):
    bl_label = "In-Place Motion Tool"
    bl_idname = "VIEW3D_PT_inplace_motion_tool"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "In-Place"

    def draw(self, context):
        layout = self.layout
        props = context.scene.inplace_props
        obj = context.active_object

        # 1. Rig & Action Overview
        box_rig = layout.box()
        col = box_rig.column(align=True)
        col.label(text="Active Rig & Animation", icon='ARMATURE_DATA')

        if not obj or obj.type != 'ARMATURE':
            col.separator(factor=0.5)
            row = col.row()
            row.alert = True
            row.label(text="Please select an Armature", icon='INFO')
            return

        col.label(text=f"Rig: {obj.name}", icon='OUTLINER_OB_ARMATURE')

        anim_data = obj.animation_data
        if not anim_data or not anim_data.action:
            col.separator(factor=0.5)
            row = col.row()
            row.alert = True
            row.label(text="No active Action on rig", icon='ACTION')
            return

        action = anim_data.action
        f_start, f_end = int(action.frame_range[0]), int(action.frame_range[1])
        col.label(text=f"Action: {action.name} ({f_start}-{f_end})", icon='ACTION')

        # 2. Motion Detection & Analysis Status
        is_rm, stats, effective_bone = analyze_motion(obj, props)

        box_status = layout.box()
        status_col = box_status.column(align=True)

        status_hdr = status_col.row()
        status_hdr.label(text="Motion Analysis", icon='VIEWZOOM')
        if effective_bone:
            status_hdr.label(text=f"Target: {effective_bone}", icon='BONE_DATA')

        if not stats['has_curves']:
            status_col.label(text="No location keys on target", icon='QUESTION')
        elif is_rm:
            alert_box = status_col.box()
            alert_box.alert = True
            row = alert_box.row()
            row.label(text="ROOT MOTION DETECTED", icon='ERROR')

            sub = status_col.column(align=True)
            sub.label(text=f"ΔX (Side): {stats['X']['travel']:.2f}m (Rng: {stats['X']['range']:.2f}m)")
            sub.label(text=f"ΔY (Forward): {stats['Y']['travel']:.2f}m (Rng: {stats['Y']['range']:.2f}m)")
            sub.label(text=f"ΔZ (Height): {stats['Z']['travel']:.2f}m (Rng: {stats['Z']['range']:.2f}m)")
        else:
            ok_box = status_col.box()
            row = ok_box.row()
            row.label(text="IN-PLACE ANIMATION", icon='CHECKMARK')

            sub = status_col.column(align=True)
            sub.label(text=f"X: {stats['X']['range']:.2f}m | Y: {stats['Y']['range']:.2f}m | Z: {stats['Z']['range']:.2f}m")

        # Quick bone suggestions if there are moving bones
        motion_dict = get_bones_with_motion(obj)
        moving_bones = [(b, d['horiz_max']) for b, d in motion_dict.items() if d['horiz_max'] > 0.05 and not is_side_bone(b)]
        if moving_bones:
            row_sug = status_col.row(align=True)
            row_sug.label(text="Moving Bone:", icon='FORWARD')
            for b_name, tr in moving_bones[:2]:
                op = row_sug.operator("inplace.set_target_bone", text=f"{b_name} ({tr:.2f}m)")
                op.target_bone = b_name

        # 3. Target Scope & Bone
        box_target = layout.box()
        t_col = box_target.column(align=True)
        t_col.label(text="Target Settings", icon='PREFERENCES')
        t_col.prop(props, "target_scope", text="Scope")

        if props.target_scope in {'BONE', 'AUTO'}:
            row = t_col.row(align=True)
            row.prop_search(props, "bone_name", obj.pose, "bones", text="Bone", icon='BONE_DATA')

            row_btns = t_col.row(align=True)
            row_btns.operator("inplace.auto_detect_bone", text="Auto Detect", icon='AUTO')
            row_btns.operator("inplace.pick_active_bone", text="Active Bone", icon='RESTRICT_SELECT_OFF')

        # 4. Conversion Settings
        box_opts = layout.box()
        opts_col = box_opts.column(align=True)
        opts_col.label(text="Freeze Options", icon='CON_LOCKTRACK')

        row_axes = opts_col.row(align=True)
        row_axes.prop(props, "freeze_x", text="X (Side)", toggle=True)
        row_axes.prop(props, "freeze_y", text="Y (Fwd)", toggle=True)
        row_axes.prop(props, "freeze_z", text="Z (Height)", toggle=True)

        opts_col.prop(props, "freeze_rot_z", text="Freeze Z Rotation (Yaw)")

        opts_col.separator(factor=0.5)
        opts_col.prop(props, "method", text="Mode")
        opts_col.prop(props, "anchor", text="Anchor")
        opts_col.prop(props, "create_backup", text="Backup Action")

        # 5. Conversion Action
        layout.separator(factor=0.5)
        btn_col = layout.column(align=True)
        btn_col.scale_y = 1.6
        btn_col.operator("inplace.make_in_place", text="Make In-Place", icon='PLAY')

        # Restore button if backup exists
        if props.last_backup_action and props.last_backup_action in bpy.data.actions:
            row_restore = layout.row(align=True)
            row_restore.scale_y = 1.2
            row_restore.operator("inplace.restore_backup", text=f"↩ Restore Backup ({props.last_backup_action})", icon='LOOP_BACK')

        # Advanced Game Engine helper
        box_adv = layout.box()
        adv_col = box_adv.column(align=True)
        adv_col.label(text="Game Engine Tools", icon='GHOST_ENABLED')
        adv_col.operator("inplace.extract_root_bone", text="Extract Motion to Root Bone", icon='DRIVER')


class INPLACE_PT_info_panel(bpy.types.Panel):
    """Information panel showing addon metadata and author links"""
    bl_label = "Information"
    bl_idname = "VIEW3D_PT_inplace_info"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "In-Place"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(text="", icon="INFO")

    def draw(self, context):
        global custom_icons
        layout = self.layout

        # 1. Tool Info & Update Section
        box_info = layout.box()
        col_info = box_info.column(align=True)
        col_info.label(text="In-Place Animation Tool v1.0", icon='TOOL_SETTINGS')
        col_info.label(text="Author: TIJEY Studio", icon='USER')

        col_info.separator(factor=0.5)
        row_ext = col_info.row(align=True)
        row_ext.scale_y = 1.25
        row_ext.operator("wm.url_open", text="Check Updates (Extensions)", icon='FILE_REFRESH').url = "https://extensions.blender.org/add-ons/inplace_anim_tool/"

        # 2. Distinct GitHub Developer Card
        box_gh = layout.box()
        col_gh = box_gh.column(align=True)

        gh_icon_id = 0
        if custom_icons and "github" in custom_icons:
            gh_icon_id = custom_icons["github"].icon_id

        row_gh = col_gh.row(align=True)
        row_gh.scale_y = 1.35
        if gh_icon_id:
            row_gh.operator("wm.url_open", text="GitHub: @tijeyoff", icon_value=gh_icon_id).url = "https://github.com/tijeyoff"
        else:
            row_gh.operator("wm.url_open", text="GitHub: @tijeyoff", icon='URL').url = "https://github.com/tijeyoff"


# ------------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------------

classes = (
    InPlaceProperties,
    INPLACE_OT_make_in_place,
    INPLACE_OT_auto_detect_bone,
    INPLACE_OT_pick_active_bone,
    INPLACE_OT_set_target_bone,
    INPLACE_OT_restore_backup,
    INPLACE_OT_extract_root_bone,
    INPLACE_PT_main_panel,
    INPLACE_PT_info_panel,
)

def register():
    global custom_icons
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.inplace_props = bpy.props.PointerProperty(type=InPlaceProperties)

    # Load custom preview icons
    custom_icons = bpy.utils.previews.new()
    icons_dir = os.path.join(os.path.dirname(__file__), "icons")
    gh_icon_path = os.path.join(icons_dir, "github.png")
    if os.path.exists(gh_icon_path):
        custom_icons.load("github", gh_icon_path, 'IMAGE')

def unregister():
    global custom_icons
    if custom_icons:
        bpy.utils.previews.remove(custom_icons)
        custom_icons = None
    if hasattr(bpy.types.Scene, "inplace_props"):
        del bpy.types.Scene.inplace_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
