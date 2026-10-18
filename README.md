# Cull Geometry HDA

Camera frustum culling for SideFX Houdini. Removes (or groups) points,
primitives or packed primitives that fall outside a camera's view. Works in
both OBJ/SOP networks and Solaris (Stage/LOP) networks.

Built and tested with Houdini 22.0.466.

## What's in the library

`otls/cull_geometry.hda` contains two nodes, both labelled **Cull Geometry**
in the Tab menu:

| Node type | Network | Use it when |
| :--- | :--- | :--- |
| `cull_geometry::1.0` | SOP | Working inside a Geometry object, or inside a SOP Modify / SOP Create LOP |
| `cull_geometry_lop::1.0` | LOP | Culling USD prims directly in `/stage` |

## Installation

Add the `otls` folder to Houdini's asset search path in `houdini.env`,
using the path where you cloned this repository:

```
HOUDINI_OTLSCAN_PATH = <Your cloned repo path>/cull_geometry/otls;&
```

Or install it for a single session with **Assets > Install Asset Library**
and select `otls/cull_geometry.hda`.

> The asset was saved with a Houdini Indie licence. Houdini FX will treat
> scenes that use it as Indie. Rebuild it with a commercial `hython` (see
> [Development](#development)) before using it in a commercial pipeline.

## Usage

### OBJ / SOP context

1. Inside a Geometry object, add **Cull Geometry** after your geometry.
2. Set **Camera** to an OBJ camera, e.g. `/obj/cam1`.

The geometry object's own transform is taken into account, so a moved or
animated container still culls correctly.

You can also use a USD camera from a LOP network here:

- `/stage/camera1` with **Camera Prim (USD)** set to `/cameras/camera1`
  (leave it empty to use the first camera on that LOP's stage), or
- `/stage/camera1/cameras/camera1` (LOP node path followed by the prim path).

### Stage / LOP context

**Option A: the LOP node.** Add **Cull Geometry** in `/stage` after a
node whose stage contains both the geometry and the camera.

- **Primitives**: the USD prims to cull (default `%type:Mesh`).
- **Camera**: the camera prim, e.g. `/cameras/camera1`. Empty uses the
  first camera found.
- **Fully Culled Prims**: prims whose geometry is entirely culled are
  **Deactivated** (default) or **Made Invisible**. Partly visible meshes get
  new topology with only the visible part.

**Option B: the SOP node inside SOP Modify.** Put the SOP version inside a
SOP Modify LOP and set **Camera** to a USD prim path such as
`/cameras/camera1`, or leave it empty to use the first camera on SOP
Modify's input stage. Note that SOP Modify only overlays edits: prims you
remove entirely will still exist on the stage. Option A handles this for you.

## Parameters

| Parameter | Default | Description |
| :--- | :--- | :--- |
| Camera | | Camera to cull against (see Usage for accepted forms). |
| Camera Prim (USD) | | SOP node only. USD camera prim when **Camera** is a LOP node. |
| Input Packed Geometry | Off | Cull whole packed primitives from their top-level bounds or centroid, without unpacking. Forces primitive mode and disables connectivity. |
| Input Heightfield | Off | SOP node only. Cull a heightfield per voxel, using the terrain surface height. See **Heightfield Output**. |
| Run over Primitives | Off | Off culls points; on culls primitives. |
| Use Connectivity | Off | Keep an entire connected piece if any of it is visible, so pieces are never cut open. |
| Primitive Test | Bounding Box | Primitive/packed mode only. **Centroid** tests one position (fastest). **Bounding Box** keeps anything whose bounds touch the frustum. |
| Z Near / Z Far | 0.1 / 1000 | Depth range, in scene units from the camera. |
| Overscan | 0 | Grows the frustum relative to the screen: `0.1` adds 10% on each side. Negative values shrink it. |
| Viewing Range | Current Frame | **Current Frame**: re-culls every frame. **Single Frame**: culls once using the camera at **Frame**. **Frame Range**: keeps anything visible on any frame from **Start/End/Inc**; the result is fixed for the whole range. |
| Output | Delete Outside Frustum | **Delete Outside**, **Delete Inside**, or (SOP only) **Group Only**, which keeps all geometry and puts culled elements in the `cull_candidates` group. |
| Heightfield Output | Mask Layer | Heightfields only. **Mask Layer** replaces `mask` with visibility (inverted for Delete Inside). **Crop to Visible** crops to the visible region and removes the heightfield if none of it is visible (Delete Outside only). Group Only writes a `cull_candidates` layer instead. |
| Fully Culled Prims | Deactivate | LOP node only. **Deactivate** or **Make Invisible**. |

### Notes

- Point culling with Delete leaves polygons with fewer vertices where they
  crossed the frustum edge. Use **Use Connectivity** or **Run over
  Primitives** to avoid holes.
- Frame Range and Single Frame aren't time dependent, so they cook once
  instead of on every frame. Use them for shot-wide culling.
- Perspective and orthographic cameras are supported, including screen
  window offset/size. Other projections (spherical, cylindrical, lens shader)
  are treated as perspective and show a warning.
- Heightfields: only the first `height` layer in the input is culled. For
  tiled heightfields, run the node in a For-Each loop per tile.
- **Group Only** is the non-destructive option: feed the `cull_candidates`
  group into your own Blast, Split or attribute logic.

## Development

`src/` is the source of truth. The `.hda` is generated from it by
`build_hda.py`, so changes made to the asset in the Houdini UI are
overwritten on the next build unless you copy them back into `src/`.

```
build_hda.py            builds otls/cull_geometry.hda (both node types)
src/python_module.py    camera resolution, projection, frustum planes
src/frustum_common.vfl  shared frustum plane test (prepended to the wrangles)
src/frustum_test.vfl    per-element frustum test (VEX)
src/heightfield_test.vfl  per-voxel heightfield test (VEX)
src/piece_vis.vfl       connectivity aggregation (VEX)
src/set_group.vfl       writes the cull_candidates group (VEX)
src/help_sop.txt        SOP help card
src/help_lop.txt        LOP help card
tests/test_cull.py      hython test suite
```

Build and test with `hython` (in
`C:/Program Files/Side Effects Software/Houdini 22.0.466/bin`):

```
hython build_hda.py
hython tests/test_cull.py
```

The test run should end with `ALL PASSED`.

### How it works

1. A Python SOP (`camera_data`) resolves the camera and, for each frame in
   the viewing range, builds six world-space frustum planes from the
   camera's transform and lens values. USD cameras go through an internal
   LOP Import Camera object, so animated LOP cameras evaluate correctly on
   any frame.
2. A wrangle tests each point, centroid or bounding box (or, for
   heightfields, each voxel's surface point or footprint) against those
   planes. In Frame Range mode an element counts as visible if it is inside
   on any frame.
3. Optional connectivity aggregation, then the `cull_candidates` group is
   written and Blast removes it (or the inverse) depending on **Output**.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 AzureBlure.
