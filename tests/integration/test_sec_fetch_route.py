"""Closed SEC route offline: fake Archives responses → verified cache → extraction."""

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from edgar_10k_item1a import TextStatus, cli
from edgar_10k_item1a.sec.cache import CacheError, fetch_and_extract, fetch_filing, load_cached_filing
from edgar_10k_item1a.sec.client import SecHTTPError

CIK, ACC = 320193, "0000320193-23-000106"
FOLDER = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{ACC.replace('-', '')}/"
INDEX, HEADER = FOLDER + f"{ACC}-index.html", FOLDER + f"{ACC}.hdr.sgml"
RISKS = ("Competition may materially adversely affect our business, financial condition and results of "
         "operations. We could lose customers and our margins may decline. ") * 12
RISKS = RISKS.rstrip()
PRIMARY = ("<html><body><p>Item 1A. Risk Factors</p><p>" + RISKS
           + "</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>").encode()
STUB = ("<html><body><p>Item 1A. Risk Factors</p><p>The information required by this Item is incorporated by "
        "reference to the section entitled \"Risk Factors\" in the 2023 Annual Report to Shareholders, filed as "
        "Exhibit 13.</p><p>Item 1B. Unresolved Staff Comments</p><p>None.</p></body></html>").encode()
EX13 = ("<html><body><p>Letter</p><p>RISK FACTORS</p><p>" + RISKS + " " + RISKS
        + "</p><p>Report of Independent Registered Public Accounting Firm</p></body></html>").encode()


def row(seq, name, doc_type, href=None):
    href = href or "/Archives/edgar/data/%d/%s/%s" % (CIK, ACC.replace("-", ""), name)
    return (f'<tr><td scope="row">{seq}</td><td scope="row">{doc_type}</td>'
            f'<td scope="row"><a href="{href}">{name}</a></td><td scope="row">{doc_type}</td>'
            '<td scope="row">1234</td></tr>')


def index_page(*rows, form="10-K"):
    return (f"<html><head><title>EDGAR Filing Documents for {ACC}</title></head><body>"
            f'<div id="formName"><strong>Form {form}</strong> - Annual report</div>'
            '<div class="formGrouping"><div class="infoHead">Filing Date</div><div class="info">2023-11-03</div>'
            '<div class="infoHead">Period of Report</div><div class="info">2023-09-30</div></div>'
            '<table class="tableFile" summary="Document Format Files">'
            "<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>"
            + "".join(rows) + "</table>"
            '<div class="companyInfo"><span class="companyName">Apple Inc. (Filer) CIK: '
            f'<a href="/cgi-bin/browse-edgar?action=getcompany&amp;CIK={CIK:010d}">{CIK:010d}</a></span></div>'
            "</body></html>").encode()


class FakeClient:
    def __init__(self, pages, redirects=None):
        self.pages, self.redirects, self.requested, self.n_http_requests = dict(pages), dict(redirects or {}), [], 0

    def get(self, url):
        self.n_http_requests += 1
        self.requested.append(url)
        final = self.redirects.get(url, url)
        if final not in self.pages:
            raise SecHTTPError("HTTP 404", url=url, http_status=404, attempts=1)
        content = self.pages[final]
        return SimpleNamespace(content=content, sha256=sha256(content).hexdigest(), final_url=final)


def archive(primary=PRIMARY, extra_rows=(), extra_pages=None):
    pages = {INDEX: index_page(row(1, "annual.htm", "10-K"), row(2, "ex13.htm", "EX-13"), *extra_rows),
             HEADER: b"<SEC-HEADER>synthetic</SEC-HEADER>", FOLDER + "annual.htm": primary,
             FOLDER + "ex13.htm": EX13}
    pages.update(extra_pages or {})
    return pages


def test_present_primary_fetches_only_bound_primary_and_reruns_offline(tmp_path):
    client = FakeClient(archive())
    receipt, result = fetch_and_extract(client, cik=CIK, accession=ACC, cache=tmp_path)
    assert client.requested == [INDEX, HEADER, FOLDER + "annual.htm"] and receipt.http_requests == 3
    assert result.text_status is TextStatus.present_primary and result.text == RISKS
    assert result.source.origin == "sec_archive" and result.source.reference == FOLDER + "annual.htm"
    assert result.source_document_sha256 == sha256(PRIMARY).hexdigest()
    manifest = json.loads((tmp_path / str(CIK) / ACC.replace("-", "") / "manifest.json").read_text())
    assert manifest["listed_exhibits"] == ["ex13.htm"] and manifest["primary_status"] == "type_match"
    again = FakeClient(archive())
    second_receipt, second = fetch_and_extract(again, cik=CIK, accession=ACC, cache=tmp_path)
    assert again.requested == [] and second_receipt.fetched == () and second == result
    assert cli.main(["extract", "--cache", str(tmp_path), "--cik", str(CIK), "--accession", ACC, "--quiet"]) == 0


