"""Command line: offline extraction by default; SEC Archives access only with --online.

JSON goes to stdout, one diagnostic line to stderr. Exit 0 means the command
completed (inspect the JSON status); exit 2 means usage, cache, network or SEC
access failure. The User-Agent is read from SEC_USER_AGENT and never printed.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys

from .extraction.item1a import extract_item1a
from .models import DocumentInput, FilingInput, IndexEvidence, MissingQuarter, SourceRef


def _emit(payload: dict, quiet: bool, line: str) -> None:
    print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    if not quiet:
        print(line, file=sys.stderr)


def _client():
    from .sec.client import SecClient
    return SecClient()


def _summary(result) -> str:
    flags = ",".join(result.flags) or "-"
    chars = len(result.text) if result.text is not None else 0
    return f"{result.text_status.value} tier={result.extraction_tier} quality={result.quality_flag} flags={flags} chars={chars}"


def _extract(args: argparse.Namespace) -> int:
    if args.input is not None:
        if args.cache or args.cik or args.accession:
            raise ValueError("use either --input or --cache/--cik/--accession")
        path = args.input
        filing = FilingInput(form=args.form, primary=DocumentInput(
            filename=path.name, content=path.read_bytes(), doc_type=args.doc_type or args.form,
            source=SourceRef(reference=f"file:{path.name}", origin="caller_declared")))
    elif args.cache and args.cik and args.accession:
        from .sec.cache import load_cached_filing
        filing = load_cached_filing(args.cache, cik=args.cik, accession=args.accession)
    else:
        raise ValueError("extract needs --input FILE or --cache DIR --cik CIK --accession ACC")
    result = extract_item1a(filing)
    _emit(result.model_dump(mode="json"), args.quiet, _summary(result))
    return 0


def _fetch(args: argparse.Namespace) -> int:
    if not args.online:
        raise ValueError("fetch contacts SEC Archives only with --online (offline is the default)")
    from .sec.cache import fetch_and_extract, fetch_filing
    client = _client()
    if args.extract:
        receipt, result = fetch_and_extract(client, cik=args.cik, accession=args.accession, cache=args.cache)
        _emit({"fetch": receipt.model_dump(mode="json"), "item1a": result.model_dump(mode="json")},
              args.quiet, f"fetched {len(receipt.fetched)} reused {len(receipt.reused)}; {_summary(result)}")
        return 0
    receipt = fetch_filing(client, cik=args.cik, accession=args.accession, cache=args.cache, exhibits=args.exhibits)
    _emit(receipt.model_dump(mode="json"), args.quiet,
          f"fetched {len(receipt.fetched)} reused {len(receipt.reused)} primary={receipt.primary_status}")
    return 0


def _quarter_input(value: str) -> tuple[int, int, Path]:
    match = re.fullmatch(r"(\d{4})Q([1-4])=(.+)", value)
    if match is None:
        raise argparse.ArgumentTypeError("expected YYYYQn=PATH, e.g. 2024Q1=master.idx")
    return int(match.group(1)), int(match.group(2)), Path(match.group(3))


def _index(args: argparse.Namespace) -> int:
    from .sec.index import evaluate_coverage, full_index_url, parse_master_idx, quarters_between
    if args.end < args.start:
        raise ValueError("--end precedes --start")
    snapshots, missing = [], []
    if args.input:
        if args.online:
            raise ValueError("use either --input or --online")
        for year, quarter, path in args.input:
            snapshots.append(parse_master_idx(path.read_bytes(), year=year, quarter=quarter, cik=args.cik,
                                              source=SourceRef(reference=f"file:{path.name}", origin="caller_declared")))
    elif args.online:
        if args.cache is None:
            raise ValueError("index --online needs --cache DIR for the fetched master.idx files")
        from .sec.cache import write_atomic
        from .sec.client import SecHTTPError, SecRetriesExhausted
        client = _client()
        for year, quarter in quarters_between(args.start, args.end):
            url = full_index_url(year, quarter)
            try:
                response = client.get(url)
            except SecHTTPError as exc:
                missing.append(MissingQuarter(year=year, quarter=quarter, reason=f"http_{exc.http_status}"))
                continue
            except SecRetriesExhausted:
                missing.append(MissingQuarter(year=year, quarter=quarter, reason="retries_exhausted"))
                continue
            if response.final_url != url:
                missing.append(MissingQuarter(year=year, quarter=quarter, reason="redirected"))
                continue
            payload = response.content
            write_atomic(Path(args.cache) / "full-index" / str(year) / f"QTR{quarter}" / "master.idx", payload)
            try:
                snapshots.append(parse_master_idx(payload, year=year, quarter=quarter, cik=args.cik,
                                                  source=SourceRef(reference=url, origin="sec_archive")))
            except ValueError:
                missing.append(MissingQuarter(year=year, quarter=quarter, reason="unparseable"))
    else:
        raise ValueError("index needs --input YYYYQn=PATH ... or --online --cache DIR")
    evidence = IndexEvidence(snapshots=tuple(snapshots), missing=tuple(missing), observed_on=args.observed_on)
    finding = evaluate_coverage(evidence, cik=args.cik, start=args.start, end=args.end)
    _emit(finding.model_dump(mode="json"), args.quiet,
          f"{finding.status} filings={len(finding.filings)} reasons={','.join(finding.reasons) or '-'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edgar-10k-item1a", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    extract = commands.add_parser("extract", help="extract Item 1A from a local file or a verified cache entry")
    extract.add_argument("--input", type=Path, help="local primary document (HTML or text)")
    extract.add_argument("--form", default="10-K", help="form of --input (default 10-K)")
    extract.add_argument("--doc-type", help="declared document type of --input (default: --form)")
    extract.add_argument("--cache", type=Path, help="cache directory written by fetch")
    extract.add_argument("--cik", type=int)
    extract.add_argument("--accession")
    extract.set_defaults(handler=_extract)

    fetch = commands.add_parser("fetch", help="download one filing's bound documents into the cache")
    fetch.add_argument("--cik", type=int, required=True)
    fetch.add_argument("--accession", required=True)
    fetch.add_argument("--cache", type=Path, required=True)
    fetch.add_argument("--online", action="store_true", help="required: permit SEC Archives requests")
    fetch.add_argument("--exhibits", action="store_true", help="also fetch every listed EX-13/EX-99")
    fetch.add_argument("--extract", action="store_true",
                       help="extract Item 1A afterwards; listed EX-13/EX-99 are fetched only when consulted")
    fetch.set_defaults(handler=_fetch)

    index = commands.add_parser("index", help="observe annual-filing coverage from quarterly master.idx files")
    index.add_argument("--cik", type=int, required=True)
    index.add_argument("--start", type=date.fromisoformat, required=True)
    index.add_argument("--end", type=date.fromisoformat, required=True)
    index.add_argument("--input", type=_quarter_input, action="append", default=[], metavar="YYYYQn=PATH")
    index.add_argument("--online", action="store_true", help="fetch the window's master.idx files from SEC Archives")
    index.add_argument("--cache", type=Path, help="where --online stores master.idx files")
    index.add_argument("--observed-on", type=date.fromisoformat, default=date.today(),
                       help="date for quarter completeness (default: today)")
    index.set_defaults(handler=_index)

    for sub in (extract, fetch, index):
        sub.add_argument("--quiet", action="store_true", help="suppress the stderr line")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from .sec.cache import CacheError
    from .sec.client import HostNotAllowed, SecFetchError
    try:
        return args.handler(args)
    except (CacheError, SecFetchError, HostNotAllowed, ValueError, OSError) as exc:
        _emit({"error": type(exc).__name__, "message": str(exc)}, args.quiet, f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
