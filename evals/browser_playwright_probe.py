r"""
browser_playwright_probe.py — minimal, harness-free check of the browser_agent's
raw ingredients (issue #287).

Bypasses the supervisor / LangGraph subagent / mcp_ready_event machinery entirely.
Talks straight to:
  1. The Playwright MCP server (spawned from config.yaml mcp_servers.playwright) via
     MCPRegistry — proves the server starts and returns real tool schemas (a).
  2. The pinned browser_agent model (qwen/qwen3-vl-8b on LM Studio) via LiteLLMAdapter,
     handed those raw tool schemas and a navigation prompt, in a manual tool-call loop —
     proves the model can actually drive them (c) and that results come back usable (b).

This isolates "is it the harness (binding/routing)?" from "is it the model/tools?".
No LangGraph, no supervisor, no eval runner/judge plumbing — just the three moving parts.

Run (requires LM Studio serving qwen/qwen3-vl-8b, and Node/npx for @playwright/mcp):
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe evals\browser_playwright_probe.py
    $env:PYTHONPATH="."; .\.venv\Scripts\python.exe evals\browser_playwright_probe.py --prompt "Go to cfa.com and tell me one item on the menu."
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.tools.mcp_registry import MCPRegistry
from src.shared.config_manager import ConfigManager

DEFAULT_PROMPT = "Go to cfa.com and tell me one specific item you found on the menu."
MAX_TURNS = 12


async def build_playwright_registry() -> MCPRegistry:
    servers_cfg = ConfigManager.get("mcp_servers") or {}
    pw_cfg = servers_cfg.get("playwright")
    if not pw_cfg:
        raise RuntimeError("mcp_servers.playwright is not configured in config.yaml")

    registry = MCPRegistry()
    registry.register(
        "playwright",
        command=pw_cfg["command"],
        env=pw_cfg.get("env", {}),
        config={k: v for k, v in pw_cfg.items() if k not in ("command", "env")},
    )
    return registry


async def run_probe(prompt: str, model_name: str, max_turns: int) -> int:
    from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
    from config.model_registry import get_profile

    print(f"=== browser_playwright_probe ===\nmodel={model_name}\nprompt={prompt!r}\n")

    # --- (a) Does the Playwright MCP server hand us real tools? ---
    registry = await build_playwright_registry()
    tool_defs = await registry.get_all_tool_definitions(use_cache=False)
    print(f"[a] Playwright MCP returned {len(tool_defs)} tools:")
    for t in tool_defs:
        print(f"    - {t['name']}")
    if not tool_defs:
        print("\n[FAIL] (a) No tools returned — Playwright MCP server failed to connect/list.")
        await registry.cleanup()
        return 1

    # Chad-schema shape expected by LiteLLMAdapter.convert_tool_schemas: name/description/parameters
    schemas = [
        {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("parameters", {})}
        for t in tool_defs
    ]
    # Mirror src/tools/web/browser_scroll.py — @playwright/mcp has no scroll primitive
    # (issue #287), so this composes scroll + settle-wait + re-snapshot as one tool.
    schemas.append({
        "name": "browser_scroll",
        "description": (
            "Scroll the current browser page and return a fresh snapshot. Use this on "
            "infinite-scroll or lazy-loaded pages (e.g. search result listings) to reveal "
            "more content before reading it — do not try to scroll via browser_evaluate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["down", "up"], "description": "Default 'down'."},
                "amount": {"type": "number", "description": "Pixels to scroll. Default one viewport height."},
            },
            "required": [],
        },
    })

    # --- model setup: same pin as AGENT_CONFIGS["browser_agent"], no harness around it ---
    provider_cfg = ConfigManager.get_local_provider_config("lmstudio")
    profile = get_profile(model_name)
    adapter = LiteLLMAdapter(
        model_name=model_name,
        base_url=provider_cfg.get("base_url", "http://localhost:1234/v1"),
        api_key=provider_cfg.get("api_key", "lm-studio"),
        profile=profile,
    )

    system_prompt = (
        "You are a browser automation agent. You control a real Chrome browser via the "
        "provided tools. Use browser_navigate to go to the base domain or homepage. "
        "NEVER guess, invent, or hallucinate deep URL paths or query parameters. "
        "Read the page via the snapshot (elements are labeled with ref=eXX handles), "
        "then interact with the on-screen controls (search boxes, buttons, links) using "
        "the EXACT ref (e.g. target=\"e12\") for clicks/typing. Re-snapshot after the page "
        "changes. When you have the answer, reply with plain text and no further tool calls."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    navigated = False
    snapshotted = False
    tool_calls_made = []
    failure_counts = {}  # signature -> consecutive fail count (mirrors executor_strategy.py)

    async def call_tool(name: str, args: dict) -> str:
        if name == "browser_scroll":
            direction = args.get("direction", "down")
            amount = args.get("amount")
            sign = -1 if direction == "up" else 1
            delta = f"{sign} * ({amount if amount is not None else 'window.innerHeight'})"
            await registry.route_and_call("browser_evaluate", {"function": f"() => window.scrollBy(0, {delta})"})
            await registry.route_and_call("browser_wait_for", {"time": 1.0})
            result = await registry.route_and_call("browser_snapshot", {})
        else:
            result = await registry.route_and_call(name, args)

        from src.infrastructure.tools.execution.snapshot_pruner import prune_snapshot, render_set_of_mark
        if isinstance(result, list):
            parts = []
            for item in result:
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                elif hasattr(item, "text"):
                    parts.append(str(item.text))
                else:
                    parts.append(str(item))
            raw_str = "\n".join(parts)
        elif isinstance(result, dict) and "text" in result:
            raw_str = str(result["text"])
        elif isinstance(result, str):
            raw_str = result
        else:
            raw_str = json.dumps(result)

        file_link_match = re.search(r"\[Snapshot\]\(([^)]+\.yml)\)", raw_str)
        if file_link_match:
            try:
                import os
                from src.infrastructure.tools.execution.executor_strategy import PROJECT_ROOT
                rel_path = file_link_match.group(1).replace("\\", os.sep).replace("/", os.sep)
                with open(os.path.join(PROJECT_ROOT, rel_path), "r", encoding="utf-8") as f:
                    yaml_content = f.read()
                pruned, orig, kept = prune_snapshot(yaml_content)
                som_menu = render_set_of_mark(pruned)
                return f"{som_menu}\n\n### Page Snapshot\n```yaml\n{pruned}\n```"
            except Exception:
                pass
        inline_match = re.search(r"```yaml\n(.*?)```", raw_str, re.DOTALL)
        if inline_match:
            pruned, orig, kept = prune_snapshot(inline_match.group(1))
            som_menu = render_set_of_mark(pruned)
            return f"{som_menu}\n\n### Page Snapshot\n```yaml\n{pruned}\n```"

        if len(raw_str) > 25000:
            raw_str = raw_str[:25000] + "\n\n... [TRUNCATED to fit context budget] ..."

        return raw_str

    for turn in range(1, max_turns + 1):
        print(f"\n--- turn {turn} ---")
        resp = await adapter.generate(messages=messages, tools=schemas, temperature=0.3, max_tokens=4096, extra_litellm_body={"reasoning_effort": "high"})

        if resp.reasoning:
            print(f"[thinking len={len(resp.reasoning)}] {resp.reasoning[:800]}")
        if not resp.tool_calls and not (resp.text or "").strip():
            print(f"[debug] finish_reason={resp.finish_reason} raw_type={type(resp.raw)} usage={resp.usage}")

        if resp.finish_reason == "tool_calls" and resp.tool_calls:
            messages.append(adapter.build_assistant_tool_call_message(resp.tool_calls, resp.text))
            for tc in resp.tool_calls:
                name, args = tc["name"], tc.get("args", {})
                tool_calls_made.append(name)
                print(f"[tool_call] {name}({json.dumps(args)})")
                if name == "browser_navigate":
                    navigated = True
                    snapshotted = True
                if name == "browser_snapshot":
                    snapshotted = True
                sig = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
                if failure_counts.get(sig, 0) >= 2:
                    result_str = (
                        f"BLOCKED: '{name}' with these exact arguments has already failed "
                        f"{failure_counts[sig]} times in a row with the same error. Repeating it "
                        "again will not work. Call browser_snapshot (or browser_wait_for) to "
                        "re-check the current page state before retrying, or use a different "
                        "element/approach."
                    )
                    print(f"[loop_breaker] blocked repeat of {sig}")
                else:
                    try:
                        result_str = await call_tool(name, args)
                    except Exception as e:
                        result_str = f"ERROR: {e}"
                    is_error = "error" in result_str.lower()[:300] or "ERROR:" in result_str[:20]
                    failure_counts[sig] = failure_counts.get(sig, 0) + 1 if is_error else 0
                preview = result_str[:500] + ("..." if len(result_str) > 500 else "")
                print(f"[tool_result] {preview}")
                # Compact earlier snapshots and SoM menus in message history to prevent context blowout
                if "### Page Snapshot" in result_str or "### Interactive Elements" in result_str:
                    for prev_m in messages:
                        prev_c = prev_m.get("content")
                        if isinstance(prev_c, str):
                            if "### Page Snapshot\n```yaml\n" in prev_c:
                                prev_c = re.sub(
                                    r"### Page Snapshot\n```yaml\n.*?\n```",
                                    "[Page snapshot from earlier turn omitted — see latest snapshot]",
                                    prev_c,
                                    flags=re.DOTALL,
                                )
                            if "### Interactive Elements (Set-of-Mark)" in prev_c:
                                prev_c = re.sub(
                                    r"### Interactive Elements \(Set-of-Mark\).*?(?=\n\n|\Z)",
                                    "[Interactive elements menu from earlier turn omitted]",
                                    prev_c,
                                    flags=re.DOTALL,
                                )
                            prev_m["content"] = prev_c
                messages.append(adapter.build_tool_result_message(tc["id"], name, result_str))
            continue

        print(f"[finish_reason] {resp.finish_reason}")
        print(f"[final_answer] {resp.text}")
        print(f"\n=== SUMMARY ===")
        print(f"tools_received: {len(schemas)}")
        print(f"tool_calls_made: {tool_calls_made}")
        print(f"navigated: {navigated}, snapshotted: {snapshotted}")
        ok = navigated and snapshotted and bool((resp.text or "").strip())
        print(f"PASS: {ok}")
        await registry.cleanup()
        return 0 if ok else 1

    print("\n[FAIL] Hit max_turns without a final answer.")
    await registry.cleanup()
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default="qwen/qwen3-vl-8b")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS)
    args = parser.parse_args()
    sys.exit(asyncio.run(run_probe(args.prompt, args.model, args.max_turns)))


if __name__ == "__main__":
    main()