def test_pointer_stub_fetches_listed_exhibit_and_types_it_from_the_index(tmp_path):
    client = FakeClient(archive(primary=STUB))
    receipt, result = fetch_and_extract(client, cik=CIK, accession=ACC, cache=tmp_path)
    assert client.requested[-1] == FOLDER + "ex13.htm" and "ex13.htm" in receipt.fetched
    assert result.text_status is TextStatus.present_exhibit and result.exhibit_type == "EX-13"
    assert result.source.origin == "sec_archive" and result.source.reference == FOLDER + "ex13.htm"


def test_missing_header_is_recorded_not_fatal(tmp_path):
    pages = archive()
    del pages[HEADER]
    receipt, result = fetch_and_extract(FakeClient(pages), cik=CIK, accession=ACC, cache=tmp_path)
    assert receipt.unavailable == ("header:http_404",) and result.text_status is TextStatus.present_primary
    assert load_cached_filing(tmp_path, cik=CIK, accession=ACC).header is None


def test_ambiguous_primary_is_not_chosen_by_list_order(tmp_path):
    pages = archive(extra_rows=(row(3, "annual-copy.htm", "10-K"),),
                    extra_pages={FOLDER + "annual-copy.htm": PRIMARY})
    client = FakeClient(pages)
    receipt = fetch_filing(client, cik=CIK, accession=ACC, cache=tmp_path)
    assert receipt.primary_status == "ambiguous" and client.requested == [INDEX, HEADER]
    with pytest.raises(CacheError, match="primary_unresolved:ambiguous"):
        load_cached_filing(tmp_path, cik=CIK, accession=ACC)


def test_unbound_links_are_never_requested(tmp_path):
    rows = (row(3, "other.htm", "EX-99.1", href="https://evil.example/other.htm"),
            row(4, "elsewhere.htm", "EX-99.2", href="/Archives/edgar/data/320193/000032019322000001/elsewhere.htm"))
    client = FakeClient(archive(primary=STUB, extra_rows=rows))
    fetch_and_extract(client, cik=CIK, accession=ACC, cache=tmp_path)
    assert all(url.startswith(FOLDER) for url in client.requested)


@pytest.mark.parametrize("tamper", ["bytes", "url", "filename"])
def test_cache_tampering_is_detected_offline(tmp_path, tamper):
    fetch_and_extract(FakeClient(archive()), cik=CIK, accession=ACC, cache=tmp_path)
    directory = tmp_path / str(CIK) / ACC.replace("-", "")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    primary = next(entry for entry in manifest["documents"] if entry["role"] == "primary")
    if tamper == "bytes":
        (directory / "annual.htm").write_bytes(PRIMARY.replace(b"Competition", b"Cooperation"))
    elif tamper == "url":
        primary["url"] = "https://www.sec.gov/Archives/edgar/data/320193/000032019322000001/annual.htm"
    else:
        primary["filename"] = "../annual.htm"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(CacheError):
        load_cached_filing(tmp_path, cik=CIK, accession=ACC)


def test_cli_fetch_requires_online_and_never_builds_a_client(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda: pytest.fail("client built without --online"))
    code = cli.main(["fetch", "--cik", str(CIK), "--accession", ACC, "--cache", str(tmp_path), "--quiet"])
    assert code == 2 and "--online" in json.loads(capsys.readouterr().out)["message"]


