"""Offline synthetic checks of the pure SEC index layer (no network, no real filings)."""

from datetime import date, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from edgar_10k_item1a.models import (
    CoverageFinding, IndexEvidence, IndexRow, IndexSnapshot, MissingQuarter, SourceRef,
)
from edgar_10k_item1a.sec import index as sec_index
from edgar_10k_item1a.sec.index import (
    COVERAGE_FORM_SCOPE, FilingIndex, IndexPageError, MasterIndexError, evaluate_coverage,
    filing_folder_url, filing_index_url, full_index_url, hdr_sgml_url, parse_filing_index,
    parse_master_idx, quarter_complete, quarters_between,
)

SOURCE = SourceRef(reference="fixture://master-idx", origin="synthetic")
PREAMBLE = (
    b"Description:           Master Index of EDGAR Dissemination Feed\n"
    b"Last Data Received:    Synthetic\n"
    b"Comments:              synthetic fixture\n\n \n"
    b"CIK|Company Name|Form Type|Date Filed|Filename\n" + b"-" * 80 + b"\n"
)


def idx_line(cik, form, filed, acc, name="SYNTH CO", file_cik=None):
    return f"{cik}|{name}|{form}|{filed}|edgar/data/{file_cik or cik}/{acc}.txt"


def master(*lines, newline="\n"):
    return PREAMBLE + newline.join(lines).encode("latin-1") + newline.encode()


def snap(year, quarter, *lines):
    return parse_master_idx(master(*lines), source=SOURCE, year=year, quarter=quarter)


# ------------------------------------------------------------------ master.idx
def test_master_idx_preamble_pipes_aliases_amendments_and_sorting():
    payload = master(
        idx_line(7, "10-K/A", "2024-03-01", "0000000007-24-000003"),
        idx_line(5, "10K405", "20240115", "0000000005-24-000001", name="A|B HOLDINGS"),
        idx_line(9, "10-K", "2024-02-02", "0000000009-24-000002", name="CAF\xc9 \x85 CORP"),
        idx_line(5, "10K405", "20240115", "0000000005-24-000001", name="A|B HOLDINGS"),
        "", newline="\r\n",
    )
    result = parse_master_idx(payload, source=SOURCE, year=2024, quarter=1)
    assert (result.year, result.quarter, result.source) == (2024, 1, SOURCE)
    assert result.source_sha256 == sha256(payload).hexdigest()
    assert result.parse_skipped == 0
    assert [(r.accession, r.cik) for r in result.rows] == [
        ("0000000005-24-000001", 5), ("0000000007-24-000003", 7), ("0000000009-24-000002", 9)]
    alias, amended, latin = result.rows
    assert (alias.company_name, alias.form, alias.base_form, alias.amendment) == (
        "A|B HOLDINGS", "10K405", "10-K405", False)
    assert alias.filing_date == date(2024, 1, 15)
    assert (amended.form, amended.base_form, amended.amendment) == ("10-K/A", "10-K", True)
    assert latin.company_name == "CAF\xc9 \x85 CORP"
    assert IndexSnapshot.model_validate_json(result.model_dump_json()) == result


def test_master_idx_malformed_lines_are_skipped_and_counted():
    good = idx_line(5, "10-K", "2024-02-01", "0000000005-24-000001")
    result = snap(
        2024, 1,
        good,
        "not an index line",
        "5|SYNTH CO|10-K|2024-02-01",  # too few fields
        idx_line("x5", "10-K", "2024-02-01", "0000000005-24-000002", file_cik=5),
        idx_line(6, "10-K", "2024-02-01", "0000000006-24-000001", file_cik=66),  # CIK mismatch
        idx_line(0, "10-K", "2024-02-01", "0000000000-24-000001"),  # CIK must be positive
        idx_line(7, "10-K", "2024-13-01", "0000000007-24-000001"),  # impossible date
        idx_line(8, "", "2024-02-01", "0000000008-24-000001"),  # empty form
        "9|SYNTH CO|10-K|2024-02-01|edgar/data/9/0000000009-24-000001.htm",  # not .txt
        idx_line(5, "10-Q", "2024-02-01", "0000000005-24-000001"),  # conflicting duplicate key
    )
    assert result.parse_skipped == 9
    assert [(r.cik, r.form) for r in result.rows] == [(5, "10-K")]


