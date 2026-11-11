"""Draft validated tool sequences with the supervisor's provider-aware adapter."""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from jsonschema import SchemaError, ValidationError, validate

from src.core.tools import failure
from src.infrastructure.repositories import persona_store
from src.shared.logger import setup_logger

logger = setup_logger("RoutineBuilder")


class RoutineBuilder:
    def __init__(self, adapter):
        self._adapter = adapter

    def _build_catalog_text(self, entries: List[Dict[str, Any]]) -> str:
        # Include every tool and full schema. Names alone omit types/requirements;
        # a first-N cap can hide the only tool capable of a required action.
        return json.dumps([
            {"name": e["name"], "description": e.get("description", ""),
             "parameters": e.get("parameters") or {}}
            for e in entries if e.get("name")
        ], ensure_ascii=False)

    def _parse_steps(self, raw: str, valid_tools: set) -> Optional[List[Dict[str, Any]]]:
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[[\s\S]*\]", text)
            if not match:
                return None
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        if not isinstance(parsed, list) or not parsed:
            return None
        steps = []
        for item in parsed:
            if not isinstance(item, dict):
                return None
            name = item.get("tool_name")
            label = item.get("label", name)
            params = item.get("params", {})
            if (not isinstance(name, str) or name.strip() not in valid_tools
                    or not isinstance(label, str) or not isinstance(params, dict)):
                return None
            steps.append({"tool_name": name.strip(), "label": label.strip() or name.strip(), "params": params})
        return steps

    async def draft(self, description: str, catalog_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        def failed(error):
            return {"success": False, "steps": [], "error": error}

        if not description or not description.strip():
            return failed("Description is required.")
        if not catalog_entries:
            return failed("No tools are available for routine drafting.")
        # Drafting also runs before PromptRegistry warmup, so resolve from disk.
        fragment = await asyncio.to_thread(persona_store.get_fragment, "routine_builder")
        if fragment is None:
            logger.warning("[ ROUTINE_BUILDER ] Missing persona fragment: routine_builder")
            return failure.failure(
                "Routine builder prompt is unavailable. Check the routine_builder persona fragment.",
                failure_class=failure.CONFIG,
                steps=[],
            )
        # The fragment parser strips the original prompt's trailing newline.
        system_prompt = fragment[1] + "\n"
        try:
            response = await asyncio.wait_for(self._adapter.generate(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"User goal:\n{description.strip()}\n\nAvailable tools:\n{self._build_catalog_text(catalog_entries)}"},
                ],
                tools=None,
                temperature=0.1,
                max_tokens=4096,
            ), timeout=90)
        except Exception as exc:
            logger.warning("[ ROUTINE_BUILDER ] Supervisor request failed: %s", exc)
            return failed("Supervisor model request failed. Check the selected model and provider.")
        if response.finish_reason in {"error", "length"}:
            return failed("Supervisor could not finish the routine draft. No partial steps were accepted.")

        catalog = {e["name"]: e for e in catalog_entries if e.get("name")}
        steps = self._parse_steps(response.text, set(catalog))
        if not steps:
            return failed(
                "Could not produce a complete fixed-step routine. Supply concrete inputs, "
                "or use a skill in chat for workflows that interpret assignments or earlier results."
            )
        for i, step in enumerate(steps):
            try:
                validate(step["params"], catalog[step["tool_name"]].get("parameters") or {})
            except ValidationError as exc:
                return failed(f"Step {i + 1} ({step['tool_name']}): {exc.message}")
            except SchemaError:
                return failed(f"Step {i + 1} ({step['tool_name']}) has an invalid tool schema.")
        return {"success": True, "steps": steps, "error": None}