def test_cli_closed_route_and_offline_rerun(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda: FakeClient(archive(primary=STUB)))
    assert cli.main(["fetch", "--cik", str(CIK), "--accession", ACC, "--cache", str(tmp_path),
                     "--online", "--extract", "--quiet"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["item1a"]["text_status"] == "present_exhibit" and "ex13.htm" in payload["fetch"]["fetched"]
    monkeypatch.setattr(cli, "_client", lambda: pytest.fail("offline extract built a client"))
    assert cli.main(["extract", "--cache", str(tmp_path), "--cik", str(CIK), "--accession", ACC, "--quiet"]) == 0
    assert json.loads(capsys.readouterr().out) == payload["item1a"]


def test_cli_index_from_local_master_idx(tmp_path, capsys):
    master = tmp_path / "master.idx"
    master.write_bytes(b"Description: Master Index\n\nCIK|Company Name|Form Type|Date Filed|Filename\n"
                       b"--------------------------------------------------------------------------------\n"
                       b"320193|Apple Inc.|10-K|2023-11-03|edgar/data/320193/0000320193-23-000106.txt\n")
    code = cli.main(["index", "--cik", str(CIK), "--start", "2023-10-01", "--end", "2023-12-31",
                     "--input", f"2023Q4={master}", "--observed-on", "2026-10-12", "--quiet"])
    finding = json.loads(capsys.readouterr().out)
    assert code == 0 and finding["status"] == "annual_found"
    assert [filing["accession"] for filing in finding["filings"]] == [ACC]


def test_package_import_loads_no_sec_adapter():
    src = Path(__file__).resolve().parents[2] / "src"
    program = ("import sys; sys.path.insert(0, sys.argv[1]); import edgar_10k_item1a; "
               "assert not any(m.startswith(('edgar_10k_item1a.sec', 'edgar_10k_item1a.cli', 'urllib.request')) "
               "for m in sys.modules), sorted(m for m in sys.modules if 'edgar' in m)")
    environment = {name: value for name, value in os.environ.items() if not name.startswith(("SEC_", "PYTHON"))}
    subprocess.run([sys.executable, "-B", "-I", "-c", program, str(src)], env=environment, check=True)


def test_cli_online_fetch_without_user_agent_fails_before_any_request(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **k: pytest.fail("network attempted"))
    code = cli.main(["fetch", "--cik", str(CIK), "--accession", ACC, "--cache", str(tmp_path), "--online", "--quiet"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2 and "SEC_USER_AGENT" in payload["message"] and not any(tmp_path.iterdir())


OTHER = "https://www.sec.gov/Archives/edgar/data/320193/000032019322000001/annual.htm"


def test_redirect_to_another_accession_is_rejected_not_attributed(tmp_path):
    pages = archive(extra_pages={OTHER: PRIMARY})
    client = FakeClient(pages, redirects={FOLDER + "annual.htm": OTHER})
    with pytest.raises(CacheError, match="redirect"):
        fetch_and_extract(client, cik=CIK, accession=ACC, cache=tmp_path)
    directory = tmp_path / str(CIK) / ACC.replace("-", "")
    assert not (directory / "annual.htm").exists() and not (directory / "manifest.json").exists()


def test_cli_index_treats_a_redirected_quarter_as_missing(tmp_path, monkeypatch, capsys):
    url = "https://www.sec.gov/Archives/edgar/full-index/2023/QTR4/master.idx"
    moved = "https://www.sec.gov/Archives/edgar/full-index/2022/QTR4/master.idx"
    payload = (b"Description: Master Index\n\nCIK|Company Name|Form Type|Date Filed|Filename\n" + b"-" * 80
               + b"\n320193|Apple Inc.|10-K|2023-11-03|edgar/data/320193/0000320193-23-000106.txt\n")
    monkeypatch.setattr(cli, "_client", lambda: FakeClient({moved: payload}, redirects={url: moved}))
    assert cli.main(["index", "--cik", str(CIK), "--start", "2023-10-01", "--end", "2023-12-31", "--online",
                     "--cache", str(tmp_path), "--observed-on", "2026-10-12", "--quiet"]) == 0
    finding = json.loads(capsys.readouterr().out)
    assert finding["status"] == "unknown" and "quarter_missing:2023Q4" in finding["reasons"]
    assert finding["filings"] == []


def test_plain_refetch_keeps_previously_fetched_exhibits(tmp_path):
    first_receipt, first = fetch_and_extract(FakeClient(archive(primary=STUB)), cik=CIK, accession=ACC, cache=tmp_path)
    assert first.text_status is TextStatus.present_exhibit
    client = FakeClient(archive(primary=STUB))
    receipt = fetch_filing(client, cik=CIK, accession=ACC, cache=tmp_path)
    assert client.requested == [] and "ex13.htm" in receipt.reused
    assert any(entry.filename == "ex13.htm" for entry in receipt.documents)
    from edgar_10k_item1a import extract_item1a
    assert extract_item1a(load_cached_filing(tmp_path, cik=CIK, accession=ACC)) == first


@pytest.mark.parametrize("tamper", ["index_bytes", "exhibit_type", "primary_type", "form"])
def test_offline_load_verifies_the_index_page_and_derives_types_from_it(tmp_path, tamper):
    fetch_and_extract(FakeClient(archive(primary=STUB)), cik=CIK, accession=ACC, cache=tmp_path)
    directory = tmp_path / str(CIK) / ACC.replace("-", "")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    by_role = {entry["role"]: entry for entry in manifest["documents"]}
    if tamper == "index_bytes":
        (directory / f"{ACC}-index.html").write_bytes(index_page(row(1, "annual.htm", "10-K")))
    elif tamper == "exhibit_type":
        by_role["exhibit"]["doc_type"] = "EX-99.1"
    elif tamper == "primary_type":
        by_role["primary"]["doc_type"] = "10-K405"
    else:
        manifest["form"] = "10-K405"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(CacheError):
        load_cached_filing(tmp_path, cik=CIK, accession=ACC)
