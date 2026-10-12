"""Explicit SEC filing cache: fetch accession-bound documents once, extract offline.

Layout: ``<cache>/<cik>/<accession without dashes>/`` holds the filing index page,
the SEC header, the primary document, any fetched EX-13/EX-99 exhibits and
``manifest.json``. The manifest records each stored file's role, SEC-declared
type, source URL and SHA-256; it has no timestamps and never the User-Agent.
Loading verifies every hash and the accession binding and never fetches.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import Field, ValidationError

from ..extraction.exhibits import EXHIBIT_ROUTE_STATUSES
from ..extraction.item1a import extract_item1a
from ..models import ContractModel, DocumentInput, FilingInput, Item1AExtraction, SourceRef
from .client import SecHTTPError
from .index import filing_index_url, hdr_sgml_url, parse_filing_index

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = "edgar-10k-item1a-cache-v1"
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
_SHA256 = r"^[a-f0-9]{64}$"


class CacheError(ValueError):
    """The cache cannot supply a verified filing; nothing is guessed or re-fetched."""


class CachedDocument(ContractModel):
    role: Literal["index", "header", "primary", "exhibit"]
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
    url: str
    sha256: str = Field(pattern=_SHA256)
    n_bytes: int = Field(ge=0)
    doc_type: str | None = None
    sequence: int | None = None


class FilingManifest(ContractModel):
    manifest_version: Literal["edgar-10k-item1a-cache-v1"] = MANIFEST_VERSION
    cik: int = Field(gt=0)
    accession: str = Field(pattern=r"^\d{10}-\d{2}-\d{6}$")
    form: str | None = None
    filing_date: str | None = None
    period_of_report: str | None = None
    primary_status: str
    listed_exhibits: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    documents: tuple[CachedDocument, ...] = ()


class FetchReceipt(ContractModel):
    cik: int
    accession: str
    primary_status: str
    manifest_sha256: str = Field(pattern=_SHA256)
    documents: tuple[CachedDocument, ...]
    fetched: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    http_requests: int = 0


class _Response(Protocol):
    content: bytes
    sha256: str
    final_url: str


class _Client(Protocol):
    n_http_requests: int

    def get(self, url: str) -> _Response: ...


def _check_identity(cik: int, accession: str) -> None:
    if not isinstance(cik, int) or isinstance(cik, bool) or cik <= 0:
        raise ValueError("cik must be a positive integer")
    if not _ACCESSION.fullmatch(accession):
        raise ValueError("accession must look like 0000320193-23-000106")


def filing_dir(cache: Path, cik: int, accession: str) -> Path:
    _check_identity(cik, accession)
    return Path(cache) / str(cik) / accession.replace("-", "")


def write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        with partial.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _stored_file(directory: Path, filename: str) -> Path:
    if not _SAFE_FILENAME.fullmatch(filename) or filename == MANIFEST_NAME:
        raise CacheError(f"unsafe cached filename: {filename!r}")
    path = directory / filename
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise CacheError(f"cached file escapes the filing directory: {filename}")
    return path


def _bound(url: str, accession: str, filename: str) -> bool:
    """``https://www.sec.gov/Archives/edgar/data/<cik>/<accession digits>/<filename>`` only."""
    parsed = urlsplit(url)
    parts = PurePosixPath(parsed.path).parts
    return (parsed.scheme == "https" and parsed.hostname == "www.sec.gov" and parsed.port is None
            and not (parsed.query or parsed.fragment or parsed.username) and len(parts) == 7
            and parts[1:4] == ("Archives", "edgar", "data") and parts[4].isdigit()
            and parts[5] == accession.replace("-", "") and parts[6] == filename)


def read_manifest(cache: Path, *, cik: int, accession: str) -> FilingManifest:
    directory = filing_dir(cache, cik, accession)
    path = directory / MANIFEST_NAME
    if path.is_symlink() or not path.is_file():
        raise CacheError(f"no cached manifest for {cik}/{accession}")
    try:
        manifest = FilingManifest.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise CacheError(f"invalid cache manifest for {accession}") from exc
    if (manifest.cik, manifest.accession) != (cik, accession):
        raise CacheError("cache manifest belongs to another filing")
    return manifest


