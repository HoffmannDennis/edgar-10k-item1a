"""Located starts and the common E1/E2/E3 end contract in finalized projected text."""

import re
from dataclasses import dataclass

from .constants import (
    ANY_CASE_ITEM_TITLE_RE, CAPS_CUT_MIN_OFFSET, CAPS_ITEM_TITLE_RE, CONTINUATION_AFTER_RE,
    DANGLING_CONNECTIVE_RE, E2_PATTERNS,
    FULL_ITEM_1A_RE, HEADING_SHAPED_BOUNDS, INLINE_CUE_RE, INLINE_PART_ITEM_TAIL_RE,
    ITEM_1A_LABEL_RE, PAGEBREAK_LINE_RE, RISK_CAPTION_RE, RISK_FACTORS_PATTERN,
    POST_START_CROSSREF_RE, RUNNING_HEADER_RE, START_CROSSREF_CUE_RE, UNCLASSIFIED_FINANCIAL_RE,
)

_E1_LINE_RE = re.compile(
    r"(?im)^[ \t\xa0]{0,30}(?P<label>i[ \t]?tem\s*"
    r"(?P<item>1\s*[bc]|1\s*\(\s*b\s*\)|1\.\s*b|2))"
    r"(?=$|[ \t.:—–/\-])(?P<tail>[^\n]*)$"
)
_E1_TITLES = {
    "1b": re.compile(r"unresolved(?:[ \t]+SEC)?[ \t]+staff[ \t]+comments\b", re.I),
    "1c": re.compile(r"cybersecurity\b", re.I),
    "2": re.compile(r"(?:description[ \t]+of[ \t]+)?propert(?:y|ies)\b", re.I),
}
_START_CAPTION_XREF_RE = re.compile(r"(?i)(?:under|in|see)\s+the\s+(?:caption|heading)\b[^.\n]{0,60}$")
_SENTENCE_END_RE = re.compile(r"[.!?:][)\"'”’]?$")
_LABEL_SEPARATOR_RE = re.compile(r"[ \t\xa0]*[.:\-—–/|]?[ \t\xa0]*")
_COMPLETE_RISK_CAPTION_RE = re.compile(
    r"[ \t\xa0]*[.:]?[ \t\xa0]*" + RISK_FACTORS_PATTERN + r"[ \t\xa0]*[.:]?[ \t\xa0]*", re.I,
)
_TOC_PAGE_SUFFIX_RE = re.compile(r"[ \t\xa0.]*\d{1,4}(?:[–\-]\d{1,4})?[ \t\xa0]*")
_INDEX_ITEM_RE = re.compile(r"(?i)i[ \t]?tems?\s+\d{1,2}[ \t]*[a-c]?(?=$|[\s.:])")
_RIGHT_REFERENCE_RE = re.compile(
    r"(?i)\A\s*(?:of\s+(?:this|our)\s+(?:report|annual\s+report|form|filing)\b|"
    r"sections?\s+(?:in|of)\s+this\s+(?:report|form|annual\s+report|filing)\b)"
)


@dataclass(frozen=True)
class EndAnchor:
    position: int
    family: str


def terminator_left_tail(plain: str, start: int) -> str:
    pos = start
    for _ in range(6):
        if pos <= 0:
            break
        newline = plain.rfind("\n", 0, pos)
        segment = plain[newline + 1:pos]
        if segment.strip() and not PAGEBREAK_LINE_RE.fullmatch(segment):
            return segment
        if newline < 0:
            break
        pos = newline
    return ""


def is_inline_crossref(plain: str, start: int) -> bool:
    tail = terminator_left_tail(plain, start).rstrip()
    return any(rx.search(tail) is not None for rx in (
        DANGLING_CONNECTIVE_RE, INLINE_CUE_RE, INLINE_PART_ITEM_TAIL_RE,
    ))


def _standalone_start(plain: str, start: int) -> bool:
    line_start = plain.rfind("\n", 0, start) + 1
    return not plain[line_start:start].strip() or _SENTENCE_END_RE.search(
        terminator_left_tail(plain, start).rstrip()
    ) is not None


def _right_lines(plain: str, end: int) -> list[tuple[int, str]]:
    """At most four lines / 256 characters of heading-right context, with offsets."""
    lines = []
    position = end
    for line in plain[end:end + 256].split("\n")[:4]:
        lines.append((position, line))
        position += len(line) + 1
    return lines


def _page_then_index(text: str) -> bool:
    page = _TOC_PAGE_SUFFIX_RE.match(text)
    return page is not None and _INDEX_ITEM_RE.match(text[page.end():].lstrip()) is not None


def _toc_label_near(plain: str, position: int) -> bool:
    left = plain[max(0, position - 512):position].split("\n")[-6:]
    return any(part.strip().casefold() == "table of contents" for part in left)


