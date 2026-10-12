"""Portable contracts: observed text, cover facts and derived decisions stay separate."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .documents import normalize_form


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_bytes="base64", val_json_bytes="base64")


class SourceRef(ContractModel):
    reference: str = Field(min_length=1)
    origin: Literal["caller_declared", "sec_archive", "synthetic"] = "caller_declared"


class DocumentInput(ContractModel):
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1, strict=True)
    source: SourceRef
    doc_type: str | None = None

    @property
    def sha256(self) -> str:
        return sha256(self.content).hexdigest()


class FilingInput(ContractModel):
    primary: DocumentInput
    form: str = Field(default="10-K", min_length=1)
    exhibits: tuple[DocumentInput, ...] = ()
    header: DocumentInput | None = None
    cik: int | None = Field(default=None, gt=0, strict=True)
    accession: str | None = Field(default=None, pattern=r"^\d{10}-\d{2}-\d{6}$")
    filing_date: date | None = None


class TextStatus(StrEnum):
    present_primary = "present_primary"
    present_recovered = "present_recovered"
    present_exhibit = "present_exhibit"
    present_cautionary_exhibit = "present_cautionary_exhibit"
    absent_statement = "absent_statement"
    stub_unresolved = "stub_unresolved"
    missing = "missing"
    not_found_in_amendment = "not_found_in_amendment"
    start_unresolved = "start_unresolved"
    boundary_unresolved = "boundary_unresolved"
    ambiguous = "ambiguous"
    unsupported_form = "unsupported_form"
    document_parse_unresolved = "document_parse_unresolved"


PRESENT_STATUSES = frozenset({TextStatus.present_primary, TextStatus.present_recovered,
                             TextStatus.present_exhibit, TextStatus.present_cautionary_exhibit})


class TextSpan(ContractModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    coordinate_space: Literal["projected_char"] = "projected_char"
    projection_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    projection_version: str = Field(min_length=1)
    normalization_version: str = Field(default="item1a-body-v1", min_length=1)

    @model_validator(mode="after")
    def ordered(self) -> TextSpan:
        if self.end <= self.start:
            raise ValueError("span must be nonempty and ordered")
        if not self.projection_version.strip() or not self.normalization_version.strip():
            raise ValueError("span version labels must not be blank")
        return self


class Item1AExtraction(ContractModel):
    text_status: TextStatus
    extraction_tier: str = "missing"
    text: str | None = None
    text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    quality_flag: str = "REVIEW"
    flags: tuple[str, ...] = ()
    evidence: str | None = None
    source_document: str = "primary"
    source_document_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source: SourceRef | None = None
    exhibit_type: str | None = None
    spans: tuple[TextSpan, ...] = ()
    projection_version: str | None = Field(default=None, min_length=1)
    projection_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    end_anchor_family: str | None = None
    end_anchor_position: int | None = Field(default=None, ge=0)
    decoding_codec: str | None = None
    decoding_strategy: str | None = None

    @model_validator(mode="after")
    def integrity(self) -> Item1AExtraction:
        if (self.text_status in PRESENT_STATUSES) != (self.text is not None):
            raise ValueError("text is required exactly for present statuses")
        if self.text is not None:
            if not self.text.strip():
                raise ValueError("present text must not be empty or whitespace-only")
            expected = sha256(self.text.encode("utf-8")).hexdigest()
            if self.text_sha256 is None:
                object.__setattr__(self, "text_sha256", expected)
            elif self.text_sha256 != expected:
                raise ValueError("text SHA-256 does not match")
        elif self.text_sha256 is not None or self.spans:
            raise ValueError("absent text cannot carry a text hash or spans")
        if self.flags != tuple(sorted(set(self.flags))):
            raise ValueError("flags must be sorted and unique")
        if self.projection_version is not None and not self.projection_version.strip():
            raise ValueError("projection version must not be blank")
        if self.spans:
            normalization_version = self.spans[0].normalization_version
            previous_end = -1
            for span in self.spans:
                if (span.projection_sha256 != self.projection_sha256
                        or span.projection_version != self.projection_version
                        or span.normalization_version != normalization_version):
                    raise ValueError("spans must share the result projection and normalization")
                if span.start < previous_end:
                    raise ValueError("spans must be in source order and non-overlapping")
                previous_end = span.end
            if self.end_anchor_position is not None and self.end_anchor_position < previous_end:
                raise ValueError("end anchor must not precede body spans")
        return self

    @property
    def present(self) -> bool:
        return self.text_status in PRESENT_STATUSES


class CoverFacts(ContractModel):
    """Reserved result vocabulary; no cover parser is exposed yet."""
    fiscal_year_end: date | None = None
    fiscal_year_end_state: Literal["not_recorded", "unique", "conflicting"] = "not_recorded"
    src_checkbox: bool | None = None
    entity_small_business: bool | None = None
    flags: tuple[str, ...] = ()


class ObligationDecision(ContractModel):
    """Separate derived decision vocabulary; no evaluator is exposed yet."""
    obligation: Literal["required", "exempt_src", "exempt_form", "not_in_force", "unknown"] = "unknown"
    basis_kind: Literal["screening"] = "screening"
    scope: str | None = None
    reasons: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


_ACCESSION_PATTERN = r"^\d{10}-\d{2}-\d{6}$"


class IndexRow(ContractModel):
    """One full-index entry; ``form`` keeps the index value, base/amendment are normalized."""
    cik: int = Field(gt=0, strict=True)
    company_name: str | None = None
    form: str = Field(min_length=1)
    base_form: str = Field(min_length=1)
    amendment: bool
    filing_date: date
    accession: str = Field(pattern=_ACCESSION_PATTERN)

    @model_validator(mode="after")
    def normalized(self) -> IndexRow:
        if normalize_form(self.form) != (self.base_form, self.amendment):
            raise ValueError("base_form/amendment must equal the normalized form")
        return self


class IndexSnapshot(ContractModel):
    """Parsed rows of one quarterly full index, bound to the hash of the supplied bytes.

    ``parse_skipped`` counts data lines that could not be represented faithfully.
    With ``cik_scope`` every line was parsed but only that CIK's rows were kept.
    """
    year: int = Field(ge=1993, strict=True)
    quarter: int = Field(ge=1, le=4, strict=True)
    source: SourceRef
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rows: tuple[IndexRow, ...]
    parse_skipped: int = Field(ge=0, strict=True)
    cik_scope: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def ordered_unique(self) -> IndexSnapshot:
        keys = [(row.accession, row.cik) for row in self.rows]
        if keys != sorted(set(keys)):
            raise ValueError("rows must be sorted and unique by (accession, cik)")
        if self.cik_scope is not None and any(row.cik != self.cik_scope for row in self.rows):
            raise ValueError("rows outside the snapshot's CIK scope")
        return self


class MissingQuarter(ContractModel):
    """A quarter whose full index was not obtained or not usable; ``reason`` is caller text."""
    year: int = Field(ge=1, strict=True)
    quarter: int = Field(ge=1, le=4, strict=True)
    reason: str = Field(min_length=1)


class IndexEvidence(ContractModel):
    """Index snapshots and known gaps, observed on one calendar day."""
    snapshots: tuple[IndexSnapshot, ...]
    missing: tuple[MissingQuarter, ...] = ()
    observed_on: date


class CoverageFinding(ContractModel):
    """Index-coverage observation for one CIK and filing-date window.

    This is an observation, never a legal exemption or obligation decision.
    ``no_annual_in_window`` means only that no annual-family filing appears in a
    complete set of quarterly index snapshots; it does not prove the absence of
    other SEC filings or of filings outside ``form_scope``.
    """
    status: Literal["annual_found", "no_annual_in_window", "foreign_form_observed", "unknown"] = "unknown"
    form_scope: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    cik: int | None = Field(default=None, gt=0, strict=True)
    start: date | None = None
    end: date | None = None
    filings: tuple[IndexRow, ...] = ()
    quarters_checked: tuple[str, ...] = ()
    observed_on: date | None = None

    @model_validator(mode="after")
    def consistent(self) -> CoverageFinding:
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("reasons must be sorted and unique")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("coverage window end precedes start")
        if self.status in {"annual_found", "foreign_form_observed"} and not self.filings:
            raise ValueError("a positive coverage status requires observed filings")
        return self
