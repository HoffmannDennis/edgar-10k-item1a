"""Pure annual-section patterns and diagnostic vocabulary."""

import re

RISK_VOCAB = frozenset({
    "risk", "adverse", "material", "may", "could", "factors",
    "financial", "business", "operations", "condition",
})
IBR_BODY_MAX = 500
IBR_PHRASE_RE = re.compile(
    r"can\s+be\s+found\s+in(?:\s+(?:Item|the|our))?|is\s+incorporated|"
    r"see\s+Item\s+\d|set\s+forth\s+in|is\s+described\s+in\s+Management|"
    r"information\s+(?:required\s+by\s+this\s+Item|relating\s+to\s+risk\s+factors)"
    r"\s+(?:is\s+(?:set\s+forth|presented|contained|included|described)|can\s+be\s+found)|"
    r"incorporated\s+(?:into\s+this|herein|by\s+reference)|"
    r"on\s+pages?\s+\d+(?:\s*(?:through|to|and|[-–])\s*\d+)?"
    r"\s+(?:of|in)\s+(?:this|the|our|Item)|"
    r"(?:there\s+(?:have\s+been|were)\s+)?no\s+material\s+changes|"
    r"not\s+applicable|(?:please\s+)?refer\s+to\s+(?:Item|the|Part)",
    re.IGNORECASE | re.DOTALL,
)
BACKREF_HEAD_RE = re.compile(
    r"^[. \t\n]*Risk\s+Factors\s+(of|and)\s+(this|the|our)\s+"
    r"(report|annual\s+report|Form\s+10[-\s]?K)", re.IGNORECASE,
)
BUSINESS_MARKER_RE = re.compile(
    r"(?:^|\n)[ \t]{0,30}(?:i[ \t]?tem\s+1\b[.\s:\-—–/]+business\b|"
    r"business[ \t]*(?:\n|$))", re.IGNORECASE,
)
PROPERTIES_MARKER_RE = re.compile(
    r"(?:^|\n)[ \t]{0,30}(?:i[ \t]?tem\s+2\b[.\s:\-—–/]+propert(?:y|ies)\b|"
    r"propert(?:y|ies)[ \t]*(?:\n|$))", re.IGNORECASE,
)
PAGEBREAK_LINE_RE = re.compile(
    r"(?i)\A[\s\xa0]*(?:table\s+of\s+contents|part\s+i{1,2}\b.{0,40}"
    r"|-{0,2}\s*\d{1,4}\s*-{0,2}|page\s+\d{1,4})[\s\xa0]*\Z"
)
DANGLING_CONNECTIVE_RE = re.compile(
    r"(?i)(?:\b(?:in|with|to|of|under|within|and|or|for|as|by|from|at|on|"
    r"the|our|its|their|a|an|see|per|via|including|include|such\s+as)\b|,)\s*$"
)
INLINE_CUE_RE = re.compile(
    r"(?i)(?:\bsee\b|refer\s+to|we\s+refer\s+you\s+to|"
    r"in\s+conjunction\s+with|in\s+connection\s+with|\(\s*see|"
    r"as\s+(?:described|discussed|set\s+forth|noted|outlined|reflected)|"
    r"(?:described|discussed|set\s+forth|referenced|outlined)\s+in|"
    r"incorporated\s+by\s+reference|\bin\s+item\b|in\s+detail\s+in)[^.\n]{0,60}$"
)
INLINE_PART_ITEM_TAIL_RE = re.compile(
    r"(?i)\bpart\s+i{1,2}\b\s*,?\s*(?:items?\s+\d{1,2}[a-c]?)?\s*[.,)]*\s*$"
)
RUNNING_HEADER_RE = re.compile(r"(?i)\bcontinued\)?[ \t\xa0.:]*$")
RISK_FACTORS_PATTERN = r"r[ \t]?i[ \t]?s[ \t]?k\s+f[ \t]?a[ \t]?c[ \t]?t[ \t]?o[ \t]?r[ \t]?s?\b"
ITEM_1A_LABEL_RE = re.compile(r"i[ \t]?tem\s+1\s*(?:a\b|\(\s*a\s*\))", re.I)
FULL_ITEM_1A_RE = re.compile(
    r"i[ \t]?tem\s+1\s*(?:a\b|\(\s*a\s*\))(?P<separator>[.\s:\-—–/|]+)"
    r"(?P<caption>" + RISK_FACTORS_PATTERN + r")", re.I,
)
RISK_CAPTION_RE = re.compile(RISK_FACTORS_PATTERN, re.I)
CONTINUATION_AFTER_RE = re.compile(
    r"(?i)\A[\s.:\-—–_)/]{0,6}(?:" + RISK_FACTORS_PATTERN + r")?[\s.:\-—–_)/]*"
    r"(?:,[ \t]*continued\b|\([ \t]*continued[ \t]*\))"
)
START_CROSSREF_CUE_RE = re.compile(
    r"(?i)(see|(?:discussed|described|set\s+forth|referenced|referred\s+to)\s+"
    r"(?:in|under|above|below|elsewhere)|reference\s+to|refer\s+to|incorporated\s+by\s+reference|"
    r"risks\s+identified\s+(?:below|above|in)|identified\s+below\s+or\s+in)\b[^.]{0,80}$"
)
# A period or colon after the caption ends the heading; run-in body text beyond it is not a reference.
POST_START_CROSSREF_RE = re.compile(
    r"(?i)\A[^.:\n]{0,80}(?:\babove\b|\bbelow\b|\(continued\)|"
    r"\bof this (?:report|annual report|form|filing)\b|\bof our (?:annual report|form|filing)\b|"
    r"\bsections?\s+(?:in|of)\s+this\s+(?:report|form\s+10[-\s]?K|annual\s+report|filing)\b)"
)
MAX_SECTION_CHARS = 200_000
BODY_NORMALIZATION_VERSION = "item1a-body-v1"


