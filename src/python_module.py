"""
PythonModule for cull_geometry::1.0 (SOP).

Resolves the camera (OBJ camera or USD camera on a LOP stage) and builds the
world-space frustum planes consumed by the `frustum_test` wrangle.

Camera path resolution (`camera` parm, optional `cam_primpath` parm):
  1. Path to an OBJ camera node            -> /obj/cam1
  2. Path to a LOP node (+ cam_primpath)   -> /stage/camera1   + /cameras/camera1
  3. LOP node path with prim path appended -> /stage/camera1/cameras/camera1
  4. Bare USD prim path                    -> /cameras/camera1
     (resolved on the input stage of the enclosing SOP Modify LOP)
  5. Empty                                 -> first camera on the enclosing LOP's input stage

USD cameras are evaluated through the internal `lop_camera` (lopimportcam)
object so transforms and lens values cook correctly at any frame, which a
single composed USD stage cannot provide for LOP-authored animation.
"""
import hou

LOP_CAMERA = "cam_import/lop_camera"

# Houdini camera `focalunits` menu tokens -> millimetres (aperture is always mm).
FOCAL_TO_MM = {"mm": 1.0, "m": 1000.0, "nm": 1.0e-6, "in": 25.4, "ft": 304.8}


# ----------------------------------------------------------------------------
# Camera resolution
# ----------------------------------------------------------------------------

def enclosing_lop_input(node):
    """Input LOP of the nearest LOP ancestor (e.g. a SOP Modify), or None."""
    parent = node.parent()
    while parent is not None:
        if isinstance(parent, hou.LopNode):
            inputs = parent.inputs()
            return inputs[0] if inputs and inputs[0] is not None else None
        parent = parent.parent()
    return None


def first_camera_prim(lop):
    """Path of the first UsdGeom.Camera on a LOP's stage, or ''."""
    from pxr import Usd, UsdGeom
    stage = lop.stage()
    if stage is None:
        return ""
    for prim in Usd.PrimRange(stage.GetPseudoRoot()):
        if prim.IsA(UsdGeom.Camera):
            return str(prim.GetPath())
    return ""


def resolve_camera(hda):
    """Resolve the HDA's camera parms.

    Returns ("obj", ObjNode) or ("usd", LopNode, prim_path).
    Raises hou.NodeError when the camera cannot be resolved.
    """
    path = hda.evalParm("camera").strip()
    prim_path = hda.evalParm("cam_primpath").strip()

    if path:
        node = hda.node(path)
        if isinstance(node, hou.ObjNode):
            return ("obj", node)
        if isinstance(node, hou.LopNode):
            return ("usd", node, prim_path or first_camera_prim(node))
        if node is not None:
            raise hou.NodeError("'%s' is not a camera object or LOP node." % path)

        # Combined "<lop node path>/<usd prim path>": longest prefix that is a LOP.
        parts = path.split("/")
        for i in range(len(parts) - 1, 1, -1):
            lop = hda.node("/".join(parts[:i]))
            if isinstance(lop, hou.LopNode):
                return ("usd", lop, "/" + "/".join(parts[i:]))

    lop = enclosing_lop_input(hda)
    if lop is None:
        if path:
            raise hou.NodeError("Cannot resolve camera '%s'." % path)
        raise hou.NodeError("No camera specified.")

    # Bare prim path (or nothing) -> USD camera on the enclosing LOP's input stage.
    return ("usd", lop, prim_path or path or first_camera_prim(lop))


def lop_camera_source(hda):
    """(loppath, primpath) for the internal lopimportcam parm expressions."""
    try:
        resolved = resolve_camera(hda)
    except hou.Error:
        return ("", "")
    if resolved[0] != "usd":
        return ("", "")
    return (resolved[1].path(), resolved[2])


def camera_object(hda):
    """ObjNode providing transform + lens values for the resolved camera."""
    resolved = resolve_camera(hda)
    if resolved[0] == "obj":
        cam = resolved[1]
    else:
        if not resolved[2]:
            raise hou.NodeError("No USD camera found on '%s'." % resolved[1].path())
        cam = hda.node(LOP_CAMERA)
    if cam.parm("focal") is None or cam.parm("aperture") is None:
        raise hou.NodeError("'%s' is not a camera." % cam.path())
    return cam


def containing_object(hda):
    """Nearest OBJ ancestor (SOP space -> world space), or None inside LOPs."""
    parent = hda.parent()
    while parent is not None:
        if isinstance(parent, hou.ObjNode):
            return parent
        parent = parent.parent()
    return None


# ----------------------------------------------------------------------------
# Frames
# ----------------------------------------------------------------------------

def frames_to_evaluate(hda):
    """Frames to test, per the Viewing Range menu.

    0 = Current Frame : the cooking frame only (node is time dependent).
    1 = Single Frame  : one fixed frame (node is NOT time dependent).
    2 = Frame Range   : every frame in start/end/inc; an element survives
                        if it is visible on ANY of these frames, so the
                        result is static across the whole range.
    """
    mode = hda.evalParm("view_range")
    if mode == 0:
        return [hou.frame()]
    if mode == 1:
        return [hda.evalParm("single_frame")]

    start, end, inc = hda.parmTuple("frange").eval()
    if inc <= 0:
        raise hou.NodeError("Frame range increment must be greater than zero.")
    if end < start:
        raise hou.NodeError("Frame range end is before start.")
    frames = []
    count = 0
    frame = start
    while frame <= end + 1.0e-6:
        frames.append(frame)
        count += 1
        frame = start + count * inc
    return frames


