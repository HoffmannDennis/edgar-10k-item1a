import codecs
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import hashlib
import socket
import sys
import threading

import pytest

from edgar_10k_item1a.documents import ANNUAL_TEXT_FORMS, decode_document, normalize_form
from edgar_10k_item1a.projection import Document, Projection, legacy_wingdings_state, project, visible_text


@pytest.mark.parametrize("form, expected", (
    ("10K", ("10-K", False)), (" 10kt405 ", ("10-KT405", False)),
    ("10K405-A", ("10-K405", True)), ("10-K/A", ("10-K", True)),
    ("10KT/A", ("10-KT", True)), ("10-KT405-A", ("10-KT405", True)),
    ("10KSB40/A", ("10-KSB405", True)), ("20-F", ("20-F", False)),
    ("10-Q", ("10-Q", False)),
))
def test_normalize_form_preserves_base_and_amendment(form, expected):
    assert normalize_form(form) == expected
    assert ANNUAL_TEXT_FORMS == frozenset({"10-K", "10-K405", "10-KT", "10-KT405"})
    assert normalize_form("10-KSB")[0] not in ANNUAL_TEXT_FORMS


@pytest.mark.parametrize("form", ("", "  ", "/A", "-A"))
def test_empty_forms_rejected(form):
    with pytest.raises(ValueError):
        normalize_form(form)


def test_utf8_bom_source_hash_and_exact_roundtrip():
    content = codecs.BOM_UTF8 + '<html><meta charset="UTF-8"><p>Risks ☑</p></html>\r\n'.encode()
    decoded = decode_document(content)
    assert decoded.codec == "utf-8"
    assert decoded.strategy == "bom"
    assert decoded.flags == ()
    assert not decoded.text.startswith("\ufeff")
    assert codecs.BOM_UTF8 + decoded.text.encode(decoded.codec) == content
    assert decoded.source_sha256 == hashlib.sha256(content).hexdigest()
    with pytest.raises(FrozenInstanceError):
        decoded.text = "changed"


def test_inferred_cp1252_quotes_are_lossless_not_replacements():
    content = b"Item1A. Risk Factors\r\n\x93Quoted\x94 risk\x97may occur."
    decoded = decode_document(content)
    assert decoded.codec == "cp1252"
    assert decoded.strategy == "cp1252_fallback"
    assert decoded.flags == ("decoding_inferred",)
    assert decoded.text == "Item1A. Risk Factors\r\n“Quoted” risk—may occur."
    assert decoded.text.encode(decoded.codec) == content
    assert decoded.source_sha256 == hashlib.sha256(content).hexdigest()
    projection = visible_text(decoded.text)
    assert projection.text == "Item 1A. Risk Factors\n“Quoted” risk—may occur."
    assert projection.sha256 == hashlib.sha256(projection.text.encode()).hexdigest()


@pytest.mark.parametrize("content, codec, flags", (
    (b'<meta charset="windows-1252"><p>\x93risk\x94</p>', "cp1252", ()),
    (b'<meta http-equiv="Content-Type" content="text/html; charset=ISO-8859-1"><p>\x81</p>', "iso-8859-1",
     ("decoding_c1_controls",)),
    (b"<?xml version='1.0' encoding='latin1'?><html><p>\x81</p></html>", "iso-8859-1", ("decoding_c1_controls",)),
    (b'<meta charset="utf8"><meta charset="UTF-8"><p>risk</p>', "utf-8", ()),
))
def test_supported_declarations_and_aliases(content, codec, flags):
    decoded = decode_document(content)
    assert decoded.codec == codec
    assert decoded.strategy == "declared_charset"
    assert decoded.flags == flags
    assert decoded.text.encode(codec) == content
    assert decoded.source_sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("content", (
    b'<meta charset="utf-8"><meta charset="windows-1252">',
    b'<meta charset="utf-8" charset="latin1">',
    b'<meta charset="utf-8"><meta http-equiv="content-type" content="text/html; charset=latin1">',
    b"<?xml encoding='utf-8'?><meta charset='cp1252'>",
    codecs.BOM_UTF8 + b'<meta charset="cp1252">',
    b'<meta charset="utf-16">', b'<meta charset="unknown-encoding">',
    b'<meta charset>', b'<meta charset="">',
    b'<meta charset="utf-8"><p>\x93risk\x94</p>',
    b'<meta charset="cp1252"><p>\x81</p>', b"\x81",
    codecs.BOM_UTF16_LE + b"a\x00", codecs.BOM_UTF32_BE + b"\x00\x00\x00a",
))
def test_unresolved_decoding_never_uses_lossy_or_latin1_catchall(content):
    with pytest.raises(ValueError, match="^decoding_unresolved"):
        decode_document(content)


