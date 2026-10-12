"""Parent-owned public-entrypoint checks independent of the extraction writer."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import socket
import sys
import threading

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, extract_item1a
from edgar_10k_item1a.models import Item1AExtraction, TextStatus
from edgar_10k_item1a.projection import visible_text
from edgar_10k_item1a.documents import decode_document


BODY = "(Our business may face material adverse financial risks.) " * 65
BODY = BODY.rstrip()
PAYLOAD = (
    "<html><head><script>DO_NOT_INCLUDE_HIDDEN_TEXT</script></head><body>"
    "<h1><span>Item</span><span>1A.</span> Risk Factors</h1>"
    f"<p>{BODY}</p><h1>Item 2. Properties</h1>"
    "<p>PROPERTY_ONLY_SENTINEL remains outside risk factors. "
    "Later prose refers to Item 1B. Unresolved Staff Comments.</p></body></html>"
).encode()


def filing(payload=PAYLOAD, form="10-K"):
    return FilingInput(form=form, primary=DocumentInput(
        filename="synthetic_annual.htm", content=payload, doc_type=form,
        source=SourceRef(reference="fixture://parent-oracle", origin="synthetic")))


def test_parent_oracle_earliest_end_exact_body_and_hashes():
    result = extract_item1a(filing())
    assert result.text_status == TextStatus.present_primary
    assert result.text == BODY
    assert "PROPERTY_ONLY_SENTINEL" not in result.text
    assert "DO_NOT_INCLUDE_HIDDEN_TEXT" not in result.text
    assert result.source_document_sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert result.text_sha256 == hashlib.sha256(BODY.encode()).hexdigest()
    assert result.end_anchor_family == "E1"
    projection = visible_text(decode_document(PAYLOAD).text)
    assert projection.text[result.end_anchor_position:].startswith("Item 2.")
    assert result.spans
    slices = [projection.text[span.start:span.end] for span in result.spans]
    assert "\n".join(slices).strip() == result.text
    assert all(span.projection_sha256 == projection.sha256 for span in result.spans)
    assert Item1AExtraction.model_validate_json(result.model_dump_json()) == result


def test_parent_oracle_no_supported_end_is_not_success():
    payload = f"Item 1A. Risk Factors\n{BODY}\nItem 7. Management discussion\nOther text".encode()
    result = extract_item1a(filing(payload))
    assert result.text_status == TextStatus.boundary_unresolved
    assert result.text is None
    assert "no_supported_end_anchor" in result.flags


def test_concurrent_public_calls_do_not_mutate_global_settings(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline extraction attempted a network connection")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    before = (sys.getrecursionlimit(), threading.stack_size())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: extract_item1a(filing()), range(12)))
    assert all(result.text == BODY for result in results)
    assert (sys.getrecursionlimit(), threading.stack_size()) == before
    assert "polars" not in sys.modules


def test_parent_toc_with_page_on_own_line_is_not_the_body():
    payload = ("Table of Contents\nItem 1A. Risk Factors\n13\nItem 2. Properties\n21\n"
               f"Item 1A. Risk Factors\n{BODY}\nItem 2. Properties\nPROPERTY_ONLY_SENTINEL").encode()
    result = extract_item1a(filing(payload))
    assert result.text_status == TextStatus.present_primary
    assert result.text == BODY


def test_parent_multiple_html_roots_are_a_visible_parse_abstention():
    result = extract_item1a(filing(b"<html><body>first</body></html><html><body>second</body></html>"))
    assert result.text_status == TextStatus.document_parse_unresolved
    assert result.text is None and result.flags
