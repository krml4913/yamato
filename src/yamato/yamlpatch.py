"""yaml の部分置き換え: コメントと並びを保って値を変える (T-110, web コンソールの team.yaml 編集用)。

PyYAML の ``compose`` が返すノードの位置 (``start_mark`` / ``end_mark``) を使い、変えた所の文字範囲
だけを元のテキストで置き換える。**dump はしない** (コメント・引用符・空行・並びは触った所の外では
一文字も変わらない)。何も変えなければ、そもそも文字列を作り直さない。

扱える操作 (パスは ``("roles", "pm", "model")`` のようなキー (str) と添字 (int) の並び):

- ``set_value``    scalar の値を変える
- ``add_key``      map にキーを足す (末尾)
- ``remove_key``   map からキーを消す
- ``insert_item``  list に要素を足す (``index=None`` で末尾)
- ``remove_item``  list から要素を消す

block 形式 (インデントで書く) も flow 形式 (``{a: 1}`` ``[a, b]``) も扱う。複数行の文字列
(``|`` ``>``) は、元が block scalar なら block scalar のまま差し替える。

扱えない形は ``YamlPatchError`` を投げる (呼ぶ側は生の yaml 欄に回す): anchor / alias / merge key
(``&a`` ``*a`` ``<<``)・複数文書・scalar を collection に (またはその逆に) 変える操作・
最後の 1 つの要素やキーを消す操作・``- - x`` のような同じ行に始まる入れ子の block list など。

安全網: 組み立てた結果を ``yaml.safe_load`` で読み直し、元の値に操作を当てた期待値と一致しなければ
``YamlPatchError`` にする。一致しないテキストを返すことはない。
"""
from __future__ import annotations

import copy
import json
import math
import re

INDENT = 2   # 新しく作る入れ子のインデント幅


class YamlPatchError(ValueError):
    """この形の yaml は部分置き換えで扱えない (または操作が不正)。生の yaml 欄で直す。"""


def _yaml():
    import yaml   # vendored; 他のモジュール同様、使うときまで import しない
    return yaml


# ---------------------------------------------------------------- 読む

def _compose(text: str):
    yaml = _yaml()
    try:
        docs = list(yaml.compose_all(text))
    except yaml.YAMLError as e:
        raise YamlPatchError(f"yaml を読めない: {e}") from None
    if len(docs) != 1 or docs[0] is None:
        raise YamlPatchError("yaml の文書が 1 つではない (空・複数文書は扱えない)")
    root = docs[0]
    seen: set[int] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if id(n) in seen:
            raise YamlPatchError("anchor / alias は扱えない")
        seen.add(id(n))
        if isinstance(n, yaml.MappingNode):
            for k, v in n.value:
                if k.tag == "tag:yaml.org,2002:merge":
                    raise YamlPatchError("merge key (<<) は扱えない")
                stack += [k, v]
        elif isinstance(n, yaml.SequenceNode):
            stack += n.value
    return root


class _Walk:
    """パスをたどった結果: 対象ノード・親・祖先に flow があるか。"""
    def __init__(self, node, parent, in_flow):
        self.node, self.parent, self.in_flow = node, parent, in_flow


def _walk(root, path) -> _Walk:
    yaml = _yaml()
    node, parent = root, None
    in_flow = False
    for step in path:
        in_flow = in_flow or bool(getattr(node, "flow_style", False))
        if isinstance(node, yaml.MappingNode):
            if not isinstance(step, str):
                raise YamlPatchError(f"map には str のキーで当てる: {step!r}")
            for k, v in node.value:
                if isinstance(k, yaml.ScalarNode) and k.value == step:
                    parent, node = node, v
                    break
            else:
                raise YamlPatchError(f"キーが無い: {step!r}")
        elif isinstance(node, yaml.SequenceNode):
            if isinstance(step, bool) or not isinstance(step, int):
                raise YamlPatchError(f"list には int の添字で当てる: {step!r}")
            if not -len(node.value) <= step < len(node.value):
                raise YamlPatchError(f"添字が範囲外: {step}")
            parent, node = node, node.value[step]
        else:
            raise YamlPatchError(f"scalar の下はたどれない: {step!r}")
    return _Walk(node, parent, in_flow)


