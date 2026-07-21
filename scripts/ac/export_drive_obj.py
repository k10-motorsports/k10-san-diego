"""Export the prepped combined blend (blender/<slug>.blend) to data/track.obj in the LOCAL frame
(x=E, y=up, z=N) so scripts.geometry.drive_test can sweep the real drivable triangles a car collides
with. The blend is Blender Z-up ((x,z,y) from make_mesh); we write (Bx, Bz, By) to invert that.

    blender --background blender/<slug>.blend --python scripts/ac/export_drive_obj.py -- <out.obj>
"""
import bpy, sys
out = sys.argv[sys.argv.index("--") + 1]
with open(out, "w") as f:
    voff = 0
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        me = ob.data
        mw = ob.matrix_world
        f.write(f"o {ob.name}\n")
        for v in me.vertices:
            co = mw @ v.co
            f.write(f"v {co.x:.3f} {co.z:.3f} {co.y:.3f}\n")   # Blender (E,N,up) -> local (E,up,N)
        for p in me.polygons:
            vs = p.vertices
            for k in range(1, len(vs) - 1):     # fan-triangulate n-gons
                f.write(f"f {vs[0]+1+voff} {vs[k]+1+voff} {vs[k+1]+1+voff}\n")
        voff += len(me.vertices)
print("WROTE", out)
