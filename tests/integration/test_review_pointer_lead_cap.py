"""A pointer-shaped first sentence does not turn a full risk section into a stub."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a


LEAD = ("You should carefully consider the risks described below together with the other information in this "
        "Form 10-K, including information incorporated by reference from our Proxy Statement.")
RISKS = ("Competition may materially adversely affect our business, financial condition and results of "
         "operations. We could lose customers and our margins may decline. ") * 24
BODY = LEAD + "\n" + RISKS.rstrip()


def run(html):
    return extract_item1a(FilingInput(primary=DocumentInput(
        filename="primary.htm", content=html.encode(), doc_type="10-K",
        source=SourceRef(reference="fixture://primary", origin="synthetic"))))


@pytest.mark.parametrize("heading", ["Item 1A. Risk Factors", "ITEM 1A — RISK FACTORS"])
def test_long_section_with_pointer_shaped_lead_is_present_primary(heading):
    result = run(f"<html><body><p>{heading}</p><p>{LEAD}</p><p>{RISKS}</p>"
                 "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>")
    assert result.text_status is TextStatus.present_primary and result.text == BODY
    assert result.extraction_tier == "primary-canonical"
    assert "ibr_lead_pointer" not in result.flags and result.evidence is None


def test_short_pointer_lead_remains_a_stub():
    stub = ("The information required by this Item is incorporated by reference to the section entitled "
            "\"Risk Factors\" in our 2023 Annual Report to Shareholders.")
    result = run(f"<html><body><p>Item 1A. Risk Factors</p><p>{stub}</p>"
                 "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>")
    assert result.text_status is TextStatus.stub_unresolved and result.evidence == stub
    assert "ibr_lead_pointer" in result.flags
