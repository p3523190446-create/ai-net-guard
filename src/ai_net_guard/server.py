"""FastAPI dashboard server for AI NetGuard."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from .config import AppConfig, load_config
from .monitor import NetworkMonitor

STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_override_script_path() -> Path:
    local_candidate = PROJECT_ROOT / "clash-ai-override.js"
    if local_candidate.exists():
        return local_candidate
    return Path.home() / "Workspace" / "apps" / "ai-net-guard" / "clash-ai-override.js"


class ConfigUpdateRequest(BaseModel):
    proxy_url: str | None = None
    mihomo_api: str | None = None
    mihomo_secret: str | None = None
    mihomo_group: str | None = None
    auto_heal: bool | None = None
    check_interval_sec: int | None = None
    fail_threshold: int | None = None
    heal_cooldown_sec: int | None = None
    max_heals_per_hour: int | None = None
    max_heals_per_day: int | None = None
    ag_watchdog_enabled: bool | None = None
    ag_eof_threshold: int | None = None
    ag_eof_window_sec: int | None = None
    ag_heal_cooldown_sec: int | None = None
    ag_silent_heal: bool | None = None


class AntigravityRecoverRequest(BaseModel):
    reason: str = "Antigravity 请求自动切换恢复"


class SwitchNodeRequest(BaseModel):
    group: str
    node: str


class ProbeOneRequest(BaseModel):
    key: str


class CustomSiteRequest(BaseModel):
    name: str
    url: str
    mode: str = "proxy"


class TestDelaysRequest(BaseModel):
    group: str | None = None


def create_app(config: AppConfig | None = None, start_monitor: bool = True) -> FastAPI:
    monitor = NetworkMonitor(config=config or load_config())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_monitor:
            monitor.start_background()
        yield
        await monitor.stop_background()

    app = FastAPI(title="AI NetGuard", version="0.2.0", lifespan=lifespan)
    app.state.monitor = monitor

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    async def index() -> str:
        html_path = STATIC_DIR / "index.html"
        return html_path.read_text(encoding="utf-8")

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        if monitor.latest_report is None:
            await monitor.check_once(trigger_auto_heal=False)
        data = monitor.snapshot()
        data["override_path"] = str(get_override_script_path())
        return data

    @app.post("/api/check")
    async def api_check() -> dict[str, Any]:
        await monitor.check_once(trigger_auto_heal=False)
        data = monitor.snapshot()
        data["override_path"] = str(get_override_script_path())
        return data

    @app.post("/api/probe-one")
    async def api_probe_one(req: ProbeOneRequest) -> dict[str, Any]:
        res = await monitor.probe_one(req.key)
        return {"result": res, "snapshot": monitor.snapshot()}

    @app.post("/api/sites")
    async def api_add_site(req: CustomSiteRequest) -> dict[str, Any]:
        monitor.add_custom_site(req.name, req.url, req.mode)
        await monitor.check_once(trigger_auto_heal=False)
        return monitor.snapshot()

    @app.delete("/api/sites/{idx}")
    async def api_delete_site(idx: int) -> dict[str, Any]:
        monitor.remove_custom_site(idx)
        await monitor.check_once(trigger_auto_heal=False)
        return monitor.snapshot()

    @app.post("/api/heal")
    async def api_heal() -> dict[str, Any]:
        result = await monitor.heal_now()
        return {"result": result, "snapshot": monitor.snapshot()}

    @app.post("/api/mihomo/switch")
    async def api_switch(req: SwitchNodeRequest) -> dict[str, Any]:
        result = await monitor.switch_node(req.group, req.node)
        return {"result": result, "snapshot": monitor.snapshot()}

    @app.post("/api/mihomo/test-delays")
    async def api_test_delays(req: TestDelaysRequest) -> dict[str, Any]:
        return await monitor.test_group_delays(group=req.group)

    @app.get("/api/mihomo/connections")
    async def api_connections() -> dict[str, Any]:
        return await monitor.get_connections()

    @app.post("/api/config")
    async def api_config(req: ConfigUpdateRequest) -> dict[str, Any]:
        monitor.update_config(req.model_dump(exclude_none=True))
        return monitor.snapshot()

    @app.get("/api/antigravity")
    async def api_antigravity() -> dict[str, Any]:
        monitor.ag_watchdog.poll()
        return monitor.ag_watchdog.status_dict()

    @app.post("/api/antigravity/recover")
    async def api_antigravity_recover(
        req: Annotated[AntigravityRecoverRequest | None, Body()] = None,
    ) -> dict[str, Any]:
        reason = req.reason if req else "Antigravity 请求自动切换恢复"
        result = await monitor.trigger_antigravity_recover(reason=reason)
        return {"result": result, "snapshot": monitor.snapshot()}

    @app.get("/api/override", response_class=PlainTextResponse)
    async def api_override() -> str:
        path = get_override_script_path()
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "// clash-ai-override.js not found"

    return app
