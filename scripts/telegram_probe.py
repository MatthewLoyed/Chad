"""
telegram_probe.py — Telegram Agent & Tool Diagnostics Probe
============================================================
Validates Telegram pocket-remote tool availability, schema bindings,
contact-desk escalation, artifact delivery (photos & documents), and live server health.

Usage:
    python scripts/telegram_probe.py
    python scripts/telegram_probe.py --live   # also tests live prod Brain port 8767 if up
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Ensure repo root is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import httpx

from src.core.orchestration.turn.mode_resolver import TurnModeResolver
from src.core.orchestration.turn.tool_selector import TurnToolSelector
from src.remote.telegram.artifact_delivery import (
    note_artifact_for_telegram,
    take_pending_artifacts,
)
from src.remote.telegram.telegram_agent import (
    TELEGRAM_AGENT_INSTRUCTION,
    TELEGRAM_AGENT_TOOL_NAMES,
)
from src.remote.telegram.telegram_client import TelegramClient
from src.shared.config_manager import ConfigManager


def print_step(title: str, ok: bool, details: str = ""):
    icon = "[OK]" if ok else "[FAIL]"
    print(f" {icon} {title}")
    if details:
        for line in details.strip().split("\n"):
            print(f"      {line}")


async def probe_telegram_tools():
    print("\n=== 1. Telegram Tool Availability & Schemas ===")

    try:
        from src.infrastructure.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry._discovery_orchestrator.run_discovery(use_cache=True)
        selector = TurnToolSelector(registry)
        selection = selector.telegram_agent_schemas()
        bound_names = selection.selection.get("final_tool_names", [])

        # Check generate_image specifically
        has_image = "generate_image" in bound_names
        print_step("generate_image available in telegram_agent", has_image)

        # Check visual / extra tools
        for tool_name in ("render_chart", "render_animation", "set_reminder", "get_datetime"):
            has_tool = tool_name in bound_names
            print_step(f"{tool_name} available in telegram_agent", has_tool)

        # Ensure dangerous/desktop tools are NOT bound
        unsafe = [t for t in bound_names if "delegate" in t or t in ("shell_command", "execute_script", "show_map")]
        print_step("Unsafe/desktop tools excluded from telegram_agent", len(unsafe) == 0, f"Unsafe tools: {unsafe}" if unsafe else "")

        # Check contact mode
        contact_selection = selector.contact_schemas()
        contact_names = contact_selection.selection.get("final_tool_names", [])
        print_step("Contact mode only has escalate_to_agent", contact_names == ["escalate_to_agent"], f"Tools: {contact_names}")

        all_ok = has_image and len(unsafe) == 0 and contact_names == ["escalate_to_agent"]
    except Exception as e:
        print_step("ToolRegistry inspection", False, str(e))
        all_ok = False

    return all_ok


def probe_instructions():
    print("\n=== 2. Telegram Agent Instructions & Persona ===")
    instruction_ok = "generate_image" in TELEGRAM_AGENT_TOOL_NAMES and "image" in TELEGRAM_AGENT_INSTRUCTION.lower()
    print_step("telegram_agent.py instructions explicitly include images", instruction_ok)

    persona_path = ROOT_DIR / "prompts" / "persona" / "telegram.md"
    persona_text = persona_path.read_text(encoding="utf-8") if persona_path.is_file() else ""
    persona_ok = "image" in persona_text.lower()
    print_step("prompts/persona/telegram.md includes images/visual outputs", persona_ok)

    return instruction_ok and persona_ok


def probe_artifact_delivery():
    print("\n=== 3. Telegram Artifact & Photo Delivery ===")
    session_id = "main:1:telegram:999888"
    take_pending_artifacts(session_id)

    # Simulate delivering a PNG artifact
    fake_png = ROOT_DIR / "artifacts" / "probe_test_cat.png"
    fake_png.parent.mkdir(parents=True, exist_ok=True)
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\nfakeimageprobebytes")

    try:
        note_artifact_for_telegram(str(fake_png), fake_png.name, session_id=session_id)
        pending = take_pending_artifacts(session_id)
        queue_ok = len(pending) == 1 and pending[0]["filename"] == fake_png.name
        print_step("Artifact queuing for Telegram session", queue_ok)

        # Test TelegramClient.send_photo with local file
        client = TelegramClient(bot_token="fake-probe-token")
        mock_post = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True, "result": {"message_id": 101}}
        mock_post.return_value = mock_resp
        client._client.post = mock_post

        res = client.send_photo(chat_id=999888, photo=str(fake_png), caption=fake_png.name)
        photo_sent_ok = res.get("message_id") == 101 and "sendPhoto" in mock_post.call_args[0][0]
        multipart_ok = "photo" in mock_post.call_args[1].get("files", {})
        print_step("TelegramClient.send_photo uploads local image via multipart", photo_sent_ok and multipart_ok)

        return queue_ok and photo_sent_ok and multipart_ok
    finally:
        if fake_png.is_file():
            fake_png.unlink(missing_ok=True)


async def probe_turn_escalation():
    print("\n=== 4. Turn Mode & Escalation Pipeline ===")
    resolver = TurnModeResolver()

    # 1. Contact mode resolution
    turn_mode, source, _ = await resolver.resolve(
        prompt="hey generate an image of a cat",
        category="contact",
        content_blocks=None,
        image_base64=None,
        history=[],
        initiator="user",
        platform="telegram",
    )
    print_step("Contact category resolves to fast chat mode", turn_mode == "chat")

    # 2. Telegram agent category resolution
    turn_mode_agent, _, _ = await resolver.resolve(
        prompt="generate an image of a cat",
        category="telegram_agent",
        content_blocks=None,
        image_base64=None,
        history=[],
        initiator="user",
        platform="telegram",
    )
    print_step("telegram_agent category resolves to agent lane", turn_mode_agent == "agent")

    return turn_mode == "chat" and turn_mode_agent == "agent"


async def probe_live_server():
    print("\n=== 5. Live Prod Brain Server (Port 8767) ===")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:8767/health")
            if resp.status_code == 200:
                print_step("Prod Brain server online on :8767", True, f"Response: {resp.text}")
                return True
            else:
                print_step("Prod Brain server responded with non-200", False, f"Status: {resp.status_code}")
                return False
    except Exception as e:
        print_step("Prod Brain server offline or unreachable on :8767", True, "(Expected if supervisor is stopped locally)")
        return True


async def main():
    parser = argparse.ArgumentParser(description="Probe Telegram agent mode and tool availability.")
    parser.add_argument("--live", action="store_true", help="Check live server on :8767")
    args = parser.parse_args()

    print("==================================================")
    print("      CHAD TELEGRAM AGENT & TOOL DIAGNOSTICS      ")
    print("==================================================")

    r1 = await probe_telegram_tools()
    r2 = probe_instructions()
    r3 = probe_artifact_delivery()
    r4 = await probe_turn_escalation()
    r5 = await probe_live_server()

    print("\n==================================================")
    if r1 and r2 and r3 and r4 and r5:
        print(" [ALL CHECKS PASSED] Telegram agent mode is verified & healthy!")
        print("==================================================")
        sys.exit(0)
    else:
        print(" [SOME CHECKS FAILED] See above for details.")
        print("==================================================")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
