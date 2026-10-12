import hashlib
import re

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a
from edgar_10k_item1a.documents import decode_document
from edgar_10k_item1a.projection import visible_text


def risks(label="Our"):
    return " ".join([
        f"{label} business risks could materially and adversely affect our financial condition and operations. "
        "Adverse market conditions may reduce demand and harm our results."
    ] * 40)


def document(text, filename="primary.txt", doc_type="10-K"):
    return DocumentInput(filename=filename, content=text.encode(), doc_type=doc_type,
                         source=SourceRef(reference=f"fixture://{filename}", origin="synthetic"))


def filing(text, *exhibits, header=None):
    return FilingInput(primary=document(text), exhibits=exhibits, header=header)


def assert_spans(result, selected_document):
    projection = visible_text(decode_document(selected_document.content).text)
    assert result.source == selected_document.source
    assert result.source_document_sha256 == selected_document.sha256
    assert result.text_sha256 == hashlib.sha256(result.text.encode()).hexdigest()
    assert result.flags == tuple(sorted(set(result.flags)))
    furniture = re.compile(
        r"(?i)\A[\s\xa0]*(?:table\s+of\s+contents|part\s+i{1,2}\b.{0,40}"
        r"|-{0,2}\s*\d{1,4}\s*-{0,2}|page\s+\d{1,4})[\s\xa0]*\Z"
    )
    pieces = []
    for span in result.spans:
        assert span.projection_sha256 == projection.sha256 == result.projection_sha256
        assert span.projection_version == projection.version == result.projection_version
        assert span.normalization_version == "item1a-body-v1"
        assert span.coordinate_space == "projected_char"
        lines = projection.text[span.start:span.end].split("\n")
        while lines and (not lines[-1].strip() or furniture.fullmatch(lines[-1])):
            lines.pop()
        pieces.append("\n".join(lines).rstrip())
    assert result.spans and "".join(pieces) == result.text
    assert result.end_anchor_position >= result.spans[-1].end


def test_combined_item_scope_starts_only_at_the_unique_risk_subhead():
    body = risks()
    input_filing = filing("Items 1 and 1A. Business and Risk Factors\nBusiness\nBUSINESS_SENTINEL.\n"
                          "Risk Factors\n" + body + "\nItem 2. Properties\nPROPERTY_SENTINEL")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary
    assert result.extraction_tier == "primary-combined"
    assert result.text == body
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("risk_headings,flag", [
    ("", "combined_risk_heading_missing"),
    ("Risk Factors\n" + risks("First") + "\nRisk Factors\n" + risks("Second"), "ambiguous_combined_risk_heading"),
], ids=["missing", "ambiguous"])
def test_unresolved_combined_scope_never_returns_business_text(risk_headings, flag):
    result = extract_item1a(filing("Items 1 and 1A. Business and Risk Factors\nBUSINESS_SENTINEL.\n"
                                  + risk_headings + "\nItem 2. Properties\nOffices."))
    assert result.text_status is TextStatus.start_unresolved
    assert result.text is None and flag in result.flags


