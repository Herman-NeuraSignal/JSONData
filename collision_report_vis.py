"""CollisionReport-driven visualization artifact helper.

WHAT THIS MODULE DOES
----------------------
Renders the synthetic MCA scene, hourglass beam geometry, and a structured
``CollisionReport`` into a static PyVista/Trame-oriented HTML artifact, plus
a JSON metadata sidecar that records traceability back to the structured
report inputs used to produce it. The HTML artifact also embeds a
human-readable, collapsible "sidecar panel" so a reviewer opening the file
in a browser can see the metadata without needing to separately open the
JSON file.

WHY IT'S A SEPARATE MODULE FROM scene_vis.py
----------------------------------------------
``nvsim_collision.vis.scene_vis`` renders a scene + beam and *recomputes*
collision hits itself, mostly as a standalone demo/debug helper. This module
is stricter: every piece of "collision evidence" you see rendered (points,
depth colors, interaction summaries) is read directly off of a
``CollisionReport`` object that was handed to us, never recomputed. That is
what makes this artifact "report-derived" instead of "hand-drawn" -- if the
report is wrong, the picture will faithfully show that wrongness, which is
exactly what you want in a review tool.

VISUAL STYLE
------------
The 3D scene and the injected HTML panel both use a small "NieR:Automata
UI inspired" palette: warm parchment/khaki tones instead of pure white,
a single muted gold/amber accent color, and a monospace typewriter-style
web font (Cutive Mono, via Google Fonts) for the panel text. This is a
purely cosmetic layer -- see the ``_NIER_PALETTE`` dict below if you want
to swap it out for something else later.

Usage:
    python -m nvsim_collision.vis.collision_report_vis
    python -m nvsim_collision.vis.collision_report_vis --out ./vis_artifacts
"""

# `from __future__ import annotations` makes every type hint in this file
# lazily-evaluated text at runtime instead of being executed immediately.
# Practically: it lets us write modern hints like `list[int]` and
# `tuple[Path, Path]` even though this file might be imported by an older
# Python, and it avoids some circular-import headaches with type hints.
from __future__ import annotations

# argparse -> turns this module into a runnable CLI (`python -m ...`).
import argparse
# dataclasses -> we use `dataclasses.asdict()` to turn frozen dataclass
# contract objects (ProbeCommand, BeamSpec, ...) into plain JSON-safe dicts.
import dataclasses
# hashlib -> sha256 checksum of the final HTML artifact, for tamper/identity
# verification (if someone hands you an .html + .json pair, you can prove
# they actually belong together).
import hashlib
# importlib -> used for a *lazy* import of pyvista (see _require_pyvista).
# We don't import pyvista at module load time because it's an optional,
# fairly heavy dependency (pulls in VTK) -- most of this codebase doesn't
# need it, so we only pay the import cost when someone actually renders.
import importlib
# json -> serialize the metadata dict to the .json sidecar, and also to
# embed metadata as a JS object literal inside the HTML artifact.
import json
# uuid -> generate a fresh random artifact_id per render, so two renders of
# the same report are still distinguishable as distinct artifacts.
import uuid
# datetime/timezone -> UTC timestamp for "when was this artifact created".
# Always store timestamps in UTC with an explicit offset (isoformat() on an
# aware datetime includes "+00:00") -- never store naive local time.
from datetime import datetime, timezone
# Path -> all filesystem paths in this module are pathlib.Path, not raw
# strings. This gets you `/`-joining, `.suffix`, `.stat()`, etc for free.
from pathlib import Path
# Any -> used for the pyvista module object itself (its type is dynamic
# since we import it lazily by name, so static type checkers can't know
# its real type without pyvista's stubs installed).
from typing import Any

# numpy -> all of the geometry math (points, transforms, angle sampling) is
# vectorized numpy, matching the rest of this codebase's style.
import numpy as np
# NDArray -> a *typed* numpy array hint, e.g. NDArray[np.float64], so
# readers (and type checkers) know what dtype an array argument expects.
from numpy.typing import NDArray

# Beam model: given a ProbePose, produces a BeamRealization (world-space
# samples along/around the beam axis).
from nvsim_collision.beam import FocusedHourglassBeamModel
# The actual physics-ish engine that turns (scene, beam, command) into a
# CollisionReport. We only use this in the CLI/known-answer harness below,
# NOT inside generate_artifact() itself -- generate_artifact() must only
# ever *read* a report that's handed to it, never compute its own.
from nvsim_collision.collision import VoxelCollisionEngine
# Typed contract / data-model classes shared across the whole simulator.
# These are the "nouns" of the domain: BeamRealization (a realized beam in
# world space), BeamSpec (beam model parameters), CollisionReport (the
# structured result of firing a beam through a scene), ProbeCommand (the
# 4-DOF probe steering input), SceneAsset (voxel volume + labels + frame).
from nvsim_collision.contracts import (
    BeamRealization,
    BeamSpec,
    CollisionReport,
    ProbeCommand,
    SceneAsset,
)
# Scene-building helpers: construct the known-answer synthetic MCA phantom,
# and compute its axis-aligned world-space bounding box.
from nvsim_collision.scene import (
    build_angled_cylinder_mca_phantom,
    scene_bounds_world,
)
# Default probe window (aperture/FOV constraints) used by the known-answer
# CLI harness below.
from nvsim_collision.window import default_left_temporal_window

