"""Tests for Antigravity high-threshold silent watchdog and HTTP endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from ai_net_guard.antigravity_watchdog import AntigravityWatchdog
from ai_net_guard.config import AppConfig
from ai_net_guard.diagnostics import (
    DiagnosticReport,
    ExitCheck,
    Finding,
    ProbeResult,
    ProxyState,
)
from ai_net_guard.mihomo import MihomoStatus
from ai_net_guard.monitor import NetworkMonitor
from ai_net_guard.server import create_app

RETRY_EOF_LINE = (
    "ERROR: logging before google.Init: I1123 18:23:07.690470  617488 run.go:316] "
    "Run: attempt 2 failed (stream reading error: unexpected EOF), retrying in 5.09s\n"
)
TERMINAL_EOF_LINE = (
    "ERROR: logging before google.Init: E1123 18:09:53.546349  573762 errorreport.go:307] "
    "agent executor error: generating and executing: stream reading error: unexpected EOF\n"
)


def _healthy_report() -> DiagnosticReport:
    return DiagnosticReport(
        timestamp="2026-11-23 19:50:00",
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
        cards={},
        findings=[Finding("ok", "健康", "一切正常", "无需操作")],
    )


def test_seek_to_end_ignores_historical_eofs(tmp_path: Path) -> None:
    log_file = tmp_path / "language_server.log"
    log_file.write_text(RETRY_EOF_LINE * 6 + TERMINAL_EOF_LINE, encoding="utf-8")

    wd = AntigravityWatchdog(log_path=log_file, seek_to_end=True)
    res = wd.poll(now_mono=100.0)
    assert res["new_eofs"] == 0
    assert res["should_heal"] is False
    assert res["recent_eof_count"] == 0
    assert res["status"]["status"] == "healthy"


def test_threshold_and_terminal_and_cooldown(tmp_path: Path) -> None:
    log_file = tmp_path / "language_server.log"
    log_file.write_text("", encoding="utf-8")

    wd = AntigravityWatchdog(
        log_path=log_file,
        eof_threshold=4,
        window_sec=420,
        cooldown_sec=300,
        seek_to_end=False,
    )

    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(RETRY_EOF_LINE * 3)

    res1 = wd.poll(now_mono=100.0)
    assert res1["new_eofs"] == 3
    assert res1["recent_eof_count"] == 3
    assert res1["should_heal"] is False
    assert res1["status"]["status"] == "watching"

    # 4th EOF within window crosses threshold
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(RETRY_EOF_LINE)

    res2 = wd.poll(now_mono=150.0)
    assert res2["new_eofs"] == 1
    assert res2["recent_eof_count"] == 4
    assert res2["should_heal"] is True
    assert res2["status"]["status"] == "stalled"

    # Enter cooldown after heal attempt
    wd.mark_healed(ok=True, message="已切换", selected_node="日本 JP-02", now_mono=150.0)
    assert wd.total_heals == 1
    assert wd.status_dict(now_mono=200.0)["cooldown_remaining_sec"] == 250

    # Even terminal EOF should be debounced during cooldown
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(TERMINAL_EOF_LINE)

    res3 = wd.poll(now_mono=200.0)
    assert res3["in_cooldown"] is True
    assert res3["should_heal"] is False

    # After cooldown expires, terminal error triggers immediately
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(TERMINAL_EOF_LINE)

    res4 = wd.poll(now_mono=460.0)
    assert res4["in_cooldown"] is False
    assert res4["saw_terminal_error"] is True
    assert res4["should_heal"] is True


def test_log_truncation_and_rotation_reset(tmp_path: Path) -> None:
    log_file = tmp_path / "language_server.log"
    log_file.write_text("info line 1\ninfo line 2\n" + RETRY_EOF_LINE * 2, encoding="utf-8")

    wd = AntigravityWatchdog(log_path=log_file, eof_threshold=4, seek_to_end=True)
    assert wd.poll(now_mono=10.0)["new_eofs"] == 0

    # Simulate truncation / rotation with smaller content containing new EOFs
    rotated = tmp_path / "language_server.log.new"
    rotated.write_text(TERMINAL_EOF_LINE, encoding="utf-8")
    rotated.replace(log_file)

    res = wd.poll(now_mono=20.0)
    assert res["new_eofs"] == 1
    assert res["saw_terminal_error"] is True
    assert res["should_heal"] is True


def test_silent_heal_does_not_notify_and_api_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_NET_GUARD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AI_NET_GUARD_STATE_DIR", str(tmp_path / "state"))

    log_file = tmp_path / "language_server.log"
    log_file.write_text("", encoding="utf-8")

    cfg = AppConfig(ag_log_path=str(log_file), ag_silent_heal=True, ag_eof_threshold=4)
    fake_mihomo = MihomoStatus(
        connected=True,
        api_url="http://127.0.0.1:9097",
        active_group="🤖 AI-稳定池",
        current_node="日本 JP-01",
        group_nodes=["日本 JP-01", "新加坡 SG-02"],
    )

    with (
        patch("ai_net_guard.monitor.run_full_diagnostics", AsyncMock(return_value=_healthy_report())),
        patch("ai_net_guard.mihomo.MihomoClient.get_status", AsyncMock(return_value=fake_mihomo)),
        patch(
            "ai_net_guard.mihomo.MihomoClient.test_and_select_best",
            AsyncMock(return_value=(True, "已切换（测速 110 ms）", "新加坡 SG-02")),
        ) as mock_select,
        patch("ai_net_guard.monitor.send_macos_notification") as mock_notify,
    ):
        # 1) Monitor check_once silently heals on 4 EOFs without desktop notification
        monitor = NetworkMonitor(config=cfg)
        log_file.write_text(RETRY_EOF_LINE * 4, encoding="utf-8")
        asyncio.run(monitor.check_once(trigger_auto_heal=True))

        mock_select.assert_awaited_once()
        mock_notify.assert_not_called()
        assert monitor.ag_watchdog.total_heals == 1

        # 2) FastAPI endpoints GET /api/antigravity & POST /api/antigravity/recover & panel in /
        app = create_app(config=cfg, start_monitor=False)
        with TestClient(app) as tc:
            home = tc.get("/")
            assert home.status_code == 200
            assert "ag-watchdog-panel" in home.text

            wd_resp = tc.get("/api/antigravity")
            assert wd_resp.status_code == 200
            wd_json = wd_resp.json()
            assert wd_json["enabled"] is True
            assert wd_json["threshold"] == 4
            assert wd_json["silent_heal"] is True

            # Empty body POST works (for simple curl scripts)
            rec1 = tc.post("/api/antigravity/recover")
            assert rec1.status_code == 200
            assert rec1.json()["result"]["ok"] is True
            assert rec1.json()["result"]["selected_node"] == "新加坡 SG-02"

            # JSON body POST works
            rec2 = tc.post("/api/antigravity/recover", json={"reason": "manual-test"})
            assert rec2.status_code == 200
            assert rec2.json()["result"]["reason"] == "manual-test"
            mock_notify.assert_not_called()
