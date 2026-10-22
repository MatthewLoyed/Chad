# Reply Directives Guide

Reply directives enable AI agents to embed structured metadata within text responses using inline annotations.

## Overview

**Pattern Source:** OpenClaw/Pi agent SDK
**Implementation:** Phase 2.3 - Reply Directives

Reply directives allow the AI to request specific delivery modes or attach metadata to responses without breaking the streaming output.

## Syntax

```
[[directive_type:data]]
[[directive_type:data|metadata]]
```

## Supported Directives

### 1. Media Directive - `[[media:url]]`
Embed media (images, videos, audio) in the response.

**Examples:**
```
"Here's the screenshot: [[media:screenshot.png]]"
"Check out this video: [[media:https://example.com/video.mp4]]"
"[[media:chart.png]] shows the results from the analysis."
```

**Handler Example:**
```python
async def handle_media(directive: ReplyDirective):
    media_url = directive.data
    await display_media(media_url)

orchestrator.register_directive_handler("media", handle_media)
```

---

### 2. Voice Directive - `[[voice]]`
Request text-to-speech for a section of the response.

**Examples:**
```
"Let me explain this verbally [[voice]]"
"[[voice]] This is important information that should be spoken."
```

**Handler Example:**
```python
async def handle_voice(directive: ReplyDirective):
    # Current section should be spoken via TTS
    await tts_engine.speak(current_text)

orchestrator.register_directive_handler("voice", handle_voice)
```

---

### 3. Reply Directive - `[[reply:message_id]]`
Reply to a specific message (threading).

**Examples:**
```
"[[reply:msg_123]] Great question! Here's the answer..."
"[[reply:msg_456]] Following up on your earlier request..."
```

**Handler Example:**
```python
def handle_reply(directive: ReplyDirective):
    message_id = directive.data
    set_reply_thread(message_id)

orchestrator.register_directive_handler("reply", handle_reply)
```

---

### 4. Reaction Directive - `[[reaction:emoji]]`
Add a reaction to a message.

**Examples:**
```
"Task completed! [[reaction:✅]]"
"That's amazing! [[reaction:🎉]]"
"Hmm, let me think... [[reaction:🤔]]"
```

**Handler Example:**
```python
def handle_reaction(directive: ReplyDirective):
    emoji = directive.data
    add_reaction_to_message(emoji)

orchestrator.register_directive_handler("reaction", handle_reaction)
```

---

### 5. File Directive - `[[file:path]]`
Attach a file to the response.

**Examples:**
```
"I've prepared the report: [[file:report.pdf]]"
"[[file:config.yaml]] Here's the configuration file."
```

**Handler Example:**
```python
async def handle_file(directive: ReplyDirective):
    file_path = directive.data
    await attach_file(file_path)

orchestrator.register_directive_handler("file", handle_file)
```

---

### 6. Action Directive - `[[action:action_id|button_text]]`
Create interactive buttons.

**Examples:**
```
"Would you like to proceed? [[action:confirm|Yes]] [[action:cancel|No]]"
"[[action:open_settings|Open Settings]] to configure this feature."
```

**Handler Example:**
```python
def handle_action(directive: ReplyDirective):
    action_id = directive.data
    button_text = directive.metadata.get('text', 'Click')
    create_button(button_text, action_id)

orchestrator.register_directive_handler("action", handle_action)
```

---

### 7. Link Directive - `[[link:url|text]]`
Create clickable links with custom text.

**Examples:**
```
"Read more: [[link:https://docs.example.com|Documentation]]"
"[[link:https://github.com/repo|View on GitHub]]"
```

**Handler Example:**
```python
def handle_link(directive: ReplyDirective):
    url = directive.data
    link_text = directive.metadata.get('text', url)
    create_hyperlink(link_text, url)

orchestrator.register_directive_handler("link", handle_link)
```

---

## Usage Examples

### Basic Parsing

```python
from src.core.intelligence.reply_parser import parse_response

text = "Here's the image [[media:test.png]] showing the results."
parsed = parse_response(text)

print(parsed.cleaned_text)  # "Here's the image showing the results."
print(parsed.directives)    # [ReplyDirective(MEDIA: test.png)]
print(parsed.has_media)     # True
```

### In ConversationOrchestrator

The orchestrator automatically parses directives from model responses:

```python
# Directives are parsed automatically
turn_context = orchestrator.current_turn
if turn_context.parsed_response:
    directives = turn_context.parsed_response.directives
    cleaned_text = turn_context.parsed_response.cleaned_text
```

### Registering Handlers

