import bpy, json
from mathutils import Vector
sp=json.load(open('/tmp/still_view.json')); eye=Vector(sp['eye']); tgt=Vector(sp['target'])
sc=bpy.context.scene
cam=bpy.data.objects.new('STILLCAM',bpy.data.cameras.new('STILLCAM')); cam.data.lens=35; cam.data.clip_end=30000
sc.collection.objects.link(cam); cam.location=eye
d=(tgt-eye); cam.rotation_euler=(-d).to_track_quat('Z','Y').to_euler()
sc.camera=cam
sun=bpy.data.objects.new('SUN',bpy.data.lights.new('SUN','SUN')); sc.collection.objects.link(sun)
sun.data.energy=3.0; sun.rotation_euler=(0.6,0.2,0.5)
sc.render.engine='BLENDER_EEVEE_NEXT'; sc.eevee.taa_render_samples=16
sc.render.resolution_x=1280; sc.render.resolution_y=720
sc.render.image_settings.file_format='PNG'
sc.render.filepath='/tmp/bridge_still2.png'
bpy.ops.render.render(write_still=True)
print("wrote /tmp/bridge_still2.png")
