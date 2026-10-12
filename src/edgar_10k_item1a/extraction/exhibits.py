"""Explicit supplied-document exhibit route; no filename-derived types or retrieval.

Formal bodies precede PSLRA cautionary bodies. A unique explicit reference restricts
selection before qualification; distinct unbound bodies are never ranked by length.
"""

import re
from dataclasses import replace

from ..documents import decode_document
from ..models import FilingInput, Item1AExtraction, TextStatus
from ..projection import visible_text
from . import constants as c
from .boundaries import canonical_body_start, is_inline_crossref, is_running_header, normalize_body
from .recovery import (Candidate, Section, after_heading, combined_candidate, qualifies,
                       result_for, risk_candidates, select_sections)

_EXHIBIT_NUMBER_RE = re.compile(
    r"(?i)\bex(?:hibit|h\.?)?[\s\-]*\(?\s*(?P<number>13|99)(?![0-9])"
    r"(?P<suffix>\.[A-Za-z0-9]+|\([A-Za-z0-9]+\))?"
)
_ANNUAL_REPORT_RE = re.compile(
    r"(?i)annual\s+report\s+to\s+(?:share|stock)holders|"
    r"\b(?:the|our)\s+(?:(?:19|20)\d{2}\s+)?(?:[A-Za-z&.]+\s+){0,3}?annual\s+report(?!\s+on\s+form)"
    r"(?:\s+to\s+(?:share|stock)holders)?"
)
_FILENAME_TOKEN = r"[\w-]+(?:\.[\w-]+)+"
_REFERENCE_FILENAME_RE = re.compile(
    r"\A[ \t]*(?:\([ \t]*|[:,][ \t]*)[\"']?"
    r"(?P<filename>" + _FILENAME_TOKEN + r")(?=[\"')\s,;]|\.(?:\s|$)|$)"
)
_POINTER_FILENAME_RE = re.compile(
    r"(?i)\bincorporated\s+(?:herein\s+)?by\s+reference\s+(?:in|from)\s+"
    r"(?:the\s+)?(?:file\s+)?[\"']?(?P<filename>" + _FILENAME_TOKEN
    + r")(?=[\"')\s,;]|\.(?:\s|$)|$)"
)


def _reference_filename(evidence: str, end: int) -> str | None:
    """Only an immediately attached filename is binding, not any mention in evidence."""
    match = _REFERENCE_FILENAME_RE.match(evidence[end:end + 256])
    return match.group("filename").casefold() if match else None


def is_ex13(doc_type: str | None) -> bool:
    return bool(doc_type and c.EX13_TYPE_RE.match(doc_type.strip()))


def is_ex99(doc_type: str | None) -> bool:
    return bool(doc_type and c.EX99_TYPE_RE.match(doc_type.strip()))


def _add_flags(result: Item1AExtraction, *flags: str) -> Item1AExtraction:
    return Item1AExtraction.model_validate(dict(result.model_dump(), flags=tuple(sorted(set(result.flags) | set(flags)))))


def _formal(plain: str, prefix: str) -> tuple[Section | None, bool]:
    start = canonical_body_start(plain)
    combined, error = combined_candidate(plain) if start is None else (None, None)
    if error:
        return Section(Candidate(0, f"{prefix}-find_item_1a"), 0, None, "", error), False
    candidate = Candidate(start, f"{prefix}-find_item_1a", "exhibit") if start is not None else (
        replace(combined, tier=f"{prefix}-find_item_1a", predicate="exhibit") if combined else None
    )
    if candidate:
        candidates = [candidate]
        if start is not None:
            offset = start
            while (later := canonical_body_start(plain, offset)) is not None:
                candidates.append(Candidate(later, f"{prefix}-find_item_1a", "exhibit"))
                offset = later
        found, ambiguous = select_sections(plain, candidates)
        if found is not None or ambiguous:
            return found, ambiguous
    recovered, ambiguous = select_sections(plain, risk_candidates(plain), require="exhibit")
    if recovered is not None:
        return replace(recovered, candidate=replace(recovered.candidate, tier=f"{prefix}-{recovered.candidate.tier}")), False
    if ambiguous:
        return None, True
    headings = [Candidate(after_heading(plain, match.end()), f"{prefix}-risk-factors-heading", "exhibit")
                for match in c.EXHIBIT_RISK_HEADING_RE.finditer(plain)
                if not is_inline_crossref(plain, match.start()) and not is_running_header(plain, match.start())]
    return select_sections(plain, headings)


