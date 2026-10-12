# edgar-10k-item1a

Extracts Item 1A ("Risk Factors") from annual reports on the SEC Form 10-K family, from
local files or after an explicit, rate-limited download from SEC EDGAR Archives. Every
result records where the text came from, which boundaries delimited it and, when the
section could not be located safely, why the tool abstained instead of guessing.

Independent project: no affiliation with, approval by or trademark license from the U.S.
Securities and Exchange Commission; EDGAR is a registered SEC mark. Development version
`0.1.0.dev0`, not yet published on PyPI.

## 1. Purpose and limits

- **Forms:** `10-K`, `10-K405`, `10-KT`, `10-KT405` and amendments that contain Item 1A.
  `10-Q`, `10-KSB`, `20-F` and `40-F` text is not extracted.
- **Inputs:** a local primary document (HTML or plain text), optionally with typed EX-13 /
  EX-99 exhibits, or a filing downloaded into a verified cache by CIK and accession.
- **Output:** JSON with `text_status`, the text, its source and SHA-256 hashes, the
  character span in the projected text, the end anchor and diagnostic `flags`.
- **Offline by default.** Only commands with `--online` contact `https://www.sec.gov/Archives/edgar/`.
- **Not provided:** cover-page parsing, filing-obligation or exemption decisions, deadline
  checks. The corresponding result models are placeholders, not working APIs.
- `quality_flag` is a heuristic diagnostic. Synthetic tests are not a measure of accuracy
  on real filings. Not legal advice. See [limitations](docs/limitations.md).

## 2. Quickstart

Python 3.12 is required.

```bash
git clone https://github.com/HoffmannDennis/edgar-10k-item1a
cd edgar-10k-item1a
python3.12 -m venv .venv
.venv/bin/python -m pip install .                    # Windows: .venv\Scripts\python -m pip install .
.venv/bin/edgar-10k-item1a extract --input examples/synthetic-10k.htm
```

