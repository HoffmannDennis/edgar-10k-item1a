"""Located recovery opportunities with distinct source qualification predicates.

Only genuine starts are supported. Ambiguous named locations are not guessed;
EOF is used solely as a bounded diagnostic probe, never a formal end.
"""

import re
from dataclasses import dataclass

from ..models import Item1AExtraction, TextSpan, TextStatus
from . import constants as c
from .boundaries import (
    _header_position, heading_right_rejected, is_inline_crossref, is_running_header,
    later_item_heading, normalize_body, section_end, unclassified_financial_heading,
)
from .quality import quality_flag

_FILER_SRC_IDENTITY_RE = re.compile(
    r"(?i)\A(?:as\s+a\s+smaller\s+reporting\s+company[,\s]+"
    r"(?:we|(?:the|our)\s+(?:company|registrant))\b|"
    r"(?=(?:we|(?:the|our)\s+(?:company|registrant))\b)[^.!?]{0,250}?\b"
    r"(?:we\s+are|(?:the|our)\s+(?:company|registrant)\s+is)\s+"
    r"(?:a\s+)?smaller\s+reporting\s+company\b)"
)


@dataclass(frozen=True)
class Candidate:
    start: int
    tier: str
    predicate: str = "primary"


@dataclass(frozen=True)
class Section:
    candidate: Candidate
    end: int
    family: str | None
    body: str
    error: str | None = None
    flags: tuple[str, ...] = ()


def after_heading(plain: str, end: int) -> int:
    """Consume at most one period/colon and whitespace, never body punctuation."""
    delimiter_allowed = end > 0 and plain[end - 1] != "\n"
    while end < len(plain) and plain[end] in " \t\xa0":
        end += 1
    if delimiter_allowed and end < len(plain) and plain[end] in ".:":
        end += 1
    while end < len(plain) and plain[end].isspace():
        end += 1
    return end


def window_metrics(body: str) -> tuple[int, int, int, int, int]:
    return (
        len(body.split()), sum(term in body[:3000].lower() for term in c.RISK_VOCAB),
        len(re.findall(r"\b\d{1,4}\b", body[:1500])),
        len(c.RECOVERY_ENUM_RE.findall(body[:4000])), len(c.RECOVERY_ENUM_RE.findall(body)),
    )


def qualifies(body: str, predicate: str) -> bool:
    if predicate == "primary":
        return bool(body)
    if predicate in {"bare", "exhibit"}:
        return (len(body) >= c.EXHIBIT_MIN_CHARS and stub_reason(body) is None
                and quality_flag(body) not in c.STUB_QUALITY_FLAGS | {"REVIEW_VOCABULARY"})
    words, vocab, bare, head, full = window_metrics(body)
    if words < c.RECOVERY_MIN_WORDS:
        return False
    if predicate == "legacy-named":
        return vocab >= c.RECOVERY_MIN_VOCAB and full >= c.RECOVERY_SUSTAINED_ENUM
    if predicate == "cautionary":
        # Typed PSLRA has its own source gate: three enumeration phrases, not twenty.
        return vocab >= c.RECOVERY_MIN_VOCAB and full >= 3
    if bare > c.RECOVERY_MAX_BARE:
        return False
    if predicate == "sec-named":
        return full >= c.RECOVERY_SUSTAINED_ENUM
    if vocab < c.RECOVERY_MIN_VOCAB or c.RECOVERY_PSLRA_EXHIBIT_RE.search(body[:200]):
        return False
    if predicate == "legacy-risk":
        return (head >= c.RECOVERY_MIN_ENUM_HEAD and (head >= 3 or words >= c.RECOVERY_LONG_WORDS)
                or full >= c.RECOVERY_SUSTAINED_ENUM)
    if predicate == "sec-alternative":
        return (head >= c.RECOVERY_MIN_ENUM_HEAD and words >= c.RECOVERY_LONG_WORDS
                or full >= c.RECOVERY_SUSTAINED_ENUM)
    raise ValueError(f"unknown qualification predicate: {predicate}")