def is_ibr_stub(body: str) -> bool:
    return len(body) <= IBR_BODY_MAX and IBR_PHRASE_RE.search(pointer_probe(body)) is not None


# The six E2 families retain their distinct case, continuation and wrapping shapes.
_BOUND_ANCHOR = r"(?:^|\n)[ \t\xa0]{0,30}"
_FIN_TITLE_TAIL = (
    r"(?=[ \t]*(?:\n|\d|at[ \t]+(?:january|february|march|april|may|june|july|"
    r"august|september|october|november|december)|as[ \t]+of[ \t]|of[ \t]+[A-Z]))"
)
_BOUND_WS = r"[ \t\xa0]*(?:\n[ \t\xa0]*\d{0,4}[ \t\xa0]*){0,2}\n?[ \t\xa0]*"
# Sentence-like titles end their line, run into text after "." / ":", or take a parenthetical.
_HEADING_TAIL = r"(?=[ \t\xa0]*(?:[.:]?[ \t\xa0]*(?:\n|$)|[.:][ \t\xa0]|\())"
_NON_ITEM_BOUND_ALTS = (
    r"exhibit[ \t]+index\b(?=[ \t]*(?:\n|exhibit\b|\d|$))",
    r"(?:information" + _BOUND_WS + r"about" + _BOUND_WS + r"our" + _BOUND_WS + r")?"
    r"executive" + _BOUND_WS + r"officers" + _BOUND_WS + r"of" + _BOUND_WS
    + r"(?:the[ \t]+registrant|our[ \t]+\w+|the[ \t]+compan(?:y|ies))\b" + _HEADING_TAIL,
    r"information" + _BOUND_WS + r"about" + _BOUND_WS + r"our" + _BOUND_WS
    + r"executive" + _BOUND_WS + r"officers\b" + _HEADING_TAIL,
    r"executive" + _BOUND_WS + r"officers\b(?=[ \t]*(?:\n|$))",
    r"consolidated" + _BOUND_WS + r"balance" + _BOUND_WS + r"sheets?" + _FIN_TITLE_TAIL,
    r"consolidated" + _BOUND_WS + r"statements?" + _BOUND_WS + r"of" + _BOUND_WS
    + r"(?:income|operations|comprehensive[ \t]+income|cash[ \t]+flows?|"
    r"financial[ \t]+position|stockholders.?[ \t]+equity|changes[ \t]+in)" + _FIN_TITLE_TAIL,
    r"note[ \t]+\d{1,2}[ \t]*[:.—]",
    r"selected[ \t]+financial[ \t]+data\b(?=[ \t]*(?:\n|$))",
    r"signatures\b(?=[ \t]*(?:\n|$))",
    r"ex-101[ \t]+(?:instance|schema|calculation|definition|label|presentation)",
    r"(?:d\d+[a-z]*ex[\w.]*\.htm|[a-z]+-20\d{6}[\w.]*\.(?:xml|xsd|htm))\b",
    r"xbrl[ \t]+(?:taxonomy[ \t]+extension|instance[ \t]+document)\b",
)
NON_ITEM_SECTION_BOUND_RE = re.compile(_BOUND_ANCHOR + r"(?:" + "|".join(_NON_ITEM_BOUND_ALTS) + r")", re.I)
# Titles that can also open a sentence; they end a section only on a heading-shaped line.
HEADING_LINE_BOUND_RE = re.compile(_BOUND_ANCHOR + r"(?:" + "|".join((
    r"reports?" + _BOUND_WS + r"of" + _BOUND_WS + r"independent" + _BOUND_WS
    + r"(?:registered" + _BOUND_WS + r"public" + _BOUND_WS + r")?accounting"
    + _BOUND_WS + r"firm\b",
    r"cybersecurity" + _BOUND_WS + r"risk[ \t]+management[ \t]+and[ \t]+strategy\b",
    r"certification" + _BOUND_WS + r"of" + _BOUND_WS + r"(?:chief|the[ \t]+(?:principal|chief))",
    r"pursuant[ \t]+to[ \t]+(?:rule[ \t]+13a-14|section[ \t]+13[ \t]+or[ \t]+15)",
)) + r")", re.I)
MDNA_CAPS_BOUND_RE = re.compile(
    _BOUND_ANCHOR + r"MANAGEMENT[ \t]?.?[ \t]?S[ \t]+DISCUSSION[ \t]+AND[ \t]+ANALYSIS\b"
)
MDNA_TITLE_BOUND_RE = re.compile(
    _BOUND_ANCHOR + r"Management[ \t]?.?[ \t]?s[ \t]+Discussion[ \t]+and[ \t]+Analysis[ \t]+of"
    r"[ \t]+Financial[ \t]+Condition[ \t]+and[ \t]+Results[ \t]+of[ \t]+Operations"
)
MANAGING_RISK_BOUND_RE = re.compile(_BOUND_ANCHOR + r"MANAGING[ \t]+(?:GLOBAL|MARKET)[ \t]+RISK\b")
CONTROLS_AND_PROCEDURES_BOUND_RE = re.compile(
    r"(?:^|\n|\.[ \t]+)[ \t\xa0]{0,30}"
    r"Controls" + _BOUND_WS + r"and" + _BOUND_WS + r"Procedures" + _BOUND_WS
    + r"Disclosure" + _BOUND_WS + r"Controls" + _BOUND_WS + r"and" + _BOUND_WS + r"Procedures\b"
)
ENVIRONMENTAL_MATTERS_IBR_BOUND_RE = re.compile(
    _BOUND_ANCHOR + r"ENVIRONMENTAL[ \t]+MATTERS\b[\s\xa0]{0,40}"
    r"(?:We[ \t]+hereby[ \t]+|We[ \t]+)?incorporate[ \t]+by[ \t]+reference[ \t]+into[ \t]+this[ \t]+Item"
)
HEADING_SHAPED_BOUNDS = (HEADING_LINE_BOUND_RE, MDNA_TITLE_BOUND_RE)
E2_PATTERNS = (
    NON_ITEM_SECTION_BOUND_RE, HEADING_LINE_BOUND_RE, MDNA_CAPS_BOUND_RE, MDNA_TITLE_BOUND_RE,
    MANAGING_RISK_BOUND_RE, CONTROLS_AND_PROCEDURES_BOUND_RE, ENVIRONMENTAL_MATTERS_IBR_BOUND_RE,
)