@pytest.mark.parametrize("heading,tier", [
    ("Risk Factors", "P1-secondheading"),
    ("FORWARD-LOOKING INFORMATION AND RISK FACTORS", "P2-item7"),
])
def test_stub_recovery_is_located_and_uses_the_common_end(heading, tier):
    body = "(Our preamble remains.) " + risks()
    input_filing = filing("Item 1A. Risk Factors\nSee Item 7 for our risk discussion.\n"
                          "Item 1B. Unresolved Staff Comments\nNone.\nItem 7. Other discussion\n"
                          + heading + "\n" + body + "\nConsolidated Balance Sheets\nFINANCIAL_SENTINEL")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered
    assert result.extraction_tier == tier
    assert result.text == body and result.end_anchor_family == "E2"
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("stub,tier", [
    ("The information is incorporated by reference under the heading 'Regulatory Exposures' in MD&A.", "P5-named-mdna"),
    ("Information is described in “Management’s Discussion and Analysis — Regulatory Exposures.”", "sec-named-heading"),
])
def test_explicit_named_heading_recovery_retains_its_distinct_tier(stub, tier):
    body = risks()
    input_filing = filing("Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
                          "Regulatory Exposures\n" + body + "\nConsolidated Balance Sheets\nStatements.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered
    assert result.extraction_tier == tier
    assert result.text == body
    assert_spans(result, input_filing.primary)


def test_toc_associated_alternative_heading_is_located_without_using_arbitrary_successor_end():
    body = risks()
    input_filing = filing("Table of Contents\nItem 1A. Risk Factors 12\nFactors That May Affect Future Results 12\n"
                          "Item 2. Properties 20\nFactors That May Affect Future Results\n" + body
                          + "\nConsolidated Balance Sheets\nStatements.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered
    assert result.extraction_tier == "sec-alternative-heading"
    assert result.text == body
    assert_spans(result, input_filing.primary)


def pointer(target="13"):
    return ("Item 1A. Risk Factors\nThe information is incorporated by reference in Exhibit " + target
            + ".\nItem 1B. Unresolved Staff Comments\nNone.")


@pytest.mark.parametrize("doc_type,tier", [
    ("EX-13", "ex13-find_item_1a"), ("EX-13.A", "ex13-find_item_1a"),
    ("EX-99.1", "ex99-find_item_1a"), ("EX-99(A)", "ex99-find_item_1a"),
])
def test_formal_exhibit_is_typed_bound_and_hashed_to_its_original_bytes(doc_type, tier):
    body = "(Our exhibit preamble remains.) " + risks()
    exhibit = document("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nPROPERTY_SENTINEL", "annual.txt", doc_type)
    result = extract_item1a(filing(pointer(doc_type.removeprefix("EX-")), exhibit))
    assert result.text_status is TextStatus.present_exhibit
    assert result.extraction_tier == tier and result.exhibit_type == doc_type
    assert result.source_document == "exhibit"
    assert result.text == body
    assert "exhibit_reference_bound" in result.flags
    assert "exhibit_type_caller_declared" in result.flags
    assert_spans(result, exhibit)


@pytest.mark.parametrize("doc_type,prefix", [("EX-13", "ex13"), ("EX-99.1", "ex99")])
def test_formal_exhibit_risk_heading_has_its_distinct_lower_qualification(doc_type, prefix):
    body = risks()
    exhibit = document("Annual report\nRisk Factors\n" + body + "\nConsolidated Balance Sheets\nStatements.",
                       "annual.txt", doc_type)
    result = extract_item1a(filing(pointer(doc_type.removeprefix("EX-")), exhibit))
    assert result.text_status is TextStatus.present_exhibit
    assert result.extraction_tier == prefix + "-P1-secondheading"
    assert result.text == body
    assert_spans(result, exhibit)


@pytest.mark.parametrize("doc_type,prefix", [("EX-13", "ex13"), ("EX-99.1", "ex99")])
def test_shorter_exhibit_risk_heading_can_pass_without_legacy_recovery_word_gate(doc_type, prefix):
    body = "Our business risk may adversely affect our financial condition and operations. " * 22
    body = body.rstrip()
    assert len(body.split()) < 300 and len(body) >= 1500
    exhibit = document("Risk Factors\n" + body + "\nItem 2. Properties\nOffices.", "annual.txt", doc_type)
    result = extract_item1a(filing(pointer(doc_type.removeprefix("EX-")), exhibit))
    assert result.text_status is TextStatus.present_exhibit
    assert result.extraction_tier == prefix + "-risk-factors-heading"
    assert result.text == body
    assert_spans(result, exhibit)


def test_missing_referenced_exhibit_is_visible_not_primary_pointer_text():
    result = extract_item1a(filing(pointer("13.2"), document("Unrelated document", "other.txt", "EX-13.1")))
    assert result.text_status is TextStatus.stub_unresolved
    assert result.text is None
    assert result.flags == ("ibr_lead_pointer", "referenced_exhibit_missing")


@pytest.mark.parametrize("doc_type", [None, "", "EX-130", "EX-990", "10-K"])
def test_unknown_or_wrong_type_never_gets_present_exhibit(doc_type):
    exhibit = document("Item 1A. Risk Factors\n" + risks() + "\nItem 2. Properties\nOffices.", "ex13.htm", doc_type)
    result = extract_item1a(filing(pointer(), exhibit))
    assert result.text is None
    assert result.text_status is TextStatus.stub_unresolved
    assert "referenced_exhibit_missing" in result.flags
    if not doc_type:
        assert "exhibit_type_unobserved" in result.flags


@pytest.mark.parametrize("heading", ["Item 1A. Risk Factors", "Risk Factors", "FORWARD-LOOKING INFORMATION AND RISK FACTORS"])
def test_every_formal_exhibit_opportunity_requires_supported_end(heading):
    exhibit = document(heading + "\n" + risks(), "annual.txt", "EX-13")
    result = extract_item1a(filing(pointer(), exhibit))
    assert result.text_status is TextStatus.boundary_unresolved
    assert result.text is None and result.spans == ()
    assert "no_supported_end_anchor" in result.flags
    assert result.source_document_sha256 == exhibit.sha256


def test_distinct_unbound_exhibit_bodies_are_ambiguous_not_longest_first():
    a = document("Item 1A. Risk Factors\n" + risks("First") + "\nItem 2. Properties\nOffices.", "a.txt", "EX-99.1")
    b = document("Item 1A. Risk Factors\n" + risks("Second") + " Extra material risk.\nItem 2. Properties\nOffices.", "b.txt", "EX-99.2")
    result = extract_item1a(filing("No risk heading in the primary.", a, b))
    assert result.text_status is TextStatus.ambiguous
    assert result.text is None and result.spans == ()
    assert result.flags == ("ambiguous_exhibit_candidates",)


def test_one_explicit_target_beats_other_qualifying_documents():
    a = document("Item 1A. Risk Factors\n" + risks("First") + "\nItem 2. Properties\nOffices.", "a.txt", "EX-13.1")
    b = document("Item 1A. Risk Factors\n" + risks("Second") + "\nItem 2. Properties\nOffices.", "b.txt", "EX-13.2")
    result = extract_item1a(filing(pointer("13.2"), a, b))
    assert result.text_status is TextStatus.present_exhibit
    assert result.text == risks("Second")
    assert_spans(result, b)


def test_broad_target_with_different_qualifying_bodies_is_ambiguous():
    a = document("Item 1A. Risk Factors\n" + risks("First") + "\nItem 2. Properties\nOffices.", "a.txt", "EX-13.1")
    b = document("Item 1A. Risk Factors\n" + risks("Second") + "\nItem 2. Properties\nOffices.", "b.txt", "EX-13.2")
    result = extract_item1a(filing(pointer(), a, b))
    assert result.text_status is TextStatus.ambiguous
    assert "ambiguous_exhibit_candidates" in result.flags


def test_unbound_single_ex13_requires_reference_binding():
    exhibit = document("Item 1A. Risk Factors\n" + risks() + "\nItem 2. Properties\nOffices.", "a.txt", "EX-13")
    result = extract_item1a(filing("No risk heading in the primary.", exhibit))
    assert result.text_status is TextStatus.missing
    assert "exhibit_reference_unbound" in result.flags and result.text is None


def test_pslra_is_separate_document_bounded_cautionary_text_not_formal_item1a():
    body = ("CAUTIONARY STATEMENT PURSUANT TO THE PRIVATE SECURITIES LITIGATION REFORM ACT\n" + risks())
    exhibit = document(body, "safe-harbor.txt", "EX-99(A)")
    result = extract_item1a(filing("Item 1A. Risk Factors\nNot applicable.\nItem 2. Properties\nOffices.", exhibit))
    assert result.text_status is TextStatus.present_cautionary_exhibit
    assert result.extraction_tier == "P4-cautionary-exhibit"
    assert result.end_anchor_family == "document_bound"
    assert result.text == body
    assert "not_applicable_observed" in result.flags and "cautionary_not_formal_item1a" in result.flags
    assert_spans(result, exhibit)


def test_primary_present_wins_and_supplied_header_is_explicitly_unprocessed():
    body = risks()
    header = document("Header not parsed", "header.txt", None)
    exhibit = document("Item 1A. Risk Factors\n" + risks("Other") + "\nItem 2. Properties\nOffices.", "a.txt", "EX-99")
    input_filing = filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.", exhibit, header=header)
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary
    assert result.text == body and result.flags == ("supplied_header_unprocessed",)
    assert_spans(result, input_filing.primary)


def test_projection_failure_becomes_precise_parse_status():
    result = extract_item1a(filing("<html><body>One</body></html><html><body>Two</body></html>"))
    assert result.text_status is TextStatus.document_parse_unresolved
    assert result.flags == ("projection_multiple_html_roots",)


@pytest.mark.parametrize("body,expected,flag", [
    ("See Item 7 for the discussion.", TextStatus.stub_unresolved, "ibr_stub"),
    ("Not applicable.", TextStatus.absent_statement, "not_applicable_observed"),
    ("As a smaller reporting company we are not required to provide this information.", TextStatus.absent_statement, "src_absence_observed"),
])
def test_short_observations_never_become_present_or_a_legal_exemption(body, expected, flag):
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices."))
    assert result.text_status is expected
    assert result.text is None and result.evidence == body
    assert result.flags == (flag,)
    assert "obligation" not in result.model_dump()


def test_filing_scope_disclaimer_is_not_an_external_pointer():
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements. ")
    body = disclaimer + risks()
    input_filing = filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("heading,tier", [
    ("Risk Factors", "P1-secondheading"), ("FORWARD-LOOKING INFORMATION AND RISK FACTORS", "P2-item7"),
    ("Regulatory Exposures", "P5-named-mdna"),
])
def test_common_no_end_and_financial_uncertainty_checks_apply_to_recovery(heading, tier):
    stub = "Information is incorporated by reference under the heading 'Regulatory Exposures' in MD&A."
    prefix = "Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n" + heading + "\n"
    for tail, flag in [("", "no_supported_end_anchor"),
                       ("\nFinancial Review\nFINANCIAL_SENTINEL\nItem 2. Properties\nOffices.", "unclassified_financial_heading")]:
        result = extract_item1a(filing(prefix + risks() + tail))
        assert result.text_status is TextStatus.boundary_unresolved
        assert result.extraction_tier == tier
        assert result.text is None and flag in result.flags


def test_named_heading_duplicate_locations_are_not_guessed_last():
    stub = "Information is incorporated by reference under the heading 'Regulatory Exposures' in MD&A."
    text = ("Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
            "Regulatory Exposures\n" + risks() + "\nItem 2. Properties\nOffices.\n"
            "Regulatory Exposures\n" + risks("Different") + "\nSIGNATURES\nSigned.")
    result = extract_item1a(filing(text))
    assert result.text_status is TextStatus.ambiguous
    assert result.text is None and result.spans == ()
    assert "ambiguous_recovery_candidates" in result.flags


def test_cautionary_source_gate_is_three_full_enumerations_not_twenty():
    header = "CAUTIONARY STATEMENT PURSUANT TO THE PRIVATE SECURITIES LITIGATION REFORM ACT"
    enumeration = "Our business risk may adversely affect our financial operations and condition. " * 3
    filler = "These statements describe important business assumptions and their factual limitations. " * 50
    body = header + "\n" + enumeration + filler.rstrip()
    exhibit = document("EXHIBIT METADATA_PREFIX\n" + body, "cautionary.txt", "EX-99.1")
    result = extract_item1a(filing(pointer("99.1"), exhibit))
    assert result.text_status is TextStatus.present_cautionary_exhibit
    assert result.text == body
    assert "METADATA_PREFIX" not in result.text
    assert_spans(result, exhibit)


def test_explicit_target_without_end_never_falls_back_to_an_unreferenced_formal_document():
    a = document("Risk Factors\n" + risks() + "\nSIGNATURES\nSigned.", "a.txt", "EX-13.1")
    b = document("Risk Factors\n" + risks("Referenced"), "b.txt", "EX-13.2")
    result = extract_item1a(filing(pointer("13.2"), a, b))
    assert result.text_status is TextStatus.boundary_unresolved
    assert result.text is None and result.source_document_sha256 == b.sha256
    assert "no_supported_end_anchor" in result.flags


def test_cautionary_type_gate_excludes_ex13_and_untyped_payload():
    text = "CAUTIONARY STATEMENT PURSUANT TO THE PRIVATE SECURITIES LITIGATION REFORM ACT\n" + risks()
    for doc_type in ("EX-13", None):
        result = extract_item1a(filing(pointer(), document(text, "safe-harbor.txt", doc_type)))
        assert result.text is None and result.text_status is TextStatus.stub_unresolved


def test_later_business_risk_heading_skips_toc_without_changing_coordinates():
    body = risks()
    input_filing = filing("Item 1A. Business Risk Factors .... 12\nItem 2. Properties .... 20\n"
                          "Item 1A. Business Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary
    assert result.extraction_tier == "sec-later-combined-heading" and result.text == body
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("heading", ["Risk Factors", "1A. Risk Factors"])
def test_bare_primary_heading_has_the_source_1500_character_floor(heading):
    body = risks()
    input_filing = filing(heading + "\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary
    assert result.extraction_tier == "primary-bare-heading" and result.text == body
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("doc_type,prefix", [("EX-13", "ex13"), ("EX-99.1", "ex99")])
def test_exhibit_item7_recovery_is_not_the_formal_canonical_tier(doc_type, prefix):
    body = risks()
    exhibit = document("FORWARD-LOOKING INFORMATION AND RISK FACTORS\n" + body
                       + "\nSIGNATURES\nSigned.", "relocated.txt", doc_type)
    result = extract_item1a(filing(pointer(doc_type.removeprefix("EX-")), exhibit))
    assert result.text_status is TextStatus.present_exhibit
    assert result.extraction_tier == prefix + "-P2-item7" and result.text == body
    assert_spans(result, exhibit)


def test_legacy_three_head_enumerations_do_not_require_sec_1500_words():
    enumeration = "Our business risk may adversely affect our financial operations and condition. " * 3
    filler = "These statements describe important assumptions and factual limitations. " * 45
    body = enumeration + filler.rstrip()
    assert 300 <= len(body.split()) < 1500
    primary = "Item 1A. Risk Factors\nSee Item 7.\nItem 1B. Unresolved Staff Comments\nNone.\n"
    input_filing = filing(primary + "Risk Factors\n" + body + "\nSIGNATURES\nSigned.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered and result.text == body
    assert result.extraction_tier == "P1-secondheading"
    assert_spans(result, input_filing.primary)
    # The same body under a SEC alternative does not meet its distinct length/full-enum gate.
    alternative = ("Table of Contents\nItem 1A. Risk Factors 12\nFactors That May Affect Future Results 12\n"
                   + primary + "Factors That May Affect Future Results\n" + body + "\nSIGNATURES\nSigned.")
    rejected = extract_item1a(filing(alternative))
    assert rejected.text_status is TextStatus.stub_unresolved and rejected.text is None


def test_sec_named_gate_can_accept_sustained_enumeration_without_head_vocabulary():
    neutral_head = "Specific investigative background provides important historical context. " * 50
    body = neutral_head + risks()
    assert "risk" not in body[:3000].lower() and "business" not in body[:3000].lower()
    stub = "Information is described in “Regulatory Exposures” in MD&A."
    input_filing = filing("Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
                          "Regulatory Exposures\n" + body + "\nSIGNATURES\nSigned.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered and result.text == body
    assert result.extraction_tier == "sec-named-heading"
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("heading", ["Item\n1(a). Risk\nFactors", "I tem 1A /\nRisk Factors", "1(a). Risk Factors"])
def test_original_wrapped_and_parenthesized_start_variants_remain_heading_free(heading):
    body = risks()
    input_filing = filing(heading + "\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert_spans(result, input_filing.primary)


def test_localized_cross_reference_index_is_evidence_not_invented_prose():
    text = ("Form 10-K cross-reference index\nItem 1A Risk Factors 12-15 Item 1B Unresolved Staff Comments\n"
            "Item 2. Properties\nOffices.")
    result = extract_item1a(filing(text))
    assert result.text_status is TextStatus.stub_unresolved
    assert result.extraction_tier == "primary-xref-index"
    assert result.evidence == "Item 1A Risk Factors 12-15 Item 1B"
    assert result.flags == ("cross_reference_index_pointer",)
    assert result.text is None and result.spans == ()


def test_canonical_formal_exhibit_multiple_locations_are_ambiguous():
    exhibit = document("Item 1A. Risk Factors\n" + risks("First") + "\nItem 2. Properties\nOffice.\n"
                       "Item 1A. Risk Factors\n" + risks("Second") + "\nSIGNATURES\nSigned.", "annual.txt", "EX-13")
    result = extract_item1a(filing(pointer(), exhibit))
    assert result.text_status is TextStatus.ambiguous
    assert result.text is None and "ambiguous_exhibit_candidates" in result.flags


def test_identical_exhibit_bodies_have_stable_reported_provenance_not_input_order():
    text = "Risk Factors\n" + risks() + "\nSIGNATURES\nSigned."
    a, b = document(text, "a.txt", "EX-13"), document(text, "b.txt", "EX-13")
    results = [extract_item1a(filing(pointer(), *ordering)) for ordering in [(a, b), (b, a)]]
    assert results[0] == results[1]
    assert results[0].text_status is TextStatus.present_exhibit
    assert "equivalent_exhibit_candidates" in results[0].flags
    assert_spans(results[0], a)


def test_financial_uncertainty_is_shared_by_formal_exhibit_tiers():
    exhibit = document("Risk Factors\n" + risks() + "\nFinancial Statements\nFINANCIAL_SENTINEL\n"
                       "Item 2. Properties\nOffices.", "annual.txt", "EX-13")
    result = extract_item1a(filing(pointer(), exhibit))
    assert result.text_status is TextStatus.boundary_unresolved and result.text is None
    assert "unclassified_financial_heading" in result.flags
    assert result.source_document_sha256 == exhibit.sha256


def test_pslra_multiple_qualifying_unbound_documents_are_not_longest_ranked():
    header = "CAUTIONARY STATEMENT PURSUANT TO THE PRIVATE SECURITIES LITIGATION REFORM ACT\n"
    a = document(header + risks("First"), "a.txt", "EX-99.1")
    b = document(header + risks("Second"), "b.txt", "EX-99.2")
    result = extract_item1a(filing("Item 1A. Risk Factors\nNot applicable.\nItem 2. Properties\nOffices.", a, b))
    assert result.text_status is TextStatus.ambiguous
    assert result.flags == ("ambiguous_exhibit_candidates", "not_applicable_observed")


def test_exhibit_projection_failure_is_a_selected_document_parse_status():
    exhibit = document("<html><body>One</body></html><html><body>Two</body></html>", "annual.txt", "EX-13")
    result = extract_item1a(filing(pointer(), exhibit))
    assert result.text_status is TextStatus.document_parse_unresolved and result.text is None
    assert result.source_document_sha256 == exhibit.sha256
    assert "exhibit_projection_multiple_html_roots" in result.flags


def test_reference_to_external_annual_report_binds_an_explicit_ex13_document():
    exhibit = document("Risk Factors\n" + risks() + "\nSIGNATURES\nSigned.", "annual.txt", "EX-13")
    text = ("Item 1A. Risk Factors\nInformation is included in the 2025 annual report.\n"
            "Item 2. Properties\nOffices.")
    result = extract_item1a(filing(text, exhibit))
    assert result.text_status is TextStatus.present_exhibit and result.text == risks()
    assert "exhibit_reference_bound" in result.flags
    assert_spans(result, exhibit)


def test_named_risk_heading_recovery_removes_the_complete_title_not_only_risk_factors():
    input_filing = filing("Item 1A. Risk Factors\nSee Item 7.\nItem 1B. Unresolved Staff Comments\nNone.\n"
                          "Risk Factors that Could Affect Our Business\n" + risks() + "\nSIGNATURES\nSigned.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered and result.text == risks()
    assert_spans(result, input_filing.primary)


def test_indented_body_period_after_combined_heading_is_not_a_second_heading_delimiter():
    body = ". This body punctuation remains. " + risks()
    input_filing = filing("Items 1 and 1A. Business and Risk Factors\nBusiness\nOverview.\nRisk Factors\n   "
                          + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text == body
    assert_spans(result, input_filing.primary)


def test_specific_exhibit_target_is_not_widened_by_its_annual_report_description():
    a = document("Risk Factors\n" + risks("Other") + "\nSIGNATURES\nSigned.", "a.txt", "EX-13.1")
    b = document("Risk Factors\n" + risks("Chosen") + "\nSIGNATURES\nSigned.", "b.txt", "EX-13.2")
    text = pointer("13.2").replace("Exhibit 13.2.", "Exhibit 13.2, the Annual Report to Shareholders.")
    result = extract_item1a(filing(text, a, b))
    assert result.text_status is TextStatus.present_exhibit and result.text == risks("Chosen")
    assert_spans(result, b)


def test_typed_combined_exhibit_without_risk_subhead_returns_start_unresolved():
    exhibit = document("Items 1 and 1A. Business and Risk Factors\n" + risks()
                       + "\nItem 2. Properties\nOffices.", "annual.txt", "EX-13")
    result = extract_item1a(filing(pointer(), exhibit))
    assert result.text_status is TextStatus.start_unresolved and result.text is None
    assert "combined_risk_heading_missing" in result.flags
    assert result.source_document_sha256 == exhibit.sha256


def test_same_line_item7_recovery_removes_known_heading_but_keeps_body_parentheses():
    body = "(The preamble remains.) " + risks()
    input_filing = filing("Item 1A. Risk Factors\nSee Item 7.\nItem 1B. Unresolved Staff Comments\nNone.\n"
                          "FORWARD-LOOKING INFORMATION AND RISK FACTORS " + body + "\nSIGNATURES\nSigned.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered and result.text == body
    assert result.extraction_tier == "P2-item7"
    assert_spans(result, input_filing.primary)


def test_unbound_ex13_does_not_hide_equivalent_typed_ex99_formal_candidate():
    text = "Risk Factors\n" + risks() + "\nSIGNATURES\nSigned."
    a, b = document(text, "a.txt", "EX-13"), document(text, "b.txt", "EX-99.1")
    result = extract_item1a(filing("No risk heading in the primary.", a, b))
    assert result.text_status is TextStatus.present_exhibit
    assert result.exhibit_type == "EX-99.1" and result.text == risks()
    assert "equivalent_exhibit_candidates" in result.flags
    assert_spans(result, b)


@pytest.mark.parametrize("distance", [199999, 200000, 200001])
def test_recovery_and_formal_exhibit_paths_share_the_exact_scope_limit(distance):
    body = (risks() + " Additional material risk." * 10000)[:distance - 1]
    assert len(body) == distance - 1
    expected = TextStatus.boundary_unresolved if distance > 200000 else TextStatus.present_recovered
    input_filing = filing("Item 1A. Risk Factors\nSee Item 7.\nItem 1B. Unresolved Staff Comments\nNone.\n"
                          "Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    recovered = extract_item1a(input_filing)
    assert recovered.text_status is expected
    exhibit = document("Risk Factors\n" + body + "\nItem 2. Properties\nOffices.", "annual.txt", "EX-13")
    selected = extract_item1a(filing(pointer(), exhibit))
    assert selected.text_status is (TextStatus.boundary_unresolved if distance > 200000 else TextStatus.present_exhibit)
    if distance > 200000:
        assert "end_anchor_out_of_scope" in recovered.flags and "end_anchor_out_of_scope" in selected.flags
        assert recovered.text is None and selected.text is None
    else:
        assert recovered.text == selected.text == body.rstrip()
        assert_spans(recovered, input_filing.primary)
        assert_spans(selected, exhibit)


@pytest.mark.parametrize("note", [
    " The unrelated attachment a.txt is background information.",
    " Background material is listed in a.txt; it is not our risk factors.",
])
def test_review_binding_unrelated_filename_does_not_replace_or_supply_specific_target(note):
    a = document("Risk Factors\n" + risks("Other") + "\nSIGNATURES\nSigned.", "a.txt", "EX-13.1")
    b = document("Risk Factors\n" + risks("Target") + "\nSIGNATURES\nSigned.", "b.txt", "EX-13.2")
    text = pointer("13.2").replace("Exhibit 13.2.", "Exhibit 13.2." + note)
    selected = extract_item1a(filing(text, a, b))
    assert selected.text == risks("Target")
    assert_spans(selected, b)
    missing = extract_item1a(filing(text, a))
    assert missing.text_status is TextStatus.stub_unresolved and missing.text is None
    assert "referenced_exhibit_missing" in missing.flags


@pytest.mark.parametrize("chosen_type", ["EX-13.1", "EX-99.1", None])
def test_review_contextual_filename_must_match_specific_declared_type(chosen_type):
    exhibit = document("Risk Factors\n" + risks() + "\nSIGNATURES\nSigned.", "chosen.txt", chosen_type)
    result = extract_item1a(filing(pointer("13.2 (chosen.txt)"), exhibit))
    assert result.text_status is TextStatus.stub_unresolved and result.text is None
    assert "referenced_exhibit_missing" in result.flags


def test_review_contextual_missing_filename_does_not_choose_another_generic_family_member():
    exhibit = document("Risk Factors\n" + risks() + "\nSIGNATURES\nSigned.", "other.txt", "EX-13")
    result = extract_item1a(filing(pointer("13 (missing.txt)"), exhibit))
    assert result.text_status is TextStatus.stub_unresolved and result.text is None
    assert "referenced_exhibit_missing" in result.flags


def test_review_specificity_is_local_to_each_exhibit_number_family():
    a = document("Risk Factors\n" + risks("Thirteen") + "\nSIGNATURES\nSigned.", "a.txt", "EX-13.2")
    b = document("Risk Factors\n" + risks("NinetyNine") + "\nSIGNATURES\nSigned.", "b.txt", "EX-99.1")
    text = pointer("13.2 and Exhibit 99")
    result = extract_item1a(filing(text, b))
    assert result.text_status is TextStatus.present_exhibit and result.text == risks("NinetyNine")
    assert_spans(result, b)
    ambiguous = extract_item1a(filing(text, a, b))
    assert ambiguous.text_status is TextStatus.ambiguous and ambiguous.text is None


@pytest.mark.parametrize("target", ["13 (chosen.txt)", "13.2 (chosen.txt)", "13.2: chosen.txt"])
def test_review_attached_filename_restricts_candidates_without_changing_type_gate(target):
    a = document("Risk Factors\n" + risks("Other") + "\nSIGNATURES\nSigned.", "other.txt", "EX-13.2")
    b = document("Risk Factors\n" + risks("Chosen") + "\nSIGNATURES\nSigned.", "chosen.txt", "EX-13.2")
    selected = extract_item1a(filing(pointer(target), a, b))
    assert selected.text_status is TextStatus.present_exhibit and selected.text == risks("Chosen")
    assert_spans(selected, b)
    missing = extract_item1a(filing(pointer(target), a))
    assert missing.text_status is TextStatus.stub_unresolved and "referenced_exhibit_missing" in missing.flags


@pytest.mark.parametrize("doc_type", ["EX-13", "EX-99.1", None])
def test_review_filename_only_binding_requires_a_pointer_context_and_observed_type(doc_type):
    a = document("Risk Factors\n" + risks("Other") + "\nSIGNATURES\nSigned.", "other.txt", "EX-13")
    b = document("Risk Factors\n" + risks("Chosen") + "\nSIGNATURES\nSigned.", "chosen.txt", doc_type)
    text = ("Item 1A. Risk Factors\nThe risk factors are incorporated by reference in chosen.txt.\n"
            "Item 1B. Unresolved Staff Comments\nNone.")
    result = extract_item1a(filing(text, a, b))
    if doc_type:
        assert result.text_status is TextStatus.present_exhibit and result.text == risks("Chosen")
        assert_spans(result, b)
    else:
        assert result.text_status is TextStatus.stub_unresolved and result.text is None
        assert "referenced_exhibit_missing" in result.flags


def test_review_unrelated_filename_alone_cannot_bind_a_formal_ex13():
    exhibit = document("Risk Factors\n" + risks() + "\nSIGNATURES\nSigned.", "background.txt", "EX-13")
    result = extract_item1a(filing("Item 1A. Risk Factors\nSee Item 7 for our risk discussion. "
                                  "The unrelated attachment background.txt is background information.\n"
                                  "Item 1B. Unresolved Staff Comments\nNone.", exhibit))
    assert result.text_status is TextStatus.stub_unresolved and result.text is None
    assert "exhibit_reference_unbound" in result.flags


@pytest.mark.parametrize("answer,expected,flag", [
    ("See Exhibit 13 for our risk factors incorporated by reference.", TextStatus.stub_unresolved, "ibr_lead_pointer"),
    ("Not applicable.", TextStatus.absent_statement, "not_applicable_observed"),
    ("As a smaller reporting company we are not required to provide this information.",
     TextStatus.absent_statement, "src_absence_observed"),
])
def test_review_short_eof_answer_is_nonpresent_evidence_not_a_formal_eof_slice(answer, expected, flag):
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + answer))
    assert result.text_status is expected and result.evidence == answer
    assert result.text is None and result.spans == () and flag in result.flags
    exhibit = document("Risk Factors\n" + risks("Exhibit") + "\nSIGNATURES\nSigned.", "annual.txt", "EX-13")
    if expected is TextStatus.stub_unresolved:
        recovered = extract_item1a(filing("Item 1A. Risk Factors\n" + answer, exhibit))
        assert recovered.text_status is TextStatus.present_exhibit and recovered.text == risks("Exhibit")
        assert_spans(recovered, exhibit)


@pytest.mark.parametrize("answer", [
    "The information is incorporated by reference in Exhibit 13. " + risks(),
    "Not applicable.\n" + risks(),
    "As a smaller reporting company we are not required to provide this information.\n" + risks(),
])
def test_review_substantial_primary_eof_text_still_requires_a_formal_end(answer):
    exhibit = document("Risk Factors\n" + risks("Exhibit") + "\nSIGNATURES\nSigned.", "annual.txt", "EX-13")
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + answer, exhibit))
    assert result.text_status is TextStatus.boundary_unresolved
    assert result.text is None and result.spans == () and result.evidence is None
    assert "no_supported_end_anchor" in result.flags


@pytest.mark.parametrize("tail", ["", " " + risks()])
def test_review_disclaimer_only_suppresses_pointer_not_substantive_quality(tail):
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    body = disclaimer + tail
    input_filing = filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    if tail:
        assert result.text_status is TextStatus.present_primary and result.text == body
        assert_spans(result, input_filing.primary)
    else:
        assert result.text_status is TextStatus.stub_unresolved and result.evidence == body
        assert result.text is None and result.spans == ()


@pytest.mark.parametrize("subject", ["Our customers", "Our suppliers", "Other issuers"])
def test_review_third_party_src_description_does_not_discard_substantive_risks(subject):
    body = (subject + " are smaller reporting companies and are not required to provide Item 1A disclosures.\n"
            + ("Their limited disclosure may adversely affect our financial business operations. " * 8).rstrip())
    input_filing = filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert_spans(result, input_filing.primary)


def test_review_self_src_description_followed_by_actual_risks_is_not_an_absence_answer():
    body = ("We are a smaller reporting company and are not required to provide Item 1A disclosures.\n"
            + ("Nevertheless limited disclosure may adversely affect our financial business operations. " * 8).rstrip())
    input_filing = filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("answer", [
    "We are a smaller reporting company and are not required to provide Item 1A disclosures.",
    "The registrant is a smaller reporting company and has elected not to provide this information.",
    "We are not required to provide this information because we are a smaller reporting company.",
    "The company need not provide Item 1A disclosures because the company is a smaller reporting company.",
])
def test_review_self_identified_src_nondisclosure_is_only_an_observation(answer):
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + answer + "\nItem 2. Properties\nOffices."))
    assert result.text_status is TextStatus.absent_statement and result.evidence == answer
    assert result.text is None and result.flags == ("src_absence_observed",)
    assert "obligation" not in result.model_dump()


def test_review_first_person_customer_description_is_not_self_src_identity():
    body = ("We depend on customers that are smaller reporting companies and are not required to provide Item 1A disclosures.\n"
            + ("Their limited disclosure may adversely affect our financial business operations. " * 8).rstrip())
    input_filing = filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert_spans(result, input_filing.primary)


def test_review_single_sentence_self_src_and_actual_risks_is_not_an_absence_answer():
    body = ("We are a smaller reporting company and are not required to provide Item 1A disclosures, but "
            + "limited information may adversely affect our financial business operations and " * 8
            + "harm our results.")
    input_filing = filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("lead", [
    "Forward-looking information and risk factors described below could materially affect our business.",
    "FORWARD-LOOKING INFORMATION AND RISK FACTORS described below could materially affect our business.",
    "Cautionary statements describe material risk factors that could adversely affect our business.",
])
def test_review_recovery_prose_leads_remain_complete_body_text(lead):
    body = lead + "\n" + risks()
    input_filing = filing("Item 1A. Risk Factors\nSee Item 7.\nItem 1B. Unresolved Staff Comments\nNone.\n"
                          "Item 7. Other discussion\n" + body + "\nSIGNATURES\nSigned.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered and result.text == body
    assert result.extraction_tier == "P2-item7"
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("titles", [("Risk Outlook", "Regulatory Exposures"),
                                   ("Regulatory Exposures", "Risk Outlook")])
def test_review_independent_named_targets_are_ambiguous_in_either_quote_order(titles):
    stub = f'Information is incorporated by reference under the headings "{titles[0]}" and "{titles[1]}" in Item 7.'
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
                                  "Risk Outlook\n" + risks("First") + "\nConsolidated Balance Sheets\nStatements.\n"
                                  "Regulatory Exposures\n" + risks("Second") + "\nSIGNATURES\nSigned."))
    assert result.text_status is TextStatus.ambiguous and result.text is None and result.spans == ()
    assert "ambiguous_recovery_candidates" in result.flags


def test_review_compound_named_title_keeps_inner_fallback_precedence():
    stub = 'Information is described under "Operating Outlook — Regulatory Exposures" in Item 7.'
    input_filing = filing("Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
                          "Operating Outlook\n" + risks("Outer") + "\nConsolidated Balance Sheets\nStatements.\n"
                          "Regulatory Exposures\n" + risks("Inner") + "\nSIGNATURES\nSigned.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered and result.text == risks("Inner")
    assert result.extraction_tier == "sec-named-heading"
    assert_spans(result, input_filing.primary)


@pytest.mark.parametrize("have_outer", [False, True])
def test_review_compound_title_continues_after_invalid_inner_candidate(have_outer):
    stub = 'Information is described under "Operating Outlook — Regulatory Exposures" in Item 7.'
    text = ("Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
            "Regulatory Exposures\n" + risks("Inner") + "\nFinancial Review\nFINANCIAL_SENTINEL\n"
            "Consolidated Balance Sheets\nStatements.")
    if have_outer:
        text += "\nOperating Outlook\n" + risks("Outer") + "\nSIGNATURES\nSigned."
    input_filing = filing(text)
    result = extract_item1a(input_filing)
    if have_outer:
        assert result.text_status is TextStatus.present_recovered and result.text == risks("Outer")
        assert_spans(result, input_filing.primary)
    else:
        assert result.text_status is TextStatus.boundary_unresolved and result.text is None
        assert "unclassified_financial_heading" in result.flags


def test_review_independent_references_to_the_same_located_named_body_are_not_ambiguous():
    titles = ["Management’s Discussion and Analysis — Regulatory Exposures", "Regulatory Exposures"]
    results = []
    for ordering in (titles, list(reversed(titles))):
        stub = f'Information is incorporated by reference under the headings "{ordering[0]}" and "{ordering[1]}" in Item 7.'
        input_filing = filing("Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
                              "Regulatory Exposures\n" + risks() + "\nSIGNATURES\nSigned.")
        result = extract_item1a(input_filing)
        assert result.text_status is TextStatus.present_recovered and result.text == risks()
        assert_spans(result, input_filing.primary)
        results.append(result)
    assert results[0].extraction_tier == results[1].extraction_tier == "sec-named-heading"
    assert results[0].flags == results[1].flags
    assert (results[0].spans[0].start, results[0].spans[0].end) == (results[1].spans[0].start, results[1].spans[0].end)


@pytest.mark.parametrize("have_valid", [False, True])
def test_review_invalid_generic_candidate_does_not_mask_valid_named_body_or_lose_error(have_valid):
    stub = 'Information is incorporated by reference under the heading "Regulatory Exposures" in Item 7.'
    text = ("Item 1A. Risk Factors\n" + stub + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
            "Risk Factors\n" + risks("Invalid") + "\nFinancial Review\nFINANCIAL_SENTINEL\n"
            "Consolidated Balance Sheets\nStatements.")
    if have_valid:
        text += "\nRegulatory Exposures\n" + risks("Valid") + "\nSIGNATURES\nSigned."
    input_filing = filing(text)
    result = extract_item1a(input_filing)
    if have_valid:
        assert result.text_status is TextStatus.present_recovered and result.text == risks("Valid")
        assert_spans(result, input_filing.primary)
    else:
        assert result.text_status is TextStatus.boundary_unresolved and result.text is None
        assert "unclassified_financial_heading" in result.flags


@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("heading,lead", [
    ("Risk Factors:\n", ""),
    ("Risk Factors. ", "(PREAMBLE) "),
    ("Risk Factors: ", ". Body punctuation remains. "),
    ("Risk Factors:\n   ", ". Body punctuation remains. "),
])
def test_review_bare_and_combined_risk_headings_share_delimiters_without_consuming_preambles(combined, heading, lead):
    prefix = "Items 1 and 1A. Business and Risk Factors\nBusiness\nBUSINESS_SENTINEL\n" if combined else ""
    body = lead + risks()
    input_filing = filing(prefix + heading + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert result.extraction_tier == ("primary-combined" if combined else "primary-bare-heading")
    assert_spans(result, input_filing.primary)


def test_review_two_delimited_combined_risk_subheads_remain_ambiguous():
    result = extract_item1a(filing("Items 1 and 1A. Business and Risk Factors\nBusiness\nOverview.\n"
                                  "Risk Factors:\n" + risks("First") + "\nRisk Factors. " + risks("Second")
                                  + "\nItem 2. Properties\nOffices."))
    assert result.text_status is TextStatus.start_unresolved and result.text is None
    assert result.spans == () and "ambiguous_combined_risk_heading" in result.flags


@pytest.mark.parametrize("order", [("a.txt", "b.txt"), ("b.txt", "a.txt")])
def test_round2_independent_annual_references_compete_in_either_order(order):
    answer = (f"The risk factors are incorporated by reference in the annual report ({order[0]}) "
              f"and the annual report ({order[1]}).")
    a = document("Risk Factors\n" + risks("First") + "\nSIGNATURES\nSigned.", "a.txt", "EX-13")
    b = document("Risk Factors\n" + risks("Second") + "\nSIGNATURES\nSigned.", "b.txt", "EX-13")
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + answer + "\nItem 2. Properties\nOffices.", a, b))
    assert result.text_status is TextStatus.ambiguous and result.text is None and result.spans == ()
    assert "ambiguous_exhibit_candidates" in result.flags


@pytest.mark.parametrize("second_type", [None, "EX-99", "EX-13"])
def test_round2_missing_or_mistyped_independent_annual_target_cannot_be_silently_discarded(second_type):
    answer = "Information is incorporated by reference in the annual report (a.txt) and the annual report (b.txt)."
    a = document("Risk Factors\n" + risks("First") + "\nSIGNATURES\nSigned.", "a.txt", "EX-13")
    b = document("Risk Factors\n" + risks("Second") + "\nSIGNATURES\nSigned.",
                 "other.txt" if second_type == "EX-13" else "b.txt", second_type)
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + answer + "\nItem 2. Properties\nOffices.", a, b))
    assert result.text_status is TextStatus.ambiguous and result.text is None
    assert "referenced_exhibit_missing" in result.flags


def test_round2_repeated_identical_annual_reference_is_one_target():
    answer = "Information is incorporated by reference in the annual report (a.txt) and the annual report (a.txt)."
    a = document("Risk Factors\n" + risks() + "\nSIGNATURES\nSigned.", "a.txt", "EX-13")
    b = document("Risk Factors\n" + risks("Other") + "\nSIGNATURES\nSigned.", "b.txt", "EX-13")
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + answer + "\nItem 2. Properties\nOffices.", b, a))
    assert result.text_status is TextStatus.present_exhibit and result.text == risks()
    assert "equivalent_exhibit_candidates" not in result.flags
    assert_spans(result, a)


