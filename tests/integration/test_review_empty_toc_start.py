"""A contents row without a page number has an empty body; the later genuine heading is tried."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a
from edgar_10k_item1a.documents import decode_document
from edgar_10k_item1a.projection import visible_text


BODY = ("Competition may materially adversely affect our business, financial condition and results of "
        "operations. We could lose customers and our margins may decline. ") * 12
BODY = BODY.rstrip()
SECTION = ("<p>PART I</p><p>Item 1. Business</p><p>We make widgets.</p><p>Item 1A. Risk Factors</p><p>" + BODY
           + "</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p>")


def filing(html):
    return FilingInput(primary=DocumentInput(
        filename="primary.htm", content=html.encode(), doc_type="10-K",
        source=SourceRef(reference="fixture://primary", origin="synthetic")))


@pytest.mark.parametrize("toc", [
    "<p>Item 1. Business</p><p>Item 1A. Risk Factors</p><p>Item 1B. Unresolved Staff Comments</p><p>Item 2. Properties</p>",
    "<table><tr><td>Item 1A.</td><td>Risk Factors</td></tr><tr><td>Item 1B.</td><td>Unresolved Staff Comments</td></tr>"
    "<tr><td>Item 2.</td><td>Properties</td></tr></table>",
    "<p><a href='#a'>Item 1A. Risk Factors</a></p><p><a href='#b'>Item 1C. Cybersecurity</a></p>",
])
def test_toc_row_without_page_number_does_not_block_the_section(toc):
    input_filing = filing("<html><body>" + toc + SECTION + "</body></html>")
    result = extract_item1a(input_filing)
    assert result.text_status is TextStatus.present_primary and result.text == BODY
    assert result.extraction_tier == "primary-canonical" and result.end_anchor_family == "E1"
    projection = visible_text(decode_document(input_filing.primary.content).text).text
    span = result.spans[0]
    assert projection[span.start:span.end].rstrip() == BODY


def test_toc_only_document_keeps_the_empty_body_diagnostic():
    toc = "<p>Item 1A. Risk Factors</p><p>Item 1B. Unresolved Staff Comments</p><p>Item 2. Properties</p>"
    result = extract_item1a(filing("<html><body>" + toc + "</body></html>"))
    assert result.text_status is TextStatus.start_unresolved and result.flags == ("empty_risk_body",)
