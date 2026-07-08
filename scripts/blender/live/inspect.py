import bpy
print("=== SCENE ===")
for ob in bpy.data.objects:
    if ob.type != 'MESH':
        continue
    me = ob.data
    xs = [v.co.x for v in me.vertices]; ys = [v.co.y for v in me.vertices]; zs = [v.co.z for v in me.vertices]
    print(f"{ob.name:8s} verts={len(me.vertices):6d}  X {min(xs):7.0f}..{max(xs):7.0f}  Y {min(ys):7.0f}..{max(ys):7.0f}  Z(elev) {min(zs):6.1f}..{max(zs):6.1f} m")
