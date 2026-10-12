"""Form normalization and strict, byte-preserving document decoding."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import re


ANNUAL_TEXT_FORMS = frozenset({"10-K", "10-K405", "10-KT", "10-KT405"})
_FORM_ALIASES = {
    "10K": "10-K", "10K405": "10-K405", "10KT": "10-KT", "10KT405": "10-KT405",
    "10KSB": "10-KSB", "10-KSB40": "10-KSB405", "10KSB40": "10-KSB405",
    "10KSB405": "10-KSB405", "10Q": "10-Q", "10QSB": "10-QSB", "10QT": "10-QT",
}


def normalize_form(form: str) -> tuple[str, bool]:
    """Return a canonical base form and amendment flag, not a support decision."""
    if not isinstance(form, str):
        raise TypeError("form must be str")
    value = form.strip().upper()
    if not value:
        raise ValueError("form must not be empty")
    amended = value.endswith(("/A", "-A"))
    base = value[:-2] if amended else value
    if not base:
        raise ValueError("form must contain a base form")
    return _FORM_ALIASES.get(base, base), amended


@dataclass(frozen=True)
class DecodedDocument:
    text: str
    codec: str
    strategy: str
    flags: tuple[str, ...]
    source_sha256: str


_CHARSET_PARAMETER = re.compile(
    r"(?:^|[;\s])charset\s*=\s*(\"[^\"]*\"|'[^']*'|[^;\s]*)", re.I
)
_XML_ENCODING = re.compile(r"\bencoding\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s?]*)", re.I)
_SUPPORTED_CODECS = {"utf-8": "utf-8", "cp1252": "cp1252", "iso8859-1": "iso-8859-1", "ascii": "ascii"}
_C1_CONTROLS = re.compile("[\x80-\x9f]")


class _CharsetDeclarations(HTMLParser):
    """Inspect head meta declarations, but XML encoding only at the leading prolog.

    The head scope ends permanently at the first ``</head>`` or ``<body>``; a
    head-less document keeps every meta declaration in scope.
    """

    def __init__(self, prolog_position: tuple[int, int]) -> None:
        super().__init__(convert_charrefs=False)
        self.labels: list[str] = []
        self.prolog_position = prolog_position
        self.in_head_scope = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self.in_head_scope = False
        if tag != "meta" or not self.in_head_scope:
            return
        self.labels.extend(value or "" for name, value in attrs if name == "charset")
        if any(name == "http-equiv" and (value or "").strip().lower() == "content-type"
               for name, value in attrs):
            for name, value in attrs:
                if name == "content":
                    self.labels.extend(match.group(1).strip("\"'")
                                       for match in _CHARSET_PARAMETER.finditer(value or ""))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.in_head_scope = False

    def handle_pi(self, data: str) -> None:
        if self.getpos() == self.prolog_position and re.match(r"xml(?:\s|$)", data, re.I):
            self.labels.extend(match.group(1).strip("\"'")
                               for match in _XML_ENCODING.finditer(data))


def _declared_codecs(content: bytes) -> set[str]:
    # This is a byte-preserving ASCII inspection view, not a document decoding route.
    payload = content[len(codecs.BOM_UTF8):] if content.startswith(codecs.BOM_UTF8) else content
    inspection = payload.decode("ascii", errors="surrogateescape")
    prefix = inspection[:len(inspection) - len(inspection.lstrip(" \t\r\n"))]
    parser = _CharsetDeclarations((prefix.count("\n") + 1, len(prefix) - prefix.rfind("\n") - 1))
    parser.feed(inspection)
    parser.close()
    found = set()
    for label in parser.labels:
        try:
            canonical = codecs.lookup(label.strip()).name
        except (LookupError, ValueError) as exc:
            raise ValueError("decoding_unresolved: unsupported charset declaration") from exc
        if canonical not in _SUPPORTED_CODECS:
            raise ValueError("decoding_unresolved: unsupported charset declaration")
        found.add(_SUPPORTED_CODECS[canonical])
    return found


def _lossless_decode(content: bytes, codec: str, bom: bytes = b"") -> str:
    payload = content[len(bom):] if bom else content
    text = payload.decode(codec, errors="strict")
    if bom + text.encode(codec, errors="strict") != content:
        raise ValueError("decoding_unresolved: byte roundtrip failed")
    return text


def decode_document(content: bytes) -> DecodedDocument:
    """Decode without replacement; declared failures never fall back to another codec.

    UTF-8 BOMs are removed from text but remain in the source hash and roundtrip.
    Strategies are ``bom``, ``declared_charset``, ``utf8_strict`` and
    ``cp1252_fallback``. Only the undeclared cp1252 fallback adds decoding_inferred.
    An ASCII declaration beside one other supported declaration is consistent only
    for pure-ASCII bytes, so the document is then decoded strictly as ASCII. Any
    decoded C1 control character (U+0080-U+009F) adds decoding_c1_controls.
    """
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    if content.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE,
                           codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        raise ValueError("decoding_unresolved: unsupported BOM")
    bom = codecs.BOM_UTF8 if content.startswith(codecs.BOM_UTF8) else b""
    declared = _declared_codecs(content)
    if len(declared - {"ascii"}) > 1 or (bom and declared and declared != {"utf-8"}):
        raise ValueError("decoding_unresolved: contradictory charset declarations")
    flags: tuple[str, ...] = ()
    if bom or declared:
        codec = "utf-8" if bom else "ascii" if "ascii" in declared else next(iter(declared))
        strategy = "bom" if bom else "declared_charset"
        try:
            text = _lossless_decode(content, codec, bom)
        except UnicodeError as exc:
            raise ValueError("decoding_unresolved: declared encoding failed") from exc
    else:
        codec, strategy = "utf-8", "utf8_strict"
        try:
            text = _lossless_decode(content, codec)
        except UnicodeError:
            codec, strategy = "cp1252", "cp1252_fallback"
            try:
                text = _lossless_decode(content, codec)
            except UnicodeError as exc:
                raise ValueError("decoding_unresolved: no lossless supported encoding") from exc
            flags = ("decoding_inferred",)
    if _C1_CONTROLS.search(text):
        flags = tuple(sorted(flags + ("decoding_c1_controls",)))
    return DecodedDocument(text=text, codec=codec, strategy=strategy, flags=flags,
                           source_sha256=sha256(content).hexdigest())
