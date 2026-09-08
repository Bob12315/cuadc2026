from __future__ import annotations

import pytest

from missions.common.actions.action_lab import create_action_lab_registry
from missions.common.actions.runner import ActionRunner
from missions.common.actions.select_drop_targets import SelectDropTargetsAction


def _select(objects, **params):
    action = SelectDropTargetsAction()
    action.start({"objects": objects, **params})
    return action.update({})


def test_select_drop_targets_update_before_start_fails() -> None:
    action = SelectDropTargetsAction()

    result = action.update({})

    assert result.failed is True
    assert result.reason == "action_not_started"


def test_select_drop_targets_selects_bucket_1_and_bucket_2() -> None:
    result = _select(
        [
            {"id": "b3", "class_name": "bucket_3", "local_x": 2, "local_y": 30, "seen_count": 3},
            {"id": "b1", "class_name": "bucket_1", "local_x": 0, "local_y": 30, "seen_count": 3},
            {"id": "b2", "class_name": "bucket_2", "local_x": 1, "local_y": 30, "seen_count": 3},
        ]
    )

    assert result.done is True
    assert result.reason == "drop_targets_selected"
    selected = result.detail["selected_targets"]
    assert selected[0]["id"] == "b1"
    assert selected[1]["id"] == "b2"
    assert selected[0]["rank"] == 1
    assert result.actions == []


def test_select_drop_targets_allow_fewer_accepts_one_target() -> None:
    result = _select(
        [{"id": "b1", "class_name": "bucket", "local_x": 1, "local_y": 2, "seen_count": 2}],
        target_count=2,
        allow_fewer=True,
        single_target_servo_outputs=[
            {"channel": 8, "release_pwm": 1750},
            {"channel": 9, "release_pwm": 1815},
        ],
        multi_target_first_servo_outputs=[{"channel": 8, "release_pwm": 1750}],
    )

    assert result.done is True
    assert result.failed is False
    assert result.detail["selected_count"] == 1
    assert result.detail["allow_fewer"] is True
    assert [item["channel"] for item in result.detail["first_release_servo_outputs"]] == [8, 9]


def test_select_drop_targets_two_targets_uses_first_payload_only() -> None:
    result = _select(
        [
            {"id": "b1", "class_name": "bucket", "local_x": 1, "local_y": 2, "seen_count": 2},
            {"id": "b2", "class_name": "bucket", "local_x": 3, "local_y": 4, "seen_count": 2},
        ],
        target_count=2,
        allow_fewer=True,
        single_target_servo_outputs=[{"channel": 8}, {"channel": 9}],
        multi_target_first_servo_outputs=[{"channel": 8}],
    )

    assert result.done is True
    assert [item["channel"] for item in result.detail["first_release_servo_outputs"]] == [8]


def test_select_drop_targets_uses_xy_as_local_xy_fallback() -> None:
    result = _select(
        [
            {"id": "b1", "class_name": "bucket_1", "x": 0.5, "y": 30.5, "seen_count": 3},
        ],
        target_count=1,
    )

    selected = result.detail["selected_targets"][0]
    assert selected["local_x"] == 0.5
    assert selected["local_y"] == 30.5
    assert selected["x"] == 0.5
    assert selected["y"] == 30.5


def test_select_drop_targets_filters_low_seen_count() -> None:
    result = _select(
        [{"id": "b1", "class_name": "bucket_1", "local_x": 0, "local_y": 30, "seen_count": 1}],
        target_count=1,
    )

    assert result.failed is True
    assert result.reason == "no_valid_drop_targets"
    assert result.detail["rejected_objects"][0]["reason"] == "low_seen_count"