@pytest.mark.parametrize("report_title", ["the annual report to shareholders", "annual report to shareholders"])
def test_round2_complete_annual_report_phrase_keeps_each_attached_filename(report_title):
    answer = f"Information is incorporated by reference in {report_title} (a.txt) and {report_title} (b.txt)."
    a = document("Risk Factors\n" + risks() + "\nSIGNATURES\nSigned.", "a.txt", "EX-13")
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + answer + "\nItem 2. Properties\nOffices.", a))
    assert result.text_status is TextStatus.ambiguous and result.text is None
    assert "referenced_exhibit_missing" in result.flags


def test_round2_numbered_13_target_is_not_widened_by_multiple_annual_descriptions():
    a = document("Risk Factors\n" + risks("Chosen") + "\nSIGNATURES\nSigned.", "chosen.txt", "EX-13.2")
    b = document("Risk Factors\n" + risks("Other") + "\nSIGNATURES\nSigned.", "other.txt", "EX-13.1")
    text = pointer("13.2").replace("Exhibit 13.2.", "Exhibit 13.2, the annual report (a.txt) and the annual report (b.txt).")
    result = extract_item1a(filing(text, a, b))
    assert result.text_status is TextStatus.present_exhibit and result.text == risks("Chosen")
    assert_spans(result, a)


