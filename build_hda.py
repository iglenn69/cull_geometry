# SPDX-FileCopyrightText: 2026 AzureBlure
# SPDX-License-Identifier: MIT
"""
Build the Cull Geometry HDAs with hython.

    hython build_hda.py [output.hda]

Creates one library file (default: otls/cull_geometry.hda) containing:
  * Sop/cull_geometry::1.0     - camera frustum culling for SOP networks
                                 (OBJ geo, SOP Modify / SOP Create LOPs)
  * Lop/cull_geometry_lop::1.0 - Solaris wrapper: SOP Modify + the SOP asset

Sources live in src/ and are embedded into the asset at build time.
"""
import os
import sys

import hou

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")

SOP_TYPE = "cull_geometry::1.0"
LOP_TYPE = "cull_geometry_lop::1.0"
GROUP_NAME = "cull_candidates"

# Run over primitives when either toggle is on (packed => per packed prim).
PRIM_MODE_EXPR = 'ch("../run_over_prims") || ch("../is_packed")'


def read_src(name):
    with open(os.path.join(SRC, name), "r") as handle:
        return handle.read()


def read_vex(name):
    """Wrangle snippet with the shared frustum functions prepended."""
    return read_src("frustum_common.vfl") + read_src(name)


# ----------------------------------------------------------------------------
# Parameters
# ----------------------------------------------------------------------------

def cull_parm_templates(heightfield):
    """Culling parms shared by the SOP and LOP assets.

    heightfield: include the Input Heightfield parms (SOP asset only; USD
    has no heightfield primitives).
    """
    # Toggles that make the point/primitive options irrelevant.
    exclusive = "{ is_packed == 1 }"
    if heightfield:
        exclusive += " { is_heightfield == 1 }"

    prim_test = hou.MenuParmTemplate(
        "bound_test", "Primitive Test",
        menu_items=("centroid", "bbox"),
        menu_labels=("Centroid", "Bounding Box"),
        default_value=1,
        help="Centroid: test the primitive's position (or a heightfield "
             "voxel's surface point) only. Bounding Box: keep the primitive "
             "(or voxel footprint) if any part of its bounds touches the "
             "frustum (conservative, no popping at the frame edge).")
    prim_test.setConditional(
        hou.parmCondType.HideWhen,
        "{ run_over_prims == 0 is_packed == 0 is_heightfield == 0 }" if heightfield
        else "{ run_over_prims == 0 is_packed == 0 }")

    run_prims = hou.ToggleParmTemplate(
        "run_over_prims", "Run over Primitives", default_value=False,
        help="Off: test and cull points. On: test and cull primitives.")
    run_prims.setConditional(hou.parmCondType.DisableWhen, exclusive)

    connectivity = hou.ToggleParmTemplate(
        "use_connectivity", "Use Connectivity", default_value=False,
        help="Treat each connected piece as a unit: a piece is kept if any "
             "of its elements is visible, so pieces are never cut open.")
    connectivity.setConditional(hou.parmCondType.DisableWhen, exclusive)

    packed = hou.ToggleParmTemplate(
        "is_packed", "Input Packed Geometry", default_value=False,
        help="Cull whole packed primitives using their top-level bounds "
             "or centroid, without touching the packed contents.")
    input_templates = [packed]
    if heightfield:
        packed.setConditional(hou.parmCondType.DisableWhen, "{ is_heightfield == 1 }")
        is_hf = hou.ToggleParmTemplate(
            "is_heightfield", "Input Heightfield", default_value=False,
            help="Treat the input as a heightfield: each voxel is tested at "
                 "its terrain surface position (height included). See "
                 "Heightfield Output.")
        is_hf.setConditional(hou.parmCondType.DisableWhen, "{ is_packed == 1 }")
        input_templates.append(is_hf)

    single = hou.FloatParmTemplate(
        "single_frame", "Frame", 1, default_expression=("$FSTART",),
        help="Frame at which the camera is evaluated.")
    single.setConditional(hou.parmCondType.HideWhen, "{ view_range != 1 }")

    frange = hou.FloatParmTemplate(
        "frange", "Start/End/Inc", 3,
        default_value=(1.0, 240.0, 1.0),
        default_expression=("$FSTART", "$FEND", ""),
        help="Elements visible on ANY frame in this range are kept.")
    frange.setConditional(hou.parmCondType.HideWhen, "{ view_range != 2 }")

    output_templates = [
        hou.MenuParmTemplate(
            "output_mode", "Output",
            menu_items=("delete_outside", "delete_inside", "group_only"),
            menu_labels=("Delete Outside Frustum", "Delete Inside Frustum",
                         "Group Only"),
            default_value=0,
            help="Group Only keeps all geometry and puts culled elements in "
                 "the '%s' group (heightfields: a '%s' layer, 1 = culled)."
                 % (GROUP_NAME, GROUP_NAME)),
    ]
    if heightfield:
        hf_output = hou.MenuParmTemplate(
            "hf_output", "Heightfield Output",
            menu_items=("mask", "crop"),
            menu_labels=("Mask Layer", "Crop to Visible"),
            default_value=0,
            help="Mask Layer: replace the mask layer with visibility (1 = "
                 "visible; inverted for Delete Inside). Crop to Visible: crop "
                 "the heightfield to the visible region, removing it entirely "
                 "if nothing is visible (Delete Outside only; Delete Inside "
                 "always uses the mask).")
        hf_output.setConditional(hou.parmCondType.HideWhen,
                                 "{ is_heightfield == 0 } { output_mode == 2 }")
        output_templates.append(hf_output)

    return input_templates + [
        run_prims,
        connectivity,
        prim_test,
        hou.SeparatorParmTemplate("sep_clip"),
        hou.FloatParmTemplate(
            "znear", "Z Near", 1, default_value=(0.1,),
            min=0.0, max=10.0, min_is_strict=True,
            help="Elements closer to the camera than this are culled."),
        hou.FloatParmTemplate(
            "zfar", "Z Far", 1, default_value=(1000.0,),
            min=0.0, max=10000.0, min_is_strict=True,
            help="Elements further from the camera than this are culled."),
        hou.FloatParmTemplate(
            "overscan", "Overscan", 1, default_value=(0.0,),
            min=-0.5, max=1.0,
            help="Expand (or shrink, if negative) the frustum relative to the "
                 "screen. 0.1 = 10% padding on every side."),
        hou.SeparatorParmTemplate("sep_range"),
        hou.MenuParmTemplate(
            "view_range", "Viewing Range",
            menu_items=("current", "single", "range"),
            menu_labels=("Current Frame", "Single Frame", "Frame Range"),
            default_value=0,
            help="Current Frame: re-culls every frame. Single Frame: culls "
                 "once from a fixed frame. Frame Range: keeps anything seen "
                 "at any frame of the range (static result for the shot)."),
        single,
        frange,
        hou.SeparatorParmTemplate("sep_output"),
    ] + output_templates


