from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import httpx

from web_ui.server import create_app
from application.web_services import WebServices


class _Runner:
    def __init__(self) -> None:
        self.action_runtime = SimpleNamespace(dispatcher=SimpleNamespace(authorization=None))
        self.calls: list[tuple[object, ...]] = []

    def web_status_snapshot(self):
        return {"ok": True}

    def active_telemetry_source(self):
        return "sitl"

    def set_send(self, enabled: bool):
        self.calls.append(("send", enabled))
        return SimpleNamespace(ok=True, message="ok")


def _config(tmp_path):
    return SimpleNamespace(
        web_host="127.0.0.1",
        audit_log_path=str(tmp_path / "audit.jsonl"),
        security_event_log_path=str(tmp_path / "security.jsonl"),
        auth_required=True,
        credential_env="UAV_WEB_OPERATOR_PASSWORD",
        credential_file_env="UAV_WEB_OPERATOR_PASSWORD_FILE",
        session_ttl_sec=60,
        allowed_hosts=("testserver",),
        allowed_origins=("http://testserver",),
    )


def _services(runner):
    unavailable = lambda *args: {"ok": False}
    return WebServices(
        system_control=runner,
        mission_control=runner,
        status_snapshot=runner.web_status_snapshot,
        field_reference_status=unavailable,
        field_reference_reset=unavailable,
        field_reference_freeze=unavailable,
        field_profile_list=unavailable,
        field_profile_get=unavailable,
        field_profile_validate=unavailable,
        runtime_sampling_start=unavailable,
        runtime_sampling_finalize=unavailable,
        runtime_sampling_cancel=unavailable,
        competition_sampling_start=unavailable,
        clear_localization=unavailable,
        action_specs=(),
        action_lab_enabled=False,
        authorization_snapshot=lambda: None,
    )


def _scenario(app):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            unauthenticated = await client.post("/api/control/send", json={"enabled": True})
            login = await client.post(
                "/api/auth/login",
                headers={"origin": "http://testserver"},
                json={"password": "test-only-operator-password"},
            )
            csrf = login.json()["csrf_token"]
            no_csrf = await client.post("/api/control/send", json={"enabled": True})
            allowed = await client.post(
                "/api/control/send",
                headers={"origin": "http://testserver", "x-uav-csrf": csrf},
                json={"enabled": True},
            )
            cross_origin = await client.post(
                "/api/control/send",
                headers={"origin": "http://attacker.invalid", "x-uav-csrf": csrf},
                json={"enabled": False},
            )
            return unauthenticated, login, no_csrf, allowed, cross_origin
    return asyncio.run(run())


def test_modifying_requests_require_auth_csrf_and_allowed_origin(tmp_path) -> None:
    runner = _Runner()
    app = create_app(_services(runner), _config(tmp_path))

    unauthenticated, login, no_csrf, allowed, cross_origin = _scenario(app)

    assert unauthenticated.status_code == 401
    assert login.status_code == 200
    assert no_csrf.status_code == 403
    assert allowed.status_code == 200
    assert cross_origin.status_code == 403
    assert runner.calls == [("send", True)]
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if (tmp_path / "audit.jsonl").exists() and (tmp_path / "security.jsonl").exists():
            break
        time.sleep(0.01)
    assert (tmp_path / "audit.jsonl").exists()
    assert (tmp_path / "security.jsonl").exists()


def test_trusted_network_allows_host_and_same_port_origin(tmp_path) -> None:
    config = _config(tmp_path)
    config.web_port = 8080
    config.allowed_networks = ("10.101.31.0/24",)
    app = create_app(_services(_Runner()), config)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://10.101.31.109:8080") as client:
            return await client.get("/api/status", headers={"origin": "http://10.101.31.109:8080"})

    assert asyncio.run(run()).status_code == 200


def test_all_ip_networks_allow_tailscale_ipv4_and_ipv6_hosts(tmp_path) -> None:
    config = _config(tmp_path)
    config.web_port = 8080
    config.allowed_networks = ("0.0.0.0/0", "::/0")
    app = create_app(_services(_Runner()), config)

    async def request(base_url: str) -> int:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
            return (await client.get("/api/status", headers={"origin": base_url})).status_code

    assert asyncio.run(request("http://100.107.97.24:8080")) == 200
    assert asyncio.run(request("http://[fd7a:115c:a1e0::3036:6119]:8080")) == 200


def test_current_private_interface_network_can_be_auto_allowed(tmp_path, monkeypatch) -> None:
    import ipaddress
    import web_ui.security as security_module

    monkeypatch.setattr(
        security_module,
        "_current_private_interface_networks",
        lambda: (ipaddress.ip_network("192.168.50.0/24"),),
    )
    config = _config(tmp_path)
    config.web_port = 8080
    config.auto_allow_current_private_network = True
    app = create_app(_services(_Runner()), config)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://192.168.50.99:8080") as client:
            return await client.get("/api/status", headers={"origin": "http://192.168.50.99:8080"})

    assert asyncio.run(run()).status_code == 200