def _verified(directory: Path, document: CachedDocument, accession: str) -> bytes:
    if not _bound(document.url, accession, document.filename):
        raise CacheError(f"cached URL is not bound to accession {accession}: {document.filename}")
    path = _stored_file(directory, document.filename)
    if not path.is_file():
        raise CacheError(f"cached file missing: {document.filename}")
    content = path.read_bytes()
    if sha256(content).hexdigest() != document.sha256 or len(content) != document.n_bytes:
        raise CacheError(f"cached file changed after fetch: {document.filename}")
    return content


def fetch_filing(client: _Client, *, cik: int, accession: str, cache: Path,
                 exhibits: bool = False) -> FetchReceipt:
    """Fetch the index page, SEC header, primary document and optionally EX-13/EX-99.

    Only documents listed on the accession's own index page are requested; already
    cached files whose recorded hash still matches are reused without a request.
    A response is stored only when its final URL is the requested URL.
    """
    directory = filing_dir(cache, cik, accession)
    try:
        previous = {document.filename: document
                    for document in read_manifest(cache, cik=cik, accession=accession).documents}
    except CacheError:
        previous = {}
    start_requests = client.n_http_requests
    fetched: list[str] = []
    reused: list[str] = []
    stored: list[CachedDocument] = []

    def obtain(role: str, url: str, filename: str, doc_type: str | None = None,
               sequence: int | None = None) -> bytes:
        path = _stored_file(directory, filename)
        old = previous.get(filename)
        if old is not None and old.url == url and old.role == role:
            try:
                content = _verified(directory, old, accession)
            except CacheError:
                content = None
            if content is not None:
                reused.append(filename)
                stored.append(old.model_copy(update={"doc_type": doc_type, "sequence": sequence}))
                return content
        response = client.get(url)
        if response.final_url != url:  # a redirect may not attribute other bytes to this document
            raise CacheError(f"redirected away from the requested document: {filename}")
        content = response.content
        write_atomic(path, content)
        fetched.append(filename)
        stored.append(CachedDocument(role=role, filename=filename, url=url, sha256=sha256(content).hexdigest(),
                                     n_bytes=len(content), doc_type=doc_type, sequence=sequence))
        return content

    index_url = filing_index_url(cik, accession)
    page = parse_filing_index(obtain("index", index_url, PurePosixPath(urlsplit(index_url).path).name),
                              cik=cik, accession=accession, page_url=index_url)
    unavailable: list[str] = []
    header_url = hdr_sgml_url(cik, accession)
    try:
        obtain("header", header_url, PurePosixPath(urlsplit(header_url).path).name, "SEC-HEADER")
    except SecHTTPError as exc:
        if exc.http_status != 404:
            raise
        unavailable.append("header:http_404")
    if page.primary is not None:
        obtain("primary", page.primary.url, page.primary.filename, page.primary.doc_type, page.primary.sequence)
    if exhibits:
        for document in page.exhibits:
            obtain("exhibit", document.url, document.filename, document.doc_type, document.sequence)
    else:
        # A plain refetch keeps exhibits cached earlier while the page still lists them unchanged.
        held = {entry.filename for entry in stored}
        for document in page.exhibits:
            old = previous.get(document.filename)
            if document.filename not in held and old is not None and old.role == "exhibit" and old.url == document.url:
                obtain("exhibit", document.url, document.filename, document.doc_type, document.sequence)
    manifest = FilingManifest(
        cik=cik, accession=accession, form=page.page_form, filing_date=page.filing_date,
        period_of_report=page.period_of_report, primary_status=page.primary_status,
        listed_exhibits=tuple(document.filename for document in page.exhibits),
        unavailable=tuple(unavailable), documents=tuple(sorted(stored, key=lambda d: (d.role, d.filename))),
    )
    payload = (json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=2) + "\n").encode()
    write_atomic(directory / MANIFEST_NAME, payload)
    return FetchReceipt(cik=cik, accession=accession, primary_status=page.primary_status,
                        manifest_sha256=sha256(payload).hexdigest(), documents=manifest.documents,
                        fetched=tuple(fetched), reused=tuple(reused), unavailable=manifest.unavailable,
                        http_requests=client.n_http_requests - start_requests)


