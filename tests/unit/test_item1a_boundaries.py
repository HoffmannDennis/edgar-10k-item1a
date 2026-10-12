import hashlib
import re

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, Item1AExtraction, SourceRef, TextStatus, extract_item1a


def _risk_text():
    paragraph = (
        "Our business faces material risk. Adverse conditions may affect our financial "
        "condition and operations. Competition could reduce demand and harm our results."
    )
    return " ".join([paragraph] * 24)


def _filing(html, form="10-K"):
    return FilingInput(
        primary=DocumentInput(
            filename="synthetic.htm",
            content=html.encode("utf-8"),
            doc_type=form,
            source=SourceRef(reference="fixture://synthetic-primary", origin="synthetic"),
        ),
        form=form,
    )


def _assert_reconstructed(result, filing):
    from edgar_10k_item1a.projection import visible_text

    projection = visible_text(filing.primary.content.decode("utf-8"))
    furniture = re.compile(
        r"(?i)\A[\s\xa0]*(?:table\s+of\s+contents|part\s+i{1,2}\b.{0,40}"
        r"|-{0,2}\s*\d{1,4}\s*-{0,2}|page\s+\d{1,4})[\s\xa0]*\Z"
    )
    slices = []
    for span in result.spans:
        assert span.coordinate_space == "projected_char"
        assert span.projection_version == result.projection_version == projection.version
        assert span.projection_sha256 == result.projection_sha256 == projection.sha256
        assert span.normalization_version == "item1a-body-v1"
        lines = projection.text[span.start:span.end].split("\n")
        while lines and (not lines[-1].strip() or furniture.fullmatch(lines[-1])):
            lines.pop()
        slices.append("\n".join(lines).rstrip())
    assert result.spans
    assert "".join(slices) == result.text


def test_properties_before_later_inline_1b_uses_earliest_genuine_end():
    risks = _risk_text()
    filing = _filing(
        "<html><body><p>Item 1A. Risk Factors</p><p>" + risks + "</p>"
        "<p>Item 2. Properties</p><p>PROPERTIES_SENTINEL: We own factories. "
        "For staff matters see Item 1B. Unresolved Staff Comments in this report.</p>"
        "</body></html>"
    )

    result = extract_item1a(filing)

    assert result.text_status is TextStatus.present_primary
    assert result.extraction_tier == "primary-canonical"
    assert result.text == risks
    assert "PROPERTIES_SENTINEL" not in result.text
    assert result.end_anchor_family == "E1"
    assert result.source == filing.primary.source
    assert result.source_document == "primary"
    assert result.source_document_sha256 == hashlib.sha256(filing.primary.content).hexdigest()
    assert result.text_sha256 == hashlib.sha256(risks.encode("utf-8")).hexdigest()
    assert result.decoding_codec == "utf-8"
    assert result.decoding_strategy == "utf8_strict"
    assert result.flags == ()
    assert Item1AExtraction.model_validate_json(result.model_dump_json()) == result
    _assert_reconstructed(result, filing)
    from edgar_10k_item1a.projection import visible_text

    projection = visible_text(filing.primary.content.decode("utf-8"))
    assert result.end_anchor_position == projection.text.index("Item 2. Properties")


@pytest.mark.parametrize("heading,body_prefix,expected_prefix", [
    ("<p>Item 1A.</p><p>Risk Factors</p>", "<p>Important preamble.</p>", "Important preamble.\n"),
    ("<p>Item 1A.</p><p>. Risk Factors:</p>", "<p>Important preamble.</p>", "Important preamble.\n"),
    ("<p>Item 1A.</p>", "<p>Investment context</p>", "Investment context\n"),
    ("<p>Item 1A.</p>", "<p>Risk Factors affecting investment</p>", "Risk Factors affecting investment\n"),
])
def test_separate_caption_is_removed_but_other_caption_and_preamble_remain(heading, body_prefix, expected_prefix):
    risks = _risk_text()
    filing = _filing("<html><body>" + heading + body_prefix + "<p>" + risks + "</p>"
                     "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>")

    result = extract_item1a(filing)

    assert result.text_status is TextStatus.present_primary
    assert result.text == expected_prefix + risks
    _assert_reconstructed(result, filing)