def test_master_idx_requires_separator_bytes_and_valid_quarter():
    with pytest.raises(MasterIndexError):
        parse_master_idx(b"CIK|Company Name|Form Type|Date Filed|Filename\n1|A|10-K|2024-01-02|x\n",
                         source=SOURCE, year=2024, quarter=1)
    assert issubclass(MasterIndexError, ValueError)
    with pytest.raises(TypeError):
        parse_master_idx(master().decode(), source=SOURCE, year=2024, quarter=1)
    for year, quarter in ((1992, 4), (2024, 0), (2024, 5)):
        with pytest.raises(ValueError):
            parse_master_idx(master(), source=SOURCE, year=year, quarter=quarter)


def test_index_contracts_reject_inconsistent_rows():
    row = dict(cik=5, company_name=None, form="10-K/A", base_form="10-K", amendment=True,
               filing_date=date(2024, 1, 2), accession="0000000005-24-000001")
    IndexRow(**row)
    for change in ({"amendment": False}, {"base_form": "10-Q"}, {"cik": 0}, {"accession": "5-24-1"}):
        with pytest.raises(ValidationError):
            IndexRow(**{**row, **change})
    first = IndexRow(**row)
    second = IndexRow(**{**row, "accession": "0000000005-24-000002"})
    fields = dict(year=2024, quarter=1, source=SOURCE, source_sha256="a" * 64, parse_skipped=0)
    assert IndexSnapshot(rows=(first, second), **fields).rows == (first, second)
    for rows in ((second, first), (first, first)):
        with pytest.raises(ValidationError):
            IndexSnapshot(rows=rows, **fields)


# ------------------------------------------------------------------ quarters and URLs
def test_full_index_url_has_lower_but_no_upper_bound():
    assert full_index_url(1993, 1) == "https://www.sec.gov/Archives/edgar/full-index/1993/QTR1/master.idx"
    assert full_index_url(2101, 4).endswith("/2101/QTR4/master.idx")
    for year, quarter in ((1992, 4), (2024, 0), (2024, 5)):
        with pytest.raises(ValueError):
            full_index_url(year, quarter)
    with pytest.raises(TypeError):
        full_index_url(2024, True)


def test_quarters_between_edges():
    assert quarters_between(date(2024, 5, 5), date(2024, 5, 5)) == [(2024, 2)]
    assert quarters_between(date(2023, 12, 31), date(2024, 4, 1)) == [(2023, 4), (2024, 1), (2024, 2)]
    assert quarters_between(date(2024, 3, 31), date(2024, 4, 1)) == [(2024, 1), (2024, 2)]
    with pytest.raises(ValueError):
        quarters_between(date(2024, 2, 1), date(2024, 1, 31))
    with pytest.raises(TypeError):
        quarters_between(datetime(2024, 1, 1), date(2024, 2, 1))


@pytest.mark.parametrize(("year", "quarter", "last_day"), (
    (2024, 1, date(2024, 3, 31)), (2024, 2, date(2024, 6, 30)),
    (2024, 3, date(2024, 9, 30)), (2024, 4, date(2024, 12, 31)),
))
def test_quarter_complete_only_after_last_day(year, quarter, last_day):
    assert not quarter_complete(year, quarter, observed_on=last_day)
    assert quarter_complete(year, quarter, observed_on=date.fromordinal(last_day.toordinal() + 1))


# ------------------------------------------------------------------ coverage
CIK = 320
START, END, OBSERVED = date(2024, 1, 1), date(2024, 12, 31), date(2025, 3, 1)
OTHER_10K = idx_line(999, "10-K", "2024-02-10", "0000000999-24-000001")


def quiet_year(**overrides):
    """One clean snapshot per 2024 quarter; ``overrides`` replaces a quarter's lines."""
    base = {
        1: [OTHER_10K, idx_line(CIK, "8-K", "2024-02-01", "0000000320-24-000001")],
        2: [idx_line(CIK, "10-Q", "2024-05-01", "0000000320-24-000002")],
        3: [idx_line(CIK, "10-Q", "2024-08-01", "0000000320-24-000003")],
        4: [idx_line(CIK, "NT 10-K", "2024-11-01", "0000000320-24-000004")],
    }
    base.update({int(key[1:]): value for key, value in overrides.items()})
    return {quarter: snap(2024, quarter, *lines) for quarter, lines in base.items()}


