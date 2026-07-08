import bpy, json, traceback, time
LOG='/tmp/flythrough_render.log'; open(LOG,'w').write('')
def log(m): open(LOG,'a').write(str(m)+'\n')
try:
    d=json.load(open('/tmp/flythrough.json'))
    fps, N, frames = d['fps'], d['n'], d['frames']
    sc=bpy.context.scene
    # --- camera + track-to target rig ---
    for nm in ('CAM_FLY','CAM_TARGET'):
        o=bpy.data.objects.get(nm)
        if o: bpy.data.objects.remove(o, do_unlink=True)
    cam_data=bpy.data.cameras.new('CAM_FLY'); cam_data.lens=24.0; cam_data.clip_end=30000.0
    cam=bpy.data.objects.new('CAM_FLY', cam_data); sc.collection.objects.link(cam)
    tgt=bpy.data.objects.new('CAM_TARGET', None); sc.collection.objects.link(tgt)
    con=cam.constraints.new('TRACK_TO'); con.target=tgt
    con.track_axis='TRACK_NEGATIVE_Z'; con.up_axis='UP_Y'
    # --- keyframe every frame (data already per-frame, constant speed) ---
    for i,(eye,t) in enumerate(frames):
        f=i+1
        cam.location=eye; cam.keyframe_insert('location', frame=f)
        tgt.location=t;  tgt.keyframe_insert('location', frame=f)
    for ob in (cam,tgt):
        for fc in ob.animation_data.action.fcurves:
            for kp in fc.keyframe_points: kp.interpolation='LINEAR'
    sc.camera=cam
    # --- render settings: 1280x720 mp4, workbench viewport render (fast) ---
    sc.frame_start=1; sc.frame_end=N; sc.render.fps=fps
    sc.render.resolution_x=1280; sc.render.resolution_y=720; sc.render.resolution_percentage=100
    sc.render.image_settings.file_format='FFMPEG'
    sc.render.ffmpeg.format='MPEG4'; sc.render.ffmpeg.codec='H264'
    sc.render.ffmpeg.constant_rate_factor='MEDIUM'
    sc.render.filepath='/Users/kevinconboy/Documents/K10/k10-san-diego/project/renders/loop_flythrough'
    log(f"rig built, {N} frames @ {fps}fps; rendering opengl...")
    t0=time.time()
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type=='VIEW_3D':
                area.spaces[0].shading.type='SOLID'
                region=next(r for r in area.regions if r.type=='WINDOW')
                with bpy.context.temp_override(window=win, area=area, region=region, space_data=area.spaces[0]):
                    bpy.ops.render.opengl(animation=True, view_context=False)
                break
    log(f"DONE in {time.time()-t0:.0f}s -> project/renders/loop_flythrough.mp4")
except Exception:
    log(traceback.format_exc())