@pytest.mark.parametrize("heading", [
    "Item 1B. Unresolved Staff Comments", "Item 1C. Cybersecurity", "Item 2. Properties",
    "Item 1B", "Item 1C.", "Item 2:",
])
def test_supported_e1_titles_and_standalone_labels(heading):
    risks = _risk_text()
    filing = _filing("<p>Item 1A. Risk Factors</p><p>" + risks + "</p>"
                     "<p>" + heading + "</p><p>NEXT_SECTION_SENTINEL</p>")

    result = extract_item1a(filing)

    assert result.text_status is TextStatus.present_primary
    assert result.text == risks
    assert result.end_anchor_family == "E1"
    _assert_reconstructed(result, filing)


@pytest.mark.parametrize("body_lead", ["(Our risks remain material.) ", ") This preamble remains. ", ". This punctuation remains. "])
def test_same_line_body_keeps_parentheses_punctuation_and_preamble(body_lead):
    expected = body_lead + _risk_text()
    filing = _filing("<p>Item 1A. Risk Factors. " + expected + "</p>"
                     "<p>Item 2. Properties</p><p>PROPERTY_SENTINEL</p>")

    result = extract_item1a(filing)

    assert result.text == expected
    _assert_reconstructed(result, filing)


def test_wrapped_and_inline_item_references_do_not_end_risk_body():
    expected = (_risk_text() + "\nRead this discussion in conjunction with\nItem 2. Properties\n"
                "Our exposure may remain material. See Item 1C. Cybersecurity for context.")
    filing = _filing("<p>Item 1A. Risk Factors</p><p>" + _risk_text() + "</p>"
                     "<p>Read this discussion in conjunction with</p><p>Item 2. Properties</p>"
                     "<p>Our exposure may remain material. See Item 1C. Cybersecurity for context.</p>"
                     "<p>Item 1B. Unresolved Staff Comments</p><p>END_SENTINEL</p>")

    result = extract_item1a(filing)

    assert result.text == expected
    assert result.end_anchor_family == "E1"
    _assert_reconstructed(result, filing)


@pytest.mark.parametrize("tail", [
    "", "<p>Item 7. Other discussion</p>",
    "<p>We also refer to Item 1B. Unresolved Staff Comments in another section.</p>",
    "<p>Item 2. This is not the Properties title</p>",
])
def test_no_supported_end_abstains_instead_of_eof_or_later_item_capture(tail):
    filing = _filing("<p>Item 1A. Risk Factors</p><p>" + _risk_text() + "</p>" + tail)

    result = extract_item1a(filing)

    assert result.text_status is TextStatus.boundary_unresolved
    assert result.text is None and result.text_sha256 is None and result.spans == ()
    assert result.quality_flag == "REVIEW"
    assert result.flags == ("no_supported_end_anchor",)
    assert result.source_document_sha256 == filing.primary.sha256


@pytest.mark.parametrize("distance", [199_999, 200_000, 200_001])
def test_section_scope_counts_projected_characters_not_raw_html_bytes(distance):
    body = "R" * (distance - 1)
    filing = _filing("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nNEXT_SECTION")

    result = extract_item1a(filing)

    assert result.end_anchor_family == "E1"
    if distance <= 200_000:
        assert result.text_status is TextStatus.present_primary
        assert result.text == body
        assert result.quality_flag == "REVIEW_TRUNCATION"
        assert result.end_anchor_position - result.spans[0].start == distance
        _assert_reconstructed(result, filing)
    else:
        assert result.text_status is TextStatus.boundary_unresolved
        assert result.text is None and result.spans == ()
        assert result.flags == ("end_anchor_out_of_scope",)