def coverage(snapshots, missing=(), observed_on=OBSERVED, start=START, end=END):
    evidence = IndexEvidence(snapshots=tuple(snapshots), missing=tuple(missing), observed_on=observed_on)
    return evaluate_coverage(evidence, cik=CIK, start=start, end=end)


def test_complete_quiet_window_is_no_annual_observation():
    finding = coverage(quiet_year().values())
    assert finding.status == "no_annual_in_window" and finding.reasons == ()
    assert finding.filings == () and finding.form_scope == COVERAGE_FORM_SCOPE
    assert finding.quarters_checked == ("2024Q1", "2024Q2", "2024Q3", "2024Q4")
    assert (finding.cik, finding.start, finding.end, finding.observed_on) == (CIK, START, END, OBSERVED)
    assert CoverageFinding.model_validate_json(finding.model_dump_json()) == finding
    assert {"10-K", "10-K/A", "10-KSB405", "10-KT405/A", "20-F", "40-F/A"} <= set(COVERAGE_FORM_SCOPE)


def test_annual_found_lists_scoped_filings_in_window_order():
    snapshots = quiet_year(q3=[
        idx_line(CIK, "10-K/A", "2024-09-20", "0000000320-24-000009"),
        idx_line(CIK, "10KSB40", "2024-08-15", "0000000320-24-000008"),
        idx_line(CIK, "10-Q", "2024-08-01", "0000000320-24-000003"),
    ]).values()
    finding = coverage(snapshots)
    assert finding.status == "annual_found" and finding.reasons == ()
    assert [(r.form, r.base_form, r.filing_date) for r in finding.filings] == [
        ("10KSB40", "10-KSB405", date(2024, 8, 15)), ("10-K/A", "10-K", date(2024, 9, 20))]


def test_positive_evidence_stands_despite_missing_quarter():
    snapshots = quiet_year(q1=[idx_line(CIK, "10-K", "2024-02-20", "0000000320-24-000010")])
    del snapshots[3]
    finding = coverage(snapshots.values(), missing=(MissingQuarter(year=2024, quarter=3, reason="http_404"),))
    assert finding.status == "annual_found"
    assert finding.reasons == ("quarter_missing:2024Q3",)
    assert finding.quarters_checked == ("2024Q1", "2024Q2", "2024Q4")


def test_foreign_form_observed_and_ignores_other_ciks_and_dates():
    snapshots = quiet_year(q2=[
        idx_line(CIK, "20-F", "2024-04-30", "0000000320-24-000011"),
        idx_line(CIK, "10-K", "2023-12-31", "0000000320-23-000099"),  # before the window
    ])
    finding = coverage(snapshots.values())
    assert finding.status == "foreign_form_observed" and finding.reasons == ()
    assert [r.form for r in finding.filings] == ["20-F"]


def test_unknown_reason_quarter_missing_without_snapshot_or_with_missing_record():
    snapshots = quiet_year()
    del snapshots[3]
    assert coverage(snapshots.values()).status == "unknown"
    assert coverage(snapshots.values()).reasons == ("quarter_missing:2024Q3",)
    explicit = coverage(quiet_year().values(), missing=(MissingQuarter(year=2024, quarter=2, reason="x"),))
    assert (explicit.status, explicit.reasons) == ("unknown", ("quarter_missing:2024Q2",))


def test_unknown_reasons_incomplete_quarter_and_window_not_elapsed():
    finding = coverage(quiet_year().values(), observed_on=date(2024, 12, 15))
    assert finding.status == "unknown"
    assert finding.reasons == ("quarter_incomplete:2024Q4", "window_not_elapsed")
    same_day = coverage(quiet_year().values(), observed_on=END)
    assert same_day.reasons == ("quarter_incomplete:2024Q4", "window_not_elapsed")
    edge = coverage(quiet_year().values(), observed_on=date(2025, 1, 1))
    assert edge.status == "no_annual_in_window"


