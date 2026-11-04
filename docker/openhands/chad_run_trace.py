"""Local JSONL execution events; keep artifacts outside the editable workspace."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time


class RunTrace:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events = self.directory / "events.jsonl"
        self.started = time.monotonic()

    def record(self, kind, **fields):
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.monotonic() - self.started, 3),
            "kind": kind,
            **fields,
        }
        with self.events.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def __call__(self, event):
        fields = {}
        for key in ("id", "timestamp", "action_id", "tool_call_id", "tool_name", "source"):
            value = getattr(event, key, None)
            if value is not None:
                fields[key] = str(value)
        for key in ("action", "observation", "error", "key", "value"):
            value = getattr(event, key, None)
            if value is not None:
                fields[key] = value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value)
        message = getattr(event, "llm_message", None)
        if message is not None:
            fields["message"] = {
                "role": str(getattr(message, "role", "")),
                "text": [item.text for item in getattr(message, "content", [])
                         if isinstance(getattr(item, "text", None), str)],
            }
        self.record(type(event).__name__, **fields)