def test_select_drop_targets_prefers_fused_objects_over_raw_like_seen_once() -> None:
    result = _select(
        [
            {
                "id": "raw_high_score",
                "class_name": "bucket_1",
                "local_x": -1.098,
                "local_y": 31.763,
                "seen_count": 1,
                "raw_count": 1,
                "weight": 1.0,
            },
            {
                "id": "fused_left",
                "class_name": "bucket_2",
                "local_x": -2.227,
                "local_y": 35.089,
                "seen_count": 6,
                "raw_count": 6,
                "weight": 6.0,
            },
            {
                "id": "fused_center",
                "class_name": "bucket_3",
                "local_x": -0.093,
                "local_y": 33.786,
                "seen_count": 11,
                "raw_count": 11,
                "weight": 11.0,
            },
            {
                "id": "raw_high_score_2",
                "class_name": "bucket_1",
                "local_x": 1.049,
                "local_y": 30.739,
                "seen_count": 1,
                "raw_count": 1,
                "weight": 1.0,
            },
        ],
        target_count=2,
        allow_fewer=True,
        min_seen_count=2,
    )

    assert result.done is True
    selected_ids = [item["id"] for item in result.detail["selected_targets"]]
    assert selected_ids == ["fused_left", "fused_center"]
    rejected_ids = {item["id"]: item["reason"] for item in result.detail["rejected_objects"]}
    assert rejected_ids["raw_high_score"] == "low_seen_count"
    assert rejected_ids["raw_high_score_2"] == "low_seen_count"


def test_select_drop_targets_filters_unknown_class() -> None:
    result = _select(
        [{"id": "u1", "class_name": "unknown", "local_x": 0, "local_y": 30, "seen_count": 3}],
        target_count=1,
    )

    assert result.failed is True
    assert result.detail["rejected_objects"][0]["reason"] == "unknown_class"


def test_select_drop_targets_filters_missing_xy() -> None:
    result = _select(
        [{"id": "b1", "class_name": "bucket_1", "seen_count": 3}],
        target_count=1,
    )

    assert result.failed is True
    assert result.detail["rejected_objects"][0]["reason"] == "missing_xy"


def test_select_drop_targets_fails_when_candidates_less_than_target_count() -> None:
    result = _select(
        [{"id": "b1", "class_name": "bucket_1", "local_x": 0, "local_y": 30, "seen_count": 3}],
        target_count=2,
    )

    assert result.failed is True
    assert result.reason == "not_enough_drop_targets"


def test_select_drop_targets_fails_when_objects_empty() -> None:
    result = _select([])

    assert result.failed is True
    assert result.reason == "no_drop_objects"


def test_select_drop_targets_allow_fewer_accepts_zero_targets() -> None:
    result = _select([], target_count=2, allow_fewer=True)

    assert result.done is True
    assert result.failed is False
    assert result.reason == "drop_targets_selected_empty"
    assert result.detail["selected_count"] == 0
    assert len(result.detail["target_slots"]) == 2
    assert all(slot["valid"] is False for slot in result.detail["target_slots"])


@pytest.mark.parametrize(
    ("objects", "expected_first_channels", "first_align", "second_align", "second_release"),
    [
        ([], [9, 10], False, False, False),
        ([{"id": "b1", "class_name": "bucket", "lat": 34.0, "lon": 108.0,
           "east_m": 1.0, "north_m": 2.0, "seen_count": 3, "raw_count": 3}],
         [9, 10], True, False, False),
        ([{"id": "b1", "class_name": "bucket", "lat": 34.0, "lon": 108.0,
           "east_m": 1.0, "north_m": 2.0, "seen_count": 3, "raw_count": 3},
          {"id": "b2", "class_name": "bucket", "lat": 34.1, "lon": 108.1,
           "east_m": 3.0, "north_m": 4.0, "seen_count": 3, "raw_count": 3}],
         [9], True, True, True),
    ],
)
def test_competition_release_plan_handles_zero_one_or_two_targets(
    objects, expected_first_channels, first_align, second_align, second_release
) -> None:
    outputs = [
        {"channel": 9, "release_pwm": 1800, "hold_pwm": 1600},
        {"channel": 10, "release_pwm": 1800, "hold_pwm": 1600},
    ]
    result = _select(
        objects,
        coordinate_mode="gps_enu",
        target_count=2,
        allow_fewer=True,
        min_seen_count=3,
        min_raw_count=3,
        fallback_target={"x": 0.0, "y": 32.5, "target_id": "drop_zone_center"},
        single_target_servo_outputs=outputs,
        multi_target_first_servo_outputs=outputs[:1],
    )

    assert result.done and not result.failed
    assert [item["channel"] for item in result.output["first_release_servo_outputs"]] == expected_first_channels
    assert result.output["first_alignment_enabled"] is first_align
    assert result.output["second_alignment_enabled"] is second_align
    assert result.output["second_release_enabled"] is second_release
    assert len(result.output["target_slots"]) == 2
    if not objects:
        fallback = result.output["target_slots"][0]
        assert fallback["valid"] is True
        assert fallback["x"] == 0.0
        assert fallback["y"] == 32.5
        assert fallback["status"] == "fallback_center"


