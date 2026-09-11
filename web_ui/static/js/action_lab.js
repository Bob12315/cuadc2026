// action_lab.js — Action Lab panel logic for UAV Action Console
// Extracted from app.js (WU-4 v3).  Sole owner of Action Lab state.
// Uses configure() pattern — no _refs, no typeof guards, no dual-state.
(function () {
  "use strict";

  var cfg = {
    api: null,
    dom: null,
    format: null,
    appState: null,
    safetyHints: {},
    zhLabels: {},
    getState: null,
    renderFieldMap: null,
    setCompletionHint: null,
  };

  var labState = {
    actionSpecs: [],
    selectedActionName: "",
    latestActionLab: null,
    actionParamCache: {},
    jsonSelecting: false,
  };

  var ACTION_UI_ALLOWED_NAMES = new Set([
    "takeoff",
    "land",
    "goto_waypoint",
    "target_lock",
    "align_descend",
    "select_drop_targets",
    "gps_capture_view",
    "gps_fuse_views",
    "gps_target_lock",
  ]);

  var SERVO_PRESETS = {
    servo1: {
      label: "舵机 1：后方 SERVO8",
      params: {
        enabled: true,
        payload_id: "payload_1",
        target_id: "target_debug",
        servo_outputs: [{channel: 8, release_pwm: 1800, hold_pwm: 1370}],
        priority: 3,
        release_wait_s: 1,
        release_wait_updates: 5,
        key: "manual_rear_servo8_release",
      },
    },
    servo2: {
      label: "舵机 2：前方 SERVO9",
      params: {
        enabled: true,
        payload_id: "payload_2",
        target_id: "target_debug",
        servo_outputs: [{channel: 9, release_pwm: 1745, hold_pwm: 1325}],
        priority: 3,
        release_wait_s: 1,
        release_wait_updates: 5,
        key: "manual_front_servo9_release",
      },
    },
  };

  // ------------------------------------------------------------------
  // configure — called once by app.js after script loads
  // ------------------------------------------------------------------
  function configure(options) {
    if (!options) return;
    if (options.api) cfg.api = options.api;
    if (options.dom) cfg.dom = options.dom;
    if (options.format) cfg.format = options.format;
    if (options.appState) cfg.appState = options.appState;
    if (options.safetyHints) cfg.safetyHints = options.safetyHints;
    if (options.zhLabels) cfg.zhLabels = options.zhLabels;
    if (options.getState) cfg.getState = options.getState;
    if (options.renderFieldMap) cfg.renderFieldMap = options.renderFieldMap;
    if (options.setCompletionHint) cfg.setCompletionHint = options.setCompletionHint;
  }

  // ------------------------------------------------------------------
  // getters (sole owners of Action Lab state)
  // ------------------------------------------------------------------
  function getActionSpecs() { return labState.actionSpecs; }
  function getSelectedActionName() { return labState.selectedActionName; }
  function getLatestActionLab() { return labState.latestActionLab; }
  function getActionParamCache() { return labState.actionParamCache; }

  // ------------------------------------------------------------------
  // helpers
  // ------------------------------------------------------------------
  function _api() { return cfg.api.request; }
  function _dom() { return cfg.dom; }
  function _fmt() { return cfg.format; }
  function _state() { return cfg.getState ? cfg.getState() : {}; }

  function actionDisplayName(name, fallback) {
    var base = String(fallback || name || "--");
    var zh = (cfg.zhLabels && cfg.zhLabels[name]) ? cfg.zhLabels[name] : "";
    if (!zh || base.indexOf(zh) >= 0) return base;
    return base + " " + zh;
  }
  function actionNameWithZh(name) {
    if (!name) return "--";
    return actionDisplayName(name, name);
  }

  // ------------------------------------------------------------------
  // shared utilities (used by both Action Lab and Dashboard)
  // ------------------------------------------------------------------
  function dispatchFromActionLab(payload) {
    var actionLab = payload || labState.latestActionLab || {};
    var status = (actionLab && actionLab.status) ? actionLab.status : (actionLab || {});
    var last = (status && status.last_result) ? status.last_result : {};
    var detail = last.detail || {};
    return actionLab.dispatch || detail.dispatch || detail.last_dispatch || actionLab.last_dispatch || last.dispatch || {};
  }
  function countDispatchItems(value) {
    if (Array.isArray(value)) return value.length;
    if (value && typeof value === "object") return Object.keys(value).length;
    return value ? 1 : 0;
  }

  // ------------------------------------------------------------------
  // Action Lab functions
  // ------------------------------------------------------------------

  function loadActionLab() {
    return _api()("/api/actions/list").then(function (result) {
      labState.actionSpecs = result.actions || [];
      var $ = _dom().$;
      var fmt = _fmt();
      var allowed = ACTION_UI_ALLOWED_NAMES;
      $("actionButtons").innerHTML = labState.actionSpecs.filter(function (spec) { return allowed.has(spec.name); }).map(function (spec) {
        return "<button data-action-name=\"" + fmt.escapeHtml(spec.name) + "\">" + fmt.escapeHtml(actionDisplayName(spec.name, spec.label || spec.name)) + "</button>";
      }).join("");
      $("actionButtons").querySelectorAll("[data-action-name]").forEach(function (button) {
        button.onclick = function () { selectAction(button.dataset.actionName); };
      });
      if (labState.actionSpecs.length) {
        var firstRegular = labState.actionSpecs.find(function (spec) { return allowed.has(spec.name); }) || labState.actionSpecs[0];
        selectAction(firstRegular.name);
      }
    });
  }

  function cacheSelectedActionParams() {
    var name = labState.selectedActionName;
    if (!name || !_dom().$("actionParams")) return;
    var cache = labState.actionParamCache;
    cache[name] = _dom().$("actionParams").value;
  }

  function selectAction(name) {
    var spec = labState.actionSpecs.find(function (item) { return item.name === name; });
    if (!spec) return;
    cacheSelectedActionParams();
    labState.selectedActionName = spec.name;
    var cache = labState.actionParamCache;
    if (cache[spec.name] === undefined) {
      cache[spec.name] = JSON.stringify(spec.default_params || {}, null, 2);
    }
    _dom().$("actionParams").value = cache[spec.name];
    var hint = spec.description || spec.label || spec.name;
    if (spec.name === "payload_release") {
      hint = hint + " servo_outputs 是飞控 SERVO 输出通道配置，不是遥控器 RC 输入通道。舵机插在输出 8 就填 channel=8。";
    } else if (spec.name === "goto_waypoint") {
      var params = spec.default_params || {};
      try { params = JSON.parse(cache[spec.name]); } catch (e) { /* use default */ }
      var frame = params.waypoint_mode === "field" ? "FIELD 坐标（x=右侧，y=前方）" : "LOCAL_NED 坐标";
      hint = hint + " 当前输入为 " + frame + "；实发前会在 Action detail 中显示 local_target。";
    }
    if (cfg.setCompletionHint) cfg.setCompletionHint(hint);
    if (_dom().$("actionParamHint")) _dom().$("actionParamHint").textContent = hint;
    var safetyEl = _dom().$("actionSafetyHint");
    if (safetyEl) safetyEl.textContent = (cfg.safetyHints && cfg.safetyHints[spec.name]) ? cfg.safetyHints[spec.name] : "普通 Action；Dispatch 请求下发，实际发送仍受系统 SEND 和 dispatch 结果约束。";
    document.querySelectorAll("[data-action-name]").forEach(function (button) {
      button.classList.toggle("active-choice", button.dataset.actionName === spec.name);
    });
    document.querySelectorAll("[data-servo-preset]").forEach(function (button) {
      button.classList.remove("active-choice");
    });
    renderActionLabStatus(labState.latestActionLab);
  }

  function selectServoPreset(name) {
    var preset = SERVO_PRESETS[name];
    if (!preset) return;
    cacheSelectedActionParams();
    var output = preset.params.servo_outputs[0];
    labState.selectedActionName = "payload_release";
    _dom().$("actionParams").value = JSON.stringify(preset.params, null, 2);
    _dom().$("actionParamHint").textContent = preset.label + "：锁定 "
      + output.hold_pwm + "，投放 " + output.release_pwm
      + "。已绑定 payload_release，需点击“Start Authorized Run”后才执行。";
    _dom().$("actionSafetyHint").textContent = "舵机预设绑定本次 payload_release Action run；实际发送仍需 run 授权与 System SEND=ON。";
    if (_dom().$("servoSelectionHint")) {
      _dom().$("servoSelectionHint").textContent = preset.label
        + " 已绑定：锁定 " + output.hold_pwm + "，投放 " + output.release_pwm
        + "。点击“Start Authorized Run”前未执行任何 Action。";
    }
    if (cfg.setCompletionHint) cfg.setCompletionHint("已选择 " + preset.label + "；可授权启动本次 Action run。");
    document.querySelectorAll("[data-action-name]").forEach(function (button) {
      button.classList.remove("active-choice");
    });
    document.querySelectorAll("[data-servo-preset]").forEach(function (button) {
      button.classList.toggle("active-choice", button.dataset.servoPreset === name);
    });
    renderActionLabStatus(labState.latestActionLab);
  }

  function refreshActionStatus() {
    return _api()("/api/actions/status").then(function (result) {
      if (!result.ok) throw new Error(result.error || "action status failed");
      renderActionLabStatus(result.action_lab || null);
      if (cfg.renderFieldMap) cfg.renderFieldMap(_state());
      return result;
    });
  }

  function renderActionLabStatus(actionLab) {
    var $ = _dom().$;
    if (!$("actionState")) return;
    if (actionLab) labState.latestActionLab = actionLab;
    var payload = labState.latestActionLab || {};
    var status = (payload && payload.status) ? payload.status : (payload || {});
    var last = (status && status.last_result) ? status.last_result : {};
    var detail = last.detail || {};
    var note = (payload && payload.note) ? payload.note : "";
    var dispatch = dispatchFromActionLab(payload);
    var sentCount = countDispatchItems(dispatch.sent);
    var skippedCount = countDispatchItems(dispatch.skipped);
    var errorCount = countDispatchItems(dispatch.errors);
    var runningAction = (status && status.running) ? (status.action_name || "") : "";
    var selectedIsRunning = Boolean(runningAction && runningAction === labState.selectedActionName);
    if ($("actionRunStatus")) {
      $("actionRunStatus").textContent = (payload && payload.run_authorized)
        ? "Run authorized" + (note ? ": " + note : "")
        : "Run not authorized" + (note ? ": " + note : "");
    }
    $("actionState").textContent = (status && status.state) ? status.state : "--";
    $("actionSelected").textContent = actionNameWithZh(labState.selectedActionName);
    $("actionRunningAction").textContent = actionNameWithZh(runningAction);
    $("actionRunning").textContent = String(Boolean(status && status.running));
    $("actionReason").textContent = last.reason || "--";
    $("actionDone").textContent = String(Boolean(last.done));
    $("actionFailed").textContent = String(Boolean(last.failed));
    if ($("actionRunToggle")) {
      $("actionRunToggle").textContent = selectedIsRunning ? "停止" : "开始";
      $("actionRunToggle").classList.toggle("stop", selectedIsRunning);
    }
    if ($("actionStop")) $("actionStop").disabled = !Boolean(status && status.running);
    if ($("actionDispatchStart")) $("actionDispatchStart").disabled = !labState.selectedActionName;
    var set = _dom().setOptionalText;
    var st = _state();
    var ctrl = st.controllers || {};
    set("actionGateAuthorized", String(Boolean(payload && payload.run_authorized)));
    set("actionGateEffective", String(Boolean(payload && payload.dispatch_effective)));
    set("actionGateSystemSend", String(Boolean(ctrl.send_commands)));
    set("actionGateRunId", (payload && payload.run_id) || "--");
    set("actionGateSentCount", String(sentCount));
    set("actionGateSkippedCount", String(skippedCount));
    set("actionGateErrorCount", String(errorCount));
    set("actionGateNote", note || dispatch.note || "--");
    if ($("actionSwitchHint")) {
      $("actionSwitchHint").textContent = runningAction && runningAction !== labState.selectedActionName
        ? "当前运行：" + actionNameWithZh(runningAction) + "；当前选中：" + actionNameWithZh(labState.selectedActionName) + "。点击\u201c开始\u201d将停止 " + actionNameWithZh(runningAction) + " 并启动 " + actionNameWithZh(labState.selectedActionName) + "。"
        : "";
    }
    var highlights = {
      dispatch_state: errorCount > 0 ? "errors" : sentCount > 0 ? "已发送" : skippedCount > 0 ? "skipped" : undefined,
      sent: dispatch.sent,
      skipped: dispatch.skipped,
      errors: dispatch.errors,
      last_servo_command: payload.last_servo_command || detail.last_servo_command,
      command: detail.command,
      estimated_objects: detail.estimated_objects,
      channels: detail.channels,
      servo_channels: detail.servo_channels,
      servo_outputs: detail.servo_outputs,
      channel_semantics: detail.channel_semantics,
      release_sent: detail.release_sent,
      hold_sent: detail.hold_sent,
      release_time: detail.release_time,
    };
    $("actionHighlights").classList.toggle("has-errors", errorCount > 0);
    $("actionHighlights").classList.toggle("has-sent", sentCount > 0 && errorCount === 0);
    $("actionHighlights").classList.toggle("has-skipped", skippedCount > 0 && sentCount === 0 && errorCount === 0);
    var fmt = _fmt();
    $("actionHighlights").innerHTML = Object.entries(highlights)
      .filter(function (entry) { return entry[1] !== undefined; })
      .map(function (entry) { return "<div><span>" + fmt.escapeHtml(entry[0]) + "</span><code>" + fmt.escapeHtml(JSON.stringify(entry[1])) + "</code></div>"; })
      .join("");
    updateActionStatusJson(JSON.stringify(payload || status || {}, null, 2));
  }

  function nodeInside(element, node) {
    if (!element || !node) return false;
    var owner = node.nodeType === 3 ? node.parentNode : node;
    return owner === element || (typeof element.contains === "function" && element.contains(owner));
  }

  function actionStatusJsonHasSelection() {
    var element = _dom().$("actionStatusJson");
    var selection = typeof window !== "undefined" && window.getSelection ? window.getSelection() : null;
    if (!element || !selection || selection.isCollapsed) return false;
    return nodeInside(element, selection.anchorNode) || nodeInside(element, selection.focusNode);
  }

  function updateActionStatusJson(text) {
    var element = _dom().$("actionStatusJson");
    if (!element) return;
    if (labState.jsonSelecting || actionStatusJsonHasSelection()) return;
    if (element.textContent === text) return;
    var scrollTop = element.scrollTop;
    var scrollLeft = element.scrollLeft;
    element.textContent = text;
    element.scrollTop = scrollTop;
    element.scrollLeft = scrollLeft;
  }

  function parseActionParams() {
    cacheSelectedActionParams();
    try {
      var value = _dom().$("actionParams").value.trim();
      return value ? JSON.parse(value) : {};
    } catch (error) {
      _dom().$("completionHint").textContent = "Action params JSON 错误: " + error.message;
      return null;
    }
  }

  function selectedActionIsRunning() {
    var status = (labState.latestActionLab && labState.latestActionLab.status) ? labState.latestActionLab.status : {};
    return Boolean(status.running && status.action_name === labState.selectedActionName);
  }

  function toggleActionLabRun() {
    if (selectedActionIsRunning()) {
      return window.UavActionLab.stopActionLabAction();
    } else {
      return window.UavActionLab.startActionLabAction(true);
    }
  }

  function activeSource() {
    var st = _state() || {};
    return String(st.active_source || (st.link && st.link.active_source) || "real").toLowerCase();
  }

  function startActionLabAction() {
    if (!labState.selectedActionName) return;
    var params = parseActionParams();
    if (params === null) return;
    var source = activeSource();
    var sourceLabel = source === "sitl" ? "SITL" : "REAL";
    var confirmed = window.confirm(
      "确认授权本次 " + sourceLabel + " Action run？\n"
      + "授权只绑定本次 run，不会继承到下一次。\n"
      + "系统 SEND=OFF 时仍不会实发。"
    );
    if (!confirmed) return;
    var requestBody = {
      name: labState.selectedActionName,
      params: params,
      authorize: true,
      target_source: source,
    };
    console.log("Action Lab start request body", requestBody);
    return _api()("/api/actions/start", {
      method: "POST",
      body: JSON.stringify(requestBody),
    }).then(function (result) {
      if (!result.ok) throw new Error(result.error || "action start failed");
      _dom().$("completionHint").textContent = result.note || (sourceLabel + " action run started");
      renderActionLabStatus(result.action_lab || result.status);
      if (cfg.renderFieldMap) cfg.renderFieldMap(_state());
    });
  }

  function stopActionLabAction() {
    return _api()("/api/actions/stop", {method: "POST", body: "{}"}).then(function (result) {
      if (!result.ok) throw new Error(result.error || "action stop failed");
      renderActionLabStatus(result.action_lab || result.status);
      if (cfg.renderFieldMap) cfg.renderFieldMap(_state());
    });
  }

  function resetActionLabAction() {
    cacheSelectedActionParams();
    return _api()("/api/actions/reset", {method: "POST", body: "{}"}).then(function (result) {
      if (!result.ok) throw new Error(result.error || "action reset failed");
      renderActionLabStatus(result.action_lab || result.status);
      if (cfg.renderFieldMap) cfg.renderFieldMap(_state());
    });
  }

  function setupActionStatusJsonCopyGuard() {
    var $ = _dom().$;
    var element = $("actionStatusJson");
    if (!element) return;
    element.addEventListener("mousedown", function () { labState.jsonSelecting = true; });
    element.addEventListener("touchstart", function () { labState.jsonSelecting = true; });
    document.addEventListener("mouseup", function () {
      setTimeout(function () { labState.jsonSelecting = actionStatusJsonHasSelection(); }, 0);
    });
    document.addEventListener("touchend", function () {
      setTimeout(function () { labState.jsonSelecting = actionStatusJsonHasSelection(); }, 0);
    });
    document.addEventListener("selectionchange", function () {
      if (!actionStatusJsonHasSelection()) labState.jsonSelecting = false;
    });
  }

  // ------------------------------------------------------------------
  // public API
  // ------------------------------------------------------------------

  window.UavActionLab = {
    configure: configure,

    getActionSpecs: getActionSpecs,
    getSelectedActionName: getSelectedActionName,
    getLatestActionLab: getLatestActionLab,
    getActionParamCache: getActionParamCache,

    dispatchFromActionLab: dispatchFromActionLab,
    countDispatchItems: countDispatchItems,

    loadActionLab: loadActionLab,
    selectAction: selectAction,
    selectServoPreset: selectServoPreset,
    cacheSelectedActionParams: cacheSelectedActionParams,
    parseActionParams: parseActionParams,
    refreshActionStatus: refreshActionStatus,
    renderActionLabStatus: renderActionLabStatus,
    startActionLabAction: startActionLabAction,
    toggleActionLabRun: toggleActionLabRun,
    stopActionLabAction: stopActionLabAction,
    resetActionLabAction: resetActionLabAction,
    selectedActionIsRunning: selectedActionIsRunning,
    setupActionStatusJsonCopyGuard: setupActionStatusJsonCopyGuard,
  };
})();
