from __future__ import annotations

import pytest

from missions.common.actions.gps_capture_view import GpsCaptureViewAction
from missions.common.actions.gps_fuse_views import _output_item
from guidance.gps_derived_enu_fusion import GpsDerivedEnuFusion, GpsFusionConfig, GpsLocalizedObject
from guidance.target_projection import GpsProjectionCamera, GpsRawEstimate, GpsTargetProjector


def test_gps_capture_view_projects_without_effects() -> None:
    action = GpsCaptureViewAction()
    action.start({"class_names": ["bucket"], "min_confidence": 0.3,
                  "source_waypoint": "DROP_SCAN_1"})
    result = action.update({
        "drone": {"lat": 34.0, "lon": 108.0, "yaw": 0.0, "relative_altitude": 4.0},
        "scene": {"image_width": 640, "image_height": 480, "detections": [
            {"class_name": "bucket", "confidence": 0.9, "cx": 320, "cy": 240,
             "track_id": 3, "frame_id": 7},
        ]},
    })
    assert result.done and not result.failed
    assert result.actions == []
    assert result.detail["count"] == 1
    assert result.detail["raw_estimates"][0]["source_waypoint"] == "DROP_SCAN_1"


def test_gps_projection_uses_pinhole_image_coordinates() -> None:
    projector = GpsTargetProjector(GpsProjectionCamera(fov_x_deg=90, fov_y_deg=90))

    estimate = projector.project(
        drone_lat=0.0,
        drone_lon=0.0,
        drone_yaw_rad=0.0,
        relative_altitude_m=10.0,
        ex=0.5,
        ey=0.0,
    )

    assert estimate.east_offset_m == pytest.approx(5.0)
    assert estimate.north_offset_m == pytest.approx(0.0)


def test_gps_projection_scales_ground_distance_from_capture_point() -> None:
    projector = GpsTargetProjector(GpsProjectionCamera(
        fov_x_deg=90, fov_y_deg=90, ground_distance_scale=1 / 3,
    ))

    estimate = projector.project(
        drone_lat=0.0,
        drone_lon=0.0,
        drone_yaw_rad=0.0,
        relative_altitude_m=10.0,
        ex=0.5,
        ey=0.0,
    )

    assert estimate.east_offset_m == pytest.approx(5 / 3)
    assert estimate.north_offset_m == pytest.approx(0.0)


def test_gps_fuse_output_uses_json_arrays_for_source_metadata() -> None:
    output = _output_item(GpsLocalizedObject(
        id=1, lat=34.0, lon=108.0, east_m=1.0, north_m=2.0,
        sample_count=2, raw_count=2, class_name="bucket", confidence=0.9,
        cluster_spread_m=0.1, source_waypoints=("VIEW_1", "VIEW_2"),
        source_frames=(101, 102),
    ))

    assert output["source_waypoints"] == ["VIEW_1", "VIEW_2"]
    assert output["source_frames"] == [101, 102]
    assert output["seen_count"] == 2
    assert output["count"] == 2


def test_gps_fusion_requires_distinct_source_waypoints_when_configured() -> None:
    def estimate(source_waypoint: str) -> GpsRawEstimate:
        return GpsRawEstimate(
            lat=34.0, lon=108.0, east_offset_m=0.0, north_offset_m=0.0,
            capture_drone_lat=34.0, capture_drone_lon=108.0,
            capture_yaw_rad=0.0, capture_relative_altitude_m=4.0,
            ex=0.0, ey=0.0, class_name="bucket_1", confidence=0.9,
            source_waypoint=source_waypoint,
        )

    fusion = GpsDerivedEnuFusion(
        origin_lat=34.0,
        origin_lon=108.0,
        config=GpsFusionConfig(min_cluster_size=3, min_source_waypoints=3),
        class_names={"bucket_1"},
    )

    assert fusion.fuse([estimate("VIEW_1"), estimate("VIEW_1"), estimate("VIEW_2")]) == []
    accepted = fusion.fuse([estimate("VIEW_1"), estimate("VIEW_2"), estimate("VIEW_3")])
    assert len(accepted) == 1
    assert accepted[0].source_waypoints == ("VIEW_1", "VIEW_2", "VIEW_3")
    assert accepted[0].weight > 0.0
