"""Five-stage AI network diagnostic engine, dual-perspective IP check, and DNS leak detector."""

from __future__ import annotations

import asyncio
import datetime
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

RESTRICTED_AI_REGIONS = {"HK", "CN", "MO", "RU", "BY", "KP", "IR"}

DNS_PROBE_DOMAINS = [
    ("api.deepseek.com", "DeepSeek 国内 API"),
    ("www.baidu.com", "百度国内基准"),
    ("www.google.com", "Google 海外基准"),
    ("api.openai.com", "OpenAI / Codex API"),
]

PROBE_SPECS: list[dict[str, Any]] = [
    # Overseas AI
    {
        "key": "antigravity",
        "label": "Antigravity 编辑器",
        "group": "overseas_ai",
        "icon": "antigravity",
        "mode": "proxy",
        "method": "HEAD",
        "url": "https://antigravity.google",
        "valid_codes": {200, 301, 302, 308},
        "warn_ms": 2000,
    },
    {
        "key": "gemini_api",
        "label": "Gemini / Google AI",
        "group": "overseas_ai",
        "icon": "gemini",
        "mode": "proxy",
        "method": "GET",
        "url": "https://generativelanguage.googleapis.com",
        "valid_codes": {200, 301, 302, 401, 404},
        "warn_ms": 1800,
    },
    {
        "key": "openai_api",
        "label": "OpenAI / Codex",
        "group": "overseas_ai",
        "icon": "openai",
        "mode": "proxy",
        "method": "GET",
        "url": "https://api.openai.com/v1/models",
        "valid_codes": {200, 401},
        "warn_ms": 1800,
    },
    {
        "key": "claude",
        "label": "Claude / Anthropic",
        "group": "overseas_ai",
        "icon": "claude",
        "mode": "proxy",
        "method": "GET",
        "url": "https://api.anthropic.com",
        "valid_codes": {200, 401, 404},
        "warn_ms": 1800,
    },
    {
        "key": "cursor",
        "label": "Cursor 官网/服务",
        "group": "overseas_ai",
        "icon": "cursor",
        "mode": "proxy",
        "method": "HEAD",
        "url": "https://cursor.com",
        "valid_codes": {200, 301, 302, 308},
        "warn_ms": 2000,
    },
    {
        "key": "github",
        "label": "GitHub 代码托管",
        "group": "overseas_ai",
        "icon": "github",
        "mode": "proxy",
        "method": "HEAD",
        "url": "https://github.com",
        "valid_codes": {200, 301, 302},
        "warn_ms": 2200,
    },
    # Domestic AI (direct)
    {
        "key": "deepseek_direct",
        "label": "DeepSeek（直连）",
        "group": "domestic_ai",
        "icon": "deepseek",
        "mode": "direct",
        "method": "GET",
        "url": "https://api.deepseek.com",
        "valid_codes": {200, 301, 302, 401, 403, 404, 405},
        "warn_ms": 1500,
    },
    {
        "key": "kimi_direct",
        "label": "Kimi 月之暗面（直连）",
        "group": "domestic_ai",
        "icon": "kimi",
        "mode": "direct",
        "method": "HEAD",
        "url": "https://kimi.moonshot.cn",
        "valid_codes": {200, 301, 302, 308},
        "warn_ms": 1500,
    },
    {
        "key": "doubao_direct",
        "label": "豆包 Doubao（直连）",
        "group": "domestic_ai",
        "icon": "doubao",
        "mode": "direct",
        "method": "HEAD",
        "url": "https://www.doubao.com",
        "valid_codes": {200, 301, 302, 308},
        "warn_ms": 1500,
    },
    {
        "key": "qwen_direct",
        "label": "通义千问 百炼（直连）",
        "group": "domestic_ai",
        "icon": "qwen",
        "mode": "direct",
        "method": "GET",
        "url": "https://dashscope.aliyuncs.com",
        "valid_codes": {200, 301, 302, 401, 403, 404},
        "warn_ms": 1500,
    },
    # Dev & Overseas Web
    {
        "key": "google_204",
        "label": "Google 搜索基础",
        "group": "dev_web",
        "icon": "google",
        "mode": "proxy",
        "method": "GET",
        "url": "https://www.google.com/generate_204",
        "valid_codes": {204, 200},
        "warn_ms": 1500,
    },
    {
        "key": "youtube",
        "label": "YouTube 视频",
        "group": "dev_web",
        "icon": "youtube",
        "mode": "proxy",
        "method": "GET",
        "url": "https://www.youtube.com/generate_204",
        "valid_codes": {204, 200},
        "warn_ms": 2000,
    },
    {
        "key": "npm_registry",
        "label": "npm 前端包源",
        "group": "dev_web",
        "icon": "npm",
        "mode": "proxy",
        "method": "GET",
        "url": "https://registry.npmjs.org/-/ping",
        "valid_codes": {200},
        "warn_ms": 1800,
    },
    {
        "key": "pypi",
        "label": "PyPI Python 包源",
        "group": "dev_web",
        "icon": "pypi",
        "mode": "proxy",
        "method": "HEAD",
        "url": "https://pypi.org/simple/",
        "valid_codes": {200, 301, 302},
        "warn_ms": 2000,
    },
]