# A short type alias: "ArrayF" reads much better in signatures below than
# repeating `NDArray[np.float64]` forty times.
ArrayF = NDArray[np.float64]

# Identifies *which helper* produced an artifact (stable machine name).
RENDERER_NAME = "collision_report_vis"
# Identifies *which version* of that helper produced it. Bump this string
# any time you change what gets rendered or what fields the metadata has,
# so old artifacts on disk can be told apart from new ones.
RENDERER_VERSION = "collision_report_vis/pyvista-html/v0.1"

# ---------------------------------------------------------------------------
# Visual theme ("NieR:Automata UI inspired" -- warm parchment/khaki palette,
# single muted gold accent, monospace typewriter font for the HTML panel).
# Centralizing every color/font choice in one dict means: (a) it's easy to
# retheme later without hunting through the render function, and (b) the
# HTML-panel CSS and the PyVista 3D scene colors can both pull from the same
# source of truth so they visually match.
# ---------------------------------------------------------------------------
_NIER_PALETTE: dict[str, str] = {
    # 3D scene background: a vertical gradient from pale parchment (top,
    # like a sky) down to a warmer khaki (bottom, standing in for "floor").
    "bg_top": "#EFE7D6",
    "bg_bottom": "#C7BEA3",
    # Vessel geometry point cloud (the "ground truth" anatomy layer).
    "vessel": "#5B5647",
    # Beam frustum/envelope surface (translucent).
    "beam_envelope": "#B08D57",
    # Beam central axis line + probe origin/focus markers.
    "beam_axis": "#2B2B26",
    "probe_origin": "#C9A227",
    "focus_point": "#A0522D",
    # Bounding box / axis-aligned grid lines standing in for a "floor grid".
    "bounds": "#8A8272",
    # Colormap used to color collision hit points by depth (mm). "copper"
    # is a warm metallic/sepia gradient that fits the parchment theme far
    # better than a default rainbow colormap like "plasma" would.
    "hit_depth_cmap": "copper",
    # HTML panel colors (used only by the injected metadata panel, not by
    # the 3D scene itself).
    "panel_bg": "#EFE8D8",
    "panel_border": "#8A8272",
    "panel_text": "#2B2B26",
    "panel_accent": "#8A6D1F",
    # Google Fonts family name + the CSS @import URL that pulls it in.
    # Cutive Mono is a free, license-clean typewriter/terminal-style font
    # used here as a stand-in for NieR:Automata's (non-free) UI font.
    "font_family": "'Cutive Mono', 'Courier New', monospace",
    "font_import_url": (
        "https://fonts.googleapis.com/css2?family=Cutive+Mono&display=swap"
    ),
}

# Recommended debug overlay layers from DC-02310 Chapter 9. Each entry maps
# to a status this helper can currently support: "present", "absent", or
# "deferred" (explicitly out of scope for this issue but planned later).
# This tuple is the *single source of truth* for which layer keys must
# appear in metadata["layers"] -- both _build_layer_manifest() below and
# the test suite check against this exact tuple, so if you ever add or
# rename a layer, update it here and nowhere else.
_LAYER_KEYS = (
    "anatomy_background",
    "temporal_window_aperture",
    "probe_face_and_axes",
    "central_beam_axis",
    "beam_frustum_envelope",
    "ray_bundle_samples",
    "labeled_vessel_masks",
    "vessel_centerlines_graph_nodes",
    "collision_hit_points_intervals",
    "mesh_entry_exit_points",
    "flow_direction_arrows",
    "quality_flag_markers",
)


def _require_pyvista() -> Any:
    """Import PyVista only when generating visualization artifacts.

    Why lazy-import instead of a normal top-of-file `import pyvista`?
    PyVista pulls in VTK, which is a large, sometimes finicky-to-install
    (and occasionally system-dependent, e.g. needing an X server / Mesa for
    off-screen rendering) dependency. Most of this package (collision
    engine, contracts, scene building) has nothing to do with visualization
    and should be importable/testable without PyVista installed at all.
    By deferring the import to the moment we actually need to render, the
    rest of the package (and its test suite) stays lightweight. Tests for
    *this* module monkeypatch this exact function to inject a fake PyVista
    stand-in, so no real rendering ever happens in CI.
    """
    try:
        # importlib.import_module("pyvista") is functionally the same as
        # `import pyvista`, just spelled so it happens at call-time.
        return importlib.import_module("pyvista")
    except ImportError as exc:
        # Re-raise as a clearer, actionable RuntimeError rather than letting
        # a raw ImportError (which just says "no module named pyvista")
        # propagate. `from exc` keeps the original traceback chained so you
        # can still see the underlying import failure if you need to debug
        # a broken pyvista install specifically.
        raise RuntimeError(
            "The collision report visualization helper requires the "
            "optional visualization dependencies. Install them with "
            "`python -m pip install -e '.[vis]'` or "
            "`python -m pip install 'ds-nvsim-collision[vis]'`."
        ) from exc


