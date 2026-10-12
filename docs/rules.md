# Item 1A rules: source review, not yet a legal evaluator

The package implements text extraction and SEC Archives retrieval only. No cover
parser, filing-duty evaluator or deadline checker is exposed yet.

## Reviewed authorities

| Claim | Authority and location |
|---|---|
| Annual introduction: fiscal years ending on/after 2005-12-01 | SEC transition FAQ, Question 13; Release 33-8591, pages 1/464 |
| Historical 10-KSB lacked a risk-factor item | Release 33-8876, page 18 |
| SRC exception introduced effective 2008-02-04 | Release 33-8876, pages 1–2/210; page 198 is Form 10, not Form 10-K |
| Newly qualifying SRC transition uses the next periodic report due after effectiveness | Release 33-8876, page 2; filing date alone is insufficient |
| Current SRC exception remains in the form | SEC Form 10-K retrieved 2026-10-11, page 11, Item 1A |
| Covered asset-backed issuers may omit Item 1A | Same Form 10-K, page 6, General Instruction J(1)(b) |
| Amendments concern the items amended | 17 CFR 240.12b-15, eCFR title current through 2026-10-08 |

Sources: `https://www.sec.gov/divisions/corpfin/transitionfaq.htm`,
`https://www.sec.gov/files/rules/final/33-8591.pdf`,
`https://www.sec.gov/files/rules/final/2007/33-8876.pdf`,
`https://www.sec.gov/about/forms/form10-k.pdf`,
`https://www.ecfr.gov/current/title-17/part-240/section-240.12b-15`.

“Not required” is not “text absent”. “Text missing” is not “exempt”. SRC status
and acceleration status are distinct. The observed checkbox will not certify
financial eligibility independently. The current form says “where appropriate”;
the future screening enum is not an adjudication of every substantive risk.

The manifest identifies three historical snapshots by exact bytes. A hash match
does not prove current law, complete historical coverage or a redistribution
right. Current-form cross-checking does not establish an uninterrupted amendment
chain. Future rule currency and assumptions must remain explicit.
