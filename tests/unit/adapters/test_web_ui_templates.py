from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[3]
STATIC = ROOT / "web_ui/static"


def test_index_contains_only_formal_v2_template_buttons() -> None:
    html = (STATIC / "index.html").read_text()
    for name in ("drop_two_targets", "recon_gps", "rescue_2026_full_auto"):
        assert f'data-action-mission-template="{name}"' in html
    assert "旧版 / 调试模板" not in html


def test_frontend_modules_load_before_thin_startup() -> None:
    html = (STATIC / "index.html").read_text()
    required = ("api_client.js", "status.js", "mission.js", "field/model.js",
                "field/render.js", "field/interaction.js", "control.js", "app.js")
    positions = [html.index(name) for name in required]
    assert positions == sorted(positions)
    versions = {line.split("?v=")[1].split('"')[0] for line in html.splitlines()
                if "architecture-refactor" in line and "?v=" in line}
    assert versions == {"architecture-refactor-20260813-1"}


def test_app_js_only_starts_application() -> None:
    source = (STATIC / "app.js").read_text()
    assert "window.UavControl.init()" in source
    assert "function " not in source
    assert "fetch(" not in source


def test_field_model_render_interaction_are_separate() -> None:
    model = (STATIC / "js/field/model.js").read_text()
    render = (STATIC / "js/field/render.js").read_text()
    interaction = (STATIC / "js/field/interaction.js").read_text()
    assert "fieldXYToLatLon" in model
    assert "worldToCanvas" in render and "canvasToWorld" in render
    assert "setupFieldMapInteractions" in interaction
    assert "drop_localization" in interaction and "recon_localization" in interaction


def test_api_client_is_only_fetch_owner() -> None:
    owners = [path for path in STATIC.rglob("*.js") if "fetch(" in path.read_text()]
    assert [path.name for path in owners] == ["api_client.js"]


def test_video_panel_draws_the_final_height_drop_offset_marker_only() -> None:
    source = (STATIC / "js/video_panel.js").read_text()
    assert 'detail.speed_control_phase !== "final_altitude"' in source
    assert 'detail.release_offset_active !== true' in source
    assert 'detail.release_target_ex' in source
    assert 'detail.release_target_ey' in source
    assert 'context.strokeStyle = "#ff3030"' in source


def test_start_uses_the_editor_mission_revision() -> None:
    source = (STATIC / "js/control.js").read_text()
    start = source[source.index("async function startActionMission()"):source.index("async function stopActionMission()")]
    assert 'json("/api/action-mission/configure-and-start"' in start
    assert "JSON.stringify({steps, authorize: true, target_source: source})" in start


def test_web_template_catalog_is_not_in_server_lifecycle() -> None:
    server = (ROOT / "web_ui/server.py").read_text()
    templates = (ROOT / "web_ui/templates.py").read_text()
    assert "drop_two_targets" not in server
    assert "drop_two_targets" in templates
