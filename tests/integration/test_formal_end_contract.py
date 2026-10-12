"""Parent-owned end-contract oracles shared by primary and later P2 tiers."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, extract_item1a
from edgar_10k_item1a.models import TextStatus


BODY = "Our business faces material financial risk and adverse market factors. " * 60
BODY = BODY.rstrip()


def run(text):
    document = DocumentInput(filename="synthetic_contract.txt", content=text.encode(),
                             source=SourceRef(reference="fixture://formal-end", origin="synthetic"))
    return extract_item1a(FilingInput(primary=document))


@pytest.mark.parametrize("heading", (
    "REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM",
    "Consolidated Balance Sheets",
    "Exhibit Index",
    "Management's Discussion and Analysis of Financial Condition and Results of Operations",
))
def test_real_nonitem_boundary_excludes_following_section(heading):
    result = run(f"Item 1A. Risk Factors\n{BODY}\n{heading}\nFINANCIAL_ONLY_SENTINEL\nItem 2. Properties\nOffice")
    assert result.text_status == TextStatus.present_primary
    assert result.text == BODY
    assert result.end_anchor_family == "E2"


@pytest.mark.parametrize("title", ("Management's Discussion and Analysis",
                                   "Management's Discussion and Analysis of Financial Condition and Results of Operations"))
def test_wrapped_cross_reference_and_inline_financial_title_remain_risk_prose(title):
    tail = (f"For discussion see\n{title}\n"
            "in this report. The consolidated balance sheets could affect our risk evaluation.")
    result = run(f"Item 1A. Risk Factors\n{BODY}\n{tail}\nItem 2. Properties\nOffice")
    assert result.text_status == TextStatus.present_primary
    assert result.text == BODY + "\n" + tail
    assert result.end_anchor_family == "E1"


@pytest.mark.parametrize("heading", ("Financial Review", "Financial Statements",
                                      "Management's Discussion and Analysis"))
def test_unknown_financial_boundary_abstains_instead_of_overcapturing(heading):
    result = run(f"Item 1A. Risk Factors\n{BODY}\n{heading}\nOTHER_SECTION\nItem 2. Properties\nOffice")
    assert result.text_status == TextStatus.boundary_unresolved
    assert result.text is None
    assert "unclassified_financial_heading" in result.flags


@pytest.mark.parametrize("distance,expected", ((199999, TextStatus.present_primary),
                                               (200000, TextStatus.present_primary),
                                               (200001, TextStatus.boundary_unresolved)))
def test_exact_scope_cap(distance, expected):
    # The newline before the end heading belongs to the body-to-anchor distance.
    body = ("Risk may be adverse. " * 10001)[:distance - 1]
    assert len(body) == distance - 1
    result = run(f"Item 1A. Risk Factors\n{body}\nItem 2. Properties\nOffice")
    assert result.text_status == expected
    if expected == TextStatus.boundary_unresolved:
        assert "end_anchor_out_of_scope" in result.flags and result.text is None
    else:
        assert result.text == body.rstrip()


def test_unique_all_caps_item_title_is_an_e3_end():
    result = run(f"Item 1A. Risk Factors\n{BODY}\nLEGAL PROCEEDINGS\nLEGAL_ONLY_SENTINEL")
    assert result.text_status == TextStatus.present_primary and result.text == BODY
    assert result.end_anchor_family == "E3"


def test_later_duplicate_caps_title_protects_a_risk_subheading():
    risk = BODY + "\nLEGAL PROCEEDINGS\nOur litigation risk could materially affect our financial operations."
    text = (f"Item 1A. Risk Factors\n{risk}\nItem 1B. Unresolved Staff Comments\nNone.\n"
            "ITEM 3. LEGAL PROCEEDINGS\nLEGAL_ONLY_SENTINEL")
    result = run(text)
    assert result.text_status == TextStatus.present_primary and result.text == risk
    assert result.end_anchor_family == "E1"