def test_unknown_reason_parse_skipped():
    finding = coverage(quiet_year(q1=[OTHER_10K, "garbage line"]).values())
    assert (finding.status, finding.reasons) == ("unknown", ("parse_skipped:2024Q1",))


def test_conflicting_snapshots_unknown_but_identical_duplicates_accepted():
    snapshots = list(quiet_year().values())
    duplicate = snap(2024, 2, idx_line(CIK, "10-Q", "2024-05-01", "0000000320-24-000002"))
    assert coverage(snapshots + [duplicate]).status == "no_annual_in_window"
    conflicting = snap(2024, 2, idx_line(CIK, "8-K", "2024-05-02", "0000000320-24-000012"))
    finding = coverage(snapshots + [conflicting])
    assert (finding.status, finding.reasons) == ("unknown", ("conflicting_snapshots:2024Q2",))


def test_amendment_only_observation_keeps_status_rules():
    lines = [idx_line(CIK, "10-K/A", "2024-04-15", "0000000320-24-000013")]
    complete = coverage(quiet_year(q2=lines).values())
    assert complete.status == "no_annual_in_window"
    assert complete.reasons == ("amendment_only_observed",)
    assert [r.form for r in complete.filings] == ["10-K/A"]
    gapped = quiet_year(q2=lines)
    del gapped[4]
    finding = coverage(gapped.values())
    assert finding.status == "unknown"
    assert finding.reasons == ("amendment_only_observed", "quarter_missing:2024Q4")


def test_reasons_are_sorted_and_window_inputs_validated():
    snapshots = quiet_year(q1=[OTHER_10K, "garbage"])
    del snapshots[2]
    finding = coverage(snapshots.values(), observed_on=date(2024, 12, 1))
    assert finding.reasons == ("parse_skipped:2024Q1", "quarter_incomplete:2024Q4",
                               "quarter_missing:2024Q2", "window_not_elapsed")
    evidence = IndexEvidence(snapshots=(), observed_on=OBSERVED)
    with pytest.raises(ValueError):
        evaluate_coverage(evidence, cik=CIK, start=END, end=START)
    with pytest.raises(ValueError):
        evaluate_coverage(evidence, cik=0, start=START, end=END)
    assert evaluate_coverage(evidence, cik=CIK, start=START, end=START).reasons == ("quarter_missing:2024Q1",)


def test_coverage_finding_contract_guards():
    assert CoverageFinding().status == "unknown"
    with pytest.raises(ValidationError):
        CoverageFinding(status="annual_found")
    with pytest.raises(ValidationError):
        CoverageFinding(reasons=("b", "a"))
    with pytest.raises(ValidationError):
        CoverageFinding(start=END, end=START)


# ------------------------------------------------------------------ filing index pages
SEC = "https://www.sec.gov"
ACC, ACC_CIK = "0000000101-24-000001", 101
FOLDER = f"/Archives/edgar/data/{ACC_CIK}/000000010124000001"
HEAD = ('<tr><th scope="col">Seq</th><th scope="col">Description</th><th scope="col">Document</th>'
        '<th scope="col">Type</th><th scope="col">Size</th></tr>')


def doc_row(seq, description, href, doc_type, size="10"):
    return (f'<tr><td scope="row">{seq}</td><td scope="row">{description}</td>'
            f'<td scope="row"><a href="{href}">{href.rsplit("/", 1)[-1]}</a></td>'
            f'<td scope="row">{doc_type}</td><td scope="row">{size}</td></tr>')