_TITLE_WS = r"[ \t\xa0]+"
_CAPS_ITEM_TITLES = (
    "UNRESOLVED STAFF COMMENTS", "PROPERTIES", "LEGAL PROCEEDINGS", "MINE SAFETY DISCLOSURES",
    "SUBMISSION OF MATTERS TO A VOTE OF SECURITY HOLDERS", "SELECTED FINANCIAL DATA",
    "QUANTITATIVE AND QUALITATIVE DISCLOSURES? ABOUT MARKET RISKS?",
    "FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA", "CONTROLS AND PROCEDURES",
    "EXECUTIVE OFFICERS OF THE REGISTRANT", "INFORMATION ABOUT OUR EXECUTIVE OFFICERS",
    "MANAGEMENT[’'`]?S REPORT ON INTERNAL CONTROL OVER FINANCIAL REPORTING",
)
CAPS_ITEM_TITLE_RE = re.compile(
    r"(?m)^[ \t\xa0]*(?P<item>ITEM[ \t\xa0]+\d{1,2}[A-C]?\.?[ \t\xa0]+)?(?P<title>"
    + "|".join(title.replace(" ", _TITLE_WS) for title in _CAPS_ITEM_TITLES) + r")[ \t\xa0.]*$"
)
ANY_CASE_ITEM_TITLE_RE = re.compile(CAPS_ITEM_TITLE_RE.pattern, re.I)
CAPS_CUT_MIN_OFFSET = 200
UNCLASSIFIED_FINANCIAL_RE = re.compile(
    r"(?im)^[ \t\xa0.]*"
    r"(?:Financial[ \t]+Review|Financial[ \t]+Statements|Management[’'`]?s?[ \t]+Discussion[ \t]+and[ \t]+Analysis"
    r"|Market[ \t]+for[ \t]+(?:the[ \t]+)?(?:(?:Registrant|Company)[’'`]?s[ \t]+|Our[ \t]+)?Common[ \t]+(?:Equity|Stock)"
    r"(?:,?[ \t]+(?:and[ \t]+)?Related[ \t]+(?:Stock|Share)holder[ \t]+Matters"
    r"(?:,?[ \t]+and[ \t]+Issuer[ \t]+Purchases[ \t]+of[ \t]+Equity[ \t]+Securities)?)?"
    r"|(?:(?:Five|Ten|\d{1,2})[- \t]+Year[ \t]+)?Selected[ \t]+(?:Consolidated[ \t]+)?Financial[ \t]+Data"
    r"|Quantitative[ \t]+and[ \t]+Qualitative[ \t]+Disclosures?[ \t]+About[ \t]+Market[ \t]+Risks?)"
    r"[ \t\xa0.]*$"
)

