"""Parent-owned start/end review fixtures; exact body preservation is mandatory."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a


BODY = ("Our business faces material financial risk and adverse market factors. " * 60).rstrip()
END = "\nItem 1B. Unresolved Staff Comments\nNone."


def run(text):
    return extract_item1a(FilingInput(primary=DocumentInput(
        filename="review_boundary.txt", content=text.encode(),
        source=SourceRef(reference="fixture://review-boundary", origin="synthetic"))))


@pytest.mark.parametrize("false_end", (
    "Item 2. Properties of this report describes our facilities.",
    "Table of Contents\nItem 2\nProperties ..... 20",
    "Table of Contents\nExhibit Index 24",
    "Table of Contents\nLEGAL PROCEEDINGS\n24",
    "Item 1B.\nUnresolved Staff Comments (Continued)",
    "Consolidated Balance Sheets\n(Continued)",
))
def test_right_context_toc_or_continued_heading_does_not_end_risk_body(false_end):
    body = BODY + "\n" + false_end + "\nTAIL_RISK_CONTINUES."
    result = run("Item 1A. Risk Factors\n" + body + END)
    assert result.text_status == TextStatus.present_primary and result.text == body
    assert result.end_anchor_family == "E1"


@pytest.mark.parametrize("toc", (
    "Table of Contents\nItem 1A. Risk Factors\n........ 13\nItem 2. Properties\n21\n",
    "Table of Contents\nItem 1A. Risk Factors\n13\nItem 1C. Cybersecurity\n21\n",
))
def test_canonical_start_skips_wrapped_dot_or_cybersecurity_toc(toc):
    result = run(toc + "Item 1A. Risk Factors\n" + BODY + END)
    assert result.text_status == TextStatus.present_primary and result.text == BODY


@pytest.mark.parametrize("heading,preamble", (
    ("Items 1 and 1A. Business and Risk Factors\nBusiness\nBUSINESS_SENTINEL\nRisk Factors:\n", ""),
    ("Items 1 and 1A. Business and Risk Factors\nBusiness\nBUSINESS_SENTINEL\nRisk Factors. ", "(PREAMBLE) "),
    ("Risk Factors:\n", ""),
))
def test_noncanonical_starts_follow_delimiter_and_preamble_contract(heading, preamble):
    result = run(heading + preamble + BODY + END)
    assert result.text_status == TextStatus.present_primary and result.text == preamble + BODY


def test_nonexact_separate_caption_is_retained_as_body_text():
    body = ".. Risk Factors\n" + BODY
    result = run("Item 1A.\n" + body + END)
    assert result.text_status == TextStatus.present_primary and result.text == body
