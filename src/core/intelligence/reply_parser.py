"""
Reply Directive Parser - Structured metadata in text responses (Pi/OpenClaw Pattern)

Parses inline directives from AI responses to enable rich, multi-modal output.

Directive Syntax:
    [[directive_type:data]]

Supported Directives:
    - [[media:url]] - Embed media (images, videos, audio)
    - [[voice]] - Request text-to-speech for this section
    - [[reply:message_id]] - Reply to specific message (threading)
    - [[reaction:emoji]] - Add reaction to message
    - [[file:path]] - Attach file to response
    - [[action:button_text|action_id]] - Interactive button
    - [[link:url|text]] - Clickable link with custom text

Examples:
    "Here's the image [[media:screenshot.png]] showing the results."
    "Let me explain this via voice [[voice]]"
    "[[reply:msg_123]] Great question! Here's the answer..."
    "Task completed! [[reaction:✅]]"

OpenClaw/Pi Pattern Source:
    - Inline directives from Pi agent SDK
    - Enables structured metadata without breaking streaming output
    - Agents can request specific delivery modes inline
"""

import re
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from src.shared.logger import setup_logger

logger = setup_logger("ReplyParser")


class DirectiveType(Enum):
    """Types of reply directives."""
    MEDIA = "media"           # Embed media (image, video, audio)
    VOICE = "voice"           # Text-to-speech request
    REPLY = "reply"           # Reply to message (threading)
    REACTION = "reaction"     # Add reaction emoji
    FILE = "file"             # Attach file
    ACTION = "action"         # Interactive button
    LINK = "link"             # Clickable link
    LEARN = "learn"           # Proactive memory logging
    DONE = "done"             # Explicit task completion signal
    UNKNOWN = "unknown"       # Unrecognized directive


@dataclass
class ReplyDirective:
    """
    Parsed reply directive with metadata.

    Attributes:
        directive_type: Type of directive
        data: Primary data (url, emoji, message_id, etc.)
        metadata: Additional metadata (button text, link text, etc.)
        position: Character position in original text
        raw: Raw directive string (for debugging)
    """
    directive_type: DirectiveType
    data: str
    metadata: Dict[str, str]
    position: int
    raw: str

    def __repr__(self) -> str:
        return f"ReplyDirective({self.directive_type.value}: {self.data})"


