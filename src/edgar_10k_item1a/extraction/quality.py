"""Deterministic text diagnostics, never a proof of section correctness."""

import re

from .constants import BACKREF_HEAD_RE, BUSINESS_MARKER_RE, PROPERTIES_MARKER_RE, RISK_VOCAB, is_ibr_stub

LENGTH_FLOOR = 200
LENGTH_SHORT_THRESHOLD = 2_000
WORD_DENSITY_LO = 0.13
WORD_DENSITY_HI = 0.20
SENTENCE_TAIL_CHARS = 200
MIN_RISK_VOCAB_HITS = 3
CONTAMINATION_WINDOW = 1_500
OVER_LONG_CHAR_THRESHOLD = 30_000
SENTENCE_TERMINATORS = frozenset(".!?;:\")")
CONTAMINATION_RE = re.compile(
    r"\bItem\s+1\s*B\b|\bItem\s+2\.\s|\bUnresolved\s+Staff\s+Comments\b", re.I,
)


def check_sentence_terminator(body: str) -> bool:
    tail = body.rstrip()
    return bool(tail) and (
        tail[-1] in SENTENCE_TERMINATORS
        or any(char in SENTENCE_TERMINATORS for char in tail[-SENTENCE_TAIL_CHARS:])
    )


def count_risk_vocab(body: str) -> int:
    lower = body.lower()
    return sum(term in lower for term in RISK_VOCAB)


def check_ibr(body: str) -> bool:
    return is_ibr_stub(body)


def check_backref_head(body: str) -> bool:
    return BACKREF_HEAD_RE.search(body[:1000]) is not None


def check_contamination(body: str) -> bool:
    return CONTAMINATION_RE.search(body[-CONTAMINATION_WINDOW:]) is None


def check_over_long(body: str) -> bool:
    return (
        len(body) > OVER_LONG_CHAR_THRESHOLD
        and BUSINESS_MARKER_RE.search(body) is not None
        and PROPERTIES_MARKER_RE.search(body) is not None
    )


def compute_word_density(body: str) -> tuple[int, float]:
    words = len(re.findall(r"\b\w+\b", body))
    return words, words / len(body) if body else 0.0


def classify(char_count: int, word_density: float, sentence_ok: bool,
             no_contamination: bool, risk_hits: int, ibr: bool,
             backref_head: bool = False, over_long: bool = False) -> str:
    if ibr:
        return "IBR_STUB"
    if backref_head:
        return "REVIEW_BACKREF_HEAD"
    if over_long:
        return "OVER_LONG"
    if char_count < LENGTH_FLOOR:
        return "REVIEW_LENGTH"
    if not sentence_ok:
        return "REVIEW_TRUNCATION"
    if not no_contamination:
        return "REVIEW_CONTAMINATION"
    if risk_hits < MIN_RISK_VOCAB_HITS:
        return "REVIEW_VOCABULARY"
    if not WORD_DENSITY_LO <= word_density <= WORD_DENSITY_HI:
        return "REVIEW_DENSITY"
    return "PASS_SHORT" if char_count < LENGTH_SHORT_THRESHOLD else "PASS"


def quality_flag(body: str) -> str:
    _, density = compute_word_density(body)
    return classify(
        len(body), density, check_sentence_terminator(body), check_contamination(body),
        count_risk_vocab(body), check_ibr(body), check_backref_head(body), check_over_long(body),
    )
