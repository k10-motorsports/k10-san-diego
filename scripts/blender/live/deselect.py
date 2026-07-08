import bpy
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(area=area, region=region, space_data=area.spaces[0]):
            bpy.ops.object.select_all(action='DESELECT')
        area.spaces[0].overlay.show_wireframes = False
        print("deselected")
        break