def _is_block_scalar(n) -> bool:
    return n.style in ("|", ">")


def _content_end(n) -> int:
    """ノードの中身が終わる位置 (末尾の空行・コメントは含まない)。"""
    yaml = _yaml()
    if isinstance(n, yaml.MappingNode) and not n.flow_style and n.value:
        return _content_end(n.value[-1][1])
    if isinstance(n, yaml.SequenceNode) and not n.flow_style and n.value:
        return _content_end(n.value[-1])
    return n.end_mark.index


def _line_start(text: str, i: int) -> int:
    return text.rfind("\n", 0, i) + 1


def _after_line(text: str, e: int) -> int:
    """位置 e のある行の次の行頭 (改行の無い最終行なら末尾)。block scalar のように行頭で終わるなら e。"""
    if e > 0 and text[e - 1] == "\n":
        return e
    j = text.find("\n", e)
    return len(text) if j < 0 else j + 1


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _insert_lines(text: str, pos: int, lines: list[str]) -> str:
    nl = _newline(text)
    block = "".join(line + nl for line in lines)
    if pos == len(text) and text and not text.endswith("\n"):
        block = nl + block
    return text[:pos] + block + text[pos:]


# ---------------------------------------------------------------- 書く (値 → テキスト)

_PLAIN_BAD_START = set("-?:,[]{}#&*!|>'\"%@`")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x85\u2028\u2029]")


def _dq(s: str) -> str:
    out = json.dumps(s, ensure_ascii=False)
    return out.replace("\x7f", "\\x7f").replace("\x85", "\\N").replace("\u2028", "\\L").replace("\u2029", "\\P")


def _plain_ok(s: str, flow: bool) -> bool:
    if not s or s != s.strip() or "\n" in s or "\r" in s or "\t" in s or _CONTROL.search(s):
        return False
    if s[0] in _PLAIN_BAD_START or ": " in s or " #" in s or s.endswith(":"):
        return False
    if flow and any(c in s for c in ",[]{}"):
        return False
    try:
        return _yaml().safe_load(s) == s
    except _yaml().YAMLError:
        return False


