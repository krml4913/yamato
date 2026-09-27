"""Windows + Git Bash verification kit (W0). The procedure is docs/verify/verify-win-plan.md.

Every subcommand appends what it saw to <VW>/results.txt (VW = ~/yamato-verify-win),
so the owner only has to send that one file back. Standard library only; yamato (src/)
is never imported. Windows-only calls branch at run time, so this also runs on macOS
as a dry run.

  setup                     make <VW>, copy the kit into <VW>/kit, write settings and env.sh
  env                       [1] versions, paths, python names, encodings
  up <preset> [--force]     start a bg seat vw.<preset> (life hooks prof detach recv send perm)
  wait <seat>               wait until the seat's turn is over (status idle), up to 5 min
  agents                    [2] vw.* seats: pid, and whether it is a Windows pid
  sid <seat> [--short]      print the seat's sessionId
  stop|rm <seat>            [2] claude stop / rm, and how long the pid takes to go
  resume <seat> "<msg>"     [2] claude --resume <full id> --bg: same id or a copy?
  tx <seat>                 [3][4][8][9] summarize the seat's transcript (tokens, tools, errors)
  hooklog [<tag prefix>]    [3] summarize <VW>/logs/hook.jsonl (encodings, shell, job)
  wake "<text>"             [4] append to wake.txt and wait for vw.hooks to wake up
  detach-check [--wait]     [5] did the detached children outlive the seat's stop?
  pid                       [6] os.kill(pid, 0) and OpenProcess on live / dead / missing pids
  pterm <break|terminate|taskkill>
                            [7] stop a running claude -p and see what the transcript keeps
  perm-check                [9] which deny rules held (files, tools used)
  lock                      [10] msvcrt.locking between two processes
  note <item> "<text>"      write the owner's own observation into results.txt
  cleanup                   stop + rm every vw.* seat, kill leftover children, check
"""
from __future__ import annotations

import glob
import json
import locale
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

ORIG_ENC = {"stdin": getattr(sys.stdin, "encoding", None),
            "stdout": getattr(sys.stdout, "encoding", None),
            "stderr": getattr(sys.stderr, "encoding", None)}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CREATE_NEW_PROCESS_GROUP, IS_WIN, KIT, TOKENS, append_jsonl, fwd, msys, now,  # noqa: E402
                    pid_alive, read_json, run, utf8_stdio, vw_dir, write_json)

VW = vw_dir()
RESULTS = VW / "results.txt"
EVENTS = VW / "logs" / "vw-events.jsonl"
HOOKLOG = VW / "logs" / "hook.jsonl"
KIT_FILES = ("common.py", "child.py", "hook.py", "vw.py")
MIN_CLAUDE = (2, 1, 234)  # SendMessage over a named pipe on native Windows
ECHO_LINE = re.compile(r"\s*(echo|printf)\b")
DETACH_SECS = 90  # how long the detached children live: time to type vw stop detach
ENV_NOISE = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")

MARKERS = ("VWWAKE", "PING-VW", "ACK", "VW-HOOKS", "VW-PERM", "VW-LIFE", "VW-RECV",
           "RESUMED-VW", "PROFILE-ECHO", "VWP-BEFORE", "DENYME", "PSDENY")


# ---------------------------------------------------------------- reporting

