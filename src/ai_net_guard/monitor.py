"""Background network monitor, persistent history, and self-healing state machine."""

from __future__ import annotations

import asyncio
import datetime
import json
import subprocess
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .antigravity_watchdog import AntigravityWatchdog
from .config import (
    AppConfig,
    get_events_log_path,
    get_heal_history_path,
    get_ip_history_path,
    load_config,
    save_config,
)
from .diagnostics import (
    PROBE_SPECS,
    DiagnosticReport,
    _build_custom_specs,
    probe_single_target,
    run_full_diagnostics,
)
from .mihomo import MihomoClient, MihomoStatus


def send_macos_notification(title: str, body: str) -> None:
    """Best-effort macOS desktop notification without blocking."""
    safe_title = title.replace('"', '\\"')
    safe_body = body.replace('"', '\\"')
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return


def _load_jsonl(path: Path, maxlen: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return items[-maxlen:]


def _append_jsonl(path: Path, item: dict[str, Any], max_keep: int = 200) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_jsonl(path, max_keep - 1)
        existing.append(item)
        lines = [json.dumps(x, ensure_ascii=False) for x in existing[-max_keep:]]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return


class NetworkMonitor:
    """Coordinates periodic AI network checks, persistent IP history, and auto-healing."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config: AppConfig = config or load_config()
        self.latest_report: DiagnosticReport | None = None
        self.latest_mihomo: MihomoStatus = MihomoStatus(connected=False)
        self.history: deque[dict[str, Any]] = deque(maxlen=60)
        self.events: deque[dict[str, Any]] = deque(
            reversed(_load_jsonl(get_events_log_path(), 100)), maxlen=100
        )
        self.ip_history: deque[dict[str, Any]] = deque(
            reversed(_load_jsonl(get_ip_history_path(), 200)), maxlen=200
        )
        self.auto_heal_records: deque[dict[str, Any]] = deque(
            _load_jsonl(get_heal_history_path(), 200), maxlen=200
        )
        self.consecutive_failures: int = 0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_notify_at: dict[str, float] = {}
        self.ag_watchdog = AntigravityWatchdog(
            log_path=self.config.ag_log_path,
            enabled=self.config.ag_watchdog_enabled,
            eof_threshold=self.config.ag_eof_threshold,
            window_sec=self.config.ag_eof_window_sec,
            cooldown_sec=self.config.ag_heal_cooldown_sec,
            silent_heal=self.config.ag_silent_heal,
        )

    def _add_event(self, level: str, message: str) -> None:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {"timestamp": ts.split(" ")[-1], "full_time": ts, "level": level, "message": message}
        self.events.appendleft(entry)
        _append_jsonl(get_events_log_path(), entry, max_keep=100)

    def _record_ip_snapshot(self, report: DiagnosticReport) -> None:
        ec = report.exit_check
        if not ec.reachable or not ec.ip_1:
            return
        latest = self.ip_history[0] if self.ip_history else None
        changed_from_prev = latest is None or latest.get("ip") != ec.ip_1 or ec.ip_changed
        if not changed_from_prev:
            return
        entry = {
            "time": report.timestamp,
            "ip": ec.ip_1,
            "ip_2": ec.ip_2,
            "flag": ec.flag,
            "country": ec.country or ec.loc,
            "city": ec.city,
            "isp": ec.isp,
            "grade": ec.ai_grade,
            "hopped": ec.ip_changed,
        }
        self.ip_history.appendleft(entry)
        _append_jsonl(get_ip_history_path(), entry, max_keep=200)

    def _notify_with_cooldown(self, key: str, title: str, body: str, cooldown_sec: int = 300) -> None:
        now = time.monotonic()
        last = self._last_notify_at.get(key, 0.0)
        if now - last >= cooldown_sec:
            self._last_notify_at[key] = now
            send_macos_notification(title, body)

    def get_anti_ban_status(self, now_epoch: float | None = None) -> dict[str, Any]:
        """Compute current anti-ban rate-limit status for automatic node switching."""
        now = time.time() if now_epoch is None else float(now_epoch)
        today_str = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        heals_last_hour = sum(
            1 for r in self.auto_heal_records if 0 <= (now - float(r.get("epoch", 0))) < 3600
        )
        heals_today = sum(1 for r in self.auto_heal_records if r.get("date") == today_str)

        cooldown_sec = max(0, int(self.config.heal_cooldown_sec))
        max_hour = max(1, int(self.config.max_heals_per_hour))
        max_day = max(1, int(self.config.max_heals_per_day))

        last_rec = self.auto_heal_records[-1] if self.auto_heal_records else None
        cooldown_remaining = 0
        if last_rec and cooldown_sec > 0:
            elapsed = now - float(last_rec.get("epoch", 0))
            if 0 <= elapsed < cooldown_sec:
                cooldown_remaining = int(cooldown_sec - elapsed)

        allowed = True
        block_reason = ""
        if cooldown_remaining > 0:
            allowed = False
            block_reason = f"处于 {max(1, cooldown_sec // 60)} 分钟防封控冷却期（还需等待 {cooldown_remaining} 秒）"
        elif heals_last_hour >= max_hour:
            allowed = False
            block_reason = f"已达每小时自动切换上限（{heals_last_hour}/{max_hour} 次），禁止继续切节点"
        elif heals_today >= max_day:
            allowed = False
            block_reason = f"已达每日自动切换上限（{heals_today}/{max_day} 次），今日仅可手动切换"

        return {
            "allowed": allowed,
            "block_reason": block_reason,
            "heals_last_hour": heals_last_hour,
            "max_heals_per_hour": max_hour,
            "heals_today": heals_today,
            "max_heals_per_day": max_day,
            "cooldown_sec": cooldown_sec,
            "cooldown_remaining_sec": cooldown_remaining,
            "last_auto_heal_time": str(last_rec.get("time", "")) if last_rec else "",
        }

    def _record_auto_heal(self, reason: str, node: str | None, now_epoch: float | None = None) -> None:
        now = time.time() if now_epoch is None else float(now_epoch)
        dt = datetime.datetime.fromtimestamp(now)
        entry = {
            "epoch": now,
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "node": node or "",
        }
        self.auto_heal_records.append(entry)
        _append_jsonl(get_heal_history_path(), entry, max_keep=200)

    def update_config(self, updates: dict[str, Any]) -> AppConfig:
        for field in (
            "proxy_url",
            "mihomo_api",
            "mihomo_secret",
            "mihomo_group",
            "auto_heal",
            "check_interval_sec",
            "fail_threshold",
            "heal_cooldown_sec",
            "max_heals_per_hour",
            "max_heals_per_day",
            "ag_watchdog_enabled",
            "ag_eof_threshold",
            "ag_eof_window_sec",
            "ag_heal_cooldown_sec",
            "ag_silent_heal",
            "ag_log_path",
        ):
            if field in updates and updates[field] is not None:
                setattr(self.config, field, updates[field])
        self.ag_watchdog.update_settings(
            enabled=self.config.ag_watchdog_enabled,
            eof_threshold=self.config.ag_eof_threshold,
            window_sec=self.config.ag_eof_window_sec,
            cooldown_sec=self.config.ag_heal_cooldown_sec,
            silent_heal=self.config.ag_silent_heal,
            log_path=self.config.ag_log_path,
        )
        save_config(self.config)
        self._add_event("info", "已保存监控配置")
        return self.config

    def add_custom_site(self, name: str, url: str, mode: str = "proxy") -> list[dict[str, str]]:
        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://")):
            clean_url = f"https://{clean_url}"
        clean_name = name.strip() or clean_url
        clean_mode = "direct" if mode == "direct" else "proxy"
        self.config.custom_sites.append({"name": clean_name, "url": clean_url, "mode": clean_mode})
        save_config(self.config)
        self._add_event("info", f"已添加自定义检测站点：{clean_name} ({clean_mode})")
        return self.config.custom_sites

    def remove_custom_site(self, index: int) -> list[dict[str, str]]:
        if 0 <= index < len(self.config.custom_sites):
            removed = self.config.custom_sites.pop(index)
            save_config(self.config)
            self._add_event("info", f"已移除自定义检测站点：{removed.get('name', '')}")
        return self.config.custom_sites

    def _mihomo_client(self) -> MihomoClient:
        return MihomoClient(api_url=self.config.mihomo_api, secret=self.config.mihomo_secret)

    async def probe_one(self, key: str) -> dict[str, Any]:
        all_specs = PROBE_SPECS + _build_custom_specs(self.config.custom_sites)
        spec = next((s for s in all_specs if s["key"] == key), None)
        if not spec:
            return {"ok": False, "error": f"未找到探针 {key}"}
        proxy_url = None
        if self.latest_report and self.latest_report.proxy_state.port_listening:
            proxy_url = self.latest_report.proxy_state.effective_proxy_url
        res = await probe_single_target(spec, proxy_url)
        if self.latest_report:
            self.latest_report.probes = [res if p.key == key else p for p in self.latest_report.probes]
        return {"ok": True, "probe": asdict(res)}

    async def check_once(self, trigger_auto_heal: bool = True) -> DiagnosticReport:
        async with self._lock:
            client = self._mihomo_client()
            report, mihomo_status = await asyncio.gather(
                run_full_diagnostics(self.config.proxy_url, custom_sites=self.config.custom_sites),
                client.get_status(preferred_group=self.config.mihomo_group),
            )
            self.latest_report = report
            self.latest_mihomo = mihomo_status
            self._record_ip_snapshot(report)

            by_key = {p.key: p for p in report.probes}
            self.history.append(
                {
                    "time": report.timestamp.split(" ")[-1],
                    "status": report.overall_status,
                    "deepseek_ms": by_key["deepseek_direct"].latency_ms if "deepseek_direct" in by_key else 0,
                    "google_ms": by_key["google_204"].latency_ms if "google_204" in by_key else 0,
                    "antigravity_ms": by_key["antigravity"].latency_ms if "antigravity" in by_key else 0,
                    "openai_ms": by_key["openai_api"].latency_ms if "openai_api" in by_key else 0,
                }
            )

            domestic_ok = (
                by_key["deepseek_direct"].ok if "deepseek_direct" in by_key else not report.domestic_ip.error
            ) or report.domestic_ip.reachable
            core_probes = [by_key[k] for k in ("antigravity", "gemini_api", "google_204") if k in by_key]
            failed_core_count = sum(1 for p in core_probes if not p.ok)
            min_fail = 2 if len(core_probes) >= 2 else 1
            proxy_active = bool(report.proxy_state.effective_proxy_url) and report.proxy_state.port_listening

            if not domestic_ok:
                is_critical_issue = False
                self.consecutive_failures = 0
                self._add_event("info", "本机基础网络/国内直连不通，原地等待，不瞎切代理节点")
            elif not proxy_active:
                is_critical_issue = False
                self.consecutive_failures = 0
                last_msg = self.events[0]["message"] if self.events else ""
                off_msg = "系统代理未开启或代理端口未监听，原地等待，不计入节点故障"
                if last_msg != off_msg:
                    self._add_event("info", off_msg)
            else:
                is_critical_issue = (
                    (failed_core_count >= min_fail)
                    or report.exit_check.restricted_region
                )
                if is_critical_issue:
                    self.consecutive_failures += 1
                    self._add_event(
                        "warn",
                        f"AI 核心链路不可用（连续第 {self.consecutive_failures}/{max(1, int(self.config.fail_threshold))} 次）：{report.summary}",
                    )
                else:
                    if failed_core_count == 1:
                        self._add_event("info", "仅单点偶发闪断，未达多点确认阈值，保持当前节点不动")
                    elif self.consecutive_failures > 0:
                        self._add_event("ok", "AI 网络链路已恢复正常")
                    self.consecutive_failures = 0

            should_heal = (
                trigger_auto_heal
                and self.config.auto_heal
                and is_critical_issue
                and self.consecutive_failures >= max(1, int(self.config.fail_threshold))
            )

            healed_by_probe = False
            if should_heal:
                anti_ban = self.get_anti_ban_status()
                if not anti_ban["allowed"]:
                    self._add_event("warn", f"🛡️ 防封控锁已拦截自动切节点：{anti_ban['block_reason']}")
                else:
                    self._notify_with_cooldown("broken", "AI NetGuard 检测到网络故障", report.summary)
                    if mihomo_status.connected:
                        ok, msg, best_node = await client.test_and_select_best(
                            preferred_group=mihomo_status.active_group or self.config.mihomo_group
                        )
                        self._add_event("ok" if ok else "warn", f"自动自愈：{msg}")
                        if ok:
                            healed_by_probe = True
                            self._record_auto_heal("AI 多点彻底不可用", best_node)
                            self._notify_with_cooldown(
                                "healed",
                                "AI NetGuard 已自动切换节点",
                                f"已切换至 {best_node}，正在复检连通性",
                            )
                            report = await run_full_diagnostics(self.config.proxy_url, custom_sites=self.config.custom_sites)
                            self.latest_report = report
                            self._record_ip_snapshot(report)
                            self.latest_mihomo = await client.get_status(preferred_group=self.config.mihomo_group)
                            self.consecutive_failures = 0
                    else:
                        self._add_event(
                            "warn",
                            "触发自动自愈条件，但当前未检测到 Clash/Mihomo 控制端口（如在使用小火箭无法通过 API 自动切节点）",
                        )

            ag_poll = self.ag_watchdog.poll()
            if ag_poll["new_eofs"] > 0 and not ag_poll["should_heal"]:
                self._add_event(
                    "info",
                    f"Antigravity 近期出现流式断流（{ag_poll['recent_eof_count']}/{self.ag_watchdog.eof_threshold} 次），持续观察暂不切节点",
                )
            if trigger_auto_heal and self.config.auto_heal and ag_poll["should_heal"]:
                if not (domestic_ok and proxy_active):
                    self._add_event("info", "基础网络或系统代理未就绪，暂缓执行 Antigravity 自动切节点")
                elif healed_by_probe:
                    self.ag_watchdog.mark_healed(
                        ok=True,
                        message="链路异常已同步切换节点",
                        selected_node=self.latest_mihomo.current_node or None,
                    )
                else:
                    await self._heal_for_antigravity(
                        client=client,
                        reason=ag_poll["reason"],
                        silent=self.config.ag_silent_heal,
                        enforce_rate_limit=True,
                    )
                    if self.latest_report is not None:
                        report = self.latest_report

            return report

    async def _heal_for_antigravity(
        self,
        client: MihomoClient,
        reason: str,
        silent: bool = True,
        enforce_rate_limit: bool = False,
    ) -> dict[str, Any]:
        """Switch to the fastest clean node for Antigravity without interrupting normal work."""
        if enforce_rate_limit:
            anti_ban = self.get_anti_ban_status()
            if not anti_ban["allowed"]:
                msg = f"🛡️ 防封控锁已拦截 Antigravity 自动切节点：{anti_ban['block_reason']}"
                self._add_event("warn", msg)
                self.ag_watchdog.mark_healed(ok=False, message=anti_ban["block_reason"])
                return {
                    "ok": False,
                    "message": msg,
                    "selected_node": None,
                    "reason": reason,
                    "blocked_by_anti_ban": True,
                }

        status = await client.get_status(preferred_group=self.config.mihomo_group)
        if not status.connected:
            err = status.error or "未检测到 Clash Verge / Mihomo 控制端口（请检查是否误切到小火箭 1082）"
            self._add_event("warn", f"Antigravity 防空转自愈未执行（{reason}）：{err}")
            self.ag_watchdog.mark_healed(ok=False, message=err)
            return {"ok": False, "message": err, "selected_node": None, "reason": reason}

        ok, msg, best_node = await client.test_and_select_best(
            preferred_group=status.active_group or self.config.mihomo_group
        )
        self._add_event("ok" if ok else "warn", f"Antigravity 防空转自愈（{reason}）：{msg}")
        self.ag_watchdog.mark_healed(ok=ok, message=msg, selected_node=best_node)
        if ok:
            if enforce_rate_limit:
                self._record_auto_heal(reason, best_node)
            if not silent:
                self._notify_with_cooldown(
                    "ag_healed",
                    "AI NetGuard 已为 Antigravity 切换节点",
                    f"已切换至 {best_node}",
                )
            self.consecutive_failures = 0
            self.latest_report = await run_full_diagnostics(
                self.config.proxy_url, custom_sites=self.config.custom_sites
            )
            self._record_ip_snapshot(self.latest_report)
            self.latest_mihomo = await client.get_status(preferred_group=self.config.mihomo_group)
        return {"ok": ok, "message": msg, "selected_node": best_node, "reason": reason}

    async def trigger_antigravity_recover(self, reason: str = "手动/外部触发") -> dict[str, Any]:
        """Explicitly switch to a healthier node on behalf of Antigravity."""
        async with self._lock:
            client = self._mihomo_client()
            return await self._heal_for_antigravity(
                client=client,
                reason=reason,
                silent=self.config.ag_silent_heal,
            )

    async def heal_now(self) -> dict[str, Any]:
        async with self._lock:
            client = self._mihomo_client()
            status = await client.get_status(preferred_group=self.config.mihomo_group)
            if not status.connected:
                self._add_event("warn", f"手动触发自愈失败：{status.error}")
                return {"ok": False, "message": status.error, "selected_node": None}

            ok, msg, best_node = await client.test_and_select_best(
                preferred_group=status.active_group or self.config.mihomo_group
            )
            self._add_event("ok" if ok else "warn", f"手动一键切换：{msg}")
            if ok:
                self.consecutive_failures = 0
                self.latest_report = await run_full_diagnostics(
                    self.config.proxy_url, custom_sites=self.config.custom_sites
                )
                self._record_ip_snapshot(self.latest_report)
                self.latest_mihomo = await client.get_status(preferred_group=self.config.mihomo_group)
            return {"ok": ok, "message": msg, "selected_node": best_node}

    async def switch_node(self, group: str, node: str) -> dict[str, Any]:
        async with self._lock:
            client = self._mihomo_client()
            ok, msg = await client.switch_node(group, node)
            self._add_event("ok" if ok else "warn", msg)
            if ok:
                self.latest_report = await run_full_diagnostics(
                    self.config.proxy_url, custom_sites=self.config.custom_sites
                )
                self._record_ip_snapshot(self.latest_report)
                self.latest_mihomo = await client.get_status(preferred_group=group)
            return {"ok": ok, "message": msg}

    async def test_group_delays(self, group: str | None = None) -> dict[str, Any]:
        client = self._mihomo_client()
        target_group = group or self.latest_mihomo.active_group or self.config.mihomo_group
        delays = await client.test_group_delays(group_name=target_group)
        return {"ok": bool(delays), "group": target_group, "delays": delays}

    async def get_connections(self) -> dict[str, Any]:
        client = self._mihomo_client()
        items = await client.get_connections(limit=25)
        return {"ok": True, "connections": items}

    async def _loop(self) -> None:
        self._add_event("info", "AI NetGuard v2.0 后台巡检服务已启动")
        while not self._stop_event.is_set():
            try:
                await self.check_once(trigger_auto_heal=True)
            except (OSError, RuntimeError, ValueError) as exc:
                self._add_event("warn", f"巡检过程异常：{exc}")
            interval = max(10, int(self.config.check_interval_sec))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
                pass

    def start_background(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop_background(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "report": self.latest_report.to_dict() if self.latest_report else None,
            "mihomo": asdict(self.latest_mihomo),
            "history": list(self.history),
            "events": list(self.events),
            "ip_history": list(self.ip_history),
            "custom_sites": list(self.config.custom_sites),
            "consecutive_failures": self.consecutive_failures,
            "antigravity_watchdog": self.ag_watchdog.status_dict(),
            "anti_ban": self.get_anti_ban_status(),
        }