def _label_points_world(scene: SceneAsset, label_id: int) -> ArrayF:
    """Return world-space centers for all voxels with ``label_id``.

    ``scene.label_volume`` is a 3D integer array (shape: grid_x, grid_y,
    grid_z) where each voxel holds a label id (0 = background, 1 = LMCA,
    etc, per ``scene.ontology.labels``). This function:
      1. Finds every voxel index matching label_id (np.argwhere).
      2. Converts those *integer grid indices* into *millimeter positions*
         in the scene's own local grid frame, by multiplying by voxel size.
      3. Applies the scene's grid->world rigid transform to get final
         world-space (patient/table) millimeter coordinates, which is the
         frame everything else in this renderer (beam, report hits) is
         already expressed in.
    """
    # np.argwhere returns an (N, 3) array of [i, j, k] integer indices for
    # every voxel where label_volume == label_id.
    indices = np.argwhere(scene.label_volume == label_id)
    # Multiply elementwise by voxel_size_mm (a 3-vector) to convert integer
    # grid indices into millimeter offsets within the grid's own frame.
    grid_mm = indices.astype(np.float64) * scene.voxel_size_mm
    # Rotate/translate those grid-frame points into world-frame millimeters.
    return scene.transform_w_from_grid.apply_points(grid_mm)


def _report_hit_points_and_depths(
    report: CollisionReport,
) -> tuple[ArrayF, ArrayF]:
    """Return world points and depths for every report debug sample.

    This is the *only* place collision hit geometry comes from in this
    module -- straight out of ``report.debug_samples``. We deliberately do
    NOT re-fire the beam through the scene here. That would be "hand-drawn"
    in the sense the issue warns against: a second, independently computed
    picture that could silently drift out of sync with what the structured
    report actually says. Reading debug_samples directly means the artifact
    is *provably* a rendering of this exact report, nothing else.
    """
    if not report.debug_samples:
        # No hits recorded (e.g. beam missed everything, or debug samples
        # weren't requested from the engine) -> return empty arrays with the
        # right shape so downstream code (`.size == 0` checks, add_points)
        # behaves correctly without special-casing "None" everywhere.
        return np.empty((0, 3)), np.empty((0,))
    # Each CollisionSample carries its own world-space point (point_w_mm)
    # and its penetration depth along the beam axis (depth_mm). Stack them
    # into two parallel numpy arrays: (N, 3) points and (N,) depths.
    points = np.asarray([s.point_w_mm for s in report.debug_samples])
    depths = np.asarray([s.depth_mm for s in report.debug_samples])
    return points, depths