def sop_parm_group():
    group = hou.ParmTemplateGroup()
    group.append(hou.StringParmTemplate(
        "camera", "Camera", 1, default_value=("",),
        string_type=hou.stringParmType.NodeReference,
        help="OBJ camera (/obj/cam1), LOP node (/stage/camera1), LOP node + "
             "prim (/stage/camera1/cameras/camera1), or a USD prim path "
             "(/cameras/camera1) when inside a SOP Modify LOP. Leave empty "
             "inside SOP Modify to use the first camera on its input stage."))
    group.append(hou.StringParmTemplate(
        "cam_primpath", "Camera Prim (USD)", 1, default_value=("",),
        help="USD camera prim path, used when Camera points at a LOP node. "
             "Empty uses the first camera found on the stage."))
    group.append(hou.SeparatorParmTemplate("sep_camera"))
    for template in cull_parm_templates(heightfield=True):
        group.append(template)
    return group


def lop_parm_group():
    prim_tags = {
        "script_action": "import loputils\nloputils.selectPrimsInParm(kwargs, True)",
        "script_action_icon": "BUTTONS_reselect",
        "script_action_help": "Select primitives in the Scene Viewer or Scene Graph Tree.",
        "sidefx::usdpathtype": "primlist",
    }
    cam_tags = dict(prim_tags)
    cam_tags["script_action"] = "import loputils\nloputils.selectPrimsInParm(kwargs, False)"
    cam_tags["sidefx::usdpathtype"] = "prim"

    group = hou.ParmTemplateGroup()
    group.append(hou.StringParmTemplate(
        "primpattern", "Primitives", 1, default_value=("%type:Mesh",),
        tags=prim_tags,
        help="USD primitives to cull."))
    group.append(hou.StringParmTemplate(
        "camera", "Camera", 1, default_value=("",), tags=cam_tags,
        help="USD camera prim on the input stage. Empty uses the first "
             "camera found."))
    group.append(hou.SeparatorParmTemplate("sep_camera"))
    for template in cull_parm_templates(heightfield=False):
        if template.name() == "output_mode":
            # SOP groups have no USD equivalent here, so no Group Only mode.
            template = hou.MenuParmTemplate(
                "output_mode", "Output",
                menu_items=("delete_outside", "delete_inside"),
                menu_labels=("Cull Outside Frustum", "Cull Inside Frustum"),
                default_value=0)
        group.append(template)
    group.append(hou.MenuParmTemplate(
        "usd_cull_method", "Fully Culled Prims",
        menu_items=("deactivate", "invisible"),
        menu_labels=("Deactivate", "Make Invisible"),
        default_value=0,
        help="How USD primitives whose geometry is entirely culled are "
             "removed. Partially culled meshes always get new topology."))
    return group