def evaluate(plain: str, candidate: Candidate) -> Section:
    anchor = section_end(plain, candidate.start)
    end = anchor.position if anchor else min(len(plain), candidate.start + c.MAX_SECTION_CHARS)
    error = None
    extra_flags = ()
    if anchor is None:
        error = "no_supported_end_anchor"
    elif end - candidate.start > c.MAX_SECTION_CHARS:
        error = "end_anchor_out_of_scope"
    if unclassified_financial_heading(plain, candidate.start, end):
        if error:
            extra_flags = ("unclassified_financial_heading",)
        else:
            error = "unclassified_financial_heading"
    if error is None and later_item_heading(plain, candidate.start, end):
        error = "unrecognized_item_heading_before_end"
    body = normalize_body(plain[candidate.start:min(end, candidate.start + c.MAX_SECTION_CHARS)])
    if not body and error is None:
        error = "empty_risk_body"
    return Section(candidate, end, anchor.family if anchor else None, body, error, extra_flags)


def result_for(section: Section, projection, metadata: dict, flags: tuple[str, ...],
               status: TextStatus) -> Item1AExtraction:
    fields = dict(metadata, extraction_tier=section.candidate.tier)
    if section.family is not None:
        fields.update(end_anchor_family=section.family, end_anchor_position=section.end)
    if section.error:
        unresolved = TextStatus.start_unresolved if section.error in {
            "empty_risk_body", "combined_risk_heading_missing", "ambiguous_combined_risk_heading",
        } else TextStatus.boundary_unresolved
        return Item1AExtraction(text_status=unresolved,
                               flags=tuple(sorted(set(flags) | set(section.flags) | {section.error})), **fields)
    span = TextSpan(start=section.candidate.start, end=section.end,
                    projection_sha256=projection.sha256, projection_version=projection.version,
                    normalization_version=c.BODY_NORMALIZATION_VERSION)
    return Item1AExtraction(text_status=status, text=section.body, quality_flag=quality_flag(section.body),
                           spans=(span,), flags=flags, **fields)


def first_sentence(body: str, limit: int = 600) -> str:
    return c.first_sentence(body, limit)


def is_pointer_lead(body: str) -> bool:
    sentence = first_sentence(c.pointer_probe(body))
    return bool(c.POINTER_VERB_RE.search(sentence)) and bool(c.EXTERNAL_TARGET_RE.search(sentence))


def absent_statement(body: str) -> bool:
    # Observe the filer's own answer, not an SRC rule or a customer's disclosure risk.
    sentence = first_sentence(body, limit=1500).strip()
    return (len(body) <= 1500 and sentence == body.strip()
            and _FILER_SRC_IDENTITY_RE.match(sentence) is not None
            and c.ABSENT_STATEMENT_RE.search(sentence) is not None
            and c.RECOVERY_ENUM_RE.search(sentence) is None)


def stub_reason(body: str) -> str | None:
    # A filing-scope FLS disclaimer is not an incorporation pointer. Like the other
    # pointer stubs, a lead pointer is bounded: a long body is the section itself.
    if len(body) <= 1000 and is_pointer_lead(body):
        return "ibr_lead_pointer"
    probe = c.pointer_probe(body)
    if c.is_ibr_stub(body) or (len(body) < 1000 and c.EX99_POINTER_RE.search(probe)):
        return "ibr_stub"
    if len(body) <= 1000 and c.IN_DOC_POINTER_VERB_RE.search(probe) and c.IN_DOC_TARGET_RE.search(probe):
        return "in_document_pointer"
    flag = quality_flag(body)
    return f"quality_{flag.lower()}" if flag in c.STUB_QUALITY_FLAGS else None


def cross_reference_index_evidence(plain: str) -> str | None:
    """Observe only a labelled cross-reference index; a bare TOC is not an IBR assertion."""
    context = re.search(r"(?i)form[ -]+10[ -]?k\s+cross[ -]+reference\s+index", plain)
    if context is None:
        return None
    match = c.ITEM_1A_XREF_INDEX_RE.search(plain, context.end())
    return match.group() if match and not is_inline_crossref(plain, match.start()) else None


