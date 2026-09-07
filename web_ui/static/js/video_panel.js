// video_panel.js — Video panel logic for UAV Action Console
// Extracted from app.js (WU-6 v2).  Uses configure() pattern.
(function () {
  "use strict";

  var cfg = {
    api: null,
    dom: null,
    format: null,
    getState: null,
    targetAction: null,
    setCompletionHint: null,
  };

  var videoState = {
    latestCameraRecording: {recording: false, path: "", message: "未录制"},
  };

  function configure(options) {
    if (!options) return;
    if (options.api) cfg.api = options.api;
    if (options.dom) cfg.dom = options.dom;
    if (options.format) cfg.format = options.format;
    if (options.getState) cfg.getState = options.getState;
    if (options.targetAction) cfg.targetAction = options.targetAction;
    if (options.setCompletionHint) cfg.setCompletionHint = options.setCompletionHint;
  }

  function _state() { return cfg.getState ? cfg.getState() : {}; }
  function _api() { return cfg.api ? cfg.api.request : window.UavApi.request; }
  function _dom() { return cfg.dom || window.UavDom; }
  function _setHint(text) { if (typeof cfg.setCompletionHint === "function") cfg.setCompletionHint(text); }

  function getLatestCameraRecording() { return videoState.latestCameraRecording; }

  // Camera recording render + refresh + toggle
  function renderCameraRecordingStatus(payload) {
    if (payload) videoState.latestCameraRecording = payload;
    var status = videoState.latestCameraRecording || {};
    var button = _dom().$("cameraRecordToggle");
    var label = _dom().$("cameraRecordStatus");
    if (button) {
      button.textContent = status.recording ? "停止录制" : "开始录制";
      button.classList.toggle("warning", Boolean(status.recording));
    }
    if (label) {
      var path = status.path ? " · " + status.path : "";
      label.textContent = (status.message || (status.recording ? "录制中" : "未录制")) + path;
    }
  }

  async function refreshCameraRecordingStatus() {
    try {
      var result = await _api()("/api/camera-recording/status");
      if (result.ok) renderCameraRecordingStatus(result.recording || {});
      return result;
    } catch (err) {
      console.warn("camera recording status failed", err);
    }
  }

  async function toggleCameraRecording() {
    var result = await _api()("/api/camera-recording/toggle", {method: "POST", body: "{}"});
    renderCameraRecordingStatus(result.recording || {message: result.message || "录制状态未知"});
    _setHint(result.message || (result.ok ? "camera recording toggled" : "camera recording failed"));
    return result;
  }

  async function resetVirtualNadir() {
    var result = await _api()("/api/virtual-nadir/reset", {method: "POST", body: "{}"});
    _setHint(result.message || (result.ok ? "虚拟云台方向已重置" : "虚拟云台方向重置失败"));
    return result;
  }

  // Click video target lock
  function clickVideo(event) {
    var st = _state();
    var scene = st.scene || {};
    var img = _dom().$("video");
    if (!scene.image_width || !scene.image_height) return;
    var rect = img.getBoundingClientRect();
    var sourceRatio = scene.image_width / scene.image_height;
    var boxRatio = rect.width / rect.height;
    var shownWidth = sourceRatio > boxRatio ? rect.width : rect.height * sourceRatio;
    var shownHeight = sourceRatio > boxRatio ? rect.width / sourceRatio : rect.height;
    var offsetX = (rect.width - shownWidth) / 2;
    var offsetY = (rect.height - shownHeight) / 2;
    var displayX = event.clientX - rect.left - offsetX;
    var displayY = event.clientY - rect.top - offsetY;
    if (displayX < 0 || displayY < 0 || displayX > shownWidth || displayY > shownHeight) return;
    var x = displayX * scene.image_width / shownWidth;
    var y = displayY * scene.image_height / shownHeight;
    var hits = (scene.detections || []).filter(function (d) { return x >= d.x1 && x <= d.x2 && y >= d.y1 && y <= d.y2; });
    if (!hits.length) {
      _setHint("点击位置没有可锁定目标");
      return;
    }
    hits.sort(function (a, b) { return (a.w * a.h) - (b.w * b.h); });
    if (cfg.targetAction) {
      Promise.resolve(cfg.targetAction("lock", hits[0].track_id)).catch(function (error) {
        _setHint(error.message || "目标锁定失败");
      });
    }
  }

  // Full video panel setup (URL, onload, onerror, retry, hit, recording)
  async function setupVideoPanel() {
    var videoConfig = await _api()("/api/yolo/stream");
    var videoUrl = location.protocol + "//" + location.hostname + ":" + videoConfig.port + videoConfig.path;
    var $ = _dom().$;
    $("video").src = videoUrl;
    $("video").onload = function () { $("videoOffline").style.display = "none"; };
    $("video").onerror = function () {
      $("videoOffline").style.display = "block";
      setTimeout(function () { $("video").src = videoUrl + "?retry=" + Date.now(); }, 1500);
    };
    $("hitCanvas").onclick = clickVideo;
    var toggle = $("cameraRecordToggle");
    if (toggle) {
      toggle.onclick = function () {
        toggleCameraRecording().catch(function (error) {
          renderCameraRecordingStatus({recording: false, path: "", message: "录制命令失败: " + error.message});
        });
      };
    }
    var reset = $("virtualNadirReset");
    if (reset) {
      reset.onclick = function () {
        reset.disabled = true;
        resetVirtualNadir().catch(function (error) {
          _setHint("虚拟云台方向重置失败: " + error.message);
        }).finally(function () {
          reset.disabled = false;
        });
      };
    }
    refreshCameraRecordingStatus().catch(function (err) {
      console.warn("camera recording init status failed", err);
    });
  }

  window.UavVideoPanel = {
    configure: configure,
    getLatestCameraRecording: getLatestCameraRecording,
    refreshCameraRecordingStatus: refreshCameraRecordingStatus,
    toggleCameraRecording: toggleCameraRecording,
    resetVirtualNadir: resetVirtualNadir,
    renderCameraRecordingStatus: renderCameraRecordingStatus,
    clickVideo: clickVideo,
    setupVideoPanel: setupVideoPanel,
  };
})();
