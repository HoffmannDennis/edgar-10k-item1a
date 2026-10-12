"""EDGAR ASCII SGML markup (<PAGE>, <TABLE>, <S>, <C>, ...) is not HTML evidence."""

import re

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a
from edgar_10k_item1a.projection import visible_text


_PARAGRAPH = (
    "Our business faces material risk. Adverse conditions may affect our\n"
    "financial condition and operations. Competition could reduce demand\n"
    "and harm our results."
)
_TABLE = (
    "<TABLE>\n<CAPTION>\n                         Customer concentration\n"
    "<S>                      <C>\nLargest customer          24%\n</TABLE>"
)
_RISKS = "\n\n".join([_PARAGRAPH] * 12 + [_TABLE] + [_PARAGRAPH] * 12 + ["<PAGE>", "12"] + [_PARAGRAPH] * 4)
_ASCII_10K = (
    "ANNUAL REPORT\n\nPART I\n\nITEM 1A.  RISK FACTORS\n\n" + _RISKS
    + "\n\nITEM 1B.  UNRESOLVED STAFF COMMENTS\n\nNone.\n\nITEM 2.  PROPERTIES\n\n"
    "<TABLE>\n<CAPTION>\n<S>                <C>\nLocation           Sq. ft.\n</TABLE>\n"
)


def _filing(text):
    return FilingInput(primary=DocumentInput(
        filename="synthetic.txt", content=text.encode("utf-8"), doc_type="10-K",
        source=SourceRef(reference="fixture://ascii-primary", origin="synthetic")))


def _assert_reconstructed(result, text):
    projection = visible_text(text)
    furniture = re.compile(
        r"(?i)\A[\s\xa0]*(?:table\s+of\s+contents|part\s+i{1,2}\b.{0,40}"
        r"|-{0,2}\s*\d{1,4}\s*-{0,2}|page\s+\d{1,4})[\s\xa0]*\Z"
    )
    slices = []
    for span in result.spans:
        assert span.coordinate_space == "projected_char"
        assert span.projection_version == result.projection_version == projection.version
        assert span.projection_sha256 == result.projection_sha256 == projection.sha256
        lines = projection.text[span.start:span.end].split("\n")
        while lines and (not lines[-1].strip() or furniture.fullmatch(lines[-1])):
            lines.pop()
        slices.append("\n".join(lines).rstrip())
    assert result.spans and "".join(slices) == result.text


def test_ascii_filing_with_sgml_table_markup_stays_plain_text():
    projection = visible_text(_ASCII_10K)
    assert projection.text == _ASCII_10K
    assert projection.text.count("\n") == _ASCII_10K.count("\n")


def test_ascii_filing_with_sgml_table_markup_extracts_wrapped_section():
    result = extract_item1a(_filing(_ASCII_10K))
    assert result.text_status is TextStatus.present_primary
    assert result.text == _RISKS
    assert result.end_anchor_family == "E1"
    _assert_reconstructed(result, _ASCII_10K)


def test_uppercase_html_table_rows_still_project_as_html():
    raw = "<TABLE><TR><TD>Left</TD><TD>Right</TD></TR></TABLE>\nAfter <B>bold</B>"
    assert visible_text(raw).text == "Left\tRight\nAfter bold"


def test_lowercase_html_with_table_caption_and_paragraph_still_projects_as_html():
    raw = "<table><caption>Cap</caption></table><p>One\ntwo</p>"
    assert visible_text(raw).text == "Cap\nOne two"
