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

from __future__ import annotations


import argparse


import dataclasses


import hashlib


import importlib


import json


import uuid


from datetime import datetime, timezone


from pathlib import Path


from typing import Any


import numpy as np


from numpy.typing import NDArray


from nvsim_collision.beam import FocusedHourglassBeamModel


from nvsim_collision.collision import VoxelCollisionEngine


from nvsim_collision.contracts import (
    BeamRealization,
    BeamSpec,
    CollisionReport,
    ProbeCommand,
    SceneAsset,
)


from nvsim_collision.scene import (
    build_angled_cylinder_mca_phantom,
    scene_bounds_world,
)


from nvsim_collision.window import default_left_temporal_window

ArrayF = NDArray[np.float64]


RENDERER_NAME = "collision_report_vis"


RENDERER_VERSION = "collision_report_vis/pyvista-html/v0.1"


_NIER_PALETTE: dict[str, str] = {
    "bg_top": "#EFE7D6",
    "bg_bottom": "#C7BEA3",
    "vessel": "#5B5647",
    "beam_envelope": "#B08D57",
    "beam_axis": "#2B2B26",
    "probe_origin": "#C9A227",
    "focus_point": "#A0522D",
    "bounds": "#8A8272",
    "hit_depth_cmap": "copper",
    "panel_bg": "#EFE8D8",
    "panel_border": "#8A8272",
    "panel_text": "#2B2B26",
    "panel_accent": "#8A6D1F",
    "font_family": "'Cutive Mono', 'Courier New', monospace",
    "font_import_url": (
        "https://fonts.googleapis.com/css2?family=Cutive+Mono&display=swap"
    ),
}


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

        return importlib.import_module("pyvista")
    except ImportError as exc:

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

    indices = np.argwhere(scene.label_volume == label_id)

    grid_mm = indices.astype(np.float64) * scene.voxel_size_mm

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

        return np.empty((0, 3)), np.empty((0,))

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

    depths = np.arange(
        0.0,
        spec.max_depth_mm + 0.5 * depth_step_mm,
        depth_step_mm,
        dtype=np.float64,
    )

    angles = np.linspace(
        0.0,
        2.0 * np.pi,
        angular_samples,
        endpoint=False,
        dtype=np.float64,
    )

    points: list[ArrayF] = []
    for depth in depths:

        radius = beam_model.radius_at_depth(float(depth))
        for theta in angles:

            point_b = np.array(
                [
                    radius * np.cos(theta),
                    radius * np.sin(theta),
                    depth,
                ],
                dtype=np.float64,
            )

            points.append(beam.transform_w_from_beam.apply_point(point_b))

    faces: list[int] = []
    for depth_idx in range(len(depths) - 1):

        row_start = depth_idx * angular_samples
        next_row_start = (depth_idx + 1) * angular_samples
        for angle_idx in range(angular_samples):

            next_angle_idx = (angle_idx + 1) % angular_samples
            faces.extend(
                [
                    4,
                    row_start + angle_idx,
                    row_start + next_angle_idx,
                    next_row_start + next_angle_idx,
                    next_row_start + angle_idx,
                ]
            )

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


def _build_layer_manifest(
    hit_points: ArrayF, has_quality_flags: bool
) -> dict[str, str]:
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
        "collision_hit_points_intervals": ("present" if hit_points.size else "absent"),
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

    metadata_json = json.dumps(metadata_for_panel, indent=2, default=str)

    palette = _NIER_PALETTE

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

    if "</body>" in html_text:
        html_text = html_text.replace("</body>", panel_markup + "</body>", 1)
    else:

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

    out_dir.mkdir(parents=True, exist_ok=True)

    pv = _require_pyvista()

    world_min, world_max = scene_bounds_world(scene)

    hit_points, hit_depths = _report_hit_points_and_depths(report)

    beam_envelope = _beam_envelope_mesh(pv, beam_model, beam)

    spec = beam.spec

    origin = beam.origin_w_mm
    focus = origin + spec.focus_depth_mm * beam.axis_w
    axis_end = origin + spec.max_depth_mm * beam.axis_w

    palette = _NIER_PALETTE

    plotter = pv.Plotter(off_screen=True, window_size=[1200, 900])

    plotter.set_background(palette["bg_bottom"], top=palette["bg_top"])

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

    plotter.add_mesh(
        beam_envelope,
        color=palette["beam_envelope"],
        opacity=0.15,
        label="Hourglass beam envelope",
        show_edges=False,
    )

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

    plotter.camera_position = [
        (145.0, -105.0, 105.0),
        (50.0, 50.0, 50.0),
        (0.0, 0.0, 1.0),
    ]

    html_path = out_dir / f"{report.scene_id}_collision_report.html"
    try:

        plotter.export_html(html_path)
    finally:

        plotter.close()

    has_quality_flags = any(vi.quality_flags for vi in report.interactions)
    layers = _build_layer_manifest(hit_points, has_quality_flags)

    metadata: dict[str, Any] = {
        "artifact_id": str(uuid.uuid4()),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "renderer": RENDERER_NAME,
        "renderer_version": RENDERER_VERSION,
        "coordinate_frame": scene.transform_w_from_grid.target_frame,
        "units": "mm",
        "scene_id": report.scene_id,
        "beam_id": report.beam_id,
        "beam_model_id": spec.model_id,
        "fidelity_level": report.fidelity_level,
        "command": dataclasses.asdict(report.command),
        "beam_spec": dataclasses.asdict(spec),
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
        "source_inputs": {
            "scene_id": scene.scene_id,
            "command": dataclasses.asdict(report.command),
            "beam_spec": dataclasses.asdict(spec),
        },
    }

    _inject_metadata_panel(html_path, metadata)

    json_path = out_dir / f"{report.scene_id}_collision_report_metadata.json"

    json_path.write_text(json.dumps(metadata, indent=2))

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

    scene = build_angled_cylinder_mca_phantom(angle_rad=angle_rad)

    command = ProbeCommand()

    window = default_left_temporal_window()

    pose = window.realize(command)

    spec = BeamSpec()
    beam_model = FocusedHourglassBeamModel(spec=spec)
    beam = beam_model.realize(pose)

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

    scene, beam_model, beam, report = build_default_report_and_inputs(args.angle)
    html, jsn = generate_artifact(args.out, scene, beam_model, beam, report)
    print(f"HTML -> {html}")
    print(f"JSON -> {jsn}")


if __name__ == "__main__":
    main()