def test_declarations_are_not_taken_from_comments_scripts_or_plain_prose():
    content = (b"<!-- <meta charset='utf-16'> -->"
               b"<script>const example = \"<meta charset='latin1'>\";</script>"
               b"<p>charset=unknown encoding=utf-16</p>\x93risk\x94")
    assert decode_document(content).codec == "cp1252"
    assert decode_document(b'charset="utf-16" in prose').codec == "utf-8"


def test_lossless_roundtrip_is_enforced():
    class CorruptDecode(bytes):
        def decode(self, encoding="utf-8", errors="strict"):
            if encoding == "utf-8":
                return "not the original"
            return super().decode(encoding, errors)

    with pytest.raises(ValueError, match="^decoding_unresolved: byte roundtrip failed"):
        decode_document(CorruptDecode(b"original"))


def test_source_hashes_distinguish_bom_and_newline_bytes_before_projection():
    variants = (b"Item1A. Risks\r\nBody", b"Item1A. Risks\nBody",
                codecs.BOM_UTF8 + b"Item1A. Risks\nBody")
    decoded = [decode_document(content) for content in variants]
    assert len({document.source_sha256 for document in decoded}) == 3
    assert [document.source_sha256 for document in decoded] == [hashlib.sha256(content).hexdigest()
                                                                for content in variants]
    projected = [visible_text(document.text) for document in decoded]
    assert len({projection.sha256 for projection in projected}) == 1


def test_full_document_decoding_has_no_probe_cap():
    content = b" " * 400001 + b"\x93General Instruction J\x94"
    decoded = decode_document(content)
    assert decoded.codec == "cp1252"
    assert decoded.text.endswith("“General Instruction J”")
    assert decoded.text.encode(decoded.codec) == content
    with pytest.raises(ValueError, match="^decoding_unresolved"):
        decode_document(b" " * 400001 + b'<meta charset="utf-16">')


def test_visible_projection_matches_structured_marks_and_table_semantics():
    raw = ('<html><head>NO</head><body><p>A <b>bold</b> word</p>'
           '<div hidden>SECRET</div><table><tr><td>Left</td><td>Right</td></tr>'
           '<tr><td>&#9746;</td><td><input type="checkbox"></td></tr></table>'
           '<script>BAD</script><div>After</div></body></html>')
    projection = project(raw)
    assert projection.text == "A bold word\nLeft\tRight\n[CHECKED]\t[UNCHECKED]\nAfter"
    assert projection.version == "structured-visible-v4"
    assert projection.sha256 == hashlib.sha256(projection.text.encode()).hexdigest()
    with pytest.raises(FrozenInstanceError):
        projection.text = "changed"


@pytest.mark.parametrize("hidden", (
    "head", "script", "style", "noscript", "ix:header", "ix:hidden", "xbrli:context", "xbrli:unit",
    "div hidden", 'div aria-hidden="TRUE"', 'div style="display: none"',
    'div style="visibility: collapse"', 'div style="visibility:hidden"',
))
def test_hidden_subtrees_do_not_leak(hidden):
    tag = hidden.split()[0]
    assert project(f'<html><{hidden}>HIDDEN<span>MARK☑</span></{tag}><p>Visible</p></html>').text == "Visible"


@pytest.mark.parametrize("raw", (
    '<html hidden><body>Secret</body></html>',
    '<div hidden><html><body>Secret</body></html></div>',
    '<div style="display:none"><html><body>Secret</body></html></div>',
))
def test_hidden_html_root_inherits_ancestor_visibility(raw):
    assert project(raw).text == ""


def test_multiple_html_roots_rejected_including_hidden_roots():
    with pytest.raises(ValueError, match="one primary HTML"):
        visible_text('<html>First</html><div hidden><html>Second</html></div>')


def test_inherited_wingdings_and_unicode_marks_and_bare_attributes():
    raw = ('<html><font face="Wingdings"><span>&#120;</span><b>&#168;</b></font>'
           '<span>&#120;</span><font face style>plain</font> ☐ ☒ ☑ '
           '<input type="checkbox" checked><input type="checkbox"></html>')
    assert project(raw).text == "[CHECKED][UNCHECKED]xplain [UNCHECKED] [CHECKED] [CHECKED] [CHECKED][UNCHECKED]"
    document = Document(raw)
    legacy_nodes = [node for node in document.nodes if document.source(node)["raw"] == "&#120;"]
    assert [legacy_wingdings_state(document, node) for node in legacy_nodes] == [True, None]


