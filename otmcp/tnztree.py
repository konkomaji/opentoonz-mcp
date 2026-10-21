"""Lossless round-trip parser for OpenToonz TOStream files (.tnz).

The format looks like XML but is not: no header, bare/quoted text tokens,
'<tag id='N'/>' persist references. We keep text tokens raw (quotes included)
so anything we do not touch is written back byte-for-byte equivalent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Union

Child = Union["Node", str]


@dataclass
class Node:
    name: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Child] = field(default_factory=list)
    selfclose: bool = False

    # ---- navigation
    def nodes(self, name: str | None = None) -> list["Node"]:
        return [c for c in self.children if isinstance(c, Node) and (name is None or c.name == name)]

    def find(self, name: str) -> "Node | None":
        for c in self.children:
            if isinstance(c, Node) and c.name == name:
                return c
        return None

    def path(self, *names: str) -> "Node | None":
        n: Node | None = self
        for nm in names:
            n = n.find(nm) if n else None
        return n

    def walk(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()

    def tokens(self) -> list[str]:
        return [c for c in self.children if isinstance(c, str)]

    def text(self) -> str:
        return " ".join(unquote(t) for t in self.tokens())

    def set_tokens(self, *values) -> "Node":
        self.children = [c for c in self.children if isinstance(c, Node)] + [fmt(v) for v in values]
        self.selfclose = False
        return self

    def child(self, name: str, create: bool = True, before: str | None = None) -> "Node":
        n = self.find(name)
        if n is None and create:
            n = Node(name)
            idx = len(self.children)
            if before:
                for i, c in enumerate(self.children):
                    if isinstance(c, Node) and c.name == before:
                        idx = i
                        break
            self.children.insert(idx, n)
        return n  # type: ignore[return-value]

    def remove(self, node: "Node") -> None:
        self.children = [c for c in self.children if c is not node]


def fmt(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if abs(v) < 1e-10:
            return "0"
        s = f"{v:.6g}"
        return s
    s = str(v)
    if s and re.fullmatch(r"[A-Za-z0-9_%]+", s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unquote(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        out, i, s = [], 1, tok
        while i < len(s) - 1:
            if s[i] == "\\" and i + 1 < len(s) - 1:
                out.append(s[i + 1])
                i += 2
            else:
                out.append(s[i])
                i += 1
        return "".join(out)
    return tok


_ATTR = re.compile(r"""\s*([A-Za-z_][\w.\-]*)\s*=\s*("([^"]*)"|'([^']*)')""")


def parse(text: str) -> Node:
    root = Node("#root")
    stack = [root]
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if text.startswith("<!--", i):
            j = text.find("-->", i)
            i = n if j < 0 else j + 3
            continue
        if text.startswith("<?", i):
            j = text.find("?>", i)
            i = n if j < 0 else j + 2
            continue
        if text.startswith("</", i):
            j = text.index(">", i)
            name = text[i + 2:j].strip()
            if len(stack) > 1 and stack[-1].name == name:
                stack.pop()
            else:
                raise ValueError(f"mismatched end tag </{name}> at {i}")
            i = j + 1
            continue
        if c == "<":
            m = re.compile(r"<([A-Za-z_][\w.\-]*)").match(text, i)
            if not m:
                raise ValueError(f"bad tag at {i}")
            node = Node(m.group(1))
            j = m.end()
            while True:
                am = _ATTR.match(text, j)
                if not am:
                    break
                node.attrs[am.group(1)] = am.group(3) if am.group(3) is not None else am.group(4)
                j = am.end()
            while text[j].isspace():
                j += 1
            stack[-1].children.append(node)
            if text.startswith("/>", j):
                node.selfclose = True
                i = j + 2
            elif text[j] == ">":
                stack.append(node)
                i = j + 1
            else:
                raise ValueError(f"bad tag end at {j}")
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            stack[-1].children.append(text[i:j + 1])
            i = j + 1
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] != "<":
            j += 1
        stack[-1].children.append(text[i:j])
        i = j
    if len(stack) != 1:
        raise ValueError(f"unclosed tag <{stack[-1].name}>")
    return root


def dump(root: Node) -> str:
    out: list[str] = []

    def w(node: Node, depth: int) -> None:
        pad = "  " * depth
        attrs = "".join(f" {k}=\"{v}\"" if "'" in v or k != "id" else f" {k}='{v}'" for k, v in node.attrs.items())
        if node.selfclose and not node.children:
            out.append(f"{pad}<{node.name}{attrs}/>")
            return
        out.append(f"{pad}<{node.name}{attrs}>")
        line: list[str] = []
        for ch in node.children:
            if isinstance(ch, str):
                line.append(ch)
            else:
                if line:
                    out.append(pad + "  " + " ".join(line) + " ")
                    line = []
                if ch.selfclose and not ch.children and out and _inline_ref(node):
                    # keep persist references inline with their tokens (e.g. inside <cell>)
                    line.append(f"<{ch.name}" + "".join(f" {k}='{v}'" for k, v in ch.attrs.items()) + "/>")
                else:
                    w(ch, depth + 1)
        if line:
            out.append(pad + "  " + " ".join(line) + " ")
        out.append(f"{pad}</{node.name}>")

    for ch in root.children:
        if isinstance(ch, Node):
            w(ch, 0)
    return "\n".join(out) + "\n"


def _inline_ref(parent: Node) -> bool:
    return parent.name == "cell"


def load(path: str) -> Node:
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return parse(f.read())


def save(root: Node, path: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
        f.write(dump(root))


def max_id(root: Node) -> int:
    m = 0
    for n in root.walk():
        v = n.attrs.get("id", "")
        if v.isdigit():
            m = max(m, int(v))
    return m
