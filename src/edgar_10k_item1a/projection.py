"""Iterative structured visibility projection, not a browser or CSS engine.

Source locators are character offsets. No scripts or external resources are loaded.
Internal page furniture is retained; section-specific trailing cleanup is separate.
`<template>` content is inert. A head without its own `</head>` ends at the first
start tag that is not head content; an explicitly closed head keeps all its children.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import re
from typing import Iterator


BLOCKS = frozenset("p div section article header footer h1 h2 h3 h4 h5 h6 ul ol li table tr br hr pre blockquote".split())
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
HIDDEN = frozenset({"head", "script", "style", "noscript", "template", "ix:header", "ix:hidden",
                    "xbrli:context", "xbrli:unit"})
HEAD_CONTENT = frozenset("base basefont bgsound link meta noframes noscript script style template title".split())
GLYPHS = {"☐": False, "☒": True, "☑": True}
LEGACY_WINGDINGS = {"&#120;": True, "&#168;": False}
_IMPORTANT_SUFFIX = re.compile(r"\s*!\s*important\s*$", re.I)
_SUPPORTED_STYLE_VALUES = {
    "display": frozenset("none block inline inline-block flow-root list-item flex inline-flex "
                         "grid inline-grid table inline-table table-row table-cell table-row-group "
                         "table-header-group table-footer-group table-column table-column-group "
                         "table-caption contents".split()),
    "visibility": frozenset({"hidden", "collapse", "visible"}),
}
_GLUED_ITEM_1A = re.compile(r"^([ \t]*)(item)(1a)\b", re.I | re.M)
_HTML_TAGS = BLOCKS | VOID | HIDDEN | frozenset(
    "html body span b strong i em u s strike font a td th tbody thead tfoot caption "
    "center dl dt dd label form button small big sub sup ix:nonnumeric ix:nonfraction".split()
)
# EDGAR ASCII documents carry SGML table/page markup; it is not HTML evidence.
_EDGAR_ASCII_TAGS = frozenset("page table caption s c fn r".split())


@dataclass(frozen=True)
class Projection:
    text: str
    version: str = "structured-visible-v4"

    @property
    def sha256(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str | None]
    start: int
    end: int
    parent: Node | None = None
    children: list[Node] = field(default_factory=list)
    text: str = ""
    closed: bool = False

    def ancestors(self) -> Iterator[Node]:
        node: Node | None = self
        while node is not None:
            yield node
            node = node.parent

    def plain(self) -> str:
        parts = []
        pending = [self]
        while pending:
            node = pending.pop()
            if node.tag == "#text":
                parts.append(node.text)
            else:
                pending.extend(reversed(node.children))
        return "".join(parts)


class Document(HTMLParser):
    def __init__(self, raw: str) -> None:
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.lines = [0] + [match.end() for match in re.finditer("\n", raw)]
        self.unclosed_heads: frozenset[int] = frozenset()
        self._parse()
        # Only heads never matched by their own </head> end implicitly; reparse with them.
        unclosed = frozenset(node.start for node in self.nodes if node.tag == "head" and not node.closed)
        if unclosed:
            self.unclosed_heads = unclosed
            self.reset()
            self._parse()

    def _parse(self) -> None:
        self.root = Node("#root", {}, 0, len(self.raw))
        self.stack = [self.root]
        self.nodes: list[Node] = []
        self.feed(self.raw)
        self.close()
        self._close_stack_at(1, len(self.raw))

    def pos(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def add(self, tag: str, attrs: list[tuple[str, str | None]], width: int,
            text: str = "") -> Node:
        node = Node(tag, dict(attrs), self.pos(), self.pos() + width, self.stack[-1], text=text)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        return node

    def _close_stack_at(self, index: int, end: int) -> None:
        for node in self.stack[index:]:
            node.end = end
        del self.stack[index:]

    def _imply_table_closure(self, tag: str) -> None:
        targets = {"tr"} if tag == "tr" else {"td", "th"} if tag in {"td", "th"} else set()
        if targets:
            for index in range(len(self.stack) - 1, 0, -1):
                if self.stack[index].tag in {"table", "template"}:
                    return
                if self.stack[index].tag in targets:
                    self._close_stack_at(index, self.pos())
                    return

    def _imply_head_closure(self, tag: str) -> None:
        head = self.stack[-1]
        if head.tag == "head" and head.start in self.unclosed_heads and tag not in HEAD_CONTENT:
            self._close_stack_at(len(self.stack) - 1, self.pos())

    def _open_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> Node:
        self._imply_head_closure(tag)
        self._imply_table_closure(tag)
        node = self.add(tag, attrs, len(self.get_starttag_text()))
        if tag not in VOID:
            self.stack.append(node)
        else:
            node.closed = True
        return node

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = self._open_tag(tag, attrs)
        node.closed = True
        if self.stack[-1] is node:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                node = self.stack[index]
                self._close_stack_at(index, self.raw.find(">", self.pos()) + 1)
                node.closed = True
                return
            if self.stack[index].tag == "template":
                return  # template content cannot close elements opened outside it

    def handle_data(self, text: str) -> None:
        self.add("#text", [], len(text), text)

    def _reference(self, prefix: str, name: str) -> None:
        raw = prefix + name
        end = self.pos() + len(raw)
        if self.raw[end:end + 1] == ";":
            raw += ";"
        self.add("#text", [], len(raw), unescape(raw))

    def handle_entityref(self, name: str) -> None:
        self._reference("&", name)

    def handle_charref(self, name: str) -> None:
        self._reference("&#", name)

    def source(self, node: Node) -> dict:
        return {"char_span": [node.start, node.end], "raw": self.raw[node.start:node.end]}

    def visible(self, node: Node) -> bool:
        return not any(_hides(parent) for parent in node.ancestors())


def _style_declarations(style: str) -> Iterator[str]:
    """Split inline declarations outside strings, comments and nested value delimiters."""
    parts: list[str] = []
    quote = ""
    depth = 0
    index = 0
    while index < len(style):
        char = style[index]
        if quote:
            parts.append(char)
            if char == "\\" and index + 1 < len(style):
                parts.append(style[index + 1])
                index += 2
                continue
            if char == quote:
                quote = ""
        elif style.startswith("/*", index):
            end = style.find("*/", index + 2)
            parts.append(" ")
            if end < 0:
                break
            index = end + 2
            continue
        elif char in "\"'":
            quote = char
            parts.append(char)
        elif char == "\\" and index + 1 < len(style):
            parts.extend((char, style[index + 1]))
            index += 2
            continue
        elif char in "([{":
            depth += 1
            parts.append(char)
        elif char in ")]}":
            depth = max(0, depth - 1)
            parts.append(char)
        elif char == ";" and depth == 0:
            yield "".join(parts)
            parts.clear()
        else:
            parts.append(char)
        index += 1
    yield "".join(parts)


def _style_hides(style: str) -> bool:
    """Recognize explicit display/visibility keywords and their inline !important order.

    Unsupported values, CSS escapes, variables, selectors and stylesheet cascades
    are not resolved. This is deliberately not browser-computed visibility.
    """
    properties: dict[str, tuple[str, bool]] = {}
    for declaration in _style_declarations(style):
        name, separator, value = declaration.partition(":")
        name = name.strip().lower()
        if not separator or name not in _SUPPORTED_STYLE_VALUES:
            continue
        important = _IMPORTANT_SUFFIX.search(value)
        value = (value[:important.start()] if important else value).strip().lower()
        if value not in _SUPPORTED_STYLE_VALUES[name]:
            continue
        previous = properties.get(name)
        if previous is None or bool(important) >= previous[1]:
            properties[name] = value, bool(important)
    return (properties.get("display", ("", False))[0] == "none"
            or properties.get("visibility", ("", False))[0] in {"hidden", "collapse"})


def _hides(node: Node) -> bool:
    return (node.tag in HIDDEN or "hidden" in node.attrs
            or (node.attrs.get("aria-hidden") or "").strip().lower() == "true"
            or _style_hides(node.attrs.get("style") or ""))


def _is_wingdings_font(node: Node) -> bool:
    return node.tag == "font" and "wingdings" in (node.attrs.get("face") or "").lower()


def legacy_wingdings_state(document: Document, node: Node) -> bool | None:
    if node.tag != "#text" or not any(_is_wingdings_font(n) for n in node.ancestors()):
        return None
    return LEGACY_WINGDINGS.get(document.raw[node.start:node.end].lower())


@dataclass(eq=False, slots=True)
class _Chunk:
    text: str
    preformatted: bool = False
    previous: _Chunk | None = None
    next: _Chunk | None = None


class _TextBuffer:
    """Splice/trim chunks without repeatedly copying deeply nested subtree text."""

    def __init__(self) -> None:
        self.first: _Chunk | None = None
        self.last: _Chunk | None = None

    def append(self, text: str, preformatted: bool = False) -> None:
        if text:
            chunk = _Chunk(text, preformatted=preformatted, previous=self.last)
            if self.last is None:
                self.first = chunk
            else:
                self.last.next = chunk
            self.last = chunk

    def prepend(self, text: str) -> None:
        chunk = _Chunk(text, next=self.first)
        if self.first is None:
            self.last = chunk
        else:
            self.first.previous = chunk
        self.first = chunk

    def extend(self, other: _TextBuffer) -> None:
        if other.first is None:
            return
        if self.last is None:
            self.first = other.first
        else:
            self.last.next = other.first
            other.first.previous = self.last
        self.last = other.last
        other.first = other.last = None

    def strip(self, chars: str | None = None) -> None:
        while self.first is not None:
            if self.first.preformatted:
                break
            self.first.text = self.first.text.lstrip(chars)
            if self.first.text:
                break
            self.first = self.first.next
            if self.first is not None:
                self.first.previous = None
        if self.first is None:
            self.last = None
        while self.last is not None:
            if self.last.preformatted:
                break
            self.last.text = self.last.text.rstrip(chars)
            if self.last.text:
                break
            self.last = self.last.previous
            if self.last is not None:
                self.last.next = None
        if self.last is None:
            self.first = None

    def projected_text(self) -> str:
        """Clean normal block boundaries without erasing preformatted whitespace."""
        parts = []
        line = []
        preserve = False
        chunk = self.first
        while chunk is not None:
            pieces = chunk.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            for index, piece in enumerate(pieces):
                preserve = preserve or chunk.preformatted
                line.append(piece)
                if index < len(pieces) - 1:
                    rendered = "".join(line)
                    if preserve or rendered.strip(" \t"):
                        parts.append(rendered if preserve else rendered.strip(" \t"))
                    line = []
                    preserve = False
            chunk = chunk.next
        rendered = "".join(line)
        if preserve or rendered.strip(" \t"):
            parts.append(rendered if preserve else rendered.strip(" \t"))
        return "\n".join(parts)


@dataclass
class _Frame:
    node: Node
    in_wingdings: bool
    in_pre: bool = False
    child_index: int = 0
    body: _TextBuffer = field(default_factory=_TextBuffer)


def _mark(checked: bool) -> str:
    return "[CHECKED]" if checked else "[UNCHECKED]"


def _render(document: Document, start: Node) -> str:
    frames = [_Frame(start, any(_is_wingdings_font(n) for n in start.ancestors()),
                     any(n.tag == "pre" for n in start.ancestors()))]
    while frames:
        frame = frames[-1]
        if frame.child_index < len(frame.node.children):
            node = frame.node.children[frame.child_index]
            frame.child_index += 1
            if _hides(node):
                continue
            in_wingdings = frame.in_wingdings or _is_wingdings_font(node)
            in_pre = frame.in_pre or node.tag == "pre"
            if node.tag == "#text":
                legacy = (LEGACY_WINGDINGS.get(document.raw[node.start:node.end].lower())
                          if in_wingdings else None)
                text = node.text if in_pre else re.sub(r"\s+", " ", node.text)
                frame.body.append(_mark(legacy) if legacy is not None else "".join(
                    _mark(GLYPHS[c]) if c in GLYPHS else c for c in text), preformatted=in_pre)
            elif node.tag == "br" and in_pre:
                frame.body.append("\n", preformatted=True)
            elif node.tag == "input" and (node.attrs.get("type") or "").lower() == "checkbox":
                frame.body.append(_mark("checked" in node.attrs), preformatted=in_pre)
            else:
                frames.append(_Frame(node, in_wingdings, in_pre))
            continue
        frames.pop()
        if frame.node.tag in {"td", "th"}:
            frame.body.strip()
            frame.body.append("\t")
        elif frame.node.tag in BLOCKS:
            frame.body.strip(" \t\n")
            frame.body.prepend("\n")
            frame.body.append("\n")
        if frames:
            frames[-1].body.extend(frame.body)
        else:
            return frame.body.projected_text()
    return ""


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    return _GLUED_ITEM_1A.sub(r"\1\2 \3", text)


def project(text: str) -> Projection:
    """Project HTML with inherited visibility and marks; fail on multiple html roots."""
    document = Document(text)
    roots = [node for node in document.nodes if node.tag == "html"]
    if len(roots) > 1:
        raise ValueError("expected one primary HTML document")
    start = roots[0] if roots else document.root
    rendered = _render(document, start) if document.visible(start) else ""
    return Projection(_normalize(rendered))


class _HTMLDetector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.found = self.found or (tag in _HTML_TAGS and tag not in _EDGAR_ASCII_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_decl(self, decl: str) -> None:
        self.found = self.found or re.match(r"doctype\s+html\b", decl, re.I) is not None


def visible_text(text: str) -> Projection:
    """Choose HTML only on recognized HTML tags, not arbitrary angle-bracket labels."""
    detector = _HTMLDetector()
    detector.feed(text)
    detector.close()
    return project(text) if detector.found else Projection(_normalize(text))