def _beam_envelope_mesh(
    pv: Any,
    beam_model: FocusedHourglassBeamModel,
    beam: BeamRealization,
    *,
    depth_step_mm: float = 2.0,
    angular_samples: int = 72,
) -> Any:
    """Build a translucent surface around the hourglass beam envelope.

    Geometrically this is a "rings and quads" surface of revolution: at
    each depth along the beam axis we sample `angular_samples` points on a
    circle whose radius is `beam_model.radius_at_depth(depth)` (this is
    exactly why the mesh pinches in toward the focus depth and re-expands
    afterward -- an "hourglass" shape). Consecutive rings are then stitched
    together into quad faces to form a closed tube-like surface.
    """
    spec = beam.spec
    # Depth samples from 0mm (the probe face) out to max_depth_mm, stepped
    # every depth_step_mm. The `+ 0.5 * depth_step_mm` nudge on the stop
    # value makes sure we actually include max_depth_mm itself (np.arange's
    # stop bound is exclusive, so without the nudge we could lose the last
    # ring due to floating point rounding).
    depths = np.arange(
        0.0,
        spec.max_depth_mm + 0.5 * depth_step_mm,
        depth_step_mm,
        dtype=np.float64,
    )
    # Angles around the beam's local Z axis for one "ring" of the tube.
    # endpoint=False means we don't duplicate the 0/2*pi point (the face
    # list below wraps around via `% angular_samples` instead).
    angles = np.linspace(
        0.0,
        2.0 * np.pi,
        angular_samples,
        endpoint=False,
        dtype=np.float64,
    )

    # `points` accumulates every ring-vertex, in world-space millimeters,
    # in row-major (depth, then angle) order -- this ordering is what lets
    # the face-index math below stay simple.
    points: list[ArrayF] = []
    for depth in depths:
        # Local beam radius at this depth (this is the hourglass profile).
        radius = beam_model.radius_at_depth(float(depth))
        for theta in angles:
            # Point in the beam's own local coordinate frame: X/Y form the
            # circle at this depth, Z is "how far along the beam axis".
            point_b = np.array(
                [
                    radius * np.cos(theta),
                    radius * np.sin(theta),
                    depth,
                ],
                dtype=np.float64,
            )
            # Transform that beam-local point into world-space millimeters
            # using the beam's realized world<-beam rigid transform, and
            # stash it in the flat points list.
            points.append(beam.transform_w_from_beam.apply_point(point_b))

    # `faces` is PyVista's flat "connectivity" encoding for a PolyData
    # surface: for each polygon, first the vertex COUNT, then that many
    # vertex indices. Every face below is a quad, so each entry is
    # `[4, i0, i1, i2, i3]` flattened into the running list.
    faces: list[int] = []
    for depth_idx in range(len(depths) - 1):
        # Index of the first vertex in "this" ring and in the "next" ring
        # (one depth-step further along the beam).
        row_start = depth_idx * angular_samples
        next_row_start = (depth_idx + 1) * angular_samples
        for angle_idx in range(angular_samples):
            # Wrap around so the last angular slice connects back to the
            # first one, closing the tube into a full circle at every ring.
            next_angle_idx = (angle_idx + 1) % angular_samples
            faces.extend(
                [
                    4,  # this face has 4 vertices (a quad)
                    row_start + angle_idx,
                    row_start + next_angle_idx,
                    next_row_start + next_angle_idx,
                    next_row_start + angle_idx,
                ]
            )

    # Hand the flat points array + flat face-connectivity array to PyVista;
    # it assembles them into a renderable PolyData surface mesh.
    return pv.PolyData(
        np.asarray(points, dtype=np.float64),
        np.asarray(faces, dtype=np.int64),
    )


def _add_point_cloud(
    plotter: Any,
    points: ArrayF,
    *,
    color: str,
    label: str,
    point_size: float,
    opacity: float = 1.0,
) -> None:
    """Add a non-empty world-space point cloud to the plotter.

    Small wrapper mostly to avoid repeating the same five keyword args at
    every call site, and to centralize the "skip entirely if there's
    nothing to draw" guard (PyVista is unhappy being handed a zero-length
    point array).
    """
    if points.size == 0:
        return
    plotter.add_points(
        points,
        color=color,
        label=label,
        point_size=point_size,
        # Rendering points as actual shaded spheres (rather than flat
        # camera-facing squares) reads much better once the camera moves.
        render_points_as_spheres=True,
        opacity=opacity,
    )