def country_code_to_flag(code: str | None) -> str:
    """Convert ISO 3166-1 alpha-2 country code (e.g. 'JP') to flag emoji."""
    if not code:
        return "🌐"
    clean = code.strip().upper()
    if len(clean) == 2 and clean.isascii() and clean.isalpha():
        return chr(0x1F1E6 + ord(clean[0]) - ord("A")) + chr(0x1F1E6 + ord(clean[1]) - ord("A"))
    return "🌐"


@dataclass
class LocalNetworkInfo:
    interface: str = ""
    lan_ip: str = ""


@dataclass
class DnsCheckItem:
    domain: str
    label: str
    resolved_ip: str
    ip_type: str  # "real_ip" | "fake_ip" | "error"
    note: str


@dataclass
class DomesticIpInfo:
    reachable: bool = False
    ip: str = ""
    flag: str = "🇨🇳"
    country: str = ""
    region: str = ""
    city: str = ""
    isp: str = ""
    error: str = ""


@dataclass
class ProxyState:
    http_enabled: bool = False
    http_host: str = ""
    http_port: int = 0
    https_enabled: bool = False
    https_host: str = ""
    https_port: int = 0
    socks_enabled: bool = False
    socks_host: str = ""
    socks_port: int = 0
    effective_proxy_url: str | None = None
    port_listening: bool = False
    dns_servers: list[str] = field(default_factory=list)
    fake_ip_dns: bool = False
    running_apps: list[str] = field(default_factory=list)
    shadowrocket_active: bool = False
    clash_verge_active: bool = False


@dataclass
class ExitCheck:
    reachable: bool = False
    ip_1: str = ""
    ip_2: str = ""
    loc: str = ""
    colo: str = ""
    ip_changed: bool = False
    restricted_region: bool = False
    error: str = ""
    flag: str = "🌐"
    country: str = ""
    region: str = ""
    city: str = ""
    isp: str = ""
    org: str = ""
    asn: str = ""
    is_hosting: bool = False
    is_proxy: bool = False
    ip_attr_label: str = ""
    ai_grade: str = "C"
    ai_grade_reason: str = ""


@dataclass
class ProbeResult:
    key: str
    label: str
    mode: str  # "direct" | "proxy"
    url: str
    ok: bool
    status: str  # "ok" | "warn" | "fail"
    http_code: int = 0
    latency_ms: int = 0
    reason: str = ""
    group: str = "overseas_ai"
    icon: str = "globe"


@dataclass
class Finding:
    level: str  # "critical" | "warn" | "info" | "ok"
    title: str
    detail: str
    action: str


@dataclass
class DiagnosticReport:
    timestamp: str
    overall_status: str  # "healthy" | "degraded" | "broken"
    summary: str
    proxy_state: ProxyState
    exit_check: ExitCheck
    probes: list[ProbeResult]
    cards: dict[str, dict[str, str]]
    findings: list[Finding]
    local_network: LocalNetworkInfo = field(default_factory=LocalNetworkInfo)
    dns_checks: list[DnsCheckItem] = field(default_factory=list)
    domestic_ip: DomesticIpInfo = field(default_factory=DomesticIpInfo)
    pipeline: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_scutil_proxy(output: str) -> ProxyState:
    """Parse macOS `scutil --proxy` output into ProxyState."""
    state = ProxyState()

    def _get(key: str) -> str:
        m = re.search(rf"^\s*{re.escape(key)}\s*:\s*(.+)$", output, re.MULTILINE)
        return m.group(1).strip() if m else ""

    state.http_enabled = _get("HTTPEnable") == "1"
    state.http_host = _get("HTTPProxy")
    state.http_port = int(_get("HTTPPort") or 0) if _get("HTTPPort").isdigit() else 0

    state.https_enabled = _get("HTTPSEnable") == "1"
    state.https_host = _get("HTTPProxy") or _get("HTTPSProxy")
    state.https_port = int(_get("HTTPSPort") or 0) if _get("HTTPSPort").isdigit() else 0

    state.socks_enabled = _get("SOCKSEnable") == "1"
    state.socks_host = _get("SOCKSProxy")
    state.socks_port = int(_get("SOCKSPort") or 0) if _get("SOCKSPort").isdigit() else 0

    if state.http_enabled and state.http_host and state.http_port:
        state.effective_proxy_url = f"http://{state.http_host}:{state.http_port}"
    elif state.https_enabled and state.https_host and state.https_port:
        state.effective_proxy_url = f"http://{state.https_host}:{state.https_port}"
    elif state.socks_enabled and state.socks_host and state.socks_port:
        state.effective_proxy_url = f"socks5://{state.socks_host}:{state.socks_port}"

    return state


def parse_scutil_dns(output: str) -> tuple[list[str], bool]:
    """Extract unique nameservers from `scutil --dns` and detect Fake-IP DNS (198.18.0.x)."""
    servers: list[str] = []
    for match in re.finditer(r"nameserver\[\d+\]\s*:\s*(\S+)", output):
        ip = match.group(1).strip()
        if ip not in servers:
            servers.append(ip)
    fake_ip = any(s.startswith(("198.18.", "198.19.")) for s in servers)
    return servers, fake_ip