@pytest.mark.parametrize("form", ["10-K", "10-K405", "10-KT", "10-KT405", "10-K/A", "10-KT405/A"])
def test_annual_forms_and_explicit_amendments_can_supply_item_text(form):
    risks = _risk_text()
    filing = _filing("Item 1A. Risk Factors\n" + risks + "\nItem 2. Properties\nNEXT_SECTION", form)
    result = extract_item1a(filing)
    assert result.text_status is TextStatus.present_primary
    assert result.text == risks
    _assert_reconstructed(result, filing)


def test_missing_heading_in_amendment_has_separate_status():
    filing = _filing("<p>Item 9A. Controls and Procedures</p><p>Amended item only.</p>", "10-K/A")
    result = extract_item1a(filing)
    assert result.text_status is TextStatus.not_found_in_amendment
    assert result.text is None and result.spans == ()
    assert result.source == filing.primary.source
    assert result.source_document_sha256 == filing.primary.sha256


@pytest.mark.parametrize("form", ["10-Q", "10-KSB", "20-F"])
def test_unsupported_forms_never_offer_annual_item1a_text(form):
    result = extract_item1a(_filing("Item 1A. Risk Factors\n" + _risk_text() + "\nItem 2. Properties", form))
    assert result.text_status is TextStatus.unsupported_form
    assert result.text is None
    assert result.flags == ("unsupported_text_form",)


def test_toc_start_and_end_entries_do_not_shadow_the_real_body():
    risks = _risk_text()
    filing = _filing("<p>Table of Contents</p><p>Item 1A. Risk Factors ........ 12</p>"
                     "<p>Item 2. Properties ........ 20</p><p>Item 1. Business</p><p>Business overview.</p>"
                     "<p>Item 1A. Risk Factors</p><p>" + risks + "</p>"
                     "<p>Item 2. Properties</p><p>PROPERTY_SENTINEL</p>")

    result = extract_item1a(filing)

    assert result.text == risks
    _assert_reconstructed(result, filing)


def test_separate_continued_caption_does_not_create_a_new_start():
    risks = _risk_text()
    filing = _filing("<p>Item 1A.</p><p>Risk Factors (Continued)</p><p>EARLY_REPEAT_SENTINEL.</p>"
                     "<p>Item 1A. Risk Factors</p><p>" + risks + "</p>"
                     "<p>Item 2. Properties</p><p>PROPERTY_SENTINEL</p>")

    result = extract_item1a(filing)

    assert result.text == risks
    _assert_reconstructed(result, filing)


@pytest.mark.parametrize("heading,body_lead", [
    ("Item 1A. Risk Factors : ", "(Our preamble remains.) "),
    ("Item 1A.\n", ". Our body punctuation remains. "),
])
def test_only_heading_delimiter_is_removed_not_body_delimiter(heading, body_lead):
    expected = body_lead + _risk_text()
    filing = _filing(heading + expected + "\nItem 2. Properties\nPROPERTY_SENTINEL")
    result = extract_item1a(filing)
    assert result.text == expected
    _assert_reconstructed(result, filing)


def test_trailing_furniture_only_is_removed_and_interior_visible_lines_remain():
    risks = _risk_text()
    expected = (risks + "\nPage 7\nTable of Contents\nGE 2014 FORM 10-K 117\n"
                "Our final risk could materially harm our business.")
    filing = _filing("<p>Item 1A. Risk Factors</p><p>" + risks + "</p>"
                     "<p>Page 7</p><p>Table of Contents</p><footer>GE 2014 FORM 10-K 117</footer>"
                     "<p>Our final risk could materially harm our business.</p>"
                     "<p>Page 8</p><p>Table of Contents</p><p>Part II running header</p>"
                     "<p>Item 2. Properties</p><p>PROPERTY_SENTINEL</p>")

    result = extract_item1a(filing)

    assert result.text == expected
    _assert_reconstructed(result, filing)


