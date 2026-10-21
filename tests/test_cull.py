# SPDX-FileCopyrightText: 2026 AzureBlure
# SPDX-License-Identifier: MIT
"""
hython tests for the Cull Geometry HDAs.

    hython tests/test_cull.py [path/to/cull_geometry.hda]

Default camera (/obj/cam1 at origin, looking down -Z, focal 50, aperture
41.4214): horizontal half-width at depth d is ~0.414 * d.
"""
import os
import sys

import hou

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HDA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "otls", "cull_geometry.hda")

FAILURES = []


def check(name, actual, expected):
    ok = actual == expected
    print("%s %-55s got %r expected %r" % ("PASS" if ok else "FAIL", name, actual, expected))
    if not ok:
        FAILURES.append(name)


def points_sop(parent, name, positions):
    sop = parent.createNode("python", name)
    sop.parm("python").set(
        "geo = hou.pwd().geometry()\n"
        "geo.addAttrib(hou.attribType.Point, 'id', 0)\n"
        "for i, p in enumerate(%r):\n"
        "    pt = geo.createPoint(); pt.setPosition(p); pt.setAttribValue('id', i)\n" % (positions,))
    return sop


def ids(node, owner="point"):
    geo = node.geometry()
    if owner == "point":
        return sorted(p.attribValue("id") for p in geo.points())
    if geo.findPrimAttrib("id"):
        return sorted(p.attribValue("id") for p in geo.prims())
    return sorted(round(p.points()[0].position()[0], 2) for p in geo.prims())  # packed: by x


def errors(node):
    try:
        node.cook(force=True)
    except hou.OperationFailed:
        pass
    return node.errors()