def _artifact_checksum(path: Path) -> str:
    """Return the sha256 checksum of the rendered artifact file.

    Reads the whole file into memory and hashes it. Fine for artifacts this
    size (single-digit MB); if these ever get large, switch to chunked
    reading (`hashlib.sha256(); for chunk in f: h.update(chunk)`).
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_layer_manifest(hit_points: ArrayF, has_quality_flags: bool) -> dict[str, str]:
    """Return present/absent/deferred status for each recommended layer.

    This is a hand-authored map, not derived automatically, because
    "present vs deferred" is a *scope* decision (what this issue chose to
    build) as much as a *data* decision (what the report happens to
    contain). The two entries that ARE data-dependent
    (`collision_hit_points_intervals`, `quality_flag_markers`) are computed
    from the actual report contents so the manifest never claims a layer is
    "present" when there was nothing to draw.
    """
    return {
        "anatomy_background": "absent",
        "temporal_window_aperture": "deferred",
        "probe_face_and_axes": "present",
        "central_beam_axis": "present",
        "beam_frustum_envelope": "present",
        "ray_bundle_samples": "deferred",
        "labeled_vessel_masks": "present",
        "vessel_centerlines_graph_nodes": "absent",
        "collision_hit_points_intervals": (
            "present" if hit_points.size else "absent"
        ),
        "mesh_entry_exit_points": "deferred",
        "flow_direction_arrows": "deferred",
        "quality_flag_markers": "present" if has_quality_flags else "absent",
    }


def _panel_html(metadata_for_panel: dict[str, Any]) -> str:
    """Build the injectable "sidecar panel" HTML/CSS/JS block.

    Design goals:
      * Self-contained: everything (CSS, JS, and the metadata itself) lives
        in this one string, so the exported .html file stays a single file
        you can email/attach/open with no other assets required (other than
        the one Google Fonts CDN request for the typeface, which degrades
        gracefully to a system monospace font if that request fails, e.g.
        opening the file completely offline).
      * Non-destructive: it's appended right before `</body>`, so it never
        touches PyVista's own generated markup/scripts above it -- if
        PyVista's export format changes in a future version, this panel
        keeps working as long as `</body>` still exists.
      * Data-driven: we don't hand-author the panel's HTML row-by-row in
        Python string formatting (error-prone, easy to typo a field name).
        Instead we JSON-serialize the metadata dict once and let a small
        piece of client-side JS render it into the DOM. That also means the
        panel automatically stays correct if new metadata fields get added
        later -- no template to forget to update.

    Note: this panel intentionally does NOT include the artifact checksum.
    The checksum is computed *after* this panel is injected (it's a hash of
    the whole final file), so it can't meaningfully describe its own file's
    contents from the inside. The checksum lives in the .json sidecar only.
    """
    # json.dumps(..., default=str) -> if any value somehow isn't natively
    # JSON-serializable (shouldn't happen here, but cheap insurance), fall
    # back to str() instead of raising and losing the whole panel.
    metadata_json = json.dumps(metadata_for_panel, indent=2, default=str)

    palette = _NIER_PALETTE
    # Using an f-string (triple-quoted) to build one big HTML/CSS/JS blob.
    # Because it's an f-string, any literal `{` or `}` meant for CSS/JS
    # (not for Python interpolation) has to be doubled to `{{` / `}}` --
    # that's why you'll see `{{ }}` pairs all over the CSS/JS below.
    return f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{palette['font_import_url']}" rel="stylesheet">
<style>
  /* Toggle button: always-visible tab pinned to the top-right corner. */
  #nvr-sidecar-toggle {{
    position: fixed;
    top: 12px;
    right: 12px;
    z-index: 9999;
    font-family: {palette['font_family']};
    font-size: 13px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: {palette['panel_text']};
    background: {palette['panel_bg']};
    border: 1px solid {palette['panel_border']};
    padding: 6px 12px;
    cursor: pointer;
    user-select: none;
  }}
  #nvr-sidecar-toggle:hover {{
    color: {palette['panel_accent']};
    border-color: {palette['panel_accent']};
  }}
  /* The sliding panel itself. Hidden off-screen (translateX) by default;
     the "open" class (toggled by JS below) slides it into view. */
  #nvr-sidecar-panel {{
    position: fixed;
    top: 0;
    right: 0;
    height: 100%;
    width: min(420px, 92vw);
    background: {palette['panel_bg']};
    border-left: 1px solid {palette['panel_border']};
    color: {palette['panel_text']};
    font-family: {palette['font_family']};
    font-size: 12px;
    line-height: 1.5;
    box-sizing: border-box;
    padding: 56px 18px 18px 18px;
    overflow-y: auto;
    z-index: 9998;
    transform: translateX(100%);
    transition: transform 180ms ease-out;
  }}
  #nvr-sidecar-panel.nvr-open {{
    transform: translateX(0);
  }}
  #nvr-sidecar-panel h2 {{
    margin: 0 0 10px 0;
    font-size: 14px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {palette['panel_accent']};
    border-bottom: 1px solid {palette['panel_border']};
    padding-bottom: 8px;
  }}
  #nvr-sidecar-panel .nvr-hint {{
    opacity: 0.7;
    font-size: 11px;
    margin-bottom: 14px;
  }}
  #nvr-sidecar-panel pre {{
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
    font-family: {palette['font_family']};
    font-size: 11px;
  }}
</style>

<!-- The toggle tab: clicking it flips the "nvr-open" class on the panel. -->
<div id="nvr-sidecar-toggle" onclick="
  document.getElementById('nvr-sidecar-panel').classList.toggle('nvr-open')
">&raquo; sidecar data</div>

<!-- The panel: starts with no "nvr-open" class, so it's off-screen until
     the toggle button above is clicked. -->
<div id="nvr-sidecar-panel">
  <h2>Collision Report Sidecar</h2>
  <div class="nvr-hint">
    Report-derived metadata for this artifact. The artifact checksum
    (sha256) is recorded in the accompanying .json sidecar file, not here,
    since this panel is part of the file being checksummed.
  </div>
  <!-- The <pre id="nvr-sidecar-json"> below starts empty; the <script>
       tag right after it fills it in at load time using the embedded
       metadata object. Keeping the fill-in in JS (rather than baking the
       formatted text directly into this f-string) means we get JSON's own
       proven serialization/indentation instead of hand-rolled formatting. -->
  <pre id="nvr-sidecar-json"></pre>
</div>

<script>
  // The metadata dict, embedded verbatim as a JS object literal. This is
  // the SAME data that's written to the .json sidecar file (minus the
  // checksum field, added after this point in the Python pipeline).
  window.__NVR_SIDECAR_METADATA__ = {metadata_json};
  document.getElementById("nvr-sidecar-json").textContent =
    JSON.stringify(window.__NVR_SIDECAR_METADATA__, null, 2);
</script>
"""


