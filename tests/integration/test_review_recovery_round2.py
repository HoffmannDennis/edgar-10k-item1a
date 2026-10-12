"""Parent-owned recovery closure counterexamples; prior frozen tests unchanged."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a


RISK_PARAGRAPH = ("Our business risks could materially and adversely affect our financial condition and operations. "
                  "Adverse market conditions may reduce demand and harm our results.")
BODY = " ".join([RISK_PARAGRAPH] * 40)


def document(text, filename="primary.txt", doc_type="10-K"):
    return DocumentInput(filename=filename, content=text.encode(), doc_type=doc_type,
                         source=SourceRef(reference=f"fixture://{filename}", origin="synthetic"))


@pytest.mark.parametrize("order", (("a.txt", "b.txt"), ("b.txt", "a.txt")))
def test_all_explicit_annual_report_references_participate_in_ambiguity(order):
    answer = (f"The risk factors are incorporated by reference in the annual report ({order[0]}) "
              f"and the annual report ({order[1]}).")
    primary = document("Item 1A. Risk Factors\n" + answer + "\nItem 2. Properties\nOffice.")
    exhibits = tuple(document("Risk Factors\n" + BODY + f" DIFFERENT_{name}.\nSIGNATURES\nSignature", name, "EX-13")
                     for name in ("a.txt", "b.txt"))
    result = extract_item1a(FilingInput(primary=primary, exhibits=exhibits))
    assert result.text_status == TextStatus.ambiguous and result.text is None


def test_disclaimer_with_short_substantive_risk_is_not_an_ibr_pointer():
    disclaimer = ("This Form 10-K and the information incorporated by reference herein, as well as our "
                  "annual report, contain forward-looking statements.")
    body = disclaimer + " " + RISK_PARAGRAPH
    assert len(body) == 297
    primary = document("Item 1A. Risk Factors\n" + body + "\nItem 2. Properties\nOffice.")
    result = extract_item1a(FilingInput(primary=primary))
    assert result.text_status == TextStatus.present_primary and result.text == body
    assert result.quality_flag != "IBR_STUB"


def test_complete_uppercase_recovery_heading_is_removed_before_capitalized_body():
    text = ("Item 1A. Risk Factors\nSee Item 7 for our risks.\nItem 1B. Unresolved Staff Comments\nNone.\n"
            "Item 7. Other discussion\nFORWARD-LOOKING INFORMATION AND RISK FACTORS "
            + BODY + "\nSIGNATURES\nSignature")
    result = extract_item1a(FilingInput(primary=document(text)))
    assert result.text_status == TextStatus.present_recovered and result.text == BODY
