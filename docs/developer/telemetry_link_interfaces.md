# Telemetry Link Public API Reference

本文面向维护飞控通信与命令派发接口的开发者。

> **Status:** T2 document — maps the current code, no behavioural change.

## Principle

`telemetry_link` is the **flight-controller egress layer**.  
App-layer code (actions, missions, web UI) must **not** call pymavlink directly.  
All vehicle commands flow through `LinkManager`.

Actions and Missions return typed effects to `ActionRuntimeService` and
`ActionDispatcher`; they **must not call `LinkManager` directly**. The method
tables below document the transport interfaces used by dispatchers/adapters,
not an alternative way for Actions to bypass SEND authorization or safety.

`telemetry_link` carries **no mission policy** — no waypoint strategy, no
search pattern, no payload-drop sequencing.  Those live in the app layer.

---

## Public API tiers

### 1. Interfaces used by the current Action dispatcher

These are transport entry-points behind the formal dispatch and safety gates.

| Method | Semantics | Notes |
|---|---|---|
| `set_mode(mode, priority=5)` | Change flight mode | |
| `arm(priority=1)` | Arm motors | |
| `takeoff(altitude_m, priority=2)` | Take off to altitude | |
| `land(priority=2)` | Land | |
| `global_goto(lat, lon, alt, frame, priority=4, yaw_rad=None, field_reference_version=None)` | GPS/GLOBAL waypoint | Current `goto_waypoint` output; FIELD targets are resolved to GPS before dispatch |
| `condition_yaw(yaw_deg, yaw_speed_deg_s=20.0, direction=0, relative=False, priority=4)` | Yaw condition | Degrees; maps to `MAV_CMD_CONDITION_YAW` |
| `change_speed(speed_mps, speed_type=1, priority=4)` | Stage speed override | Maps to `MAV_CMD_DO_CHANGE_SPEED` |
| **Semantic wrappers (preferred)** | | |
| `send_body_velocity(vx_forward_mps, vy_right_mps, vz_down_mps, yaw_rad=None, yaw_rate_rad_s=None)` | Velocity, `BODY_NED` frame | Use over raw `send_velocity_command` |
| `set_servo_output_pwm(servo_output, pwm, priority=3)` | SERVO output PWM | Maps to `MAV_CMD_DO_SET_SERVO` |
| **Legacy (kept for compat / tests)** | | |
| `send_velocity_command(vx, vy, vz, frame=1, yaw_rad=None, yaw_rate_rad_s=None)` | Raw velocity | Prefer `send_body_velocity`; default frame is LOCAL_NED |
| `set_servo(channel, pwm, priority=3)` | Raw servo | Prefer `set_servo_output_pwm` |
| **Stop** | | |
| `stop_control(frame=1)` | All-zero velocity stop | `ControlType.STOP` |
| `stop_body_velocity()` | All-zero BODY_NED velocity stop | Explicit BODY_NED wrapper |
| `stop_body_velocity_and_clear()` | BODY_NED stop and continuous queue cleanup | Avoids stale continuous command reuse |

### 2. Advanced / debug interfaces

The interfaces below remain available at the transport layer for adapters, service plumbing
and tests. Their existence does not authorize a new direct Action/Mission
call path; Web UI remains the formal human-operation entry.

| Method | Semantics |
|---|---|
| `disarm(priority=1)` | Disarm motors; not a current ActionDispatcher request type |
| `goto_local_ned(x_north_m, y_east_m, z_down_m, yaw_rad=None, priority=4)` | LOCAL_NED position wrapper; current `goto_waypoint` does not use it |
| `local_position(x, y, z, frame, yaw=None, priority=4)` | Raw position; prefer the semantic wrapper for LOCAL_NED |
| `send_gimbal_angle(pitch, yaw, roll=0.0, mount_mode=None, priority=5)` | Absolute angle; pitch/yaw in degrees |
| `send_gimbal_rate(yaw_rate, pitch_rate, yaw_lock=False, gimbal_device_id=0)` | Gimbal rate control |
| `send_yaw_rate_command(yaw_rate, frame=1)` | Yaw-only rate |

The old terminal-only Home/ROI/reposition/relay and public message-interval
facades have been deleted. They had no production consumers and no typed broker
payload. Startup telemetry subscription remains a private runtime operation.

### 3. Restricted interfaces

**Do not call these from Action or Mission code.**  
Queue submission methods exist for internal adapter plumbing and tests.
The retired `release_payload` API has been deleted; no payload mapping fallback exists.

| Method | Restriction reason |
|---|---|
| `submit_action_command(command)` | Raw queue injection — bypasses all wrappers. |
| `submit_control_command(command)` | Raw queue injection — bypasses all wrappers. |

---

## Payload release canonical path

### Correct (main path)

```
PayloadReleaseAction
  -> set_servo action  (action_type="set_servo")
  -> ActionRuntimeService
  -> ActionDispatcher._dispatch_set_servo()
     (after SEND/run authorization and ActionSafetyPipeline approval)
  -> VehicleCommandPort adapter
  -> LinkManager.set_servo_output_pwm() / set_servo()
  -> SetServo typed command / VehicleCommandEnvelope
  -> CommandBroker / CommandBrokerWorker
  -> MavlinkEnvelopeWriter -> MavlinkCommandEncoder._send_set_servo()
  -> MAV_CMD_DO_SET_SERVO
```

### Forbidden

| Path | Why |
|---|---|
| `release_payload()` | Deleted; only the authorized `payload_release` Action -> `set_servo` path exists. |
| RC override | Not supported in this architecture. |
| Direct `pymavlink` call | Bypasses `LinkManager` queue, safety gate, and audit. |

---

## Naming conventions

| Term | Meaning |
|---|---|
| `servo_output` | Flight-controller **SERVO output** number (1‒16), **not** an RC input channel. |
| `channel` (in `set_servo`) | Same as `servo_output` — legacy parameter name. |
| `pwm` | PWM pulse width in **microseconds** (µs), typically 500‒2500. |
| `yaw_rad` | Yaw angle in **radians**. |
| `pitch` / `yaw` in gimbal APIs | **Degrees** — per the current interface contract. |
| `altitude_m` | Altitude in metres, positive **up**. |
| `z_down_m` | Z in LOCAL_NED/BODY_NED, positive **down**. |

---

## Frame constants

The dependency-free contract source is `contracts.frames`; `telemetry_link.frames`
is a compatibility re-export. Action code should import the contract directly,
and must never hardcode integers.

```python
from contracts.frames import LOCAL_NED, BODY_NED, GLOBAL_RELATIVE_ALT_INT
```

See [`coordinate_frames.md`](coordinate_frames.md) for the full specification.