def _inject_metadata_panel(html_path: Path, metadata_for_panel: dict[str, Any]) -> None:
    """Insert the sidecar panel into an already-exported PyVista HTML file.

    This runs as a post-processing step: PyVista's ``export_html`` has
    already written a complete, self-contained HTML document (typically
    ending in a `</body></html>` pair). We read that file back in as text,
    splice our panel markup in right before the closing `</body>` tag, and
    write the file back out in place. Doing it this way -- rather than
    trying to get PyVista/VTK.js to draw an HTML overlay itself -- keeps
    this module decoupled from PyVista's internal export format; all we
    depend on is "the output is HTML with a closing body tag", which is
    about as stable an assumption as you can make.
    """
    html_text = html_path.read_text()
    panel_markup = _panel_html(metadata_for_panel)
    # str.replace with count=1 -> only touch the *first* occurrence of
    # "</body>". PyVista's export template only has one, but being
    # explicit about count=1 protects us if that ever changes.
    if "</body>" in html_text:
        html_text = html_text.replace("</body>", panel_markup + "</body>", 1)
    else:
        # Defensive fallback: if some future PyVista version doesn't emit a
        # closing body tag at all, just tack the panel onto the very end
        # rather than silently dropping it.
        html_text = html_text + panel_markup
    html_path.write_text(html_text)