def test_continued_end_heading_is_retained_until_the_genuine_end():
    risks = _risk_text()
    expected = risks + "\nItem 1B. Unresolved Staff Comments (Continued)\n" + risks
    filing = _filing("<p>Item 1A. Risk Factors</p><p>" + risks + "</p>"
                     "<p>Item 1B. Unresolved Staff Comments (Continued)</p><p>" + risks + "</p>"
                     "<p>Item 2. Properties</p><p>PROPERTY_SENTINEL</p>")

    result = extract_item1a(filing)

    assert result.text == expected
    _assert_reconstructed(result, filing)


@pytest.mark.parametrize("heading", [
    "Reports of Independent Registered\n\nPublic Accounting Firm",
    "MANAGEMENT'S DISCUSSION AND ANALYSIS",
    "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "MANAGING GLOBAL RISK Our risk-management framework follows.",
    "Controls and Procedures\n23\nDisclosure Controls and Procedures",
    "ENVIRONMENTAL MATTERS\nWe hereby incorporate by reference into this Item",
])
def test_each_shaped_e2_family_ends_at_its_first_genuine_header(heading):
    risks = _risk_text()
    filing = _filing("Item 1A. Risk Factors\n" + risks + "\n" + heading
                     + "\nOTHER_SECTION_SENTINEL\nItem 2. Properties\nOffices.")
    result = extract_item1a(filing)
    assert result.text == risks
    assert result.end_anchor_family == "E2"
    _assert_reconstructed(result, filing)


