"""Pure SEC Archives index helpers: full-index parsing, coverage and filing index pages.

Nothing here performs a network request. Callers supply bytes obtained elsewhere
together with explicit source metadata; URL helpers only build strings.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import parse_qs, urljoin, urlsplit

from pydantic import Field, model_validator

from ..documents import decode_document, normalize_form
from ..models import (
    ContractModel, CoverageFinding, IndexEvidence, IndexRow, IndexSnapshot, SourceRef,
)

__all__ = [
    "ANNUAL_FAMILY_FORMS", "COVERAGE_FORM_SCOPE", "FOREIGN_ANNUAL_FORMS", "FilingIndex",
    "IndexDocument", "IndexPageError", "MasterIndexError", "evaluate_coverage",
    "filing_folder_url", "filing_index_url", "full_index_url", "hdr_sgml_url",
    "parse_filing_index", "parse_master_idx", "quarter_complete", "quarters_between",
]

SEC_BASE = "https://www.sec.gov"
_SEC_HOST = "www.sec.gov"
FULL_INDEX_BASE = f"{SEC_BASE}/Archives/edgar/full-index"
FIRST_FULL_INDEX_YEAR = 1993

#: Annual reports counted as positive annual-family evidence (originals only).
ANNUAL_FAMILY_FORMS = frozenset({"10-K", "10-K405", "10-KT", "10-KT405", "10-KSB", "10-KSB405"})
#: Foreign-issuer annual reports: index observation only, never Item 1A text.
FOREIGN_ANNUAL_FORMS = frozenset({"20-F", "40-F"})
#: Normalized forms (originals and amendments) inspected by :func:`evaluate_coverage`.
COVERAGE_FORM_SCOPE = tuple(sorted(
    form for base in ANNUAL_FAMILY_FORMS | FOREIGN_ANNUAL_FORMS for form in (base, f"{base}/A")
))

_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_ACCESSION_TOKEN = re.compile(r"(?<!\d)\d{10}-\d{2}-\d{6}(?!\d)")


class MasterIndexError(ValueError):
    """The payload is not a parseable full-index ``master.idx`` file."""


class IndexPageError(ValueError):
    """A filing index page cannot be decoded or does not describe the requested filing."""


# ------------------------------------------------------------------ validation
def _require_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int")
    return value


def _require_date(value: object, name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{name} must be a date, not a datetime")
    return value


def _check_quarter(year: object, quarter: object, *, min_year: int = 1) -> tuple[int, int]:
    year, quarter = _require_int(year, "year"), _require_int(quarter, "quarter")
    if quarter not in (1, 2, 3, 4) or year < min_year:
        raise ValueError(f"invalid year/quarter {year}Q{quarter}")
    return year, quarter


def _quarter_label(year: int, quarter: int) -> str:
    return f"{year:04d}Q{quarter}"


def _quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def _quarter_end(year: int, quarter: int) -> date:
    month = 3 * quarter
    return date(year, month, 31 if month in (3, 12) else 30)


def _normal_accession(accession: object) -> str:
    if not isinstance(accession, str) or not _ACCESSION.fullmatch(accession):
        raise ValueError(f"accession must look like NNNNNNNNNN-NN-NNNNNN, got {accession!r}")
    return accession.replace("-", "")


def _check_cik(cik: object) -> int:
    if _require_int(cik, "cik") <= 0:
        raise ValueError("cik must be positive")
    return cik


# ------------------------------------------------------------------ full index
def full_index_url(year: int, quarter: int) -> str:
    """URL of one quarterly ``master.idx``; no upper bound is imposed on the year."""
    year, quarter = _check_quarter(year, quarter, min_year=FIRST_FULL_INDEX_YEAR)
    return f"{FULL_INDEX_BASE}/{year}/QTR{quarter}/master.idx"


def quarters_between(start: date, end: date) -> list[tuple[int, int]]:
    """Calendar quarters overlapping the inclusive date window, in order."""
    start, end = _require_date(start, "start"), _require_date(end, "end")
    if end < start:
        raise ValueError("end precedes start")
    out: list[tuple[int, int]] = []
    year, quarter = _quarter_of(start)
    last = _quarter_of(end)
    while (year, quarter) <= last:
        out.append((year, quarter))
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return out


def quarter_complete(year: int, quarter: int, *, observed_on: date) -> bool:
    """True when the quarter's last calendar day lies strictly before ``observed_on``."""
    year, quarter = _check_quarter(year, quarter)
    return _quarter_end(year, quarter) < _require_date(observed_on, "observed_on")


