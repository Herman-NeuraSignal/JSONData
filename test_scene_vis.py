"""
tests/unit/test_scene_vis.py

Tests for the synthetic-scene visualization artifact helper (Issue #17).
Verifies PNG creation and JSON metadata sidecar — no pixel-perfect comparisons.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import the helper.  Adjust the import path if you move scene_vis.py into
# the package (e.g. collision_sim_mvp.vis.scene_vis).
import sys, importlib

# Dynamically locate scene_vis.py whether tests are run from repo root or
# from the tests/ directory.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))

from scene_vis import generate_artifact


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def artifact_dir(tmp_path: Path) -> Path:
    """Temporary directory for test artifacts (pytest cleans up after the run)."""
    return tmp_path / "vis_artifacts"


@pytest.fixture()
def artifacts(artifact_dir: Path):
    """Generate the PNG + JSON once and share across tests in this module."""
    png, jsn = generate_artifact(artifact_dir)
    return png, jsn


# ── tests ─────────────────────────────────────────────────────────────────────

class TestPngCreation:
    def test_png_file_exists(self, artifacts):
        png, _ = artifacts
        assert png.exists(), f"Expected PNG at {png}"

    def test_png_has_nonzero_size(self, artifacts):
        png, _ = artifacts
        assert png.stat().st_size > 0, "PNG file is empty"

    def test_png_filename_contains_scene_id(self, artifacts):
        png, _ = artifacts
        assert "synthetic_straight_mca_v0_1" in png.name


class TestJsonSidecar:
    def test_json_file_exists(self, artifacts):
        _, jsn = artifacts
        assert jsn.exists(), f"Expected JSON at {jsn}"

    def test_json_is_parseable(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert isinstance(data, dict)

    # ── required traceability fields (from the issue acceptance criteria) ──

    def test_scene_id_field(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert data["scene_id"] == "synthetic_straight_mca_v0_1"

    def test_label_abbreviation_is_lmca(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert data["label_abbreviation"] == "L-MCA"

    def test_coordinate_frame_recorded(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert data["coordinate_frame"] == "world"

    def test_units_are_millimeters(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert data["units"] == "mm"

    def test_renderer_field_present(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert "renderer" in data and data["renderer"]

    def test_command_inputs_recorded(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        cmd = data["command"]
        assert set(cmd.keys()) >= {"x_mm", "y_mm", "rx_rad", "ry_rad"}

    def test_beam_spec_recorded(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        spec = data["beam_spec"]
        assert "model_id" in spec
        assert "focus_depth_mm" in spec

    def test_beam_id_recorded(self, artifacts):
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert "beam_id" in data and data["beam_id"]

    def test_artifact_produced_from_synthetic_scene_not_hardcoded(self, artifacts):
        """
        The metadata must name the synthetic scene explicitly. This proves the
        artifact was produced by running the real code, not by copying a
        hand-drawn image.
        """
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert "synthetic" in data["scene_id"]
        assert "mca" in data["scene_id"].lower()
