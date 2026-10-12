import hashlib
from datetime import date

import pytest
from pydantic import ValidationError

from edgar_10k_item1a.models import (
    CoverFacts, CoverageFinding, DocumentInput, FilingInput, Item1AExtraction,
    ObligationDecision, SourceRef, TextSpan, TextStatus,
)


def test_bytes_roundtrip_and_observation_origin():
    doc = DocumentInput(filename="synthetic.htm", content=b"\xff\x00example",
                        source=SourceRef(reference="fixture://example", origin="synthetic"))
    filing = FilingInput(primary=doc, form="10-K/A")
    assert FilingInput.model_validate_json(filing.model_dump_json()) == filing
    assert doc.sha256 == hashlib.sha256(doc.content).hexdigest()


@pytest.mark.parametrize("kwargs", ({"cik": 0}, {"accession": "bad"}, {"src_checkbox": False}))
def test_invalid_inputs_and_attestation_shortcuts_rejected(kwargs):
    doc = DocumentInput(filename="x", content=b"x", source=SourceRef(reference="fixture://x"))
    with pytest.raises(ValidationError):
        FilingInput(primary=doc, **kwargs)


def test_distinct_unobserved_domains():
    assert CoverFacts().src_checkbox is None
    assert ObligationDecision().obligation == "unknown"
    assert CoverageFinding().status == "unknown"


def test_result_hash_warnings_and_none_roundtrip():
    result = Item1AExtraction(text_status=TextStatus.present_primary, text="Risk may be material.",
                             flags=("decoding_inferred",), quality_flag="REVIEW_LENGTH")
    assert Item1AExtraction.model_validate_json(result.model_dump_json()) == result
    assert result.text_sha256 == hashlib.sha256(result.text.encode()).hexdigest()
    with pytest.raises(ValidationError):
        Item1AExtraction(text_status=TextStatus.missing, text="risk")
    with pytest.raises(ValidationError):
        Item1AExtraction(text_status=TextStatus.present_primary, text="risk", text_sha256="wrong")


def test_flags_and_spans_are_checked():
    with pytest.raises(ValidationError):
        Item1AExtraction(text_status=TextStatus.missing, flags=("z", "a"))
    with pytest.raises(ValidationError):
        TextSpan(start=3, end=2, projection_sha256="a" * 64, projection_version="v1")


def test_dates_none_and_frozen_inputs_roundtrip():
    doc = DocumentInput(filename="synthetic.htm", content=b"test",
                        source=SourceRef(reference="fixture://dates", origin="synthetic"))
    filing = FilingInput(primary=doc, filing_date=date(2026, 10, 11),
                         cik=123, accession="0000000123-26-000001")
    restored = FilingInput.model_validate_json(filing.model_dump_json())
    assert restored == filing and restored.filing_date == date(2026, 10, 11)
    assert restored.header is None and restored.exhibits == ()
    with pytest.raises(ValidationError):
        filing.form = "10-Q"
    cover = CoverFacts(fiscal_year_end=date(2025, 12, 31), fiscal_year_end_state="unique",
                       flags=("period_source_unverified",))
    assert CoverFacts.model_validate_json(cover.model_dump_json()) == cover
    assert cover.src_checkbox is None


@pytest.mark.parametrize("content", ("risk", bytearray(b"risk")))
def test_python_document_inputs_must_be_actual_bytes(content):
    with pytest.raises(ValidationError):
        DocumentInput(filename="x", content=content, source=SourceRef(reference="fixture://x"))


@pytest.mark.parametrize("cik", (True, False, "123", 123.0))
def test_cik_has_no_boolean_or_numeric_coercion(cik):
    doc = DocumentInput(filename="x", content=b"risk", source=SourceRef(reference="fixture://x"))
    with pytest.raises(ValidationError):
        FilingInput(primary=doc, cik=cik)


@pytest.mark.parametrize("text", ("", " \n\t"))
def test_present_status_requires_nonblank_text(text):
    with pytest.raises(ValidationError):
        Item1AExtraction(text_status=TextStatus.present_primary, text=text)


@pytest.mark.parametrize("field", ("source_document_sha256", "projection_sha256", "text_sha256"))
def test_output_provenance_hashes_are_well_formed(field):
    with pytest.raises(ValidationError):
        Item1AExtraction(text_status=TextStatus.missing, **{field: "not-a-hash"})


def test_output_span_bundle_consistency():
    def span(start, end, **kwargs):
        return TextSpan(start=start, end=end, projection_sha256=kwargs.get("hash", "a" * 64),
                        projection_version=kwargs.get("version", "v1"),
                        normalization_version=kwargs.get("normalization", "item1a-body-v1"))
    defaults = dict(text_status=TextStatus.present_primary, text="risk",
                    projection_sha256="a" * 64, projection_version="v1")
    invalid = (
        {"spans": (span(0, 4, hash="b" * 64),)},
        {"spans": (span(0, 4, version="v2"),)},
        {"spans": (span(5, 9), span(0, 4))},
        {"spans": (span(0, 4), span(3, 8))},
        {"spans": (span(0, 4), span(5, 9, normalization="other"))},
        {"spans": (span(0, 4),), "end_anchor_position": 0},
    )
    for fields in invalid:
        with pytest.raises(ValidationError):
            Item1AExtraction(**defaults, **fields)
    result = Item1AExtraction(**defaults, spans=(span(0, 4),), end_anchor_position=4)
    assert Item1AExtraction.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field", ("projection_version", "normalization_version"))
@pytest.mark.parametrize("value", ("", " "))
def test_span_versions_are_not_blank(field, value):
    fields = dict(start=0, end=4, projection_sha256="a" * 64,
                  projection_version="v1", normalization_version="item1a-body-v1")
    fields[field] = value
    with pytest.raises(ValidationError):
        TextSpan(**fields)
