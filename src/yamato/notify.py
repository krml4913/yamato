"""Notifying the human (design §15, policy-audit D32, design-p1 §2.4).

``notify.via`` in team.yaml lists channels; every one listed is tried with the
same subject / body / level (``LEVELS``). Best-effort: a channel that fails
never raises, never stops the others and never fails the command that asked
for the notification. The failure is returned as a status line and, when the
caller passes ``shipdir``, recorded in events.jsonl (kind ``notify_failed``).

Channels (ported from agent-fleet's ``fleet/notify.py``):

- ``slack``: POST to an incoming webhook. The URL is read from the environment
  variable named by ``notify.slack.webhook_env``; it is never written in the
  ship folder.
- ``mac``: ``osascript`` ``display notification`` (skipped on other OSes).
- ``windows``: a PowerShell toast (skipped on other OSes). The click-through
  target of agent-fleet (``fleet://attach``) is not ported yet.
- ``command``: run ``notify.command`` through the shell with the message on
  stdin as JSON (``title`` / ``message`` / ``level``) and in YAMATO_TITLE /
  YAMATO_MESSAGE / YAMATO_LEVEL. This is the way to reach anything else
  (mail, ...): no more built-in channels.

PushNotification is not a channel: it cannot be sent from outside a seat
(verify-p1-d V11).
"""
from __future__ import annotations

import base64
import json
import os
import platform
import re
import subprocess
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from . import events

CHANNELS = ("slack", "mac", "windows", "command")
LEVELS = ("success", "waiting", "progress", "error", "info")
DEFAULT_LEVEL = "info"
NOTIFY_FAILED = "notify_failed"   # events.jsonl kind (docs/events.md)

TIMEOUT = 10           # seconds: slack / osascript / powershell
COMMAND_TIMEOUT = 30   # notify.command is the owner's own script; give it longer
MAC_BODY_CHARS = 300
TITLE_CHARS = 60

_EMOJI = {"success": "✅", "waiting": "🟡", "progress": "▶️", "error": "❌", "info": "ℹ️"}
# Slack attachment colors
_COLOR = {"success": "good", "waiting": "warning", "progress": "#439FE0", "error": "danger",
          "info": "#cccccc"}


class _Skip(Exception):
    """This channel does not apply here (e.g. ``mac`` on Windows): not a failure."""


def emoji(level: str) -> str:
    return _EMOJI.get(level, _EMOJI[DEFAULT_LEVEL])


def notify(team: dict, title: str, text: str, level: str = DEFAULT_LEVEL, *,
           shipdir: Path | None = None) -> list[str]:
    """Try every channel in ``notify.via``; returns one status line per channel."""
    cfg = team.get("notify") or {}
    if level not in LEVELS:
        level = DEFAULT_LEVEL
    lines = []
    for via in cfg.get("via") or []:
        try:
            send = _SENDERS.get(via)
            if send is None:
                raise RuntimeError(f"知らない経路 (選べるのは {' / '.join(CHANNELS)})")
            lines.append(f"通知 {via}: {send(team, cfg, title, text, level)}")
        except _Skip as e:
            lines.append(f"通知 {via}: 送らない ({e})")
        except Exception as e:  # noqa: BLE001 — best effort: one channel never stops the rest
            reason = " ".join(str(e).split())[:200] or type(e).__name__
            lines.append(f"通知 {via}: 失敗 ({reason})")
            if shipdir is not None:
                events.emit(shipdir, NOTIFY_FAILED, summary=f"通知 {via} に失敗: {reason}",
                            data={"via": via, "level": level, "reason": reason})
    return lines


def _one_line(s: str, limit: int) -> str:
    s = re.sub(r"[\x00-\x08\x0e-\x1b\x7f]", " ", s)   # not allowed in the toast's XML
    return " ".join(s.split())[:limit]


