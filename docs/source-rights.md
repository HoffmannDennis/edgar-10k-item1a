# Legal-source identity and distribution limits (2026-10-11)

The three manifest sources are SEC-authored final rule releases 33-8591 and
33-8876, and a Division of Corporation Finance transition FAQ. They are not
issuer filings, licensed vendor inputs, or covered institutional scholarly works.
Only manifest metadata and short attributed quotations belong in this Git repo;
no original PDF/HTML or artwork is redistributed here.

## Basis actually read

- SEC Privacy Information, **Website Dissemination**, reviewed 2023-11-29:
  information on sec.gov may be copied/further distributed without SEC permission
  with appropriate citation. This same section excludes use of SEC seal, logos
  and artwork, and identifies EDGAR and related marks as registered trademarks.
  Source: `https://www.sec.gov/about/privacy-information#dissemination`.
- 17 U.S.C. 105(a), U.S. Code 2024 official edition: U.S. Government works do not
  receive copyright protection under Title 17. This is not a universal rule for
  third-party works hosted by government or for non-U.S. rights.
  Source: `https://www.govinfo.gov/content/pkg/USCODE-2024-title17/html/USCODE-2024-title17-chap1-sec105.htm`.

## Per-asset assessment

| Asset | Government authorship evidence | Separate limitation |
|---|---|---|
| 33-8591 | SEC heading, release identifiers, final-rule agency attribution, Commission rule text | Quotes/references to outside research are not assigned the package license; no seal/artwork license claimed |
| 33-8876 | Same SEC/final-rule attribution and agency amendments | Outside comments/citations retain their provenance; no blanket foreign-rights conclusion |
| Transition FAQ | SEC-hosted Division of Corporation Finance staff attribution, modified 2005-09-13 | HTML references SEC banner/seal image paths; those image assets are NOT copied and must not be auto-loaded or used in product branding |

The SEC policy supports reproduction of the regulatory information. It does not
give this project a trademark license or a software-license grant over the source
documents. This assessment is not legal advice. Snapshots are kept separate from the
code, SEC authorship is cited and no affiliation or approval is claimed. No snapshot
file is distributed with this package.

## Source identity check

`tools/verify_rule_sources.py --sources DIR` computes the SHA-256 of the three files
named in the bundled manifest inside `DIR` and compares them with the pinned hashes.
Obtain the documents from the SEC URLs listed in the manifest and keep their manifest
filenames. Copies retrieved today can differ in bytes from the pinned snapshots; the
check then reports a mismatch. A match proves byte identity only, not current law or
completeness of the historical rule chain. The check makes no network request.