DEACTIVATE_LOP_CODE = '''\
# Prims whose geometry was entirely removed by the cull are not written back
# by SOP Modify (it only overlays edits), so deactivate / hide them here.
from pxr import UsdGeom

node = hou.pwd()
hda = node.parent()
cull = hda.node("sop_modify/modify/modify/cull_geometry")
stage = node.editableStage()

def prim_paths(geo):
    if geo is None or geo.findPrimAttrib("path") is None:
        return set()
    return set(geo.primStringAttribValues("path"))

before = prim_paths(cull.node("frustum_test").geometry())
after = prim_paths(cull.geometry())
invisible = hda.evalParm("usd_cull_method") == 1

for path in sorted(before - after):
    prim = stage.GetPrimAtPath(path)
    if not prim:
        continue
    if invisible:
        UsdGeom.Imageable(prim).MakeInvisible()
    else:
        prim.SetActive(False)
'''


# ----------------------------------------------------------------------------
# SOP asset
# ----------------------------------------------------------------------------

def build_sop_contents(hda):
    """Internal network:

        (input) -> frustum_test -> switch_connectivity -> set_group
                       ^             \\-> connectivity -> piece_vis
                  camera_data
        set_group -> blast -> switch_output -> cleanup -> switch_heightfield -> output0
        (input) -> heightfield branch (build_heightfield_branch) -^
                \\________________________/
    """
    for child in hda.children():
        child.destroy()
    hda_input = hda.indirectInputs()[0]

    # Camera data (no inputs; dependencies are tracked from parm evaluation).
    objnet = hda.createNode("objnet", "cam_import")
    lop_cam = objnet.createNode("lopimportcam", "lop_camera")
    lop_cam.parm("loppath").setExpression(
        'hou.node("../..").hdaModule().lop_camera_source(hou.node("../.."))[0]',
        hou.exprLanguage.Python)
    lop_cam.parm("primpath").setExpression(
        'hou.node("../..").hdaModule().lop_camera_source(hou.node("../.."))[1]',
        hou.exprLanguage.Python)

    camera_data = hda.createNode("python", "camera_data")
    camera_data.parm("python").set(
        "node = hou.pwd()\n"
        "node.parent().hdaModule().build_camera_data(node)\n")

    test = hda.createNode("attribwrangle", "frustum_test")
    test.setInput(0, hda_input)
    test.setInput(1, camera_data)
    test.parm("class").setExpression("if(%s, 1, 2)" % PRIM_MODE_EXPR)
    test.parm("snippet").set(read_vex("frustum_test.vfl"))

    connectivity = hda.createNode("connectivity", "connectivity")
    connectivity.setFirstInput(test)
    connectivity.parm("connecttype").setExpression("if(%s, 1, 0)" % PRIM_MODE_EXPR)
    connectivity.parm("attribname").set("__cull_class")

    piece_vis = hda.createNode("attribwrangle", "piece_vis")
    piece_vis.setFirstInput(connectivity)
    piece_vis.parm("class").set(0)  # detail
    piece_vis.parm("snippet").set(read_src("piece_vis.vfl"))

    switch_conn = hda.createNode("switch", "switch_connectivity")
    switch_conn.setInput(0, test)
    switch_conn.setInput(1, piece_vis)
    switch_conn.parm("input").setExpression(
        'ch("../use_connectivity") && !ch("../is_packed")')

    set_group = hda.createNode("attribwrangle", "set_group")
    set_group.setFirstInput(switch_conn)
    set_group.parm("class").setExpression("if(%s, 1, 2)" % PRIM_MODE_EXPR)
    set_group.parm("snippet").set(read_src("set_group.vfl"))

    blast = hda.createNode("blast", "blast")
    blast.setFirstInput(set_group)
    blast.parm("group").set(GROUP_NAME)
    blast.parm("grouptype").setExpression("if(%s, 4, 3)" % PRIM_MODE_EXPR)
    blast.parm("negate").setExpression('ch("../output_mode") == 1')
    blast.parm("removegrp").set(1)

    switch_out = hda.createNode("switch", "switch_output")
    switch_out.setInput(0, set_group)
    switch_out.setInput(1, blast)
    switch_out.parm("input").setExpression('ch("../output_mode") != 2')

    # Only enable the classes that carry temp attributes (avoids "no match" warnings).
    cleanup = hda.createNode("attribdelete", "cleanup")
    cleanup.setFirstInput(switch_out)
    cleanup.parm("doptdel").setExpression("!(%s)" % PRIM_MODE_EXPR)
    cleanup.parm("ptdel").set("__cull_*")
    cleanup.parm("doprimdel").setExpression(PRIM_MODE_EXPR)
    cleanup.parm("primdel").set("__cull_*")
    cleanup.parm("dodtldel").setExpression('ch("../use_connectivity") && !ch("../is_packed")')
    cleanup.parm("dtldel").set("__cull_*")

    hf_cleanup = build_heightfield_branch(hda, hda_input, camera_data)

    switch_mode = hda.createNode("switch", "switch_heightfield")
    switch_mode.setInput(0, cleanup)
    switch_mode.setInput(1, hf_cleanup)
    switch_mode.parm("input").setExpression('ch("../is_heightfield")')

    output = hda.createNode("output", "output0")
    output.setFirstInput(switch_mode)
    output.setDisplayFlag(True)
    output.setRenderFlag(True)

    hda.layoutChildren()


