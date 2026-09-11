const AppState = window.UavAppState;

let state = {};
let history = AppState.history;

let historyIndex = -1;
// currentConfigPath/currentOriginal moved to config_panel.js (WU-7B v2)
let currentActionMission = null;
let currentActionMissionSteps = [];
let lastActionMissionStatus = null;
let lastActionMissionResult = null;
let lastActionMissionSummaryHtml = "";
let statusUpdatesStop = null;
// latestCameraRecording moved to video_panel.js (WU-6 v2)
const ACTION_SAFETY_HINTS = {
  goto_waypoint: "默认输入 FIELD 坐标（x=右，y=前），转换为 LOCAL_NED 后下发；需要 SEND=ON 才实发。",
  target_lock: "YOLO 锁定命令，不需要 SEND=ON，但需要 Dispatch。",
  align_descend: "BODY_NED 速度控制，需要 SEND=ON 才实发。",
  payload_release: "舵机 PWM 输出，需要 SEND=ON 才实发；确认 SERVO 输出通道和 PWM。",
  select_drop_targets: "从 drop_scan.localized_objects 选择投放目标，不发送飞控命令。",
  gps_capture_view: "当前位置采集一帧并投影到 GPS，不发送飞控命令。",
  gps_fuse_views: "融合 Mission 已采集的 GPS 视图，不发送飞控命令。",
};
const ACTION_ZH_LABELS = {
  takeoff: "起飞",
  land: "降落",
  goto_waypoint: "飞到航点",
  target_lock: "目标锁定",
  align_descend: "对准下降",
  payload_release: "载荷投放",
  select_drop_targets: "选择投放目标",
  gps_capture_view: "GPS 单视图采集",
  gps_fuse_views: "GPS 多视图融合",
};
const DEFAULT_ACTION_MISSION_STEPS = [
  {
    name: "goto_waypoint",
    params: {
      x: 0.0,
      y: 0.0,
      altitude_m: 1.5,
      waypoint_mode: "field",
      yaw_mode: "field_heading",
    },
  },
  {
    name: "payload_release",
    params: {
      servo_outputs: [
        {
          servo_output: 8,
          release_pwm: 1200,
          hold_pwm: 1700,
        },
      ],
      payload_id: "p1",
      target_id: "t1",
      release_wait_updates: 1,
    },
  },
];
const actionMissionPresets = {};
const POSITION_DEMO_ENABLED = new URLSearchParams(window.location.search).get("demo-position") === "1";
const POSITION_DEMO = Object.freeze({
  field_x: 12.345,
  field_y: 28.910,
  field_z: 6.250,
  yaw_deg: 127.64,
  field_yaw_deg: 42.64,
  lat: 34.341_456_789,
  lon: 108.939_123_456,
});

const json = window.UavApi.request;

const {
  stamp,
  escapeHtml,
  num,
  boolText,
} = window.UavFormat;

const {
  $,
  setOptionalText,
  cards,
  infoRows,
  renderSummaryRows,
} = window.UavDom;

// Field Map thin aliases (WU-5) — functions now in field_map.js
const pointX = function () { return window.UavFieldMap.pointX.apply(window.UavFieldMap, arguments); };
const pointY = function () { return window.UavFieldMap.pointY.apply(window.UavFieldMap, arguments); };
const renderFieldMap = function (next) { return window.UavFieldMap.renderFieldMap(next); };
const setupFieldMapInteractions = function () { window.UavFieldMap.setupFieldMapInteractions(); };

async function yoloTargetAction(action, trackId) {
  const query = trackId === undefined ? "" : `?track_id=${encodeURIComponent(trackId)}`;
  const result = await json(`/api/yolo/target/${encodeURIComponent(action)}${query}`, {
    method: "POST", body: "{}",
  });
  if (!result.ok) throw new Error(result.message || "YOLO target command failed");
  return result;
}
async function clearLocalization() {
  const result = await json("/api/localization/clear", {method: "POST", body: "{}"});
  $("completionHint").textContent = result.message || "localized object coordinates cleared";
  state.localization = {};
  renderFieldMap(state);
  return result;
}
// Field Map configure (WU-5 v3) — must be before init() and ActionLab
if (window.UavFieldMap && window.UavFieldMap.configure) {
  window.UavFieldMap.configure({
    dom: window.UavDom,
    format: window.UavFormat,
    getState: function () { return state; },
    getLatestActionLab: function () { return window.UavActionLab.getLatestActionLab(); },
    onClearLocalization: clearLocalization,
    setCompletionHint: function (text) { $("completionHint").textContent = text; },
  });
}
const setBadge = window.UavStatus.setBadge;
function actionDisplayName(name, fallback = "") {
  const base = String(fallback || name || "--");
  const zh = ACTION_ZH_LABELS[name] || "";
  if (!zh || base.includes(zh)) return base;
  return `${base} ${zh}`;
}
function actionNameWithZh(name) {
  if (!name) return "--";
  return actionDisplayName(name, name);
}
function setButtonActive(selector, predicate) {
  document.querySelectorAll(selector).forEach(button => {
    button.classList.toggle("active-choice", Boolean(predicate(button)));
  });
}
function updateControlHighlights(next, drone, controls) {
  const sendEnabled = Boolean(controls.send_commands);
  $("sendToggle").classList.toggle("active-choice", sendEnabled);
  $("sendToggleState").textContent = sendEnabled ? "ON" : "OFF";
  setButtonActive("[data-mode]", button => (drone.mode || "").toUpperCase() === button.dataset.mode);
  setButtonActive("[data-arm-state]", button =>
    (button.dataset.armState === "armed" && drone.armed)
    || (button.dataset.armState === "disarmed" && !drone.armed));
  setButtonActive("[data-source]", button => button.dataset.source === next.active_source);
  ["gimbal", "body", "approach"].forEach(name => {
    const enabled = Boolean(controls[name]);
    const row = document.querySelector(`[data-controller-row="${name}"]`);
    if (!row) return;
    row.classList.toggle("enabled", enabled);
    row.querySelectorAll("button").forEach(button => {
      const command = button.dataset.command || "";
      button.classList.toggle("active-choice", command.endsWith(enabled ? " on" : " off"));
    });
  });
  const allEnabled = Boolean(controls.gimbal && controls.body && controls.approach);
  const allDisabled = Boolean(!controls.gimbal && !controls.body && !controls.approach);
  const allRow = document.querySelector('[data-controller-row="all"]');
  if (allRow) {
    allRow.classList.toggle("enabled", allEnabled);
    allRow.querySelectorAll("button").forEach(button => {
      const command = button.dataset.command || "";
      button.classList.toggle(
        "active-choice",
        (allEnabled && command.endsWith(" on")) || (allDisabled && command.endsWith(" off"))
      );
    });
  }
}
// Field Heading / Field Reference moved to field_reference.js
//   confirmFieldHeading calls "/api/field-heading/confirm"
//   renderFieldHeading uses delta_current_to_field_deg
//   Field Reference uses /api/field-reference/*
const {
  gpsText,
  fetchFieldReferenceStatus,
  renderFieldReference,
  frPost,
} = window.UavFieldRef;

