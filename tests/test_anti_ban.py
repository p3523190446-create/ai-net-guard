"""Tests for anti-ban safety locks: slow-speed guard, domestic-down guard, quorum, cooldown, and caps."""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from ai_net_guard.config import AppConfig
from ai_net_guard.diagnostics import (
    DiagnosticReport,
    DomesticIpInfo,
    ExitCheck,
    Finding,
    ProbeResult,
    ProxyState,
)
from ai_net_guard.mihomo import MihomoStatus
from ai_net_guard.monitor import NetworkMonitor


def _make_report(
    *,
    deepseek_ok: bool = True,
    domestic_ok: bool = True,
    ag_ok: bool = True,
    ag_status: str = "ok",
    gemini_ok: bool = True,
    google_ok: bool = True,
    restricted: bool = False,
) -> DiagnosticReport:
    return DiagnosticReport(
        timestamp="2026-11-23 23:45:00",
        overall_status="healthy" if (ag_ok and gemini_ok and google_ok and ag_status == "ok") else "degraded",
        summary="test",
        proxy_state=ProxyState(effective_proxy_url="http://127.0.0.1:7897", port_listening=True),
        exit_check=ExitCheck(reachable=True, ip_1="1.2.3.4", ip_2="1.2.3.4", loc="HK" if restricted else "JP", restricted_region=restricted),
        probes=[
            ProbeResult("deepseek_direct", "DeepSeek", "direct", "https://api.deepseek.com", deepseek_ok, "ok" if deepseek_ok else "fail", 401 if deepseek_ok else 0, 200, ""),
            ProbeResult("antigravity", "Antigravity", "proxy", "https://antigravity.google", ag_ok, ag_status, 200 if ag_ok else 0, 4000 if ag_status == "warn" else 300, ""),
            ProbeResult("gemini_api", "Gemini", "proxy", "https://generativelanguage.googleapis.com", gemini_ok, "ok" if gemini_ok else "fail", 404 if gemini_ok else 0, 300, ""),
            ProbeResult("google_204", "Google", "proxy", "https://www.google.com/generate_204", google_ok, "ok" if google_ok else "fail", 204 if google_ok else 0, 300, ""),
        ],
        cards={},
        findings=[Finding("ok", "test", "test", "test")],
        domestic_ip=DomesticIpInfo(reachable=domestic_ok, ip="120.0.0.1" if domestic_ok else ""),
    )


def _fake_mihomo() -> MihomoStatus:
    return MihomoStatus(
        connected=True,
        api_url="http://127.0.0.1:9097",
        active_group="🤖 AI-稳定池",
        current_node="日本 JP-01",
        group_nodes=["日本 JP-01", "新加坡 SG-02"],
    )


def test_slow_speed_never_switches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_NET_GUARD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AI_NET_GUARD_STATE_DIR", str(tmp_path / "state"))

    slow_report = _make_report(ag_ok=True, ag_status="warn")
    cfg = AppConfig(fail_threshold=2, ag_log_path=str(tmp_path / "missing.log"))
    monitor = NetworkMonitor(config=cfg)

    with (
        patch("ai_net_guard.monitor.run_full_diagnostics", AsyncMock(return_value=slow_report)),
        patch("ai_net_guard.mihomo.MihomoClient.get_status", AsyncMock(return_value=_fake_mihomo())),
        patch("ai_net_guard.mihomo.MihomoClient.test_and_select_best", AsyncMock()) as mock_switch,
    ):
        for _ in range(10):
            asyncio.run(monitor.check_once(trigger_auto_heal=True))

    mock_switch.assert_not_awaited()
    assert monitor.consecutive_failures == 0


def test_single_probe_blip_never_switches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_NET_GUARD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AI_NET_GUARD_STATE_DIR", str(tmp_path / "state"))

    single_fail = _make_report(ag_ok=False, ag_status="fail", gemini_ok=True, google_ok=True)
    cfg = AppConfig(fail_threshold=2, ag_log_path=str(tmp_path / "missing.log"))
    monitor = NetworkMonitor(config=cfg)

    with (
        patch("ai_net_guard.monitor.run_full_diagnostics", AsyncMock(return_value=single_fail)),
        patch("ai_net_guard.mihomo.MihomoClient.get_status", AsyncMock(return_value=_fake_mihomo())),
        patch("ai_net_guard.mihomo.MihomoClient.test_and_select_best", AsyncMock()) as mock_switch,
    ):
        for _ in range(6):
            asyncio.run(monitor.check_once(trigger_auto_heal=True))

    mock_switch.assert_not_awaited()
    assert monitor.consecutive_failures == 0


def test_domestic_internet_down_never_switches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_NET_GUARD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AI_NET_GUARD_STATE_DIR", str(tmp_path / "state"))

    wifi_down = _make_report(deepseek_ok=False, domestic_ok=False, ag_ok=False, gemini_ok=False, google_ok=False)
    cfg = AppConfig(fail_threshold=1, ag_log_path=str(tmp_path / "missing.log"))
    monitor = NetworkMonitor(config=cfg)

    with (
        patch("ai_net_guard.monitor.run_full_diagnostics", AsyncMock(return_value=wifi_down)),
        patch("ai_net_guard.mihomo.MihomoClient.get_status", AsyncMock(return_value=_fake_mihomo())),
        patch("ai_net_guard.mihomo.MihomoClient.test_and_select_best", AsyncMock()) as mock_switch,
    ):
        for _ in range(5):
            asyncio.run(monitor.check_once(trigger_auto_heal=True))

    mock_switch.assert_not_awaited()
    assert monitor.consecutive_failures == 0