def _send_command(team: dict, cfg: dict, title: str, text: str, level: str) -> str:
    if not cfg.get("command"):
        raise RuntimeError("notify.command が空")
    env = dict(os.environ, YAMATO_TITLE=title, YAMATO_MESSAGE=text, YAMATO_LEVEL=level)
    payload = json.dumps({"title": title, "message": text, "level": level})
    cp = subprocess.run(cfg["command"], shell=True, env=env, input=payload, capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=COMMAND_TIMEOUT)
    if cp.returncode != 0:
        raise RuntimeError(f"exit {cp.returncode}: {(cp.stderr or '').strip()[:200]}")
    return "exit 0"


def _send_slack(team: dict, cfg: dict, title: str, text: str, level: str) -> str:
    env_name = (cfg.get("slack") or {}).get("webhook_env")
    if not env_name:
        raise RuntimeError("notify.slack.webhook_env が未設定")
    url = os.environ.get(env_name, "").strip()
    if not url:
        raise RuntimeError(f"環境変数 {env_name} が空")
    if not url.startswith(("https://", "http://")):
        raise RuntimeError(f"環境変数 {env_name} が http(s) の URL ではない")
    attachment = {
        "color": _COLOR.get(level, _COLOR[DEFAULT_LEVEL]),
        "fallback": f"{emoji(level)} {title}: {text}",
        "title": f"{emoji(level)} {title}",
        "text": text,
        "footer": f"yamato · {team.get('name', '')}".rstrip(" ·"),
    }
    req = urllib.request.Request(url, data=json.dumps({"attachments": [attachment]}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read()
    except Exception as e:  # noqa: BLE001 — re-raised without the URL (it is a secret)
        raise RuntimeError(str(e).replace(url, "<webhook>") or type(e).__name__) from None
    return "送った"


def _send_mac(team: dict, cfg: dict, title: str, text: str, level: str) -> str:
    if platform.system() != "Darwin":
        raise _Skip("macOS ではない")
    body = _one_line(f"{emoji(level)} {text}", MAC_BODY_CHARS)
    script = f"display notification {_q(body)} with title {_q(_one_line(title, TITLE_CHARS))}"
    cp = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=TIMEOUT)
    if cp.returncode != 0:
        raise RuntimeError(f"exit {cp.returncode}: {(cp.stderr or '').strip()[:200]}")
    return "exit 0"


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# AppUserModelID of Windows PowerShell's own Start-menu shortcut: a toast shown
# under it needs no registration of our own (no click-through either).
_WINDOWS_TOAST_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_WINDOWS_TOAST_SCRIPT = """\
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('{xml}')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def _toast_text(text: str) -> str:
    """XML-escape ``text`` into pure ASCII (non-ASCII -> ``&#N;``).

    Quotes become entities too, so the result sits safely inside a PowerShell
    single-quoted string (PowerShell also treats U+2018..U+201B as ``'``; being
    non-ASCII they become ``&#N;``).
    """
    escaped = _xml_escape(text, {'"': "&quot;", "'": "&apos;"})
    return escaped.encode("ascii", "xmlcharrefreplace").decode("ascii")


def _windows_toast_script(title: str, text: str, level: str) -> str:
    body = _one_line(f"{emoji(level)} {text}", MAC_BODY_CHARS)
    xml = ("<toast><visual><binding template=\"ToastGeneric\">"
           f"<text>{_toast_text(_one_line(title, TITLE_CHARS))}</text>"
           f"<text>{_toast_text(body)}</text>"
           "</binding></visual></toast>")
    return _WINDOWS_TOAST_SCRIPT.format(xml=xml, app_id=_WINDOWS_TOAST_APP_ID)


def _send_windows(team: dict, cfg: dict, title: str, text: str, level: str) -> str:
    if platform.system() != "Windows":
        raise _Skip("Windows ではない")
    script = _windows_toast_script(title, text, level)
    # -EncodedCommand (base64 UTF-16LE) sidesteps all command-line quoting.
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cp = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        capture_output=True, timeout=TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if cp.returncode != 0:
        err = (cp.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"exit {cp.returncode}: {err[:200]}")
    return "送った"


_SENDERS = {"slack": _send_slack, "mac": _send_mac, "windows": _send_windows, "command": _send_command}