const {
  fetchProfileList,
  fetchFieldReferenceStatus: fpFetchFieldReferenceStatus,
} = window.UavFieldProfiles || {};

// confirmFieldHeading removed (centerline-only)

function dispatchFromActionLab(actionLab) {
  const payload = actionLab || window.UavActionLab.getLatestActionLab() || {};
  const status = payload?.status || payload || {};
  const last = status?.last_result || {};
  const detail = last.detail || {};
  return payload.dispatch || detail.dispatch || detail.last_dispatch || payload.last_dispatch || last.dispatch || {};
}
function countDispatchItems(value) {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") return Object.keys(value).length;
  return value ? 1 : 0;
}
const actionMissionDetail = window.UavMission.detail;
const actionMissionBlackboard = window.UavMission.blackboard;
function inferActionMissionStepStatus(actionMission, index, stepCount) {
  const payload = actionMission || {};
  const current = Number.isFinite(Number(payload.current_index)) ? Number(payload.current_index) : 0;
  const detail = actionMissionDetail(payload);
  const timing = detail.step_timings?.[index] || detail.step_timings?.[String(index)] || {};
  const skippedSet = new Set((Array.isArray(detail.skipped_steps) ? detail.skipped_steps : []).map(s => s.index));
  if (skippedSet.has(index)) return "skipped";
  if (["done", "failed", "skipped", "continued", "stopped"].includes(timing.status)) return timing.status;
  if (timing.status === "running") return "running";
  if (timing.status === "pending" && detail.step_timings) return "pending";
  if (payload.done) return index <= current ? "done" : "pending";
  if (payload.failed) {
    if (index < current) return "done";
    if (index === current) return "failed";
    return "pending";
  }
  if (payload.running && index === current && current < stepCount) return "running";
  if (index < current) return "done";
  return "pending";
}
const failurePolicyLabel = window.UavMission.failurePolicyLabel;
const actionMissionStatusLabel = window.UavMission.statusLabel;
const actionMissionDurationLabel = window.UavMission.durationLabel;
function renderActionMissionTimeline(actionMission, configuredSteps) {
  const element = $("actionMissionTimeline");
  if (!element) return [];
  const steps = Array.isArray(configuredSteps) ? configuredSteps : [];
  const detail = actionMissionDetail(actionMission);
  const attempts = detail.step_attempts || {};
  const timings = detail.step_timings || {};
  const skippedSet = new Set((Array.isArray(detail.skipped_steps) ? detail.skipped_steps : []).map(s => s.index));
  const current = Number.isFinite(Number(actionMission?.current_index)) ? Number(actionMission.current_index) : -1;
  const statuses = steps.map((step, index) => inferActionMissionStepStatus(actionMission || {}, index, steps.length));
  element.innerHTML = steps.map((step, index) => {
    const status = statuses[index];
    const timing = timings[index] || timings[String(index)] || {};
    const reason = timing.reason || (index === current ? (actionMission?.reason || "-")
      : skippedSet.has(index) ? "manual skip"
      : "-");
    return `<tr class="${index === current ? "current-step" : ""}" data-step-status="${status}">
      <td>${index}</td>
      <td><span class="step-status ${status}">${escapeHtml(actionMissionStatusLabel(status))}</span></td>
      <td>${escapeHtml(step.label || "-")}</td>
      <td>${escapeHtml(actionNameWithZh(step.name))}</td>
      <td>${escapeHtml(step.save_as || "-")}</td>
      <td>${escapeHtml(failurePolicyLabel(step.on_failed))}</td>
      <td>${escapeHtml(attempts[index] ?? attempts[String(index)] ?? "-")}</td>
      <td>${escapeHtml(actionMissionDurationLabel(timing.duration_s))}</td>
      <td>${escapeHtml(reason)}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="9" class="hint">加载模板或粘贴任务 JSON 后预览步骤。</td></tr>`;
  return statuses;
}
function renderBlackboardKeys(actionMission) {
  const element = $("actionMissionBlackboardKeys");
  if (!element) return;
  const detail = actionMissionDetail(actionMission);
  const blackboard = actionMissionBlackboard(actionMission);
  const keys = Array.isArray(detail.blackboard_keys) ? detail.blackboard_keys : Object.keys(blackboard);
  element.innerHTML = keys.length
    ? keys.map(key => `<span class="blackboard-key">${escapeHtml(key)}</span>`).join("")
    : `<div class="hint">暂无黑板键。</div>`;
}
function getPathValue(source, path) {
  let current = source;
  for (const part of path) {
    if (current === null || current === undefined) return undefined;
    current = current[part];
  }
  return current;
}
function summarizeDropScan(blackboard) {
  const dropScan = blackboard.drop_scan || {};
  const objects = getPathValue(dropScan, ["localization", "objects"])
    || dropScan.localized_objects
    || dropScan.objects;
  if (!Array.isArray(objects)) return "";
  return `<div class="mission-result-group"><strong>投放区扫描</strong><div>已定位目标数：${objects.length}</div></div>`;
}
function summarizeDropTargets(blackboard) {
  const selected = getPathValue(blackboard, ["drop_targets", "selected_targets"]);
  if (!Array.isArray(selected)) return "";
  const rows = selected.slice(0, 6).map((target, index) => {
    const id = target.id ?? target.track_id ?? index + 1;
    const x = target.local_x ?? target.x ?? "--";
    const y = target.local_y ?? target.y ?? "--";
    return `<div>T${index + 1}: id=${escapeHtml(id)} x=${escapeHtml(x)} y=${escapeHtml(y)}</div>`;
  }).join("");
  return `<div class="mission-result-group"><strong>已选择投放目标</strong><div>投放目标数：${selected.length}</div>${rows}</div>`;
}
function setResultSection(container, name, html) {
  let section = container.querySelector(`[data-result-section="${name}"]`);
  if (!section) { section = document.createElement("div"); section.dataset.resultSection = name; container.appendChild(section); }
  if (section.dataset.html !== html) { section.dataset.html = html; section.innerHTML = html; }
  return section;
}
function renderActionMissionSummary(actionMission) {
  const element = $("actionMissionResults");
  if (!element) return;
  const blackboard = actionMissionBlackboard(actionMission);
  const dropScan = summarizeDropScan(blackboard), dropTargets = summarizeDropTargets(blackboard);
  setResultSection(element, "drop-scan", dropScan);
  setResultSection(element, "drop-targets", dropTargets);
  const hasResults = Boolean(dropScan || dropTargets);
  if (!hasResults) setResultSection(element, "empty", `<div class="hint">暂无任务结果详情。</div>`);
  else { const empty = element.querySelector('[data-result-section="empty"]'); if (empty) empty.remove(); }
}
function updateActionMissionAutoTickButton() {
  const button = $("actionMissionAutoTick");
  if (!button) return;
  const running = Boolean(lastActionMissionStatus?.running);
  button.textContent = running ? "后台自动推进中" : "后台自动推进";
  button.disabled = true;
}
function stopActionMissionAutoTick() {
  updateActionMissionAutoTickButton();
}
function renderActionMissionStatus(actionMission) {
  const payload = actionMission || {};
  const detail = actionMissionDetail(payload);
  const sendEnabled = Boolean(state?.controllers?.send_commands);
  lastActionMissionStatus = payload;
  const actionResult = detail.action_result || detail.previous_action_result || detail.failed_action_result || null;
  if (actionResult) lastActionMissionResult = actionResult;
  setOptionalText("actionMissionSystemSend", sendEnabled ? "ON" : "OFF");
  setOptionalText("actionMissionEnabled", boolText(Boolean(payload.enabled), "是", "否"));
  setOptionalText("actionMissionRunning", boolText(Boolean(payload.running), "是", "否"));
  setOptionalText("actionMissionDone", boolText(Boolean(payload.done), "是", "否"));
  setOptionalText("actionMissionFailed", boolText(Boolean(payload.failed), "是", "否"));
  setOptionalText("actionMissionIndex", payload.current_index ?? "--");
  setOptionalText("actionMissionCurrent", actionNameWithZh(payload.current_action));
  setOptionalText("actionMissionReason", payload.reason || "--");
  setOptionalText("actionMissionDuration", actionMissionDurationLabel(detail.mission_duration_s));
  const warning = $("actionMissionSendWarning");
  if (warning) {
    warning.textContent = sendEnabled
      ? "警告：System SEND=ON，推进一次可能向飞控或仿真器下发命令。"
      : "干跑模式：推进不会下发飞控命令。";
    warning.classList.toggle("send-on", sendEnabled);
  }
  const skipButton = $("actionMissionSkipCurrent");
  if (skipButton) {
    const canSkip = Boolean(payload.enabled && payload.running && !payload.done && !payload.failed);
    skipButton.disabled = !canSkip;
  }
  const detailElement = $("actionMissionDetail");
  if (detailElement) detailElement.textContent = JSON.stringify(detail, null, 2);
  renderBlackboardKeys(payload);
  renderActionMissionSummary(payload);
  renderActionMissionTimeline(payload, currentActionMissionSteps);
  if (payload.done || payload.failed) stopActionMissionAutoTick();
  else updateActionMissionAutoTickButton();
}
function renderDashboardSummaries(next) {
  const actionLab = next.action_lab || window.UavActionLab.getLatestActionLab() || {};
  const actionStatus = actionLab?.status || actionLab || {};
  const actionLast = actionStatus?.last_result || {};
  const dispatch = dispatchFromActionLab(actionLab);
  const mission = next.action_mission || {};
  renderSummaryRows("dashboardActionSummary", [
    ["Action", actionNameWithZh(actionStatus.action_name), actionStatus.running ? "active" : ""],
    ["State", actionStatus.state || "--"],
    ["Reason", actionLast.reason || "--"],
    ["Done / Failed", `${Boolean(actionLast.done)} / ${Boolean(actionLast.failed)}`, actionLast.failed ? "danger" : ""],
  ]);
  renderSummaryRows("dashboardMissionSummary", [
    ["Enabled", String(Boolean(mission.enabled))],
    ["Running", String(Boolean(mission.running)), mission.running ? "active" : ""],
    ["Current", actionNameWithZh(mission.current_action)],
    ["Reason", mission.reason || "--"],
  ]);
  renderSummaryRows("dashboardDispatchSummary", [
    ["sent", JSON.stringify(dispatch.sent ?? "--"), dispatch.sent ? "ok" : ""],
    ["skipped", JSON.stringify(dispatch.skipped ?? "--")],
    ["errors", JSON.stringify(dispatch.errors ?? "--"), dispatch.errors ? "danger" : ""],
    ["note", actionLab.note || dispatch.note || "--"],
  ]);
}
function fullPositionNumber(value, unit = "") {
  return Number.isFinite(Number(value)) ? `${String(value)}${unit}` : "--";
}
function positionDisplay(next, drone) {
  if (POSITION_DEMO_ENABLED) return POSITION_DEMO;
  const field = next.field_position || {};
  const localZ = Number(field.local_z);
  const headingYaw = Number(next.field_heading?.current_yaw_deg);
  const fieldHeadingYaw = Number(next.field_heading?.field_heading_yaw_deg);
  const reportedFieldYaw = Number(next.field_heading?.delta_current_to_field_deg);
  const fallbackYaw = Number(drone.yaw) * 180 / Math.PI;
  const northYaw = Number.isFinite(headingYaw) ? headingYaw : fallbackYaw;
  const calculatedFieldYaw = Number.isFinite(northYaw) && Number.isFinite(fieldHeadingYaw)
    ? ((northYaw - fieldHeadingYaw + 540) % 360) - 180
    : NaN;
  return {
    field_x: field.x,
    field_y: field.y,
    // FIELD +Z is up; LOCAL_NED z is down.
    field_z: Number.isFinite(localZ) ? -localZ : drone.relative_altitude,
    yaw_deg: northYaw,
    // FIELD yaw is relative to FIELD +Y (the runway/field forward direction).
    field_yaw_deg: Number.isFinite(reportedFieldYaw) ? reportedFieldYaw : calculatedFieldYaw,
    lat: drone.lat,
    lon: drone.lon,
    source: field.source || "--",
  };
}
function renderStatus(next) {
  state = next;
  const link = next.link || {};
  const drone = next.drone || {};
  const gimbal = next.gimbal || {};
  const controls = next.controllers || {};
  setBadge($("sourceBadge"), `SOURCE ${String(next.active_source || "--").toUpperCase()}`, next.active_source === "real" ? "warning" : "");
  setBadge($("linkBadge"), `LINK ${link.connected ? "OK" : "DOWN"}`, link.connected ? "ok" : "danger");
  setBadge($("sendBadge"), `SEND ${controls.send_commands ? "ON" : "OFF"}`, controls.send_commands ? "danger" : "ok");
  setBadge($("armBadge"), `ARM ${drone.armed ? "ON" : "OFF"}`, drone.armed ? "warning" : "");
  setBadge($("modeBadge"), `MODE ${drone.mode || "--"}`, drone.mode === "GUIDED" ? "warning" : "");
  setBadge($("batteryBadge"), `BAT ${drone.battery_valid ? `${drone.battery_remaining}%` : "--"}`, "");
  setBadge($("altitudeBadge"), `ALT ${num(drone.relative_altitude, 1, "m")}`, "");
  const actionStatus = (next.action_lab?.status || next.action_lab || {});
  setBadge($("actionBadge"), `ACTION ${actionStatus.running ? actionNameWithZh(actionStatus.action_name || "RUN") : "--"}`, actionStatus.running ? "active" : "");
  setBadge($("missionBadge"), `MISSION ${next.action_mission?.running ? actionNameWithZh(next.action_mission.current_action || "RUN") : "--"}`, next.action_mission?.running ? "active" : "");
  setOptionalText("missionName", next.mission || "--");
  setOptionalText("missionStage", next.stage || "--");
  setOptionalText("stageController", next.stage_controller || "--");
  setOptionalText("holdReason", next.hold_reason || "none");
  updateControlHighlights(next, drone, controls);
  const position = positionDisplay(next, drone);
  const demoLabel = $("positionDemoLabel");
  if (demoLabel) demoLabel.hidden = !POSITION_DEMO_ENABLED;
  infoRows($("aircraftInfo"), [
    ["FIELD X（右，m）", fullPositionNumber(position.field_x)],
    ["FIELD Y（前，m）", fullPositionNumber(position.field_y)],
    ["FIELD Z（上，m）", fullPositionNumber(position.field_z)],
    ["偏航 yaw（北基准）", fullPositionNumber(position.yaw_deg, "°")],
    ["偏航 yaw（FIELD +Y）", fullPositionNumber(position.field_yaw_deg, "°")],
    ["GPS 纬度", fullPositionNumber(position.lat)],
    ["GPS 经度", fullPositionNumber(position.lon)],
    ["位置来源", POSITION_DEMO_ENABLED ? "DEMO 假数据" : position.source],
    ["GPS 质量", `${drone.gps_fix_type ?? "--"} fix / ${drone.satellites_visible ?? "--"} sats`],
  ]);
  cards($("statusCards"), {
    "链路": `${link.status_text || "--"} / ${link.transport || "--"}`,
    "心跳": link.connected ? `${num(drone.hb_age_sec, 2, " s")} ago` : "--",
    "接收": link.connected ? `${num(drone.rx_age_sec, 2, " s")} ago` : "--",
    "目标系统": `${link.target_system ?? "--"}:${link.target_component ?? "--"}`,
    "飞控模式": drone.mode || "--",
    "解锁状态": drone.armed ? "ARMED" : "DISARMED",
    "姿态 R/P/Y": `${num(drone.roll, 3)} / ${num(drone.pitch, 3)} / ${num(drone.yaw, 3)}`,
    "高度": `${num(drone.relative_altitude, 2, " m")} / ${num(drone.altitude, 2, " m")}`,
    "速度 NED": `${num(drone.vx, 2)} / ${num(drone.vy, 2)} / ${num(drone.vz, 2)}`,
    "本地位置": drone.local_position_valid ? `${num(drone.local_x, 2)} / ${num(drone.local_y, 2)} / ${num(drone.local_z, 2)}` : "--",
    "GPS": `${drone.gps_fix_type ?? "--"} / ${drone.satellites_visible ?? "--"} sats`,
    "经纬度": drone.global_position_valid ? `${num(drone.lat, 7)}, ${num(drone.lon, 7)}` : "--",
    "电池": drone.battery_valid ? `${Number(drone.battery_voltage).toFixed(1)} V / ${drone.battery_remaining}%` : "--",
    "云台 Y/P/R": gimbal.gimbal_valid ? `${num(gimbal.yaw, 3)} / ${num(gimbal.pitch, 3)} / ${num(gimbal.roll, 3)}` : "--",
    "最新消息": drone.last_message_type || "--",
    "Mission": next.mission || "--", "Stage": next.stage || "--",
    "Hold": next.hold_reason || "none"
  });
  const cmd = next.command || {};
  cards($("commandCards"), {
    "VX": Number(cmd.vx_cmd || 0).toFixed(3), "VY": Number(cmd.vy_cmd || 0).toFixed(3),
    "VZ": Number(cmd.vz_cmd || 0).toFixed(3), "Yaw": Number(cmd.yaw_rate_cmd || 0).toFixed(3),
    "Gimbal Y": Number(cmd.gimbal_yaw_rate_cmd || 0).toFixed(3),
    "Gimbal P": Number(cmd.gimbal_pitch_rate_cmd || 0).toFixed(3),
    "Active": String(Boolean(cmd.active)), "SEND": controls.send_commands ? "ON" : "OFF"
  });
  $("events").innerHTML = (next.events || []).map(item =>
    `<div class="log-line">${stamp(item.timestamp)} ${escapeHtml(item.level)} &nbsp; ${escapeHtml(item.message)}</div>`).join("");
  renderFieldMap(next);
  renderActionLabStatus(next.action_lab || null);
  renderActionMissionStatus(next.action_mission || null);
  renderDashboardSummaries(next);
}
// Video panel configure (WU-6 v2)
if (window.UavVideoPanel && window.UavVideoPanel.configure) {
  window.UavVideoPanel.configure({
    api: window.UavApi,
    dom: window.UavDom,
    format: window.UavFormat,
    getState: function () { return state; },
    targetAction: yoloTargetAction,
    setCompletionHint: function (text) { $("completionHint").textContent = text; },
  });
}
var refreshCameraRecordingStatus = function () { return window.UavVideoPanel.refreshCameraRecordingStatus(); };
var toggleCameraRecording = function () { return window.UavVideoPanel.toggleCameraRecording(); };
var renderCameraRecordingStatus = function (payload) { window.UavVideoPanel.renderCameraRecordingStatus(payload); };

// Action Lab moved to action_lab.js (WU-4 v4) — sole state owner
if (window.UavActionLab && window.UavActionLab.configure) {
  window.UavActionLab.configure({
    api: window.UavApi,
    dom: window.UavDom,
    format: window.UavFormat,
    appState: window.UavAppState,
    safetyHints: ACTION_SAFETY_HINTS,
    zhLabels: ACTION_ZH_LABELS,
    getState: function () { return state; },
    renderFieldMap: renderFieldMap,
    setCompletionHint: function (text) { $("completionHint").textContent = text; },
  });
}
var loadActionLab = function () { return window.UavActionLab.loadActionLab(); };
var cacheSelectedActionParams = function () { window.UavActionLab.cacheSelectedActionParams(); };
var selectAction = function (name) { return window.UavActionLab.selectAction(name); };
var selectServoPreset = function (name) { return window.UavActionLab.selectServoPreset(name); };
var refreshActionStatus = function () { return window.UavActionLab.refreshActionStatus(); };
var renderActionLabStatus = function (actionLab) { window.UavActionLab.renderActionLabStatus(actionLab); };
var nodeInside = function (element, node) { return window.UavActionLab.nodeInside(element, node); };
var actionStatusJsonHasSelection = function () { return window.UavActionLab.actionStatusJsonHasSelection(); };
var updateActionStatusJson = function (text) { window.UavActionLab.updateActionStatusJson(text); };
var parseActionParams = function () { return window.UavActionLab.parseActionParams(); };
var dispatchFromActionLab = function (actionLab) { return window.UavActionLab.dispatchFromActionLab(actionLab); };
var countDispatchItems = function (value) { return window.UavActionLab.countDispatchItems(value); };
function parseActionMissionInput(text) {
  const parsed = text.trim() ? JSON.parse(text) : [];
  if (Array.isArray(parsed)) return {name: "", steps: parsed};
  if (parsed && typeof parsed === "object" && Array.isArray(parsed.steps)) return parsed;
  throw new Error("Action Mission JSON 必须是步骤数组，或包含 steps 的对象");
}
function normalizeActionMissionSteps(input) {
  const steps = Array.isArray(input) ? input : input?.steps;
  if (!Array.isArray(steps)) throw new Error("steps 必须是 JSON 数组");
  if (!steps.length) throw new Error("steps 不能为空");
  return steps.map((step, index) => {
    if (!step || typeof step !== "object" || typeof step.name !== "string" || !step.name.trim()) {
      throw new Error(`step ${index + 1} 必须包含 name`);
    }
    if (step.params !== undefined && (step.params === null || Array.isArray(step.params) || typeof step.params !== "object")) {
      throw new Error(`step ${index + 1} params 必须是 object`);
    }
    if (step.save_as !== undefined && step.save_as !== null && typeof step.save_as !== "string") {
      throw new Error(`step ${index + 1} save_as 必须是 string`);
    }
    if (step.label !== undefined && step.label !== null && typeof step.label !== "string") {
      throw new Error(`step ${index + 1} label 必须是 string`);
    }
    if (step.on_failed !== undefined && step.on_failed !== null && (Array.isArray(step.on_failed) || typeof step.on_failed !== "object")) {
      throw new Error(`step ${index + 1} on_failed 必须是 object`);
    }
    return {
      name: step.name.trim(),
      params: step.params || {},
      ...(step.save_as ? {save_as: step.save_as} : {}),
      ...(step.label ? {label: step.label} : {}),
      ...(step.on_failed ? {on_failed: step.on_failed} : {}),
    };
  });
}
function parseActionMissionSteps() {
  try {
    const parsed = parseActionMissionInput($("actionMissionSteps").value);
    return normalizeActionMissionSteps(parsed);
  } catch (error) {
    $("completionHint").textContent = `Action Mission JSON 错误: ${error.message}`;
    return null;
  }
}
async function refreshActionMission() {
  const result = await json("/api/action-mission/status");
  if (!result.ok) throw new Error(result.error || "Action Mission 状态刷新失败");
  renderActionMissionStatus(result.action_mission || null);
  return result;
}
async function configureActionMission() {
  const steps = parseActionMissionSteps();
  if (steps === null) return;
  currentActionMission = parseActionMissionInput($("actionMissionSteps").value);
  currentActionMissionSteps = steps;
  const result = await json("/api/action-mission/configure", {
    method: "POST",
    body: JSON.stringify({steps}),
  });
  if (!result.ok) throw new Error(result.error || "Action Mission 配置失败");
  $("completionHint").textContent = "Action Mission 已配置";
  lastActionMissionSummaryHtml = "";
  renderActionMissionStatus(result.action_mission || null);
}
async function startActionMission() {
  const source = String(state.active_source || state.link?.active_source || "real").toLowerCase();
  const sourceLabel = source === "sitl" ? "SITL" : "REAL";
  const confirmed = window.confirm(
    `确认授权并启动本次 ${sourceLabel} Action Mission？\n`
    + "授权绑定本次 run；系统 SEND=OFF 时仍不会实发。"
  );
  if (!confirmed) return;
  const result = await json("/api/action-mission/start", {
    method: "POST",
    body: JSON.stringify({authorize: true, target_source: source}),
  });
  if (!result.ok) throw new Error(result.error || "Action Mission 启动失败");
  $("completionHint").textContent = "Action Mission 已启动";
  lastActionMissionSummaryHtml = "";
  renderActionMissionStatus(result.action_mission || null);
}
async function stopActionMission() {
  stopActionMissionAutoTick();
  const result = await json("/api/action-mission/stop", {method: "POST", body: "{}"});
  if (!result.ok) throw new Error(result.error || "Action Mission 停止失败");
  $("completionHint").textContent = "Action Mission 已停止";
  renderActionMissionStatus(result.action_mission || null);
}
async function resetActionMission() {
  stopActionMissionAutoTick();
  const result = await json("/api/action-mission/reset", {method: "POST", body: "{}"});
  if (!result.ok) throw new Error(result.error || "Action Mission 重置失败");
  $("completionHint").textContent = "Action Mission 已重置";
  lastActionMissionSummaryHtml = "";
  renderActionMissionStatus(result.action_mission || null);
}
async function tickActionMission() {
  const result = await json("/api/action-mission/tick", {method: "POST", body: "{}"});
  if (!result.ok) throw new Error(result.error || "Action Mission 推进失败");
  $("completionHint").textContent = "Action Mission 推进完成";
  renderActionMissionStatus(result.action_mission || null);
}
async function skipCurrentActionMissionStep() {
  const current = lastActionMissionStatus || state.action_mission || {};
  const index = current.current_index ?? "--";
  const action = current.current_action || "--";
  const confirmed = window.confirm(
    `确认跳过当前 Action Mission 阶段？\n\n` +
    `当前序号: ${index}\n` +
    `当前动作: ${action}\n\n` +
    `该操作会停止当前 Action，清理连续控制/LOCAL_POSITION，并尝试保持当前位置，然后进入下一阶段。\n` +
    `不会清空 blackboard。`
  );
  if (!confirmed) return;
  const result = await json("/api/action-mission/skip-current", {
    method: "POST",
    body: "{}",
  });
  if (!result.ok) throw new Error(result.error || "skip current step failed");
  lastActionMissionStatus = result.action_mission || {};
  renderActionMissionStatus(lastActionMissionStatus);
  $("completionHint").textContent = `已跳过当前阶段：${lastActionMissionStatus.reason || "manual_skip"}`;
}
function setActionMissionEditorValue(mission) {
  const editor = $("actionMissionSteps");
  if (!editor) return;
  currentActionMission = mission;
  currentActionMissionSteps = normalizeActionMissionSteps(mission);
  editor.value = JSON.stringify(mission, null, 2);
  renderActionMissionTimeline(lastActionMissionStatus || {}, currentActionMissionSteps);
}
async function loadActionMissionTemplates() {
  const element = $("actionMissionTemplateList");
  if (!element) return;
  try {
    const result = await json("/api/action-mission/templates");
    if (!result.ok) throw new Error(result.error || "模板列表加载失败");
    element.classList.add("action-mission-presets", "primary");
    element.innerHTML = (result.templates || []).map(template =>
      `<button data-action-mission-template="${escapeHtml(template.name)}">
        ${escapeHtml(template.label || template.name)}
      </button>`
    ).join("");
    element.querySelectorAll("[data-action-mission-template]").forEach(button => {
      button.onclick = () => loadActionMissionTemplate(button.dataset.actionMissionTemplate)
        .catch(error => { $("completionHint").textContent = error.message; });
    });
  } catch (error) {
    element.textContent = `模板接口不可用：${error.message}`;
  }
}
async function loadActionMissionTemplate(name, options = {}) {
  const {confirmOverwrite = true} = options;
  const current = $("actionMissionSteps")?.value.trim();
  const defaultText = JSON.stringify(DEFAULT_ACTION_MISSION_STEPS, null, 2).trim();
  if (confirmOverwrite && current && current !== defaultText && !window.confirm("当前 Mission JSON 将被覆盖，确认？")) return;
  const result = await json(`/api/action-mission/template/${encodeURIComponent(name)}`);
  if (!result.ok) throw new Error(result.error || "模板加载失败");
  setActionMissionEditorValue(result.template);
  $("completionHint").textContent = "已加载比赛模板，请检查参数后配置";
}
function validateActionMissionJson() {
  const steps = parseActionMissionSteps();
  if (steps === null) return;
  currentActionMission = parseActionMissionInput($("actionMissionSteps").value);
  currentActionMissionSteps = steps;
  renderActionMissionTimeline(lastActionMissionStatus || {}, currentActionMissionSteps);
  $("completionHint").textContent = `Action Mission JSON 校验通过：${steps.length} 个步骤`;
}
function formatActionMissionJson() {
  const parsed = parseActionMissionInput($("actionMissionSteps").value);
  $("actionMissionSteps").value = JSON.stringify(parsed, null, 2);
  validateActionMissionJson();
}
async function copyText(text, message) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
  } else {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  $("completionHint").textContent = message;
}
async function toggleActionMissionAutoTick() {
  updateActionMissionAutoTickButton();
  $("completionHint").textContent = "任务由 RK3588 后台自动推进，无需保持网页连接";
}
function loadActionMissionPreset(name) {
  const preset = actionMissionPresets[name];
  const editor = $("actionMissionSteps");
  if (!preset || !editor) return;
  const current = editor.value.trim();
  const defaultText = JSON.stringify(DEFAULT_ACTION_MISSION_STEPS, null, 2).trim();
  if (current && current !== defaultText && !window.confirm("当前 Step JSON 将被覆盖，确认？")) {
    return;
  }
  setActionMissionEditorValue(preset);
  $("completionHint").textContent = "已加载模板，请检查参数后 Configure";
}
var selectedActionIsRunning = function () { return window.UavActionLab.selectedActionIsRunning(); };
var toggleActionLabRun = function () { return window.UavActionLab.toggleActionLabRun(); };
var startActionLabAction = function () { return window.UavActionLab.startActionLabAction(); };
var stopActionLabAction = function () { return window.UavActionLab.stopActionLabAction(); };
var resetActionLabAction = function () { return window.UavActionLab.resetActionLabAction(); };

function startStatusUpdates() {
  if (statusUpdatesStop !== null) return statusUpdatesStop;
  let stopped = false;
  let fallbackTimer = null;
  let actionTimer = null;
  let reconnectTimer = null;
  let socket = null;
  const pollStatus = async () => {
    try {
      renderStatus(await json("/api/status"));
    } catch (error) {
      $("completionHint").textContent = `状态刷新失败: ${error.message}`;
    } finally {
      if (!stopped && fallbackTimer !== null) {
        fallbackTimer = setTimeout(pollStatus, 500);
      }
    }
  };
  const pollAction = async () => {
    try {
      await refreshActionStatus();
    } catch (error) {
      $("completionHint").textContent = `Action Lab 刷新失败: ${error.message}`;
    } finally {
      if (!stopped && actionTimer !== null) {
        actionTimer = setTimeout(pollAction, 1000);
      }
    }
  };
  const startActionTimer = () => {
    if (actionTimer !== null) return;
    actionTimer = setTimeout(pollAction, 0);
  };
  // Field Reference polling moved to UavFieldRef.init()
  const startFallback = () => {
    if (fallbackTimer !== null) return;
    fallbackTimer = setTimeout(pollStatus, 0);
    startActionTimer();
  };
  const stopFallback = () => {
    if (fallbackTimer !== null) clearTimeout(fallbackTimer);
    fallbackTimer = null;
  };
  const connect = () => {
    if (stopped) return;
    try {
      socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/status`);
      socket.onmessage = event => renderStatus(JSON.parse(event.data));
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        socket = null;
        startFallback();
        if (!stopped && reconnectTimer === null) {
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
          }, 2000);
        }
      };
      socket.onopen = () => {
        stopFallback();
        startActionTimer();
      };
    } catch {
      startFallback();
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, 2000);
    }
  };
  statusUpdatesStop = () => {
    if (stopped) return;
    stopped = true;
    stopFallback();
    if (actionTimer !== null) clearTimeout(actionTimer);
    if (reconnectTimer !== null) clearTimeout(reconnectTimer);
    actionTimer = null;
    reconnectTimer = null;
    if (socket !== null) socket.close();
    socket = null;
    statusUpdatesStop = null;
  };
  window.addEventListener("beforeunload", statusUpdatesStop, {once: true});
  connect();
  return statusUpdatesStop;
}
var setupActionStatusJsonCopyGuard = function () { window.UavActionLab.setupActionStatusJsonCopyGuard(); };

async function init() {
  // Competition Field Setup — must initialize BEFORE any optional modules
  // so that a failure in Video Panel cannot block the Field Setup lifecycle.
  if (window.UavFieldProfiles && typeof window.UavFieldProfiles.init === "function") {
    window.UavFieldProfiles.init();
  }

  if (window.UavFieldRef && typeof window.UavFieldRef.init === "function") {
    window.UavFieldRef.init();
  }

  // Video panel setup — optional, must not block Field Setup
  if (window.UavVideoPanel &&
      typeof window.UavVideoPanel.setupVideoPanel === "function") {
    Promise.resolve(window.UavVideoPanel.setupVideoPanel()).catch(function (err) {
      console.warn("video panel setup failed", err);
    });
  } else {
    console.warn("UavVideoPanel unavailable");
  }
  $("takeoffButton").onclick = () => {
    const altitude = Number($("takeoffAltitude").value);
    selectAction("takeoff");
    $("actionParams").value = JSON.stringify({altitude_m: altitude}, null, 2);
    if (confirm(`确认准备授权本次起飞至 ${altitude} m？`)) startActionLabAction();
  };
  if ($("landActionButton")) $("landActionButton").onclick = () => {
    selectAction("land");
    $("actionParams").value = "{}";
    startActionLabAction().catch(error => { $("completionHint").textContent = error.message; });
  };
  if ($("sendToggle")) $("sendToggle").onclick = async () => {
    const enabled = !Boolean((state.controllers || {}).send_commands);
    const label = enabled ? "ON" : "OFF";
    if (!confirm(`确认将系统 SEND 切换为 ${label}？`)) return;
    const result = await json("/api/control/send", {
      method: "POST", body: JSON.stringify({enabled: enabled}),
    });
    $("completionHint").textContent = result.message;
  };
  document.querySelectorAll("[data-source]").forEach(button => button.onclick = async () => {
    const source = button.dataset.source;
    if (!confirm(`切换到 ${source.toUpperCase()} 将关闭 SEND 并撤销 run 授权，确认？`)) return;
    const result = await json("/api/telemetry/source", {
      method: "POST", body: JSON.stringify({source: source}),
    });
    $("completionHint").textContent = result.message;
  });
  window.UavFieldRef.init();
  document.querySelectorAll(".tab").forEach(tab => tab.onclick = () => {
    document.querySelectorAll(".tab").forEach(item => item.classList.toggle("active", item === tab));
    document.querySelectorAll(".page").forEach(page => page.classList.toggle("active", page.id === `${tab.dataset.page}Page`));
  });
  $("actionParams").oninput = () => cacheSelectedActionParams();
  setupActionStatusJsonCopyGuard();
  if ($("actionRunToggle")) $("actionRunToggle").onclick = () => toggleActionLabRun().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionDispatchStart")) $("actionDispatchStart").onclick = () => startActionLabAction().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionStop")) $("actionStop").onclick = () => stopActionLabAction().catch(error => { $("completionHint").textContent = error.message; });
  $("actionReset").onclick = () => resetActionLabAction().catch(error => { $("completionHint").textContent = error.message; });
  $("actionRefresh").onclick = () => refreshActionStatus().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionSteps")) {
    await loadActionMissionTemplate("rescue_2026_full_auto", {confirmOverwrite: false});
  }
  if ($("actionMissionConfigure")) $("actionMissionConfigure").onclick = () => configureActionMission().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionStart")) $("actionMissionStart").onclick = () => startActionMission().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionStop")) $("actionMissionStop").onclick = () => stopActionMission().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionReset")) $("actionMissionReset").onclick = () => resetActionMission().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionTick")) $("actionMissionTick").onclick = () => tickActionMission().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionAutoTick")) $("actionMissionAutoTick").onclick = () => toggleActionMissionAutoTick().catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionRefresh")) $("actionMissionRefresh").onclick = () => refreshActionMission().catch(error => { $("completionHint").textContent = error.message; });
  $("actionMissionSkipCurrent")?.addEventListener("click", () => {
    skipCurrentActionMissionStep().catch(error => {
      $("completionHint").textContent = `跳过当前阶段失败: ${error.message}`;
    });
  });
  if ($("actionMissionLoadCustom")) $("actionMissionLoadCustom").onclick = () => {
    $("actionMissionSteps").focus();
    $("completionHint").textContent = "请粘贴自定义任务 JSON，然后校验或配置";
  };
  if ($("actionMissionValidate")) $("actionMissionValidate").onclick = () => validateActionMissionJson();
  if ($("actionMissionFormatJson")) $("actionMissionFormatJson").onclick = () => formatActionMissionJson();
  if ($("actionMissionCopyJson")) $("actionMissionCopyJson").onclick = () => copyText($("actionMissionSteps").value, "任务 JSON 已复制").catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionCopyStatus")) $("actionMissionCopyStatus").onclick = () => copyText(JSON.stringify(lastActionMissionStatus || {}, null, 2), "Action Mission 状态已复制").catch(error => { $("completionHint").textContent = error.message; });
  if ($("actionMissionCopyResult")) $("actionMissionCopyResult").onclick = () => copyText(JSON.stringify(lastActionMissionResult || {}, null, 2), "Action Mission 最后结果已复制").catch(error => { $("completionHint").textContent = error.message; });
  document.querySelectorAll("[data-action-mission-template]").forEach(button => {
    button.onclick = () => loadActionMissionTemplate(button.dataset.actionMissionTemplate).catch(error => { $("completionHint").textContent = error.message; });
  });
  document.querySelectorAll("[data-action-mission-preset]").forEach(button => {
    button.onclick = () => loadActionMissionPreset(button.dataset.actionMissionPreset);
  });
  if ($("servo1Select")) $("servo1Select").dataset.servoPreset = "servo1";
  if ($("servo2Select")) $("servo2Select").dataset.servoPreset = "servo2";
  document.querySelectorAll("[data-servo-preset]").forEach(button => {
    button.onclick = () => selectServoPreset(button.dataset.servoPreset);
  });
  await loadActionLab();
  startStatusUpdates();
}
window.UavControl = Object.freeze({init});