def generate_artifact(
    out_dir: Path,
    scene: SceneAsset,
    beam_model: FocusedHourglassBeamModel,
    beam: BeamRealization,
    report: CollisionReport,
) -> tuple[Path, Path]:
    """Build the CollisionReport-derived HTML visualization and JSON sidecar.

    Args:
        out_dir: Directory to write the HTML and JSON artifacts into.
        scene: The scene asset the report was generated against.
        beam_model: The beam model used to realize ``beam``.
        beam: The realized beam used to generate ``report``.
        report: The structured collision report to render and describe.

    Returns:
        ``(html_path, json_path)`` for the generated artifacts.
    """
    # Make sure the output directory exists (mkdir -p semantics: create any
    # missing parent directories too, and don't error if it already exists).
    out_dir.mkdir(parents=True, exist_ok=True)

    # Lazily grab the real (or, in tests, faked) pyvista module.
    pv = _require_pyvista()
    # World-space axis-aligned bounding box of the whole scene, used both
    # for the on-screen grid/bounds display and recorded in the metadata.
    world_min, world_max = scene_bounds_world(scene)

    # The one and only source of collision-evidence geometry: the report's
    # own debug samples (see docstring on _report_hit_points_and_depths).
    hit_points, hit_depths = _report_hit_points_and_depths(report)
    # Build the hourglass beam-envelope surface mesh once, reused below.
    beam_envelope = _beam_envelope_mesh(pv, beam_model, beam)

    spec = beam.spec
    # `beam.origin_w_mm` + `beam.axis_w` describe the beam as a ray in world
    # space: origin is the probe face position, axis_w is a unit vector
    # pointing "into" the tissue. Scaling axis_w by a depth and adding it to
    # the origin gives you the world-space point at that depth along the
    # beam -- used here to find the focus point and the far end of the axis
    # line we draw.
    origin = beam.origin_w_mm
    focus = origin + spec.focus_depth_mm * beam.axis_w
    axis_end = origin + spec.max_depth_mm * beam.axis_w

    palette = _NIER_PALETTE

    # off_screen=True -> render into a framebuffer, never pop up a real
    # window (required for headless CI / server rendering).
    plotter = pv.Plotter(off_screen=True, window_size=[1200, 900])
    # Vertical gradient background: `top=` is the color at the top of the
    # viewport, the first positional `color` arg is the bottom color. This
    # is what gives the scene its "parchment sky fading to khaki floor"
    # look instead of a flat white background.
    plotter.set_background(palette["bg_bottom"], top=palette["bg_top"])

    # --- Layer 1: vessel geometry -------------------------------------
    # One point cloud per labeled vessel in the ontology (there may be more
    # than one label even though the known-answer scene only has L-MCA
    # today), kept visually distinct in color/opacity from the report's own
    # hit-point layer below.
    for label_id, label_def in sorted(scene.ontology.labels.items()):
        vessel_points = _label_points_world(scene, label_id)
        _add_point_cloud(
            plotter,
            vessel_points,
            color=palette["vessel"],
            label=label_def.abbreviation,
            point_size=7.0,
            opacity=0.35,
        )

    # --- Layer 2: beam frustum / envelope -------------------------------
    plotter.add_mesh(
        beam_envelope,
        color=palette["beam_envelope"],
        opacity=0.15,
        label="Hourglass beam envelope",
        show_edges=False,
    )

    # --- Layer 3: report-derived collision/depth evidence ---------------
    # Colored by depth (not a flat color) so it visually reads as
    # *evidence* rather than a re-skinned copy of the vessel layer above.
    # `scalars=hit_depths` + `cmap=...` tells PyVista to color each point
    # according to where its depth value falls in the colormap.
    if hit_points.size:
        plotter.add_points(
            hit_points,
            scalars=hit_depths,
            cmap=palette["hit_depth_cmap"],
            point_size=9.0,
            render_points_as_spheres=True,
            opacity=0.95,
            show_scalar_bar=True,
            scalar_bar_args={"title": "Hit depth (mm)"},
        )

    # --- Layer 4: beam axis + probe/focus markers -----------------------
    plotter.add_mesh(
        pv.Line(origin, axis_end),
        color=palette["beam_axis"],
        line_width=3,
        label="Beam axis",
    )
    plotter.add_mesh(
        pv.Sphere(radius=1.6, center=origin),
        color=palette["probe_origin"],
        label="Probe origin",
    )
    plotter.add_mesh(
        pv.Sphere(radius=1.4, center=focus),
        color=palette["focus_point"],
        label="Focus point",
    )

    # --- Chrome: bounding box, floor/wall grid, axes, legend, camera ----
    plotter.add_bounding_box(color=palette["bounds"], line_width=1)
    plotter.show_bounds(
        bounds=(
            float(world_min[0]),
            float(world_max[0]),
            float(world_min[1]),
            float(world_max[1]),
            float(world_min[2]),
            float(world_max[2]),
        ),
        grid="back",
        location="outer",
        xtitle="World X (mm)",
        ytitle="World Y (mm)",
        ztitle="World Z (mm)",
        font_size=10,
        color=palette["bounds"],
    )
    plotter.add_axes()
    plotter.add_legend(size=(0.22, 0.22), loc="upper right")
    # A fixed, hand-picked camera position/focal-point/up-vector that frames
    # the known-answer synthetic scene nicely. If you generalize this to
    # arbitrary scenes later, replace this with `plotter.camera.reset()` or
    # compute a position from `world_min`/`world_max` instead of hardcoding.
    plotter.camera_position = [
        (145.0, -105.0, 105.0),
        (50.0, 50.0, 50.0),
        (0.0, 0.0, 1.0),
    ]

    # Filename includes the scene_id so multiple renders don't collide and
    # so it's obvious at a glance which scene an artifact belongs to.
    html_path = out_dir / f"{report.scene_id}_collision_report.html"
    try:
        # export_html writes a fully self-contained HTML+JS viewer (VTK.js
        # embedded) -- no server, no separate asset files needed to view it.
        plotter.export_html(html_path)
    finally:
        # Always release the off-screen render context, even if export
        # raised -- otherwise repeated calls (e.g. in a test loop) can leak
        # GPU/VTK resources.
        plotter.close()

    # True if ANY interaction in the report carries at least one quality
    # flag (e.g. "VOXEL_SAMPLED", low-confidence markers, etc). Used both
    # in the metadata and to decide the quality_flag_markers layer status.
    has_quality_flags = any(vi.quality_flags for vi in report.interactions)
    layers = _build_layer_manifest(hit_points, has_quality_flags)

    # This is the full metadata dict -- the single source of truth that
    # BOTH the injected HTML panel and the .json sidecar are built from.
    # Building it once and reusing it for both destinations guarantees they
    # can never disagree with each other.
    metadata: dict[str, Any] = {
        # Fresh random ID + UTC creation timestamp identifying THIS render.
        "artifact_id": str(uuid.uuid4()),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "renderer": RENDERER_NAME,
        "renderer_version": RENDERER_VERSION,
        # Coordinate frame + units every point/measurement in this artifact
        # is expressed in -- critical for anyone downstream trying to
        # cross-reference this artifact against other tooling.
        "coordinate_frame": scene.transform_w_from_grid.target_frame,
        "units": "mm",
        "scene_id": report.scene_id,
        "beam_id": report.beam_id,
        "beam_model_id": spec.model_id,
        "fidelity_level": report.fidelity_level,
        # dataclasses.asdict() recursively converts a (frozen) dataclass
        # instance into a plain dict of JSON-serializable primitives --
        # this is why `command` and `beam_spec` below are dicts, not the
        # original dataclass objects (which json.dumps can't serialize
        # directly).
        "command": dataclasses.asdict(report.command),
        "beam_spec": dataclasses.asdict(spec),
        # One entry per VesselInteraction the report recorded -- this is
        # the "structured" summary of what the beam hit, independent of the
        # raw per-sample debug_samples used for the 3D point cloud above.
        "interactions": [
            {
                "label_id": vi.label_id,
                "label_name": vi.label_name,
                "label_abbreviation": vi.label_abbreviation,
                "depth_interval_mm": [vi.depth_min_mm, vi.depth_max_mm],
                "max_coverage_fraction": vi.max_coverage_fraction,
                "mean_coverage_fraction": vi.mean_coverage_fraction,
                "hit_sample_count": vi.hit_sample_count,
                "quality_flags": list(vi.quality_flags),
            }
            for vi in report.interactions
        ],
        "warnings": list(report.warnings),
        "quality_flags_available": has_quality_flags,
        "layers": layers,
        "world_min_mm": world_min.tolist(),
        "world_max_mm": world_max.tolist(),
        "voxel_size_mm": scene.voxel_size_mm.tolist(),
        "vessel_label_count": int(len(scene.ontology.labels)),
        "collision_sample_count": int(len(report.debug_samples)),
        # Fields the current contracts do not yet expose. Recorded
        # explicitly (rather than silently omitted) per the issue's
        # acceptance criteria -- an absent field and an *acknowledged*
        # absent field mean very different things to a reviewer.
        "traceability_limitations": [
            "collision_report_id: CollisionReport has no report identifier "
            "field yet",
            "command_id: ProbeCommand has no command identifier field yet",
            "beam_model_version_or_param_hash: BeamSpec has no version or "
            "parameter hash field yet (only model_id)",
            "window_model_id: TemporalWindowModel/ProbePose expose no "
            "window identifier field yet",
            "realized_pose_id: ProbePose has no pose identifier field yet",
            "simulator_version: no package-level simulator version constant "
            "is currently wired into this helper",
        ],
        # The exact structured inputs used to produce this artifact, so a
        # reviewer (or another tool) can re-derive/re-render it later
        # without needing to reverse-engineer it from the picture alone.
        "source_inputs": {
            "scene_id": scene.scene_id,
            "command": dataclasses.asdict(report.command),
            "beam_spec": dataclasses.asdict(spec),
        },
    }

    # Inject the human-readable sidecar panel into the HTML file we already
    # wrote to disk. We deliberately do this BEFORE computing the checksum
    # below, so the checksum reflects the file's true final byte contents
    # (including the panel), not a pre-panel intermediate state.
    _inject_metadata_panel(html_path, metadata)

    json_path = out_dir / f"{report.scene_id}_collision_report_metadata.json"
    # First write: lets us persist metadata to disk before we know the
    # checksum (which depends on the finished html_path contents).
    json_path.write_text(json.dumps(metadata, indent=2))

    # Checksum is computed after the HTML is fully finalized (panel and
    # all), then folded back into the metadata dict and the file is
    # rewritten so the .json sidecar carries the checksum too.
    metadata["artifact_checksum_sha256"] = _artifact_checksum(html_path)
    json_path.write_text(json.dumps(metadata, indent=2))

    return html_path, json_path


