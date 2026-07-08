import bpy, json, time
LOG='/tmp/flythrough_render.log'; open(LOG,'w').write('')
def log(m): open(LOG,'a').write(str(m)+'\n'); print(m)
d=json.load(open('/tmp/flythrough.json'))
fps,N,frames=d['fps'],d['n'],d['frames']
sc=bpy.context.scene
cam_data=bpy.data.cameras.new('CAM_FLY'); cam_data.lens=24.0; cam_data.clip_end=30000.0
cam=bpy.data.objects.new('CAM_FLY',cam_data); sc.collection.objects.link(cam)
tgt=bpy.data.objects.new('CAM_TARGET',None); sc.collection.objects.link(tgt)
con=cam.constraints.new('TRACK_TO'); con.target=tgt; con.track_axis='TRACK_NEGATIVE_Z'; con.up_axis='UP_Y'
for i,(eye,t) in enumerate(frames):
    f=i+1
    cam.location=eye; cam.keyframe_insert('location',frame=f)
    tgt.location=t;  tgt.keyframe_insert('location',frame=f)
for ob in (cam,tgt):
    for fc in ob.animation_data.action.fcurves:
        for kp in fc.keyframe_points: kp.interpolation='LINEAR'
sc.camera=cam
# sun so EEVEE isn't black
sun=bpy.data.objects.new('SUN',bpy.data.lights.new('SUN','SUN')); sc.collection.objects.link(sun)
sun.data.energy=3.0; sun.rotation_euler=(0.6,0.2,0.5)
sc.frame_start=1; sc.frame_end=N; sc.render.fps=fps
sc.render.resolution_x=1280; sc.render.resolution_y=720
try: sc.render.engine='BLENDER_EEVEE_NEXT'
except Exception: sc.render.engine='BLENDER_EEVEE'
sc.eevee.taa_render_samples=8
sc.render.image_settings.file_format='FFMPEG'
sc.render.ffmpeg.format='MPEG4'; sc.render.ffmpeg.codec='H264'; sc.render.ffmpeg.constant_rate_factor='MEDIUM'
sc.render.filepath='/Users/kevinconboy/Documents/K10/k10-san-diego/project/renders/loop_flythrough'
log(f"rendering {N} frames @ {fps}fps, engine {sc.render.engine}")
t0=time.time()
bpy.ops.render.render(animation=True)
log(f"DONE in {time.time()-t0:.0f}s")
