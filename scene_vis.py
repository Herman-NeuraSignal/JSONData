"""
scene_vis.py — synthetic scene visualization artifact helper.

Produces a static PNG showing the MCA phantom geometry + default beam,
plus a JSON metadata sidecar. Headless, deterministic, CI-safe.

Usage:
    python scene_vis.py                    # writes to ./vis_artifacts/
    python scene_vis.py --out /some/dir    # writes to a custom directory
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless — no display server required

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

# ── project imports ────────────────────────────────────────────────────────────
# Make sure you run this from the repo root, or install the package first:
#   pip install -e .
from collision_sim_mvp.contracts import BeamSpec, ProbeCommand
from collision_sim_mvp.scene.scene import scene_bounds_world
from collision_sim_mvp.scene.synthetic import build_straight_mca_phantom
from collision_sim_mvp.window.window import default_left_temporal_window
from collision_sim_mvp.beam.beam import FocusedHourglassBeamModel


# ── helpers ────────────────────────────────────────────────────────────────────

def _vessel_slice_xz(scene) -> np.ndarray:
    """
    Return a 2-D boolean mask of the L-MCA vessel in the XZ plane at y=50.

    The phantom is 3-D (x, y, z).  We take a single y-slice so we can draw
    the vessel cross-section in the X-Z plane — the same plane the beam
    travels through.
    """
    mid_y = scene.label_volume.shape[1] // 2   # y = 50 for the default 100³ volume
    slice_xz = scene.label_volume[:, mid_y, :]  # shape (100, 100)
    return slice_xz == 1   # True where label is L-MCA (label_id = 1)


def generate_artifact(out_dir: Path) -> tuple[Path, Path]:
    """
    Build the PNG + JSON sidecar.  Returns (png_path, json_path).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. build scene ────────────────────────────────────────────────────────
    scene = build_straight_mca_phantom()

    # label info for the L-MCA (label_id = 1)
    lmca = scene.ontology.labels[1]

    # scene bounds in world-space millimetres
    world_min, world_max = scene_bounds_world(scene)

    # ── 2. realise the default probe command & beam ───────────────────────────
    default_command = ProbeCommand()          # all zeros: x=0, y=0, rx=0, ry=0
    window = default_left_temporal_window()
    pose   = window.realize(default_command)

    spec   = BeamSpec()
    beam_model = FocusedHourglassBeamModel(spec=spec)
    beam   = beam_model.realize(pose)

    # ── 3. collect beam samples in the XZ plane (cross_y ≈ 0) ────────────────
    # Each BeamSample has a world-space point.  We project onto X-Z.
    beam_xs = np.array([s.point_w_mm[0] for s in beam.samples if abs(s.cross_y_mm) < 0.5])
    beam_zs = np.array([s.point_w_mm[2] for s in beam.samples if abs(s.cross_y_mm) < 0.5])

    # ── 4. vessel mask for drawing ────────────────────────────────────────────
    vessel_mask = _vessel_slice_xz(scene)   # (100, 100) bool, axes are x, z

    # Physical extents for imshow so axes show mm not voxel indices
    x_extent = [world_min[0], world_max[0]]   # 0 … 99 mm
    z_extent = [world_min[2], world_max[2]]

    # ── 5. draw ───────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 6), dpi=120)

    # vessel cross-section — imshow: rows=first axis (x), cols=second axis (z)
    # We transpose so x goes left-right and z goes up-down.
    ax.imshow(
        vessel_mask.T,                        # (z, x) after transpose
        origin="lower",
        extent=[x_extent[0], x_extent[1], z_extent[0], z_extent[1]],
        cmap="Blues",
        alpha=0.5,
        vmin=0,
        vmax=1,
    )

    # beam centre-line samples (cross_y ≈ 0)
    ax.scatter(beam_xs, beam_zs, s=4, c="orangered", label="Beam samples (cross-y≈0)")

    # probe origin marker
    origin = beam.origin_w_mm
    ax.plot(origin[0], origin[2], marker="D", color="green", ms=8, label="Probe origin")

    # focus depth marker
    focus_x = origin[0] + spec.focus_depth_mm * beam.axis_w[0]
    focus_z = origin[2] + spec.focus_depth_mm * beam.axis_w[2]
    ax.plot(focus_x, focus_z, marker="*", color="gold", ms=12, label=f"Focus depth ({spec.focus_depth_mm:.0f} mm)")

    # vessel label annotation
    vessel_pixels = np.argwhere(vessel_mask)  # shape (N, 2), axes (x-idx, z-idx)
    if vessel_pixels.size:
        cx = vessel_pixels[:, 0].mean() * scene.voxel_size_mm[0]
        cz = vessel_pixels[:, 1].mean() * scene.voxel_size_mm[2]
        ax.annotate(
            lmca.abbreviation,
            xy=(cx, cz),
            xytext=(cx + 8, cz + 8),
            fontsize=10,
            color="steelblue",
            arrowprops=dict(arrowstyle="->", color="steelblue"),
        )

    ax.set_xlabel("World X (mm)")
    ax.set_ylabel("World Z (mm)")
    ax.set_title(
        f"Synthetic scene: {scene.scene_id}\n"
        f"XZ mid-plane (y=50 mm) · Beam: {spec.model_id}"
    )
    ax.legend(loc="upper right", fontsize=8)
    ax.set_aspect("equal")

    png_path = out_dir / f"{scene.scene_id}.png"
    fig.savefig(png_path, bbox_inches="tight")
    plt.close(fig)

    # ── 6. metadata sidecar ───────────────────────────────────────────────────
    metadata = {
        # traceability fields required by the issue
        "scene_id":         scene.scene_id,
        "label_id":         lmca.label_id,
        "label_name":       lmca.name,
        "label_abbreviation": lmca.abbreviation,
        "coordinate_frame": scene.transform_w_from_grid.target_frame,  # "world"
        "units":            "mm",
        "renderer":         "scene_vis/matplotlib-agg/v0.1",

        # command + beam inputs used for this view
        "command": {
            "x_mm":   default_command.x_mm,
            "y_mm":   default_command.y_mm,
            "rx_rad": default_command.rx_rad,
            "ry_rad": default_command.ry_rad,
        },
        "beam_spec": {
            "model_id":          spec.model_id,
            "focus_depth_mm":    spec.focus_depth_mm,
            "waist_radius_mm":   spec.waist_radius_mm,
            "aperture_radius_mm": spec.aperture_radius_mm,
            "max_depth_mm":      spec.max_depth_mm,
        },
        "beam_id": beam.beam_id,

        # scene geometry summary
        "world_min_mm": world_min.tolist(),
        "world_max_mm": world_max.tolist(),
        "voxel_size_mm": scene.voxel_size_mm.tolist(),
    }

    json_path = out_dir / f"{scene.scene_id}_metadata.json"
    json_path.write_text(json.dumps(metadata, indent=2))

    return png_path, json_path


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("vis_artifacts"),
        help="Output directory for PNG and JSON sidecar.",
    )
    args = parser.parse_args()

    png, jsn = generate_artifact(args.out)
    print(f"PNG  → {png}")
    print(f"JSON → {jsn}")


if __name__ == "__main__":
    main()