def index_page(rows, *, accession=ACC, form="10-K", filers=(ACC_CIK,), data_rows=(), title=None):
    entities = "".join(
        f'<div class="companyInfo"><span class="companyName">SYNTH CO {cik} (Filer)\n'
        f' <acronym title="Central Index Key">CIK</acronym>: <a href="/cgi-bin/browse-edgar?CIK={cik:010d}'
        f'&amp;action=getcompany">{cik:010d} (see all company filings)</a></span></div>'
        for cik in filers)
    data = (f'<table class="tableFile" summary="Data Files">{HEAD}{"".join(data_rows)}</table>'
            if data_rows else "")
    return (
        '<!DOCTYPE html><html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8" />'
        f'<title>{title or f"EDGAR Filing Documents for {accession}"}</title></head><body>'
        f'<div id="formName">\n<strong>Form {form}</strong> - Annual report</div>'
        f'<div id="secNum">\n<strong>SEC Accession No.</strong> {accession}\n</div>'
        '<div class="infoHead">Filing Date</div>\n<div class="info">2024-02-02</div>'
        '<div class="infoHead">Period of Report</div><div class="info">2023-12-31</div>'
        f'<p>Document Format Files</p><table class="tableFile" summary="Document Format Files">{HEAD}'
        f'{"".join(rows)}</table>{data}<div class="filerDiv">{entities}</div></body></html>'
    ).encode()


def parse(rows, **kwargs):
    page_kwargs = {k: kwargs.pop(k) for k in ("form", "filers", "data_rows", "title") if k in kwargs}
    return parse_filing_index(index_page(rows, **page_kwargs), cik=kwargs.pop("cik", ACC_CIK),
                              accession=ACC, **kwargs)


def test_url_helpers():
    assert filing_folder_url(12927, "0000012927-12-000024") == (
        "https://www.sec.gov/Archives/edgar/data/12927/000001292712000024/")
    assert filing_index_url(12927, "0000012927-12-000024") == (
        "https://www.sec.gov/Archives/edgar/data/12927/000001292712000024/0000012927-12-000024-index.html")
    assert hdr_sgml_url(12927, "0000012927-12-000024") == (
        "https://www.sec.gov/Archives/edgar/data/12927/000001292712000024/0000012927-12-000024.hdr.sgml")
    with pytest.raises(ValueError):
        filing_index_url(12927, "12-34")
    with pytest.raises(ValueError):
        filing_index_url(0, "0000012927-12-000024")
    with pytest.raises(TypeError):
        filing_index_url("12927", "0000012927-12-000024")


def test_type_match_ix_unwrap_exhibits_submission_and_rejections():
    result = parse([
        doc_row("1", "10-K", f"/ix?doc={FOLDER}/synth-10k.htm", "10-K", "1,234"),
        doc_row("2", "ANNUAL REPORT", f"{FOLDER}/ex13.htm", "EX-13", "5,000"),
        doc_row("3", "PRESS", f"{FOLDER}/ex991.htm", "EX-99.1", "700"),
        doc_row("4", "CONSENT", f"{FOLDER}/ex23.htm", "EX-23"),
        doc_row("5", "EXHIBIT 130", f"{FOLDER}/ex130.htm", "EX-130"),
        doc_row("6", "OFFSITE", f"https://evil.example{FOLDER}/x.htm", "EX-99.2"),
        doc_row("7", "OTHER FILING", "/Archives/edgar/data/101/000000010124000099/y.htm", "EX-99.3"),
        doc_row("&nbsp;", "Complete submission text file", f"{FOLDER}/{ACC}.txt", "&nbsp;", "99999"),
    ], data_rows=[doc_row("8", "XBRL INSTANCE", f"{FOLDER}/synth-20231231.xml", "EX-101.INS")])
    assert isinstance(result, FilingIndex)
    assert (result.page_form, result.primary_status) == ("10-K", "type_match")
    assert result.primary.url == f"{SEC}{FOLDER}/synth-10k.htm" and result.primary.size_bytes == 1234
    assert result.primary.filename == "synth-10k.htm" and result.primary.sequence == 1
    assert [(d.doc_type, d.filename, d.sequence) for d in result.exhibits] == [
        ("EX-13", "ex13.htm", 2), ("EX-99.1", "ex991.htm", 3)]
    assert result.complete_submission_url == f"{SEC}{FOLDER}/{ACC}.txt"
    assert result.rejected_links == (f"https://evil.example{FOLDER}/x.htm",
                                     "/Archives/edgar/data/101/000000010124000099/y.htm")
    assert {d.table for d in result.documents} == {"Document Format Files", "Data Files"}
    assert (result.filing_date, result.period_of_report) == ("2024-02-02", "2023-12-31")
    assert result.entities == ((ACC_CIK, "Filer"),)
    assert result.index_url == filing_index_url(ACC_CIK, ACC)
    assert FilingIndex.model_validate_json(result.model_dump_json()) == result