# ----------------------------------------------------------------------------
# Projection
# ----------------------------------------------------------------------------

def projection_matrix(cam, frame):
    """Camera-space -> clip-space matrix (row-vector convention).

    clip.xy / clip.w gives NDC in [-1, 1] across the screen window,
    clip.z is the positive view depth, clip.w is the perspective divide.
    Cameras look down -Z, so depth = -z.
    """
    def ev(name, default):
        parm = cam.parm(name)
        return parm.evalAtFrame(frame) if parm is not None else default

    resx = float(ev("resx", 1920))
    resy = float(ev("resy", 1080))
    pixel_aspect = float(ev("aspect", 1.0))
    image_aspect = (resx * pixel_aspect) / resy

    win_x = ev("winx", 0.0)
    win_y = ev("winy", 0.0)
    win_sx = ev("winsizex", 1.0) or 1.0
    win_sy = ev("winsizey", 1.0) or 1.0

    projection = cam.parm("projection").evalAsStringAtFrame(frame)
    ortho = projection == "ortho"

    if ortho:
        scale_x = 2.0 / ev("orthowidth", 1.0)
    else:
        units = cam.parm("focalunits")
        focal_mm = ev("focal", 50.0) * FOCAL_TO_MM.get(
            units.evalAsStringAtFrame(frame) if units else "mm", 1.0)
        scale_x = 2.0 * focal_mm / ev("aperture", 41.4214)
    scale_y = scale_x * image_aspect

    # Screen window: shift by the offset (in screen widths), then scale.
    scale_x /= win_sx
    scale_y /= win_sy
    offset_x = -2.0 * win_x / win_sx
    offset_y = -2.0 * win_y / win_sy

    if ortho:
        rows = ((scale_x, 0.0, 0.0, 0.0),
                (0.0, scale_y, 0.0, 0.0),
                (0.0, 0.0, -1.0, 0.0),
                (offset_x, offset_y, 0.0, 1.0))
    else:
        rows = ((scale_x, 0.0, 0.0, 0.0),
                (0.0, scale_y, 0.0, 0.0),
                (-offset_x, -offset_y, -1.0, -1.0),
                (0.0, 0.0, 0.0, 0.0))
    return hou.Matrix4(rows), projection


def frustum_planes(m_viewproj, znear, zfar, margin):
    """Six normalised planes (nx, ny, nz, d); a point is inside when
    dot(n, P) + d >= 0 for all of them.

    Derived from the clip-space conditions
        -margin*w <= clip.x <= margin*w
        -margin*w <= clip.y <= margin*w
         znear    <= clip.z <= zfar
    where clip = P * m_viewproj and margin = 1 + overscan.
    """
    col = [[m_viewproj.at(i, j) for i in range(4)] for j in range(4)]

    def comb(a, sa, b, sb):
        return [a[i] * sa + b[i] * sb for i in range(4)]

    unit_w = [0.0, 0.0, 0.0, 1.0]
    planes = [
        comb(col[3], margin, col[0], 1.0),   # left
        comb(col[3], margin, col[0], -1.0),  # right
        comb(col[3], margin, col[1], 1.0),   # bottom
        comb(col[3], margin, col[1], -1.0),  # top
        comb(col[2], 1.0, unit_w, -znear),   # near
        comb(unit_w, zfar, col[2], -1.0),    # far
    ]
    flat = []
    for plane in planes:
        length = (plane[0] ** 2 + plane[1] ** 2 + plane[2] ** 2) ** 0.5
        if length > 0.0:
            plane = [c / length for c in plane]
        flat.extend(plane)
    return flat


# ----------------------------------------------------------------------------
# Python SOP entry point
# ----------------------------------------------------------------------------

def build_camera_data(sop):
    """Cook callback for the internal `camera_data` Python SOP."""
    hda = sop.parent()
    geo = sop.geometry()

    cam = camera_object(hda)
    obj = containing_object(hda)
    frames = frames_to_evaluate(hda)

    znear = hda.evalParm("znear")
    zfar = hda.evalParm("zfar")
    if zfar <= znear:
        raise hou.NodeError("Z Far must be greater than Z Near.")
    margin = 1.0 + hda.evalParm("overscan")

    planes = []
    first_matrix = None
    for frame in frames:
        time = hou.frameToTime(frame)
        view = cam.worldTransformAtTime(time).inverted()
        if obj is not None:
            view = obj.worldTransformAtTime(time) * view
        proj, projection = projection_matrix(cam, frame)
        if projection not in ("perspective", "ortho"):
            sop.addWarning("Projection '%s' is approximated as perspective." % projection)
        m_viewproj = view * proj
        if first_matrix is None:
            first_matrix = m_viewproj
        planes.extend(frustum_planes(m_viewproj, znear, zfar, margin))

    geo.addAttrib(hou.attribType.Global, "cam_nframes", 0)
    geo.addArrayAttrib(hou.attribType.Global, "cam_planes", hou.attribData.Float, 1)
    geo.addArrayAttrib(hou.attribType.Global, "cam_frames", hou.attribData.Float, 1)
    geo.addAttrib(hou.attribType.Global, "cam_matrix", (0.0,) * 16)
    geo.addAttrib(hou.attribType.Global, "cam_path", "")

    geo.setGlobalAttribValue("cam_nframes", len(frames))
    geo.setGlobalAttribValue("cam_planes", planes)
    geo.setGlobalAttribValue("cam_frames", [float(f) for f in frames])
    geo.setGlobalAttribValue("cam_matrix", first_matrix.asTuple())
    geo.setGlobalAttribValue("cam_path", cam.path())
