(function () {
  "use strict";

  function detail(mission) {
    return mission && typeof mission.detail === "object" && mission.detail ? mission.detail : {};
  }

  function blackboard(mission) {
    const value = detail(mission).blackboard;
    return value && typeof value === "object" ? value : {};
  }

  function failurePolicyLabel(policy) {
    if (!policy || typeof policy !== "object") return "-";
    const action = policy.action || "fail";
    if (policy.target) return `${action}:${policy.target}`;
    return policy.max_attempts === undefined ? String(action) : `${action} x${policy.max_attempts}`;
  }

  function statusLabel(status) {
    return {pending: "待执行", running: "执行中", paused: "已暂停", done: "已完成", failed: "失败",
      skipped: "已跳过", continued: "已继续", stopped: "已停止"}[status] || status;
  }

  function durationLabel(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) return "-";
    if (seconds < 60) return `${seconds.toFixed(2)} 秒`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes} 分 ${(seconds - minutes * 60).toFixed(1)} 秒`;
  }

  window.UavMission = Object.freeze({detail, blackboard, failurePolicyLabel, statusLabel, durationLabel});
})();