class ReplyDirectiveParser:
    """
    Parses inline directives from AI responses.

    Directive Format:
        [[type:data]]
        [[type:data|metadata]]

    Examples:
        [[media:https://example.com/image.png]]
        [[voice]]
        [[reply:msg_123]]
        [[reaction:✅]]
        [[action:Submit|submit_form]]
        [[link:https://example.com|Click here]]
    """

    # Regex pattern for directives: [[type:data]] or [[type:data|metadata]]
    DIRECTIVE_PATTERN = re.compile(
        r'\[\[([a-zA-Z_]+)(?::([^\]|]+))?(?:\|([^\]]+))?\]\]'
    )

    def __init__(self):
        """Initialize reply directive parser."""
        logger.debug("ReplyDirectiveParser initialized")

    def parse(self, text: str) -> Tuple[str, List[ReplyDirective]]:
        """
        Parse text and extract directives.

        Args:
            text: Text containing directives

        Returns:
            Tuple of (cleaned_text, directives)
            - cleaned_text: Text with directive markup removed
            - directives: List of parsed ReplyDirective objects

        Example:
            >>> parser.parse("Here's the image [[media:test.png]] for you")
            ("Here's the image for you", [ReplyDirective(MEDIA: test.png)])
        """
        if not text:
            return "", []

        directives = []
        cleaned_text = text

        # Find all directive matches
        for match in self.DIRECTIVE_PATTERN.finditer(text):
            directive_type_str = match.group(1)  # type (media, voice, etc.)
            data = match.group(2) or ""          # primary data
            metadata_str = match.group(3) or ""  # additional metadata

            # Parse metadata
            metadata = self._parse_metadata(metadata_str)

            # Determine directive type
            try:
                directive_type = DirectiveType(directive_type_str.lower())
            except ValueError:
                directive_type = DirectiveType.UNKNOWN
                logger.warning(f"Unknown directive type: {directive_type_str}")

            # Create directive object
            directive = ReplyDirective(
                directive_type=directive_type,
                data=data.strip(),
                metadata=metadata,
                position=match.start(),
                raw=match.group(0)
            )

            directives.append(directive)

        # Remove directive markup from text
        cleaned_text = self.DIRECTIVE_PATTERN.sub('', text)

        # Clean up extra whitespace
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()

        logger.debug(
            f"Parsed {len(directives)} directives from text "
            f"(original: {len(text)} chars, cleaned: {len(cleaned_text)} chars)"
        )

        return cleaned_text, directives

    def _parse_metadata(self, metadata_str: str) -> Dict[str, str]:
        """
        Parse metadata string into dictionary.

        Supports key=value pairs separated by commas.
        If no '=', treats entire string as single value with key 'text'.

        Examples:
            "button_text" -> {"text": "button_text"}
            "text=Click,color=blue" -> {"text": "Click", "color": "blue"}
        """
        if not metadata_str:
            return {}

        metadata = {}

        # Check if it contains key=value pairs
        if '=' in metadata_str:
            # Parse key=value pairs
            for pair in metadata_str.split(','):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    metadata[key.strip()] = value.strip()
        else:
            # Single value, use 'text' as key
            metadata['text'] = metadata_str.strip()

        return metadata

    def extract_directives(self, text: str) -> List[ReplyDirective]:
        """
        Extract directives without cleaning text.

        Args:
            text: Text containing directives

        Returns:
            List of ReplyDirective objects
        """
        _, directives = self.parse(text)
        return directives

    def clean_text(self, text: str) -> str:
        """
        Remove directive markup from text.

        Args:
            text: Text with directives

        Returns:
            Cleaned text with directives removed
        """
        cleaned_text, _ = self.parse(text)
        return cleaned_text

    def has_directives(self, text: str) -> bool:
        """
        Check if text contains any directives.

        Args:
            text: Text to check

        Returns:
            True if directives found, False otherwise
        """
        return bool(self.DIRECTIVE_PATTERN.search(text))

    def get_directives_by_type(
        self,
        text: str,
        directive_type: DirectiveType
    ) -> List[ReplyDirective]:
        """
        Extract directives of a specific type.

        Args:
            text: Text containing directives
            directive_type: Type to filter by

        Returns:
            List of directives matching the type
        """
        directives = self.extract_directives(text)
        return [d for d in directives if d.directive_type == directive_type]

    def format_directive(
        self,
        directive_type: str,
        data: str,
        metadata: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Format a directive string for embedding in text.

        Args:
            directive_type: Type of directive (media, voice, etc.)
            data: Primary data
            metadata: Optional metadata dictionary

        Returns:
            Formatted directive string

        Example:
            >>> parser.format_directive("media", "test.png")
            "[[media:test.png]]"
            >>> parser.format_directive("action", "submit", {"text": "Submit Form"})
            "[[action:submit|Submit Form]]"
        """
        if metadata:
            # Format metadata
            if len(metadata) == 1 and 'text' in metadata:
                # Single text value
                metadata_str = metadata['text']
            else:
                # Multiple key=value pairs
                metadata_str = ','.join(f"{k}={v}" for k, v in metadata.items())

            return f"[[{directive_type}:{data}|{metadata_str}]]"
        else:
            if data:
                return f"[[{directive_type}:{data}]]"
            else:
                return f"[[{directive_type}]]"


@dataclass
class ParsedResponse:
    """
    Complete parsed response with text and directives.

    Attributes:
        original_text: Original text with directives
        cleaned_text: Text with directive markup removed
        directives: List of parsed directives
        has_media: Quick check for media directives
        has_voice: Quick check for voice directives
        has_actions: Quick check for action directives
    """
    original_text: str
    cleaned_text: str
    directives: List[ReplyDirective]

    @property
    def has_media(self) -> bool:
        """Check if response contains media directives."""
        return any(d.directive_type == DirectiveType.MEDIA for d in self.directives)

    @property
    def has_voice(self) -> bool:
        """Check if response contains voice directives."""
        return any(d.directive_type == DirectiveType.VOICE for d in self.directives)

    @property
    def has_actions(self) -> bool:
        """Check if response contains action directives."""
        return any(d.directive_type == DirectiveType.ACTION for d in self.directives)

    @property
    def has_reactions(self) -> bool:
        """Check if response contains reaction directives."""
        return any(d.directive_type == DirectiveType.REACTION for d in self.directives)

    @property
    def has_done(self) -> bool:
        """Check if response contains the explicit completion signal."""
        return any(d.directive_type == DirectiveType.DONE for d in self.directives)

    def get_directives_by_type(self, directive_type: DirectiveType) -> List[ReplyDirective]:
        """Get all directives of a specific type."""
        return [d for d in self.directives if d.directive_type == directive_type]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'original_text': self.original_text,
            'cleaned_text': self.cleaned_text,
            'directives': [
                {
                    'type': d.directive_type.value,
                    'data': d.data,
                    'metadata': d.metadata,
                    'position': d.position
                }
                for d in self.directives
            ],
            'has_media': self.has_media,
            'has_voice': self.has_voice,
            'has_actions': self.has_actions,
            'has_reactions': self.has_reactions,
            'has_done': self.has_done
        }


def parse_response(text: str) -> ParsedResponse:
    """
    Convenience function to parse a response.

    Args:
        text: Response text with directives

    Returns:
        ParsedResponse object

    Example:
        >>> response = parse_response("Here's an image [[media:test.png]] for you")
        >>> response.cleaned_text
        "Here's an image for you"
        >>> response.has_media
        True
    """
    parser = ReplyDirectiveParser()
    cleaned_text, directives = parser.parse(text)

    return ParsedResponse(
        original_text=text,
        cleaned_text=cleaned_text,
        directives=directives
    )


# Global singleton parser instance
_parser: Optional[ReplyDirectiveParser] = None


def get_reply_parser() -> ReplyDirectiveParser:
    """
    Get the global ReplyDirectiveParser instance.

    Returns:
        Singleton ReplyDirectiveParser instance
    """
    global _parser

    if _parser is None:
        _parser = ReplyDirectiveParser()

    return _parser