def build_default_report_and_inputs(
    angle_rad: float = np.pi / 4,
) -> tuple[SceneAsset, FocusedHourglassBeamModel, BeamRealization, CollisionReport]:
    """Build the known-answer synthetic MCA scene, beam, and report.

    This is the "known-answer" harness used by the CLI entry point and by
    tests: a centered synthetic MCA phantom with the default probe command,
    default window, and default beam spec. It's the only place in this
    module that's allowed to actually RUN the collision engine -- everything
    else (generate_artifact and its helpers) only ever consumes an
    already-built CollisionReport that's handed to it.
    """
    # Build the synthetic angled-cylinder MCA phantom scene (voxel volume +
    # vessel labels) at the requested centerline angle.
    scene = build_angled_cylinder_mca_phantom(angle_rad=angle_rad)
    # Default (centered, zero-rotation) probe steering command.
    command = ProbeCommand()
    # Default left-temporal acoustic window (aperture/FOV constraints).
    window = default_left_temporal_window()
    # "Realize" the window+command combo into a concrete world-space probe
    # pose (position + orientation).
    pose = window.realize(command)

    # Default beam spec/model, realized against the pose above to get an
    # actual set of world-space beam samples.
    spec = BeamSpec()
    beam_model = FocusedHourglassBeamModel(spec=spec)
    beam = beam_model.realize(pose)

    # Fire the beam through the scene: fidelity_level=2 controls sampling
    # density/accuracy tradeoffs inside the engine; include_debug_samples
    # is required here since that's exactly what this visualizer reads to
    # draw the collision/depth-evidence layer.
    report = VoxelCollisionEngine(
        fidelity_level=2,
        include_debug_samples=True,
    ).evaluate(scene, beam, command)

    return scene, beam_model, beam, report


def main() -> None:
    """CLI entry point: ``python -m nvsim_collision.vis.collision_report_vis``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("vis_artifacts"),
        help="Output directory for HTML and JSON sidecar.",
    )
    parser.add_argument(
        "--angle",
        type=float,
        default=np.pi / 4,
        help="Angle in radians for the synthetic MCA phantom.",
    )
    args = parser.parse_args()
    # Build the known-answer inputs, then render exactly what was built --
    # no hidden extra computation happens inside generate_artifact itself.
    scene, beam_model, beam, report = build_default_report_and_inputs(args.angle)
    html, jsn = generate_artifact(args.out, scene, beam_model, beam, report)
    print(f"HTML -> {html}")
    print(f"JSON -> {jsn}")


if __name__ == "__main__":
    main()
