"""Parent-owned synthetic recovery/source-selection acceptance fixtures."""

import hashlib

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, extract_item1a
from edgar_10k_item1a.documents import decode_document
from edgar_10k_item1a.extraction.boundaries import normalize_body
from edgar_10k_item1a.models import Item1AExtraction, TextStatus
from edgar_10k_item1a.projection import visible_text


BODY = ("Our business may face material adverse financial risk from market factors and operations. " * 60).rstrip()
PRIMARY = ("Item 1A. Risk Factors\nSee Exhibit 13 for our risk factors incorporated by reference.\n"
           "Item 1B. Unresolved Staff Comments\nNone.\nItem 2. Properties\nOffice.")


def document(filename, text, doc_type=None):
    return DocumentInput(filename=filename, content=text.encode(), doc_type=doc_type,
                         source=SourceRef(reference=f"fixture://{filename}", origin="synthetic"))


def assert_source_and_spans(result, selected):
    assert result.source_document_sha256 == hashlib.sha256(selected.content).hexdigest()
    assert result.source == selected.source
    projection = visible_text(decode_document(selected.content).text)
    assert result.projection_sha256 == projection.sha256
    assert result.spans
    assert all(span.projection_sha256 == projection.sha256 for span in result.spans)
    assert "".join(normalize_body(projection.text[span.start:span.end])
                   for span in result.spans) == result.text
    assert Item1AExtraction.model_validate_json(result.model_dump_json()) == result


def test_parent_combined_heading_excludes_business_and_properties():
    text = ("Items 1 and 1A. Business and Risk Factors\nBusiness\nBUSINESS_ONLY_SENTINEL\n"
            f"Risk Factors\n{BODY}\nItem 2. Properties\nPROPERTY_ONLY_SENTINEL")
    primary = document("combined.txt", text, "10-K")
    result = extract_item1a(FilingInput(primary=primary))
    assert result.text_status == TextStatus.present_primary and result.text == BODY
    assert_source_and_spans(result, primary)


def test_parent_combined_heading_without_risk_subhead_abstains():
    primary = document("combined_unresolved.txt",
                       f"Items 1 and 1A. Business and Risk Factors\n{BODY}\nItem 2. Properties\nOffice.")
    result = extract_item1a(FilingInput(primary=primary))
    assert result.text_status == TextStatus.start_unresolved and result.text is None


def test_parent_pointer_recovers_later_risk_heading_with_same_source():
    text = ("Item 1A. Risk Factors\nSee risk factors in the annual report incorporated by reference.\n"
            "Item 1B. Unresolved Staff Comments\nNone.\nItem 7. Management Discussion\n"
            f"Risk Factors\n{BODY}\nSIGNATURES\nSignature")
    primary = document("recovery.txt", text, "10-K")
    result = extract_item1a(FilingInput(primary=primary))
    assert result.text_status == TextStatus.present_recovered and result.text == BODY
    assert_source_and_spans(result, primary)


def test_parent_item7_prose_lead_remains_part_of_recovered_body():
    lead = "Factors that could cause our actual results to differ include the following risks."
    risk = lead + "\n" + BODY
    text = ("Item 1A. Risk Factors\nSee Item 7 for our risks in this report.\n"
            "Item 1B. Unresolved Staff Comments\nNone.\nItem 7. Management Discussion\n"
            f"{risk}\nSIGNATURES\nSignature")
    primary = document("item7_recovery.txt", text, "10-K")
    result = extract_item1a(FilingInput(primary=primary))
    assert result.text_status == TextStatus.present_recovered and result.text == risk
    assert_source_and_spans(result, primary)


def test_parent_named_pointer_recovers_only_named_section():
    text = ("Item 1A. Risk Factors\nSee the disclosure under the heading \"Risk Outlook\" in Item 7.\n"
            "Item 1B. Unresolved Staff Comments\nNone.\nItem 7. Management Discussion\n"
            f"Risk Outlook\n{BODY}\nSIGNATURES\nSignature")
    primary = document("named_recovery.txt", text, "10-K")
    result = extract_item1a(FilingInput(primary=primary))
    assert result.text_status == TextStatus.present_recovered and result.text == BODY
    assert_source_and_spans(result, primary)


