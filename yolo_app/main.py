from __future__ import annotations

import os
import sys
import time
from collections import deque

# Some conda-packaged OpenCV builds use the Qt backend but do not bundle fonts.
# Point Qt to a common system font directory before importing cv2 to suppress warnings.
os.environ.setdefault("QT_QPA_FONTDIR", "/usr/share/fonts/truetype/dejavu")

import cv2

try:
    from .attitude_history import AttitudeHistory
    from .attitude_receiver import AttitudeReceiver
    from .annotator import Annotator
    from .command_receiver import CommandReceiver
    from .config import load_config
    from .mjpeg_stream import MjpegStream
    from .raw_frame_recorder import RawFrameRecorder
    from .target_manager import TargetManager, build_scene_detections
    from .tracker_runner import TrackerRunner
    from .udp_publisher import UdpPublisher
    from .utils import ensure_parent_dir
    from .video_source import VideoSource
    from .virtual_nadir import VirtualNadirRectifier, build_debug_comparison
except ImportError:
    from attitude_history import AttitudeHistory
    from attitude_receiver import AttitudeReceiver
    from annotator import Annotator
    from command_receiver import CommandReceiver
    from config import load_config
    from mjpeg_stream import MjpegStream
    from raw_frame_recorder import RawFrameRecorder
    from target_manager import TargetManager, build_scene_detections
    from tracker_runner import TrackerRunner
    from udp_publisher import UdpPublisher
    from utils import ensure_parent_dir
    from video_source import VideoSource
    from virtual_nadir import VirtualNadirRectifier, build_debug_comparison


def build_video_writer(save_path: str, fps: float, width: int, height: int) -> cv2.VideoWriter:
    ensure_parent_dir(save_path)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    return cv2.VideoWriter(save_path, fourcc, fps if fps > 0 else 30.0, (width, height))