def test_page_furniture_does_not_hide_wrapped_crossref_to_e2_header():
    risks = _risk_text()
    crossref = ("For context see\n- 9 -\nTable of Contents\n"
                "Management's Discussion and Analysis of Financial Condition and Results of Operations\n"
                "in this report. Our financial exposure could be material.")
    filing = _filing("Item 1A. Risk Factors\n" + risks + "\n" + crossref
                     + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(filing)
    assert result.text == risks + "\n" + crossref
    assert result.end_anchor_family == "E1"
    _assert_reconstructed(result, filing)


def test_e3_caps_title_ends_formal_body_without_a_later_item_label():
    risks = _risk_text()
    filing = _filing("Item 1A. Risk Factors\n" + risks + "\nLEGAL PROCEEDINGS\nLAWSUIT_SENTINEL")
    result = extract_item1a(filing)
    assert result.text == risks
    assert result.end_anchor_family == "E3"
    _assert_reconstructed(result, filing)


def test_e3_duplicate_title_after_provisional_end_keeps_risk_subheading():
    risks = _risk_text()
    expected = risks + "\nLEGAL PROCEEDINGS\n" + risks
    filing = _filing("Item 1A. Risk Factors\n" + expected
                     + "\nItem 1B. Unresolved Staff Comments\nNone.\nITEM 3.\nLEGAL PROCEEDINGS\nCases.")
    result = extract_item1a(filing)
    assert result.text == expected
    assert result.end_anchor_family == "E1"
    _assert_reconstructed(result, filing)


def test_running_mdna_header_does_not_hide_second_page_risks():
    risks = _risk_text()
    running = "Management's Discussion and Analysis of Financial Condition and Results of Operations - Continued"
    expected = risks + "\n" + running + "\n" + risks
    filing = _filing("Item 1A. Risk Factors\n" + expected
                     + "\nConsolidated Balance Sheets\nFINANCIAL_SENTINEL")
    result = extract_item1a(filing)
    assert result.text == expected
    assert result.end_anchor_family == "E2"
    _assert_reconstructed(result, filing)


def test_e3_repeated_bare_title_before_explicit_same_title_end_is_not_a_risk_truncation():
    risks = _risk_text()
    expected = risks + "\nLEGAL PROCEEDINGS\n" + risks
    filing = _filing("Item 1A. Risk Factors\n" + expected + "\nITEM 3. LEGAL PROCEEDINGS\nLAWSUIT_SENTINEL")
    result = extract_item1a(filing)
    assert result.text == expected and result.end_anchor_family == "E3"
    _assert_reconstructed(result, filing)


def test_wrapped_item_label_retains_its_original_end_coordinate():
    risks = _risk_text()
    filing = _filing("Item 1A. Risk Factors\n" + risks + "\nItem\n1(b). Unresolved Staff Comments\nNone.")
    result = extract_item1a(filing)
    assert result.text == risks and result.end_anchor_family == "E1"
    from edgar_10k_item1a.projection import visible_text

    projection = visible_text(filing.primary.content.decode())
    assert projection.text[result.end_anchor_position:].startswith("Item\n1(b)")
    _assert_reconstructed(result, filing)


def test_missing_end_also_reports_unclassified_financial_heading():
    result = extract_item1a(_filing("Item 1A. Risk Factors\n" + _risk_text() + "\nFinancial Review\nFINANCIAL_SENTINEL"))
    assert result.text_status is TextStatus.boundary_unresolved and result.text is None
    assert result.flags == ("no_supported_end_anchor", "unclassified_financial_heading")


@pytest.mark.parametrize("false_heading", [
    "Item 1A. Risk Factors of this report",
    "Item 1A. Risk Factors (continued). Continued prose follows.",
])
def test_original_right_context_start_rejection_does_not_shadow_a_later_genuine_heading(false_heading):
    risks = _risk_text()
    filing = _filing(false_heading + "\nEARLY_REFERENCE_SENTINEL " + risks + "\n"
                     "Item 1A. Risk Factors\n" + risks + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(filing)
    assert result.text == risks
    _assert_reconstructed(result, filing)


@pytest.mark.parametrize("false_end", [
    "Item 2. Properties of our annual report describes our facilities.",
    "Item 2.\nProperties of this report describes our facilities.",
    "Table of Contents\nItem 1C.\nCybersecurity ..... 24",
    "Table of Contents\nExhibit Index 24",
    "Table of Contents\nLEGAL PROCEEDINGS\n24",
    "Table of Contents\nITEM 3. LEGAL PROCEEDINGS\n24",
    "Item 1B.\nUnresolved Staff Comments (Continued)",
    "Consolidated Balance Sheets\n(Continued)",
    "LEGAL PROCEEDINGS\n(Continued)",
])
def test_review_shared_end_validator_rejects_right_refs_toc_and_split_continuations(false_end):
    body = _risk_text() + "\n" + false_end + "\nTAIL_RISK_CONTINUES."
    input_filing = _filing("Item 1A. Risk Factors\n" + body + "\nItem 1B. Unresolved Staff Comments\nNone.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert result.end_anchor_family == "E1"
    _assert_reconstructed(result, input_filing)


@pytest.mark.parametrize("heading,family", [
    ("Item 1B.\nUnresolved Staff Comments", "E1"),
    ("Item 2.\nProperties\n24", "E1"),
    ("Consolidated Balance Sheets\n2025", "E2"),
    ("LEGAL PROCEEDINGS\n24", "E3"),
])
def test_review_wrapped_genuine_headings_and_numeric_data_are_not_blanket_toc_rejected(heading, family):
    input_filing = _filing("Item 1A. Risk Factors\n" + _risk_text() + "\n" + heading + "\nOTHER_SECTION_SENTINEL")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == _risk_text()
    assert result.end_anchor_family == family
    _assert_reconstructed(result, input_filing)


@pytest.mark.parametrize("toc", [
    "Table of Contents\nItem 1A. Risk Factors\n........ 13\nItem 2. Properties\n21\n",
    "Table of Contents\nItem 1A. Risk Factors\n13\nItem 1C. Cybersecurity\n21\n",
])
def test_review_wrapped_toc_start_continues_search_for_genuine_caption(toc):
    input_filing = _filing(toc + "Item 1A. Risk Factors\n" + _risk_text() + "\nItem 1B. Unresolved Staff Comments\nNone.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == _risk_text()
    _assert_reconstructed(result, input_filing)


@pytest.mark.parametrize("caption", [".. Risk Factors", ":: Risk Factors", "Risk Factors affecting investment"])
def test_review_nonexact_separate_caption_is_preserved_including_leading_punctuation(caption):
    body = caption + "\n" + _risk_text()
    input_filing = _filing("Item 1A.\n" + body + "\nItem 1B. Unresolved Staff Comments\nNone.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    _assert_reconstructed(result, input_filing)


def test_review_body_after_explicit_heading_delimiter_may_refer_below():
    body = "(The risks below remain material.) " + _risk_text()
    input_filing = _filing("Item 1A. Risk Factors. " + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    _assert_reconstructed(result, input_filing)


@pytest.mark.parametrize("heading,family", [
    ("Item 2. Properties", "E1"),
    ("Consolidated Balance Sheets", "E2"),
    ("LEGAL PROCEEDINGS", "E3"),
])
def test_review_genuine_end_heading_can_be_followed_by_prose_referring_below(heading, family):
    input_filing = _filing("Item 1A. Risk Factors\n" + _risk_text() + "\n" + heading
                          + "\nThe information below describes this separate section.\nOTHER_SECTION_SENTINEL")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == _risk_text()
    assert result.end_anchor_family == family
    _assert_reconstructed(result, input_filing)


def test_review_financial_uncertainty_is_not_suppressed_by_body_prose_referring_below():
    result = extract_item1a(_filing("Item 1A. Risk Factors\n" + _risk_text()
                                   + "\nFinancial Review\nThe information below describes our financial condition."
                                   "\nItem 2. Properties\nOffices."))
    assert result.text_status is TextStatus.boundary_unresolved and result.text is None
    assert result.spans == () and "unclassified_financial_heading" in result.flags


@pytest.mark.parametrize("heading", [
    "Consolidated Balance Sheets 2025",
    "Consolidated Statements of Cash Flows 2024",
])
def test_round2_financial_title_year_without_index_context_is_an_e2_end(heading):
    input_filing = _filing("Item 1A. Risk Factors\n" + _risk_text() + "\n" + heading
                          + "\nFINANCIAL_SENTINEL\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == _risk_text()
    assert result.end_anchor_family == "E2"
    _assert_reconstructed(result, input_filing)


@pytest.mark.parametrize("entry", [
    "Table of Contents\nConsolidated Balance Sheets 2025",
    "Consolidated Balance Sheets ..... 24",
    "Consolidated Balance Sheets 24\nItem 2. Properties 25",
])
def test_round2_financial_title_numeric_suffix_with_actual_toc_evidence_is_rejected(entry):
    body = _risk_text() + "\n" + entry + "\nTAIL_RISK_CONTINUES."
    input_filing = _filing("Item 1A. Risk Factors\n" + body + "\nItem 1B. Unresolved Staff Comments\nNone.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert result.end_anchor_family == "E1"
    _assert_reconstructed(result, input_filing)


@pytest.mark.parametrize("punctuation", ["..", ".", "::", "/"])
def test_round2_separate_caption_cannot_attach_across_an_intervening_body_line(punctuation):
    body = punctuation + "\nRisk Factors\n" + _risk_text()
    input_filing = _filing("Item 1A.\n" + body + "\nItem 2. Properties\nOffices.")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == body
    _assert_reconstructed(result, input_filing)


@pytest.mark.parametrize("heading", [
    "Item 1A.\n\nRisk Factors:\n",
    "Item 1A:\n \n. Risk Factors:\n",
    "Item 1A /\nRisk Factors\n",
    "<b>Item 1A.</b><br><strong>Risk Factors</strong><br>",
])
def test_round2_separate_caption_whitespace_delimiter_and_style_variants_still_attach(heading):
    tail = ("<p>" + _risk_text() + "</p><p>Item 2. Properties</p><p>Offices.</p>" if heading.startswith("<")
            else _risk_text() + "\nItem 2. Properties\nOffices.")
    input_filing = _filing(heading + tail)
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == _risk_text()
    _assert_reconstructed(result, input_filing)
