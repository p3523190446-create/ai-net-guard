"""Clash / Mihomo External Controller REST API client and auto-healing logic."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

INFO_NODE_RE = re.compile(
    r"剩余|流量|过期|到期|官网|网址|群|通知|维护|重置|套餐|距离|客服|返利|购买|试用|DIRECT|REJECT",
    re.IGNORECASE,
)
RESTRICTED_NODE_RE = re.compile(r"香港|港|HK|Hong\s*Kong|中国|CN|澳门|MO|Macau|俄|RU|Russia", re.IGNORECASE)
PREFERRED_NODE_RE = re.compile(
    r"日本|东京|大阪|JP|Japan|新加坡|狮城|SG|Singapore|台湾|台北|TW|Taiwan|美国|US|America|洛杉矶|硅谷|韩国|KR",
    re.IGNORECASE,
)

NODE_COUNTRY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"日本|东京|大阪|\bJP\b|Japan", re.IGNORECASE), "🇯🇵"),
    (re.compile(r"新加坡|狮城|\bSG\b|Singapore", re.IGNORECASE), "🇸🇬"),
    (re.compile(r"台湾|台北|\bTW\b|Taiwan", re.IGNORECASE), "🇹🇼"),
    (re.compile(r"美国|洛杉矶|硅谷|西雅图|纽约|\bUS\b|America|USA", re.IGNORECASE), "🇺🇸"),
    (re.compile(r"韩国|首尔|\bKR\b|Korea", re.IGNORECASE), "🇰🇷"),
    (re.compile(r"香港|港|\bHK\b|Hong\s*Kong", re.IGNORECASE), "🇭🇰"),
    (re.compile(r"英国|伦敦|\bUK\b|\bGB\b|Britain", re.IGNORECASE), "🇬🇧"),
    (re.compile(r"德国|法兰克福|\bDE\b|Germany", re.IGNORECASE), "🇩🇪"),
    (re.compile(r"法国|巴黎|\bFR\b|France", re.IGNORECASE), "🇫🇷"),
    (re.compile(r"加拿大|\bCA\b|Canada", re.IGNORECASE), "🇨🇦"),
    (re.compile(r"澳大利亚|澳洲|悉尼|\bAU\b|Australia", re.IGNORECASE), "🇦🇺"),
    (re.compile(r"印度|\bIN\b|India", re.IGNORECASE), "🇮🇳"),
    (re.compile(r"俄|莫斯科|\bRU\b|Russia", re.IGNORECASE), "🇷🇺"),
    (re.compile(r"中国|\bCN\b|China", re.IGNORECASE), "🇨🇳"),
]


def classify_node(name: str) -> tuple[str, str]:
    """Return (flag_emoji, tag) where tag is info|restricted|recommended|normal."""
    flag = "🌐"
    for pattern, emoji in NODE_COUNTRY_PATTERNS:
        if pattern.search(name):
            flag = emoji
            break

    if INFO_NODE_RE.search(name):
        tag = "info"
    elif RESTRICTED_NODE_RE.search(name):
        tag = "restricted"
    elif PREFERRED_NODE_RE.search(name):
        tag = "recommended"
    else:
        tag = "normal"
    return flag, tag


@dataclass
class MihomoStatus:
    connected: bool
    api_url: str = ""
    version: str = ""
    active_group: str = ""
    current_node: str = ""
    all_groups: list[str] | None = None
    group_nodes: list[str] | None = None
    node_items: list[dict[str, Any]] | None = None
    error: str = ""


def filter_clean_ai_nodes(names: list[str]) -> list[str]:
    """Exclude provider notice lines and AI-restricted regions; prioritize JP/SG/TW/US/KR."""
    usable = [n for n in names if isinstance(n, str) and n.strip() and not INFO_NODE_RE.search(n)]
    non_restricted = [n for n in usable if not RESTRICTED_NODE_RE.search(n)]
    pool = non_restricted if non_restricted else usable
    return sorted(pool, key=lambda n: (0 if PREFERRED_NODE_RE.search(n) else 1, n))


def is_meta_group(name: str, proxies: dict[str, Any]) -> bool:
    """Return True if *name* is a nested routing/meta group rather than an exit node/region."""
    meta = proxies.get(name)
    if not isinstance(meta, dict):
        return False
    ptype = str(meta.get("type") or "")
    if ptype in {"Selector", "Fallback"}:
        return True
    if ptype == "URLTest":
        return not any(pat.search(name) for pat, _ in NODE_COUNTRY_PATTERNS)
    return False


def _count_non_meta_nodes(proxies: dict[str, Any], group_name: str) -> int:
    meta = proxies.get(group_name)
    if not isinstance(meta, dict):
        return 0
    nodes = [n for n in (meta.get("all") or []) if isinstance(n, str) and n.strip() and not INFO_NODE_RE.search(n)]
    return sum(1 for n in nodes if not is_meta_group(n, proxies))


def resolve_effective_selector_group(proxies: dict[str, Any], start_group: str) -> str:
    """Penetrate nested meta Selector groups down to the leaf Selector holding actual exit nodes."""
    current = start_group
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        meta = proxies.get(current)
        if not isinstance(meta, dict):
            break
        if _count_non_meta_nodes(proxies, current) >= 2:
            return current

        now_target = str(meta.get("now") or "")
        now_meta = proxies.get(now_target) if now_target else None
        if isinstance(now_meta, dict) and now_meta.get("type") == "Selector" and now_target not in visited:
            current = now_target
            continue

        child_selectors = [
            n
            for n in (meta.get("all") or [])
            if isinstance(n, str)
            and n not in visited
            and isinstance(proxies.get(n), dict)
            and proxies[n].get("type") == "Selector"
        ]
        if not child_selectors:
            break
        child_selectors.sort(key=lambda g: _count_non_meta_nodes(proxies, g), reverse=True)
        current = child_selectors[0]
    return current


class MihomoClient:
    """Async REST client for Mihomo / Clash Meta External Controller."""

    def __init__(self, api_url: str = "http://127.0.0.1:9097", secret: str = "") -> None:
        self.api_url = api_url.rstrip("/")
        self.secret = secret

    def _headers(self) -> dict[str, str]:
        if self.secret:
            return {"Authorization": f"Bearer {self.secret}"}
        return {}

    async def probe_endpoint(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=2.0) as client:
                resp = await client.get(f"{url.rstrip('/')}/version", headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return str(data.get("version") or "unknown")
        except (httpx.HTTPError, OSError, ValueError):
            return None
        return None

    async def resolve_api_url(self) -> str | None:
        candidates = [self.api_url, "http://127.0.0.1:9097", "http://127.0.0.1:9090"]
        seen: set[str] = set()
        for url in candidates:
            clean = url.rstrip("/")
            if not clean or clean in seen:
                continue
            seen.add(clean)
            if await self.probe_endpoint(clean) is not None:
                self.api_url = clean
                return clean
        return None

    async def fetch_proxies_map(self) -> dict[str, Any]:
        """Fetch raw Mihomo /proxies mapping; returns empty dict on failure."""
        base = self.api_url.rstrip("/")
        if not base:
            return {}
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=2.0) as client:
                resp = await client.get(f"{base}/proxies", headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json().get("proxies")
                    return data if isinstance(data, dict) else {}
        except (httpx.HTTPError, OSError, ValueError):
            return {}
        return {}

    async def get_status(self, preferred_group: str = "🤖 AI-稳定池") -> MihomoStatus:
        resolved = await self.resolve_api_url()
        if not resolved:
            return MihomoStatus(
                connected=False,
                api_url=self.api_url,
                error="未检测到 Clash/Mihomo 控制端口 (9097/9090)；若仍在使用小火箭，请切换到 Clash Verge Rev 开启高级自愈。",
            )

        version = await self.probe_endpoint(resolved) or ""
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=4.0) as client:
                resp = await client.get(f"{resolved}/proxies", headers=self._headers())
                resp.raise_for_status()
                proxies: dict[str, Any] = resp.json().get("proxies", {})
        except (httpx.HTTPError, OSError, ValueError) as exc:
            return MihomoStatus(connected=False, api_url=resolved, error=f"读取代理组失败: {exc}")

        selectable_types = {"Selector", "Fallback", "URLTest"}
        groups = [
            name
            for name, meta in proxies.items()
            if isinstance(meta, dict) and meta.get("type") in selectable_types and name != "GLOBAL"
        ]

        active_group = ""
        if preferred_group in proxies:
            active_group = preferred_group
        else:
            for hint in ("AI", "稳定", "Proxy", "节点", "选择", "海外"):
                match = next((g for g in groups if hint.lower() in g.lower()), "")
                if match:
                    active_group = match
                    break
            if not active_group and groups:
                active_group = groups[0]

        if active_group:
            active_group = resolve_effective_selector_group(proxies, active_group)

        group_meta = proxies.get(active_group, {}) if active_group else {}
        raw_nodes = list(group_meta.get("all") or [])
        current_node = str(group_meta.get("now") or "")
        node_items = []
        for n in raw_nodes:
            flag, tag = classify_node(n)
            node_items.append({"name": n, "flag": flag, "tag": tag, "is_current": n == current_node})

        return MihomoStatus(
            connected=True,
            api_url=resolved,
            version=version,
            active_group=active_group,
            current_node=current_node,
            all_groups=groups,
            group_nodes=raw_nodes,
            node_items=node_items,
        )

    async def test_node_delay(
        self,
        node_name: str,
        test_url: str = "https://www.google.com/generate_204",
        timeout_ms: int = 4000,
    ) -> int | None:
        encoded = quote(node_name, safe="")
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=timeout_ms / 1000.0 + 1.5) as client:
                resp = await client.get(
                    f"{self.api_url}/proxies/{encoded}/delay",
                    params={"timeout": str(timeout_ms), "url": test_url},
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    delay = int(resp.json().get("delay") or 0)
                    return delay if delay > 0 else None
        except (httpx.HTTPError, OSError, ValueError):
            return None
        return None

    async def test_group_delays(self, group_name: str = "🤖 AI-稳定池", limit: int = 24) -> dict[str, int | None]:
        status = await self.get_status(preferred_group=group_name)
        if not status.connected:
            return {}
        nodes = [n for n in (status.group_nodes or []) if not INFO_NODE_RE.search(n)][:limit]
        if not nodes:
            return {}
        delays = await asyncio.gather(*(self.test_node_delay(n) for n in nodes))
        return dict(zip(nodes, delays))

    async def get_connections(self, limit: int = 25) -> list[dict[str, Any]]:
        resolved = await self.resolve_api_url()
        if not resolved:
            return []
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=3.0) as client:
                resp = await client.get(f"{resolved}/connections", headers=self._headers())
                if resp.status_code != 200:
                    return []
                conns = resp.json().get("connections") or []
        except (httpx.HTTPError, OSError, ValueError):
            return []

        items: list[dict[str, Any]] = []
        for c in conns[:limit]:
            meta = c.get("metadata") or {}
            host = meta.get("host") or meta.get("destinationIP") or ""
            items.append(
                {
                    "host": host,
                    "process": meta.get("process") or meta.get("processPath") or "未知进程",
                    "rule": c.get("rule") or "",
                    "chains": list(c.get("chains") or []),
                    "upload": int(c.get("upload") or 0),
                    "download": int(c.get("download") or 0),
                }
            )
        return items

    async def switch_node(self, group_name: str, node_name: str) -> tuple[bool, str]:
        resolved = await self.resolve_api_url()
        if not resolved:
            return False, "Mihomo 控制接口未连接"
        encoded_group = quote(group_name, safe="")
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=4.0) as client:
                resp = await client.put(
                    f"{resolved}/proxies/{encoded_group}",
                    json={"name": node_name},
                    headers=self._headers(),
                )
                if resp.status_code in (200, 204):
                    return True, f"已将 [{group_name}] 切换至节点: {node_name}"
                return False, f"切换失败 HTTP {resp.status_code}: {resp.text}"
        except (httpx.HTTPError, OSError) as exc:
            return False, f"切换请求异常: {exc}"

    async def test_and_select_best(
        self,
        preferred_group: str = "🤖 AI-稳定池",
        max_candidates: int = 12,
    ) -> tuple[bool, str, str | None]:
        """Probe candidate AI nodes concurrently and switch to the lowest-latency healthy one."""
        status = await self.get_status(preferred_group=preferred_group)
        if not status.connected or not status.active_group:
            return False, status.error or "未找到可切换的代理组", None

        raw_nodes = status.group_nodes or []
        proxies_map = await self.fetch_proxies_map()
        candidates = filter_clean_ai_nodes(raw_nodes)
        if proxies_map:
            non_meta = [n for n in candidates if not is_meta_group(n, proxies_map)]
            if non_meta:
                candidates = non_meta
        if status.current_node in candidates and len(candidates) > 1:
            candidates = [n for n in candidates if n != status.current_node]

        candidates = candidates[:max_candidates]
        if not candidates:
            return False, f"代理组 [{status.active_group}] 中没有可用的非受限候选节点", None

        delays = await asyncio.gather(
            *(self.test_node_delay(node, test_url="https://antigravity.google") for node in candidates)
        )
        healthy = [(node, delay) for node, delay in zip(candidates, delays) if delay is not None and delay > 0]
        if not healthy:
            delays = await asyncio.gather(
                *(self.test_node_delay(node, test_url="https://www.google.com/generate_204") for node in candidates)
            )
            healthy = [(node, delay) for node, delay in zip(candidates, delays) if delay is not None and delay > 0]
        if not healthy:
            return False, f"测试了 {len(candidates)} 个候选节点，全部超时无响应", None

        healthy.sort(key=lambda item: item[1])
        best_node, best_delay = healthy[0]
        ok, msg = await self.switch_node(status.active_group, best_node)
        if ok:
            return True, f"{msg}（测速 {best_delay} ms）", best_node
        return False, msg, None