_SEPARATOR = re.compile(r"^-{10,}\s*$")
_CIK_FIELD = re.compile(r"\d{1,10}")
_FILENAME = re.compile(r"edgar/data/(\d{1,10})/(\d{10}-\d{2}-\d{6})\.txt")


def _parse_index_date(text: str) -> date:
    value = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    if re.fullmatch(r"\d{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:]))
    raise ValueError(f"unparseable date {text!r}")


def _parse_index_line(line: str) -> IndexRow:
    parts = line.split("|")
    if len(parts) < 5:
        raise ValueError("expected at least 5 fields")
    if not _CIK_FIELD.fullmatch(parts[0]):
        raise ValueError("CIK column is not a decimal number")
    cik = int(parts[0])
    match = _FILENAME.fullmatch(parts[-1].strip())
    if match is None or int(match.group(1)) != cik:
        raise ValueError("filename does not bind the CIK column to an accession")
    form = parts[-3].strip()
    base_form, amendment = normalize_form(form)
    return IndexRow(cik=cik, company_name="|".join(parts[1:-3]).strip() or None, form=form,
                    base_form=base_form, amendment=amendment,
                    filing_date=_parse_index_date(parts[-2]), accession=match.group(2))


def parse_master_idx(payload: bytes, *, source: SourceRef, year: int, quarter: int,
                     cik: int | None = None) -> IndexSnapshot:
    """Parse one quarterly ``master.idx`` (``CIK|Company Name|Form Type|Date Filed|Filename``).

    Bytes are decoded as Latin-1, which is lossless for every byte; lines split
    only on LF (an optional CR is removed). Data starts after the first dashed
    separator line; without one the payload is rejected. Company names may
    contain ``|``. A malformed data line, a filename CIK differing from the CIK
    column, or a later row repeating an (accession, CIK) key with different
    content is skipped and counted in ``parse_skipped``; identical repeats are
    dropped silently. Rows are sorted by (accession, CIK). With ``cik`` every line
    is still parsed and counted, but only that CIK's rows are kept (``cik_scope``),
    so a quarter of several hundred thousand rows need not stay in memory.
    """
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    year, quarter = _check_quarter(year, quarter, min_year=FIRST_FULL_INDEX_YEAR)
    scope = None if cik is None else _check_cik(cik)
    lines = [line.removesuffix("\r") for line in payload.decode("latin-1").split("\n")]
    start = next((i + 1 for i, line in enumerate(lines) if _SEPARATOR.match(line)), None)
    if start is None:
        raise MasterIndexError("no dashed separator line: not a master.idx payload")
    rows: dict[tuple[str, int], IndexRow] = {}
    skipped = 0
    for line in lines[start:]:
        if not line.strip():
            continue
        try:
            row = _parse_index_line(line)
        except ValueError:
            skipped += 1
            continue
        if scope is not None and row.cik != scope:
            continue
        key = (row.accession, row.cik)
        previous = rows.setdefault(key, row)
        if previous != row:
            skipped += 1
    return IndexSnapshot(year=year, quarter=quarter, source=source,
                         source_sha256=sha256(payload).hexdigest(),
                         rows=tuple(rows[key] for key in sorted(rows)), parse_skipped=skipped,
                         cik_scope=scope)


# ------------------------------------------------------------------ coverage
def evaluate_coverage(evidence: IndexEvidence, *, cik: int, start: date, end: date) -> CoverageFinding:
    """Summarize annual-family index observations for one CIK and filing-date window.

    ``annual_found`` needs one original annual-family row and stands even when
    the evidence has gaps. Otherwise any 20-F/40-F row gives
    ``foreign_form_observed``. ``no_annual_in_window`` requires every overlapping
    quarter to have exactly one distinct snapshot (identical duplicates allowed),
    no skipped lines, no recorded missing quarter, each quarter complete as of
    ``observed_on`` and the window end before ``observed_on``; anything else is
    ``unknown``. Gap reasons are reported for every status. The finding is an
    index observation, never a legal exemption or proof that no SEC filing exists.
    """
    if not isinstance(evidence, IndexEvidence):
        raise TypeError("evidence must be IndexEvidence")
    cik = _check_cik(cik)
    window = quarters_between(start, end)
    observed_on = evidence.observed_on
    by_quarter: dict[tuple[int, int], list[IndexSnapshot]] = defaultdict(list)
    reasons: set[str] = set()
    usable = []
    for snapshot in evidence.snapshots:
        if snapshot.cik_scope not in (None, cik):  # another CIK's rows were discarded
            reasons.add(f"snapshot_out_of_scope:{_quarter_label(snapshot.year, snapshot.quarter)}")
            continue
        usable.append(snapshot)
        by_quarter[(snapshot.year, snapshot.quarter)].append(snapshot)
    missing = {(item.year, item.quarter) for item in evidence.missing}

    checked: list[str] = []
    for year, quarter in window:
        label = _quarter_label(year, quarter)
        snapshots = by_quarter.get((year, quarter), [])
        if snapshots:
            checked.append(label)
        if not snapshots or (year, quarter) in missing:
            reasons.add(f"quarter_missing:{label}")
        if len({snapshot.source_sha256 for snapshot in snapshots}) > 1:
            reasons.add(f"conflicting_snapshots:{label}")
        if any(snapshot.parse_skipped for snapshot in snapshots):
            reasons.add(f"parse_skipped:{label}")
        if not quarter_complete(year, quarter, observed_on=observed_on):
            reasons.add(f"quarter_incomplete:{label}")
    if end >= observed_on:
        reasons.add("window_not_elapsed")

    scope = ANNUAL_FAMILY_FORMS | FOREIGN_ANNUAL_FORMS
    filings = sorted(
        {row for snapshot in usable for row in snapshot.rows
         if row.cik == cik and start <= row.filing_date <= end and row.base_form in scope},
        key=lambda row: (row.filing_date, row.accession, row.form, row.company_name or ""),
    )
    annual = [row for row in filings if row.base_form in ANNUAL_FAMILY_FORMS]
    status: Literal["annual_found", "no_annual_in_window", "foreign_form_observed", "unknown"]
    if any(not row.amendment for row in annual):
        status = "annual_found"
    elif any(row.base_form in FOREIGN_ANNUAL_FORMS for row in filings):
        status = "foreign_form_observed"
    elif not reasons:
        status = "no_annual_in_window"
    else:
        status = "unknown"
    if annual and all(row.amendment for row in annual):
        reasons.add("amendment_only_observed")
    return CoverageFinding(status=status, form_scope=COVERAGE_FORM_SCOPE, reasons=tuple(sorted(reasons)),
                           cik=cik, start=start, end=end, filings=tuple(filings),
                           quarters_checked=tuple(checked), observed_on=observed_on)


# ------------------------------------------------------------------ filing index page
def filing_folder_url(cik: int, accession: str) -> str:
    """``https://www.sec.gov/Archives/edgar/data/{cik}/{accession without dashes}/``."""
    return f"{SEC_BASE}/Archives/edgar/data/{_check_cik(cik)}/{_normal_accession(accession)}/"


def filing_index_url(cik: int, accession: str) -> str:
    """Filing index page ``.../{accession}-index.html``."""
    return f"{filing_folder_url(cik, accession)}{accession}-index.html"


def hdr_sgml_url(cik: int, accession: str) -> str:
    """Submission header ``.../{accession}.hdr.sgml``."""
    return f"{filing_folder_url(cik, accession)}{accession}.hdr.sgml"


DOCUMENT_TABLE = "Document Format Files"
_EXHIBIT_TYPE = re.compile(r"^EX-(13|99)(?![0-9])", re.I)
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
_TEXT_SUFFIX = re.compile(r"\.(?:html?|txt)$", re.I)
_ENTITY = re.compile(r'<span class="companyName">(.*?)</span>', re.S | re.I)
_ROLE = re.compile(r"\((Filer|Subject|Filed by|Reporting|Issuer|Depositor|Securitizer)\)", re.I)
_ENTITY_CIK = re.compile(r"CIK=(\d{1,10})(?!\d)")
_FORM_NAME = re.compile(r'<div id="formName">\s*<strong>\s*Form\s+([^<]+?)\s*</strong>', re.I)
_TITLE = re.compile(r"<title(?:\s[^>]*)?>(.*?)</title>", re.S | re.I)
_SEC_NUM = re.compile(r'<div id="secNum">(.*?)</div>', re.S | re.I)


class IndexDocument(ContractModel):
    """One linked document row of a filing index page."""
    table: str
    sequence: int | None = None
    description: str
    filename: str = Field(min_length=1)
    url: str = Field(min_length=1)
    doc_type: str
    size_bytes: int | None = None


class FilingIndex(ContractModel):
    """Documents of one filing index page; ``primary`` is set only for ``type_match``."""
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    cik: int = Field(gt=0, strict=True)
    index_url: str
    page_form: str | None = None
    filing_date: str | None = None
    period_of_report: str | None = None
    entities: tuple[tuple[int, str], ...] = ()
    documents: tuple[IndexDocument, ...] = ()
    primary: IndexDocument | None = None
    primary_status: Literal["type_match", "ambiguous", "missing"]
    exhibits: tuple[IndexDocument, ...] = ()
    complete_submission_url: str | None = None
    rejected_links: tuple[str, ...] = ()

    @model_validator(mode="after")
    def primary_matches_status(self) -> FilingIndex:
        if (self.primary is not None) != (self.primary_status == "type_match"):
            raise ValueError("primary is set exactly for type_match")
        return self


class _Cell:
    __slots__ = ("hrefs", "text")

    def __init__(self) -> None:
        self.text: list[str] = []
        self.hrefs: list[str] = []


class _IndexRows(HTMLParser):
    """Collect ``<tr>`` cells of every table, tagged with the table ``summary``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, list[_Cell]]] = []
        self._tables: list[str] = []
        self._row: list[_Cell] | None = None
        self._cell: _Cell | None = None

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None:
            self._row.append(self._cell)
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            self.rows.append((self._tables[-1] if self._tables else "", self._row))
        self._row = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._close_row()
            self._tables.append(dict(attrs).get("summary") or "")
        elif tag == "tr":
            self._close_row()
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._close_cell()
            self._cell = _Cell()
        elif tag == "a" and self._cell is not None:
            href = dict(attrs).get("href")
            if href:
                self._cell.hrefs.append(href)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table":
            self._close_row()
            if self._tables:
                self._tables.pop()


def _clean(parts: list[str]) -> str:
    return " ".join(" ".join(parts).replace("\xa0", " ").split())


def _to_int(value: str) -> int | None:
    text = value.replace(",", "").strip()
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def _sec_path(url: str) -> str:
    """Path of an https www.sec.gov URL without credentials, port, query or fragment."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != _SEC_HOST or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError(f"not a plain https www.sec.gov URL: {url!r}")
    return parsed.path


def _document_url(page_url: str, href: str) -> str:
    """Absolute document URL; unwraps an SEC ``/ix?doc=`` viewer link with one relative doc."""
    parsed = urlsplit(urljoin(page_url, href.strip()))
    if parsed.path == "/ix" and parsed.query:
        _sec_path(parsed._replace(query="").geturl())
        values = parse_qs(parsed.query, keep_blank_values=True)
        docs = values.get("doc", [])
        if set(values) != {"doc"} or len(docs) != 1:
            raise ValueError("viewer link must contain exactly one doc parameter")
        document = urlsplit(docs[0])
        if document.scheme or document.netloc or document.query or document.fragment \
                or not document.path.startswith("/"):
            raise ValueError("viewer doc must be a query-free root-relative path")
        return SEC_BASE + document.path
    return SEC_BASE + _sec_path(parsed.geturl())


def _bind_document_url(url: str, accession: str, allowed_ciks: set[int]) -> str:
    """Require ``/Archives/edgar/data/<allowed cik>/<acc no dashes>/<safe filename>``."""
    parts = _sec_path(url).split("/")
    if (len(parts) != 7 or parts[:4] != ["", "Archives", "edgar", "data"]
            or not re.fullmatch(r"[0-9]{1,10}", parts[4]) or int(parts[4]) not in allowed_ciks
            or parts[5] != _normal_accession(accession) or not _SAFE_FILENAME.fullmatch(parts[6])):
        raise ValueError(f"document URL not bound to accession {accession}: {url}")
    return url


def _info(text: str, head: str) -> str | None:
    match = re.search(r'<div class="infoHead">\s*' + re.escape(head)
                      + r'\s*</div>\s*<div class="info">\s*([^<]*?)\s*</div>', text, re.I)
    return match.group(1) if match else None


def _entities(text: str) -> tuple[tuple[int, str], ...]:
    entities: list[tuple[int, str]] = []
    for block in _ENTITY.findall(text):
        cik_match = _ENTITY_CIK.search(block)
        if cik_match is None or int(cik_match.group(1)) <= 0:
            continue
        role = _ROLE.search(block)
        entities.append((int(cik_match.group(1)), role.group(1).title() if role else "Unknown"))
    return tuple(entities)


def _normalized_or_none(form: str | None) -> tuple[str, bool] | None:
    try:
        return normalize_form(form) if form else None
    except ValueError:
        return None


def parse_filing_index(html: bytes, *, cik: int, accession: str, page_url: str | None = None,
                       form: str | None = None) -> FilingIndex:
    """Parse a filing ``{accession}-index.html`` page into documents, primary and exhibits.

    The page must state the requested accession in its title or accession block
    and must not name another one there. Document links are accepted only as
    https www.sec.gov ``/Archives/edgar/data/<cik>/<accession>/<file>`` URLs whose
    CIK is the requested one or an entity listed on the page; other links are
    returned in ``rejected_links``. The primary is the unique text document
    (.htm/.html/.txt) in ``Document Format Files`` whose type equals the page
    form (or ``form`` when given) after normalization; several give
    ``ambiguous`` and none ``missing``. List order never selects a primary.
    """
    if not isinstance(html, bytes):
        raise TypeError("html must be bytes")
    cik = _check_cik(cik)
    _normal_accession(accession)
    canonical_url = filing_index_url(cik, accession)
    if page_url is not None:
        _sec_path(page_url)
    url = page_url or canonical_url
    try:
        text = decode_document(html).text
    except ValueError as exc:
        raise IndexPageError(f"index page is not decodable: {exc}") from exc

    identity = [match.group(1) for match in (_TITLE.search(text), _SEC_NUM.search(text)) if match]
    stated = {token for block in identity for token in _ACCESSION_TOKEN.findall(block)}
    if stated - {accession}:
        raise IndexPageError(f"index page names another accession: {sorted(stated - {accession})}")
    if accession not in stated:
        raise IndexPageError(f"index page does not state accession {accession}")

    page_form_match = _FORM_NAME.search(text)
    page_form = page_form_match.group(1).strip() if page_form_match else None
    wanted = _normalized_or_none(page_form)
    if form is not None:
        requested = normalize_form(form)
        if wanted is not None and wanted != requested:
            raise IndexPageError(f"index page form {page_form!r} differs from requested {form!r}")
        wanted = requested

    entities = _entities(text)
    allowed = {cik} | {entity_cik for entity_cik, _ in entities}
    parser = _IndexRows()
    parser.feed(text)
    parser.close()
    documents: list[IndexDocument] = []
    rejected: list[str] = []
    complete_submission: str | None = None
    for table, row in parser.rows:
        if len(row) < 5 or not row[2].hrefs:
            continue
        href = row[2].hrefs[0]
        try:
            doc_url = _bind_document_url(_document_url(url, href), accession, allowed)
        except ValueError:
            rejected.append(href)
            continue
        description = _clean(row[1].text)
        if description.lower() == "complete submission text file":
            complete_submission = complete_submission or doc_url
            continue
        documents.append(IndexDocument(
            table=table, sequence=_to_int(_clean(row[0].text)), description=description,
            filename=PurePosixPath(urlsplit(doc_url).path).name, url=doc_url,
            doc_type=_clean(row[3].text), size_bytes=_to_int(_clean(row[4].text)),
        ))

    candidates = {doc.url: doc for doc in documents
                  if wanted is not None and doc.table == DOCUMENT_TABLE
                  and _TEXT_SUFFIX.search(doc.filename) and _normalized_or_none(doc.doc_type) == wanted}
    primary = next(iter(candidates.values())) if len(candidates) == 1 else None
    status: Literal["type_match", "ambiguous", "missing"] = (
        "type_match" if primary else "ambiguous" if candidates else "missing")
    exhibits = sorted((doc for doc in documents if _EXHIBIT_TYPE.match(doc.doc_type)),
                      key=lambda doc: (doc.sequence is None, doc.sequence or 0, doc.filename))
    return FilingIndex(
        accession=accession, cik=cik, index_url=url, page_form=page_form,
        filing_date=_info(text, "Filing Date"), period_of_report=_info(text, "Period of Report"),
        entities=entities, documents=tuple(documents), primary=primary, primary_status=status,
        exhibits=tuple(exhibits), complete_submission_url=complete_submission,
        rejected_links=tuple(rejected),
    )
