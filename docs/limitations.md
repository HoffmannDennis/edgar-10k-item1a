# Limitations

- Development version `0.1.0.dev0`; not legal advice.
- Cover/obligation models are only separate result contracts. A cover parser and
  an obligation evaluator are not implemented; the CLI has no `analyze` command.
- SEC retrieval covers Archives only: quarterly `master.idx`, a filing's index page,
  `hdr.sgml`, its primary document and listed EX-13/EX-99. No `data.sec.gov`
  submissions API, full-text search, XBRL frames or crawling of linked hosts.
  Local full-submission `.txt` splitting, exhibit manifests for local files,
  JSONL batches and `verify-rules` are not implemented yet.
- Rate limiting is per client and serial; it is no cross-process guarantee. Run one
  fetching process at a time. Index coverage is an observation of the parsed form
  scope; a gap, an unfinished quarter or a skipped line leaves it `unknown`.
- Supported annual text forms are listed in the root README. 10-Q, financial
  statement data sets and case-specific corpus overrides are excluded. No corpus
  accuracy claim is made.
- Projection is not a browser: linked stylesheets, generated content, images,
  layout-dependent whitespace and full CSS visibility are not established.
  Scripts and remote resources are never executed or fetched.
- Missing supported formal boundaries cause abstention, even when a person
  could identify a risk section. Quality PASS is heuristic, not completeness.
- Formal endings use genuine Item 1B/1C/2 headings, a finite set of shaped
  non-item headings or guarded all-caps item titles. EOF, an image marker or a
  later Item 3–15 alone does not qualify. A body-to-end distance above 200,000
  projected characters is outside scope; no silently truncated formal text.
  A genuine unsupported item heading or standalone annual-report section title
  (MD&A, market for common equity, selected financial data, market risk) before
  the accepted end causes abstention rather than overcapture.
- Combined Business/Risk headings need an identifiable risk-only subheading.
  Formal exhibit recovery needs declared document type and source binding;
  different qualifying unbound bodies remain ambiguous. EX-99 cautionary text
  is a separate type and may be document-bounded, never called formal Item 1A.
- Explicitly declared offline source metadata/document types are not SEC
  authenticity certification. A source hash proves only supplied-byte identity.
- Heading-context checks are finite: four lines/256 characters on the right and
  six lines/512 characters for TOC-label lookback. Short EOF pointers/absence
  answers can be recorded as nonpresent evidence (1,000/1,500-character caps);
  these exceptions do not license formal text without a supported end.
- Filename binding and filer-absence answers require explicit conservative
  wording. Unsupported or ambiguous phrasing can abstain; no language-wide
  recovery/completeness guarantee is made.
- No human-annotated benchmark has been run; tests are synthetic. One live SEC
  retrieval was checked manually, which is not an accuracy measurement.
- The name uses a registered SEC mark. No SEC affiliation, approval or license is
  claimed.
