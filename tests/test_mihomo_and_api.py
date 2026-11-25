"""Tests for Mihomo node selection and FastAPI endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from ai_net_guard.config import AppConfig
from ai_net_guard.diagnostics import (
    DiagnosticReport,
    ExitCheck,
    Finding,
    ProbeResult,
    ProxyState,
)
from ai_net_guard.mihomo import (
    MihomoClient,
    MihomoStatus,
    classify_node,
    filter_clean_ai_nodes,
)
from ai_net_guard.server import create_app


def test_filter_clean_ai_nodes() -> None:
    raw = [
        "剩余流量：88GB",
        "官网地址：example.com",
        "香港 HK-01",
        "Hong Kong 专线",
        "DIRECT",
        "新加坡 SG-02",
        "日本 Tokyo-01",
        "冷门节点-01",
        "美国 US-LosAngeles",
    ]
    cleaned = filter_clean_ai_nodes(raw)
    assert "剩余流量：88GB" not in cleaned
    assert "官网地址：example.com" not in cleaned
    assert "香港 HK-01" not in cleaned
    assert "Hong Kong 专线" not in cleaned
    assert "DIRECT" not in cleaned
    assert set(cleaned) == {"日本 Tokyo-01", "新加坡 SG-02", "美国 US-LosAngeles", "冷门节点-01"}
    # Preferred regions sorted before non-preferred
    assert cleaned.index("日本 Tokyo-01") < cleaned.index("冷门节点-01")


def test_mihomo_test_and_select_best() -> None:
    client = MihomoClient(api_url="http://127.0.0.1:9097")
    fake_status = MihomoStatus(
        connected=True,
        api_url="http://127.0.0.1:9097",
        version="v1.19.0",
        active_group="🤖 AI-稳定池",
        current_node="日本 Tokyo-01",
        all_groups=["🤖 AI-稳定池"],
        group_nodes=["香港 HK-01", "日本 Tokyo-01", "新加坡 SG-02", "美国 US-03"],
    )

    async def fake_delay(node: str, **kwargs):
        return {"新加坡 SG-02": 180, "美国 US-03": 420}.get(node)

    with (
        patch.object(client, "get_status", AsyncMock(return_value=fake_status)),
        patch.object(client, "test_node_delay", side_effect=fake_delay),
        patch.object(client, "switch_node", AsyncMock(return_value=(True, "已切换"))) as mock_switch,
    ):
        ok, _msg, chosen = asyncio.run(client.test_and_select_best())

    assert ok is True
    assert chosen == "新加坡 SG-02"
    mock_switch.assert_awaited_once_with("🤖 AI-稳定池", "新加坡 SG-02")


def test_fastapi_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_NET_GUARD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AI_NET_GUARD_STATE_DIR", str(tmp_path / "state"))

    fake_report = DiagnosticReport(
        timestamp="2026-11-23 18:00:00",
        overall_status="healthy",
        summary="四层网络健康",
        proxy_state=ProxyState(effective_proxy_url="http://127.0.0.1:7897", port_listening=True),
        exit_check=ExitCheck(reachable=True, ip_1="1.1.1.1", ip_2="1.1.1.1", loc="JP", colo="NRT"),
        probes=[
            ProbeResult("deepseek_direct", "DeepSeek", "direct", "https://api.deepseek.com", True, "ok", 401, 200, "正常"),
            ProbeResult("google_204", "Google", "proxy", "https://www.google.com/generate_204", True, "ok", 204, 300, "正常"),
            ProbeResult("antigravity", "Antigravity", "proxy", "https://antigravity.google", True, "ok", 200, 350, "正常"),
            ProbeResult("gemini_api", "Gemini", "proxy", "https://generativelanguage.googleapis.com", True, "ok", 404, 310, "正常"),
        ],
        cards={
            "domestic_ai": {"status": "ok", "headline": "ok", "subtext": "ok"},
            "proxy_layer": {"status": "ok", "headline": "ok", "subtext": "ok"},
            "exit_stability": {"status": "ok", "headline": "ok", "subtext": "ok"},
            "overseas_ai": {"status": "ok", "headline": "ok", "subtext": "ok"},
        },
        findings=[Finding("ok", "健康", "一切正常", "无需操作")],
    )

    with (
        patch("ai_net_guard.monitor.run_full_diagnostics", AsyncMock(return_value=fake_report)),
        patch(
            "ai_net_guard.mihomo.MihomoClient.get_status",
            AsyncMock(return_value=MihomoStatus(connected=False, error="mocked offline")),
        ),
    ):
        app = create_app(config=AppConfig(), start_monitor=False)
        with TestClient(app) as tc:
            home = tc.get("/")
            assert home.status_code == 200
            assert "AI NetGuard" in home.text

            status_resp = tc.get("/api/status")
            assert status_resp.status_code == 200
            assert status_resp.json()["report"]["overall_status"] == "healthy"

            check_resp = tc.post("/api/check")
            assert check_resp.status_code == 200
            assert len(check_resp.json()["history"]) >= 1

            cfg_resp = tc.post("/api/config", json={"auto_heal": False})
            assert cfg_resp.status_code == 200
            assert cfg_resp.json()["config"]["auto_heal"] is False

            ov_resp = tc.get("/api/override")
            assert ov_resp.status_code == 200
            assert "AI-稳定池" in ov_resp.text

            # v2 endpoints: ip_history persisted, probe-one, custom sites, mihomo test-delays & connections
            assert len(status_resp.json()["ip_history"]) >= 1

            add_site = tc.post("/api/sites", json={"name": "HF", "url": "huggingface.co", "mode": "proxy"})
            assert add_site.status_code == 200
            assert len(add_site.json()["custom_sites"]) == 1

            with patch("ai_net_guard.monitor.probe_single_target", AsyncMock(return_value=fake_report.probes[0])):
                p_one = tc.post("/api/probe-one", json={"key": "deepseek_direct"})
                assert p_one.status_code == 200
                assert p_one.json()["result"]["ok"] is True

            del_site = tc.delete("/api/sites/0")
            assert del_site.status_code == 200
            assert del_site.json()["custom_sites"] == []

            with (
                patch("ai_net_guard.mihomo.MihomoClient.test_group_delays", AsyncMock(return_value={"日本 JP-01": 120})),
                patch(
                    "ai_net_guard.mihomo.MihomoClient.get_connections",
                    AsyncMock(return_value=[{"host": "antigravity.google", "process": "Antigravity", "rule": "DOMAIN-SUFFIX", "chains": ["🤖 AI-稳定池"]}]),
                ),
            ):
                td = tc.post("/api/mihomo/test-delays", json={"group": "🤖 AI-稳定池"})
                assert td.status_code == 200
                assert td.json()["delays"]["日本 JP-01"] == 120

                conns = tc.get("/api/mihomo/connections")
                assert conns.status_code == 200
                assert conns.json()["connections"][0]["host"] == "antigravity.google"


def test_classify_node_and_group_delays() -> None:
    assert classify_node("日本 Tokyo-01") == ("🇯🇵", "recommended")
    assert classify_node("香港 HK-02") == ("🇭🇰", "restricted")
    assert classify_node("剩余流量：50GB") == ("🌐", "info")
    assert classify_node("新加坡 SG-03") == ("🇸🇬", "recommended")

    client = MihomoClient(api_url="http://127.0.0.1:9097")
    fake_status = MihomoStatus(
        connected=True,
        api_url="http://127.0.0.1:9097",
        active_group="🤖 AI-稳定池",
        group_nodes=["剩余流量：50GB", "日本 Tokyo-01", "新加坡 SG-03"],
    )

    async def fake_delay(node: str, **kwargs):
        return {"日本 Tokyo-01": 150, "新加坡 SG-03": 210}.get(node)

    with (
        patch.object(client, "get_status", AsyncMock(return_value=fake_status)),
        patch.object(client, "test_node_delay", side_effect=fake_delay),
    ):
        delays = asyncio.run(client.test_group_delays("🤖 AI-稳定池"))

    assert "剩余流量：50GB" not in delays
    assert delays == {"日本 Tokyo-01": 150, "新加坡 SG-03": 210}


def test_mihomo_get_connections() -> None:
    client = MihomoClient(api_url="http://127.0.0.1:9097")

    class _FakeResp:
        status_code = 200

        def json(self):
            return {
                "connections": [
                    {
                        "metadata": {"host": "api.deepseek.com", "process": "curl"},
                        "rule": "DOMAIN-SUFFIX(deepseek.com)",
                        "chains": ["DIRECT"],
                        "upload": 128,
                        "download": 512,
                    }
                ]
            }

    with (
        patch.object(client, "resolve_api_url", AsyncMock(return_value="http://127.0.0.1:9097")),
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp())),
    ):
        conns = asyncio.run(client.get_connections())

    assert len(conns) == 1
    assert conns[0]["host"] == "api.deepseek.com"
    assert conns[0]["chains"] == ["DIRECT"]


def test_nested_selector_group_penetration_and_skip_meta() -> None:
    nested_proxies = {
        "🤖 AI": {
            "type": "Selector",
            "now": "天机 TIANJI",
            "all": ["AI 家宽", "天机 TIANJI", "AI 自动", "📍 指定节点"],
        },
        "AI 家宽": {
            "type": "Selector",
            "now": "🏠 家宽",
            "all": ["🏠 家宽"],
        },
        "🏠 家宽": {
            "type": "Selector",
            "now": "🇯🇵 东京家宽 · 01",
            "all": ["🇯🇵 东京家宽 · 01"],
        },
        "AI 自动": {
            "type": "Fallback",
            "now": "🇯🇵 东京 · 11",
            "all": ["🇯🇵 东京 · 11", "🇺🇸 洛杉矶 · 05"],
        },
        "天机 TIANJI": {
            "type": "Selector",
            "now": "📍 指定节点",
            "all": ["📍 指定节点", "⚡ 自动选择"],
        },
        "⚡ 自动选择": {
            "type": "URLTest",
            "now": "🇯🇵 东京 · 11",
            "all": ["🇯🇵 东京 · 11", "🇺🇸 洛杉矶 · 05"],
        },
        "📍 指定节点": {
            "type": "Selector",
            "now": "🇸🇬 新加坡 · 01",
            "all": ["🇸🇬 新加坡 · 01", "🇯🇵 东京 · 11", "🇺🇸 洛杉矶 · 05", "🇭🇰 香港家宽"],
        },
        "🇸🇬 新加坡 · 01": {"type": "URLTest", "now": "sg-node-1", "all": ["sg-node-1"]},
        "🇯🇵 东京 · 11": {"type": "URLTest", "now": "jp-node-1", "all": ["jp-node-1"]},
        "🇺🇸 洛杉矶 · 05": {"type": "URLTest", "now": "us-node-1", "all": ["us-node-1"]},
        "🇭🇰 香港家宽": {"type": "URLTest", "now": "hk-node-1", "all": ["hk-node-1"]},
    }

    client = MihomoClient(api_url="http://127.0.0.1:9097")

    async def fake_delay(node: str, **kwargs):
        return {"🇯🇵 东京 · 11": 410, "🇺🇸 洛杉矶 · 05": 240}.get(node)

    class _FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"proxies": nested_proxies}

    with (
        patch.object(client, "resolve_api_url", AsyncMock(return_value="http://127.0.0.1:9097")),
        patch.object(client, "probe_endpoint", AsyncMock(return_value="v1.19.0")),
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_FakeResp())),
    ):
        status = asyncio.run(client.get_status(preferred_group="🤖 AI-稳定池"))
        assert status.connected is True
        assert status.active_group == "📍 指定节点"
        assert status.current_node == "🇸🇬 新加坡 · 01"

        with (
            patch.object(client, "test_node_delay", side_effect=fake_delay),
            patch.object(client, "switch_node", AsyncMock(return_value=(True, "已切换"))) as mock_switch,
        ):
            ok, _msg, chosen = asyncio.run(client.test_and_select_best(preferred_group="🤖 AI-稳定池"))

    assert ok is True
    assert chosen == "🇺🇸 洛杉矶 · 05"
    mock_switch.assert_awaited_once_with("📍 指定节点", "🇺🇸 洛杉矶 · 05")