def build_heightfield_branch(hda, hda_input, camera_data):
    """Input Heightfield branch:

        (input) -> hf_layers -> hf_test -> hf_reduce -> switch_hf_crop -> hf_cleanup
                                   ^                     |- hf_crop
                              camera_data                \\- hf_remove (nothing visible)
    """
    # Volume wrangles only write existing layers, so create them first.
    # Layer 2 is the output layer, or a throwaway so an existing mask survives.
    hf_layers = hda.createNode("heightfield_copylayer", "hf_layers")
    hf_layers.setFirstInput(hda_input)
    hf_layers.parm("numcopy").set(2)
    hf_layers.parm("srcname1").set("height")
    hf_layers.parm("dstname1").set("__cull_vis")
    hf_layers.parm("srcname2").set("height")
    hf_layers.parm("dstname2").setExpression(
        'ifs(ch("../output_mode") == 2, "%s", '
        'ifs(ch("../hf_output") == 0 || ch("../output_mode") == 1, "mask", "__cull_unused"))'
        % GROUP_NAME)

    hf_test = hda.createNode("volumewrangle", "hf_test")
    hf_test.setInput(0, hf_layers)
    hf_test.setInput(1, camera_data)
    hf_test.parm("snippet").set(read_vex("heightfield_test.vfl"))

    hf_reduce = hda.createNode("volumereduce", "hf_reduce")
    hf_reduce.setFirstInput(hf_test)
    hf_reduce.parm("group").set("@name=__cull_vis")
    hf_reduce.parm("reduction").set("max")
    hf_reduce.parm("result").set("detail")
    hf_reduce.parm("resultattrib").set("__cull_any")

    hf_crop = hda.createNode("heightfield_crop", "hf_crop")
    hf_crop.setFirstInput(hf_reduce)
    hf_crop.parm("cropbylayer").set("__cull_vis")
    hf_crop.parm("cropvalue").set(0.5)

    hf_remove = hda.createNode("blast", "hf_remove")
    hf_remove.setFirstInput(hf_reduce)
    hf_remove.parm("group").set("@intrinsic:typename=Volume")
    hf_remove.parm("grouptype").set(4)  # prims

    switch_crop = hda.createNode("switch", "switch_hf_crop")
    switch_crop.setInput(0, hf_reduce)
    switch_crop.setInput(1, hf_crop)
    switch_crop.setInput(2, hf_remove)
    switch_crop.parm("input").setExpression(
        'if(ch("../output_mode") == 0 && ch("../hf_output") == 1, '
        'if(detail("../hf_reduce", "__cull_any", 0) > 0.5, 1, 2), 0)')

    hf_cleanup_layers = hda.createNode("blast", "hf_cleanup_layers")
    hf_cleanup_layers.setFirstInput(switch_crop)
    hf_cleanup_layers.parm("group").set("@name=__cull_*")
    hf_cleanup_layers.parm("grouptype").set(4)  # prims

    hf_cleanup = hda.createNode("attribdelete", "hf_cleanup")
    hf_cleanup.setFirstInput(hf_cleanup_layers)
    hf_cleanup.parm("dodtldel").set(1)
    hf_cleanup.parm("dtldel").set("__cull_*")
    return hf_cleanup