def test_cooldown_blocks_second_auto_heal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_NET_GUARD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AI_NET_GUARD_STATE_DIR", str(tmp_path / "state"))

    broken_report = _make_report(ag_ok=False, ag_status="fail", gemini_ok=False, google_ok=False)
    cfg = AppConfig(
        fail_threshold=2,
        heal_cooldown_sec=1800,
        max_heals_per_hour=2,
        max_heals_per_day=5,
        ag_log_path=str(tmp_path / "missing.log"),
    )
    monitor = NetworkMonitor(config=cfg)

    with (
        patch("ai_net_guard.monitor.run_full_diagnostics", AsyncMock(return_value=broken_report)),
        patch("ai_net_guard.mihomo.MihomoClient.get_status", AsyncMock(return_value=_fake_mihomo())),
        patch(
            "ai_net_guard.mihomo.MihomoClient.test_and_select_best",
            AsyncMock(return_value=(True, "已切换", "新加坡 SG-02")),
        ) as mock_switch,
        patch("ai_net_guard.monitor.send_macos_notification"),
    ):
        # First 2 checks hit fail_threshold=2 and trigger 1st heal
        asyncio.run(monitor.check_once(trigger_auto_heal=True))
        asyncio.run(monitor.check_once(trigger_auto_heal=True))
        assert mock_switch.await_count == 1
        assert monitor.get_anti_ban_status()["allowed"] is False

        # Subsequent failures during 30-min cooldown must be blocked
        asyncio.run(monitor.check_once(trigger_auto_heal=True))
        asyncio.run(monitor.check_once(trigger_auto_heal=True))
        assert mock_switch.await_count == 1


def test_hourly_and_daily_caps(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_NET_GUARD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AI_NET_GUARD_STATE_DIR", str(tmp_path / "state"))

    cfg = AppConfig(heal_cooldown_sec=60, max_heals_per_hour=2, max_heals_per_day=5)
    monitor = NetworkMonitor(config=cfg)

    base = datetime.datetime(2026, 11, 23, 12, 0, 0).timestamp()
    monitor._record_auto_heal("r1", "JP-1", now_epoch=base)
    monitor._record_auto_heal("r2", "SG-2", now_epoch=base + 120)

    # 5 minutes later: cooldown passed (180 > 60), but hourly cap 2/2 reached
    st_hour = monitor.get_anti_ban_status(now_epoch=base + 300)
    assert st_hour["allowed"] is False
    assert "每小时" in st_hour["block_reason"]

    # Spread 5 heals across different hours on the same day -> daily cap 5/5 reached
    monitor._record_auto_heal("r3", "US-3", now_epoch=base + 4000)
    monitor._record_auto_heal("r4", "TW-4", now_epoch=base + 8000)
    monitor._record_auto_heal("r5", "KR-5", now_epoch=base + 12000)
    st_day = monitor.get_anti_ban_status(now_epoch=base + 16000)
    assert st_day["allowed"] is False
    assert "每日" in st_day["block_reason"]


def test_proxy_off_never_counts_failure_or_cooldown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_NET_GUARD_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AI_NET_GUARD_STATE_DIR", str(tmp_path / "state"))

    proxy_off_report = _make_report(deepseek_ok=True, domestic_ok=True, ag_ok=False, gemini_ok=False, google_ok=False)
    proxy_off_report.proxy_state = ProxyState(effective_proxy_url="", port_listening=False)

    port_dead_report = _make_report(deepseek_ok=True, domestic_ok=True, ag_ok=False, gemini_ok=False, google_ok=False)
    port_dead_report.proxy_state = ProxyState(effective_proxy_url="http://127.0.0.1:7897", port_listening=False)

    cfg = AppConfig(fail_threshold=1, heal_cooldown_sec=1800, ag_log_path=str(tmp_path / "missing.log"))
    monitor = NetworkMonitor(config=cfg)

    with (
        patch(
            "ai_net_guard.monitor.run_full_diagnostics",
            AsyncMock(side_effect=[proxy_off_report, port_dead_report, proxy_off_report]),
        ),
        patch("ai_net_guard.mihomo.MihomoClient.get_status", AsyncMock(return_value=_fake_mihomo())),
        patch("ai_net_guard.mihomo.MihomoClient.test_and_select_best", AsyncMock()) as mock_switch,
    ):
        for _ in range(3):
            asyncio.run(monitor.check_once(trigger_auto_heal=True))

    mock_switch.assert_not_awaited()
    assert monitor.consecutive_failures == 0
    assert monitor.get_anti_ban_status()["allowed"] is True
    assert monitor.get_anti_ban_status()["heals_today"] == 0