COMBINED_HEADER_RE = re.compile(
    r"(?i)(?:^|\n)[ \t]{0,30}(?:"
    r"(?P<cat>i[ \t]?tem\s+1\s*a\b[.\s:\-—–/]+business\s+risk\s+factors)|"
    r"(?P<multi>items?\s+(?:1\b[.\s,]+(?:and\s+)?1\s*a\b|1\s*a\b[.\s,]+(?:and\s+)?2\b)))"
)
# Discovery stops at the title. Delimiter consumption is shared and bounded;
# neither a same-line preamble nor punctuation on the next body line is part of it.
_RISK_HEADING_TAIL = r"(?=[ \t]*(?:[.:]|\n|$)|[ \t]+(?=[(\"']))"
COMBINED_RISK_SUBHEAD_RE = re.compile(
    r"(?:^|\n)[ \t]{0,30}" + RISK_FACTORS_PATTERN + _RISK_HEADING_TAIL, re.I,
)
BARE_RISK_HEADING_RE = re.compile(
    r"(?im)(?:^|\n)[ \t]{0,30}(?:1\s*(?:a\b|\(\s*a\s*\))[\s.:\-—–_)/]+)?"
    + RISK_FACTORS_PATTERN + _RISK_HEADING_TAIL
)
_RECOVERY_RISK_FACTORS_PAT = r"r[ \t]?i[ \t]?s[ \t]?k\s+f[ \t]?a[ \t]?c[ \t]?t[ \t]?o[ \t]?r[ \t]?s?"
RECOVERY_HEADING_RE = re.compile(
    r"(?im)(?:^|\n|[.)][ \t]+)[ \t\xa0]{0,30}(?:"
    + _RECOVERY_RISK_FACTORS_PAT + r"\s+that\s+could\s+affect\s+our\s+business\b|"
    r"(?:item[ \t]+1a[.\s:\-]*)?" + _RECOVERY_RISK_FACTORS_PAT
    + r"\b(?=[ \t]*\n|[ \t]+[A-Z(\"']))"
)
RECOVERY_LEAD_RE = re.compile(
    r"(?i)(?:forward[-\s]+looking\s+information\s+and\s+" + _RECOVERY_RISK_FACTORS_PAT
    + r"|factors[ \t]+that[ \t]+could[ \t]+cause[ \t]+(?:our|the)[ \t]+actual[ \t]+results"
    r"|you[ \t]+should[ \t]+(?:carefully[ \t]+)?consider[ \t]+(?:each[ \t]+of[ \t]+)?"
    r"the[ \t]+(?:following[ \t]+)?risks|cautionary[ \t]+statements?\b"
    r"(?![ \t\S]{0,60}(?:pursuant|private[ \t]+securities|safe[ \t]+harbor)))"
)
RECOVERY_ENUM_RE = re.compile(
    r"(?i)(?:could\s+(?:also\s+)?(?:adversely\s+|materially\s+)*"
    r"(?:affect|harm|impact|reduce|cause|result|have|lead)|material(?:ly)?\s+adverse"
    r"|adversely\s+(?:affect|impact)|may\s+(?:adversely\s+)?(?:affect|harm|impact|reduce|be\s+unable)"
    r"|risks?\s+(?:and\s+uncertainties|relating\s+to|associated\s+with|that\s+could)"
    r"|subject\s+(?:us|the\s+company|our)\s+\w+\s+to)"
)
RECOVERY_MIN_WORDS = 300
RECOVERY_MIN_VOCAB = 4
RECOVERY_MAX_BARE = 12
RECOVERY_MIN_ENUM_HEAD = 2
RECOVERY_LONG_WORDS = 1500
RECOVERY_SUSTAINED_ENUM = 20
RECOVERY_PSLRA_EXHIBIT_RE = re.compile(
    r"(?i)cautionary\s+statement\s+pursuant\s+to\s+(?:the\s+)?"
    r"private\s+securities\s+litigation\s+reform\s*act"
)
NAMED_HEADING_RE = re.compile(r"(?i)under\s+the\s+heading\s+['\"]?([A-Za-z][^'\"\n]{6,80}?)['\"]?\s+in\b")
ALTERNATIVE_HEADING_RE = re.compile(
    r"(?im)^[ \t\xa0]*(?:(?:certain|other|additional|important)" + _TITLE_WS + r")?(?:risk" + _TITLE_WS
    + r")?factors" + _TITLE_WS + r"(?:that" + _TITLE_WS + r")?(?:may|could|might)" + _TITLE_WS
    + r"affect" + _TITLE_WS + r"(?:(?:our|the" + _TITLE_WS + r"company[’'`]?s)" + _TITLE_WS
    + r")?(?:future" + _TITLE_WS + r")?(?:results|performance|operating" + _TITLE_WS
    + r"results|business)(?:" + _TITLE_WS + r"of" + _TITLE_WS + r"operations)?[ \t\xa0.:]*$"
)
TOC_ITEM_1A_RE = re.compile(
    r"(?im)^[ \t\xa0]*item[ \t\xa0]*1a\.?[ \t\xa0]+risk[ \t\xa0]+factors[ \t\xa0.]+(\d{1,3})[ \t\xa0]*$"
)
QUOTED_TITLE_RE = re.compile(r"“([^”\n]{8,200})”|\"([^\"\n]{8,200})\"")
MDNA_TITLE_RE = re.compile(r"(?i)^management[’'`]?s?\s+discussion")
POINTER_VERB_RE = re.compile(
    r"(?is)(?:is|are)\s+(?:included|incorporated|set\s+forth|contained|presented|described)"
    r"|can\s+be\s+found|incorporated\s+(?:herein\s+)?by\s+reference"
)
EXTERNAL_TARGET_RE = re.compile(
    r"(?is)\bthe\s+(?:(?:19|20)\d{2}\s+)?(?:[A-Za-z&.]+\s+){0,3}?annual\s+report(?!\s+on\s+form)"
    r"|annual\s+report\s+to\s+(?:share|stock)holders|exhibit\s*\(?\s*(?:13|99)(?![0-9])|\bproxy\s+statement"
)
SENTENCE_END_RE = re.compile(r"[.!?](?=\s+[A-Z\"(]|\s*$)")
FLS_SCOPE_DISCLAIMER_RE = re.compile(
    r"(?is)\bthis\s+form\s+10-k\b.*?\binformation\s+incorporated\s+by\s+reference(?:\s+herein)?\b"
    r".*?\bas\s+well\s+as\s+(?:our\s+)?(?:(?:19|20)\d{2}\s+)?annual\s+report\b"
    r".*?\bcontain(?:s)?\s+[“\"']?forward[-\s]looking\s+statements\b"
)
_SCOPE_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")