def _cautionary(plain: str) -> Section | None:
    matches = list(c.RECOVERY_PSLRA_EXHIBIT_RE.finditer(plain))
    # Only one document-local start is supported, rather than guessing a repeat.
    if len(matches) != 1:
        return None
    start = matches[0].start()
    if is_inline_crossref(plain, start) or not qualifies(plain[start:], "cautionary"):
        return None
    # Document bounds are licensed only for this separately typed disclosure, not formal Item 1A.
    return Section(Candidate(start, "P4-cautionary-exhibit", "cautionary"), len(plain),
                   "document_bound", normalize_body(plain[start:]))


def _targets(filing: FilingInput, evidence: str) -> tuple[list, bool, bool]:
    evidence = c.pointer_probe(evidence)
    typed = [document for document in filing.exhibits if is_ex13(document.doc_type) or is_ex99(document.doc_type)]
    references = [(match.group("number"), match.group("suffix"), _reference_filename(evidence, match.end()))
                  for match in _EXHIBIT_NUMBER_RE.finditer(evidence)]
    # Numbered EX-13 binding retains precedence over its annual-report description.
    # Without that binding, every independently named annual report participates.
    annual_names = set()
    if not any(number == "13" for number, _, _ in references):
        for match in _ANNUAL_REPORT_RE.finditer(evidence):
            filename = _reference_filename(evidence, match.end())
            references.append(("13", None, filename))
            if filename:
                annual_names.add(filename)
    if not references:
        # A filename-only incorporation pointer is supported, but bare filename
        # mentions are not. It cannot override an explicit numbered target.
        filenames = {match.group("filename").casefold() for match in _POINTER_FILENAME_RE.finditer(evidence[:1000])}
        if filenames:
            return [document for document in typed if document.filename.casefold() in filenames], True, False
        return typed, False, False
    supplied_annual_names = {document.filename.casefold() for document in typed if is_ex13(document.doc_type)}
    missing_independent_annual = len(annual_names) > 1 and not annual_names <= supplied_annual_names
    # Specificity belongs to a number family; EX-13.2 cannot erase a generic EX-99.
    specific_families = {number for number, suffix, filename in references if suffix or filename}
    references = [(number, suffix, filename) for number, suffix, filename in references
                  if number not in specific_families or suffix or filename]
    targets = []
    for document in typed:
        doc_type = document.doc_type.strip().upper()
        for number, suffix, filename in references:
            if suffix:
                matched = doc_type == "EX-" + number + suffix.upper()
            else:
                matched = is_ex13(doc_type) if number == "13" else is_ex99(doc_type)
            if matched and (filename is None or document.filename.casefold() == filename):
                targets.append(document)
                break
    return targets, True, missing_independent_annual


def _ambiguous(primary: Item1AExtraction) -> Item1AExtraction:
    return Item1AExtraction(text_status=TextStatus.ambiguous, evidence=primary.evidence,
                           source=primary.source, source_document_sha256=primary.source_document_sha256,
                           projection_version=primary.projection_version, projection_sha256=primary.projection_sha256,
                           decoding_codec=primary.decoding_codec, decoding_strategy=primary.decoding_strategy,
                           flags=tuple(sorted(set(primary.flags) | {"ambiguous_exhibit_candidates"})))