def _check_against_index(manifest: FilingManifest, contents: dict[str, bytes]) -> None:
    """Form, primary and exhibit types must be those of the verified index page."""
    indexes = [entry for entry in manifest.documents if entry.role == "index"]
    if len(indexes) != 1:
        raise CacheError("cache holds no unique index page")
    page = parse_filing_index(contents[indexes[0].filename], cik=manifest.cik, accession=manifest.accession,
                              page_url=indexes[0].url)
    listed = {document.filename: document for document in (*page.exhibits, *((page.primary,) if page.primary else ()))}
    observed = (page.page_form, page.primary_status, page.filing_date, page.period_of_report,
                tuple(document.filename for document in page.exhibits))
    recorded = (manifest.form, manifest.primary_status, manifest.filing_date, manifest.period_of_report,
                manifest.listed_exhibits)
    if observed != recorded:
        raise CacheError("cache manifest disagrees with the verified index page")
    for entry in manifest.documents:
        if entry.role not in {"primary", "exhibit"}:
            continue
        document = listed.get(entry.filename)
        primary = page.primary is not None and entry.filename == page.primary.filename
        if (document is None or (entry.role == "primary") != primary
                or (entry.url, entry.doc_type, entry.sequence) != (document.url, document.doc_type, document.sequence)):
            raise CacheError(f"cached {entry.role} disagrees with the verified index page: {entry.filename}")


def load_cached_filing(cache: Path, *, cik: int, accession: str) -> FilingInput:
    """Offline: rebuild a FilingInput from verified cached bytes and SEC-declared types.

    Every manifest entry, including the index page, is hash-verified; form, primary
    and exhibit types must agree with the re-parsed index page.
    """
    manifest = read_manifest(cache, cik=cik, accession=accession)
    directory = filing_dir(cache, cik, accession)
    contents = {entry.filename: _verified(directory, entry, accession) for entry in manifest.documents}
    _check_against_index(manifest, contents)

    def document(entry: CachedDocument) -> DocumentInput:
        return DocumentInput(filename=entry.filename, content=contents[entry.filename],
                             doc_type=entry.doc_type, source=SourceRef(reference=entry.url, origin="sec_archive"))

    primaries = [entry for entry in manifest.documents if entry.role == "primary"]
    if len(primaries) != 1:
        raise CacheError(f"primary_unresolved:{manifest.primary_status}")
    if manifest.form is None:
        raise CacheError("index page form unobserved")
    headers = [entry for entry in manifest.documents if entry.role == "header"]
    try:
        filed = date.fromisoformat(manifest.filing_date) if manifest.filing_date else None
    except ValueError:
        filed = None
    return FilingInput(
        form=manifest.form, primary=document(primaries[0]),
        exhibits=tuple(document(entry) for entry in manifest.documents if entry.role == "exhibit"),
        header=document(headers[0]) if headers else None, cik=cik, accession=accession, filing_date=filed,
    )


def fetch_and_extract(client: _Client, *, cik: int, accession: str,
                      cache: Path) -> tuple[FetchReceipt, Item1AExtraction]:
    """Closed route: fetch, extract offline, and fetch listed EX-13/EX-99 only when consulted.

    The returned extraction equals ``extract_item1a`` over the primary document plus
    every exhibit the index page lists whenever the extractor would consult them.
    """
    receipt = fetch_filing(client, cik=cik, accession=accession, cache=cache)
    result = extract_item1a(load_cached_filing(cache, cik=cik, accession=accession))
    manifest = read_manifest(cache, cik=cik, accession=accession)
    if result.text_status in EXHIBIT_ROUTE_STATUSES and manifest.listed_exhibits:
        second = fetch_filing(client, cik=cik, accession=accession, cache=cache, exhibits=True)
        receipt = second.model_copy(update={
            "fetched": tuple(sorted(set(receipt.fetched) | set(second.fetched))),
            "reused": tuple(sorted(set(second.reused) - set(receipt.fetched))),
            "http_requests": receipt.http_requests + second.http_requests,
        })
        result = extract_item1a(load_cached_filing(cache, cik=cik, accession=accession))
    return receipt, result