def combined_candidate(plain: str) -> tuple[Candidate | None, str | None]:
    for match in c.COMBINED_HEADER_RE.finditer(plain):
        position = _header_position(plain, match)
        if is_inline_crossref(plain, position) or is_running_header(plain, position):
            continue
        line_end = plain.find("\n", match.end())
        line_end = line_end if line_end >= 0 else len(plain)
        if re.search(r"[ \t.]+\d{1,4}[ \t]*$", plain[match.end():line_end]):
            continue
        if match.group("cat"):
            return Candidate(after_heading(plain, match.end()), "sec-later-combined-heading"), None
        scope_start = min(line_end + 1, len(plain))
        anchor = section_end(plain, scope_start)
        scope_end = anchor.position if anchor else min(len(plain), scope_start + c.MAX_SECTION_CHARS)
        hits = [hit for hit in c.COMBINED_RISK_SUBHEAD_RE.finditer(plain, max(0, scope_start - 1), scope_end)
                 if not is_inline_crossref(plain, _header_position(plain, hit))
                 and not heading_right_rejected(plain, _header_position(plain, hit), hit.end(), crossref=False)]
        if len(hits) != 1:
            return None, "combined_risk_heading_missing" if not hits else "ambiguous_combined_risk_heading"
        return Candidate(after_heading(plain, hits[0].end()), "primary-combined"), None
    return None, None


def bare_candidates(plain: str, tier: str = "primary-bare-heading") -> list[Candidate]:
    return [Candidate(after_heading(plain, match.end()), tier, "bare")
            for match in c.BARE_RISK_HEADING_RE.finditer(plain)
            if not is_inline_crossref(plain, _header_position(plain, match))
            and not is_running_header(plain, _header_position(plain, match))
            and not heading_right_rejected(plain, _header_position(plain, match), match.end(), crossref=False)]


def risk_candidates(plain: str) -> list[Candidate]:
    candidates = []
    for match in c.RECOVERY_HEADING_RE.finditer(plain):
        position = _header_position(plain, match)
        if (is_inline_crossref(plain, position) or is_running_header(plain, position)
                or c.CONTINUATION_AFTER_RE.match(plain[match.end():match.end() + 64])):
            continue
        candidates.append(Candidate(after_heading(plain, match.end()), "P1-secondheading", "legacy-risk"))
    for match in c.RECOVERY_LEAD_RE.finditer(plain):
        position = match.start()
        line_start = plain.rfind("\n", 0, position) + 1
        if plain[line_start:position].strip() or is_inline_crossref(plain, position) or is_running_header(plain, position):
            continue
        # Remove an exact section-heading lead. Prose leads remain part of the body.
        line_end = plain.find("\n", match.end())
        tail = plain[match.end():line_end if line_end >= 0 else len(plain)]
        heading = re.match(r"(?i)forward[-\s]+looking|cautionary\s+statements?", match.group())
        complete = (not tail.strip(" .:\t")
                    or re.match(r"[ \t]*[.:](?=[ \t]|$)", tail)
                    or match.group().isupper() and re.match(r"[ \t]+(?=[A-Z(\"'])", tail))
        start = after_heading(plain, match.end()) if heading and complete else position
        candidates.append(Candidate(start, "P2-item7", "legacy-risk"))
    return candidates


def title_pattern(title: str) -> re.Pattern:
    words = [re.escape(word) for word in title.strip().split()]
    return re.compile(r"(?im)^[ \t\xa0]*" + r"[ \t\xa0]+".join(words) + r"[ \t\xa0.:]*$")


