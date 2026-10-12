"""Reviewer counterexamples at the start and end boundary of Item 1A."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a


RISKS = ("Competition may materially adversely affect our business, financial condition and results. " * 20).rstrip()
END = "<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>"


def run(body):
    return extract_item1a(FilingInput(primary=DocumentInput(
        filename="primary.htm", content=f"<html><body>{body}</body></html>".encode(), doc_type="10-K",
        source=SourceRef(reference="fixture://primary", origin="synthetic"))))


@pytest.mark.parametrize("delimiter", [":", "."])
def test_run_in_body_after_a_delimited_caption_is_not_a_cross_reference(delimiter):
    lead = "The risks described below could harm us."
    result = run(f"<p>Item 1A. Risk Factors{delimiter} {lead} {RISKS}</p>{END}")
    assert result.text_status is TextStatus.present_primary and result.text == f"{lead} {RISKS}"


def test_undelimited_reference_after_the_caption_is_still_rejected():
    result = run(f"<p>Item 1A Risk Factors below describes how we manage risk.</p>"
                 f"<p>Item 1A. Risk Factors</p><p>{RISKS}</p>{END}")
    assert result.text_status is TextStatus.present_primary and result.text == RISKS


@pytest.mark.parametrize("sentence", [
    "Cybersecurity risk management and strategy are essential to protecting our systems.",
    "Reports of independent registered public accounting firms may identify material weaknesses.",
    "Certification of the principal executive officer is required and adds personal liability.",
    "Pursuant to Section 13 or 15(d) of the Exchange Act, we must file costly periodic reports.",
    "Management's Discussion and Analysis of Financial Condition and Results of Operations contains more detail.",
])
def test_sentence_starting_with_an_e2_phrase_does_not_end_the_section(sentence):
    result = run(f"<p>Item 1A. Risk Factors</p><p>{RISKS}</p><p>{sentence}</p><p>{RISKS}</p>{END}")
    assert result.text_status is TextStatus.present_primary
    assert result.text == f"{RISKS}\n{sentence}\n{RISKS}" and result.end_anchor_family == "E1"


@pytest.mark.parametrize("heading", [
    "Cybersecurity Risk Management and Strategy", "CYBERSECURITY RISK MANAGEMENT AND STRATEGY",
    "Report of Independent Registered Public Accounting Firm",
    "Report of Independent Registered Public Accounting Firm on Internal Control over Financial Reporting",
    "REPORT OF INDEPENDENT REGISTERED PUBLIC ACCOUNTING FIRM. To the Board of Directors",
    "CERTIFICATION OF CHIEF EXECUTIVE OFFICER PURSUANT TO SECTION 302",
    "Management's Discussion and Analysis of Financial Condition and Results of Operations",
])
def test_heading_shaped_e2_lines_still_end_the_section(heading):
    result = run(f"<p>Item 1A. Risk Factors</p><p>{RISKS}</p><p>{heading}</p><p>Other text follows.</p>")
    assert result.text_status is TextStatus.present_primary
    assert result.text == RISKS and result.end_anchor_family == "E2"