def heading_right_rejected(plain: str, position: int, end: int, *, crossref: bool = True) -> bool:
    """Shared bounded rejection for explicit right refs, TOC rows and continuation markers.

    A bare numeric line needs TOC-label or next-entry context; otherwise financial
    years / page furniture after a genuine heading must not become TOC evidence.
    """
    right = plain[end:end + 256]
    lines = _right_lines(plain, end)
    suffix = lines[0][1] if lines else ""
    numeric_suffix = _TOC_PAGE_SUFFIX_RE.fullmatch(suffix) is not None
    # The shaped financial E2 alternatives admit years on their title line.
    # A year alone is not a TOC page; other heading families keep their page gate.
    financial_title = plain[position:end].casefold().startswith("consolidated")
    if (crossref and _RIGHT_REFERENCE_RE.match(right)
            or CONTINUATION_AFTER_RE.match(right)
            or RUNNING_HEADER_RE.search(suffix)
            or _page_then_index(right)):
        return True
    if numeric_suffix and (not financial_title or ".." in suffix or _toc_label_near(plain, position)):
        return True
    if suffix.strip(" \t\xa0.:") and not (numeric_suffix and financial_title):
        return False
    following = next(((index, line) for index, (_, line) in enumerate(lines[1:], 1) if line.strip()), None)
    if following is None:
        return False
    index, line = following
    if RUNNING_HEADER_RE.fullmatch(line.lstrip(" \t\xa0.:-—–(")):
        return True
    if numeric_suffix:
        return False
    if _TOC_PAGE_SUFFIX_RE.fullmatch(line):
        next_entry = _INDEX_ITEM_RE.match("\n".join(text for _, text in lines[index + 1:]).lstrip())
        return ".." in line or _toc_label_near(plain, position) or next_entry is not None
    return _page_then_index(line)


def _genuine_heading(plain: str, position: int, end: int) -> bool:
    return (not is_inline_crossref(plain, position) and not is_running_header(plain, position, end)
            and not heading_right_rejected(plain, position, end))


def canonical_body_start(plain: str, offset: int = 0) -> int | None:
    for match in ITEM_1A_LABEL_RE.finditer(plain, offset):
        tail = terminator_left_tail(plain, match.start()).rstrip()
        if (not _standalone_start(plain, match.start())
                or is_inline_crossref(plain, match.start())
                or _START_CAPTION_XREF_RE.search(tail)
                or START_CROSSREF_CUE_RE.search(plain[max(0, match.start() - 100):match.start()])
                or CONTINUATION_AFTER_RE.match(plain[match.end():match.end() + 64])):
            continue
        separator = _LABEL_SEPARATOR_RE.match(plain, match.end())
        caption = RISK_CAPTION_RE.match(plain, separator.end())
        full = FULL_ITEM_1A_RE.match(plain, match.start())
        if full is not None:
            full_line_end = plain.find("\n", full.end())
            full_line_end = full_line_end if full_line_end >= 0 else len(plain)
            caption_line_start = plain.rfind("\n", 0, full.start("caption")) + 1
            attachment = full.group("separator").split("\n")
            # A separately printed caption is removed only when it is exact;
            # e.g. "Risk Factors affecting investment" remains body text.
            if (len(attachment) == 1
                    or (_LABEL_SEPARATOR_RE.fullmatch(attachment[0])
                        and not any(part.strip() for part in attachment[1:-1])
                        and _COMPLETE_RISK_CAPTION_RE.fullmatch(plain[caption_line_start:full_line_end]))):
                caption = full
        line_end = plain.find("\n", match.end())
        if line_end < 0:
            line_end = len(plain)
        if caption is not None:
            if (POST_START_CROSSREF_RE.match(plain[caption.end():caption.end() + 200])
                    or heading_right_rejected(plain, match.start(), caption.end())):
                continue
            caption_line_end = plain.find("\n", caption.end())
            if caption_line_end < 0:
                caption_line_end = len(plain)
            if _TOC_PAGE_SUFFIX_RE.fullmatch(plain[caption.end():caption_line_end]):
                continue
            if RUNNING_HEADER_RE.search(plain[caption.end():caption_line_end]):
                continue
            end = caption.end()
            while end < len(plain) and plain[end] in " \t\xa0":
                end += 1
            if end < len(plain) and plain[end] in ".:":
                end += 1
        elif re.fullmatch(r"[ \t\xa0]*[.:]?[ \t\xa0]*", plain[match.end():line_end]):
            end = min(line_end + 1, len(plain))
            next_line_end = plain.find("\n", end)
            if next_line_end < 0:
                next_line_end = len(plain)
            next_line = plain[end:next_line_end]
            caption_view = next_line.lstrip(" \t\xa0.:")
            next_caption = RISK_CAPTION_RE.match(caption_view)
            if next_caption is not None and (
                RUNNING_HEADER_RE.search(next_line)
                or _TOC_PAGE_SUFFIX_RE.fullmatch(caption_view[next_caption.end():])
            ):
                continue
            if _COMPLETE_RISK_CAPTION_RE.fullmatch(next_line):
                end = next_line_end
        else:
            continue
        line = plain[match.start():line_end]
        if RUNNING_HEADER_RE.search(line):
            continue
        # The caption's right reference was already checked before consuming its
        # delimiter. Text after that delimiter is body prose, not another heading.
        if heading_right_rejected(plain, match.start(), end, crossref=False):
            continue
        while end < len(plain) and plain[end].isspace():
            end += 1
        return end
    return None