def first_sentence(body: str, limit: int = 600) -> str:
    head = body.lstrip()[:limit]
    match = SENTENCE_END_RE.search(head)
    return head[:match.end()] if match else head


def pointer_probe(body: str) -> str:
    """Ignore only the recognized leading filing-scope disclaimer for pointer tests.

    This is analysis text, never an emitted body. Keep any preceding text and
    following sentences, including a separately stated incorporation pointer.
    """
    clean = body.lstrip()
    scope = FLS_SCOPE_DISCLAIMER_RE.search(first_sentence(clean))
    if scope is None:
        return body
    terminator = _SCOPE_SENTENCE_END_RE.search(clean, scope.end(), min(len(clean), 600))
    end = terminator.end() if terminator else scope.end()
    return clean[:scope.start()] + clean[end:].lstrip()


IN_DOC_POINTER_VERB_RE = re.compile(
    r"(?is)\b(?:is|are)\s+(?:described|discussed|included|contained|set\s+forth|presented|provided|found)"
    r"\s+(?:in|under|within|at)\b|\bcan\s+be\s+found\b|\b(?:see|refer\s+to)\b"
    r"|\bincorporated\s+(?:herein\s+)?by\s+reference\b"
)
IN_DOC_TARGET_RE = re.compile(
    r"(?is)management[’'`]?s?\s+discussion|\bmd&a\b|\bitem\s+\d|\bunder\s+the\s+(?:heading|caption)\b"
)
ABSENT_STATEMENT_RE = re.compile(
    r"(?is)smaller\s+reporting\s+compan(?:y|ies)[^.]{0,250}?"
    r"(?:not\s+required|need\s+not|(?:is|are)\s+not\s+applicable|elected\s+not|omit)"
    r"|(?:not\s+required|need\s+not)[^.]{0,250}?smaller\s+reporting\s+compan"
)
NOT_APPLICABLE_RE = re.compile(r"(?i)\A\s*not\s+applicable[.\s]*\Z")
EX99_POINTER_RE = re.compile(r"(?i)set\s+forth\s+in\s+exhibit\s+99")
STUB_QUALITY_FLAGS = frozenset({"IBR_STUB", "REVIEW_LENGTH", "REVIEW_BACKREF_HEAD"})
EX13_TYPE_RE = re.compile(r"(?i)\AEX-13(?![0-9])")
EX99_TYPE_RE = re.compile(r"(?i)\AEX-99(?![0-9])")
EXHIBIT_MIN_CHARS = 1500
EXHIBIT_REFERENCE_RE = re.compile(
    r"(?i)\bex(?:hibit|h\.?)?[\s\-]*\(?\s*(?:13|99)(?![0-9])|annual\s+report\s+to\s+(?:share|stock)holders"
)
EXHIBIT_RISK_HEADING_RE = re.compile(
    r"(?m)^[ \t]*(?:RISK[ \t]+FACTORS[ \t]*(?:\n|(?=[A-Z][a-z]))|Risk[ \t]+Factors[ \t]*\n)"
)
ITEM_1A_XREF_INDEX_RE = re.compile(
    r"i[ \t]?tem\s+1\s*a\b[.\s:\-—–/]*" + RISK_FACTORS_PATTERN
    + r"\s*(?P<pages>\d{1,4}(?:\s*[-–]\s*\d{1,4})?)\s*i[ \t]?tem\s+1\s*b\b", re.I,
)
