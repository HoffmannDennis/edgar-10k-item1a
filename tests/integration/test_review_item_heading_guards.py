"""Later item headings never leak into returned text as a successful extraction."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a


BODY = ("Competition may materially adversely affect our business, financial condition and results "
        "of operations. We could lose customers and our margins may decline. ") * 12
BODY = BODY.rstrip()


def document(html, filename="primary.htm", doc_type="10-K"):
    return DocumentInput(filename=filename, content=html.encode(), doc_type=doc_type,
                         source=SourceRef(reference=f"fixture://{filename}", origin="synthetic"))


def run(html, *exhibits):
    return extract_item1a(FilingInput(primary=document(html), exhibits=exhibits))


def section(following):
    return "<html><body><p>Item 1A. Risk Factors</p><p>" + BODY + "</p>" + following + "</body></html>"


@pytest.mark.parametrize("heading", [
    "Item 2. Description of Property", "Item 2. Property", "ITEM 2. DESCRIPTION OF PROPERTIES",
    "Item1B. Unresolved Staff Comments", "Item 1B. None.", "Item 1B. Not applicable.",
])
def test_item_title_variants_are_genuine_ends(heading):
    result = run(section(f"<p>{heading}</p><p>LEAK We lease an office.</p><p>ITEM 3. LEGAL PROCEEDINGS</p><p>None.</p>"))
    assert result.text_status is TextStatus.present_primary and result.text == BODY
    assert result.end_anchor_family == "E1"


@pytest.mark.parametrize("heading", [
    "Item 2. Facilities", "Items 1B and 2. Unresolved Staff Comments and Properties",
    "PART I - Item 2. Real Estate", "Item 3. Legal Proceedings",
])
def test_unrecognized_item_heading_before_accepted_end_abstains(heading):
    result = run(section(f"<p>{heading}</p><p>LEAK We lease an office.</p>"
                         "<p>ITEM 3. LEGAL PROCEEDINGS</p><p>None.</p>"
                         "<p>Executive Officers of the Registrant</p><p>Jane Doe.</p>"))
    assert result.text_status is TextStatus.boundary_unresolved and result.text is None
    assert "unrecognized_item_heading_before_end" in result.flags


@pytest.mark.parametrize("line", [
    "Item 7 of this report describes our liquidity.",
    "Item 7. Management's Discussion and Analysis (continued)",
])
def test_item_references_and_running_headers_inside_body_do_not_abstain(line):
    body = BODY + "\n" + line + "\n" + BODY
    html = ("<html><body><p>Item 1A. Risk Factors</p><p>" + BODY + "</p><p>" + line + "</p><p>" + BODY
            + "</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>")
    result = run(html)
    assert result.text_status is TextStatus.present_primary and result.text == body


def test_mdna_named_recovery_does_not_swallow_item_7a_and_item_8():
    stub = ("The information required by this Item is set forth under the caption \"Risk Factors\" "
            "in Item 7 of this report.")
    html = ("<html><body><p>Item 1. Business</p><p>We make widgets.</p><p>Item 1A. Risk Factors</p><p>" + stub
            + "</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p><p>Item 2. Properties</p><p>We lease.</p>"
            "<p>Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations</p>"
            "<p>Overview</p><p>We make widgets.</p><p>Risk Factors</p><p>" + BODY + "</p>"
            "<p>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</p><p>LEAK rates.</p>"
            "<p>Item 8. Financial Statements and Supplementary Data</p>"
            "<p>Report of Independent Registered Public Accounting Firm</p><p>To the board.</p></body></html>")
    result = run(html)
    assert result.text_status is TextStatus.stub_unresolved and result.text is None


@pytest.mark.parametrize("heading", [
    "Market for Common Equity", "Five-Year Selected Financial Data",
    "Market for Registrant's Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities",
    "Quantitative and Qualitative Disclosures About Market Risk",
])
def test_exhibit_annual_report_title_before_end_abstains(heading):
    primary = ("<html><body><p>Item 1A. Risk Factors</p><p>The information required by this Item is incorporated "
               "by reference to the section entitled \"Risk Factors\" in the 2023 Annual Report to Shareholders, "
               "filed as Exhibit 13.</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p>"
               "<p>Item 2. Properties</p><p>We lease.</p></body></html>")
    ex13 = ("<html><body><p>Letter to Shareholders</p><p>It was a good year.</p><p>RISK FACTORS</p><p>" + BODY
            + "</p><p>" + heading + "</p><p>LEAK Our stock trades on NASDAQ.</p>"
            "<p>Report of Independent Registered Public Accounting Firm</p><p>To the board.</p></body></html>")
    result = run(primary, document(ex13, filename="ex13.htm", doc_type="EX-13"))
    assert result.text is None or "LEAK" not in result.text
    assert result.text_status is not TextStatus.present_exhibit


def test_market_for_common_stock_risk_subhead_sentence_is_body():
    subhead = "Market for our common stock may be volatile and illiquid"
    body = BODY + "\n" + subhead + "\n" + BODY
    html = ("<html><body><p>Item 1A. Risk Factors</p><p>" + BODY + "</p><p>" + subhead + "</p><p>" + BODY
            + "</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>")
    result = run(html)
    assert result.text_status is TextStatus.present_primary and result.text == body