def _e1_end(plain: str, start: int) -> EndAnchor | None:
    for match in _E1_LINE_RE.finditer(plain, start):
        position = match.start("label")
        tail = match.group("tail")
        # A bare label may carry the item's whole answer, e.g. "Item 1B. None."
        standalone = re.fullmatch(r"(?i)[ \t\xa0]*[.:]?[ \t\xa0]*(?:(?:none|not[ \t]+applicable)[ \t\xa0]*\.?[ \t\xa0]*)?",
                                  tail) is not None
        item = re.sub(r"[\s().]", "", match.group("item")).lower()
        separator = _LABEL_SEPARATOR_RE.match(tail)
        title = _E1_TITLES[item].match(tail, separator.end())
        end = match.start("tail") + title.end() if title else match.end()
        if title is None and standalone:
            # Inspect a separate proper caption before accepting a bare label.
            for line_start, line in _right_lines(plain, match.end())[1:]:
                if not line.strip():
                    continue
                title = _E1_TITLES[item].match(line, _LABEL_SEPARATOR_RE.match(line).end())
                if title:
                    end = line_start + title.end()
                break
        if title is None and not standalone:
            continue
        if not _genuine_heading(plain, position, end):
            continue
        return EndAnchor(position, "E1")
    return None


def _header_position(plain: str, match: re.Match) -> int:
    position = match.start()
    if plain[position:position + 1] == ".":
        position += 1  # A same-line Controls pair follows, rather than consumes, sentence punctuation.
    while position < match.end() and plain[position] in " \t\r\n\xa0":
        position += 1
    return position


def is_running_header(plain: str, position: int, match_end: int | None = None) -> bool:
    end = plain.find("\n", match_end if match_end is not None else position)
    return RUNNING_HEADER_RE.search(plain[position:end if end >= 0 else len(plain)]) is not None


_LOWERCASE_ANCHOR_RE = re.compile(r"(?i)d\d|[a-z]+-20\d{6}|ex-101")


def _wrapped_continuation(plain: str, position: int) -> bool:
    """A lowercase line start after an unterminated line continues prose (filenames excepted)."""
    if not plain[position:position + 1].islower() or _LOWERCASE_ANCHOR_RE.match(plain, position):
        return False
    previous = terminator_left_tail(plain, position).rstrip()
    return bool(previous) and previous[-1] not in ".!?:;)\"'”’"


_HEADING_FUNCTION_WORDS = frozenset(
    "a an and as at by for from in into of on or over the to under with".split()
)


def _heading_shaped(plain: str, position: int) -> bool:
    """The line up to a run-in ". "/": " has no lowercase content word (single letters aside)."""
    line_end = plain.find("\n", position)
    line = plain[position:line_end if line_end >= 0 else len(plain)]
    head = re.split(r"[.:](?:\s|$)", line, maxsplit=1)[0]
    words = re.findall(r"[A-Za-z][A-Za-z'’]+", head)
    return len(head) <= 200 and not any(
        word[0].islower() and word.lower() not in _HEADING_FUNCTION_WORDS for word in words
    )


def first_structural_pos(rx: re.Pattern, plain: str, start: int) -> int | None:
    for match in rx.finditer(plain, max(0, start - 1)):
        position = _header_position(plain, match)
        if (position < start or _wrapped_continuation(plain, position)
                or (rx in HEADING_SHAPED_BOUNDS and not _heading_shaped(plain, position))
                or not _genuine_heading(plain, position, match.end())):
            continue
        return position
    return None


def non_item_boundary_pos(plain: str, start: int) -> int | None:
    positions = [position for rx in E2_PATTERNS
                 if (position := first_structural_pos(rx, plain, start)) is not None]
    return min(positions) if positions else None


def _title_key(title: str) -> str:
    return " ".join(title.replace("’", "'").split()).casefold()