def test_two_text_documents_typed_like_form_are_ambiguous():
    result = parse([doc_row("1", "10-K", f"{FOLDER}/a.htm", "10-K"),
                    doc_row("2", "10-K", f"{FOLDER}/b.htm", "10-K")])
    assert result.primary is None and result.primary_status == "ambiguous"


def test_no_type_match_is_missing_without_sequence_fallback():
    result = parse([doc_row("1", "ANNUAL", f"{FOLDER}/a.txt", "10-K")], form="10-K405")
    assert result.primary is None and result.primary_status == "missing"
    data_only = parse([doc_row("1", "COVER", f"{FOLDER}/cover.jpg", "GRAPHIC")],
                      data_rows=[doc_row("2", "10-K", f"{FOLDER}/k.htm", "10-K")])
    assert data_only.primary_status == "missing"


def test_courtesy_pdf_typed_like_form_is_ignored():
    result = parse([doc_row("1", "COURTESY", f"{FOLDER}/k.pdf", "10-K"),
                    doc_row("2", "10-K", f"{FOLDER}/k.htm", "10-K")])
    assert result.primary_status == "type_match" and result.primary.filename == "k.htm"
    pdf_only = parse([doc_row("1", "COURTESY", f"{FOLDER}/k.pdf", "10-K")])
    assert pdf_only.primary_status == "missing"


def test_form_normalization_for_aliases_amendments_and_requested_form():
    alias = parse([doc_row("1", "FORM 10-KSB", f"{FOLDER}/k.htm", "10-KSB")], form="10KSB")
    assert alias.primary_status == "type_match"
    amended = [doc_row("1", "10-K", f"{FOLDER}/k.htm", "10-K"),
               doc_row("2", "10-K/A", f"{FOLDER}/ka.htm", "10-K/A")]
    assert parse(amended, form="10-K/A").primary.filename == "ka.htm"
    assert parse(amended[:1], form="10-K/A").primary_status == "missing"
    assert parse(amended[:1], form="10K").primary_status == "type_match"
    assert parse(amended[:1]).primary_status == "type_match"
    page = index_page(amended, form="10-K/A")
    assert parse_filing_index(page, cik=ACC_CIK, accession=ACC, form="10-K/A").primary_status == "type_match"
    with pytest.raises(IndexPageError):
        parse_filing_index(page, cik=ACC_CIK, accession=ACC, form="10-K")


def test_entity_cik_allows_its_folder_and_other_folders_are_rejected():
    other = "/Archives/edgar/data/303/000000010124000001"
    result = parse([doc_row("1", "COMBINED 10-K", f"{other}/combo10k.htm", "10-K"),
                    doc_row("2", "EX-99", f"{other}/ex99.htm", "EX-99"),
                    doc_row("3", "STRAY", "/Archives/edgar/data/999/000000010124000001/s.htm", "EX-99.9")],
                   filers=(303, ACC_CIK))
    assert result.primary.url == f"{SEC}{other}/combo10k.htm"
    assert [d.doc_type for d in result.exhibits] == ["EX-99"]
    assert result.rejected_links == ("/Archives/edgar/data/999/000000010124000001/s.htm",)
    assert result.entities == ((303, "Filer"), (ACC_CIK, "Filer"))


@pytest.mark.parametrize("href", (
    f"https://evil.example/ix?doc={FOLDER}/k.htm",
    f"/ix?doc={FOLDER}/k.htm&doc={FOLDER}/j.htm",
    f"/ix?doc=https://evil.example{FOLDER}/k.htm",
    f"/ix?doc={FOLDER}/k.htm&x=1",
    f"http://www.sec.gov{FOLDER}/k.htm",
    f"https://www.sec.gov:8443{FOLDER}/k.htm",
    f"{FOLDER}/k.htm?download=1",
    f"{FOLDER}/k%2F.htm",
    f"{FOLDER}/sub/k.htm",
    "/cgi-bin/browse-edgar?action=getcompany",
))
def test_unbound_or_unsafe_links_are_rejected(href):
    result = parse([doc_row("1", "10-K", href, "10-K")])
    assert result.rejected_links == (href,) and result.documents == ()
    assert result.primary_status == "missing"