@pytest.mark.parametrize("risk_repeats", [0, 1, 40])
def test_round2_disclaimer_cue_is_ignored_consistently_but_body_and_substance_are_preserved(risk_repeats):
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    paragraph = ("Our business risks could materially and adversely affect our financial condition and operations. "
                 "Adverse market conditions may reduce demand and harm our results.")
    body = disclaimer + (" " + " ".join([paragraph] * risk_repeats) if risk_repeats else "")
    input_filing = filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    if risk_repeats:
        assert result.text_status is TextStatus.present_primary and result.text == body
        assert result.quality_flag != "IBR_STUB"
        assert_spans(result, input_filing.primary)
    else:
        assert result.text_status is TextStatus.stub_unresolved and result.text is None
        assert result.evidence == body and "quality_review_length" in result.flags


@pytest.mark.parametrize("sentence", [
    "The risk factors are incorporated by reference in Exhibit 13.2.",
    "the risk factors are incorporated by reference in Exhibit 13.2.",
])
def test_round2_real_external_pointer_after_disclaimer_still_binds_its_typed_target(sentence):
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    body = disclaimer + " " + sentence
    a = document("Risk Factors\n" + risks("Chosen") + "\nSIGNATURES\nSigned.", "chosen.txt", "EX-13.2")
    b = document("Risk Factors\n" + risks("Other") + "\nSIGNATURES\nSigned.", "other.txt", "EX-13.1")
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.", b, a))
    assert result.text_status is TextStatus.present_exhibit and result.text == risks("Chosen")
    assert result.evidence == body and "ibr_lead_pointer" in result.flags
    assert_spans(result, a)


