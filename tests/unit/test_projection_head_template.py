"""Inert <template> content and an omitted </head> in the structured visibility projection."""

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a
from edgar_10k_item1a.projection import Document, visible_text

VERSION = "structured-visible-v4"
PARAGRAPH = ("Our business faces material risk. Adverse conditions may affect our financial "
             "condition and operations. Competition could reduce demand and harm our results.")
RISKS = " ".join([PARAGRAPH] * 24)
SECTION = f"<p>Item 1A. Risk Factors</p><p>{RISKS}</p>"
TAIL = "<p>Item 2. Properties</p><p>We own factories.</p>"
VISIBLE_SECTION = f"Item 1A. Risk Factors\n{RISKS}\nItem 2. Properties\nWe own factories."


def _extract(html):
    return extract_item1a(FilingInput(
        primary=DocumentInput(filename="synthetic.htm", content=html.encode("utf-8"), doc_type="10-K",
                              source=SourceRef(reference="fixture://synthetic-primary", origin="synthetic")),
        form="10-K",
    ))


def _assert_present_from_projection(html):
    result = _extract(html)
    projection = visible_text(html)
    assert result.text_status is TextStatus.present_primary
    assert result.text == RISKS
    assert result.projection_version == projection.version == VERSION
    assert result.projection_sha256 == projection.sha256
    assert result.spans
    assert "".join(projection.text[span.start:span.end] for span in result.spans).strip() == RISKS
    return projection


def test_item_1a_only_inside_template_is_missing():
    html = f"<html><body><p>Annual report cover.</p><template>{SECTION}</template>{TAIL}</body></html>"
    assert visible_text(html).text == "Annual report cover.\nItem 2. Properties\nWe own factories."
    result = _extract(html)
    assert result.text_status is TextStatus.missing
    assert result.text is None


def test_template_decoy_does_not_displace_visible_item_1a():
    decoy = f"<template><p>Item 1A. Risk Factors</p><p>TEMPLATE_SENTINEL {RISKS}</p>{TAIL}</template>"
    html = f"<html><body>{decoy}{SECTION}{TAIL}</body></html>"
    projection = _assert_present_from_projection(html)
    assert projection.text == VISIBLE_SECTION
    assert "TEMPLATE_SENTINEL" not in projection.text


@pytest.mark.parametrize("raw, expected", (
    ('<div><template><p>A</div><p>B</p></template></div><p>C</p>', "C"),
    ('<table><tr><td><template><tr><td>SECRET</td></tr></template>VISIBLE</td></tr></table>', "VISIBLE"),
    ('<p>A</p><template><p>SECRET</body></html>', "A"),
))
def test_template_content_cannot_close_elements_opened_outside_it(raw, expected):
    assert visible_text(raw).text == expected


def test_omitted_head_end_before_body_projects_body():
    html = f"<html><head><title>t</title><body>{SECTION}{TAIL}</body></html>"
    assert _assert_present_from_projection(html).text == VISIBLE_SECTION
    document = Document(html)
    head = next(node for node in document.nodes if node.tag == "head")
    body = next(node for node in document.nodes if node.tag == "body")
    assert document.source(head)["raw"] == "<head><title>t</title>"
    assert head.closed is False and body.parent is head.parent
    assert document.source(body)["raw"] == f"<body>{SECTION}{TAIL}</body>"
    assert all(document.source(node)["raw"] == html[node.start:node.end] for node in document.nodes)


def test_omitted_head_end_and_body_projects_first_non_head_content():
    html = f'<html><head><meta charset="utf-8">{SECTION}{TAIL}</html>'
    assert _assert_present_from_projection(html).text == VISIBLE_SECTION


@pytest.mark.parametrize("raw, expected", (
    ('<head><title>T</title><div>X</div>', "X"),
    ('<head><title>T</title><br>X', "X"),
    ('<html><head><base href="x"><link rel="x"><meta name="m"><style>S</style><script>J</script>'
     '<noscript>N</noscript><template>P</template><title>T</title><p>X</p></html>', "X"),
))
def test_non_head_start_tag_implicitly_ends_unclosed_head(raw, expected):
    assert visible_text(raw).text == expected


def test_explicit_head_end_keeps_previous_projection():
    html = ('<html><head><title>Annual Report</title><meta charset="utf-8"><style>p{color:red}</style>'
            'HEADTEXT<span>HEADSPAN</span><div>HEADDIV</div></head><body><p>Item 1A. Risk Factors</p>'
            '<div>Body <b>text</b></div><table><tr><td>A</td><td>B</td></tr></table></body></html>')
    projection = visible_text(html)
    assert projection.text == "Item 1A. Risk Factors\nBody text\nA\tB"
    assert projection.version == VERSION