def main():
    hou.hda.installFile(HDA)
    hou.hipFile.clear(suppress_save_prompt=True)
    hou.setFrame(1)

    cam = hou.node("/obj").createNode("cam", "cam1")
    geo = hou.node("/obj").createNode("geo", "geo1")

    # --- Points --------------------------------------------------------------
    pts = points_sop(geo, "pts", [
        (0, 0, -5),      # 0 visible
        (0, 0, 5),       # 1 behind camera
        (10, 0, -5),     # 2 right of frustum (half width ~2.07)
        (0, 0, -2000),   # 3 beyond far
        (0, 0, -0.05),   # 4 closer than near
        (2.3, 0, -5),    # 5 just outside, inside with 20% overscan
        (0, 1.0, -5),    # 6 visible (half height ~1.16 at 16:9)
        (0, 1.5, -5),    # 7 above frustum
    ])
    cull = geo.createNode("cull_geometry::1.0", "cull_points")
    cull.setFirstInput(pts)
    cull.parm("camera").set("/obj/cam1")
    check("points: no errors", errors(cull), ())
    check("points: no warnings", cull.warnings(), ())
    check("points: delete outside", ids(cull), [0, 6])
    check("points: time dependent in Current Frame", cull.node("camera_data").isTimeDependent(), True)

    cull.parm("overscan").set(0.2)
    check("points: overscan 0.2", ids(cull), [0, 5, 6])
    cull.parm("overscan").set(0.0)

    cull.parm("zfar").set(3.0)
    check("points: z far 3", ids(cull), [])
    cull.parm("zfar").set(1000.0)

    cull.parm("output_mode").set(1)
    check("points: delete inside", ids(cull), [1, 2, 3, 4, 5, 7])
    cull.parm("output_mode").set(2)
    group = cull.geometry().findPointGroup("cull_candidates")
    check("points: group only keeps all", len(cull.geometry().points()), 8)
    check("points: group contents", sorted(p.attribValue("id") for p in group.points()) if group else None,
          [1, 2, 3, 4, 5, 7])
    check("points: temp attribs removed",
          [a.name() for a in cull.geometry().pointAttribs() if a.name().startswith("__")], [])
    cull.parm("output_mode").set(0)

    # --- Object-level transform of the geometry container --------------------
    geo.parm("tx").set(-10)   # point 2 (x=10) moves to world x=0
    check("points: container object transform", ids(cull), [2])
    geo.parm("tx").set(0)

    # --- Animated camera + viewing range --------------------------------------
    cam.parm("tx").setExpression("($F - 1) * 0.5")   # x = 0 at f1, 9.5 at f20
    check("range: current frame (f1)", ids(cull), [0, 6])
    hou.setFrame(20)
    check("range: current frame (f20)", ids(cull), [2])
    cull.parm("view_range").set(1)
    cull.parm("single_frame").set(1)
    check("range: single frame 1 while at f20", ids(cull), [0, 6])
    check("range: single frame not time dependent", cull.node("camera_data").isTimeDependent(), False)
    cull.parm("view_range").set(2)
    cull.parmTuple("frange").deleteAllKeyframes()
    cull.parmTuple("frange").set((1, 20, 1))
    check("range: frame range 1-20 union", ids(cull), [0, 2, 5, 6])
    check("range: frame range not time dependent", cull.node("camera_data").isTimeDependent(), False)
    cull.parm("view_range").set(0)
    cam.parm("tx").deleteAllKeyframes()
    cam.parm("tx").set(0)
    hou.setFrame(1)

    # --- Primitives: centroid vs bounding box ---------------------------------
    grid = geo.createNode("grid", "big_grid")  # 1 big poly straddling the frustum edge
    grid.parm("type").set("poly")
    grid.parm("rows").set(2)
    grid.parm("cols").set(2)
    grid.parm("sizex").set(4)
    grid.parm("sizey").set(4)
    grid.parm("orient").set(0)  # XY plane
    grid.parm("tx").set(3.5)    # x in [1.5, 5.5], centroid at 3.5 (outside at d=5)
    grid.parm("tz").set(-5)
    cull_prims = geo.createNode("cull_geometry::1.0", "cull_prims")
    cull_prims.setFirstInput(grid)
    cull_prims.parm("camera").set("/obj/cam1")
    cull_prims.parm("run_over_prims").set(1)
    check("prims: bbox keeps straddling prim", len(cull_prims.geometry().prims()), 1)
    cull_prims.parm("bound_test").set(0)
    check("prims: centroid culls straddling prim", len(cull_prims.geometry().prims()), 0)

    # --- Connectivity -----------------------------------------------------------
    # Grid of 4x4 points on one connected mesh crossing the frustum edge.
    mesh = geo.createNode("grid", "mesh")
    mesh.parm("orient").set(0)
    mesh.parm("sizex").set(6)
    mesh.parm("tx").set(3)  # x in [0, 6]
    mesh.parm("tz").set(-5)
    cull_conn = geo.createNode("cull_geometry::1.0", "cull_conn")
    cull_conn.setFirstInput(mesh)
    cull_conn.parm("camera").set("/obj/cam1")
    total = len(mesh.geometry().points())
    partial = len(cull_conn.geometry().points())
    check("connectivity off: mesh partially culled", 0 < partial < total, True)
    cull_conn.parm("use_connectivity").set(1)
    check("connectivity on: whole piece kept", len(cull_conn.geometry().points()), total)

    # --- Packed primitives -------------------------------------------------------
    copy_pts = points_sop(geo, "copy_pts", [(0, 0, -5), (6, 0, -5), (2.6, 0, -5)])
    box = geo.createNode("box", "box")
    copy = geo.createNode("copytopoints::2.0", "copy")
    copy.setInput(0, box)
    copy.setInput(1, copy_pts)
    copy.parm("pack").set(1)
    cull_packed = geo.createNode("cull_geometry::1.0", "cull_packed")
    cull_packed.setFirstInput(copy)
    cull_packed.parm("camera").set("/obj/cam1")
    cull_packed.parm("is_packed").set(1)
    check("packed: bbox keeps edge instance", ids(cull_packed, "prim"), [0.0, 2.6])
    cull_packed.parm("bound_test").set(0)
    check("packed: centroid", ids(cull_packed, "prim"), [0.0])
    check("packed: still packed",
          set(p.type() for p in cull_packed.geometry().prims()), {hou.primType.PackedGeometry})

    # --- Heightfields ------------------------------------------------------------
    # cam_down at y=10 looking straight down: visible ground is roughly
    # |x| <= 4.14, |z| <= 2.33 for a flat heightfield at y=0.
    cam_down = hou.node("/obj").createNode("cam", "cam_down")
    cam_down.parm("ty").set(10)
    cam_down.parm("rx").set(-90)
    terrain = geo.createNode("heightfield", "terrain")
    terrain.parm("sizex").set(100)
    terrain.parm("sizey").set(100)
    terrain.parm("gridspacing").set(1)
    terrain.parm("initialmask").set(0.5)
    cull_hf = geo.createNode("cull_geometry::1.0", "cull_hf")
    cull_hf.setFirstInput(terrain)
    cull_hf.parm("camera").set("/obj/cam_down")
    cull_hf.parm("is_heightfield").set(1)

    def layer(node, name):
        for prim in node.geometry().prims():
            if prim.type() == hou.primType.Volume and prim.attribValue("name") == name:
                return prim
        return None

    def sample(node, name, pos):
        return round(layer(node, name).sample(hou.Vector3(pos)), 3)

    def layer_names(node):
        return sorted(p.attribValue("name") for p in node.geometry().prims())

    def footprint(node):
        b = layer(node, "height").intrinsicValue("bounds")
        return (b[1] - b[0], b[5] - b[4])

    check("hf mask: no errors", errors(cull_hf), ())
    check("hf mask: no warnings", cull_hf.warnings(), ())
    check("hf mask: values (centre, inside edge, outside x, far)",
          [sample(cull_hf, "mask", p) for p in ((0, 0, 0), (3.5, 0, 0), (6, 0, 0), (20, 0, 20))],
          [1.0, 1.0, 0.0, 0.0])
    check("hf mask: temp layers / attribs removed",
          (layer_names(cull_hf), cull_hf.geometry().findGlobalAttrib("__cull_any")),
          (["height", "mask"], None))

    cull_hf.parm("output_mode").set(1)
    check("hf delete inside: mask inverted",
          [sample(cull_hf, "mask", p) for p in ((0, 0, 0), (20, 0, 20))], [0.0, 1.0])

    cull_hf.parm("output_mode").set(2)
    check("hf group only: cull_candidates layer",
          [sample(cull_hf, "cull_candidates", p) for p in ((0, 0, 0), (20, 0, 20))], [0.0, 1.0])
    check("hf group only: mask untouched", sample(cull_hf, "mask", (0, 0, 0)), 0.5)
    cull_hf.parm("output_mode").set(0)

    cull_hf.parm("hf_output").set(1)
    size_x, size_z = footprint(cull_hf)
    print("     crop footprint:", size_x, size_z)
    check("hf crop: cropped to visible region (x ~8.3, z ~4.7 + pad)",
          (8 <= size_x <= 12, 4 <= size_z <= 8), (True, True))
    check("hf crop: mask written (visible centre)", sample(cull_hf, "mask", (0, 0, 0)), 1.0)
    check("hf crop: temp layers removed", layer_names(cull_hf), ["height", "mask"])
    cull_hf.parm("bound_test").set(0)
    check("hf crop: centroid no larger than footprint test", footprint(cull_hf)[0] <= size_x, True)
    cull_hf.parm("bound_test").set(1)

    # Camera spun 45 degrees: the visible footprint runs diagonally, so the
    # cropped rectangle holds hidden voxels and the mask marks them. (2.5, 2.5)
    # and (2.5, -2.5) lie along the footprint's long and short axes at 3.54.
    cam_down.parm("ry").set(45)
    check("hf crop diagonal: mask marks hidden voxels inside the crop",
          sorted(sample(cull_hf, "mask", p) for p in ((2.5, 0, 2.5), (2.5, 0, -2.5))),
          [0.0, 1.0])
    cam_down.parm("ry").set(0)

    # Height is part of the test: terrain raised above the camera is invisible.
    terrain.parm("initialheight").set(20)
    check("hf crop: nothing visible removes heightfield", len(cull_hf.geometry().prims()), 0)
    cull_hf.parm("hf_output").set(0)
    check("hf mask: raised terrain culled", sample(cull_hf, "mask", (0, 0, 0)), 0.0)
    terrain.parm("initialheight").set(5)   # depth 5: visible |x| <= ~2.07
    check("hf mask: height changes footprint",
          [sample(cull_hf, "mask", p) for p in ((1.5, 0, 0), (3.5, 0, 0))], [1.0, 0.0])

    # --- Occlusion: wall on the second input ---------------------------------
    # Wall at z=-10 covering x in [-2, 4.5]. From cam1 at the origin:
    #   0 (0,0,-5)  in front of the wall          -> visible
    #   1 (0,0,-20) behind the wall               -> occluded
    #   2 (3,0,-20) behind the wall (ray x=1.5)   -> occluded
    wall = geo.createNode("grid", "wall")
    wall.parm("orient").set(0)
    wall.parm("rows").set(2)
    wall.parm("cols").set(2)
    wall.parm("sizex").set(6.5)
    wall.parm("sizey").set(4)
    wall.parm("tx").set(1.25)
    wall.parm("tz").set(-10)
    occ_pts = points_sop(geo, "occ_pts", [(0, 0, -5), (0, 0, -20), (3, 0, -20)])
    cull_occ = geo.createNode("cull_geometry::1.0", "cull_occ")
    cull_occ.setInput(0, occ_pts)
    cull_occ.setInput(1, wall)
    cull_occ.parm("camera").set("/obj/cam1")
    check("occlusion off: all in frustum kept", ids(cull_occ), [0, 1, 2])
    cull_occ.parm("occlusion").set(1)
    check("occlusion (input geometry): points occlude nothing", ids(cull_occ), [0, 1, 2])
    cull_occ.parm("occluder_source").set(1)
    check("occlusion (second input): wall hides points", ids(cull_occ), [0])
    check("occlusion: no errors/warnings", (errors(cull_occ), cull_occ.warnings()), ((), ()))
    cull_occ.parm("occluder_source").set(2)
    check("occlusion (both): wall hides points", ids(cull_occ), [0])
    cull_occ.parm("output_mode").set(2)
    group = cull_occ.geometry().findPointGroup("cull_candidates")
    check("occlusion: group only", sorted(p.attribValue("id") for p in group.points()), [1, 2])
    cull_occ.parm("output_mode").set(0)

    # Frame range: camera x goes 0 -> 9.5 over frames 1-20.
    #   point 1: in frustum only while x < 8.28, unblocked only while x > 9
    #            -> never both on the same frame -> culled
    #   point 2: unblocked once x > 6 and still in frustum -> kept
    #   point 0: in frustum while x < 2.07, never blocked -> kept
    cam.parm("tx").setExpression("($F - 1) * 0.5")
    cull_occ.parm("view_range").set(2)
    cull_occ.parmTuple("frange").deleteAllKeyframes()
    cull_occ.parmTuple("frange").set((1, 20, 1))
    check("occlusion frame range: frustum and ray tested per frame", ids(cull_occ), [0, 2])
    cull_occ.parm("view_range").set(0)
    cam.parm("tx").deleteAllKeyframes()
    cam.parm("tx").set(0)

    # --- Occlusion: packed self-occlusion ------------------------------------
    # Big box (2 units, front face z=-9) hides a small box at z=-20; a tall
    # box at z=-20 (top y=3, still in frame) peeks over it. Each box's centre is inside itself, so
    # this also checks a primitive never occludes itself.
    def packed_box(name, size, centre):
        box_node = geo.createNode("box", name)
        box_node.parmTuple("size").set(size)
        box_node.parmTuple("t").set(centre)
        pack_node = geo.createNode("pack", name + "_pack")
        pack_node.setFirstInput(box_node)
        return pack_node

    buildings = geo.createNode("merge", "buildings")
    buildings.setInput(0, packed_box("big", (2, 2, 2), (0, 0, -10)))
    buildings.setInput(1, packed_box("small", (1, 1, 1), (0, 0, -20)))
    buildings.setInput(2, packed_box("tall", (1, 6, 1), (0.3, 0, -20)))
    cull_bld = geo.createNode("cull_geometry::1.0", "cull_buildings")
    cull_bld.setFirstInput(buildings)
    cull_bld.parm("camera").set("/obj/cam1")
    cull_bld.parm("is_packed").set(1)
    cull_bld.parm("occlusion").set(1)

    def heights(node):
        return sorted(round(p.intrinsicValue("bounds")[3] - p.intrinsicValue("bounds")[2], 1)
                      for p in node.geometry().prims())

    check("occlusion packed bbox: hidden box culled, peeking box kept", heights(cull_bld), [2.0, 6.0])
    cull_bld.parm("bound_test").set(0)
    check("occlusion packed centroid: peeking box culled too", heights(cull_bld), [2.0])

    # --- Occlusion: heightfield ridge ------------------------------------------
    # Camera at (-30, 5, 0) looking +X, tilted 10 degrees down. A 4-unit ridge
    # at |x| < 2 (top still in frame) hides the ground behind it (x > 2).
    cam_ridge = hou.node("/obj").createNode("cam", "cam_ridge")
    cam_ridge.parmTuple("t").set((-30, 5, 0))
    cam_ridge.parmTuple("r").set((-10, -90, 0))
    ridge_hf = geo.createNode("heightfield", "ridge_hf")
    ridge_hf.parm("sizex").set(100)
    ridge_hf.parm("sizey").set(100)
    ridge_hf.parm("gridspacing").set(1)
    ridge = geo.createNode("volumewrangle", "ridge")
    ridge.setFirstInput(ridge_hf)
    ridge.parm("snippet").set("if (abs(v@P.x) < 2) f@height = 4;")
    cull_ridge = geo.createNode("cull_geometry::1.0", "cull_ridge")
    cull_ridge.setFirstInput(ridge)
    cull_ridge.parm("camera").set("/obj/cam_ridge")
    cull_ridge.parm("is_heightfield").set(1)
    ridge_probe = ((-10, 0, 0), (0, 0, 0), (10, 0, 0))   # in front, ridge top, behind
    check("hf occlusion off: all in frustum",
          [sample(cull_ridge, "mask", p) for p in ridge_probe], [1.0, 1.0, 1.0])
    cull_ridge.parm("occlusion").set(1)
    check("hf occlusion: ground behind ridge culled",
          [sample(cull_ridge, "mask", p) for p in ridge_probe], [1.0, 1.0, 0.0])
    check("hf occlusion: no errors/warnings", (errors(cull_ridge), cull_ridge.warnings()), ((), ()))
    cull_ridge.parm("occluder_lod").set(0.25)
    check("hf occlusion: lower occluder LOD",
          [sample(cull_ridge, "mask", p) for p in ridge_probe], [1.0, 1.0, 0.0])

    # --- Errors -----------------------------------------------------------------
    bad = geo.createNode("cull_geometry::1.0", "cull_nocam")
    bad.setFirstInput(pts)
    check("error: no camera", bool(errors(bad)), True)
    print("     message:", errors(bad))

    # --- USD camera from a LOP, used in OBJ context ---------------------------
    # Camera LOP defaults: focal 50, aperture 20.955 x 11.787 -> at depth 5 the
    # half extents are ~1.05 x ~0.59, so point 6 (y=1) is outside.
    stage = hou.node("/stage")
    cam_lop = stage.createNode("camera", "camera1")
    cam_lop.parm("tx").setExpression("($F - 1) * 0.5")
    cull_usd = geo.createNode("cull_geometry::1.0", "cull_usd")
    cull_usd.setFirstInput(pts)
    cull_usd.parm("camera").set("/stage/camera1/cameras/camera1")
    hou.setFrame(1)
    check("usd cam (combined path): no errors", errors(cull_usd), ())
    check("usd cam (combined path): f1", ids(cull_usd), [0])
    cull_usd.parm("camera").set("/stage/camera1")
    check("usd cam (node, auto prim): f1", ids(cull_usd), [0])
    cull_usd.parm("view_range").set(2)
    cull_usd.parmTuple("frange").deleteAllKeyframes()
    cull_usd.parmTuple("frange").set((1, 20, 1))
    check("usd cam: frame range union (LOP animation)", ids(cull_usd), [0, 2, 5])

    # --- LOP asset ----------------------------------------------------------------
    cubes = stage.createNode("sopcreate", "cubes")
    inner = cubes.node("sopnet/create")
    box_in = inner.createNode("box")
    copy_in = inner.createNode("copytopoints::2.0")
    copy_in.setInput(0, box_in)
    copy_in.setInput(1, points_sop(inner, "pts", [(0, 0, -5), (10, 0, -5)]))
    connect_in = inner.createNode("connectivity")
    connect_in.setFirstInput(copy_in)
    connect_in.parm("connecttype").set(1)
    connect_in.parm("attribname").set("class")
    wr = inner.createNode("attribwrangle")
    wr.setFirstInput(connect_in)
    wr.parm("class").set(1)
    wr.parm("snippet").set('s@name = sprintf("cube_%d", i@class);')
    out_in = inner.createNode("output")
    out_in.setFirstInput(wr)
    out_in.setDisplayFlag(True)

    merge = stage.createNode("merge", "merge")
    merge.setInput(0, cubes)
    merge.setInput(1, cam_lop)
    lop_cull = stage.createNode("cull_geometry_lop::1.0", "cull")
    lop_cull.setFirstInput(merge)
    lop_cull.parm("primpattern").set("/cubes/*")
    lop_cull.parm("is_packed").set(1)
    hou.setFrame(1)
    check("lop asset: no errors", errors(lop_cull), ())
    usd = lop_cull.stage()
    from pxr import UsdGeom
    meshes = sorted(str(p.GetPath()) for p in usd.Traverse() if p.IsA(UsdGeom.Mesh))
    print("     input meshes:", sorted(str(p.GetPath()) for p in merge.stage().Traverse() if p.IsA(UsdGeom.Mesh)))
    check("lop asset (packed): off-screen cube removed", meshes, ["/cubes/cube_0"])

    lop_cull.parm("is_packed").set(0)
    errors(lop_cull)
    usd = lop_cull.stage()
    counts = {}
    for prim in usd.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            counts[str(prim.GetPath())] = len(UsdGeom.Mesh(prim).GetPointsAttr().Get(hou.frame()) or [])
    print("     unpacked lop errors/warnings:", lop_cull.errors(), lop_cull.warnings())
    check("lop asset (points): off-screen cube emptied", counts.get("/cubes/cube_1", 0), 0)
    check("lop asset (points): on-screen cube intact", counts.get("/cubes/cube_0"), 8)
    check("lop asset (points): culled cube deactivated",
          usd.GetPrimAtPath("/cubes/cube_1").IsActive(), False)

    # Partially visible mesh gets new (smaller) topology.
    wide = stage.createNode("sopcreate", "wide")
    wide_grid = wide.node("sopnet/create").createNode("grid")
    wide_grid.parm("orient").set(0)
    wide_grid.parm("sizex").set(6)
    wide_grid.parm("tx").set(3)
    wide_grid.parm("tz").set(-5)
    wide_out = wide.node("sopnet/create").createNode("output")
    wide_out.setFirstInput(wide_grid)
    wide_out.setDisplayFlag(True)
    merge.setInput(2, wide)
    lop_cull.parm("primpattern").set("/cubes/* /wide/*")
    usd = lop_cull.stage()
    wide_mesh = [p for p in usd.Traverse() if p.IsA(UsdGeom.Mesh) and str(p.GetPath()).startswith("/wide")]
    n_wide = len(UsdGeom.Mesh(wide_mesh[0]).GetPointsAttr().Get(hou.frame())) if wide_mesh else 0
    print("     wide mesh points after cull:", n_wide, "warnings:", lop_cull.warnings())
    check("lop asset (points): partial mesh trimmed", 0 < n_wide < 100, True)
    lop_cull.parm("view_range").set(1)
    usd = lop_cull.stage()
    attr = UsdGeom.Mesh(usd.GetPrimAtPath("/wide/mesh_0")).GetFaceVertexCountsAttr()
    check("lop asset: single frame writes static topology",
          (attr.GetNumTimeSamples(), 0 < len(attr.Get()) < 81), (0, True))
    lop_cull.parm("view_range").set(0)

    # Make Invisible instead of deactivate.
    lop_cull.parm("usd_cull_method").set(1)
    usd = lop_cull.stage()
    prim = usd.GetPrimAtPath("/cubes/cube_1")
    check("lop asset: make invisible",
          (prim.IsActive(), UsdGeom.Imageable(prim).ComputeVisibility()), (True, UsdGeom.Tokens.invisible))
    lop_cull.parm("usd_cull_method").set(0)

    # Dependencies: moving the camera re-cooks without forcing.
    merge.setInput(2, None)
    lop_cull.parm("primpattern").set("/cubes/*")
    cam_lop.parm("tx").deleteAllKeyframes()
    cam_lop.parm("tx").set(10)
    usd = lop_cull.stage()
    check("lop asset: re-cooks on camera move",
          (usd.GetPrimAtPath("/cubes/cube_0").IsActive(), usd.GetPrimAtPath("/cubes/cube_1").IsActive()),
          (False, True))

    # Occlusion: a wall between the camera (x=10) and cube_1 hides it.
    wall_lop = stage.createNode("sopcreate", "wall")
    wall_lop_grid = wall_lop.node("sopnet/create").createNode("grid")
    wall_lop_grid.parm("orient").set(0)
    wall_lop_grid.parm("sizex").set(2)
    wall_lop_grid.parm("sizey").set(2)
    wall_lop_grid.parm("tx").set(10)
    wall_lop_grid.parm("tz").set(-2.5)
    wall_lop_out = wall_lop.node("sopnet/create").createNode("output")
    wall_lop_out.setFirstInput(wall_lop_grid)
    wall_lop_out.setDisplayFlag(True)
    merge.setInput(2, wall_lop)
    lop_cull.parm("primpattern").set("/cubes/* /wall/*")
    lop_cull.parm("occlusion").set(1)
    usd = lop_cull.stage()
    check("lop asset: occluded cube deactivated, wall kept",
          (usd.GetPrimAtPath("/cubes/cube_1").IsActive(), usd.GetPrimAtPath("/wall/mesh_0").IsActive()),
          (False, True))
    lop_cull.parm("occlusion").set(0)
    usd = lop_cull.stage()
    check("lop asset: occlusion off keeps cube", usd.GetPrimAtPath("/cubes/cube_1").IsActive(), True)

    print()
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