def test_page_identity_and_decoding_failures_raise_index_page_error():
    rows = [doc_row("1", "10-K", f"{FOLDER}/k.htm", "10-K")]
    with pytest.raises(IndexPageError):
        parse(rows, title="EDGAR Filing Documents for 0000000202-24-000002")
    with pytest.raises(IndexPageError):
        parse_filing_index(index_page(rows, accession="0000000202-24-000002"),
                           cik=ACC_CIK, accession=ACC)
    page = index_page(rows)
    with pytest.raises(IndexPageError):
        parse_filing_index(page.replace(ACC.encode(), b"accession withheld"), cik=ACC_CIK, accession=ACC)
    with pytest.raises(IndexPageError):
        parse_filing_index(page.replace(b"Annual report", b"Annual \xff report"), cik=ACC_CIK, accession=ACC)
    undeclared = page.replace(b' content="text/html; charset=utf-8"', b"")
    with pytest.raises(IndexPageError):
        parse_filing_index(undeclared + b"\x81", cik=ACC_CIK, accession=ACC)
    assert issubclass(IndexPageError, ValueError)


def test_argument_validation():
    page = index_page([doc_row("1", "10-K", f"{FOLDER}/k.htm", "10-K")])
    with pytest.raises(TypeError):
        parse_filing_index(page.decode(), cik=ACC_CIK, accession=ACC)
    with pytest.raises(ValueError):
        parse_filing_index(page, cik=ACC_CIK, accession=ACC, page_url="https://evil.example/x-index.html")
    custom = f"{SEC}{FOLDER}/"
    assert parse_filing_index(page, cik=ACC_CIK, accession=ACC, page_url=custom).index_url == custom


def test_index_module_is_pure():
    source = Path(sec_index.__file__).read_text()
    for forbidden in ("import socket", "urllib.request", "http.client", "requests", ".client import"):
        assert forbidden not in source


# ------------------------------------------------------------------ CIK-scoped snapshots
def scoped(year, quarter, *lines, cik=CIK):
    return parse_master_idx(master(*lines), source=SOURCE, year=year, quarter=quarter, cik=cik)


def test_cik_scope_keeps_only_that_cik_but_counts_every_malformed_line():
    lines = (OTHER_10K, idx_line(CIK, "10-K", "2024-02-01", "0000000320-24-000009"), "not|a|row",
             idx_line(999, "10-K", "2024-02-11", "0000000999-24-000002", file_cik=998))
    full, narrow = snap(2024, 1, *lines), scoped(2024, 1, *lines)
    assert narrow.cik_scope == CIK and {row.cik for row in narrow.rows} == {CIK}
    assert narrow.parse_skipped == full.parse_skipped == 2 and narrow.source_sha256 == full.source_sha256
    with pytest.raises(ValidationError):
        IndexSnapshot.model_validate(dict(full.model_dump(), cik_scope=CIK))


def test_cik_scoped_evidence_gives_the_same_finding_as_full_snapshots():
    quarters = {1: [OTHER_10K, idx_line(CIK, "8-K", "2024-02-01", "0000000320-24-000001")],
                2: [idx_line(CIK, "10-Q", "2024-05-01", "0000000320-24-000002")],
                3: [idx_line(CIK, "10-Q", "2024-08-01", "0000000320-24-000003")],
                4: [idx_line(CIK, "NT 10-K", "2024-11-01", "0000000320-24-000004")]}
    narrow = coverage([scoped(2024, quarter, *lines) for quarter, lines in quarters.items()])
    assert narrow == coverage(quiet_year().values())


def test_snapshot_scoped_to_another_cik_never_supports_a_negative_finding():
    snapshots = dict(quiet_year())
    snapshots[2] = scoped(2024, 2, idx_line(CIK, "10-Q", "2024-05-01", "0000000320-24-000002"), cik=999)
    finding = coverage(snapshots.values())
    assert finding.status == "unknown"
    assert {"snapshot_out_of_scope:2024Q2", "quarter_missing:2024Q2"} <= set(finding.reasons)
