"""Startup banner for ``yamato up`` (sortie) and ``yamato ship create`` (launch): the battleship
Yamato against a rising sun.

The banner is decoration. It must never disturb real output and never make a command fail:

* It is written only to a terminal (a TTY stdout). Through a pipe or a redirect, and from a seat
  (Claude Code's Bash), nothing is printed, so code that parses the output and the seat's own
  conversation stay clean. ``--quiet`` and ``$YAMATO_NO_BANNER`` turn it off too.
* Color only on a TTY with ``NO_COLOR`` unset. Without color the ship still reads by shape.
* The size follows the terminal: large (98 columns) / medium (62) / small (40) / one line. No line
  is ever wider than ``columns - 1``.
* The picture is drawn on a half-block canvas (one pixel = one column x half a row). A stdout
  encoding that cannot encode the blocks or Japanese gets ASCII / English instead.
* Any error while rendering or writing is swallowed.

Widths are counted with "ambiguous = one column" (box drawing and blocks are ambiguous). A terminal
set to CJK double-width breaks the layout; ``YAMATO_NO_BANNER=1`` is the way out. Stdlib only.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import sys
import time
import unicodedata
from typing import TextIO

NO_BANNER_ENV = "YAMATO_NO_BANNER"

RESET = "\033[0m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# One rendered line: a list of (text, SGR parameters or None).
Line = list[tuple[str, str | None]]


# --- text width -----------------------------------------------------------------

def _cw(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Columns ``text`` takes on a terminal (SGR codes are free, full-width is 2, ambiguous is 1)."""
    return sum(_cw(c) for c in _ANSI_RE.sub("", text))


def _clip(text: str, cols: int) -> str:
    out, used = [], 0
    for c in text:
        w = _cw(c)
        if used + w > cols:
            break
        out.append(c)
        used += w
    return "".join(out)


def _can_encode(text: str, encoding: str | None) -> bool:
    """Whether ``encoding`` writes ``text`` (strict). An unknown encoding (a StringIO) is taken as capable."""
    if not encoding:
        return True
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _rstrip(line: Line) -> Line:
    line = list(line)
    while line and not line[-1][0].strip():
        line.pop()
    if line:
        line[-1] = (line[-1][0].rstrip(), line[-1][1])
    return line


