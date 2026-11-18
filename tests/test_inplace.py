"""Run with blender --background --factory-startup --python-exit-code 1 --python tests/test_inplace.py."""

import importlib.util
import math
from pathlib import Path
import sys
import unittest

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("inplace_anim_tool", ROOT / "__init__.py")
addon = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = addon
SPEC.loader.exec_module(addon)


def activate(obj):
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def new_rig(name):
    data = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(rig)
    activate(rig)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = data.edit_bones.new("Hips")
    bone.head = (0, 0, 0)
    bone.tail = (0, 10, 2)
    bpy.ops.object.mode_set(mode='OBJECT')
    return rig


def active_curves(rig):
    ad = rig.animation_data
    if hasattr(ad, "action_slot"):
        return ad.action.layers[0].strips[0].channelbag(ad.action_slot).fcurves
    return ad.action.fcurves


def moving_rig():
    rig = new_rig("Character")
    rig.rotation_euler.x = math.pi / 2
    rig.scale = (0.01,) * 3
    hips = rig.pose.bones["Hips"]
    hips.keyframe_insert(data_path="location", frame=1)
    path = hips.path_from_id("location")
    # Duplicate times can survive imports. Updating only the first key at each
    # time leaves the other value behind when Blender deduplicates the curve.
    keys = [(1, (0, 0, 0)), (3.5, (9, -7, 60)), (7, (-4, -23, 130)),
            (7, (-4, -23, 130)), (12, (0, 11, 220))]
    for axis in range(3):
        points = active_curves(rig).find(path, index=axis).keyframe_points
        points.add(len(keys) - len(points))
        for p, (time, values) in zip(points, keys):
            p.co = (time, values[axis])
            p.interpolation = 'LINEAR'
    rig.animation_data.action.update_tag()
    return rig


def positions(rig, frames, bone=None):
    result = []
    for frame in frames:
        whole = math.floor(frame)
        bpy.context.scene.frame_set(whole, subframe=frame - whole)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        world = evaluated.matrix_world
        point = world @ evaluated.pose.bones[bone].head if bone else world.translation
        result.append(point.copy())
    return result


class InPlaceTests(unittest.TestCase):
    def setUp(self):
        props = bpy.context.scene.inplace_props
        props.target_scope = 'AUTO'
        props.method = 'FREEZE'
        props.anchor = 'START'
        props.freeze_x = props.freeze_z = props.freeze_rot_z = False
        props.freeze_y = props.create_backup = True
        self.props = props
        self.frames = [1 + i * 0.5 for i in range(23)]

    def test_bone_conversion_and_restore(self):
        for method in ('FREEZE', 'DRIFT'):
            with self.subTest(method=method):
                self.props.method = method
                rig = moving_rig()
                before = positions(rig, self.frames, "Hips")
                travel = before[-1].y - before[0].y
                self.assertGreater(abs(travel), 2)
                is_moving, stats, name = addon.analyze_motion(rig, self.props)
                self.assertTrue(is_moving)
                self.assertEqual(name, "Hips")
                self.assertAlmostEqual(stats['Y']['travel'], abs(travel), places=5)
                self.assertEqual(bpy.ops.inplace.make_in_place(), {'FINISHED'})
                after = positions(rig, self.frames, "Hips")
                for frame, src, dst in zip(self.frames, before, after):
                    expected = before[0].y if method == 'FREEZE' else src.y - travel * (frame - 1) / 11
                    self.assertAlmostEqual(dst.y, expected, places=5)
                    self.assertAlmostEqual(dst.x, src.x, places=5)
                    self.assertAlmostEqual(dst.z, src.z, places=5)
                is_moving, stats, _ = addon.analyze_motion(rig, self.props)
                self.assertFalse(is_moving)
                self.assertGreater(stats['X']['range'], 0.05)
                self.assertEqual(bpy.ops.inplace.restore_backup(), {'FINISHED'})
                restored = positions(rig, self.frames, "Hips")
                self.assertLess(max((a - b).length for a, b in zip(restored, before)), 1e-5)

    def test_scene_units_in_motion_report(self):
        rig = moving_rig()
        a, b = positions(rig, [1, 12], "Hips")
        units = bpy.context.scene.unit_settings
        previous = (units.system, units.scale_length)
        try:
            units.system, units.scale_length = 'METRIC', 0.01
            _, stats, _ = addon.analyze_motion(rig, self.props)
            self.assertAlmostEqual(stats['Y']['travel'], abs(b.y - a.y) * 0.01, places=5)
        finally:
            units.system, units.scale_length = previous

    def test_extract_horizontal_motion_on_rotated_rig(self):
        rig = moving_rig()
        before = positions(rig, self.frames, "Hips")
        self.assertEqual(bpy.ops.inplace.extract_root_bone(), {'FINISHED'})
        self.assertEqual(rig.data.bones["Hips"].parent.name, "root")
        after = positions(rig, self.frames, "Hips")
        root = positions(rig, self.frames, "root")
        self.assertLess(max((a - b).length for a, b in zip(after, before)), 1e-5)
        self.assertAlmostEqual(root[-1].y - root[0].y, before[-1].y - before[0].y, places=5)
        self.assertLess(max(p.z for p in root) - min(p.z for p in root), 1e-5)

    @unittest.skipUnless(bpy.app.version >= (4, 4, 0), "Action slots were introduced in Blender 4.4")
    def test_shared_action_slots_and_unbound_rig(self):
        a, b = new_rig("First"), new_rig("Second")
        shared = bpy.data.actions.new("Two characters")
        strip = shared.layers.new("Layer").strips.new(type='KEYFRAME')
        for rig, name, end in ((a, "A", 3.5), (b, "B", 7.5)):
            slot = shared.slots.new('OBJECT', name)
            fc = strip.channelbag(slot, ensure=True).fcurves.new(data_path="location", index=1)
            for t, y in ((1, 0.5), (12, end)):
                fc.keyframe_points.insert(t, y)
            rig.location = (1.25, 0.5, 0.75)
            ad = rig.animation_data_create()
            ad.action = shared
            ad.action_slot = slot

        self.props.target_scope = 'OBJECT'
        a.animation_data.action_slot = None
        self.assertFalse(addon.analyze_motion(a, self.props)[1]['has_curves'])
        activate(a)
        self.assertEqual(bpy.ops.inplace.make_in_place(), {'CANCELLED'})
        a.animation_data.action_slot = shared.slots[0]

        for rig in (a, b):
            activate(rig)
            self.assertFalse(bpy.ops.inplace.restore_backup.poll())
            self.assertEqual(bpy.ops.inplace.make_in_place(), {'FINISHED'})
            end = positions(rig, [12])[0]
            self.assertLess((end - Vector((1.25, 0.5, 0.75))).length, 1e-5)
            if rig == a:
                self.assertAlmostEqual(positions(b, [12])[0].y, 7.5)
        for rig, expected in ((a, 3.5), (b, 7.5)):
            activate(rig)
            self.assertEqual(bpy.ops.inplace.restore_backup(), {'FINISHED'})
            self.assertAlmostEqual(positions(rig, [12])[0].y, expected)


if __name__ == "__main__":
    addon.register()
    try:
        result = unittest.main(argv=[__file__], exit=False, verbosity=2).result
        if not result.wasSuccessful():
            raise RuntimeError("In-place animation regression tests failed")
    finally:
        addon.unregister()
