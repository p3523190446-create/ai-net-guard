"""Configuration and state file paths for AI NetGuard."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def get_config_dir() -> Path:
    base = Path(os.environ.get("AI_NET_GUARD_CONFIG_DIR", Path.home() / ".config" / "ai-net-guard"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_state_dir() -> Path:
    base = Path(os.environ.get("AI_NET_GUARD_STATE_DIR", Path.home() / ".local" / "state" / "ai-net-guard"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_events_log_path() -> Path:
    return get_state_dir() / "events.jsonl"


def get_ip_history_path() -> Path:
    return get_state_dir() / "ip_history.jsonl"


def get_heal_history_path() -> Path:
    return get_state_dir() / "heal_history.jsonl"


@dataclass
class AppConfig:
    proxy_url: str = "auto"
    mihomo_api: str = "http://127.0.0.1:9097"
    mihomo_secret: str = ""
    mihomo_group: str = "🤖 AI-稳定池"
    auto_heal: bool = True
    check_interval_sec: int = 30
    fail_threshold: int = 5
    heal_cooldown_sec: int = 1800
    max_heals_per_hour: int = 2
    max_heals_per_day: int = 5
    dashboard_port: int = 18888
    custom_sites: list[dict[str, str]] = field(default_factory=list)
    ag_watchdog_enabled: bool = True
    ag_eof_threshold: int = 4
    ag_eof_window_sec: int = 420
    ag_heal_cooldown_sec: int = 300
    ag_silent_heal: bool = True
    ag_log_path: str = str(Path.home() / "Library" / "Logs" / "Antigravity" / "language_server.log")


def load_config() -> AppConfig:
    path = get_config_path()
    defaults = AppConfig()
    if not path.exists():
        save_config(defaults)
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = asdict(defaults)
        for key in data:
            if key in raw and raw[key] is not None:
                data[key] = raw[key]
        return AppConfig(**data)
    except (OSError, ValueError, TypeError):
        return defaults


def save_config(cfg: AppConfig) -> AppConfig:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cfg