def _block(lines: list[Line], columns: int) -> list[Line]:
    """Center a block by its widest line: the same left pad for every row (per-row centering shears the art)."""
    pad = max(0, (columns - max((_line_width(ln) for ln in lines), default=0)) // 2)
    return [([(" " * pad, None)] + ln) if pad and ln else ln for ln in lines]


def _line_width(line: Line) -> int:
    return sum(display_width(t) for t, _ in line)


def _to_text(lines: list[Line], columns: int, color: bool) -> str:
    """Lines to a string. Each is cut at ``columns - 1``: a write to the last column wraps on some terminals."""
    room = columns - 1
    out = []
    for line in lines:
        text, used = [], 0
        for seg, sgr in _rstrip(line):
            seg = _clip(seg, room - used)
            used += display_width(seg)
            text.append(f"\033[{sgr}m{seg}{RESET}" if (color and sgr and seg) else seg)
        out.append("".join(text))
    return "\n".join(out) + "\n"


# --- half-block canvas ------------------------------------------------------------
# A pixel has a kind; the kind decides its color and its ASCII stand-in.

_FG = {"sun": 91, "ray": 93, "ship": 37, "smoke": 90, "sea": 34, "crest": 36, "foam": 97}
_ASCII = {"ship": "#", "sun": "@", "smoke": ":", "foam": "*", "ray": ".", "crest": "~", "sea": "~"}
_PRIORITY = ("ship", "sun", "smoke", "foam", "ray", "crest", "sea")   # what wins when a cell holds two kinds
_MONO_BLOCK = {"sun": "░"}       # without color the sun behind the ship is a light fill, not '@'
_BLOCK_CHARS = "█▀▄░━╗═║"        # what the stdout encoding must be able to write for the block art


class Canvas:
    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height + (height % 2)          # two pixel rows make one text row
        self.px: list[list[str | None]] = [[None] * self.w for _ in range(self.h)]

    def set(self, x: int, y: int, kind: str) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = kind

    def rect(self, x0: int, y0: int, x1: int, y1: int, kind: str) -> None:
        for y in range(y0, y1):
            for x in range(x0, x1):
                self.set(x, y, kind)

    def rows(self, *, color: bool, blocks: bool) -> list[Line]:
        out = []
        for r in range(0, self.h, 2):
            segs: Line = []
            for x in range(self.w):
                ch, sgr = self._cell(self.px[r][x], self.px[r + 1][x], color, blocks)
                if segs and segs[-1][1] == sgr:
                    segs[-1] = (segs[-1][0] + ch, sgr)
                else:
                    segs.append((ch, sgr))
            out.append(segs)
        return out

    @staticmethod
    def _cell(t: str | None, b: str | None, color: bool, blocks: bool) -> tuple[str, str | None]:
        """One text cell from its upper and lower pixel."""
        if t is None and b is None:
            return " ", None
        if color and blocks:
            if t == b:
                return "█", str(_FG[t])
            if t is None:
                return "▄", str(_FG[b])
            if b is None:
                return "▀", str(_FG[t])
            return "▀", f"{_FG[t]};{_FG[b] + 10}"       # upper pixel as foreground, lower as background
        if blocks and "ship" in (t, b):                   # no color: only the ship keeps its half blocks
            if t == b:
                return "█", None
            return ("▀" if t == "ship" else "▄"), None
        kind = next(k for k in _PRIORITY if k in {t, b})
        ch = _MONO_BLOCK.get(kind, _ASCII[kind]) if blocks else _ASCII[kind]
        return ch, (str(_FG[kind]) if color else None)


# --- the ship (Yamato, bow to the right) ---------------------------------------------
# Parts are (x0, x1, h0, h1): x from the stern, h the height above the waterline, in pixels, half-open.
# The smaller ships scale the same coordinates and round (thin parts collapse to one pixel).

_D = 4                          # deck height
_SHIP_LEN = 81                  # length in pixels, to the tip of the barrels
_SMOKE = ((21, _D + 11), (18, _D + 13), (15, _D + 14), (12, _D + 16), (9, _D + 17), (6, _D + 19))   # (x, h) drifting aft
_FLAG = (42, 45, _D + 21, _D + 23)   # the ensign at the foremast


def _yamato_parts() -> list[tuple[int, int, int, int]]:
    d = _D
    parts = [
        (4, 66, -1, d),                                     # hull
        (54, 66, d, d + 1),                                 # sheer of the forecastle
        (6, 13, d, d + 3), (7, 12, d + 3, d + 4),           # turret 3 (trained aft)
        (0, 6, d + 1, d + 3),                               #   its barrels
        (16, 22, d, d + 3), (17, 21, d + 3, d + 5),         # after superstructure
        (19, 20, d + 5, d + 12), (17, 22, d + 9, d + 10),   #   mast and yard
        (48, 52, d, d + 2), (49, 51, d + 2, d + 3),         # secondary turret
        (55, 64, d + 1, d + 3), (55, 64, d + 3, d + 7), (56, 63, d + 7, d + 8),   # turret 2 (superfiring)
        (64, 76, d + 5, d + 7),                             #   its barrels
        (65, 73, d + 1, d + 4), (66, 72, d + 4, d + 5),     # turret 1
        (73, 81, d + 2, d + 4),                             #   its barrels
        # the pagoda tower: tiers narrow going up, with lookout platforms
        (34, 47, d, d + 3),
        (35, 46, d + 3, d + 6), (34, 47, d + 6, d + 7),
        (36, 45, d + 7, d + 9), (35, 46, d + 9, d + 10),
        (37, 44, d + 10, d + 12), (36, 45, d + 12, d + 13),
        (38, 43, d + 13, d + 15), (37, 44, d + 15, d + 16),
        (40, 41, d + 16, d + 23),                           # foremast
        (38, 43, d + 19, d + 20),                           #   yard
    ]
    for h, x_end in zip(range(-1, d + 2), (66, 67, 68, 70, 72, 74, 75)):   # the bow leans forward
        parts.append((66, x_end, h, h + 1))
    for h in range(d, d + 10):                                             # the funnel rakes aft
        k = (h - d) // 3
        parts.append((25 - k, 31 - k, h, h + 1))
    return parts


def _scale(v: int, s: float) -> int:
    return int(round(v * s))


def _draw_ship(cv: Canvas, ox: int, base: int, s: float) -> None:
    """``ox`` is the stern's x, ``base`` the pixel row of the waterline (from the top)."""
    for x0, x1, h0, h1 in _yamato_parts():
        a, b = _scale(x0, s), max(_scale(x0, s) + 1, _scale(x1, s))
        lo, hi = _scale(h0, s), max(_scale(h0, s) + 1, _scale(h1, s))
        cv.rect(ox + a, base - hi, ox + b, base - lo, "ship")
    x0, x1, h0, h1 = _FLAG
    if s >= 0.5:
        cv.rect(ox + _scale(x0, s), base - _scale(h1, s), ox + max(_scale(x0, s) + 1, _scale(x1, s)),
                base - _scale(h0, s), "sun")


def _draw_sun(cv: Canvas, cx: int, horizon: int, radius: int, ray_from: int, ray_len: int) -> None:
    """A half disc rising from pixel row ``horizon``, with rays every 15 degrees around it."""
    for h in range(radius):
        dx = math.sqrt(max(0.0, radius * radius - (h + 0.5) ** 2))
        cv.rect(int(round(cx - dx)), horizon - h - 1, int(round(cx + dx)) + 1, horizon - h, "sun")
    for deg in range(15, 166, 15) if ray_len else ():
        a = math.radians(deg)
        for r in range(ray_from, ray_from + ray_len):
            cv.set(int(round(cx + r * math.cos(a))), horizon - 1 - int(round(r * math.sin(a))), "ray")


def _draw_sea(cv: Canvas, base: int, waves: int, ox: int, s: float) -> None:
    """Wave lines from the waterline down (the first over the keel), bow spray and a wake astern."""
    for i in range(waves):
        for x in range(cv.w):
            cv.set(x, base + 4 * i + int(round(math.sin(x * 0.3 + i * 1.7))), "sea" if i % 2 == 0 else "crest")
    bow = ox + _scale(72, s)
    for dx, dy in ((0, 0), (1, 0), (2, 0), (0, -1), (1, -1), (3, 1), (2, 1), (0, 1)):
        cv.set(bow + dx, base - 1 + dy, "foam")
    stern = ox + _scale(4, s)
    for k in range(1, 9):
        cv.set(stern - 3 * k, base + (k % 2), "foam" if k % 3 else "crest")


def _draw_gulls(cv: Canvas, spots: tuple[tuple[int, int], ...]) -> None:
    for x, y in spots:
        cv.set(x, y + 1, "foam")
        cv.set(x + 1, y, "foam")
        cv.set(x + 2, y + 1, "foam")


# name: (width, ship scale, sun radius, where the rays start, ray length, wave lines)
_SCENES = {
    "large": (96, 1.0, 22, 25, 12, 3),
    "medium": (60, 0.62, 15, 17, 8, 2),
    "small": (38, 0.4, 9, 0, 0, 2),
}


def scene(name: str, *, decor: bool = True) -> Canvas:
    """Sun, ship and sea at one size. ``decor`` adds the pieces that only make sense in color (smoke, gulls)."""
    width, s, radius, ray_from, ray_len, waves = _SCENES[name]
    ship_len = _scale(_SHIP_LEN, s)
    sky = max(ray_from + ray_len if ray_len else radius, _scale(_D + 24, s)) + 2     # pixel rows above the waterline
    cv = Canvas(width, sky + 4 * waves)
    base = sky
    ox = (width - ship_len) // 2 + 2
    _draw_sun(cv, ox + int(ship_len * 0.47), base, radius, ray_from, ray_len)        # the sun is behind the tower
    _draw_ship(cv, ox, base, s)
    _draw_sea(cv, base, waves, ox, s)
    if decor and s >= 0.5:
        for k, (x, h) in enumerate(_SMOKE):
            y = base - _scale(h, s) - 1
            for dx in (0, 1):
                cv.set(ox + _scale(x, s) + dx, y, "smoke")
            if k % 2 == 0:
                cv.set(ox + _scale(x, s), y - 1, "smoke")
    if decor and name == "large":
        _draw_gulls(cv, ((6, 7), (15, 12), (width - 14, 9)))
    return cv


# --- logo, title, info ---------------------------------------------------------------

_LOGO_BIG = (
    "██╗   ██╗ █████╗ ███╗   ███╗ █████╗ ████████╗ ██████╗ ",
    "╚██╗ ██╔╝██╔══██╗████╗ ████║██╔══██╗╚══██╔══╝██╔═══██╗",
    " ╚████╔╝ ███████║██╔████╔██║███████║   ██║   ██║   ██║",
    "  ╚██╔╝  ██╔══██║██║╚██╔╝██║██╔══██║   ██║   ██║   ██║",
    "   ██║   ██║  ██║██║ ╚═╝ ██║██║  ██║   ██║   ╚██████╔╝",
    "   ╚═╝   ╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝    ╚═════╝ ",
)
_LOGO_SMALL = (          # plain ASCII: for the small size and for terminals without the block characters
    "Y   Y  AAA  M   M  AAA  TTTTT  OOO ",
    " Y Y  A   A MM MM A   A   T   O   O",
    "  Y   AAAAA M M M AAAAA   T   O   O",
    "  Y   A   A M   M A   A   T   O   O",
    "  Y   A   A M   M A   A   T    OOO ",
)

# kind: (Japanese title, English title, Japanese subtitle)
_TITLE = {
    "up": ("抜 錨 ・ 出 撃", "SORTIE", "天気晴朗ナレドモ波高シ"),
    "create": ("進 水", "LAUNCH", "新造艦、本日進水セリ"),
}
_JA_SAMPLE = "艦出撃進水稼働上限ひな形天気晴朗ナレドモ波高シ新造艦、本日セリ抜錨・まで"   # what a Japanese banner writes

_WHITE, _RED, _DIM = "1;97", "31", "2"


def _logo_lines(rows: tuple[str, ...], color: bool) -> list[Line]:
    out = []
    for row in rows:
        segs: Line = []
        for ch in row:
            sgr = (_WHITE if ch in "█YAMTO" else _RED) if color else None    # letters white, shadow red
            if segs and segs[-1][1] == sgr:
                segs[-1] = (segs[-1][0] + ch, sgr)
            else:
                segs.append((ch, sgr))
        out.append(segs)
    return out


def _title_lines(kind: str, ja: bool, blocks: bool, color: bool) -> list[Line]:
    ja_title, en_title, ja_sub = _TITLE[kind]
    bar = ("━" if blocks else "=") * 10
    red, white = (_RED, _WHITE) if color else (None, None)
    out: list[Line] = [[(bar + "  ", red), (ja_title if ja else " ".join(en_title), white), ("  " + bar, red)]]
    if ja:
        out.append([(ja_sub, _DIM if color else None)])
    return out


def info_lines(kind: str, *, name: str, template: str | None, captain: str | None, span: str | None,
               until: str | None, columns: int, color: bool, ja: bool) -> list[Line]:
    """Ship name, template, uptime and captain seat; wrapped between items when they do not fit."""
    pairs: list[tuple[str, str]] = [("艦" if ja else "ship", name)]
    if template:
        pairs.append(("ひな形" if ja else "template", template))
    if span:
        head = ("稼働" if kind == "up" else "稼働上限") if ja else ("uptime" if kind == "up" else "limit")
        tail = (f" ({until} まで)" if ja else f" (until {until})") if until else ""
        pairs.append((head, span + tail))
    if captain:
        pairs.append(("captain", captain))
    sep = "   "
    lines: list[Line] = []
    cur: Line = []
    for k, v in pairs:
        item: Line = [(k + " ", _DIM if color else None), (v, _WHITE if color else None)]
        if cur and _line_width(cur) + len(sep) + _line_width(item) > columns - 2:
            lines.append(cur)
            cur = []
        if cur:
            cur.append((sep, None))
        cur.extend(item)
    if cur:
        lines.append(cur)
    return lines


def render(kind: str, *, name: str, template: str | None = None, captain: str | None = None,
           span: str | None = None, until: str | None = None, columns: int = 80, color: bool = False,
           encoding: str | None = "utf-8") -> str:
    """The banner as newline-terminated lines, each at most ``columns - 1`` wide; ``""`` if the terminal is too narrow.

    ``kind`` is ``"up"`` or ``"create"``. ``span`` is the uptime (the limit, for ``create``) and ``until``
    the clock time it ends.
    """
    if kind not in _TITLE:
        raise ValueError(kind)
    blocks = _can_encode(_BLOCK_CHARS, encoding)
    ja = _can_encode(_JA_SAMPLE, encoding)
    room = columns - 1
    if room < 12:
        return ""
    if room >= 98:
        tier = "large"
    elif room >= 62:
        tier = "medium"
    elif room >= 40:
        tier = "small"
    else:
        tag = _TITLE[kind][0].replace(" ", "") if ja else _TITLE[kind][1]
        return _to_text([[(f"YAMATO {tag} {name}", _WHITE if color else None)]], columns, color)
    art = scene(tier, decor=color).rows(color=color, blocks=blocks)
    logo = _logo_lines(_LOGO_BIG if tier != "small" and blocks else _LOGO_SMALL, color)
    info = info_lines(kind, name=name, template=template, captain=captain, span=span, until=until,
                      columns=columns, color=color, ja=ja)
    lines = _block(art, columns) + [[]] + _block(logo, columns) + [[]]
    lines += [c for ln in _title_lines(kind, ja, blocks, color) for c in _block([ln], columns)] + [[]]
    lines += [c for ln in info for c in _block([ln], columns)] + [[]]
    return _to_text(lines, columns, color)


# --- when to print, and the entry points ----------------------------------------------

def enabled(stream: TextIO, *, quiet: bool = False, env=None) -> bool:
    """True on a terminal, unless turned off (``--quiet``, ``$YAMATO_NO_BANNER``) or called from a seat."""
    env = os.environ if env is None else env
    if quiet or env.get(NO_BANNER_ENV, "") not in ("", "0"):
        return False
    if env.get("CLAUDECODE"):            # a seat (Claude Code's Bash) is calling
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def add_quiet(parser) -> None:
    parser.add_argument("-q", "--quiet", action="store_true",
                        help=f"バナーを出さない (環境変数 {NO_BANNER_ENV}=1 でも消せる)")


def show(kind: str, shipdir, *, template: str | None = None, span: str | None = None, quiet: bool = False,
         stream: TextIO | None = None) -> None:
    """Print the banner for ``"up"`` / ``"create"``, reading name, captain and time limit from the ship's
    team.yaml. ``span`` is ``--for`` (it wins over ``time_limit``). Never raises."""
    try:
        stream = stream if stream is not None else sys.stdout
        if not enabled(stream, quiet=quiet):
            return
        from .team import load_team
        from .util import fmt_span, parse_duration

        team = load_team(shipdir)
        limit = parse_duration(span) if span else team["time_limit"]
        until = time.strftime("%m-%d %H:%M", time.localtime(time.time() + limit)) if kind == "up" and limit else None
        text = render(kind, name=team["name"], template=template, captain=team["hub"],
                      span="上限なし" if limit is None else fmt_span(limit),
                      until=until, columns=shutil.get_terminal_size().columns,
                      color="NO_COLOR" not in os.environ, encoding=getattr(stream, "encoding", None))
        if text:
            stream.write(text)
            stream.flush()
    except Exception:
        pass