# Primary outcomes for which supplied exhibits are consulted; a fetch route uses the same set.
EXHIBIT_ROUTE_STATUSES = frozenset({
    TextStatus.stub_unresolved, TextStatus.missing, TextStatus.not_found_in_amendment, TextStatus.absent_statement,
})


def extract_from_exhibits(filing: FilingInput, primary: Item1AExtraction) -> Item1AExtraction:
    if primary.present or primary.text_status not in EXHIBIT_ROUTE_STATUSES:
        return primary
    targets, bound, missing_independent_annual = _targets(filing, primary.evidence or "")
    if any(not document.doc_type or not document.doc_type.strip() for document in filing.exhibits):
        primary = _add_flags(primary, "exhibit_type_unobserved")
    if missing_independent_annual:
        return _ambiguous(_add_flags(primary, "referenced_exhibit_missing"))
    if bound and not targets:
        return _add_flags(primary, "referenced_exhibit_missing")
    formal, cautionary, failures = [], [], []
    for document in targets:
        metadata = dict(source_document="exhibit", source_document_sha256=document.sha256,
                        source=document.source, exhibit_type=document.doc_type, evidence=primary.evidence)
        flags = set(primary.flags) | {"exhibit_type_caller_declared"}
        if bound:
            flags.add("exhibit_reference_bound")
        try:
            decoded = decode_document(document.content)
        except ValueError:
            failures.append(Item1AExtraction(text_status=TextStatus.document_parse_unresolved,
                                            flags=tuple(sorted(flags | {"exhibit_decoding_unresolved"})), **metadata))
            continue
        flags.update(decoded.flags)
        metadata.update(decoding_codec=decoded.codec, decoding_strategy=decoded.strategy)
        try:
            projection = visible_text(decoded.text)
        except ValueError as error:
            reason = "exhibit_projection_multiple_html_roots" if "one primary HTML document" in str(error) else "exhibit_projection_unresolved"
            failures.append(Item1AExtraction(text_status=TextStatus.document_parse_unresolved,
                                            flags=tuple(sorted(flags | {reason})), **metadata))
            continue
        metadata.update(projection_sha256=projection.sha256, projection_version=projection.version)
        section, ambiguous = _formal(projection.text, "ex13" if is_ex13(document.doc_type) else "ex99")
        if ambiguous:
            return _ambiguous(primary)
        if section:
            result = result_for(section, projection, metadata, tuple(sorted(flags)), TextStatus.present_exhibit)
            (formal if result.present else failures).append(result)
        if is_ex99(document.doc_type) and (section is None or section.error is not None):
            caut = _cautionary(projection.text)
            if caut:
                cautionary.append(result_for(caut, projection, metadata,
                                            tuple(sorted(flags | {"cautionary_not_formal_item1a"})),
                                            TextStatus.present_cautionary_exhibit))
    if len({result.text for result in formal}) > 1:
        return _ambiguous(primary)
    eligible_formal = formal if bound else [result for result in formal if not is_ex13(result.exhibit_type)]
    candidates = eligible_formal or cautionary
    if len({(result.text_status, result.text) for result in candidates}) > 1:
        return _ambiguous(primary)
    if candidates:
        # Equivalent bodies are not different findings; stable provenance, with an explicit warning.
        selected = min(candidates, key=lambda result: (result.source.reference, result.source_document_sha256))
        if len(candidates) > 1 or (eligible_formal and len(formal) > 1):
            selected = _add_flags(selected, "equivalent_exhibit_candidates")
        if failures:
            selected = _add_flags(selected, "other_exhibit_candidates_unresolved")
        return selected
    if formal and not bound:
        return _add_flags(primary, "exhibit_reference_unbound")
    if len(failures) == 1:
        return failures[0]
    if failures:
        return _add_flags(primary, "exhibit_candidates_unresolved")
    return _add_flags(primary, "referenced_exhibit_no_qualifying_text") if bound else primary
