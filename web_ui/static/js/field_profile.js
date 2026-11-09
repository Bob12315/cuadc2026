window.UavFieldProfiles = (function () {
    "use strict";

    // ── module state ────────────────────────────────────────────────────
    var lastStatus = null;
    var requestBusy = false;
    var templateSummary = null;
    var initCalled = false;

    function $(id) { return document.getElementById(id); }
    function setText(id, text) {
        var e = $(id);
        if (e) {
            var t = typeof text === "string" ? text : String(text || "");
            if (e.textContent !== t) e.textContent = t;
        }
    }
    var api = window.UavApi;

    function gps2(lat, lon) {
        if (lat == null || lon == null) return "--";
        return Number(lat).toFixed(7) + ", " + Number(lon).toFixed(7);
    }

    // ── fetch template summary (one-time) ──────────────────────────────
    async function fetchTemplateSummary() {
        try {
            var d = await api.request("/api/field-profiles/competition_runtime");
            if (d && d.ok === true) {
                templateSummary = d;
                renderTemplateSummary(d);
            }
        } catch (e) { /* ignore */ }
    }

    function renderTemplateSummary(data) {
        if (!data) return;
        var fg = data.field_geometry || {};
        setText("cfsLaneHalfWidth", (fg.lane_half_width_m || "--") + " m");
        setText("cfsDropRange",
            ((fg.drop_area_y_min_m != null ? fg.drop_area_y_min_m : fg.drop_area_y_min) || "--") + " \u2013 " +
            ((fg.drop_area_y_max_m != null ? fg.drop_area_y_max_m : fg.drop_area_y_max) || "--") + " m");
        setText("cfsRecceRange",
            ((fg.recce_area_y_min_m != null ? fg.recce_area_y_min_m : fg.recce_area_y_min) || "--") + " \u2013 " +
            ((fg.recce_area_y_max_m != null ? fg.recce_area_y_max_m : fg.recce_area_y_max) || "--") + " m");
        var ds = data.drop_scan || {};
        setText("cfsDropScanPoints",
            (ds.waypoints || []).map(function (w, i) {
                return "pt" + (i + 1) + "(" + w.x_m + "," + w.y_m + "," + w.altitude_m + ")";
            }).join(" ") || "--");
        setText("cfsScanAltitude",
            (ds.waypoints && ds.waypoints.length ? ds.waypoints[0].altitude_m + " m" : "--"));
        var ros = data.runtime_origin_sampling || {};
        setText("cfsSamplingPolicy",
            ros.min_samples + " samples / " + ros.sample_window_s + "s / spread<" +
            ros.max_horizontal_spread_m + "m / " + ros.estimator);
        var bp = data.binding_policy || {};
        setText("cfsBaselinePolicy",
            "min=" + (bp.min_baseline_m || "--") + "m warn=" + (bp.warn_baseline_below_m || "--") + "m");
    }

    // ── input validation ──────────────────────────────────────────────
    function parseInputLatLon() {
        var latEl = $("cfsForwardLat"), lonEl = $("cfsForwardLon");
        if (!latEl || !lonEl) return null;
        var latStr = (latEl.value || "").trim();
        var lonStr = (lonEl.value || "").trim();
        if (!latStr || !lonStr) return null;
        var lat = Number(latStr), lon = Number(lonStr);
        if (!isFinite(lat) || !isFinite(lon)) return null;
        if (lat > 90 || lat < -90) return null;
        if (lon > 180 || lon < -180) return null;
        return { lat: lat, lon: lon };
    }

    function isInputValid() {
        return parseInputLatLon() !== null;
    }

    // ── state machine ──────────────────────────────────────────────────
    function getRuntimeState() {
        var fr = (lastStatus || {}).field_reference || {};
        var runtime = fr.runtime_binding || {};
        return {
            state: runtime.state || "idle",
            canFinalize: (runtime.sampling || {}).can_finalize === true,
            isFrozen: fr.is_frozen === true,
            isConfirmed: fr.is_confirmed === true,
            isApplied: runtime.state === "applied",
            isSampling: runtime.state === "sampling",
            isSamplingFailed: runtime.state === "sampling_failed",
            isApplyFailed: runtime.state === "apply_failed"
        };
    }

    function updateButtons() {
        var rs = getRuntimeState();
        var busy = requestBusy;
        var inputOk = isInputValid();
        var sBtn = $("cfsStart");
        var rtkBtn = $("cfsStartRtk");
        var cBtn = $("cfsCancel"), rBtn = $("cfsReset");

        function disableAll() {
            if (sBtn) sBtn.disabled = true;
            if (rtkBtn) rtkBtn.disabled = true;
            if (cBtn) cBtn.disabled = true;
            if (rBtn) rBtn.disabled = true;
        }

        // Lock B inputs during non-idle states
        var inputsLocked = rs.state !== "idle";
        var latEl = $("cfsForwardLat"), lonEl = $("cfsForwardLon");
        if (latEl) latEl.disabled = inputsLocked;
        if (lonEl) lonEl.disabled = inputsLocked;

        if (busy) { disableAll(); return; }

        if (rs.isApplied) {
            // applied — only Reset
            disableAll();
            if (rBtn) rBtn.disabled = false;
            return;
        }

        if (rs.state === "idle") {
            disableAll();
            if (sBtn && inputOk && !rs.isFrozen) sBtn.disabled = false;
            if (rtkBtn && !rs.isFrozen) rtkBtn.disabled = false;
            if (rBtn) rBtn.disabled = false;
            return;
        }

        if (rs.isSampling) {
            disableAll();
            if (cBtn) cBtn.disabled = false;
            if (rBtn) rBtn.disabled = false;
            return;
        }

        if (rs.isSamplingFailed) {
            disableAll();
            if (cBtn) cBtn.disabled = false;
            if (rBtn) rBtn.disabled = false;
            return;
        }

        if (rs.isApplyFailed) {
            disableAll();
            if (cBtn) cBtn.disabled = false;
            if (rBtn) rBtn.disabled = false;
            return;
        }

        disableAll();
        if (rBtn) rBtn.disabled = false;
    }

    // ── render status ──────────────────────────────────────────────────
    function onFieldReferenceStatus(data) {
        lastStatus = data;
        var fr = (data || {}).field_reference || {};
        var runtime = fr.runtime_binding || {};
        var sampling = runtime.sampling || {};
        var telemetry = (data || {}).telemetry || {};

        // ---- telemetry ----------------------------------------------------
        setText("cfsCurrentGps", gps2(telemetry.lat, telemetry.lon));
        setText("cfsGpsFixSats",
            "fix=" + (telemetry.gps_fix_type != null ? telemetry.gps_fix_type : "--") +
            " sats=" + (telemetry.satellites_visible != null ? telemetry.satellites_visible : "--"));
        setText("cfsGpsEphEpv",
            "eph=" + (telemetry.gps_eph != null ? Number(telemetry.gps_eph).toFixed(2) : "--") +
            " epv=" + (telemetry.gps_epv != null ? Number(telemetry.gps_epv).toFixed(2) : "--"));
        setText("cfsGpsValid", telemetry.global_position_valid ? "YES" : "no");
        setText("cfsGpsTimestamp",
            telemetry.last_global_position_time != null
                ? String(telemetry.last_global_position_time)
                : "--");

        // ---- runtime panel ------------------------------------------------
        var rsPanel = $("cfsRuntimePanel");
        if (rsPanel) {
            rsPanel.style.display =
                (runtime.state === "sampling" || runtime.state === "sampling_failed")
                    ? "" : "none";
        }
        setText("cfsSamplingState", runtime.state || "--");
        setText("cfsSamplingAccepted",
            (sampling.accepted_samples != null ? sampling.accepted_samples : "--") +
            " / " + (sampling.min_samples || 20));
        setText("cfsSamplingRejected",
            sampling.rejected_samples != null ? String(sampling.rejected_samples) : "--");
        setText("cfsSamplingDuplicate",
            sampling.duplicate_samples != null ? String(sampling.duplicate_samples) : "--");
        setText("cfsSamplingElapsed",
            (sampling.elapsed_s != null ? Number(sampling.elapsed_s).toFixed(1) : "--") +
            " / " + (sampling.sample_window_s || 0) + " s");
        var summary = runtime.candidate_summary;
        setText("cfsSamplingSpread",
            (summary && summary.horizontal_spread_m != null)
                ? Number(summary.horizontal_spread_m).toFixed(3) + " m" : "--");
        setText("cfsSamplingLastRejection", sampling.last_rejection_reason || "--");

        var prog = $("cfsSamplingProgress");
        if (prog) {
            prog.max = Math.max(Number(sampling.sample_window_s) || 1, 1);
            prog.value = Math.min(Number(sampling.elapsed_s) || 0, prog.max);
        }

        // ---- candidate preview --------------------------------------------
        var preview_panel = $("cfsCandidatePanel");
        var preview_error = $("cfsError");
        if (preview_panel) {
            var showPreview = (summary && runtime.state === "sampling" && sampling.window_complete);
            if (runtime.preview_error) showPreview = true;
            if (runtime.state === "sampling_failed") showPreview = true;
            preview_panel.style.display = showPreview ? "" : "none";
        }
        if (summary) {
            setText("cfsOriginA", gps2(summary.origin_lat, summary.origin_lon));
            setText("cfsForwardB", gps2(summary.forward_marker_lat, summary.forward_marker_lon));
            setText("cfsBaseline",
                summary.baseline_m != null ? Number(summary.baseline_m).toFixed(2) + " m" : "--");
            setText("cfsHeading",
                summary.field_heading_deg != null
                    ? Number(summary.field_heading_deg).toFixed(2) + " deg" : "--");
            setText("cfsWarnings",
                (summary.warnings && summary.warnings.length)
                    ? summary.warnings.join("; ") : "--");
        } else {
            setText("cfsOriginA", "--");
            setText("cfsForwardB",
                gps2(runtime.forward_marker_lat, runtime.forward_marker_lon));
            setText("cfsBaseline", "--");
            setText("cfsHeading", "--");
            setText("cfsWarnings", "--");
        }
        setText("cfsError", runtime.preview_error || runtime.last_error || "--");

        // ---- confirmed panel ----------------------------------------------
        var cp = $("cfsConfirmedPanel");
        if (cp) cp.style.display = (runtime.state === "applied") ? "" : "none";
        setText("cfsConfirmed", fr.is_confirmed ? "YES" : "no");
        setText("cfsFrozen", fr.is_frozen ? "YES" : "no");
        setText("cfsGpsReady", fr.is_ready_for_field_to_gps ? "YES" : "no");
        setText("cfsSynced", fr.synced_to_runtime ? "YES" : "no");

        // ---- Field Map (runtime geometry) ---------------------------------
        var geom = runtime.geometry || (runtime.last_result || {}).geometry;
        if (window.UavFieldMap && window.UavFieldMap.setRuntimeGeometry) {
            if (geom) {
                window.UavFieldMap.setRuntimeGeometry(
                    geom,
                    runtime.state === "applied"
                );
            } else {
                // Clear stale competition geometry on idle/cancel/reset/sampling-before-preview
                window.UavFieldMap.setRuntimeGeometry(null, false);
            }
        }

        updateButtons();

    }

    // ── actions ────────────────────────────────────────────────────────
    async function _doPost(url, confirmMsg, body, onOk) {
        if (requestBusy) return;
        if (confirmMsg && !window.confirm(confirmMsg)) return;
        requestBusy = true;
        updateButtons();
        try {
            var r = await api.request(url, { method: "POST", body: body || "{}" });
            if (r && r.ok === false) {
                var msg = r.error || "unknown error";
                alert(msg);
            }
            if (onOk && r && r.ok === true) onOk(r);
            // trigger immediate re-fetch
            if (window.UavFieldRef && window.UavFieldRef.fetchFieldReferenceStatus) {
                window.UavFieldRef.fetchFieldReferenceStatus({ scheduleNext: false });
            }
        } catch (error) {
            alert(error && error.message ? error.message : String(error));
            if (window.UavFieldRef && window.UavFieldRef.fetchFieldReferenceStatus) {
                window.UavFieldRef.fetchFieldReferenceStatus({ scheduleNext: false });
            }
        } finally {
            requestBusy = false;
            updateButtons();
        }
    }

    function onStart() {
        var coords = parseInputLatLon();
        if (!coords) { alert("请先输入有效的 WGS84 坐标"); return; }
        _doPost(
            "/api/field-reference/runtime-sampling/start",
            "将使用当前飞机 WGS84 GPS 采样起点 A，\n并使用输入的远点 B 定义场地 +Y。\n请保持无人机静止；采样通过后会自动确认并冻结。\n该操作不会启动 Mission，不会发送飞控命令。",
            JSON.stringify({
                forward_marker_lat: coords.lat,
                forward_marker_lon: coords.lon
            }),
            null
        );
    }

    function onStartRtk() {
        _doPost(
            "/api/field-reference/rtk-heading-sampling/start",
            "将使用 RTK 双天线航向作为场地 +Y 方向。\n请确保飞机机头对准场地前方，然后开始采样。\n采样通过后会自动确认并冻结。\n该操作不会启动 Mission，不会发送飞控命令。",
            "{}",
            null
        );
    }

    function onCancel() {
        _doPost(
            "/api/field-reference/runtime-sampling/cancel",
            "取消当前 GPS 采样？\n该操作不会发送飞控命令。",
            null
        );
    }

    function onReset() {
        var confirmed = window.confirm(
            "将清除本次远点 B、GPS采样、Field Reference 和冻结状态。\n不会发送飞控命令。"
        );
        if (!confirmed) return;
        requestBusy = true;
        updateButtons();
        api.request("/api/field-reference/reset", { method: "POST", body: "{}" }).then(function (r) {
            if (r && r.ok === true) {
                var latEl = $("cfsForwardLat"), lonEl = $("cfsForwardLon");
                if (latEl) latEl.value = "";
                if (lonEl) lonEl.value = "";
            } else {
                var msg = (r && r.error) ? r.error : "Reset failed";
                alert(msg);
            }
            requestBusy = false;
            updateButtons();
            if (window.UavFieldRef && window.UavFieldRef.fetchFieldReferenceStatus) {
                window.UavFieldRef.fetchFieldReferenceStatus({ scheduleNext: false });
            }
        }).catch(function (e) {
            alert("Reset 网络异常: " + (e && e.message ? e.message : e));
            requestBusy = false;
            updateButtons();
        });
    }

    // ── init ───────────────────────────────────────────────────────────
    function init() {
        if (initCalled) return;
        initCalled = true;
        // Wire competition buttons
        var sb = $("cfsStart"); if (sb) sb.addEventListener("click", onStart);
        var rtkb = $("cfsStartRtk"); if (rtkb) rtkb.addEventListener("click", onStartRtk);
        var cb = $("cfsCancel"); if (cb) cb.addEventListener("click", onCancel);
        var rb = $("cfsReset"); if (rb) rb.addEventListener("click", onReset);

        // Input listeners for enable/disable
        var latEl = $("cfsForwardLat"), lonEl = $("cfsForwardLon");
        var inputHandler = function () { updateButtons(); };
        if (latEl) latEl.addEventListener("input", inputHandler);
        if (lonEl) lonEl.addEventListener("input", inputHandler);

        // Fetch template for fixed summary display
        fetchTemplateSummary();

        updateButtons();
    }

    return {
        init: init,
        onFieldReferenceStatus: onFieldReferenceStatus,
        getRuntimeUiState: function () {
            return { requestBusy: requestBusy };
        },
        updateRuntimeControls: updateButtons
    };
})();