def test_parent_unique_formal_exhibit_source_identity():
    primary = document("primary.txt", PRIMARY, "10-K")
    exhibit = document("annual_report.txt", f"Risk Factors\n{BODY}\nSIGNATURES\nSignature", "EX-13")
    result = extract_item1a(FilingInput(primary=primary, exhibits=(exhibit,)))
    assert result.text_status == TextStatus.present_exhibit and result.text == BODY
    assert result.exhibit_type == "EX-13"
    assert_source_and_spans(result, exhibit)


def test_parent_formal_exhibit_has_no_eof_exception():
    primary = document("primary.txt", PRIMARY, "10-K")
    exhibit = document("annual_report.txt", f"Risk Factors\n{BODY}", "EX-13")
    result = extract_item1a(FilingInput(primary=primary, exhibits=(exhibit,)))
    assert result.text_status == TextStatus.boundary_unresolved and result.text is None
    assert "no_supported_end_anchor" in result.flags


def test_parent_unknown_exhibit_type_does_not_assert_formal_source():
    primary = document("primary.txt", PRIMARY, "10-K")
    exhibit = document("looks_like_ex13.txt", f"Risk Factors\n{BODY}\nSIGNATURES\nSignature")
    result = extract_item1a(FilingInput(primary=primary, exhibits=(exhibit,)))
    assert result.text_status == TextStatus.stub_unresolved and result.text is None
    assert "exhibit_type_unobserved" in result.flags


def test_parent_multiple_different_ex13_bodies_are_not_first_or_longest():
    primary = document("primary.txt", PRIMARY, "10-K")
    exhibits = tuple(document(f"annual_{number}.txt",
                              f"Risk Factors\n{BODY} DIFFERENT_{number}.\nSIGNATURES\nSignature", "EX-13")
                     for number in (1, 2))
    for ordering in (exhibits, tuple(reversed(exhibits))):
        result = extract_item1a(FilingInput(primary=primary, exhibits=ordering))
        assert result.text_status == TextStatus.ambiguous and result.text is None


def test_parent_explicit_exhibit_filename_resolves_other_candidates():
    primary = document("primary.txt", PRIMARY.replace("Exhibit 13", "Exhibit 13 (chosen.txt)"), "10-K")
    chosen = document("chosen.txt", f"Risk Factors\n{BODY}\nSIGNATURES\nSignature", "EX-13")
    other = document("other.txt", f"Risk Factors\n{BODY} OTHER_TARGET.\nSIGNATURES\nSignature", "EX-13")
    result = extract_item1a(FilingInput(primary=primary, exhibits=(other, chosen)))
    assert result.text_status == TextStatus.present_exhibit and result.text == BODY
    assert_source_and_spans(result, chosen)


def test_parent_typed_cautionary_exhibit_is_not_formal_item1a():
    primary = document("primary.txt", PRIMARY.replace("Exhibit 13", "Exhibit 99"), "10-K")
    cautionary = document("cautionary.txt",
                         "CAUTIONARY STATEMENT PURSUANT TO THE PRIVATE SECURITIES LITIGATION REFORM ACT OF 1995\n"
                         "Safe Harbor for Forward-Looking Statements\n" + BODY, "EX-99.1")
    result = extract_item1a(FilingInput(primary=primary, exhibits=(cautionary,)))
    assert result.text_status == TextStatus.present_cautionary_exhibit
    assert result.exhibit_type == "EX-99.1" and BODY in result.text
    assert_source_and_spans(result, cautionary)


def test_parent_absence_statement_is_observation_not_legal_exemption():
    primary = document("absence.txt",
                       "Item 1A. Risk Factors\nNot applicable.\nItem 1B. Unresolved Staff Comments\nNone.", "10-K")
    result = extract_item1a(FilingInput(primary=primary))
    assert result.text_status == TextStatus.absent_statement and result.text is None
    assert result.evidence and "not applicable" in result.evidence.lower()
    assert "obligation" not in result.model_dump()
