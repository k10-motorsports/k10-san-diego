import bpy
# make sure everything is visible + selectable
for ob in bpy.data.objects:
    ob.hide_set(False); ob.hide_viewport = False
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        region = next(r for r in area.regions if r.type == 'WINDOW')
        space = area.spaces[0]
        space.shading.type = 'SOLID'
        with bpy.context.temp_override(area=area, region=region, space_data=space):
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.view3d.view_axis(type='TOP')
            bpy.ops.view3d.view_all()
        print("framed TOP, ortho set")
        break