def detect_proxy_processes(ps_output: str) -> tuple[list[str], bool, bool]:
    """Detect running proxy apps/cores from `ps` output."""
    patterns = [
        ("Shadowrocket", re.compile(r"Shadowrocket\.app|MacPacketTunnel\.appex")),
        ("Clash Verge", re.compile(r"Clash Verge\.app|verge-mihomo")),
        ("Mihomo/Clash", re.compile(r"(?:^|/)(?:mihomo|clash-meta|clash)(?:\s|$)")),
        ("FlClash", re.compile(r"FlClash\.app|FlClashCore")),
        ("Flycc", re.compile(r"Flycc\.app")),
        ("Sing-box", re.compile(r"(?:^|/)sing-box(?:\s|$)")),
    ]
    found: list[str] = []
    for line in ps_output.splitlines():
        for label, regex in patterns:
            if regex.search(line) and label not in found:
                found.append(label)
    shadowrocket_active = "Shadowrocket" in found
    clash_verge_active = "Clash Verge" in found or "Mihomo/Clash" in found
    return found, shadowrocket_active, clash_verge_active


def check_tcp_port(host: str, port: int, timeout: float = 1.5) -> bool:
    if not host or port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run_cmd(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def collect_local_network_info() -> LocalNetworkInfo:
    """Detect active macOS network interface (e.g. en0) and local LAN IP."""
    route_out = _run_cmd(["route", "-n", "get", "default"])
    iface_match = re.search(r"interface:\s*(\S+)", route_out)
    iface = iface_match.group(1) if iface_match else ""
    lan_ip = ""
    if iface:
        lan_ip = _run_cmd(["ipconfig", "getifaddr", iface]).strip()
    if not lan_ip:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("223.5.5.5", 53))
                lan_ip = s.getsockname()[0]
        except OSError:
            lan_ip = ""
    return LocalNetworkInfo(interface=iface or "未知", lan_ip=lan_ip)


def collect_local_proxy_state(configured_proxy_url: str = "auto") -> ProxyState:
    """Gather L1 local proxy, TCP port, DNS, and running process state."""
    scutil_proxy_out = _run_cmd(["scutil", "--proxy"])
    scutil_dns_out = _run_cmd(["scutil", "--dns"])
    ps_out = _run_cmd(["ps", "-eo", "command"])

    state = parse_scutil_proxy(scutil_proxy_out)
    dns_servers, fake_ip = parse_scutil_dns(scutil_dns_out)
    apps, sr_active, cv_active = detect_proxy_processes(ps_out)

    state.dns_servers = dns_servers
    state.fake_ip_dns = fake_ip
    state.running_apps = apps
    state.shadowrocket_active = sr_active
    state.clash_verge_active = cv_active

    if configured_proxy_url and configured_proxy_url != "auto":
        state.effective_proxy_url = configured_proxy_url

    if state.effective_proxy_url:
        m = re.match(r"^[a-z0-9]+://([^:/]+):(\d+)", state.effective_proxy_url)
        if m:
            state.port_listening = check_tcp_port(m.group(1), int(m.group(2)))
    elif state.http_host and state.http_port:
        state.port_listening = check_tcp_port(state.http_host, state.http_port)

    return state


def _resolve_one_domain(domain: str, label: str) -> DnsCheckItem:
    try:
        infos = socket.getaddrinfo(domain, 443, socket.AF_INET, socket.SOCK_STREAM)
        ip = str(infos[0][4][0]) if infos else ""
        if ip.startswith(("198.18.", "198.19.")):
            return DnsCheckItem(
                domain=domain,
                label=label,
                resolved_ip=ip,
                ip_type="fake_ip",
                note="被代理虚拟 DNS 劫持（198.18.x Fake-IP）",
            )
        return DnsCheckItem(
            domain=domain,
            label=label,
            resolved_ip=ip,
            ip_type="real_ip",
            note="正常真实 IP 解析",
        )
    except OSError as exc:
        return DnsCheckItem(
            domain=domain,
            label=label,
            resolved_ip="",
            ip_type="error",
            note=f"解析失败：{exc}",
        )


async def collect_dns_checks() -> list[DnsCheckItem]:
    return list(
        await asyncio.gather(*(asyncio.to_thread(_resolve_one_domain, dom, lbl) for dom, lbl in DNS_PROBE_DOMAINS))
    )


