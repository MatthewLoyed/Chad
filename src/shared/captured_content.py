"""Session-scoped, immutable text for tool-to-tool transfers without LLM rewriting."""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from src.core.orchestration.context import domain_var, session_id_var
from src.shared.paths import get_project_root

TTL_SECONDS = 24 * 60 * 60
MAX_BYTES = 4 * 1024 * 1024


def _root() -> Path:
    return get_project_root() / 'artifacts' / 'captured_text'


def _owner() -> str:
    session = session_id_var.get()
    if not session:
        raise ValueError('Captured content requires an active conversation.')
    return hashlib.sha256(json.dumps([session, domain_var.get()]).encode('utf-8')).hexdigest()


def text_checksum(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def save_captured_text(text: str, source_url: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError('The selected element contains no text. Select the full message body.')
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Selected text exceeds the 4 MiB capture limit; nothing was saved.')
    owner = _owner()
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for expired in root.glob('capture_*.json'):
        try:
            if expired.stat().st_mtime < now - TTL_SECONDS:
                expired.unlink()
        except OSError:
            pass
    content_id = 'capture_' + uuid.uuid4().hex
    record = dict(content_id=content_id, text=text, source_url=source_url,
                  char_count=len(text), sha256=text_checksum(text), owner=owner, created_at=now)
    with (root / f'{content_id}.json').open('x', encoding='utf-8') as output:
        json.dump(record, output, ensure_ascii=False)
    return {key: record[key] for key in ('content_id', 'source_url', 'char_count', 'sha256')}


def load_captured_text(content_id: str) -> dict:
    if not isinstance(content_id, str) or not re.fullmatch(r'capture_[0-9a-f]{32}', content_id):
        raise ValueError('Invalid captured content ID. Use the ID returned by browser_capture_text.')
    try:
        record = json.loads((_root() / f'{content_id}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Captured content is unavailable; capture the source again.') from exc
    if record.get('owner') != _owner():
        raise ValueError('Captured content belongs to a different conversation or domain.')
    if time.time() - record['created_at'] > TTL_SECONDS:
        raise ValueError('Captured content has expired; capture the source again.')
    if text_checksum(record['text']) != record['sha256'] or len(record['text']) != record['char_count']:
        raise ValueError('Captured content failed its integrity check; capture the source again.')
    return record