def named_candidates(plain: str, stub: str) -> list[list[list[Candidate]]]:
    """Independent references → ordered title fallbacks → located candidates.

    A compound quoted path has source-defined inner-first fallback precedence;
    separate quoted references must compete, not inherit that precedence.
    """
    targets = []
    legacy = list(c.NAMED_HEADING_RE.finditer(stub))
    used_legacy = set()

    def located(title: str, tier: str, predicate: str) -> list[Candidate]:
        return [Candidate(after_heading(plain, hit.end()), tier, predicate)
                for hit in title_pattern(title).finditer(plain)
                if not is_inline_crossref(plain, hit.start())]

    for match in c.QUOTED_TITLE_RE.finditer(stub):
        groups = []
        for index, old in enumerate(legacy):
            if old.start() <= match.start() and match.end() <= old.end():
                groups.append(located(old.group(1).strip().rstrip("'\""), "P5-named-mdna", "legacy-named"))
                used_legacy.add(index)
        quoted = match.group(1) or match.group(2)
        parts = [part.strip() for part in re.split(r"\s*[—–]\s*|\s+-\s+", quoted) if part.strip()]
        titles = [part.rstrip(".,;: ") for part in reversed(parts)]
        titles += [half.strip() for title in list(titles) for half in reversed(re.split(r"\s+and\s+", title)[1:])]
        for title in dict.fromkeys(titles):
            if len(title) >= 8 and not c.MDNA_TITLE_RE.match(title):
                groups.append(located(title, "sec-named-heading", "sec-named"))
        targets.append(groups)
    for index, match in enumerate(legacy):
        if index not in used_legacy:
            targets.append([located(match.group(1).strip().rstrip("'\""), "P5-named-mdna", "legacy-named")])
    return targets


def alternative_candidates(plain: str) -> list[Candidate]:
    item_page = c.TOC_ITEM_1A_RE.search(plain)
    if item_page is None:
        return []
    candidates = []
    for match in c.ALTERNATIVE_HEADING_RE.finditer(plain):
        heading = match.group().strip().rstrip(".:")
        entry = re.compile(r"(?im)^[ \t\xa0]*" + r"[ \t\xa0]+".join(map(re.escape, heading.split()))
                           + r"[ \t\xa0.]+(\d{1,3})[ \t\xa0]*$")
        pages = {entry_match.group(1) for entry_match in entry.finditer(plain)}
        if pages == {item_page.group(1)} and not is_inline_crossref(plain, match.start()):
            candidates.append(Candidate(after_heading(plain, match.end()), "sec-alternative-heading", "sec-alternative"))
    return candidates


def _choose_sections(sections: list[Section]) -> tuple[Section | None, bool]:
    present = [section for section in sections if section.error is None]
    eligible = present or sections
    # Same located body discovered twice is one candidate; different locations are not guessed.
    unique = {(section.candidate.start, section.end, section.body): section for section in reversed(eligible)}
    if len(unique) > 1:
        return None, True
    return next(iter(unique.values()), None), False


def select_sections(plain: str, candidates: list[Candidate], *, require: str | None = None,
                    include_errors: bool = True) -> tuple[Section | None, bool]:
    sections = []
    for candidate in candidates:
        section = evaluate(plain, candidate)
        if section.error and not include_errors:
            continue
        if qualifies(section.body, candidate.predicate) and (require is None or qualifies(section.body, require)):
            sections.append(section)
    return _choose_sections(sections)


def _named_section(plain: str, targets: list[list[list[Candidate]]], *,
                   include_errors: bool) -> tuple[Section | None, bool]:
    selected = []
    for alternatives in targets:
        for group in alternatives:
            section, ambiguous = select_sections(plain, group, include_errors=include_errors)
            if ambiguous:
                return None, True
            if section is not None:
                selected.append(section)
                break
    return _choose_sections(selected)


def recovery_section(plain: str, stub: str) -> tuple[Section | None, bool]:
    risk, named, alternative = risk_candidates(plain), named_candidates(plain, stub), alternative_candidates(plain)
    # Search every lower-precedence opportunity for valid text before reporting
    # an earlier failed boundary. Keep the original diagnostics if none succeeds.
    for include_errors in (False, True):
        named_section, named_ambiguous = _named_section(plain, named, include_errors=include_errors)
        if named_ambiguous:
            return None, True
        section, ambiguous = select_sections(plain, risk, include_errors=include_errors)
        if section is not None or ambiguous:
            return section, ambiguous
        if named_section is not None:
            return named_section, False
        section, ambiguous = select_sections(plain, alternative, include_errors=include_errors)
        if section is not None or ambiguous:
            return section, ambiguous
    return None, False