def test_round2_real_in_document_pointer_after_disclaimer_remains_a_stub():
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    body = disclaimer + " See Item 7 for our risk discussion."
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices."))
    assert result.text_status is TextStatus.stub_unresolved and result.text is None and result.evidence == body
    assert "ibr_stub" in result.flags


def test_round2_real_incorporation_sentence_after_disclaimer_and_short_risks_is_not_suppressed():
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    body = (disclaimer + " Our business risks could materially and adversely affect our financial condition. "
            "The information is incorporated by reference in Exhibit 13.2.")
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices."))
    assert result.text_status is TextStatus.stub_unresolved and result.text is None and result.evidence == body
    assert "ibr_stub" in result.flags and "referenced_exhibit_missing" in result.flags


def test_round2_disclaimer_does_not_invent_binding_for_a_supplied_ex13():
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    exhibit = document("Risk Factors\n" + risks() + "\nSIGNATURES\nSigned.", "annual.txt", "EX-13")
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + disclaimer + "\nItem 2. Properties\nOffices.", exhibit))
    assert result.text_status is TextStatus.stub_unresolved and result.text is None and result.evidence == disclaimer
    assert "quality_review_length" in result.flags and "exhibit_reference_unbound" in result.flags


def test_round2_filename_pointer_after_disclaimer_is_not_widened_by_the_disclaimers_annual_report():
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    body = disclaimer + " The risk factors are incorporated by reference in chosen.txt."
    a = document("Risk Factors\n" + risks("Chosen") + "\nSIGNATURES\nSigned.", "chosen.txt", "EX-99.1")
    b = document("Risk Factors\n" + risks("Other") + "\nSIGNATURES\nSigned.", "other.txt", "EX-13")
    result = extract_item1a(filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffices.", b, a))
    assert result.text_status is TextStatus.present_exhibit and result.text == risks("Chosen")
    assert result.evidence == body
    assert_spans(result, a)


@pytest.mark.parametrize("heading,body_lead,remove_heading", [
    ("FORWARD-LOOKING INFORMATION AND RISK FACTORS ", "", True),
    ("FORWARD-LOOKING INFORMATION AND RISK FACTORS ", "(Our preamble remains.) ", True),
    ("FORWARD-LOOKING INFORMATION AND RISK FACTORS: ", ". Body punctuation remains. ", True),
    ("Forward-looking information and risk factors described below could materially affect our business.\n", "", False),
    ("FORWARD-LOOKING INFORMATION AND RISK FACTORS described below could materially affect our business.\n", "", False),
])
def test_round2_complete_uppercase_recovery_heading_is_distinct_from_a_prose_lead(heading, body_lead, remove_heading):
    body = body_lead + risks()
    input_filing = filing("Item 1A. Risk Factors\nSee Item 7.\nItem 1B. Unresolved Staff Comments\nNone.\n"
                          "Item 7. Other discussion\n" + heading + body + "\nSIGNATURES\nSigned.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_recovered
    assert result.text == (body if remove_heading else heading + body)
    assert result.extraction_tier == "P2-item7"
    assert_spans(result, input_filing.primary)
