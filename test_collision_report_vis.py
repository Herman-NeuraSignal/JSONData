"""Tests for the CollisionReport-driven visualization artifact helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nvsim_collision.vis import collision_report_vis


class FakePlotter:
    def __init__(self, *, off_screen: bool, window_size: list[int]) -> None:
        self.camera_position: list[tuple[float, float, float]] = []

    def set_background(self, color: str, top: str | None = None) -> None:
        pass

    def add_points(self, points: object, **kwargs: object) -> None:
        pass

    def add_mesh(self, mesh: object, **kwargs: object) -> None:
        pass

    def add_bounding_box(self, **kwargs: object) -> None:
        pass

    def show_bounds(self, **kwargs: object) -> None:
        pass

    def add_axes(self) -> None:
        pass

    def add_legend(self, **kwargs: object) -> None:
        pass

    def export_html(self, filename: Path) -> None:
        Path(filename).write_text("<html><body>collision report</body></html>")

    def close(self) -> None:
        pass


class FakePv:
    Plotter = FakePlotter

    @staticmethod
    def PolyData(points: object, faces: object) -> object:
        return {"points": points, "faces": faces}

    @staticmethod
    def Line(start: object, end: object) -> object:
        return {"start": start, "end": end}

    @staticmethod
    def Sphere(radius: float, center: object) -> object:
        return {"radius": radius, "center": center}


@pytest.fixture(scope="module")
def report_inputs():
    return collision_report_vis.build_default_report_and_inputs()


@pytest.fixture(scope="module")
def artifact_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("collision_report_vis") / "vis_artifacts"


@pytest.fixture(scope="module")
def artifacts(artifact_dir: Path, report_inputs) -> tuple[Path, Path]:
    scene, beam_model, beam, report = report_inputs
    patch = pytest.MonkeyPatch()
    patch.setattr(collision_report_vis, "_require_pyvista", lambda: FakePv)
    try:
        html, jsn = collision_report_vis.generate_artifact(
            artifact_dir, scene, beam_model, beam, report
        )
        return html, jsn
    finally:
        patch.undo()


class TestHtmlCreation:
    def test_html_file_exists(self, artifacts: tuple[Path, Path]) -> None:
        html, _ = artifacts
        assert html.exists()

    def test_html_has_nonzero_size(self, artifacts: tuple[Path, Path]) -> None:
        html, _ = artifacts
        assert html.stat().st_size > 0

    def test_html_filename_contains_scene_id(
        self, artifacts: tuple[Path, Path], report_inputs
    ) -> None:
        html, _ = artifacts
        _, _, _, report = report_inputs
        assert report.scene_id in html.name


class TestEmbeddedSidecarPanel:
    def test_html_contains_sidecar_toggle(
        self, artifacts: tuple[Path, Path]
    ) -> None:
        html, _ = artifacts
        assert "nvr-sidecar-toggle" in html.read_text()

    def test_html_contains_embedded_metadata(
        self, artifacts: tuple[Path, Path]
    ) -> None:
        html, _ = artifacts
        assert "__NVR_SIDECAR_METADATA__" in html.read_text()

    def test_embedded_metadata_scene_id_matches_report(
        self, artifacts: tuple[Path, Path], report_inputs
    ) -> None:
        html, _ = artifacts
        _, _, _, report = report_inputs
        assert f'"scene_id": "{report.scene_id}"' in html.read_text()


class TestJsonSidecar:
    def test_json_is_parseable(self, artifacts: tuple[Path, Path]) -> None:
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert isinstance(data, dict)

    def test_required_top_level_fields_present(
        self, artifacts: tuple[Path, Path]
    ) -> None:
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        required = {
            "scene_id",
            "command",
            "beam_id",
            "beam_model_id",
            "coordinate_frame",
            "units",
            "artifact_id",
            "created_at_utc",
            "artifact_checksum_sha256",
            "renderer",
            "renderer_version",
        }
        assert required <= data.keys()

    def test_checksum_matches_html_file(
        self, artifacts: tuple[Path, Path]
    ) -> None:
        html, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert data["artifact_checksum_sha256"] == (
            collision_report_vis._artifact_checksum(html)
        )

    def test_depth_interval_matches_structured_report(
        self, artifacts: tuple[Path, Path], report_inputs
    ) -> None:
        _, jsn = artifacts
        _, _, _, report = report_inputs
        data = json.loads(jsn.read_text())
        assert len(data["interactions"]) == len(report.interactions)
        for reported, source in zip(data["interactions"], report.interactions):
            assert reported["depth_interval_mm"] == [
                source.depth_min_mm,
                source.depth_max_mm,
            ]
            assert reported["label_id"] == source.label_id

    def test_traceability_limitations_recorded(
        self, artifacts: tuple[Path, Path]
    ) -> None:
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert len(data["traceability_limitations"]) > 0

    def test_layer_manifest_covers_recommended_layers(
        self, artifacts: tuple[Path, Path]
    ) -> None:
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert set(data["layers"].keys()) == set(
            collision_report_vis._LAYER_KEYS
        )
        assert set(data["layers"].values()) <= {"present", "absent", "deferred"}

    def test_collision_layer_present_when_samples_exist(
        self, artifacts: tuple[Path, Path], report_inputs
    ) -> None:
        _, jsn = artifacts
        _, _, _, report = report_inputs
        data = json.loads(jsn.read_text())
        if report.debug_samples:
            assert data["layers"]["collision_hit_points_intervals"] == "present"

    def test_source_inputs_recorded(self, artifacts: tuple[Path, Path]) -> None:
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert "source_inputs" in data
        assert "command" in data["source_inputs"]
        assert "beam_spec" in data["source_inputs"]

    def test_units_and_frame(self, artifacts: tuple[Path, Path]) -> None:
        _, jsn = artifacts
        data = json.loads(jsn.read_text())
        assert data["units"] == "mm"
        assert data["coordinate_frame"] == "world"


def test_report_uses_structured_report_not_hardcoded(report_inputs) -> None:
    scene, beam_model, beam, report = report_inputs
    assert report.scene_id == scene.scene_id
    assert report.beam_id == beam.beam_id