def build_sop_asset(hda_path):
    geo = hou.node("/obj").createNode("geo", "build_sop")
    subnet = geo.createNode("subnet", "cull_geometry")
    asset = subnet.createDigitalAsset(
        name=SOP_TYPE, hda_file_name=hda_path, description="Cull Geometry",
        min_num_inputs=1, max_num_inputs=1, version="1.0",
        ignore_external_references=True)

    definition = asset.type().definition()
    definition.setParmTemplateGroup(sop_parm_group())
    definition.addSection("PythonModule", read_src("python_module.py"))
    definition.setExtraFileOption("PythonModule/IsPython", True)
    definition.addSection("Help", read_src("help_sop.txt"))
    definition.setIcon("SOP_blast")

    asset.allowEditingOfContents()
    build_sop_contents(asset)
    definition.updateFromNode(asset)
    definition.save(definition.libraryFilePath(), asset)
    geo.destroy()


# ----------------------------------------------------------------------------
# LOP asset
# ----------------------------------------------------------------------------

def build_lop_contents(hda):
    """(input) -> IN -> sop_modify [ (input) -> cull_geometry -> OUT ]
                 -> remove_culled_prims -> output0"""
    for child in hda.children():
        child.destroy()

    null_in = hda.createNode("null", "IN")
    null_in.setInput(0, hda.indirectInputs()[0])

    sop_modify = hda.createNode("sopmodify", "sop_modify")
    sop_modify.setFirstInput(null_in)
    sop_modify.parm("primpattern").set(hda.parm("primpattern"))
    sop_modify.parm("unpacktopolygons").setExpression('!ch("../is_packed")')
    # Author new topology for partially culled meshes: time sampled when the
    # cull changes per frame (Current Frame), a static default otherwise.
    sop_modify.parm("enable_topologyhandling").set(1)
    sop_modify.parm("topologyhandling").setExpression(
        'ifs(ch("../view_range") == 0, "animated", "static")')

    modify = sop_modify.node("modify/modify")
    out = modify.node("OUT")
    cull = modify.createNode(SOP_TYPE, "cull_geometry")
    cull.setFirstInput(modify.indirectInputs()[0])
    out.setFirstInput(cull)

    # Camera: bare prim path on the SOP Modify input stage (or empty = first camera).
    cull.parm("camera").set(hda.parm("camera"))
    for parm in cull.parms():
        if parm.name() in ("camera", "cam_primpath"):
            continue
        outer = hda.parm(parm.name())
        if outer is not None:
            parm.set(outer)
    modify.layoutChildren()

    remove_culled = hda.createNode("pythonscript", "remove_culled_prims")
    remove_culled.setFirstInput(sop_modify)
    remove_culled.parm("python").set(DEACTIVATE_LOP_CODE)

    output = hda.createNode("output", "output0")
    output.setFirstInput(remove_culled)
    output.setDisplayFlag(True)
    hda.layoutChildren()


def build_lop_asset(hda_path):
    subnet = hou.node("/stage").createNode("subnet", "cull_geometry_lop")
    asset = subnet.createDigitalAsset(
        name=LOP_TYPE, hda_file_name=hda_path, description="Cull Geometry",
        min_num_inputs=1, max_num_inputs=1, version="1.0",
        ignore_external_references=True)

    definition = asset.type().definition()
    definition.setParmTemplateGroup(lop_parm_group())
    definition.addSection("Help", read_src("help_lop.txt"))
    definition.setIcon("SOP_blast")

    asset.allowEditingOfContents()
    build_lop_contents(asset)
    definition.updateFromNode(asset)
    definition.save(definition.libraryFilePath(), asset)
    asset.destroy()


def main():
    hda_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "otls", "cull_geometry.hda")
    hda_path = os.path.abspath(hda_path).replace("\\", "/")
    os.makedirs(os.path.dirname(hda_path), exist_ok=True)
    if os.path.exists(hda_path):
        hou.hda.uninstallFile(hda_path)
        os.remove(hda_path)

    hou.hipFile.clear(suppress_save_prompt=True)
    build_sop_asset(hda_path)
    hou.hda.installFile(hda_path)
    build_lop_asset(hda_path)
    print("Wrote %s" % hda_path)
    for definition in hou.hda.definitionsInFile(hda_path):
        print("  %s/%s" % (definition.nodeTypeCategory().name(), definition.nodeTypeName()))


if __name__ == "__main__":
    main()