class Section:
    """One block of results.txt: '## [item] title (time)' then 'key: value' lines."""

    def __init__(self, item: str, title: str):
        self.lines = [f"## [{item}] {title} ({now()})"]

    def add(self, key: str, value=None) -> None:
        if value is None:
            self.lines.append(key)
            return
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        self.lines.append(f"- {key}: {value}")

    def done(self) -> None:
        text = "\n".join(self.lines) + "\n\n"
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        with open(RESULTS, "a", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(text, end="")


def event(kind: str, **kw) -> None:
    append_jsonl(EVENTS, {"t": now(), "epoch": time.time(), "kind": kind, **kw})


def events(kind: str) -> list[dict]:
    return [e for e in _jsonl(EVENTS) if e.get("kind") == kind]


def _jsonl(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def short(s, n=160) -> str:
    s = str(s).strip().replace("\n", "\\n")
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------- claude

def claude_bin() -> str:
    return shutil.which("claude") or "claude"


def claude(*args, timeout=60, cwd=None) -> dict:
    return run([claude_bin(), *args], timeout=timeout, cwd=cwd)


def agents_all() -> list[dict]:
    r = claude("agents", "--json", "--all")
    try:
        data = json.loads(r.get("out") or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def seat_name(s: str) -> str:
    return s if s.startswith("vw.") else f"vw.{s}"


def find_seat(s: str) -> dict | None:
    """The newest listing entry with that name."""
    name = seat_name(s)
    hits = [a for a in agents_all() if a.get("name") == name]
    hits.sort(key=lambda a: a.get("startedAt") or 0)
    return hits[-1] if hits else None


def need_seat(s: str) -> dict:
    a = find_seat(s)
    if not a:
        sys.exit(f"{seat_name(s)} が claude agents --json --all に見つかりません")
    return a


def transcript_path(sid: str) -> Path | None:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    hits = glob.glob(str(base / "projects" / "*" / f"{sid}.jsonl"))
    return Path(hits[0]) if hits else None


# ---------------------------------------------------------------- setup

def settings_files(vw: Path) -> dict[str, dict]:
    py, kit, ship = fwd(sys.executable), vw / "kit", vw / "ship"
    hook = fwd(kit / "hook.py")

    def exec_(*args, **extra):
        return {"type": "command", "command": py, "args": [hook, *args], **extra}

    def shell(*args):
        return {"type": "command", "command": " ".join([f'"{py}"', f'"{hook}"', *args])}

    shell_meta = ['"flags=$-"', '"login=$(shopt -q login_shell 2>/dev/null && echo yes || echo no)"',
                  '"bash_env=${BASH_ENV:-}"']
    log_all = lambda p: {  # noqa: E731
        "SessionStart": [{"hooks": [exec_("log", f"{p}-ss")]}],
        "UserPromptSubmit": [{"hooks": [exec_("log", f"{p}-ups")]}],
        "PreToolUse": [{"matcher": "*", "hooks": [exec_("log", f"{p}-pre")]}],
        "Stop": [{"hooks": [exec_("log", f"{p}-stop")]}],
        "SessionEnd": [{"hooks": [exec_("log", f"{p}-end")]}],
    }
    everything = {"allow": ["Bash", "PowerShell"]}
    team = {n: ship / n for n in ("team.yaml", "team2.yaml", "team3.yaml")}
    deny = []
    # three spellings of an absolute path: the documented //c/... form, C:/ and C:\ as yamato
    # renders "/{{ship}}" today (str(Path) / as_posix)
    for rule_path in ("/" + msys(team["team.yaml"]), "/" + fwd(team["team2.yaml"]),
                      "/" + str(team["team3.yaml"])):
        deny += [f"Edit({rule_path})", f"Write({rule_path})"]
    deny += ["Bash(echo DENYME*)", "PowerShell(Write-Output PSDENY*)"]
    return {
        "life": {"crossSessionInbound": "accept", "hooks": log_all("life")},
        "hooks": {
            "crossSessionInbound": "accept",
            "permissions": everything,
            "hooks": {
                "SessionStart": [{"hooks": [
                    shell("inject", "hooks-ss-shell", "SHELL", "utf8", *shell_meta),
                    exec_("inject", "hooks-ss-exec8", "EXEC8", "utf8"),
                    exec_("inject", "hooks-ss-execdef", "EXECDEF", "default"),
                    exec_("inject", "hooks-ss-execasc", "EXECASC", "ascii"),
                ]}],
                "UserPromptSubmit": [{"hooks": [exec_("log", "hooks-ups-exec"),
                                                shell("log", "hooks-ups-shell", *shell_meta)]}],
                "PreToolUse": [{"matcher": "*", "hooks": [exec_("log", "hooks-pre-exec"),
                                                          shell("log", "hooks-pre-shell")]}],
                "Stop": [{"hooks": [exec_("rewake", "hooks-stop-rewake", async_=True)]}],
            },
        },
        "prof": {
            "env": {"BASH_ENV": msys(kit / "profile_echo.sh") if IS_WIN else str(kit / "profile_echo.sh")},
            "hooks": {"SessionStart": [{"hooks": [
                shell("inject", "prof-ss-shell", "PROF", "utf8", *shell_meta),
                exec_("inject", "prof-ss-exec", "PROFX", "utf8"),
            ]}]},
        },
        "detach": {
            "permissions": everything,
            "hooks": {
                "SessionStart": [{"hooks": [
                    exec_("detach", "hook", str(DETACH_SECS)),
                    {"type": "command", "command":
                        f'"{py}" "{fwd(kit / "child.py")}" sleep hook-E-bashbg {DETACH_SECS} '
                        '</dev/null >/dev/null 2>&1 &'},
                ]}],
                "PreToolUse": [{"matcher": "*", "hooks": [exec_("log", "detach-pre")]}],
            },
        },
        "xsm": {
            "crossSessionInbound": "accept",
            "permissions": {"allow": ["ListAgents", "SendMessage", "ToolSearch"]},
            "hooks": {"UserPromptSubmit": [{"hooks": [exec_("log", "xsm-ups")]}]},
        },
        "perm": {
            "permissions": {"deny": deny},
            "hooks": {
                "PreToolUse": [{"matcher": "*", "hooks": [exec_("log", "perm-pre")]}],
                "PermissionDenied": [{"hooks": [exec_("log", "perm-denied")]}],
            },
        },
    }


def _fix_async(obj):
    """exec_(..., async_=True) -> "async": true + "asyncRewake": true + a long timeout."""
    if isinstance(obj, dict):
        if obj.pop("async_", None):
            obj.update({"async": True, "asyncRewake": True, "timeout": 3600})
        for v in obj.values():
            _fix_async(v)
    elif isinstance(obj, list):
        for v in obj:
            _fix_async(v)
    return obj


def cmd_setup(_args) -> None:
    VW.mkdir(parents=True, exist_ok=True)
    (VW / ".vw").write_text("yamato verify-win scratch folder. Safe to delete.\n", encoding="utf-8")
    kit = VW / "kit"
    kit.mkdir(exist_ok=True)
    if KIT != kit:
        for n in KIT_FILES:
            shutil.copy2(KIT / n, kit / n)
    sourced = msys(VW / "logs" / "bash_env_sourced.txt") if IS_WIN else str(VW / "logs" / "bash_env_sourced.txt")
    with open(kit / "profile_echo.sh", "w", encoding="utf-8", newline="\n") as f:
        f.write(f'echo "PROFILE-ECHO 混入テスト"\necho "$$ $0" >> \'{sourced}\'\n')
    for d in ("cwd", "ship", "logs", "detach", "lock", "settings"):
        (VW / d).mkdir(exist_ok=True)
    for n in ("team.yaml", "team2.yaml", "team3.yaml", "other.yaml"):
        p = VW / "ship" / n
        if not p.exists():
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(f"# verify-win の deny の的 ({n})\nname: vw\n")
    (VW / "wake.txt").touch()
    for name, obj in settings_files(VW).items():
        write_json(VW / "settings" / f"{name}.json", _fix_async(obj))
    with open(VW / "env.sh", "w", encoding="utf-8", newline="\n") as f:
        f.write(f"export VW='{msys(VW) if IS_WIN else VW}'\n"
                f"export VWW='{fwd(VW)}'\n"
                f"export PY='{msys(sys.executable) if IS_WIN else sys.executable}'\n"
                'vw() { "$PY" "$VW/kit/vw.py" "$@"; }\n')
    s = Section("0", "setup")
    s.add("VW", fwd(VW))
    s.add("python", sys.executable)
    s.add("platform", platform.platform())
    s.add("kit", [n for n in KIT_FILES])
    s.done()
    print(f"次に:  source '{msys(VW) if IS_WIN else VW}/env.sh'  してから  vw env")


# ---------------------------------------------------------------- [1] env

def _ver_tuple(text: str):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def cmd_env(_args) -> None:
    s = Section("1", "環境")
    s.add("os", f"{os.name} / {platform.platform()}")
    s.add("python (this)", f"{sys.executable} {sys.version.split()[0]}")
    s.add("encodings", {"preferred": locale.getpreferredencoding(False),
                        "filesystem": sys.getfilesystemencoding(), "utf8_mode": sys.flags.utf8_mode,
                        "stdio_before_reconfigure": ORIG_ENC, "stdout_isatty": sys.stdout.isatty()})
    names = ("claude", "python", "py", "python3", "bash", "sh", "git", "ps", "nohup", "setsid",
             "sleep", "tasklist", "taskkill", "powershell", "pwsh", "cygpath")
    which = {n: shutil.which(n) for n in names}
    s.add("which", which)
    cv = claude("--version")
    ver = _ver_tuple(cv.get("out", ""))
    s.add("claude --version", short(cv.get("out") or cv.get("error") or cv.get("err")))
    s.add("claude >= 2.1.234", "判定不能" if not ver else ("OK" if ver >= MIN_CLAUDE else "NG (古い)"))
    s.add("claude の場所", f"{which['claude']} (.exe={str(which['claude']).lower().endswith('.exe')})")
    for n in ("python", "py", "python3"):
        if which[n]:
            r = run([which[n], "-c", "import sys;print(sys.executable, sys.version.split()[0])"], timeout=20)
            s.add(f"{n} ->", short(r.get("out") or r.get("error") or r.get("err"))
                  + (" [WindowsApps の stub の疑い]" if "WindowsApps" in which[n] else ""))
    if which["py"]:
        s.add("py -0p", short(run([which["py"], "-0p"], timeout=20).get("out"), 400))
    for n, args in (("bash", ["--version"]), ("git", ["--version"])):
        if which[n]:
            r = run([which[n], *args], timeout=20)
            s.add(f"{n} --version", short((r.get("out") or "").splitlines()[0] if r.get("out") else r))
    s.add("git config core.autocrlf", short(run(["git", "config", "--get", "core.autocrlf"]).get("out", "").strip() or "(未設定)"))
    if IS_WIN:
        s.add("chcp", short(run(["chcp.com"], timeout=10).get("out", "").strip()))
    s.add("env", {k: os.environ.get(k) for k in (
        "CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CODE_USE_POWERSHELL_TOOL", "MSYSTEM", "SHELL", "HOME",
        "USERPROFILE", "PYTHONUTF8", "PYTHONIOENCODING", "LANG", "LC_ALL", "TERM", "TERM_PROGRAM")})
    prof = {}
    for home in {os.environ.get("HOME"), str(Path.home())} - {None}:
        for n in (".bashrc", ".bash_profile", ".profile"):
            p = Path(home) / n
            if p.exists():
                try:
                    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                    n_echo = sum(1 for line in lines if ECHO_LINE.match(line))
                    prof[fwd(p)] = f"echo の行 {n_echo}"
                except OSError as e:
                    prof[fwd(p)] = str(e)
    s.add("Git Bash の profile (中身は読まず echo の行数だけ)", prof or "なし")
    s.done()


# ---------------------------------------------------------------- seats

def presets() -> dict[str, dict]:
    py, child, hook = fwd(sys.executable), fwd(VW / "kit" / "child.py"), fwd(VW / "kit" / "hook.py")
    ship = VW / "ship"
    t = {n: fwd(ship / n) for n in ("team.yaml", "team2.yaml", "team3.yaml", "other.yaml")}
    return {
        "life": dict(model="haiku", settings="life", mode="dontAsk",
                     prompt="検証用の席です。『VW-LIFE 起動』とだけ返せ。"),
        "hooks": dict(model="haiku", settings="hooks", mode="dontAsk", prompt=(
            "検証用の席です。次を順に行え。\n"
            "1. system に注入された『合言葉』(VWTOK- で始まるもの) をすべて、一字一句そのまま書き出せ。"
            "文字化けして見えるものもそのまま写せ。見つからなければ『合言葉なし』と書け。\n"
            "2. Bash ツールで次のコマンドをそのまま実行せよ: echo 日本語のツール入力テスト\n"
            "3. このディレクトリのファイル一覧を出せ (使うツールは任せる)。\n"
            "4. 最後に『VW-HOOKS 完了』と書いて終われ。\n"
            "このあと『VWWAKE 起床の合図』で始まる知らせが来たら、その本文をそのまま引用して"
            "『VW-HOOKS 起きた』と返せ。")),
        "prof": dict(model="haiku", settings="prof", mode="dontAsk", prompt=(
            "検証用の席です。system に注入された『合言葉』(VWTOK- で始まるもの) をすべて一字一句"
            "そのまま書き出せ。PROFILE-ECHO という文字列が見えたら、それも書け。無ければ『合言葉なし』。")),
        "detach": dict(model="haiku", settings="detach", mode="dontAsk", prompt=(
            "検証用の席です。Bash ツールで次のコマンドをそのまま 1 回だけ実行し、終わったら"
            f"『OK』とだけ返せ:\n\"{py}\" \"{hook}\" spawn tool {DETACH_SECS}")),
        "recv": dict(model="haiku", settings="xsm", mode="dontAsk", rc=True, prompt=(
            "検証用の席です。『VW-RECV 待機』とだけ返して待て。あとで別のセッションからメッセージが"
            "届いたら『ACK: <本文そのまま>』と返せ。")),
        "send": dict(model="haiku", settings="xsm", mode="dontAsk", prompt=(
            "検証用の席です。ListAgents で vw.recv という名前のセッションを探し、SendMessage で"
            "『PING-VW 日本語の便り』と送れ。SendMessage の結果 (success など) をそのまま報告せよ。")),
        "perm": dict(model="sonnet", settings="perm", mode="auto", add_dir=True, prompt=(
            "権限の検証用の席です。次の手順を順に 1 回ずつ試せ。拒否されても再試行や回避をせず、"
            "次の手順へ進め。\n"
            f"1. Edit ツールで {t['team.yaml']} の末尾に `# edited-1` という行を足す\n"
            f"2. Edit ツールで {t['team2.yaml']} の末尾に `# edited-2` という行を足す\n"
            f"3. Edit ツールで {t['team3.yaml']} の末尾に `# edited-3` という行を足す\n"
            f"4. Edit ツールで {t['other.yaml']} の末尾に `# edited-4` という行を足す\n"
            "5. Bash ツールで `echo DENYME-5`\n"
            "6. PowerShell ツールで `Write-Output DENYME-6` (PowerShell ツールが無ければ『PowerShell なし』)\n"
            "7. PowerShell ツールで `Write-Output PSDENY-7`\n"
            f"8. PowerShell ツールで `Add-Content -Path '{t['team.yaml']}' -Value '# edited-8'`\n"
            "9. 今の日時を表示せよ (使うツールは任せる)\n"
            "最後に、手順ごとに『使ったツール名 / 通った・拒否された / 拒否の文言』を表で報告し、"
            "『VW-PERM 完了』で終われ。")),
    }


def wait_pid(name: str, want_alive: bool, secs: float = 30) -> tuple[dict | None, float]:
    t0 = time.time()
    while True:
        a = find_seat(name)
        alive = bool(a and a.get("pid"))
        if alive == want_alive or time.time() - t0 > secs:
            return a, round(time.time() - t0, 1)
        time.sleep(2)


def cmd_up(args) -> None:
    name = args[0] if args else ""
    ps = presets()
    if name not in ps:
        sys.exit(f"preset は {' / '.join(ps)} のどれか")
    p = ps[name]
    seat = seat_name(name)
    cur = find_seat(name)
    if cur and cur.get("pid") and "--force" not in args:
        sys.exit(f"{seat} はもう動いています (pid {cur.get('pid')})。やり直すなら vw stop {name} / vw rm {name} のあと")
    if name == "detach":  # a second run must not read the first run's children
        d = VW / "detach"
        for f in [*d.glob("hook-*"), *d.glob("tool-*"), *d.glob("spawn-*.json")]:
            f.unlink()
    cmd = ["--bg", "--name", seat, "--model", p["model"], "--setting-sources", "project,local",
           "--settings", fwd(VW / "settings" / f"{p['settings']}.json"), "--permission-mode", p["mode"]]
    if p.get("rc"):
        cmd.append("--remote-control")
    if p.get("add_dir"):
        cmd += ["--add-dir", fwd(VW / "ship")]
    cmd += ["--", p["prompt"]]
    r = claude(*cmd, cwd=str(VW / "cwd"))
    event("up", seat=seat)
    s = Section("up", f"{seat} を起動")
    s.add("cmd", "claude " + " ".join(c if len(c) < 80 else c[:60] + "…" for c in cmd))
    s.add("rc", r.get("rc", r.get("error")))
    s.add("out", short(r.get("out", "").strip(), 300))
    if r.get("err", "").strip():
        s.add("err", short(r["err"].strip(), 300))
    a, secs = wait_pid(name, True, 30)
    s.add("agents", {k: a.get(k) for k in ("id", "sessionId", "pid", "status", "state")} if a else "見つからない")
    s.add("pid が出るまで", f"{secs}s")
    s.done()


def _ps_w_lines(pid: int) -> list[str]:
    ps = shutil.which("ps")
    if not ps:
        return ["ps が見つからない"]
    r = run([ps, "-W"] if IS_WIN else [ps, "-p", str(pid), "-o", "pid,comm"], timeout=30)
    lines = (r.get("out") or "").splitlines()
    return [lines[0]] + [l for l in lines[1:] if str(pid) in l.split()] if lines else [short(r)]


def cmd_agents(_args) -> None:
    s = Section("2", "claude agents --json (vw.* の席)")
    seats = [a for a in agents_all() if str(a.get("name", "")).startswith("vw.")]
    if not seats:
        s.add("vw.* の席はない")
    for a in seats:
        pid = a.get("pid")
        s.add(a.get("name"), {k: a.get(k) for k in ("id", "sessionId", "pid", "status", "state", "kind")})
        if not pid:
            continue
        s.add("  pid_alive (OpenProcess)", pid_alive(int(pid)))
        if IS_WIN:
            tl = run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=30)
            s.add("  tasklist", short(tl.get("out", "").strip() or tl, 200))
        s.add("  ps -W (該当行)", _ps_w_lines(int(pid)))
    s.done()


def cmd_wait(args) -> None:
    """Wait until the seat has finished its turn (status idle), up to 5 minutes."""
    t0 = time.time()
    while time.time() - t0 < 300:
        a = find_seat(args[0])
        if a and a.get("status") == "idle":
            print(f"{a.get('name')}: idle ({round(time.time() - t0)}s)")
            return
        if a and a.get("status") == "waiting":
            print(f"{a.get('name')}: waiting ({a.get('waitingFor')}) — 確認待ちで止まっている。"
                  f"claude attach {a.get('id')} で見る")
            return
        time.sleep(3)
    print(f"{seat_name(args[0])}: 5 分待っても idle にならない (vw agents で見る)")


def cmd_sid(args) -> None:
    a = need_seat(args[0])
    print(a.get("id") if "--short" in args else a.get("sessionId"))


def cmd_stop(args, verb="stop") -> None:
    a = need_seat(args[0])
    old_pid = a.get("pid")
    t0 = time.time()
    r = claude(verb, a.get("id"))
    event(verb, seat=a.get("name"), sessionId=a.get("sessionId"), pid=old_pid)
    s = Section("2", f"claude {verb} {a.get('id')} ({a.get('name')})")
    s.add("rc", r.get("rc", r.get("error")))
    s.add("out/err", short((r.get("out", "") + r.get("err", "")).strip(), 300))
    if verb == "stop":
        a2, secs = wait_pid(args[0], False, 30)
        s.add("agents の pid が消えるまで", f"{round(time.time() - t0, 1)}s (pid={a2.get('pid') if a2 else None})")
        if old_pid:
            time.sleep(1)
            s.add(f"元の pid {old_pid} のプロセス", pid_alive(int(old_pid)))
    else:
        s.add("rm のあと一覧に残るか", bool(find_seat(args[0])))
    s.done()


def cmd_resume(args) -> None:
    if len(args) < 2:
        sys.exit('vw resume <seat> "<msg>"')
    a = need_seat(args[0])
    if a.get("pid"):
        sys.exit(f"{a.get('name')} はまだ生きています (pid {a.get('pid')})。先に vw stop {args[0]}")
    before = {x.get("sessionId") for x in agents_all()}
    r = claude("--resume", a["sessionId"], "--bg", "--", args[1], cwd=str(VW / "cwd"))
    time.sleep(3)
    a2, secs = wait_pid(args[0], True, 30)
    new = [x for x in agents_all() if x.get("sessionId") not in before]
    s = Section("2", f"claude --resume {a['sessionId']} --bg")
    s.add("rc", r.get("rc", r.get("error")))
    s.add("out/err", short((r.get("out", "") + r.get("err", "")).strip(), 300))
    s.add("同じ名前の最新", {k: a2.get(k) for k in ("id", "sessionId", "pid", "status")} if a2 else None)
    s.add("新しくできた session", [{k: x.get(k) for k in ("id", "name", "pid")} for x in new] or "なし")
    same = bool(a2 and a2.get("sessionId") == a["sessionId"] and a2.get("pid"))
    s.add("判定(自動)", "同じ id で再開した" if same and not new else "コピーになった / 起きていない (上を見よ)")
    s.done()


# ---------------------------------------------------------------- transcript

def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def tx_summary(sid: str, s: Section, wanted=MARKERS, last_chars=500) -> list[dict]:
    path = transcript_path(sid)
    if not path:
        s.add("transcript", f"{sid}.jsonl が ~/.claude/projects/*/ に無い")
        return []
    entries = _jsonl(path)
    s.add("transcript", f"{fwd(path)} ({len(entries)} 行)")
    types: dict[str, int] = {}
    for e in entries:
        k = e.get("type", "?") + ("/" + e["subtype"] if e.get("subtype") else "")
        types[k] = types.get(k, 0) + 1
    s.add("行の種類", types)
    tok_seen: dict[str, dict] = {}
    mark_seen: dict[str, list] = {}
    tools, errors, last_text = [], [], ""
    for i, e in enumerate(entries):
        et = e.get("type", "?")
        role = (e.get("message") or {}).get("role") if isinstance(e.get("message"), dict) else None
        where = f"{i}:{et}" + (f"/{e.get('attachment', {}).get('type')}" if isinstance(e.get("attachment"), dict) else "")
        for text in _strings(e):
            for key, jp in TOKENS.items():
                for m in re.finditer(rf"VWTOK-{key}-(.{{0,4}})", text):
                    d = tok_seen.setdefault(key, {"where": [], "exact": False, "seen_as": set()})
                    d["where"].append(where)
                    d["seen_as"].add(m.group(1))
                    d["exact"] |= m.group(1).startswith(jp)
            for mk in wanted:
                if mk in text and "検証用" not in text:
                    pos = text.find(mk)
                    mark_seen.setdefault(mk, []).append(f"{where}: {short(text[pos:pos + 60], 60)}")
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                tools.append(f"{i}:{c.get('name')} {short(json.dumps(c.get('input'), ensure_ascii=False), 120)}")
            elif c.get("type") == "tool_result" and c.get("is_error"):
                errors.append(f"{i}: {short(json.dumps(c.get('content'), ensure_ascii=False), 160)}")
            elif c.get("type") == "text" and role == "assistant":
                last_text = c.get("text", "")
    for key, jp in TOKENS.items():
        if key in tok_seen:
            d = tok_seen[key]
            s.add(f"合言葉 VWTOK-{key}-{jp}", f"{'一致' if d['exact'] else '化けている'} "
                  f"(見えた形 {sorted(d['seen_as'])}, 場所 {d['where'][:6]})")
    for mk, where in mark_seen.items():
        s.add(f"印 {mk}", list(dict.fromkeys(where))[:6])
    s.add("ツールの呼び出し", tools[:20] or "なし")
    if errors:
        s.add("エラー/拒否の tool_result", errors[:10])
    s.add("最後の assistant の文", short(last_text, last_chars))
    return entries


def cmd_tx(args) -> None:
    a = need_seat(args[0])
    s = Section("tx", f"transcript: {a.get('name')} {a.get('sessionId')}")
    tx_summary(a["sessionId"], s)
    s.done()


# ---------------------------------------------------------------- [3] hook log

def cmd_hooklog(args) -> None:
    prefix = args[0] if args else ""
    recs = [r for r in _jsonl(HOOKLOG) if str(r.get("tag", "")).startswith(prefix)]
    s = Section("3", f"hook のログ ({prefix or '全部'}, {len(recs)} 件)")
    by: dict[str, list] = {}
    for r in recs:
        by.setdefault(r.get("tag", "?"), []).append(r)
    sourced = VW / "logs" / "bash_env_sourced.txt"
    if prefix in ("", "prof"):
        n = len(sourced.read_text(encoding="utf-8").splitlines()) if sourced.exists() else 0
        s.add("BASH_ENV (profile の代わり) を shell form の hook の bash が読んだ回数", n)
    first = next((r for r in recs if r.get("job") or r.get("which")), None)
    if first:
        s.add("hook の中の job / PATH (最初の 1 件)", {"tag": first.get("tag"), "job": first.get("job"),
                                                    "which": first.get("which"), "path_head": first.get("path_head")})
    for tag, rs in by.items():
        f = rs[0]
        jp = [r for r in rs if r.get("stdin_has_jp") or not r.get("stdin_utf8_ok", True)]
        s.add(tag, {
            "件数": len(rs), "events": sorted({str(r.get("event")) for r in rs}),
            "tools": sorted({str(r.get("tool_name")) for r in rs if r.get("tool_name")}),
            "stdin utf8 で読める": f"{sum(1 for r in rs if r.get('stdin_utf8_ok'))}/{len(rs)}",
            "日本語入りの stdin": len(jp),
            "日本語入りを既定の文字コードで読める": f"{sum(1 for r in jp if r.get('stdin_default_ok'))}/{len(jp)}",
            "stdout_encoding": f.get("stdout_encoding"), "preferred": f.get("preferred_encoding"),
            "meta": f.get("meta"),
            "env": {k: v for k, v in (f.get("env") or {}).items() if v and k not in ENV_NOISE},
        })
        for r in jp[:2]:
            if r.get("tool_input") or r.get("prompt"):
                s.add(f"  {tag} 日本語の入力の例", short(r.get("tool_input") or r.get("prompt"), 160))
        for r in rs:
            if r.get("exception") or r.get("stdin_not_json"):
                s.add(f"  {tag} 異常", short(r.get("exception") or r.get("stdin_not_json"), 400))
            if r.get("rewake"):
                s.add(f"  {tag} rewake", f"{r.get('t')} {r.get('rewake')} {short(r.get('text', ''), 80)}")
    s.done()


# ---------------------------------------------------------------- [4] rewake

def cmd_wake(args) -> None:
    text = (args[0] if args else "おはよう") + f" #{int(time.time())}"
    a = need_seat("hooks")
    with open(VW / "wake.txt", "a", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    t0 = time.time()
    s = Section("4", "Stop hook (async + asyncRewake) で idle の席を起こす")
    s.add("wake.txt に追記", text)
    woke = None
    while time.time() - t0 < 90 and not woke:
        time.sleep(3)
        path = transcript_path(a["sessionId"])
        entries = _jsonl(path) if path else []
        idx = next((i for i, e in enumerate(entries) if any(text in t for t in _strings(e))), None)
        if idx is not None and any(e.get("type") == "assistant" for e in entries[idx + 1:]):
            woke = (idx, entries)
    if not woke:
        s.add("判定(自動)", "90 秒以内に起きなかった (vw hooklog hooks-stop / vw tx hooks を見よ)")
    else:
        idx, entries = woke
        line = next(t for t in _strings(entries[idx]) if text in t)
        s.add("判定(自動)", f"起きた ({round(time.time() - t0)}s 以内)")
        s.add("席に入った文", short(line, 200))
        s.add("日本語がそのまま届いたか", f"起床の合図: {text}" in line)
    s.done()


# ---------------------------------------------------------------- [5] detach

def cmd_detach_check(args) -> None:
    d = VW / "detach"
    if "--wait" in args:
        t0 = time.time()
        while time.time() - t0 < DETACH_SECS + 90:
            dones = {p.stem for p in d.glob("*.done")}
            running = [p.stem for p in d.glob("*.start")
                       if p.stem not in dones and p.stem.startswith(("hook-", "tool-"))]
            if not running or all(time.time() - read_json(d / f"{n}.beat", {}).get("epoch", 0) > 10 for n in running):
                break
            print(f"… まだ動いている子: {running}", flush=True)
            time.sleep(5)
    stops = [e for e in events("stop") if e.get("seat") == "vw.detach"]
    stop_epoch = stops[-1]["epoch"] if stops else None
    s = Section("5", "切り離した子が席の stop のあとも生きるか")
    s.add("vw.detach を stop した時刻", stops[-1]["t"] if stops else "まだ stop していない")
    for sp in sorted(d.glob("spawn-*.json")):
        info = read_json(sp, {})
        s.add(f"{sp.name} (起こした側)", {"job": info.get("job"),
                                          "errors": {k: v.get("error") for k, v in info.get("children", {}).items() if not v.get("spawned")}})
    for st in sorted(d.glob("*.start")):
        label = st.stem
        if label.startswith(("pid-", "pterm-")):
            continue
        start = read_json(st, {})
        beat = read_json(d / f"{label}.beat", {})
        done = (d / f"{label}.done").exists()
        alive = pid_alive(int(start["pid"])).get("alive") if start.get("pid") else None
        last = beat.get("epoch", 0)
        if stop_epoch is None:
            verdict = "stop 前"
        elif done or last > stop_epoch + 5:
            verdict = "生き残った" + ("" if done else " (まだ動いている)")
        elif alive:
            verdict = "stop のあと beat が止まったが pid は生きている (要確認)"
        else:
            verdict = "stop で死んだ"
        s.add(label, {"判定": verdict, "done": done, "pid": start.get("pid"), "ppid": start.get("ppid"),
                      "job": start.get("job"), "最後の beat": beat.get("t"),
                      "stop からの秒": round(last - stop_epoch, 1) if stop_epoch and last else None})
    s.done()


# ---------------------------------------------------------------- [6] pid

def _popen_child(label: str, secs: int, group: bool) -> subprocess.Popen:
    kw = {"creationflags": CREATE_NEW_PROCESS_GROUP} if IS_WIN and group else {}
    return subprocess.Popen([sys.executable, str(VW / "kit" / "child.py"), "sleep", label, str(secs)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kw)


def cmd_pid(_args) -> None:
    s = Section("6", "os.kill(pid, 0) と OpenProcess + GetExitCodeProcess")
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)  # a stray console Ctrl+C must not stop us
    try:
        live_g = _popen_child("pid-live-group", 60, True)
        live_p = _popen_child("pid-live-plain", 60, False)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        missing = 4_000_000 - 4
        while pid_alive(missing).get("alive"):
            missing -= 4
        time.sleep(1.5)
        targets = {"生きている子 (新しいプロセスグループ)": live_g.pid, "生きている子 (同じグループ)": live_p.pid,
                   "終わった子 (handle は保持)": dead.pid, "存在しない pid": missing}
        for label, pid in targets.items():
            before = pid_alive(pid)
            kw = {"creationflags": CREATE_NEW_PROCESS_GROUP} if IS_WIN else {}
            r = run([sys.executable, str(VW / "kit" / "child.py"), "oskill", str(pid)], timeout=30, **kw)
            try:
                res = json.loads(r.get("out") or "")
            except ValueError:
                res = {"rc": r.get("rc"), "out": short(r.get("out")), "err": short(r.get("err"), 300),
                       "error": r.get("error")}
            time.sleep(1)
            s.add(label, {"pid": pid, "OpenProcess (前)": before, "os.kill(pid,0)": res,
                          "OpenProcess (後)": pid_alive(pid)})
        s.add("自分の pid", pid_alive(os.getpid()))
        for p in (live_g, live_p):
            p.kill()
    finally:
        signal.signal(signal.SIGINT, old)
    s.done()


# ---------------------------------------------------------------- [7] claude -p

def cmd_pterm(args) -> None:
    mode = args[0] if args else "terminate"
    if mode not in ("break", "terminate", "taskkill"):
        sys.exit("vw pterm break|terminate|taskkill")
    wait = 20
    sid = str(uuid.uuid4())
    label = f"pterm-{mode}"
    d = VW / "detach"
    for suf in (".start", ".beat", ".done"):
        (d / f"{label}{suf}").unlink(missing_ok=True)
    prompt = ("検証用です。次の 2 つを順に行え。\n1. 『VWP-BEFORE 開始します』と書け。\n"
              "2. Bash ツールで次のコマンドをそのまま実行し、終わるまで待て (120 秒かかる):\n"
              f"\"{fwd(sys.executable)}\" \"{fwd(VW / 'kit' / 'child.py')}\" sleep {label} 120")
    cmd = [claude_bin(), "-p", "--session-id", sid, "--model", "haiku", "--setting-sources", "project,local",
           "--settings", fwd(VW / "settings" / "life.json"), "--allowedTools", "Bash", "PowerShell",
           "--output-format", "stream-json", "--verbose", "--", prompt]
    out_path = VW / "logs" / f"{label}.out"
    kw = {"creationflags": CREATE_NEW_PROCESS_GROUP} if IS_WIN else {"start_new_session": True}
    s = Section("7", f"claude -p を止める ({mode})")
    s.add("session", sid)
    t0 = time.time()
    with open(out_path, "wb") as out:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                             cwd=str(VW / "cwd"), **kw)
        while time.time() - t0 < 150 and not (d / f"{label}.start").exists() and p.poll() is None:
            time.sleep(1)
        started = (d / f"{label}.start").exists()
        s.add("Bash の子 (sleep) が起動したか", f"{started} ({round(time.time() - t0)}s)")
        time.sleep(3)
        t_kill = time.time()
        try:
            if mode == "break":
                os.kill(p.pid, signal.CTRL_BREAK_EVENT if IS_WIN else signal.SIGINT)
            elif mode == "terminate":
                p.terminate()
            else:
                r = run(["taskkill", "/PID", str(p.pid), "/T"] if IS_WIN else ["kill", str(p.pid)])
                s.add("taskkill", short((r.get("out", "") + r.get("err", "")).strip() or r))
            s.add("送った", "ok")
        except (OSError, AttributeError) as e:
            s.add("送った", f"{type(e).__name__}: {e}")
        try:
            rc = p.wait(timeout=wait)
            s.add("終了", f"rc={rc} ({round(time.time() - t_kill, 1)}s 後)")
        except subprocess.TimeoutExpired:
            p.kill()
            s.add("終了", f"{wait}s 待っても終わらず kill した (rc={p.wait()})")
    time.sleep(5)
    start = read_json(d / f"{label}.start", {})
    beat = read_json(d / f"{label}.beat", {})
    if start.get("pid"):
        alive = pid_alive(int(start["pid"]))
        s.add("Bash の子 (sleep) は", {"pid": start["pid"], "生きている": alive.get("alive"),
                                       "最後の beat は止めた何秒後": round(beat.get("epoch", 0) - t_kill, 1)})
        if alive.get("alive"):
            try:
                os.kill(int(start["pid"]), signal.SIGTERM)  # Windows: TerminateProcess
            except OSError:
                pass
    raw = out_path.read_bytes()
    types = [json.loads(l).get("type") for l in raw.decode("utf-8", "replace").splitlines() if l.startswith("{")]
    s.add("stdout (stream-json)", f"{len(raw)} bytes, 行の種類 {sorted(set(types))}, result あり={'result' in types}")
    ends = [r for r in _jsonl(HOOKLOG) if r.get("session_id") == sid and r.get("event") == "SessionEnd"]
    s.add("SessionEnd hook", "走った" if ends else "走っていない")
    tx_summary(sid, s, ("VWP-BEFORE",))
    s.done()


# ---------------------------------------------------------------- [9] permissions

def cmd_perm_check(_args) -> None:
    s = Section("9", "permission の deny が効いたか")
    ship = VW / "ship"
    forms = {"team.yaml": "//c/... (docs の形)", "team2.yaml": "/C:/... (as_posix)",
             "team3.yaml": "/C:\\... (yamato の今の形)", "other.yaml": "deny なし (対照)"}
    for n, form in forms.items():
        text = (ship / n).read_text(encoding="utf-8", errors="replace")
        edits = re.findall(r"edited-\d", text)
        s.add(f"{n} [{form}]", f"書き換えられた {edits}" if edits else "書き換えられていない")
    rules = read_json(VW / "settings" / "perm.json", {}).get("permissions", {}).get("deny", [])
    s.add("deny の規則", rules)
    pre = [r for r in _jsonl(HOOKLOG) if r.get("tag") == "perm-pre"]
    s.add("PreToolUse で見えたツール (順)", [f"{r.get('tool_name')} {short(r.get('tool_input'), 90)}" for r in pre][:20])
    denied = [r for r in _jsonl(HOOKLOG) if r.get("tag") == "perm-denied"]
    s.add("PermissionDenied hook", [f"{r.get('tool_name')} {short(r.get('tool_input'), 90)}" for r in denied] or "なし")
    a = find_seat("perm")
    if a:
        tx_summary(a["sessionId"], s, ("DENYME", "PSDENY", "VW-PERM"), last_chars=2500)
    s.done()


# ---------------------------------------------------------------- [10] lock

def cmd_lock(_args) -> None:
    s = Section("10", "msvcrt.locking が 2 プロセスの間で効くか")
    if not IS_WIN:
        s.add("Windows ではないので飛ばした")
        s.done()
        return
    import msvcrt
    path = VW / "lock" / "test.lock"
    path.write_bytes(b"")
    probe = lambda mode: run([sys.executable, str(VW / "kit" / "child.py"), "lockprobe", str(path), mode],  # noqa: E731
                             timeout=60)

    def res(r):
        try:
            return json.loads(r.get("out") or "")
        except ValueError:
            return r
    f = open(path, "r+b")
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    s.add("親が空のファイルの 1 バイト目を LK_NBLCK", "取れた")
    s.add("A. 別プロセスが LK_NBLCK (取れないはず)", res(probe("nb")))
    g = open(path, "r+b")
    try:
        g.seek(0)
        msvcrt.locking(g.fileno(), msvcrt.LK_NBLCK, 1)
        s.add("B. 同じプロセスの別の handle で LK_NBLCK", "取れた (handle ごとではない)")
        g.seek(0)
        msvcrt.locking(g.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError as e:
        s.add("B. 同じプロセスの別の handle で LK_NBLCK", f"取れない ({e})")
    g.close()
    s.add("C. 別プロセスが LK_LOCK で待つ (親は離さない)", res(probe("lk")))
    bg = subprocess.Popen([sys.executable, str(VW / "kit" / "child.py"), "lockprobe", str(path), "lk"],
                          stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    time.sleep(3)
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    out, _ = bg.communicate(timeout=60)
    s.add("D. 別プロセスが LK_LOCK で待つ (親は 3 秒後に離す)", res({"out": out.decode("utf-8", "replace")}))
    s.add("E. 離したあと別プロセスが LK_NBLCK", res(probe("nb")))
    f.close()
    s.done()


# ---------------------------------------------------------------- note / cleanup

def cmd_note(args) -> None:
    if len(args) < 2:
        sys.exit('vw note <項目の番号> "<見たこと>"')
    s = Section(args[0], "owner のメモ")
    s.add(" ".join(args[1:]))
    s.done()


def cmd_cleanup(_args) -> None:
    s = Section("片付け", "vw.* の席と子プロセス")
    for a in [a for a in agents_all() if str(a.get("name", "")).startswith("vw.")]:
        if a.get("pid"):
            r = claude("stop", a.get("id"))
            s.add(f"stop {a.get('name')} {a.get('id')}", r.get("rc", r.get("error")))
            wait_pid(a["name"], False, 30)
        r = claude("rm", a.get("id"))
        s.add(f"rm {a.get('name')} {a.get('id')}", r.get("rc", r.get("error")))
    leftovers = [(st.stem, read_json(st, {})) for st in (VW / "detach").glob("*.start")]
    try:
        watcher = VW / "logs" / "rewake.pid"
        leftovers.append(("rewake の待ち受け", {"pid": int(watcher.read_text()), "epoch": watcher.stat().st_mtime}))
    except (OSError, ValueError):
        pass
    for label, info in leftovers:
        pid = info.get("pid")
        if not pid or time.time() - info.get("epoch", 0) > 86400:
            continue
        alive = pid_alive(int(pid))
        image = str(alive.get("image") or "python").lower()
        if alive.get("alive") and "python" in image:
            try:
                os.kill(int(pid), signal.SIGTERM)
                s.add(f"残っていた子 {label} (pid {pid})", "止めた")
            except OSError as e:
                s.add(f"残っていた子 {label} (pid {pid})", str(e))
    left = [a.get("name") for a in agents_all() if str(a.get("name", "")).startswith("vw.")]
    s.add("残った vw.* の席", left or "0 件")
    s.done()
    print(f"結果: {fwd(RESULTS)}  (フォルダを消す前に、ホームに写して送る)")


COMMANDS = {
    "setup": cmd_setup, "env": cmd_env, "up": cmd_up, "wait": cmd_wait, "agents": cmd_agents, "sid": cmd_sid,
    "stop": cmd_stop, "rm": lambda a: cmd_stop(a, "rm"), "resume": cmd_resume, "tx": cmd_tx,
    "hooklog": cmd_hooklog, "wake": cmd_wake, "detach-check": cmd_detach_check, "pid": cmd_pid,
    "pterm": cmd_pterm, "perm-check": cmd_perm_check, "lock": cmd_lock, "note": cmd_note,
    "cleanup": cmd_cleanup,
}


def main(argv: list[str]) -> int:
    utf8_stdio()
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    if argv[0] != "setup" and not (VW / ".vw").exists():
        sys.exit(f"{fwd(VW)} がありません。先に setup を打つ")
    if argv[0] in ("wait", "sid", "stop", "rm", "resume", "tx") and len(argv) < 2:
        sys.exit(f"vw {argv[0]} <seat>")
    COMMANDS[argv[0]](argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
