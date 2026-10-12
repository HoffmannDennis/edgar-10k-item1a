"""Charset declarations: ASCII labels, head-only meta scope and C1 control signalling."""

import hashlib

import pytest

from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, TextStatus, extract_item1a
from edgar_10k_item1a.documents import decode_document


RISKS = " ".join(["Our business risks could materially and adversely affect our financial condition "
                  "and operations. Adverse market conditions may reduce demand and harm our results."] * 30)
SECTION = "<p>Item 1A. Risk Factors</p><p>" + RISKS + "</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p>"


def document(content, filename="primary.htm", doc_type="10-K"):
    return DocumentInput(filename=filename, content=content, doc_type=doc_type,
                         source=SourceRef(reference=f"fixture://{filename}", origin="synthetic"))


def html(head, body=SECTION):
    return f"<html><head>{head}</head><body>{body}</body></html>".encode("ascii")


@pytest.mark.parametrize("content", (
    b'<meta http-equiv="Content-Type" content="text/html; charset=us-ascii"><p>risk</p>',
    b'<meta charset="ASCII"><p>risk</p>',
    b"<?xml version='1.0' encoding='ASCII'?><html><p>risk</p></html>",
    b'<?xml version="1.0" encoding="US-ASCII"?><html><head><meta charset="utf-8"></head><p>risk</p></html>',
    b'<meta charset="us-ascii"><meta charset="windows-1252"><p>risk</p>',
))
def test_ascii_declarations_decode_strictly_as_ascii(content):
    decoded = decode_document(content)
    assert (decoded.codec, decoded.strategy, decoded.flags) == ("ascii", "declared_charset", ())
    assert decoded.text.encode("ascii") == content
    assert decoded.source_sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("content", (
    b'<meta charset="us-ascii"><p>\x93risk\x94</p>',
    b'<meta charset="us-ascii"><meta charset="utf-8"><p>Caf\xc3\xa9</p>',
    b'<meta charset="us-ascii"><meta charset="utf-8"><meta charset="windows-1252"><p>risk</p>',
    b'\xef\xbb\xbf<meta charset="us-ascii"><p>risk</p>',
))
def test_ascii_declaration_failures_and_conflicts_still_abstain(content):
    with pytest.raises(ValueError, match="^decoding_unresolved"):
        decode_document(content)


def test_us_ascii_primary_is_extracted_end_to_end():
    result = extract_item1a(FilingInput(primary=document(html(
        '<meta http-equiv="Content-Type" content="text/html; charset=us-ascii">'))))
    assert result.text_status is TextStatus.present_primary and result.text == RISKS
    assert (result.decoding_codec, result.decoding_strategy) == ("ascii", "declared_charset")


@pytest.mark.parametrize("body_meta", (
    "<meta charset='koi8-r'>", "<meta charset='utf-16'>", '<meta charset="windows-1252">',
    '<meta http-equiv="Content-Type" content="text/html; charset=unknown-encoding">',
))
def test_meta_declarations_after_head_are_not_document_encodings(body_meta):
    content = html('<meta charset="utf-8">', SECTION + "<p>Sample markup:</p>" + body_meta)
    decoded = decode_document(content)
    assert (decoded.codec, decoded.strategy) == ("utf-8", "declared_charset")
    result = extract_item1a(FilingInput(primary=document(content)))
    assert result.text_status is TextStatus.present_primary and result.text == RISKS
    assert result.decoding_codec == "utf-8"


@pytest.mark.parametrize("content, codec, strategy", (
    (b'<html><meta charset="windows-1252"><p>\x93risk\x94</p></html>', "cp1252", "declared_charset"),
    (b'<meta charset="windows-1252"><p>\x93risk\x94</p>', "cp1252", "declared_charset"),
    (b'<html><head></head><meta charset="windows-1252"><p>\x93risk\x94</p></html>', "cp1252", "cp1252_fallback"),
    (b'<html><head><title>t</title></head><body><meta charset="koi8-r"><p>risk</p></body></html>',
     "utf-8", "utf8_strict"),
))
def test_head_scope_ends_only_at_head_close_or_body_start(content, codec, strategy):
    decoded = decode_document(content)
    assert (decoded.codec, decoded.strategy) == (codec, strategy)
    assert decoded.text.encode(codec) == content


def test_head_close_ends_scope_even_without_body_tag():
    content = b'<html><head><meta charset="utf-8"></head><p>risk</p><meta charset="koi8-r"></html>'
    assert decode_document(content).codec == "utf-8"


@pytest.mark.parametrize("content, codec", (
    (b'<meta charset="iso-8859-1"><p>The Company\x92s risk</p>', "iso-8859-1"),
    (b"<p>The Company\xc2\x92s risk</p>", "utf-8"),
))
def test_c1_control_characters_are_flagged(content, codec):
    decoded = decode_document(content)
    assert decoded.codec == codec
    assert "\x92" in decoded.text
    assert decoded.flags == ("decoding_c1_controls",)


def test_cp1252_fallback_without_c1_controls_keeps_only_inference_flag():
    decoded = decode_document(b"<p>The Company\x92s risk</p>")
    assert (decoded.codec, decoded.flags) == ("cp1252", ("decoding_inferred",))
    assert decoded.text == "<p>The Company’s risk</p>"


def test_c1_flag_reaches_primary_result():
    content = html('<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">').replace(
        b"Our business", b"The Company\x92s business", 1)
    result = extract_item1a(FilingInput(primary=document(content)))
    assert result.text_status is TextStatus.present_primary and "\x92" in result.text
    assert result.decoding_codec == "iso-8859-1"
    assert result.flags == ("decoding_c1_controls",)


def test_c1_flag_reaches_exhibit_result():
    primary = html('<meta charset="utf-8">', "<p>Item 1. Business</p><p>We make widgets.</p>")
    exhibit = html('<meta charset="iso-8859-1">', SECTION).replace(b"Our business", b"The Company\x92s business", 1)
    result = extract_item1a(FilingInput(primary=document(primary), exhibits=(
        document(exhibit, filename="ex99.htm", doc_type="EX-99.1"),)))
    assert result.text_status is TextStatus.present_exhibit and "\x92" in result.text
    assert result.decoding_codec == "iso-8859-1"
    assert "decoding_c1_controls" in result.flags
    assert result.flags == tuple(sorted(set(result.flags)))