On Debian/Ubuntu, `python3.12 -m venv` needs the `python3.12-venv` package; with
[uv](https://docs.astral.sh/uv/) use `uv venv --python 3.12` and `uv pip install .` instead.
The last command needs no network. It prints JSON to stdout and one line to stderr:

```text
present_primary tier=primary-canonical quality=PASS_SHORT flags=- chars=1852
```

Download and extract a real filing (SEC fair-access policy: a User-Agent with your name and
a contact e-mail; it is sent as a request header only and never printed or stored):

```bash
export SEC_USER_AGENT="Your Name your.name@example.org"
.venv/bin/edgar-10k-item1a fetch --cik 320193 --accession 0000320193-23-000106 \
    --cache CACHE --online --extract > result.json
.venv/bin/edgar-10k-item1a extract --cache CACHE --cik 320193 --accession 0000320193-23-000106
.venv/bin/edgar-10k-item1a index --cik 320193 --start 2023-01-01 --end 2023-12-31 --online --cache CACHE
```

Keep `CACHE` and the outputs outside Git repositories: they contain filing text.

## 3. Setup prompt for an AI coding assistant

Copy the prompt below into your coding assistant. **Never paste your User-Agent contact or
any other personal data into a chat;** set `SEC_USER_AGENT` yourself as shown above.

```text
Please install and run the command-line tool "edgar-10k-item1a" for me.

1. In a folder of my choice, clone https://github.com/HoffmannDennis/edgar-10k-item1a and change
   into it. Python 3.12 is required.
2. Create a virtual environment with Python 3.12 (`python3.12 -m venv .venv`) and install the
   tool into it (`.venv/bin/python -m pip install .`; on Windows use `.venv\Scripts\python`).
   If venv creation fails because ensurepip is missing, use uv when available
   (`uv venv --python 3.12`, then `uv pip install .`); otherwise tell me to install the
   python3.12-venv package myself.
3. Check the installation offline:
   `.venv/bin/edgar-10k-item1a extract --input examples/synthetic-10k.htm --quiet`
   It prints JSON whose "text_status" is "present_primary".
4. Downloads from SEC EDGAR need the environment variable SEC_USER_AGENT with my name and
   contact e-mail. Tell me how to set it myself. Never ask me for it, never print it and never
   write it into a file. You only see it if it was set before you were started; if I set it
   now, I restart you or run the commands myself.
5. Ask me for the company's CIK and the filing's accession number. If I only know the company
   and the year, run `.venv/bin/edgar-10k-item1a index --cik <CIK> --start <YYYY-01-01>
   --end <YYYY-12-31> --online --cache <cache dir>` and show me the annual filings it lists.
   Before every command with --online, tell me what will be requested and wait for my OK.
   Use a cache directory outside any Git repository and never run downloads in parallel.
6. Run `.venv/bin/edgar-10k-item1a fetch --cik <CIK> --accession <ACCESSION> --cache <cache dir>
   --online --extract > result.json`.
7. Summarise result.json: item1a.text_status, extraction_tier, end_anchor_family,
   quality_flag, flags and the length of item1a.text, and tell me where the text is. For any
   status other than present_primary, present_recovered or present_exhibit, explain the
   status and flags from the README instead of presenting text. present_cautionary_exhibit is
   safe-harbor text, not Item 1A. Do not edit the downloaded files.
```

## 4. How the section is located

1. **Decode** the bytes losslessly (BOM, declared charset, strict UTF-8, then strict
   windows-1252); failure gives `document_parse_unresolved`.
2. **Project** HTML to visible text: hidden elements (`head`, `script`, `style`, `template`,
   inline XBRL headers, `display:none`) are dropped, blocks become lines and table cells tabs.
   EDGAR plain-text filings stay plain text. All positions refer to this projection.
3. **Start:** a line-leading "Item 1A" heading with "Risk Factors" on the same or the next
   line. Cross-references ("see Item 1A"), table-of-contents rows and running headers are
   rejected. Fallbacks: a combined "Items 1 and 1A" heading with a unique risk subheading, a
   bare "Risk Factors" heading, recovery from a pointer into MD&A, then EX-13 / EX-99.
4. **End:** the earliest supported anchor after the start: a genuine Item 1B, 1C or 2
   heading (E1), a fixed set of heading-shaped non-item titles such as the auditor's report
   or the MD&A title (E2), or an all-caps item title (E3).
5. **Abstain** (`boundary_unresolved`, no text) when no anchor exists, when the anchor is more
   than 200,000 characters away, or when an unsupported item heading or a standalone
   annual-report heading appears before the accepted end.
6. **Classify** the body: an absence statement, an incorporation pointer (`stub_unresolved`)
   or text with a `quality_flag`. Only trailing blank lines and page furniture are removed.

| Status | Meaning |
|---|---|
| `present_primary`, `present_recovered`, `present_exhibit` | Item 1A text found (primary document, recovered location, EX-13/EX-99) |
| `present_cautionary_exhibit` | PSLRA safe-harbor text from an EX-99, **not** Item 1A |
| `absent_statement` | The filer states that the item is not provided (e.g. smaller reporting company); not a legal finding |
| `missing`, `not_found_in_amendment` | No Item 1A heading found |
| `stub_unresolved` | A pointer to text elsewhere whose target was not supplied or not found |
| `start_unresolved`, `boundary_unresolved`, `ambiguous` | Abstained: start, end or source not determinable |
| `unsupported_form`, `document_parse_unresolved` | Form out of scope, or bytes/HTML not decodable |

## 5. SEC retrieval and cache

- `fetch` requests the accession's index page, its SEC header and the unique primary
  document whose index type equals the filing form (several → ambiguous, none → missing;
  never list order). With `--extract`, listed EX-13 / EX-99 exhibits are fetched only when
  the extractor would consult them. Every URL must lie in that accession's Archives folder,
  and a response redirected elsewhere is rejected.
- The cache (`CACHE/<cik>/<accession>/manifest.json`) records role, SEC-declared type, URL and
  SHA-256 per file. `extract --cache` re-verifies every hash, checks the manifest against the
  cached index page and never fetches. `origin=sec_archive` is retrieval provenance, not an
  authenticity certificate.
- One client sends at most 5 requests per second, one at a time; it retries 429/5xx with
  backoff and `Retry-After`, rejects truncated bodies, follows redirects only inside the
  allowlist and stops for good on HTTP 403 or an SEC block page.
- `index` reports `annual_found`, `foreign_form_observed`, `no_annual_in_window` or
  `unknown`. A negative finding requires every overlapping quarter to be complete, elapsed and
  parsed without skipped lines; it covers the annual form scope of that index, never all SEC
  filings and never a legal exemption.
- Exit code 0 means the command completed (inspect the JSON); 2 means a usage, cache, network
  or SEC access failure.

## 6. Python API

```python
from pathlib import Path
from edgar_10k_item1a import DocumentInput, FilingInput, SourceRef, extract_item1a

filing = FilingInput(
    form="10-K",
    primary=DocumentInput(
        filename="annual-report.htm",
        content=Path("annual-report.htm").read_bytes(),
        doc_type="10-K",
        source=SourceRef(reference="caller://annual-report", origin="caller_declared"),
    ),
)
result = extract_item1a(filing)
print(result.model_dump_json(indent=2))
```

`extract_item1a` never downloads anything. Caller-supplied source metadata is provenance, not
SEC authentication. The source hash covers the supplied original bytes. Spans address the
projected text; their projection hash and version, plus the normalization version
`item1a-body-v1`, reproduce the returned text exactly. The SEC adapter lives in
`edgar_10k_item1a.sec` (`SecClient`, `fetch_and_extract`, `load_cached_filing`,
`parse_master_idx`, `evaluate_coverage`) and is never imported by the core package.

## 7. Development

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -B -m pytest -q
.venv/bin/python -m build --no-isolation
```

The tests are synthetic and offline. `tools/verify_rule_sources.py --sources DIR` checks the
SHA-256 identity of three SEC rule sources listed in the bundled manifest; a hash match is byte
identity, not legal validation. See [rules](docs/rules.md) and
[source rights](docs/source-rights.md).

## 8. License

[MIT License](LICENSE). SEC rule texts referenced by the manifest are not covered by this
license.