def test_omitted_cells_and_nested_blocks_preserve_tabs():
    assert project('<html><body><table><tr><td>Left<td>Right</table></body></html>').text == "Left\tRight"
    assert project('<table><tr><td> <p>Left</p> </td><td> Right </td></tr></table>').text == "Left\tRight"
    assert project('<table><tr><td> <div> </div> </td><td> Right </td></tr></table>').text == "Right"


def test_split_spans_and_glued_item_labels_are_normalized_before_hash():
    raw = ('<html><p><span>Item</span><span>1</span><span>A.</span> '
           '<span>Risk Factors</span></p><p>(Leading body punctuation.)</p></html>')
    projection = visible_text(raw)
    assert projection.text == "Item 1A. Risk Factors\n(Leading body punctuation.)"
    assert projection.sha256 == hashlib.sha256(projection.text.encode()).hexdigest()
    assert projection.sha256 != hashlib.sha256(projection.text.replace("Item 1A", "Item1A").encode()).hexdigest()


def test_plain_text_is_not_html_merely_because_it_contains_arbitrary_tags():
    raw = "<DOCUMENT>\r\n<TYPE>10-K\r\nItem1A. Risk Factors\r\nLosses < estimates >.\u00a0\r\n<RISK>retained</RISK>"
    expected = "<DOCUMENT>\n<TYPE>10-K\nItem 1A. Risk Factors\nLosses < estimates >. \n<RISK>retained</RISK>"
    assert visible_text(raw) == Projection(expected)
    assert visible_text("<!-- <html>only an example</html> -->").text == "<!-- <html>only an example</html> -->"
    assert visible_text("A < B and C > D\nInline Item1A stays.").text == "A < B and C > D\nInline Item1A stays."


def test_internal_page_furniture_and_risk_text_are_retained():
    raw = ('<html><p>First risk paragraph.</p><p>12</p><p>Table of Contents</p>'
           '<header>RISK FACTORS (continued)</header><p>Last risk paragraph.</p><p>13</p></html>')
    assert project(raw).text == "First risk paragraph.\n12\nTable of Contents\nRISK FACTORS (continued)\nLast risk paragraph.\n13"


def test_source_char_spans_for_entities_and_unclosed_nodes():
    raw = "<html>\r\n<p>A &amp; &#120; &nbsp end"
    document = Document(raw)
    assert document.root.plain() == "\r\nA & x \u00a0 end"
    for node in document.nodes:
        assert document.source(node)["raw"] == raw[node.start:node.end]
    refs = [node for node in document.nodes if node.tag == "#text" and node.text in {"&", "x", "\u00a0"}]
    assert [document.source(node)["raw"] for node in refs] == ["&amp;", "&#120;", "&nbsp"]
    assert document.nodes[0].end == len(raw)
    assert document.nodes[0].closed is False


def test_deep_unclosed_legacy_html_and_plain_traversal_are_iterative():
    raw = "<html><body>" + "<p>" * 6000 + "Item1A. Risk Factors\nRisk body."
    assert project(raw).text == "Item 1A. Risk Factors Risk body."
    assert Document(raw).root.plain() == "Item1A. Risk Factors\nRisk body."


def test_concurrent_projection_does_not_change_interpreter_settings(monkeypatch):
    recursion, stack = sys.getrecursionlimit(), threading.stack_size()

    def forbidden(*args, **kwargs):
        raise AssertionError("projection must not change interpreter settings")

    monkeypatch.setattr(sys, "setrecursionlimit", forbidden)
    original_stack_size = threading.stack_size

    def checked_stack_size(*args):
        if args:
            forbidden()
        return original_stack_size()

    monkeypatch.setattr(threading, "stack_size", checked_stack_size)
    raw = "<html>" + "<p>" * 2500 + "Item1A. Risk Factors"
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(visible_text, [raw] * 8))
    assert all(output == Projection("Item 1A. Risk Factors") for output in outputs)
    assert sys.getrecursionlimit() == recursion
    assert threading.stack_size() == stack


