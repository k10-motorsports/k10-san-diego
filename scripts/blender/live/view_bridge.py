import bpy, json, traceback
LOG='/tmp/view_bridge.log'; open(LOG,'w').write('')
def log(m): open(LOG,'a').write(str(m)+'\n')
try:
    from mathutils import Vector
    target=Vector(json.load(open('/tmp/bridge_view.json'))['target'])
    m=bpy.data.objects.get("BRIDGE_MARK") or bpy.data.objects.new("BRIDGE_MARK", None)
    if m.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(m)
    m.location=target; m.empty_display_size=25.0; m.empty_display_type='PLAIN_AXES'
    for ob in bpy.data.objects: ob.select_set(False)
    m.select_set(True); bpy.context.view_layer.objects.active=m
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type=='VIEW_3D':
                region=next(r for r in area.regions if r.type=='WINDOW')
                sp=area.spaces[0]; sp.clip_end=20000.0; sp.shading.type='SOLID'
                with bpy.context.temp_override(window=win, area=area, region=region, space_data=sp):
                    bpy.ops.view3d.view_axis(type='FRONT')       # look north (+Y)
                    if sp.region_3d.view_perspective!='PERSP':
                        bpy.ops.view3d.view_persportho()          # -> perspective
                    bpy.ops.view3d.view_selected()                # frame the bridge marker
                    bpy.ops.view3d.view_orbit(type='ORBITUP')     # ~15 deg downward look up the road
                    bpy.ops.view3d.zoom(delta=-1)
                log("perspective look north up College, at the bridge")
    log("done")
except Exception:
    log(traceback.format_exc())
