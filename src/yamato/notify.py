"""Notifying the human (design §15, policy-audit D32): the entry point only.

``notify.via`` in team.yaml lists channels; several may be given. P0 ships
``command`` (run ``notify.command`` with the message in YAMATO_TITLE /
YAMATO_MESSAGE) and ``mac`` (a macOS notification). ``slack`` and ``windows``
are reserved names for P1 (the agent-fleet notifier is to be ported).
"""
from __future__ import annotations

import os
import subprocess

P1_CHANNELS = ("slack", "windows")


def notify(team: dict, title: str, text: str) -> list[str]:
    """Try every configured channel; returns one status line per channel."""
    cfg = team.get("notify") or {}
    lines = []
    for via in cfg.get("via") or []:
        try:
            if via == "command":
                if not cfg.get("command"):
                    lines.append("通知 command: notify.command が空")
                    continue
                env = dict(os.environ, YAMATO_TITLE=title, YAMATO_MESSAGE=text)
                cp = subprocess.run(cfg["command"], shell=True, env=env, capture_output=True, text=True, timeout=30)
                lines.append(f"通知 command: exit {cp.returncode}")
            elif via == "mac":
                script = f"display notification {_q(text[:200])} with title {_q(title)}"
                cp = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
                lines.append(f"通知 mac: exit {cp.returncode}")
            elif via in P1_CHANNELS:
                lines.append(f"通知 {via}: P1 で実装予定 (いまは送らない)")
            else:
                lines.append(f"通知 {via}: 知らない経路")
        except (OSError, subprocess.TimeoutExpired) as e:
            lines.append(f"通知 {via}: 失敗 ({e})")
    return lines


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