def _scalar(value, *, flow: bool, hint: str | None = None) -> str:
    """1 行の scalar。hint は元の引用符 (' か ") — 同じ書き方に寄せる。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise YamlPatchError(f"有限でない float は書けない: {value!r}")
        return repr(value)
    if not isinstance(value, str):
        raise YamlPatchError(f"scalar にできない型: {type(value).__name__}")
    if hint == "'" and not _CONTROL.search(value) and not re.search(r"[\n\r\t]", value):
        return "'" + value.replace("'", "''") + "'"
    if hint != '"' and _plain_ok(value, flow):
        return value
    return _dq(value)


def _flow(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_scalar(k, flow=True)}: {_flow(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_flow(v) for v in value) + "]"
    return _scalar(value, flow=True)


def _literal_ok(s: str) -> bool:
    if "\n" not in s.rstrip("\n") or _CONTROL.search(s) or "\r" in s or "\t" in s:
        return False
    lines = s.split("\n")
    return not lines[0].startswith(" ") and not any(not ln.strip() and ln for ln in lines)


def _literal(s: str, indent: int) -> list[str]:
    """``|`` の行の並び (最初の要素が見出し)。"""
    chomp = "" if s.endswith("\n") and not s.endswith("\n\n") else ("-" if not s.endswith("\n") else "+")
    body = s[:-1] if s.endswith("\n") else s
    pad = " " * indent
    return ["|" + chomp] + [(pad + ln) if ln else "" for ln in body.split("\n")]


def _entry_lines(prefix: str, value, indent: int) -> list[str]:
    """``prefix`` (``"key:"`` や ``"-"``) に value を続けた block の行 (インデントは indent 桁)。"""
    pad = " " * indent
    if isinstance(value, dict) and value:
        return [pad + prefix] + _map_lines(value, indent + INDENT)
    if isinstance(value, (list, tuple)) and value and prefix != "-":
        return [pad + prefix] + _seq_lines(value, indent + INDENT)
    if isinstance(value, str) and _literal_ok(value):
        head, *body = _literal(value, indent + INDENT)
        return [f"{pad}{prefix} {head}"] + body
    if isinstance(value, (dict, list, tuple)):
        return [f"{pad}{prefix} {_flow(value)}"]
    return [f"{pad}{prefix} {_scalar(value, flow=False)}"]


def _map_lines(d: dict, indent: int) -> list[str]:
    out: list[str] = []
    for k, v in d.items():
        out += _entry_lines(_scalar(k, flow=False) + ":", v, indent)
    return out


def _seq_lines(items, indent: int) -> list[str]:
    out: list[str] = []
    for v in items:
        if isinstance(v, dict) and v:
            sub = _map_lines(v, indent + INDENT)
            out.append(" " * indent + "- " + sub[0][indent + INDENT:])
            out += sub[1:]
        else:
            out += _entry_lines("-", v, indent)
    return out


# ---------------------------------------------------------------- 期待値 (安全網)

def _target(doc, path):
    for step in path:
        doc = doc[step]
    return doc


def _check(original: str, new: str, mutate) -> str:
    yaml = _yaml()
    expected = copy.deepcopy(yaml.safe_load(original))
    mutate(expected)
    try:
        got = yaml.safe_load(new)
    except yaml.YAMLError as e:
        raise YamlPatchError(f"置き換えた結果が yaml として読めない (扱えない形): {e}") from None
    if got != expected:
        raise YamlPatchError("置き換えた結果が意図した値と合わない (扱えない形)")
    return new


# ---------------------------------------------------------------- 操作

def set_value(text: str, path, value) -> str:
    """path の scalar を value に変える (元の引用符・block scalar の書き方に寄せる)。"""
    yaml = _yaml()
    path = tuple(path)
    if not path:
        raise YamlPatchError("path が空")
    w = _walk(_compose(text), path)
    n = w.node
    if not isinstance(n, yaml.ScalarNode):
        raise YamlPatchError("scalar 以外 (map / list) は set_value では変えられない")
    if isinstance(value, (dict, list, tuple)):
        raise YamlPatchError("scalar を map / list に変える操作は扱えない")
    start, end = n.start_mark.index, n.end_mark.index
    nl = _newline(text)
    if _is_block_scalar(n):
        old = text[start:end]
        if isinstance(value, str) and _literal_ok(value):
            indent = w.parent.start_mark.column + INDENT
            new = nl.join(_literal(value, indent)) + nl
        else:
            new = _scalar(value, flow=False) + nl
        if not old.endswith("\n"):
            new = new[:-len(nl)]
    else:
        new = _scalar(value, flow=w.in_flow, hint=n.style)
        if start == end and start > 0 and text[start - 1] == ":":
            new = " " + new     # `key:` の空の値
    out = text[:start] + new + text[end:]

    def mutate(doc):
        _target(doc, path[:-1])[path[-1]] = value
    return _check(text, out, mutate)


def add_key(text: str, path, key: str, value) -> str:
    """path の map の末尾に key: value を足す。value は scalar か、dict / list (入れ子)。"""
    yaml = _yaml()
    path = tuple(path)
    n = _walk(_compose(text), path).node
    if not isinstance(n, yaml.MappingNode):
        raise YamlPatchError("map ではない所にキーは足せない")
    if not isinstance(key, str):
        raise YamlPatchError("キーは str")
    if any(isinstance(k, yaml.ScalarNode) and k.value == key for k, _ in n.value):
        raise YamlPatchError(f"キーが既にある: {key!r}")
    if n.flow_style:
        entry = f"{_scalar(key, flow=True)}: {_flow(value)}"
        if n.value:
            at = _content_end(n.value[-1][1])
            out = text[:at] + ", " + entry + text[at:]
        else:
            at = n.start_mark.index + 1
            out = text[:at] + entry + text[at:]
    else:
        if not n.value:
            raise YamlPatchError("空の block map には足せない")
        lines = _map_lines({key: value}, n.start_mark.column)
        out = _insert_lines(text, _after_line(text, _content_end(n)), lines)

    def mutate(doc):
        _target(doc, path)[key] = value
    return _check(text, out, mutate)


def remove_key(text: str, path) -> str:
    """path のキー (と値) を map から消す。直前のコメント行は残す。"""
    yaml = _yaml()
    path = tuple(path)
    if not path or not isinstance(path[-1], str):
        raise YamlPatchError("消すキーを str で指定する")
    root = _compose(text)
    m = _walk(root, path[:-1]).node
    if not isinstance(m, yaml.MappingNode):
        raise YamlPatchError("map ではない所からキーは消せない")
    idx = next((i for i, (k, _) in enumerate(m.value)
                if isinstance(k, yaml.ScalarNode) and k.value == path[-1]), None)
    if idx is None:
        raise YamlPatchError(f"キーが無い: {path[-1]!r}")
    if len(m.value) == 1:
        raise YamlPatchError("最後の 1 キーは消せない (空の map にはできない)")
    k, v = m.value[idx]
    if m.flow_style:
        if idx + 1 < len(m.value):
            a, b = k.start_mark.index, m.value[idx + 1][0].start_mark.index
        else:
            a, b = _content_end(m.value[idx - 1][1]), _content_end(v)
        out = text[:a] + text[b:]
    else:
        ls = _line_start(text, k.start_mark.index)
        if text[ls:k.start_mark.index].strip():
            raise YamlPatchError("`- key:` のように行の途中から始まるキーは消せない")
        out = text[:ls] + text[_after_line(text, _content_end(v)):]

    def mutate(doc):
        del _target(doc, path[:-1])[path[-1]]
    return _check(text, out, mutate)


def insert_item(text: str, path, value, index: int | None = None) -> str:
    """path の list に要素を足す。index=None なら末尾、そうでなければその位置に入れる。"""
    yaml = _yaml()
    path = tuple(path)
    n = _walk(_compose(text), path).node
    if not isinstance(n, yaml.SequenceNode):
        raise YamlPatchError("list ではない所に要素は足せない")
    size = len(n.value)
    if index is None:
        index = size
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= size:
        raise YamlPatchError(f"添字が範囲外: {index!r}")
    if n.flow_style:
        if size == 0:
            at, piece = n.start_mark.index + 1, _flow(value)
        elif index == size:
            at, piece = _content_end(n.value[-1]), ", " + _flow(value)
        else:
            at, piece = n.value[index].start_mark.index, _flow(value) + ", "
        out = text[:at] + piece + text[at:]
    else:
        if size == 0:
            raise YamlPatchError("空の block list には足せない")
        _require_dash_line(text, n.value[0])
        lines = _seq_lines([value], n.start_mark.column)
        if index == 0:
            pos = _line_start(text, n.value[0].start_mark.index)
        else:
            pos = _after_line(text, _content_end(n.value[index - 1]))
        out = _insert_lines(text, pos, lines)

    def mutate(doc):
        _target(doc, path).insert(index, value)
    return _check(text, out, mutate)


def remove_item(text: str, path) -> str:
    """path の list 要素を消す (path の最後は添字)。"""
    yaml = _yaml()
    path = tuple(path)
    if not path or isinstance(path[-1], bool) or not isinstance(path[-1], int):
        raise YamlPatchError("消す要素を int の添字で指定する")
    root = _compose(text)
    s = _walk(root, path[:-1]).node
    if not isinstance(s, yaml.SequenceNode):
        raise YamlPatchError("list ではない所から要素は消せない")
    size = len(s.value)
    if not -size <= path[-1] < size:
        raise YamlPatchError(f"添字が範囲外: {path[-1]}")
    idx = path[-1] % size
    if size == 1:
        raise YamlPatchError("最後の 1 要素は消せない (空の list にはできない)")
    item = s.value[idx]
    if s.flow_style:
        if idx + 1 < size:
            a, b = item.start_mark.index, s.value[idx + 1].start_mark.index
        else:
            a, b = _content_end(s.value[idx - 1]), _content_end(item)
        out = text[:a] + text[b:]
    else:
        _require_dash_line(text, item)
        ls = _line_start(text, item.start_mark.index)
        out = text[:ls] + text[_after_line(text, _content_end(item)):]

    def mutate(doc):
        del _target(doc, path[:-1])[path[-1]]
    return _check(text, out, mutate)


def _require_dash_line(text: str, item) -> None:
    ls = _line_start(text, item.start_mark.index)
    if not re.fullmatch(r"\s*-\s*", text[ls:item.start_mark.index]):
        raise YamlPatchError("`- ` が行頭に無い list 要素 (入れ子の `- - x` など) は扱えない")