def parse_cf_trace(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


async def probe_domestic_ip() -> DomesticIpInfo:
    """Query user's real domestic IP without proxy."""
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
            resp = await client.get("https://myip.ipip.net/json")
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data", {})
                loc = data.get("location") or []
                return DomesticIpInfo(
                    reachable=True,
                    ip=str(data.get("ip") or ""),
                    flag="🇨🇳",
                    country=loc[0] if len(loc) > 0 else "中国",
                    region=loc[1] if len(loc) > 1 else "",
                    city=loc[2] if len(loc) > 2 else "",
                    isp=loc[4] if len(loc) > 4 else "",
                )
    except (httpx.HTTPError, OSError, ValueError):
        pass

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5.0) as client:
            resp = await client.get("http://cip.cc", headers={"User-Agent": "curl/8.0"})
            if resp.status_code == 200:
                ip_m = re.search(r"IP\s*:\s*(\S+)", resp.text)
                addr_m = re.search(r"地址\s*:\s*(.+)", resp.text)
                isp_m = re.search(r"运营商\s*:\s*(.+)", resp.text)
                parts = (addr_m.group(1).strip().split() if addr_m else [])
                return DomesticIpInfo(
                    reachable=bool(ip_m),
                    ip=ip_m.group(1) if ip_m else "",
                    flag="🇨🇳",
                    country=parts[0] if len(parts) > 0 else "中国",
                    region=parts[1] if len(parts) > 1 else "",
                    city=parts[2] if len(parts) > 2 else "",
                    isp=isp_m.group(1).strip() if isp_m else "",
                )
    except (httpx.HTTPError, OSError) as exc:
        return DomesticIpInfo(reachable=False, error=str(exc) or exc.__class__.__name__)

    return DomesticIpInfo(reachable=False, error="无法获取国内直连出口 IP")


def compute_exit_grade(
    reachable: bool,
    loc: str,
    ip_changed: bool,
    is_hosting: bool,
    is_proxy: bool,
) -> tuple[str, str, str]:
    """Return (ip_attr_label, ai_grade, ai_grade_reason)."""
    if not reachable:
        return ("❌ 无法连接", "F", "代理出口无法访问测试接口")

    if is_proxy:
        attr = "⚠️ 已被标记为代理/高风险 IP"
    elif is_hosting:
        attr = "🏢 机房数据中心 IP（部分 AI 偶发人机验证）"
    else:
        attr = "🏠 住宅/宽带原生 IP（纯净度高）"

    if loc in RESTRICTED_AI_REGIONS:
        return (attr, "F", f"出口位于 AI 受限地区 ({loc})，会被直接拒绝")
    if ip_changed:
        return (attr, "C", "短时间出口 IP 发生跳变，长对话极易中断并触发倒计时重试")
    if is_proxy and is_hosting:
        return (attr, "C", "被风控库标记为数据中心代理，易出现 403/421")
    if is_hosting or is_proxy:
        return (attr, "B", "地区合规且 IP 固定，但属于机房/共享出口，偶发风控校验")
    if loc in {"JP", "SG", "TW", "US", "KR", "DE", "GB"}:
        return (attr, "S", "首选稳定地区 + 原生宽带纯净出口 + IP 固定不跳")
    return (attr, "A", "地区合规且出口 IP 稳定")


