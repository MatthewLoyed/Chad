"""
Lightweight Entity Classifier — zero-latency classification of navigational targets
and external web services.

Detects when an instruction references an external web service, website, or app
(e.g., "go to my gemini notebook", "check supabase dashboard", "notion.so")
vs local operations (filesystem, calendar, smart home, pure code, conversation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Set


@dataclass(frozen=True)
class EntityClassification:
    """Outcome of entity classification on an incoming user query."""

    should_ground: bool
    entity: Optional[str] = None
    search_query: Optional[str] = None
    is_domain: bool = False
    reason: Optional[str] = None


# Known local targets, system concepts, and devices that must NEVER trigger web search grounding.
_LOCAL_EXCLUSIONS: Set[str] = {
    "download", "downloads", "document", "documents", "desktop", "file", "files",
    "folder", "folders", "directory", "directories", "repo", "repos", "repository",
    "code", "src", "git", "terminal", "bash", "powershell", "cmd", "disk", "drive",
    "trash", "recycle bin", "local", "pc", "laptop", "computer", "server", "process",
    # Smart home & personal tools
    "lamp", "light", "lights", "bedroom", "living room", "fan", "switch", "plug",
    "calendar", "schedule", "event", "events", "agenda", "meeting", "meetings",
    "todo", "todos", "task", "tasks", "routine", "routines", "reminder", "reminders",
    # Local files / common file formats
    "pdf", "docx", "txt", "csv", "xlsx", "json", "yaml", "yml", "png", "jpg",
}

# Domains / TLDs indicating explicit web addresses (handles both 'notion.so' and 'notebook .com')
_DOMAIN_PATTERN = re.compile(
    r"\b([a-zA-Z0-9_\-]+(?:\s*\.\s*[a-zA-Z0-9_\-]+)*\s*\.\s*(?:com|org|net|ai|io|app|dev|edu|gov|co|so|me|tv))\b",
    re.IGNORECASE,
)

# Common URL prefixes
_URL_PATTERN = re.compile(r"https?://[^\s]+|www\.[^\s]+", re.IGNORECASE)

# Navigational verbs targeting a named surface/app
# Delimiters stop on conjunctions/prepositions or end-of-sentence punctuation (except domain dots)
_DELIM = r"(?:\s+(?:for|and|to|in|on|at|with|\?|$)|(?:\.(?!\s*(?:com|org|net|ai|io|app|dev|edu|gov|co|so|me|tv))))"

_NAV_VERB_PATTERNS = [
    # "go to (my/the) <entity>"
    re.compile(rf"\bgo\s+to\s+(?:(?:my|the|our)\s+)?([a-zA-Z0-9_\.\- ]+?)(?:{_DELIM}|$)", re.IGNORECASE),
    # "navigate to (my/the) <entity>"
    re.compile(rf"\bnavigate\s+to\s+(?:(?:my|the|our)\s+)?([a-zA-Z0-9_\.\- ]+?)(?:{_DELIM}|$)", re.IGNORECASE),
    # "open (my/the) <entity>"
    re.compile(rf"\bopen\s+(?:(?:my|the|our)\s+)?([a-zA-Z0-9_\.\- ]+?)(?:{_DELIM}|$)", re.IGNORECASE),
    # "check (my/the) <entity> dashboard/quota/usage"
    re.compile(r"\bcheck\s+(?:(?:my|the|our)\s+)?([a-zA-Z0-9_\.\- ]+?)\s+(?:dashboard|quota|usage|account|billing|settings)\b", re.IGNORECASE),
    # "log into / sign in to (my/the) <entity>"
    re.compile(rf"\b(?:log|sign)\s*(?:in|into)\s*(?:to\s+)?(?:(?:my|the|our)\s+)?([a-zA-Z0-9_\.\- ]+?)(?:{_DELIM}|$)", re.IGNORECASE),
]

# Pure conversational or coding keywords that indicate the turn needs no web grounding
_CONVERSATIONAL_PREFIXES = (
    "hi", "hello", "hey", "howdy", "sup", "what's up", "who are you",
    "how are you", "tell me a joke", "thank you", "thanks", "good morning",
    "good evening", "good night",
)

_PURE_CODE_TRIGGERS = (
    "write a python", "write python", "write a function", "write code",
    "implement a", "how to write", "create a class", "explain the algorithm",
    "fix the syntax", "write a script", "regex for", "sql query for",
)


class EntityClassifier:
    """Fast, zero-latency classifier identifying candidate external web entities."""

    @classmethod
    def classify(cls, query: str) -> EntityClassification:
        """Classify whether the query contains an external entity needing web grounding."""
        if not query or not query.strip():
            return EntityClassification(should_ground=False, reason="empty_query")

        text = query.strip()
        lower = text.lower()

        # 1. Pure conversation check
        clean_first_words = " ".join(lower.split()[:4])
        if any(clean_first_words.startswith(prefix) for prefix in _CONVERSATIONAL_PREFIXES):
            # Unless there's a subsequent explicit navigational command
            if not any(token in lower for token in ("go to", "navigate to", ".com", ".ai", ".io")):
                return EntityClassification(should_ground=False, reason="conversational")

        # 2. Pure code generation check
        if any(trigger in lower for trigger in _PURE_CODE_TRIGGERS):
            return EntityClassification(should_ground=False, reason="pure_code")

        # 3. Explicit URL check (e.g. https://linear.app/projects)
        url_match = _URL_PATTERN.search(text)
        if url_match:
            raw_url = url_match.group(0)
            return EntityClassification(
                should_ground=True,
                entity=raw_url,
                search_query=raw_url,
                is_domain=True,
                reason="explicit_url",
            )

        # 4. Navigational verb check (e.g. "go to my gemini notebook .com", "navigate to perplexity")
        for pattern in _NAV_VERB_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                # Clean up candidate from leading possessives
                candidate_clean = re.sub(r"^(?:my|the|our)\s+", "", candidate, flags=re.IGNORECASE).strip()
                words = candidate_clean.lower().split()
                if not words:
                    continue

                # Filter out pure local targets (e.g. "go to my downloads folder", "open my calendar")
                if any(w in _LOCAL_EXCLUSIONS for w in words):
                    continue

                # Filter out obvious non-entities (single short common stop words)
                if len(candidate_clean) < 3 or candidate_clean.lower() in ("it", "there", "here", "this", "that", "home", "sleep", "bed"):
                    continue

                # Cap length to avoid dragging whole sentences
                if len(words) > 5:
                    candidate_clean = " ".join(words[:4])

                is_domain = bool(re.search(r"\.(?:com|org|net|ai|io|app|dev|edu|gov|co|so|me|tv)\b", candidate_clean, re.I))
                clean_search = re.sub(r"\s*\.(?:com|org|net|ai|io|app|dev|edu|gov|co|so|me|tv)\b", "", candidate_clean, flags=re.I).strip()
                return EntityClassification(
                    should_ground=True,
                    entity=candidate_clean,
                    search_query=f"{clean_search} official website app",
                    is_domain=is_domain,
                    reason="navigational_verb",
                )

        # 5. Standalone domain check (e.g., "log into notion.so", "visit example.com")
        domain_match = _DOMAIN_PATTERN.search(text)
        if domain_match:
            domain = domain_match.group(1).strip()
            # Check for spurious matches like "document.pdf" or "test.py"
            ext = domain.split(".")[-1].lower().strip()
            if ext not in ("pdf", "docx", "txt", "py", "sh", "bat", "exe", "json", "yml", "yaml", "md"):
                clean_domain = domain.rstrip(".,;")
                return EntityClassification(
                    should_ground=True,
                    entity=clean_domain,
                    search_query=clean_domain,
                    is_domain=True,
                    reason="domain_pattern",
                )

        # 6. Fallback: No candidate needing web grounding
        return EntityClassification(should_ground=False, reason="no_entity_detected")
