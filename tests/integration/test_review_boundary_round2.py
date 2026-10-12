"""Parent-owned closure-review near misses; previous frozen oracles unchanged."""

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a


BODY = ("Our business faces material financial risk and adverse market factors. " * 60).rstrip()


def run(body, following):
    text = "Item 1A.\n" + body + "\n" + following
    return extract_item1a(FilingInput(primary=DocumentInput(
        filename="boundary_round2.txt", content=text.encode(),
        source=SourceRef(reference="fixture://boundary-round2", origin="synthetic"))))


def test_financial_heading_year_without_toc_evidence_is_not_a_page_suffix():
    result = run("Risk Factors\n" + BODY,
                 "Consolidated Balance Sheets 2025\nFINANCIAL_ONLY_SENTINEL\nItem 2. Properties\nOffice.")
    assert result.text_status == TextStatus.present_primary and result.text == BODY
    assert result.end_anchor_family == "E2"


def test_separate_caption_is_not_attached_across_body_punctuation():
    body = "..\nRisk Factors\n" + BODY
    result = run(body, "Item 2. Properties\nOffice.")
    assert result.text_status == TextStatus.present_primary and result.text == body
