"""Sentence-shaped or wrapped prose is not a non-item end heading."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a


PARA = ("Our business faces material risk. Adverse conditions may affect our financial "
        "condition and operations. Competition could reduce demand and harm our results.")
BODY = " ".join([PARA] * 24)
TAIL = "TAIL_SENTINEL more risks could adversely affect us."


def run(text, filename="primary.htm"):
    return extract_item1a(FilingInput(primary=DocumentInput(
        filename=filename, content=text.encode(), doc_type="10-K",
        source=SourceRef(reference=f"fixture://{filename}", origin="synthetic"))))


def wrapped(text, width=70):
    return "\n".join(text[index:index + width] for index in range(0, len(text), width))


@pytest.mark.parametrize("paragraph", [
    "Executive officers of the Company are critical to our success, and the loss of any of them could harm us.",
    "Information about our executive officers may change if key personnel leave.",
    "Executive officers of our Company may leave at any time.",
])
def test_sentence_starting_with_heading_words_is_body(paragraph):
    html = ("<html><body><p>Item 1A. Risk Factors</p><p>" + BODY + "</p><p>" + paragraph + "</p><p>" + TAIL
            + "</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>")
    result = run(html)
    assert result.text_status is TextStatus.present_primary
    assert result.text == BODY + "\n" + paragraph + "\n" + TAIL and result.end_anchor_family == "E1"


@pytest.mark.parametrize("continuation", [
    "We depend heavily on a small group of key\nexecutive officers of the Company. The loss of\nany of them could harm us.",
    "As a public company we must file periodic reports\npursuant to Section 13 or 15(d) of the Exchange Act, which is costly.",
    "Our certifying officers must sign the annual\ncertification of the principal executive officer, which adds liability.",
])
def test_wrapped_text_continuation_line_is_body(continuation):
    body = wrapped(BODY) + "\n" + continuation + "\n" + TAIL
    result = run("ITEM 1A. RISK FACTORS\n\n" + body + "\n\nITEM 1B. UNRESOLVED STAFF COMMENTS\n\nNone.\n",
                 filename="primary.txt")
    assert result.text_status is TextStatus.present_primary and result.text == body
    assert result.end_anchor_family == "E1"


@pytest.mark.parametrize("subhead,later", [
    ("LEGAL PROCEEDINGS", "Item 3. Legal Proceedings"),
    ("CONTROLS AND PROCEDURES", "Item 9A. Controls and Procedures"),
])
def test_caps_risk_subhead_repeated_later_in_mixed_case_is_body(subhead, later):
    body = BODY + "\n" + subhead + "\n" + BODY
    html = ("<html><body><p>Item 1A. Risk Factors</p><p>" + BODY + "</p><p>" + subhead + "</p><p>" + BODY
            + "</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p><p>Item 2. Properties</p><p>We lease.</p>"
            "<p>" + later + "</p><p>None.</p></body></html>")
    result = run(html)
    assert result.text_status is TextStatus.present_primary and result.text == body


@pytest.mark.parametrize("heading", [
    "EXECUTIVE OFFICERS OF THE REGISTRANT", "Executive Officers of the Registrant.",
    "Information About Our Executive Officers", "Executive Officers of the Company (as of March 1, 2025)",
])
def test_genuine_executive_officer_headings_still_end_the_section(heading):
    html = ("<html><body><p>Item 1A. Risk Factors</p><p>" + BODY + "</p><p>" + heading
            + "</p><p>Jane Doe, 52, Chief Executive Officer.</p></body></html>")
    result = run(html)
    assert result.text_status is TextStatus.present_primary and result.text == BODY
    assert result.end_anchor_family == "E2"
