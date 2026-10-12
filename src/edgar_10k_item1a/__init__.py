"""Offline annual-filing risk-factor extraction from explicit input bytes."""

from .api import extract_item1a
from .models import DocumentInput, FilingInput, Item1AExtraction, SourceRef, TextSpan, TextStatus

__all__ = [
    "DocumentInput", "FilingInput", "Item1AExtraction", "SourceRef", "TextSpan",
    "TextStatus", "extract_item1a",
]
