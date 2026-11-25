"""Antigravity language_server.log watchdog for high-threshold silent self-healing."""

from __future__ import annotations

import datetime
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

RETRY_EOF_RE = re.compile(
    r"run\.go:\d+\]\s*Run:\s*attempt\s+(\d+)\s+failed\s*\(stream reading error:\s*unexpected EOF\)",
    re.IGNORECASE,
)
TERMINAL_EOF_RE = re.compile(
    r"errorreport\.go:\d+\]\s*agent executor error:\s*generating and executing:\s*stream reading error:\s*unexpected EOF",
    re.IGNORECASE,
)


def default_antigravity_log_path() -> Path:
    """Return the default Antigravity language server log path on macOS."""
    return Path.home() / "Library" / "Logs" / "Antigravity" / "language_server.log"


class AntigravityWatchdog:
    """Incrementally tails Antigravity logs to detect stalled EOF retry loops."""

    def __init__(
        self,
        log_path: str | Path | None = None,
        enabled: bool = True,
        eof_threshold: int = 4,
        window_sec: int = 420,
        cooldown_sec: int = 300,
        silent_heal: bool = True,
        seek_to_end: bool = True,
    ) -> None:
        self.log_path = Path(log_path).expanduser() if log_path else default_antigravity_log_path()
        self.enabled = bool(enabled)
        self.eof_threshold = max(1, int(eof_threshold))
        self.window_sec = max(30, int(window_sec))
        self.cooldown_sec = max(10, int(cooldown_sec))
        self.silent_heal = bool(silent_heal)

        self._offset: int = 0
        self._inode: int | None = None
        self._eof_events: deque[tuple[float, str]] = deque()
        self._last_heal_mono: float | None = None

        self.last_eof_time: str = ""
        self.last_eof_detail: str = ""
        self.last_heal_time: str = ""
        self.last_heal_result: str = ""
        self.total_heals: int = 0

        self._init_cursor(seek_to_end=seek_to_end)

    def _init_cursor(self, seek_to_end: bool) -> None:
        try:
            st = self.log_path.stat()
        except OSError:
            return
        self._inode = st.st_ino
        self._offset = st.st_size if seek_to_end else 0

    def update_settings(
        self,
        enabled: bool | None = None,
        eof_threshold: int | None = None,
        window_sec: int | None = None,
        cooldown_sec: int | None = None,
        silent_heal: bool | None = None,
        log_path: str | Path | None = None,
    ) -> None:
        """Update watchdog thresholds and paths dynamically."""
        if enabled is not None:
            self.enabled = bool(enabled)
        if eof_threshold is not None:
            self.eof_threshold = max(1, int(eof_threshold))
        if window_sec is not None:
            self.window_sec = max(30, int(window_sec))
        if cooldown_sec is not None:
            self.cooldown_sec = max(10, int(cooldown_sec))
        if silent_heal is not None:
            self.silent_heal = bool(silent_heal)
        if log_path:
            new_path = Path(log_path).expanduser()
            if new_path != self.log_path:
                self.log_path = new_path
                self._offset = 0
                self._inode = None
                self._init_cursor(seek_to_end=True)

    def _prune_window(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._eof_events and self._eof_events[0][0] < cutoff:
            self._eof_events.popleft()

    def _read_new_lines(self) -> list[str]:
        try:
            st = self.log_path.stat()
        except OSError:
            return []

        if self._inode is None or st.st_ino != self._inode or st.st_size < self._offset:
            self._inode = st.st_ino
            self._offset = 0

        if st.st_size == self._offset:
            return []

        try:
            with self.log_path.open("rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                self._offset = fh.tell()
        except OSError:
            return []

        if not chunk:
            return []
        return chunk.decode("utf-8", errors="replace").splitlines()

    def poll(self, now_mono: float | None = None) -> dict[str, Any]:
        """Incrementally inspect new log lines and report if silent auto-heal should fire."""
        now = time.monotonic() if now_mono is None else float(now_mono)
        lines = self._read_new_lines()
        self._prune_window(now)

        if not self.enabled:
            return {
                "new_eofs": 0,
                "saw_terminal_error": False,
                "recent_eof_count": len(self._eof_events),
                "in_cooldown": False,
                "should_heal": False,
                "reason": "",
                "status": self.status_dict(now_mono=now),
            }

        new_eofs = 0
        saw_terminal_error = False
        now_wall = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for line in lines:
            if TERMINAL_EOF_RE.search(line):
                saw_terminal_error = True
                new_eofs += 1
                detail = "agent executor error: unexpected EOF"
                self._eof_events.append((now, detail))
                self.last_eof_time = now_wall
                self.last_eof_detail = detail
                continue

            m = RETRY_EOF_RE.search(line)
            if m:
                new_eofs += 1
                detail = f"Run attempt {m.group(1)} failed (unexpected EOF)"
                self._eof_events.append((now, detail))
                self.last_eof_time = now_wall
                self.last_eof_detail = detail

        self._prune_window(now)
        recent_count = len(self._eof_events)
        in_cooldown = (
            self._last_heal_mono is not None and (now - self._last_heal_mono) < self.cooldown_sec
        )
        threshold_hit = recent_count >= self.eof_threshold
        should_heal = (not in_cooldown) and (threshold_hit or saw_terminal_error)

        reason = ""
        if should_heal:
            if saw_terminal_error and not threshold_hit:
                reason = "Antigravity 出现终态 stream reading error: unexpected EOF"
            else:
                mins = max(1, round(self.window_sec / 60))
                reason = f"Antigravity {mins} 分钟内连续出现 {recent_count} 次 unexpected EOF 断流"

        return {
            "new_eofs": new_eofs,
            "saw_terminal_error": saw_terminal_error,
            "recent_eof_count": recent_count,
            "in_cooldown": in_cooldown,
            "should_heal": should_heal,
            "reason": reason,
            "status": self.status_dict(now_mono=now),
        }

    def mark_healed(
        self,
        ok: bool,
        message: str,
        selected_node: str | None = None,
        now_mono: float | None = None,
    ) -> None:
        """Record a heal attempt, enter cooldown, and clear the current EOF window."""
        now = time.monotonic() if now_mono is None else float(now_mono)
        self._last_heal_mono = now
        self.last_heal_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        suffix = f" -> {selected_node}" if selected_node and selected_node not in message else ""
        prefix = "成功" if ok else "未完成"
        self.last_heal_result = f"{prefix}: {message}{suffix}"
        if ok:
            self.total_heals += 1
        self._eof_events.clear()

    def status_dict(self, now_mono: float | None = None) -> dict[str, Any]:
        """Return a JSON-serializable watchdog status snapshot."""
        now = time.monotonic() if now_mono is None else float(now_mono)
        self._prune_window(now)
        recent_count = len(self._eof_events)
        cooldown_remaining = 0
        if self._last_heal_mono is not None:
            cooldown_remaining = max(0, round(self.cooldown_sec - (now - self._last_heal_mono)))

        if not self.enabled:
            status = "disabled"
        elif recent_count >= self.eof_threshold:
            status = "stalled"
        elif recent_count > 0:
            status = "watching"
        else:
            status = "healthy"

        return {
            "enabled": self.enabled,
            "log_path": str(self.log_path),
            "log_exists": self.log_path.exists(),
            "status": status,
            "recent_eof_count": recent_count,
            "threshold": self.eof_threshold,
            "window_sec": self.window_sec,
            "cooldown_sec": self.cooldown_sec,
            "cooldown_remaining_sec": cooldown_remaining,
            "silent_heal": self.silent_heal,
            "last_eof_time": self.last_eof_time,
            "last_eof_detail": self.last_eof_detail,
            "last_heal_time": self.last_heal_time,
            "last_heal_result": self.last_heal_result,
            "total_heals": self.total_heals,
        }