def main() -> int:
    cfg = load_config()
    attitude_history = None
    attitude_receiver = None
    rectifier = None
    if cfg.virtual_nadir.enabled:
        attitude_cfg = cfg.virtual_nadir.attitude
        attitude_history = AttitudeHistory(
            max_samples=attitude_cfg.max_samples,
            history_ms=attitude_cfg.history_ms,
        )
        attitude_receiver = AttitudeReceiver(
            attitude_cfg.ip,
            attitude_cfg.port,
            attitude_history,
            expected_source=attitude_cfg.source,
        )
        rectifier = VirtualNadirRectifier(cfg.virtual_nadir)
        attitude_receiver.start()
        calibration_label = (
            "approximate_calibration"
            if cfg.virtual_nadir.camera.approximate_calibration
            else "calibrated"
        )
        print(
            f"virtual_nadir enabled yaw_reference_mode={cfg.virtual_nadir.yaw_reference_mode} "
            f"calibration={calibration_label}",
            flush=True,
        )
    video_source = VideoSource(
        cfg.source,
        camera_width=cfg.camera_width,
        camera_height=cfg.camera_height,
        camera_fps=cfg.camera_fps,
        camera_fourcc=cfg.camera_fourcc,
        latest_frame=cfg.latest_frame,
    )
    tracker = TrackerRunner(cfg)
    target_manager = TargetManager(cfg)
    udp_publisher = UdpPublisher(cfg.udp_ip, cfg.udp_port,
        max_datagram_bytes=cfg.max_datagram_bytes, max_detections=cfg.max_detections)
    command_receiver = CommandReceiver(cfg.command_ip, cfg.command_port, enabled=cfg.command_enabled,
                                       process_session_id=udp_publisher.process_session_id)
    annotator = Annotator(cfg)
    raw_recorder = RawFrameRecorder(cfg.recording_dir, cfg.camera_fps)
    writer = None
    web_stream = (
        MjpegStream(
            cfg.web_stream_host,
            cfg.web_stream_port,
            cfg.web_stream_jpeg_quality,
            cfg.web_stream_max_fps,
            cfg.web_stream_width,
            cfg.web_stream_height,
        )
        if cfg.web_stream_enabled
        else None
    )
    frame_count = 0
    start_time = time.perf_counter()

    if cfg.show:
        cv2.namedWindow(cfg.window_name, cv2.WINDOW_NORMAL)
        if cfg.fullscreen:
            cv2.setWindowProperty(cfg.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    if web_stream is not None:
        web_stream.start()

    try:
        pending_frames = deque()
        source_finished = False
        while pending_frames or not source_finished:
            while not source_finished and len(pending_frames) < cfg.inference_workers:
                packet = video_source.read()
                if packet is None:
                    source_finished = True
                    break

                raw_frame = packet.frame
                frame = raw_frame
                rectification = None
                attitude = None
                if rectifier is not None and attitude_history is not None:
                    attitude_cfg = cfg.virtual_nadir.attitude
                    attitude = attitude_history.lookup(
                        packet.captured_at_monotonic_ns,
                        max_sample_distance_ms=attitude_cfg.max_sample_distance_ms,
                        max_bracket_span_ms=attitude_cfg.max_bracket_span_ms,
                        future_wait_ms=attitude_cfg.future_wait_ms,
                        min_rate_hz=attitude_cfg.min_rate_hz,
                        rate_window_ms=attitude_cfg.rate_window_ms,
                        min_rate_samples=attitude_cfg.min_rate_samples,
                    )
                    rectification = rectifier.rectify(raw_frame, attitude)
                    frame = rectification.frame

                invalid = rectification is not None and not rectification.valid
                if invalid:
                    # A failed attitude match is an immediate perception fence.
                    # Pending older detections may finish on the NPU but can no
                    # longer reach the tracker or resurrect a target lock.
                    for pending in pending_frames:
                        ticket = pending[4]
                        if ticket is not None:
                            tracker.cancel(ticket)
                    pending_frames.clear()
                    tracker.reset()
                    target_manager.invalidate()
                    ticket = None
                else:
                    valid_mask = None if rectification is None else rectification.valid_mask
                    ticket = tracker.submit(frame, valid_mask=valid_mask)
                pending_frames.append((packet, raw_frame, frame, rectification, ticket, attitude))
                if invalid:
                    break

            if not pending_frames:
                continue
            packet, raw_frame, frame, rectification, ticket, attitude = pending_frames.popleft()
            image_height, image_width = frame.shape[:2]

            if ticket is None:
                tracker.reset()
                target_manager.invalidate()
                tracks = []
                inference_metrics = {"preprocess": 0.0, "npu": 0.0, "postprocess": 0.0}
            else:
                tracks = tracker.complete(ticket)
                inference_metrics = tracker.last_metrics_ms
            frame_count += 1
            fps = frame_count / max(time.perf_counter() - start_time, 1e-9)
            # Producer and consumer run on the same board.  Use the monotonic
            # capture timestamp so NTP wall-clock corrections cannot turn into
            # fake video latency.
            frame_age_ms = max(
                0.0,
                (time.monotonic_ns() - packet.captured_at_monotonic_ns) / 1_000_000.0,
            )
            commands = command_receiver.poll()
            for command in commands:
                if command_receiver.is_expired(command):
                    recorder_status = raw_recorder.status()
                    command_receiver.complete(command, applied=False,
                        result_state="EXPIRED", reason_code="command_expired",
                        locked_track_id=getattr(target_manager, "locked_track_id", None),
                        recording_state=recorder_status.state,
                        recorder_boot_id=recorder_status.recorder_boot_id,
                        recorder_session_id=recorder_status.recorder_session_id,
                        actual_path=recorder_status.path or None, frames=recorder_status.frames,
                        error=recorder_status.error or None)
                    continue
                applied = True
                reset_reason = None
                if command.action == "reset_virtual_nadir":
                    if rectifier is None or attitude is None:
                        applied = False
                        reset_reason = "virtual_nadir_unavailable"
                    elif not rectifier.reset_yaw_reference(attitude):
                        applied = False
                        reset_reason = "virtual_nadir_attitude_invalid"
                    else:
                        # Old-reference detections must never appear after a
                        # successful reset.  Only a newly rectified frame may
                        # establish a subsequent target lock.
                        for pending in pending_frames:
                            pending_ticket = pending[4]
                            if pending_ticket is not None:
                                tracker.cancel(pending_ticket)
                        pending_frames.clear()
                        tracker.reset()
                        target_manager.invalidate()
                        tracks = []
                        inference_metrics = {"preprocess": 0.0, "npu": 0.0, "postprocess": 0.0}
                        reset_reason = "virtual_nadir_reset"
                elif command.action in {"recording_start", "recording_stop"}:
                    raw_recorder.handle_command(command.action, raw_frame.shape)
                else:
                    applied = target_manager.apply_command(command, tracks)
                recorder_status = raw_recorder.status()
                applied = applied and not bool(recorder_status.error)
                command_receiver.complete(
                    command,
                    applied=applied,
                    locked_track_id=getattr(target_manager, "locked_track_id", None),
                    recording_state=recorder_status.state,
                    recorder_boot_id=recorder_status.recorder_boot_id,
                    recorder_session_id=recorder_status.recorder_session_id,
                    actual_path=recorder_status.path or None,
                    frames=recorder_status.frames,
                    error=recorder_status.error or None,
                    reason_code=reset_reason,
                )

            current_target = target_manager.update(
                tracks=tracks,
                image_width=image_width,
                image_height=image_height,
                frame_id=packet.frame_id,
                timestamp=packet.timestamp,
            )
            scene = build_scene_detections(
                tracks=tracks,
                image_width=image_width,
                image_height=image_height,
                frame_id=packet.frame_id,
                timestamp=packet.timestamp,
            )
            udp_publisher.publish(current_target, scene, raw_recorder.status(),
                                  packet.captured_at_monotonic_ns)
            raw_recorder.write(raw_frame)

            if cfg.show or cfg.save_video or web_stream is not None:
                annotated = annotator.annotate(
                    frame=frame,
                    tracks=tracks,
                    current_target=current_target,
                    locked_track_id=target_manager.locked_track_id,
                    fps=fps,
                    frame_age_ms=frame_age_ms,
                    wait_frame_ms=packet.wait_frame_ms,
                    source_read_ms=packet.source_read_ms,
                    npu_ms=inference_metrics["npu"],
                )
                local_view = (
                    build_debug_comparison(raw_frame, rectification)
                    if rectification is not None and cfg.virtual_nadir.debug_compare
                    else annotated
                )
                if cfg.show:
                    cv2.imshow(cfg.window_name, local_view)
                    key = cv2.waitKey(1) & 0xFF
                    if key in {27, ord("q")}:
                        break
                if web_stream is not None:
                    # Web UI always shows the annotated YOLO input domain:
                    # stabilized when Virtual Nadir is enabled, raw otherwise.
                    web_stream.publish(annotated)
                if cfg.save_video:
                    if writer is None:
                        fps = video_source.cap.get(cv2.CAP_PROP_FPS)
                        writer = build_video_writer(cfg.save_path, fps, image_width, image_height)
                    writer.write(annotated)
            if frame_count == 1 or frame_count % 60 == 0:
                web_metrics = (
                    web_stream.encoder_metrics
                    if web_stream is not None
                    else {"encode_ms": 0.0, "replaced": 0}
                )
                rectify_status = (
                    ""
                    if rectification is None
                    else (
                        f" rectify_valid={rectification.valid} rectify_reason={rectification.reason} "
                        f"attitude_match_ms={rectification.attitude_match_ms} "
                        f"attitude_rate_hz={rectification.attitude_rate_hz} "
                        f"rectify_ms={rectification.rectify_ms:.1f}"
                    )
                )
                print(
                    f"frame={frame_count} fps={fps:.1f} wait_frame_ms={packet.wait_frame_ms:.1f} "
                    f"source_read_decode_ms={packet.source_read_ms:.1f} "
                    f"preprocess_ms={inference_metrics['preprocess']:.1f} "
                    f"npu_ms={inference_metrics['npu']:.1f} "
                    f"postprocess_ms={inference_metrics['postprocess']:.1f} "
                    f"web_encode_ms={float(web_metrics['encode_ms']):.1f} "
                    f"web_replaced={int(web_metrics['replaced'])} "
                    f"frame_age_ms={frame_age_ms:.1f} tracks={len(tracks)}{rectify_status}",
                    flush=True,
                )
    except KeyboardInterrupt:
        pass
    finally:
        video_source.release()
        tracker.release()
        udp_publisher.close()
        command_receiver.close()
        if attitude_receiver is not None:
            attitude_receiver.close()
        if web_stream is not None:
            web_stream.close()
        if writer is not None:
            writer.release()
        raw_recorder.close()
        cv2.destroyAllWindows()

    elapsed = max(time.perf_counter() - start_time, 1e-9)
    print(f"finished frames={frame_count} average_fps={frame_count / elapsed:.1f}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
