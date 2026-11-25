"""Command-line entry point for AI NetGuard (`ai-net`)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn

from .config import get_state_dir, load_config
from .diagnostics import DiagnosticReport, run_full_diagnostics
from .mihomo import MihomoClient
from .server import create_app, get_override_script_path

LAUNCHD_LABEL = "com.panyuanhao.ai-net-guard"


def _pid_file() -> Path:
    return get_state_dir() / "server.pid"


def _log_file() -> Path:
    return get_state_dir() / "server.log"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _is_dashboard_alive(port: int) -> bool:
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/api/override", timeout=1.5, trust_env=False)
        return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def _print_report(report: DiagnosticReport) -> None:
    badge = {"healthy": "✅ 健康", "degraded": "⚠️  存在隐患", "broken": "❌ 严重故障"}.get(
        report.overall_status, report.overall_status
    )
    print("=" * 68)
    print(f"  AI NetGuard v2.0 五步全链路网络体检报告  [{badge}]")
    print(f"  检查时间: {report.timestamp}")
    print(f"  总结论  : {report.summary}")
    print("=" * 68)

    icon = {"ok": "🟢", "warn": "🟡", "fail": "🔴"}
    if report.pipeline:
        print("\n[5 步全链路排查]")
        for step in report.pipeline:
            st = step.get("status", "warn")
            print(
                f"  {icon.get(st, '⚪')} 第{step.get('step','')}步 {step.get('title',''):<12} | {step.get('metric',''):<18} | {step.get('summary','')}"
            )

    print("\n[双视角出口 IP 体检]")
    dom = report.domestic_ip
    if dom.reachable:
        print(f"  {dom.flag} 国内直连出口 : {dom.ip} ({dom.country} {dom.region} {dom.city} · {dom.isp})")
    else:
        print(f"  🔴 国内直连出口 : 获取失败 ({dom.error})")

    ec = report.exit_check
    if ec.reachable:
        hop_txt = f" ⚠️ 跳变中 ({ec.ip_1} -> {ec.ip_2})" if ec.ip_changed else " 🔒 IP 固定"
        print(
            f"  {ec.flag} 代理对外出口 : {ec.ip_1} ({ec.country} {ec.city} · {ec.isp or ec.asn}) | AI 评级 [{ec.ai_grade}]{hop_txt}"
        )
        print(f"     IP 属性判断  : {ec.ip_attr_label} | {ec.ai_grade_reason}")
    else:
        print(f"  🔴 代理对外出口 : 不可达 ({ec.error})")

    print("\n[大白话诊断与修复建议]")
    level_tag = {"critical": "🔴 关键问题", "warn": "🟡 潜在隐患", "info": "ℹ️  提示", "ok": "🟢 正常"}
    for idx, f in enumerate(report.findings, 1):
        print(f"  {idx}. [{level_tag.get(f.level, f.level)}] {f.title}")
        print(f"     原因: {f.detail}")
        print(f"     建议: {f.action}")

    print("\n[各软件与网站实测明细]")
    for p in report.probes:
        st = "✅" if p.status == "ok" else ("⚠️" if p.status == "warn" else "❌")
        code_str = f"HTTP {p.http_code}" if p.http_code else "ERR"
        print(f"  {st} {p.label:<24} [{p.mode:<6}] {code_str:<9} {p.latency_ms:>5} ms  {p.reason}")
    print("=" * 68)


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_config()
    report = asyncio.run(run_full_diagnostics(cfg.proxy_url))
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report.overall_status != "broken" else 1


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = load_config()
    port = args.port or cfg.dashboard_port
    app = create_app(config=cfg, start_monitor=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    cfg = load_config()
    port = args.port or cfg.dashboard_port
    if _is_dashboard_alive(port):
        print(f"✅ AI NetGuard 已在运行：http://127.0.0.1:{port}")
        return 0

    plist = _plist_path()
    if plist.exists() and not args.port:
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True, check=False)
        res = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            for _ in range(15):
                if _is_dashboard_alive(port):
                    print(f"✅ AI NetGuard 已通过 LaunchAgent 启动：http://127.0.0.1:{port}")
                    return 0
                time.sleep(0.3)

    log_path = _log_file()
    log_fh = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ai_net_guard.cli", "serve", "--port", str(port)],
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )
    _pid_file().write_text(str(proc.pid), encoding="utf-8")
    for _ in range(20):
        if _is_dashboard_alive(port):
            print(f"✅ AI NetGuard 后台服务已启动 (PID {proc.pid})：http://127.0.0.1:{port}")
            return 0
        time.sleep(0.3)
    print(f"⚠️ 服务启动超时，请查看日志：{log_path}", file=sys.stderr)
    return 1


def cmd_stop(args: argparse.Namespace) -> int:
    plist = _plist_path()
    if plist.exists():
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True, check=False)

    pid_path = _pid_file()
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pid_path.unlink(missing_ok=True)
            print("🛑 AI NetGuard 已停止")
            return 0
        pid_path.unlink(missing_ok=True)
    print("🛑 AI NetGuard 已停止")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    port = cfg.dashboard_port
    if _is_dashboard_alive(port):
        print(f"🟢 运行中：http://127.0.0.1:{port}")
        return 0
    print("🔴 未运行（运行 `ai-net start` 或 `ai-net open` 启动）")
    return 1


def cmd_open(args: argparse.Namespace) -> int:
    cfg = load_config()
    port = args.port or cfg.dashboard_port
    if not _is_dashboard_alive(port):
        rc = cmd_start(args)
        if rc != 0:
            return rc
    url = f"http://127.0.0.1:{port}"
    subprocess.run(["open", url], check=False)
    print(f"🌐 已打开面板：{url}")
    return 0


async def _heal_async() -> int:
    cfg = load_config()
    client = MihomoClient(api_url=cfg.mihomo_api, secret=cfg.mihomo_secret)
    ok, msg, _ = await client.test_and_select_best(preferred_group=cfg.mihomo_group)
    print(("✅ " if ok else "⚠️ ") + msg)
    return 0 if ok else 1


def cmd_heal(args: argparse.Namespace) -> int:
    return asyncio.run(_heal_async())


async def _wait_healthy_async(timeout_sec: int, interval_sec: int) -> int:
    cfg = load_config()
    deadline = time.monotonic() + timeout_sec
    client = MihomoClient(api_url=cfg.mihomo_api, secret=cfg.mihomo_secret)
    healed_once = False

    while True:
        report = await run_full_diagnostics(cfg.proxy_url)
        by_key = {p.key: p for p in report.probes}
        core_ok = all(by_key[k].ok for k in ("antigravity", "gemini_api", "google_204") if k in by_key)
        if core_ok and not report.exit_check.restricted_region:
            print(f"[ai-net] 网络健康就绪 ({report.summary})")
            return 0

        if not healed_once:
            status = await client.get_status(preferred_group=cfg.mihomo_group)
            if status.connected:
                ok, msg, _ = await client.test_and_select_best(preferred_group=status.active_group or cfg.mihomo_group)
                print(f"[ai-net] 尝试自动切节点自愈：{msg}")
                healed_once = True
                if ok:
                    continue

        if time.monotonic() >= deadline:
            print(f"[ai-net] 等待网络恢复超时（{timeout_sec}s）：{report.summary}", file=sys.stderr)
            return 1

        print(f"[ai-net] 网络暂不健康 ({report.summary})，{interval_sec}s 后复检...")
        await asyncio.sleep(interval_sec)


def cmd_wait_healthy(args: argparse.Namespace) -> int:
    return asyncio.run(_wait_healthy_async(args.timeout, args.interval))


def cmd_export_override(args: argparse.Namespace) -> int:
    path = get_override_script_path()
    print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-net", description="AI NetGuard CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="输出四层网络体检报告")
    p_doc.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    p_doc.set_defaults(func=cmd_doctor)

    p_serve = sub.add_parser("serve", help="前台启动监控与 Web 仪表盘")
    p_serve.add_argument("--port", type=int, default=0)
    p_serve.set_defaults(func=cmd_serve)

    p_start = sub.add_parser("start", help="后台启动常驻服务")
    p_start.add_argument("--port", type=int, default=0)
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="停止后台服务")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="查看服务运行状态")
    p_status.set_defaults(func=cmd_status)

    p_open = sub.add_parser("open", help="打开中文可视化面板")
    p_open.add_argument("--port", type=int, default=0)
    p_open.set_defaults(func=cmd_open)

    p_heal = sub.add_parser("heal", help="立即检测并通过 Mihomo 切换最优节点")
    p_heal.set_defaults(func=cmd_heal)

    p_wait = sub.add_parser("wait-healthy", help="等待核心 AI 网络恢复健康")
    p_wait.add_argument("--timeout", type=int, default=300)
    p_wait.add_argument("--interval", type=int, default=10)
    p_wait.set_defaults(func=cmd_wait_healthy)

    p_exp = sub.add_parser("export-override", help="显示 Clash Verge 覆写脚本路径")
    p_exp.set_defaults(func=cmd_export_override)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
