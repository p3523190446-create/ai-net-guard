"""Tests for AI NetGuard diagnostic parsing and evaluation."""

from ai_net_guard.diagnostics import (
    DnsCheckItem,
    ExitCheck,
    LocalNetworkInfo,
    ProbeResult,
    ProxyState,
    compute_exit_grade,
    country_code_to_flag,
    detect_proxy_processes,
    evaluate_report,
    parse_scutil_dns,
    parse_scutil_proxy,
)


def _healthy_probes() -> list[ProbeResult]:
    return [
        ProbeResult("deepseek_direct", "国内 DeepSeek（直连）", "direct", "https://api.deepseek.com", True, "ok", 401, 280, "正常"),
        ProbeResult("google_204", "Google 基础连通", "proxy", "https://www.google.com/generate_204", True, "ok", 204, 340, "正常"),
        ProbeResult("antigravity", "Antigravity 编辑器服务", "proxy", "https://antigravity.google", True, "ok", 200, 410, "正常"),
        ProbeResult("gemini_api", "Gemini / Google AI 接口", "proxy", "https://generativelanguage.googleapis.com", True, "ok", 404, 390, "正常"),
        ProbeResult("openai_api", "OpenAI / Codex 接口", "proxy", "https://api.openai.com/v1/models", True, "ok", 401, 450, "正常"),
        ProbeResult("github", "GitHub 代码库", "proxy", "https://github.com", True, "ok", 200, 500, "正常"),
        ProbeResult("youtube", "YouTube 视频", "proxy", "https://www.youtube.com/generate_204", True, "ok", 204, 380, "正常"),
    ]


def test_parse_scutil_proxy_and_dns() -> None:
    proxy_out = """<dictionary> {
      HTTPEnable : 1
      HTTPPort : 1082
      HTTPProxy : 127.0.0.1
      HTTPSEnable : 1
      HTTPSPort : 1082
      SOCKSEnable : 0
    }"""
    state = parse_scutil_proxy(proxy_out)
    assert state.http_enabled is True
    assert state.effective_proxy_url == "http://127.0.0.1:1082"

    dns_out = """
      nameserver[0] : 198.18.0.2
      nameserver[0] : 202.192.72.36
      nameserver[1] : 119.145.71.239
    """
    servers, fake_ip = parse_scutil_dns(dns_out)
    assert fake_ip is True
    assert servers == ["198.18.0.2", "202.192.72.36", "119.145.71.239"]


def test_detect_proxy_processes() -> None:
    ps_out = """
    71050 /Applications/Shadowrocket.app/Contents/MacOS/Shadowrocket
    71055 /Applications/Shadowrocket.app/Contents/PlugIns/MacPacketTunnel.appex/Contents/MacOS/MacPacketTunnel
    """
    apps, sr, cv = detect_proxy_processes(ps_out)
    assert apps == ["Shadowrocket"]
    assert sr is True
    assert cv is False


def test_evaluate_dead_port_fake_connection() -> None:
    state = ProxyState(
        http_enabled=True,
        http_host="127.0.0.1",
        http_port=1082,
        effective_proxy_url="http://127.0.0.1:1082",
        port_listening=False,
    )
    report = evaluate_report(state, ExitCheck(reachable=False, error="Connection refused"), _healthy_probes())
    assert report.overall_status == "broken"
    assert report.cards["proxy_layer"]["status"] == "fail"
    assert any("假连接" in f.title for f in report.findings)


def test_evaluate_ip_hopping_and_shadowrocket() -> None:
    state = ProxyState(
        http_enabled=True,
        http_host="127.0.0.1",
        http_port=1082,
        effective_proxy_url="http://127.0.0.1:1082",
        port_listening=True,
        fake_ip_dns=True,
        running_apps=["Shadowrocket"],
        shadowrocket_active=True,
    )
    exit_chk = ExitCheck(reachable=True, ip_1="60.71.147.142", ip_2="126.51.123.1", loc="JP", colo="NRT", ip_changed=True)
    report = evaluate_report(state, exit_chk, _healthy_probes())
    assert report.overall_status == "degraded"
    assert report.cards["exit_stability"]["status"] == "warn"
    assert any("出口 IP 高频跳变" in f.title for f in report.findings)
    assert any("Shadowrocket" in f.title for f in report.findings)