async def probe_exit_stability(proxy_url: str | None) -> ExitCheck:
    """Query proxy exit IP geolocation, ISP/ASN purity, and run a second hop check."""
    if not proxy_url:
        return ExitCheck(reachable=False, error="未配置代理端口", ai_grade="F", ai_grade_reason="未配置代理端口")

    geo: dict[str, Any] = {}
    ip_1 = ""
    last_err = ""

    # Source 1: ip-api.com (fast + Chinese region + hosting/proxy flag)
    try:
        async with httpx.AsyncClient(proxy=proxy_url, trust_env=False, timeout=6.0) as client:
            resp = await client.get(
                "http://ip-api.com/json/?lang=zh-CN&fields=status,country,countryCode,regionName,city,isp,org,as,hosting,proxy,query",
                headers={"Connection": "close"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    geo = data
                    ip_1 = str(data.get("query") or "")
    except (httpx.HTTPError, OSError, ValueError) as exc:
        last_err = str(exc) or exc.__class__.__name__

    # Fallback source: ipwho.is
    if not ip_1:
        try:
            async with httpx.AsyncClient(proxy=proxy_url, trust_env=False, timeout=6.0) as client:
                resp = await client.get("https://ipwho.is/", headers={"Connection": "close"})
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        conn = data.get("connection") or {}
                        geo = {
                            "country": data.get("country") or "",
                            "countryCode": data.get("country_code") or "",
                            "regionName": data.get("region") or "",
                            "city": data.get("city") or "",
                            "isp": conn.get("isp") or "",
                            "org": conn.get("org") or "",
                            "as": f"AS{conn.get('asn')}" if conn.get("asn") else "",
                            "hosting": (data.get("type") or "").lower() == "hosting",
                            "proxy": False,
                        }
                        ip_1 = str(data.get("ip") or "")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            last_err = str(exc) or exc.__class__.__name__

    # Second independent connection to Cloudflare trace to check IP stability & colo
    trace: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(proxy=proxy_url, trust_env=False, timeout=6.0) as client:
            resp = await client.get("https://cloudflare.com/cdn-cgi/trace", headers={"Connection": "close"})
            if resp.status_code == 200:
                trace = parse_cf_trace(resp.text)
    except (httpx.HTTPError, OSError) as exc:
        if not last_err:
            last_err = str(exc) or exc.__class__.__name__

    ip_2 = trace.get("ip", "")
    if not ip_1 and not ip_2:
        return ExitCheck(
            reachable=False,
            error=last_err or "无法获取代理出口 IP",
            ai_grade="F",
            ai_grade_reason="代理出口无法连通测试节点",
        )

    if not ip_1:
        ip_1 = ip_2
    if not ip_2:
        ip_2 = ip_1

    loc = (str(geo.get("countryCode") or trace.get("loc") or "")).upper()
    colo = trace.get("colo", "")
    ip_changed = bool(ip_1 and ip_2 and ip_1 != ip_2)
    restricted = loc in RESTRICTED_AI_REGIONS
    is_hosting = bool(geo.get("hosting", False))
    is_proxy = bool(geo.get("proxy", False))
    attr_label, grade, grade_reason = compute_exit_grade(True, loc, ip_changed, is_hosting, is_proxy)

    return ExitCheck(
        reachable=True,
        ip_1=ip_1,
        ip_2=ip_2,
        loc=loc,
        colo=colo,
        ip_changed=ip_changed,
        restricted_region=restricted,
        error="",
        flag=country_code_to_flag(loc),
        country=str(geo.get("country") or loc),
        region=str(geo.get("regionName") or ""),
        city=str(geo.get("city") or ""),
        isp=str(geo.get("isp") or ""),
        org=str(geo.get("org") or ""),
        asn=str(geo.get("as") or ""),
        is_hosting=is_hosting,
        is_proxy=is_proxy,
        ip_attr_label=attr_label,
        ai_grade=grade,
        ai_grade_reason=grade_reason,
    )


async def probe_single_target(spec: dict[str, Any], proxy_url: str | None) -> ProbeResult:
    mode = spec.get("mode", "proxy")
    method = str(spec.get("method", "GET")).upper()
    url = str(spec["url"])
    group = str(spec.get("group", "custom"))
    icon = str(spec.get("icon", "globe"))
    valid_codes = set(spec.get("valid_codes", {200, 204, 301, 302, 308}))
    warn_ms = int(spec.get("warn_ms", 2000))

    if mode == "proxy" and not proxy_url:
        return ProbeResult(
            key=spec["key"],
            label=spec["label"],
            mode=mode,
            url=url,
            ok=False,
            status="fail",
            reason="系统未开启代理端口",
            group=group,
            icon=icon,
        )

    client_kwargs: dict[str, Any] = {"trust_env": False, "timeout": 8.0, "follow_redirects": False}
    if mode == "proxy":
        client_kwargs["proxy"] = proxy_url

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.request(method, url)
        latency_ms = int((time.monotonic() - t0) * 1000)
        code = resp.status_code
        valid = code in valid_codes

        if not valid:
            reason = f"异常状态码 HTTP {code}"
            if spec["key"] == "openai_api" and code == 421:
                reason = "HTTP 421（节点 IP 共享或被风控拦截）"
            elif code == 403:
                reason = "HTTP 403（当前节点地区或 IP 被拒）"
            return ProbeResult(
                key=spec["key"],
                label=spec["label"],
                mode=mode,
                url=url,
                ok=False,
                status="fail",
                http_code=code,
                latency_ms=latency_ms,
                reason=reason,
                group=group,
                icon=icon,
            )

        if latency_ms > warn_ms:
            return ProbeResult(
                key=spec["key"],
                label=spec["label"],
                mode=mode,
                url=url,
                ok=True,
                status="warn",
                http_code=code,
                latency_ms=latency_ms,
                reason=f"延迟偏高 ({latency_ms} ms)",
                group=group,
                icon=icon,
            )

        return ProbeResult(
            key=spec["key"],
            label=spec["label"],
            mode=mode,
            url=url,
            ok=True,
            status="ok",
            http_code=code,
            latency_ms=latency_ms,
            reason=f"正常 ({latency_ms} ms)",
            group=group,
            icon=icon,
        )
    except (httpx.HTTPError, OSError) as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        msg = str(exc) or exc.__class__.__name__
        if "timed out" in msg.lower() or "timeout" in msg.lower():
            msg = f"连接超时 (>{latency_ms} ms)"
        return ProbeResult(
            key=spec["key"],
            label=spec["label"],
            mode=mode,
            url=url,
            ok=False,
            status="fail",
            latency_ms=latency_ms,
            reason=msg,
            group=group,
            icon=icon,
        )


def _build_custom_specs(custom_sites: list[dict[str, str]] | None) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for idx, site in enumerate(custom_sites or []):
        url = (site.get("url") or "").strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        specs.append(
            {
                "key": f"custom_{idx}",
                "label": site.get("name") or url,
                "group": "custom",
                "icon": "globe",
                "mode": "direct" if site.get("mode") == "direct" else "proxy",
                "method": "HEAD",
                "url": url,
                "valid_codes": {200, 204, 301, 302, 307, 308, 401, 403, 404, 405},
                "warn_ms": 2500,
            }
        )
    return specs


def evaluate_report(
    proxy_state: ProxyState,
    exit_check: ExitCheck,
    probes: list[ProbeResult],
    timestamp: str | None = None,
    local_network: LocalNetworkInfo | None = None,
    dns_checks: list[DnsCheckItem] | None = None,
    domestic_ip: DomesticIpInfo | None = None,
) -> DiagnosticReport:
    """Turn raw L1-L5 telemetry into 5-stage pipeline, cards, and plain-Chinese findings."""
    ts = timestamp or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    by_key = {p.key: p for p in probes}
    findings: list[Finding] = []
    local_net = local_network or LocalNetworkInfo()
    dns_list = dns_checks or []
    dom_ip = domestic_ip or DomesticIpInfo()

    proxy_probes = [p for p in probes if p.mode == "proxy"]
    proxy_forward_blocked = (
        bool(proxy_state.effective_proxy_url)
        and proxy_state.port_listening
        and not exit_check.reachable
        and bool(proxy_probes)
        and all(not p.ok for p in proxy_probes)
    )

    # 1. Proxy layer card
    if proxy_state.effective_proxy_url and not proxy_state.port_listening:
        proxy_card = {
            "status": "fail",
            "headline": "假连接：系统代理已开，但后端端口没通",
            "subtext": f"端口 {proxy_state.effective_proxy_url} 无响应",
        }
        findings.append(
            Finding(
                level="critical",
                title="发现“假连接/死端口”：界面显示连接，实际代理后端没起来",
                detail=f"macOS 系统代理指向 {proxy_state.effective_proxy_url}，但本机探测该端口根本没有程序在监听。",
                action="请重新打开代理软件的开关，或直接切换到 Clash Verge Rev 接管端口。",
            )
        )
    elif proxy_forward_blocked:
        proxy_card = {
            "status": "fail",
            "headline": "后端堵死：端口开着但无法转发外网",
            "subtext": f"{proxy_state.effective_proxy_url} 转发全部失败",
        }
        findings.append(
            Finding(
                level="critical",
                title="发现“后端假死”：端口开着，但数据发不出去",
                detail=f"系统代理端口 {proxy_state.effective_proxy_url} 虽然处于监听状态，但所有海外测试全部失败，说明梯子内核或当前节点已卡死。",
                action="立即切换到 Clash Verge Rev 或点击「一键切换最优 AI 节点」。",
            )
        )
    elif not proxy_state.effective_proxy_url:
        proxy_card = {
            "status": "warn",
            "headline": "未开启系统代理",
            "subtext": "当前所有流量走直连",
        }
        findings.append(
            Finding(
                level="warn",
                title="系统代理未开启",
                detail="当前 macOS 未启用 HTTP/HTTPS 系统代理，国外 AI 编辑器可能无法直连。",
                action="打开 Clash Verge Rev 并开启系统代理或 TUN 模式。",
            )
        )
    elif proxy_state.shadowrocket_active:
        proxy_card = {
            "status": "warn",
            "headline": "Shadowrocket 运行中（易发 Fake-IP 假死）",
            "subtext": f"端口 {proxy_state.effective_proxy_url} 在线",
        }
        findings.append(
            Finding(
                level="warn",
                title="当前使用的是 iOS 移植版 Shadowrocket（Mac 挂机容易整体卡死）",
                detail=(
                    "检测到 Shadowrocket (MacPacketTunnel) 正在接管系统代理和 Fake-IP DNS (198.18.0.2)。"
                    "在 Mac 上跑长时间 AI 流式连接时容易静默假死，一旦假死会连带国内 DeepSeek 一起卡住。"
                ),
                action="建议按控制台「Clash / 代理控制中心」里的 3 步指南切换到已装好的原生 Clash Verge Rev。",
            )
        )
    else:
        apps_text = "、".join(proxy_state.running_apps) if proxy_state.running_apps else "代理运行中"
        proxy_card = {
            "status": "ok",
            "headline": f"代理层正常 ({apps_text})",
            "subtext": f"{proxy_state.effective_proxy_url}",
        }

    if len(proxy_state.running_apps) > 1:
        findings.append(
            Finding(
                level="warn",
                title=f"同时运行了多个代理软件：{', '.join(proxy_state.running_apps)}",
                detail="多个代理工具同时接管系统代理或虚拟网卡会互相抢端口与 DNS，导致一会儿通一会儿断。",
                action="只保留一个主力代理（推荐 Clash Verge Rev），退出其余代理软件。",
            )
        )

    # 2. Domestic AI card
    ds = by_key.get("deepseek_direct")
    if ds and ds.ok:
        ds_sub = f"直连响应 {ds.latency_ms} ms"
        if proxy_state.fake_ip_dns:
            ds_sub += " · 注意：当前 DNS 仍被 198.18.0.2 托管"
        domestic_card = {
            "status": "warn" if proxy_state.fake_ip_dns else "ok",
            "headline": "国内 DeepSeek 当前可达" if not proxy_state.fake_ip_dns else "DeepSeek 可达，但受 Fake-IP DNS 牵连风险",
            "subtext": ds_sub,
        }
        if proxy_state.fake_ip_dns and not proxy_state.clash_verge_active:
            findings.append(
                Finding(
                    level="warn",
                    title="国内网站/DeepSeek 的 DNS 解析被代理接管 (198.18.0.2)",
                    detail="一旦梯子后端卡顿或没切分流规则，DeepSeek 会因为解析不到真实 IP 而跟着一起转圈圈。",
                    action="改用 Clash Verge Rev + 本项目自带的 clash-ai-override.js，DeepSeek 将强制走直连不经过梯子。",
                )
            )
    else:
        domestic_card = {
            "status": "fail",
            "headline": "国内 DeepSeek 无法连接",
            "subtext": ds.reason if ds else "探测失败",
        }
        findings.append(
            Finding(
                level="critical",
                title="国内 DeepSeek 直连异常",
                detail=f"直连 https://api.deepseek.com 失败：{ds.reason if ds else '未知错误'}。常见于本地 DNS 被挂死的代理劫持或校园网断开。",
                action="先关闭当前异常代理的 TUN/系统代理测试恢复，或检查 Wi-Fi/校园网登录状态。",
            )
        )

    # 3. Exit stability card
    if not exit_check.reachable:
        exit_card = {
            "status": "fail",
            "headline": "无法探测代理出口 IP",
            "subtext": exit_check.error or "节点可能已断连",
        }
        findings.append(
            Finding(
                level="critical",
                title="代理节点无法访问海外测试服务",
                detail=f"通过代理访问出口检测服务失败：{exit_check.error}。",
                action="当前节点已失效，请切换其他可用节点（或点击「一键切换最优 AI 节点」）。",
            )
        )
    elif exit_check.restricted_region:
        exit_card = {
            "status": "fail",
            "headline": f"{exit_check.flag} 节点在 AI 受限地区 ({exit_check.loc})",
            "subtext": f"出口 IP: {exit_check.ip_1} · 评级 {exit_check.ai_grade}",
        }
        findings.append(
            Finding(
                level="critical",
                title=f"当前出口节点位于受限地区：{exit_check.flag} {exit_check.loc}",
                detail="Antigravity、Gemini 和 OpenAI/Codex 对香港/中国大陆/澳门等地区 IP 会直接拒绝或频繁中断。",
                action="请立即切换到日本、新加坡、台湾或美国节点。",
            )
        )
    elif exit_check.ip_changed:
        exit_card = {
            "status": "warn",
            "headline": f"{exit_check.flag} 出口 IP 在跳变！({exit_check.loc})",
            "subtext": f"{exit_check.ip_1} → {exit_check.ip_2}",
        }
        findings.append(
            Finding(
                level="warn",
                title="检测到节点出口 IP 高频跳变（机场负载均衡/多出口）",
                detail=(
                    f"连续两次请求的出口 IP 从 {exit_check.ip_1} 变成了 {exit_check.ip_2}。"
                    "这是导致 Antigravity 和 Codex 长会话突然报 network issue 并进入 161s 重试倒计时的常见元凶！"
                ),
                action="在 Clash Verge 中使用固定的「🤖 AI-稳定池（fallback 模式）」并选择独享/稳定落地节点。",
            )
        )
    else:
        loc_display = f"{exit_check.flag} {exit_check.country or exit_check.loc}".strip()
        exit_card = {
            "status": "ok",
            "headline": f"出口稳定 ({loc_display} · 评级 {exit_check.ai_grade})",
            "subtext": f"固定 IP: {exit_check.ip_1}" + (f" · {exit_check.isp}" if exit_check.isp else ""),
        }

    # 4. Overseas AI card
    ag = by_key.get("antigravity")
    gm = by_key.get("gemini_api")
    oa = by_key.get("openai_api")
    g204 = by_key.get("google_204")

    core_ai = [p for p in (ag, gm, g204) if p is not None]
    failed_core = [p for p in core_ai if not p.ok]
    warn_core = [p for p in core_ai if p.ok and p.status == "warn"]

    if failed_core:
        names = "、".join(p.label for p in failed_core)
        overseas_card = {
            "status": "fail",
            "headline": f"{names} 不可用",
            "subtext": failed_core[0].reason,
        }
        findings.append(
            Finding(
                level="critical",
                title=f"海外 AI 专线故障：{names}",
                detail="; ".join(f"{p.label}: {p.reason}" for p in failed_core),
                action="立即切换健康节点；如果正在用小火箭，建议迁到 Clash Verge Rev 开启自动故障转移。",
            )
        )
    elif oa and not oa.ok:
        overseas_card = {
            "status": "warn",
            "headline": "Google/Antigravity 可通，但 OpenAI/Codex 异常",
            "subtext": f"OpenAI: {oa.reason}",
        }
        findings.append(
            Finding(
                level="warn",
                title=f"OpenAI / Codex 接口异常：{oa.reason}",
                detail="Antigravity 目前可访问，但当前节点出口可能被 OpenAI 风控或路由不匹配，跑 Codex 时会报错。",
                action="切换其他日/新/美节点，或点「一键切换最优 AI 节点」。",
            )
        )
    elif warn_core:
        overseas_card = {
            "status": "warn",
            "headline": "海外 AI 可通，但延迟偏高",
            "subtext": "; ".join(f"{p.label} {p.latency_ms}ms" for p in warn_core),
        }
        findings.append(
            Finding(
                level="warn",
                title="当前代理链路延迟偏高（>1.5 秒）",
                detail="基础能通但响应太慢，AI 流式输出或大上下文长对话很容易触发超时重试。",
                action="建议测速并切换到 300ms~800ms 以内的日本/新加坡节点。",
            )
        )
    else:
        lat_text = f"Google {g204.latency_ms}ms · Antigravity {ag.latency_ms}ms" if (g204 and ag) else "全部正常"
        overseas_card = {
            "status": "ok",
            "headline": "Antigravity / Gemini / Codex 全部健康",
            "subtext": lat_text,
        }

    if not findings:
        findings.append(
            Finding(
                level="ok",
                title="五步全链路全部健康！",
                detail="本机网卡、DNS、真端口转发、出口纯净度与国内外 AI 专线均处于理想状态。",
                action="可以放心挂机运行长时间 AI 任务。",
            )
        )

    # 5-stage pipeline visualization
    stage1_ok = bool(local_net.lan_ip) or (ds is not None and ds.ok)
    stage1 = {
        "step": "1",
        "title": "本地网卡联网",
        "status": "ok" if stage1_ok else "warn",
        "metric": local_net.lan_ip or local_net.interface or "在线",
        "summary": f"网卡 {local_net.interface or '默认'} 已接入局域网" if stage1_ok else "未获取到局域网 IP",
    }
    stage2_status = "fail" if (ds and not ds.ok) else ("warn" if proxy_state.fake_ip_dns else "ok")
    stage2 = {
        "step": "2",
        "title": "系统代理与 DNS",
        "status": stage2_status,
        "metric": "Fake-IP 托管" if proxy_state.fake_ip_dns else (proxy_state.dns_servers[0] if proxy_state.dns_servers else "系统 DNS"),
        "summary": "国内域名仍经过虚拟 DNS (198.18.x)" if proxy_state.fake_ip_dns else "本地 DNS 解析正常",
    }
    if not proxy_state.effective_proxy_url:
        stage3 = {"step": "3", "title": "后端真端口转发", "status": "warn", "metric": "未启用", "summary": "系统未配置 HTTP 代理端口"}
    elif not proxy_state.port_listening:
        stage3 = {"step": "3", "title": "后端真端口转发", "status": "fail", "metric": "端口未监听", "summary": "假连接：UI 显示连着，后端端口没起"}
    elif proxy_forward_blocked:
        stage3 = {"step": "3", "title": "后端真端口转发", "status": "fail", "metric": "转发堵死", "summary": "端口开着，但转发外网全部超时/失败"}
    else:
        stage3 = {"step": "3", "title": "后端真端口转发", "status": "ok", "metric": proxy_state.effective_proxy_url.replace("http://", ""), "summary": "真端口监听且成功转发外网"}

    stage4 = {
        "step": "4",
        "title": "出口 IP 与纯净度",
        "status": exit_card["status"],
        "metric": f"{exit_check.flag} {exit_check.ip_1}" if exit_check.reachable else "不可达",
        "summary": f"AI 评级 {exit_check.ai_grade} · {'IP 跳变中' if exit_check.ip_changed else 'IP 固定'}",
    }
    stage5 = {
        "step": "5",
        "title": "AI 软件实测",
        "status": overseas_card["status"],
        "metric": f"{ag.latency_ms} ms" if (ag and ag.ok) else ("异常" if failed_core else "就绪"),
        "summary": overseas_card["headline"],
    }
    pipeline = [stage1, stage2, stage3, stage4, stage5]

    has_critical = any(f.level == "critical" for f in findings)
    has_warn = any(f.level == "warn" for f in findings)
    if has_critical:
        overall_status = "broken"
        summary = findings[0].title
    elif has_warn:
        overall_status = "degraded"
        summary = findings[0].title
    else:
        overall_status = "healthy"
        summary = "五步全链路健康，可放心无人值守运行 AI"

    return DiagnosticReport(
        timestamp=ts,
        overall_status=overall_status,
        summary=summary,
        proxy_state=proxy_state,
        exit_check=exit_check,
        probes=probes,
        cards={
            "domestic_ai": domestic_card,
            "proxy_layer": proxy_card,
            "exit_stability": exit_card,
            "overseas_ai": overseas_card,
        },
        findings=findings,
        local_network=local_net,
        dns_checks=dns_list,
        domestic_ip=dom_ip,
        pipeline=pipeline,
    )


async def run_full_diagnostics(
    configured_proxy_url: str = "auto",
    custom_sites: list[dict[str, str]] | None = None,
) -> DiagnosticReport:
    """Execute complete 5-stage diagnostics."""
    local_net = collect_local_network_info()
    proxy_state = collect_local_proxy_state(configured_proxy_url)
    proxy_url = proxy_state.effective_proxy_url if proxy_state.port_listening else None

    all_specs = PROBE_SPECS + _build_custom_specs(custom_sites)
    dns_task = collect_dns_checks()
    dom_task = probe_domestic_ip()
    exit_task = probe_exit_stability(proxy_url)
    probe_tasks = [probe_single_target(spec, proxy_url) for spec in all_specs]

    results = await asyncio.gather(dns_task, dom_task, exit_task, *probe_tasks)
    dns_checks = results[0]
    domestic_ip = results[1]
    exit_check = results[2]
    probes = list(results[3:])

    return evaluate_report(
        proxy_state,
        exit_check,
        probes,
        local_network=local_net,
        dns_checks=dns_checks,
        domestic_ip=domestic_ip,
    )