def test_select_drop_targets_allow_fewer_output_passes_action_contract() -> None:
    runner = ActionRunner(create_action_lab_registry())
    start = runner.start(
        "select_drop_targets",
        {
            "objects": [
                {
                    "id": 0,
                    "class_name": "bucket",
                    "lat": -35.362971,
                    "lon": 149.165251,
                    "east_m": 1.21,
                    "north_m": 32.36,
                    "sample_count": 2,
                    "raw_count": 2,
                }
            ],
            "coordinate_mode": "gps_enu",
            "target_count": 2,
            "allow_fewer": True,
            "min_seen_count": 2,
            "min_raw_count": 2,
        },
    )

    assert start.failed is False
    result = runner.update({})
    assert result.done is True
    assert result.failed is False
    assert result.reason == "drop_targets_selected"
    assert result.output["target_slots"][0]["valid"] is True
    assert result.output["target_slots"][1]["valid"] is False
    assert result.output["target_slots"][1]["lat"] is None


def test_select_drop_targets_prefers_effective_sample_count_to_raw_count() -> None:
    result = _select(
        [
            {"id": "outlier_heavy", "class_name": "bucket", "local_x": 0.0, "local_y": 30.0,
             "sample_count": 2, "raw_count": 20},
            {"id": "well_supported", "class_name": "bucket", "local_x": 2.0, "local_y": 30.0,
             "sample_count": 3, "raw_count": 3},
        ],
        target_count=1,
        min_seen_count=2,
    )

    assert result.done is True
    assert result.detail["selected_targets"][0]["id"] == "well_supported"


def test_select_drop_targets_deduplicates_nearby_candidates() -> None:
    result = _select(
        [
            {"id": "b1", "class_name": "bucket_1", "local_x": 0.0, "local_y": 30.0, "seen_count": 3},
            {"id": "b2", "class_name": "bucket_2", "local_x": 0.1, "local_y": 30.1, "seen_count": 3},
            {"id": "b3", "class_name": "bucket_3", "local_x": 1.0, "local_y": 30.0, "seen_count": 3},
        ],
        target_count=2,
        deduplicate_radius_m=0.35,
    )

    selected_ids = [item["id"] for item in result.detail["selected_targets"]]
    rejected_reasons = [item["reason"] for item in result.detail["rejected_objects"]]
    assert selected_ids == ["b1", "b3"]
    assert "duplicate_near_selected" in rejected_reasons


def test_select_drop_targets_target_count_one_selects_highest_score() -> None:
    result = _select(
        [
            {"id": "b2", "class_name": "bucket_2", "local_x": 1, "local_y": 30, "seen_count": 3},
            {"id": "b1", "class_name": "bucket_1", "local_x": 0, "local_y": 30, "seen_count": 3},
        ],
        target_count=1,
    )

    assert result.done is True
    assert result.detail["selected_targets"] == [result.detail["selected_targets"][0]]
    assert result.detail["selected_targets"][0]["id"] == "b1"
    assert result.detail["selected_count"] == 1


def test_select_drop_targets_prefers_zone_center_when_other_scores_tie() -> None:
    result = _select(
        [
            {"id": "far", "class_name": "bucket", "local_x": 5.0, "local_y": 30.0, "seen_count": 3},
            {"id": "near", "class_name": "bucket", "local_x": 0.1, "local_y": 30.0, "seen_count": 3},
        ],
        target_count=1,
        zone_center={"x": 0.0, "y": 30.0},
    )

    assert result.detail["selected_targets"][0]["id"] == "near"


@pytest.mark.parametrize(
    "params",
    [
        {"objects": "bad"},
        {"objects": [], "target_count": 0},
        {"objects": [], "score_table": []},
        {"objects": [], "deduplicate_radius_m": -0.1},
    ],
)
def test_select_drop_targets_rejects_invalid_params(params) -> None:
    action = SelectDropTargetsAction()

    with pytest.raises(ValueError):
        action.start(params)


def test_select_drop_targets_done_update_returns_cached_result() -> None:
    action = SelectDropTargetsAction()
    action.start(
        {
            "objects": [
                {"id": "b1", "class_name": "bucket_1", "local_x": 0, "local_y": 30, "seen_count": 3},
            ],
            "target_count": 1,
        }
    )

    first = action.update({})
    second = action.update({})

    assert first.done is True
    assert second.done is True
    assert second.reason == first.reason
    assert second.detail == first.detail


