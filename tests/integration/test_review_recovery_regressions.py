"""Parent-owned adversarial review repros; must not be weakened by the writer."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a
from edgar_10k_item1a.documents import decode_document
from edgar_10k_item1a.extraction.boundaries import normalize_body
from edgar_10k_item1a.projection import visible_text


def risks(label="Our"):
    return (f"{label} business risks could materially and adversely affect our financial condition and operations. "
            "Adverse market conditions may reduce demand and harm our results. " * 40).rstrip()


def document(text, filename="primary.txt", doc_type="10-K"):
    return DocumentInput(filename=filename, content=text.encode(), doc_type=doc_type,
                         source=SourceRef(reference=f"fixture://{filename}", origin="synthetic"))


def formal(label, filename, doc_type):
    return document(f"Risk Factors\n{risks(label)}\nSIGNATURES\nSignature", filename, doc_type)


def extract(answer, *exhibits, following="Item 1B. Unresolved Staff Comments\nNone."):
    text = "Item 1A. Risk Factors\n" + answer + ("\n" + following if following else "")
    return extract_item1a(FilingInput(primary=document(text), exhibits=exhibits))


def assert_source(result, selected):
    assert result.source == selected.source and result.source_document_sha256 == selected.sha256
    projection = visible_text(decode_document(selected.content).text)
    assert result.projection_sha256 == projection.sha256
    assert "".join(normalize_body(projection.text[s.start:s.end]) for s in result.spans) == result.text


def test_unrelated_filename_cannot_override_specific_exhibit_reference():
    a, b = formal("First", "a.txt", "EX-13.1"), formal("Second", "b.txt", "EX-13.2")
    answer = ("The risk factors are incorporated by reference in Exhibit 13.2. "
              "The unrelated attachment a.txt is only background information.")
    result = extract(answer, a, b)
    assert result.text_status == TextStatus.present_exhibit and result.text == risks("Second")
    assert_source(result, b)
    missing = extract(answer, a)
    assert missing.text_status == TextStatus.stub_unresolved and missing.text is None
    assert "referenced_exhibit_missing" in missing.flags


def test_pointer_at_primary_eof_can_bind_a_formally_ended_exhibit():
    exhibit = formal("Exhibit", "annual.txt", "EX-13")
    result = extract("See Exhibit 13 for our risk factors incorporated by reference.", exhibit, following="")
    assert result.text_status == TextStatus.present_exhibit and result.text == risks("Exhibit")
    assert_source(result, exhibit)


def test_exact_absence_answer_at_eof_remains_an_observation():
    result = extract("Not applicable.", following="")
    assert result.text_status == TextStatus.absent_statement and result.text is None
    assert result.evidence == "Not applicable."


def test_forward_looking_prose_lead_is_not_partially_removed_as_heading():
    lead = "Forward-looking information and risk factors described below could materially affect our business."
    body = lead + "\n" + risks()
    following = "Item 1B. Unresolved Staff Comments\nNone.\nItem 7. Other discussion\n" + body + "\nSIGNATURES\nSignature"
    result = extract("See Item 7 for our risk discussion.", following=following)
    assert result.text_status == TextStatus.present_recovered and result.text == body


@pytest.mark.parametrize("titles", (("Risk Outlook", "Regulatory Exposures"),
                                   ("Regulatory Exposures", "Risk Outlook")))
def test_competing_explicit_named_targets_do_not_depend_on_quote_order(titles):
    answer = f'The information is incorporated by reference under the headings "{titles[0]}" and "{titles[1]}" in Item 7.'
    following = ("Item 1B. Unresolved Staff Comments\nNone.\nRisk Outlook\n" + risks("First")
                 + "\nConsolidated Balance Sheets\nStatements.\nRegulatory Exposures\n" + risks("Second")
                 + "\nSIGNATURES\nSignature")
    result = extract(answer, following=following)
    assert result.text_status == TextStatus.ambiguous and result.text is None


def test_specific_reference_in_one_family_does_not_erase_other_family():
    exhibit = formal("Referenced99", "other.txt", "EX-99.1")
    result = extract("The risk factors are incorporated by reference in Exhibit 13.2 and Exhibit 99.", exhibit)
    assert result.text_status == TextStatus.present_exhibit and result.text == risks("Referenced99")
    assert_source(result, exhibit)


def test_invalid_generic_candidate_does_not_mask_valid_named_body():
    answer = 'The information is incorporated by reference under the heading "Regulatory Exposures" in Item 7.'
    following = ("Item 1B. Unresolved Staff Comments\nNone.\nRisk Factors\n" + risks("Invalid")
                 + "\nFinancial Review\nFINANCIAL_ONLY_SENTINEL\nConsolidated Balance Sheets\nStatements.\n"
                 "Regulatory Exposures\n" + risks("Valid") + "\nSIGNATURES\nSignature")
    result = extract(answer, following=following)
    assert result.text_status == TextStatus.present_recovered and result.text == risks("Valid")


def test_disclaimer_exception_does_not_bypass_substantive_quality_gate():
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    assert len(disclaimer) == 134
    result = extract(disclaimer)
    assert result.text_status == TextStatus.stub_unresolved and result.text is None
    assert result.evidence == disclaimer


def test_customer_exemption_description_is_not_filer_absence_answer():
    body = ("Our customers are smaller reporting companies and are not required to provide Item 1A disclosures. "
            + "Their limited disclosure may adversely affect our financial business operations. " * 8).rstrip()
    result = extract(body)
    assert result.text_status == TextStatus.present_primary and result.text == body
