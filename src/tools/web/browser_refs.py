"""Playwright snapshot references, including frame-prefixed production refs."""
import re

REF_PATTERN = r'(?:f\d+)?e\d+'

def is_browser_ref(value):
    return isinstance(value, str) and re.fullmatch(REF_PATTERN, value) is not None