def test_evaluate_restricted_hk_region() -> None:
    state = ProxyState(effective_proxy_url="http://127.0.0.1:7897", port_listening=True, clash_verge_active=True)
    exit_chk = ExitCheck(reachable=True, ip_1="1.2.3.4", ip_2="1.2.3.4", loc="HK", colo="HKG", restricted_region=True)
    report = evaluate_report(state, exit_chk, _healthy_probes())
    assert report.overall_status == "broken"
    assert report.cards["exit_stability"]["status"] == "fail"
    assert any("受限地区" in f.title for f in report.findings)


def test_evaluate_healthy_and_openai_421() -> None:
    state = ProxyState(
        effective_proxy_url="http://127.0.0.1:7897",
        port_listening=True,
        running_apps=["Clash Verge"],
        clash_verge_active=True,
    )
    exit_chk = ExitCheck(reachable=True, ip_1="45.1.2.3", ip_2="45.1.2.3", loc="JP", colo="NRT")
    healthy = evaluate_report(state, exit_chk, _healthy_probes())
    assert healthy.overall_status == "healthy"
    assert healthy.cards["overseas_ai"]["status"] == "ok"

    probes_421 = _healthy_probes()
    probes_421[4] = ProbeResult(
        "openai_api", "OpenAI / Codex 接口", "proxy", "https://api.openai.com/v1/models", False, "fail", 421, 500, "HTTP 421"
    )
    degraded = evaluate_report(state, exit_chk, probes_421)
    assert degraded.overall_status == "degraded"
    assert any("OpenAI" in f.title for f in degraded.findings)


def test_flag_and_exit_grade() -> None:
    assert country_code_to_flag("jp") == "🇯🇵"
    assert country_code_to_flag("cn") == "🇨🇳"
    assert country_code_to_flag("invalid") == "🌐"

    _, g_s, _ = compute_exit_grade(True, "JP", False, False, False)
    _, g_b, _ = compute_exit_grade(True, "US", False, True, False)
    _, g_c, _ = compute_exit_grade(True, "SG", True, False, False)
    _, g_f, _ = compute_exit_grade(True, "HK", False, False, False)
    assert (g_s, g_b, g_c, g_f) == ("S", "B", "C", "F")


def test_proxy_forward_blocked_and_pipeline() -> None:
    state = ProxyState(
        http_enabled=True,
        http_host="127.0.0.1",
        http_port=1082,
        effective_proxy_url="http://127.0.0.1:1082",
        port_listening=True,
        fake_ip_dns=True,
    )
    probes = [
        ProbeResult("deepseek_direct", "DeepSeek", "direct", "https://api.deepseek.com", True, "ok", 401, 200, "正常"),
        ProbeResult("antigravity", "Antigravity", "proxy", "https://antigravity.google", False, "fail", 0, 8000, "超时"),
        ProbeResult("google_204", "Google", "proxy", "https://www.google.com/generate_204", False, "fail", 0, 8000, "超时"),
    ]
    dns_list = [
        DnsCheckItem("api.deepseek.com", "DeepSeek", "198.18.0.5", "fake_ip", "被代理虚拟 DNS 劫持"),
    ]
    report = evaluate_report(
        state,
        ExitCheck(reachable=False, error="timeout"),
        probes,
        local_network=LocalNetworkInfo(interface="en0", lan_ip="192.168.1.20"),
        dns_checks=dns_list,
    )
    assert report.overall_status == "broken"
    assert any("后端假死" in f.title for f in report.findings)
    assert len(report.pipeline) == 5
    assert report.pipeline[0]["status"] == "ok"
    assert report.pipeline[1]["status"] == "warn"
    assert report.pipeline[2]["status"] == "fail"
    assert report.pipeline[2]["metric"] == "转发堵死"