def item_title_cut(plain: str, start: int, provisional_end: int) -> int | None:
    """Bare CAPS titles remain risk subheads when their title repeats beyond the provisional end.

    A repeat counts as a CAPS title or as a numbered item heading in any case.
    """
    later = {_title_key(match.group("title"))
             for rx in (CAPS_ITEM_TITLE_RE, ANY_CASE_ITEM_TITLE_RE)
             for match in rx.finditer(plain, provisional_end)
             if (rx is CAPS_ITEM_TITLE_RE or match.group("item"))
             and _genuine_heading(plain, _header_position(plain, match), match.end())}
    for match in CAPS_ITEM_TITLE_RE.finditer(plain, start + CAPS_CUT_MIN_OFFSET, provisional_end):
        position = _header_position(plain, match)
        if not _genuine_heading(plain, position, match.end()):
            continue
        if match.group("item") or _title_key(match.group("title")) not in later:
            return position
    return None


def section_end(plain: str, start: int) -> EndAnchor | None:
    candidates = []
    e1 = _e1_end(plain, start)
    if e1 is not None:
        candidates.append(e1)
    e2 = non_item_boundary_pos(plain, start)
    if e2 is not None:
        candidates.append(EndAnchor(e2, "E2"))
    # A later numbered CAPS title supplies the provisional end even without E1/E2.
    # Thus a bare risk subhead with that same title is protected by the duplicate guard.
    for match in CAPS_ITEM_TITLE_RE.finditer(plain, start + CAPS_CUT_MIN_OFFSET):
        position = _header_position(plain, match)
        if match.group("item") and _genuine_heading(plain, position, match.end()):
            candidates.append(EndAnchor(position, "E3"))
            break
    provisional_end = min((anchor.position for anchor in candidates), default=len(plain))
    e3 = item_title_cut(plain, start, provisional_end)
    if e3 is not None:
        candidates.append(EndAnchor(e3, "E3"))
    return min(candidates, key=lambda anchor: (anchor.position, anchor.family)) if candidates else None


def unclassified_financial_heading(plain: str, start: int, end: int) -> bool:
    for match in UNCLASSIFIED_FINANCIAL_RE.finditer(plain, start, end):
        position = match.start()
        if _genuine_heading(plain, position, match.end()):
            return True
    return False


_LATER_ITEM_HEADING_RE = re.compile(
    r"(?im)^[ \t\xa0]{0,30}(?:part[ \t\xa0]+i{1,3}v?[ \t\xa0]*[.,:\-—–]*[ \t\xa0]*)?"
    r"(?P<label>i[ \t]?tems?[ \t\xa0]*(?:1[ \t]*(?:[bc]|\([ \t]*[bc][ \t]*\)|\.[ \t]*[bc])|1[0-6]|[2-9])[a-c]?)"
    r"(?=$|[ \t\xa0.:,—–/\-])[^\n]{0,200}$"
)
_IN_LINE_REFERENCE_RE = re.compile(r"(?i)\b(?:of|in)\s+(?:this|our)\s+(?:report|annual\s+report|form|filing)\b")
_TRAILING_PAGE_RE = re.compile(r"[ \t\xa0.]*\d{1,4}(?:[–\-]\d{1,4})?[ \t\xa0]*$")


def later_item_heading(plain: str, start: int, end: int) -> bool:
    """A genuine later item heading before the accepted end leaves that end unsupported.

    Like E1, a standalone label takes its title from the next nonblank line, and a
    trailing page number stays right context for the shared TOC checks. A label
    directly attached to the accepted heading (``Item 9A.`` before its title) is that heading.
    """
    for match in _LATER_ITEM_HEADING_RE.finditer(plain, start, end):
        if re.fullmatch(r"[\s.:\-—–/|]*", plain[match.end("label"):end]):
            continue
        title_start, title = match.end("label"), match.group()[match.end("label") - match.start():]
        if not title.strip(" \t\xa0.:-—–/"):
            title_start, title = next(((offset, line) for offset, line in _right_lines(plain, match.end())[1:]
                                       if line.strip()), (match.end(), ""))
        if _IN_LINE_REFERENCE_RE.search(title):
            continue
        page = _TRAILING_PAGE_RE.search(title)
        title_end = title_start + (page.start() if page and page.start() else len(title))
        if _genuine_heading(plain, match.start("label"), title_end):
            return True
    return False


def normalize_body(body: str) -> str:
    """item1a-body-v1: preserve interior text; peel only trailing blank/furniture lines."""
    lines = body.split("\n")
    while lines and (not lines[-1].strip() or PAGEBREAK_LINE_RE.fullmatch(lines[-1])):
        lines.pop()
    return "\n".join(lines).rstrip()