```python
# Register handlers for specific directive types
orchestrator.register_directive_handler("media", handle_media)
orchestrator.register_directive_handler("voice", handle_voice)
orchestrator.register_directive_handler("reaction", handle_reaction)

# List registered handlers
handlers = orchestrator.list_directive_handlers()
print(handlers)  # ['media', 'voice', 'reaction']

# Unregister handler
orchestrator.unregister_directive_handler("voice")
```

### Complex Example

```python
async def handle_media(directive: ReplyDirective):
    """Display media in the UI."""
    media_url = directive.data
    await ui.display_media(media_url)

async def handle_voice(directive: ReplyDirective):
    """Enable TTS for this section."""
    await tts.enable()

def handle_reaction(directive: ReplyDirective):
    """Add reaction emoji."""
    emoji = directive.data
    ui.add_reaction(emoji)

# Register all handlers
orchestrator.register_directive_handler("media", handle_media)
orchestrator.register_directive_handler("voice", handle_voice)
orchestrator.register_directive_handler("reaction", handle_reaction)

# Model response: "Here's the screenshot [[media:test.png]] - let me explain [[voice]]"
# Parsed automatically:
#   - Cleaned text: "Here's the screenshot - let me explain"
#   - Directives: [MEDIA(test.png), VOICE()]
#   - Handlers called: handle_media(test.png), handle_voice()
```

---

## Advanced Features

### Accessing Directive Metadata

```python
directive = parsed.directives[0]
print(directive.directive_type)  # DirectiveType.MEDIA
print(directive.data)            # "test.png"
print(directive.metadata)        # {}
print(directive.position)        # Character position in original text
print(directive.raw)             # "[[media:test.png]]"
```

### Filtering by Type

```python
from src.core.intelligence.reply_parser import DirectiveType

# Get only media directives
media_directives = parsed.get_directives_by_type(DirectiveType.MEDIA)

# Or use parser directly
parser = get_reply_parser()
media_directives = parser.get_directives_by_type(text, DirectiveType.MEDIA)
```

### Creating Directives Programmatically

```python
parser = get_reply_parser()

# Simple directive
media_directive = parser.format_directive("media", "screenshot.png")
# Result: "[[media:screenshot.png]]"

# With metadata
action_directive = parser.format_directive(
    "action",
    "submit_form",
    {"text": "Submit"}
)
# Result: "[[action:submit_form|Submit]]"
```

---

## Integration with Existing Systems

### TTS Integration

```python
async def tts_directive_handler(directive: ReplyDirective):
    """Enable TTS when [[voice]] directive is present."""
    if tts_system.is_available():
        # Get text from current turn
        if orchestrator.current_turn:
            text = orchestrator.current_turn.parsed_response.cleaned_text
            await tts_system.speak(text)

orchestrator.register_directive_handler("voice", tts_directive_handler)
```

### Media Display Integration

```python
async def media_directive_handler(directive: ReplyDirective):
    """Display media in the GUI."""
    media_url = directive.data

    # Resolve relative paths
    if not media_url.startswith("http"):
        media_url = resolve_media_path(media_url)

    # Display in UI
    await gui.display_media(media_url)

orchestrator.register_directive_handler("media", media_directive_handler)
```

---

## Benefits

1. **Structured Metadata:** Embed metadata without breaking text flow
2. **Streaming Compatible:** Works with streaming model output
3. **Extensible:** Easy to add new directive types
4. **Handler-Based:** Flexible processing via registered handlers
5. **Multi-Modal:** Enables rich responses (voice, media, actions)
6. **OpenClaw Compatible:** Follows proven Pi SDK patterns

---

## Future Enhancements

- **Conditional Directives:** `[[media:url|if:desktop]]` (show only on desktop)
- **Directive Chaining:** `[[media:url]] [[voice]]` (show image and speak)
- **Template Directives:** `[[template:card|title=Hello]]` (render UI templates)
- **Streaming Directives:** Real-time directive parsing during streaming output
- **Directive Validation:** Schema-based validation for directive data

---

## Implementation Details

- **Parser:** `src/core/intelligence/reply_parser.py`
- **Integration:** `src/core/orchestration/conversation_orchestrator.py`
- **Pattern:** Regex-based parsing with directive type enum
- **Storage:** Directives stored in `TurnContext.parsed_response`
- **Processing:** Async handler system via `_process_directives()`

---

## Testing

```python
# Test parsing
from src.core.intelligence.reply_parser import parse_response

text = "Test [[media:img.png]] [[voice]] [[reaction:✅]]"
parsed = parse_response(text)

assert parsed.cleaned_text == "Test"
assert len(parsed.directives) == 3
assert parsed.has_media == True
assert parsed.has_voice == True
assert parsed.has_reactions == True
```

---

**Phase 2.3 Complete** ✅
Reply directives enable rich, multi-modal responses while maintaining clean text output.