def test_projection_never_loads_external_resources_or_executes_scripts(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external resource access forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    raw = ('<html><head><link href="https://invalid.example/style.css"></head>'
           '<body><img src="https://invalid.example/image.png">'
           '<script>throw new Error("must not run")</script><p>Risk text.</p></body></html>')
    assert visible_text(raw).text == "Risk text."


def test_reviewer_nested_hidden_table_keeps_outer_cell_source_and_visibility():
    nested = '<table><tr><td>SECRET</td></tr></table>'
    outer_cell = f'<td hidden>{nested}</td>'
    raw = f'<table><tr>{outer_cell}<td>VISIBLE</td></tr></table>'
    assert visible_text(raw).text == "VISIBLE"
    document = Document(raw)
    hidden_cell = next(node for node in document.nodes if node.tag == "td" and "hidden" in node.attrs)
    assert document.source(hidden_cell)["raw"] == outer_cell
    secret = next(node for node in document.nodes if node.text == "SECRET")
    assert not document.visible(secret)


@pytest.mark.parametrize("nested", (
    '<table><tr><td>SECRET<td>MORE<tr><td>LAST</table>',
    '<table><td>SECRET<td>MORE</table>',
    '<table><tr><th>SECRET<th>MORE</table>',
    '<table><tr><td><table><tr><td>SECRET</table></table>',
))
def test_nested_omitted_cells_and_rows_never_close_outer_hidden_row(nested):
    outer_row = f'<tr hidden><td>{nested}</td></tr>'
    raw = f'<table>{outer_row}<tr><td>VISIBLE</td></tr></table>'
    assert visible_text(raw).text == "VISIBLE"
    document = Document(raw)
    hidden_row = next(node for node in document.nodes if node.tag == "tr" and "hidden" in node.attrs)
    assert document.source(hidden_row)["raw"] == outer_row


def test_nested_table_preserves_visible_omitted_cell_and_row_boundaries():
    raw = ('<table><tr><td>Outer<table><tr><td>Left<td>Right<tr><td>Next</table>'
           '<td>After<tr><td>Last</table>')
    assert visible_text(raw).text == "Outer\nLeft\tRight\nNext\tAfter\nLast"


def test_reviewer_preformatted_item_headings_keep_line_anchors():
    text = "Item 1A. Risk Factors\nRisk body.\nItem 2. Properties"
    assert visible_text(f'<pre>{text}</pre>').text == text


def test_preformatted_whitespace_is_inherited_through_inline_nodes_and_entities():
    raw = ('<pre>Item1A. Risk Factors\r\n<span>  Risk\tbody.\r\n\r\n'
           '<b>Item 2.</b> Properties</span>\n&nbsp;☑</pre>')
    expected = "Item 1A. Risk Factors\n  Risk\tbody.\n\nItem 2. Properties\n [CHECKED]"
    assert visible_text(raw).text == expected


def test_preformatted_leading_trailing_blank_lines_are_not_interior_furniture_cleanup():
    assert visible_text('<pre>\n  Risk text.\n\n</pre>').text == "\n  Risk text.\n\n"
    assert visible_text('<p>Normal   spaces.</p><pre>A\n\n  B</pre><p>After.</p>').text == "Normal spaces.\nA\n\n  B\nAfter."


@pytest.mark.parametrize("raw, expected", (
    ('<pre>A<br><br>B</pre>', "A\n\nB"),
    ('<pre><br>A</pre>', "\nA"),
    ('<pre>A<br></pre>', "A\n"),
))
def test_preformatted_generated_breaks_keep_boundaries(raw, expected):
    projection = visible_text(raw)
    assert projection.text == expected
    assert projection.sha256 == hashlib.sha256(expected.encode("utf-8")).hexdigest()


def test_preformatted_generated_breaks_are_inherited_through_inline_descendants():
    raw = '<pre><span><br>A<b><br><br></b>B<br></span></pre>'
    projection = visible_text(raw)
    assert projection.text == "\nA\n\nB\n"
    assert projection.sha256 == hashlib.sha256(b"\nA\n\nB\n").hexdigest()


@pytest.mark.parametrize("raw, expected", (
    ('<pre>A\n<br>B</pre>', "A\n\nB"),
    ('<pre>A<br>\nB</pre>', "A\n\nB"),
    ('<pre>\n<br>A<br>\n</pre>', "\n\nA\n\n"),
    ('<pre><span>A\r\n<br/></span><b>\nB<br></b></pre>', "A\n\n\nB\n"),
))
def test_preformatted_literal_and_generated_breaks_are_not_collapsed(raw, expected):
    projection = visible_text(raw)
    assert projection.text == expected
    assert projection.sha256 == hashlib.sha256(expected.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("raw, expected", (
    ('<div>A<br><br>B</div>', "A\nB"),
    ('<div><br>A</div>', "A"),
    ('<div>A<br></div>', "A"),
))
def test_generated_break_cleanup_outside_preformatted_context_is_unchanged(raw, expected):
    assert visible_text(raw).text == expected


def test_deep_inherited_preformatted_rendering_is_still_iterative():
    raw = '<pre>' + '<span>' * 2500 + 'Item1A. Risk Factors\nRisk body.\nItem 2. Properties'
    assert visible_text(raw).text == "Item 1A. Risk Factors\nRisk body.\nItem 2. Properties"


@pytest.mark.parametrize("prefix", ("", '<meta charset="utf-8">'))
def test_reviewer_embedded_xml_encoding_never_changes_document_codec(prefix):
    raw = (f'<html><head>{prefix}</head><body><p>Café</p>'
           '<?xml version="1.0" encoding="windows-1252"?></body></html>')
    content = raw.encode("utf-8")
    decoded = decode_document(content)
    assert decoded.codec == "utf-8"
    assert decoded.text == raw
    assert visible_text(decoded.text).text == "Café"
    assert decoded.source_sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize("prefix", (b"", b" \r\n\t", codecs.BOM_UTF8))
def test_leading_xml_prolog_is_honored_after_bom_or_leading_whitespace(prefix):
    content = prefix + b'<?xml version="1.0" encoding="utf-8"?><html><p>Caf\xc3\xa9</p></html>'
    decoded = decode_document(content)
    assert decoded.codec == "utf-8"
    assert decoded.strategy == ("bom" if prefix == codecs.BOM_UTF8 else "declared_charset")
    assert visible_text(decoded.text).text == "Café"


@pytest.mark.parametrize("prefix", ('<!-- before -->', '<!DOCTYPE html>', '<?other?>', '<html>'))
def test_nonleading_unsupported_xml_instruction_is_ignored(prefix):
    content = f'{prefix}<?xml version="1.0" encoding="utf-16"?><p>Café</p>'.encode()
    assert decode_document(content).codec == "utf-8"


def test_xml_prolog_does_not_limit_full_document_meta_declaration_scan():
    content = (b'<?xml version="1.0" encoding="utf-8"?>' + b' ' * 400001
               + b'<meta charset="cp1252">')
    with pytest.raises(ValueError, match="^decoding_unresolved"):
        decode_document(content)


@pytest.mark.parametrize("style", (
    '--display:none', "font-family:'display:none'", '--visibility:hidden',
    "font-family:'display:none;visibility:hidden';color:red",
    '--tokens:"display:none;visibility:hidden";display:block',
    '--tokens:{display:none;visibility:hidden};display:block',
    'background:url(data:text/plain;display:none);color:red',
    '/* display:none;visibility:hidden */ color:red',
    'display:none-like;visibility:hidden-ish', 'display:"none";visibility:"hidden"',
    'display:none;display:block', 'visibility:hidden;visibility:visible',
    'display:block!important;display:none', 'visibility:visible!important;visibility:hidden',
    'display:none;display:block!important', 'visibility:hidden;visibility:visible ! important',
    r'font-family:"quoted\";display:none";display:block',
))
def test_style_substrings_quotes_comments_and_overridden_values_do_not_hide(style):
    raw = f"<div style='{style.replace(chr(39), '&apos;')}'>Visible risk text.</div>"
    assert visible_text(raw).text == "Visible risk text."


@pytest.mark.parametrize("style", (
    'DISPLAY : none', 'visibility : hidden', 'visibility:collapse',
    'color:red;display:none;', 'display:block;display:none',
    'visibility:visible;visibility:hidden', 'display:none;display:invalid',
    'display:none!important;display:block', 'visibility:hidden !important;visibility:visible',
    'display:block!important;display:none!important',
    'display:/* comment;visibility:visible */none',
    '/* display:block; */display:none',
    'font-family:"quoted;display:block";display:none',
    '--custom:"semicolon;display:block";display:none',
))
def test_supported_inline_style_hiding_and_importance_are_explicit(style):
    raw = f"<div style='{style}'>Hidden risk text.</div><p>VISIBLE</p>"
    assert visible_text(raw).text == "VISIBLE"