# ── zone_center_mode tests ──────────────────────────────────────────────


def test_select_drop_targets_zone_center_mode_local_default():
    """zone_center_mode 默认 local，行为与旧版一致。"""
    result = _select(
        [
            {"id": "far", "class_name": "bucket", "local_x": 5.0, "local_y": 30.0, "seen_count": 3},
            {"id": "near", "class_name": "bucket", "local_x": 0.1, "local_y": 30.0, "seen_count": 3},
        ],
        target_count=1,
        zone_center={"x": 0.0, "y": 30.0},
    )
    assert result.detail["selected_targets"][0]["id"] == "near"


def test_select_drop_targets_zone_center_mode_field_heading_zero():
    """Schema-v3 FIELD center rotates into GPS-derived ENU for ranking."""
    ctx = {
        "field_gps_transform_confirmed": True,
        "field_heading_yaw_rad": 0.0,
    }
    near = {"id": "near", "class_name": "bucket", "east_m": 0.0, "north_m": 32.5,
            "lat": 34.0, "lon": 108.0, "seen_count": 3}
    far = {"id": "far", "class_name": "bucket", "east_m": 32.5, "north_m": 0.0,
           "lat": 34.0, "lon": 108.001, "seen_count": 3}
    action = SelectDropTargetsAction()
    action.start({"objects": [far, near], "target_count": 1, "allow_fewer": True,
                  "zone_center": {"x": 0.0, "y": 32.5},
                  "zone_center_mode": "field", "coordinate_mode": "gps_enu"})
    result = action.update(ctx)
    assert result.done
    assert result.detail["selected_targets"][0]["id"] == "near"


def test_select_drop_targets_zone_center_mode_field_missing_context():
    """zone_center_mode=field 但 context 缺失 → failed。"""
    action = SelectDropTargetsAction()
    action.start({"objects": [{"id": "b1", "class_name": "bucket", "local_x": 1, "local_y": 2,
                               "seen_count": 3}],
                  "target_count": 1, "allow_fewer": True,
                  "zone_center": {"x": 0.0, "y": 32.5},
                  "zone_center_mode": "field", "coordinate_mode": "gps_enu"})
    result = action.update({})
    assert result.failed
    assert result.reason == "missing_field_reference_for_zone_center"


def test_gps_enu_targets_receive_stable_nonempty_target_ids() -> None:
    objects = [
        {"id": None, "target_id": None, "class_name": "bucket_1", "lat": 34.0, "lon": 108.0,
         "east_m": 1.0, "north_m": 2.0, "seen_count": 3, "raw_count": 3},
        {"id": "null", "target_id": "None", "class_name": "bucket_2", "lat": 34.001, "lon": 108.001,
         "east_m": 3.0, "north_m": 4.0, "seen_count": 3, "raw_count": 3},
    ]
    action = SelectDropTargetsAction()
    action.start({"objects": objects, "coordinate_mode": "gps_enu", "target_count": 2})

    first = action.update({})
    second = action.update({})
    target_ids = [slot["target_id"] for slot in first.detail["target_slots"]]

    assert target_ids == ["gps_target_0", "gps_target_1"]
    assert len(set(target_ids)) == 2
    assert all(value.lower() not in {"", "none", "null"} for value in target_ids)
    assert [slot["target_id"] for slot in second.detail["target_slots"]] == target_ids
    assert [(slot["east_m"], slot["north_m"]) for slot in first.detail["target_slots"]] == [(1.0, 2.0), (3.0, 4.0)]


def test_gps_enu_duplicate_source_ids_are_made_distinct() -> None:
    result = _select(
        [
            {"id": "same", "target_id": "same", "class_name": "bucket_1", "lat": 34.0, "lon": 108.0,
             "east_m": 0.0, "north_m": 0.0, "seen_count": 3},
            {"id": "same", "target_id": "same", "class_name": "bucket_2", "lat": 34.001, "lon": 108.001,
             "east_m": 2.0, "north_m": 2.0, "seen_count": 3},
        ],
        coordinate_mode="gps_enu",
        target_count=2,
    )
    target_ids = [slot["target_id"] for slot in result.detail["target_slots"]]
    assert target_ids == ["same", "same_1"]
    assert len(set(target_ids)) == 2
