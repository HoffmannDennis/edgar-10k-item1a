"""Offline section orchestration over explicit primary and supplied exhibit bytes."""

from ..documents import ANNUAL_TEXT_FORMS, decode_document, normalize_form
from ..models import FilingInput, Item1AExtraction, TextStatus
from ..projection import visible_text
from .boundaries import canonical_body_start
from .constants import NOT_APPLICABLE_RE
from .exhibits import extract_from_exhibits
from .recovery import (Candidate, absent_statement, bare_candidates, combined_candidate,
                       cross_reference_index_evidence, evaluate, recovery_section, result_for,
                       select_sections, stub_reason)


def extract_item1a(filing: FilingInput) -> Item1AExtraction:
    if not isinstance(filing, FilingInput):
        raise TypeError("filing must be a FilingInput")
    document = filing.primary
    metadata = {
        "source_document": "primary",
        "source_document_sha256": document.sha256,
        "source": document.source,
    }
    input_flags = {"supplied_header_unprocessed"} if filing.header is not None else set()
    base_form, amendment = normalize_form(filing.form)
    if base_form not in ANNUAL_TEXT_FORMS:
        return Item1AExtraction(text_status=TextStatus.unsupported_form,
                               flags=tuple(sorted(input_flags | {"unsupported_text_form"})), **metadata)
    try:
        decoded = decode_document(document.content)
    except ValueError:
        return Item1AExtraction(text_status=TextStatus.document_parse_unresolved,
                               flags=tuple(sorted(input_flags | {"decoding_unresolved"})), **metadata)
    metadata.update(decoding_codec=decoded.codec, decoding_strategy=decoded.strategy)
    try:
        projection = visible_text(decoded.text)
    except ValueError as error:
        reason = "projection_multiple_html_roots" if "one primary HTML document" in str(error) else "projection_unresolved"
        return Item1AExtraction(text_status=TextStatus.document_parse_unresolved,
                               flags=tuple(sorted(input_flags | set(decoded.flags) | {reason})), **metadata)
    metadata.update(
        decoding_codec=decoded.codec, decoding_strategy=decoded.strategy,
        projection_version=projection.version, projection_sha256=projection.sha256,
    )
    flags = tuple(sorted(input_flags | set(decoded.flags)))
    start = canonical_body_start(projection.text)
    candidate = Candidate(start, "primary-canonical") if start is not None else None
    # A contents row without a page number has an empty body; a later genuine heading
    # replaces it. Without one, the first start keeps its empty-body diagnostic.
    while (candidate is not None and evaluate(projection.text, candidate).error == "empty_risk_body"
           and (later := canonical_body_start(projection.text, candidate.start)) is not None):
        candidate = Candidate(later, "primary-canonical")
    if candidate is None:
        candidate, error = combined_candidate(projection.text)
        if error:
            return Item1AExtraction(text_status=TextStatus.start_unresolved, extraction_tier="primary-combined",
                                   flags=tuple(sorted(set(flags) | {error})), **metadata)
    section = evaluate(projection.text, candidate) if candidate else None
    if section is not None:
        not_applicable = NOT_APPLICABLE_RE.fullmatch(section.body) is not None
        absence = absent_statement(section.body) or not_applicable
        reason = stub_reason(section.body)
        # EOF licenses only a complete, short nonpresent answer. It never licenses
        # formal text, an over-scope window or a financial-uncertainty bypass.
        eof_observation = (section.error == "no_supported_end_anchor" and not section.flags
                           and section.end == len(projection.text)
                           and (absence and section.end - candidate.start <= 1500
                                or reason in {"ibr_lead_pointer", "ibr_stub", "in_document_pointer"}
                                and section.end - candidate.start <= 1000))
        if section.error and not eof_observation:
            return result_for(section, projection, metadata, flags, TextStatus.present_primary)
        if absence:
            observed_flags = set(flags) | {"not_applicable_observed" if not_applicable
                                            else "src_absence_observed"}
            primary = Item1AExtraction(text_status=TextStatus.absent_statement, evidence=section.body,
                                      extraction_tier=section.candidate.tier, flags=tuple(sorted(observed_flags)), **metadata)
        else:
            if reason is None:
                return result_for(section, projection, metadata, flags, TextStatus.present_primary)
            primary = Item1AExtraction(text_status=TextStatus.stub_unresolved, evidence=section.body,
                                      extraction_tier=section.candidate.tier,
                                      flags=tuple(sorted(set(flags) | {reason})), **metadata)
    else:
        index_evidence = cross_reference_index_evidence(projection.text)
        if index_evidence:
            primary = Item1AExtraction(text_status=TextStatus.stub_unresolved, extraction_tier="primary-xref-index",
                                      evidence=index_evidence,
                                      flags=tuple(sorted(set(flags) | {"cross_reference_index_pointer"})), **metadata)
        else:
            primary = Item1AExtraction(text_status=TextStatus.not_found_in_amendment if amendment else TextStatus.missing,
                                      flags=flags, **metadata)
    if candidate is None:
        bare, ambiguous = select_sections(projection.text, bare_candidates(projection.text))
        if ambiguous:
            return Item1AExtraction(text_status=TextStatus.ambiguous,
                                   flags=tuple(sorted(set(flags) | {"ambiguous_primary_candidates"})), **metadata)
        if bare is not None:
            return result_for(bare, projection, metadata, flags, TextStatus.present_primary)
    if primary.text_status is not TextStatus.absent_statement:
        recovered, ambiguous = recovery_section(projection.text, primary.evidence or "")
        if ambiguous:
            return Item1AExtraction(text_status=TextStatus.ambiguous, evidence=primary.evidence,
                                   flags=tuple(sorted(set(primary.flags) | {"ambiguous_recovery_candidates"})), **metadata)
        if recovered is not None:
            return result_for(recovered, projection, dict(metadata, evidence=primary.evidence),
                              primary.flags, TextStatus.present_recovered)
    return extract_from_exhibits(filing, primary)
