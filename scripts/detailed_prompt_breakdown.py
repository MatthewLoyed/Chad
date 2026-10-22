import sys, os, asyncio, json
from pathlib import Path

# Force UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tiktoken
enc = tiktoken.get_encoding("cl100k_base")

def tok(text: str) -> int:
    if not text:
        return 0
    return len(enc.encode(text))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.repositories.prompt_registry import PromptRegistry
from src.core.intelligence.prompt_builder import SystemPromptBuilder
from src.core.orchestration.turn.state_builder import TurnStateBuilder
from src.core.skill_system.skill_loader import get_skill_loader
from src.infrastructure.tools.mcp_roster import build_active_mcp_roster
from src.infrastructure.tools.registry import ToolRegistry
from src.core.orchestration.agents.factory import AGENT_CONFIGS
from src.core.orchestration.handoffs import create_handoff_tools
from langchain_core.utils.function_calling import convert_to_openai_tool
from src.core.intelligence.graph_rag_client import GraphRAGClient
from src.core.intelligence.activity_rag_client import ActivityRAGClient
from src.core.intelligence.drive_catalog_client import DriveCatalogClient
from src.core.intelligence.session_history_client import SessionHistoryClient
from src.core.intelligence.calendar_schedule_client import CalendarScheduleClient
from src.core.intelligence.entity_grounding_client import EntityGroundingClient
from src.core.orchestration.nodes.graphrag_node import fetch_knowledge_boost

async def analyze_query(query: str):
    print(f"\n=======================================================")
    print(f"ANALYZING QUERY: {query}")
    print(f"=======================================================\n")
    
    # 1. Warm up prompt registry
    reg = PromptRegistry.get_instance()
    await reg.warmup()
    
    # 2. Measure individual persona fragments
    matched_prompts = reg.get_prompts_by_scopes(["supervisor", "always"])
    print("--- 1. SYSTEM PERSONA FRAGMENTS ---")
    persona_total_chars = 0
    persona_total_tokens = 0
    for pid, content in sorted(matched_prompts.items()):
        chars = len(content)
        tokens = tok(content)
        persona_total_chars += chars
        persona_total_tokens += tokens
        print(f"  {pid:25s}: {chars:6d} chars | {tokens:5d} tokens")
    print(f"  {'SUBTOTAL PERSONA':25s}: {persona_total_chars:6d} chars | {persona_total_tokens:5d} tokens\n")
    
    # 3. Dynamic blocks in SystemPromptBuilder
    print("--- 2. SYSTEM PROMPT CONTEXT BLOCKS ---")
    date_block = SystemPromptBuilder.current_date_block()
    core_profile = SystemPromptBuilder.core_profile_block()
    user_behavior = SystemPromptBuilder.user_behavior_block()
    upcoming_sched = SystemPromptBuilder.upcoming_schedule_block()
    
    print(f"  {'Current Date Block':25s}: {len(date_block):6d} chars | {tok(date_block):5d} tokens")
    print(f"  {'Core Profile Block':25s}: {len(core_profile):6d} chars | {tok(core_profile):5d} tokens")
    print(f"  {'User Preferences':25s}: {len(user_behavior):6d} chars | {tok(user_behavior):5d} tokens")
    print(f"  {'Upcoming Schedule':25s}: {len(upcoming_sched):6d} chars | {tok(upcoming_sched):5d} tokens")
    
    # Full System Prompt from TurnStateBuilder
    full_sys_instr = TurnStateBuilder.build_system_instruction(
        category="agent",
        turn_mode="agent",
        initiator="user",
        platform="pc",
        prompt=query,
        history=[],
        memory_engine=None,
        session_id="eval-breakdown",
    )
    print(f"  {'FULL SYS INSTR (TurnStateBuilder)':25s}: {len(full_sys_instr):6d} chars | {tok(full_sys_instr):5d} tokens\n")
    
    # 4. Orchestration Logic added by supervisor_node
    print("--- 3. ORCHESTRATION LOGIC (supervisor_node) ---")
    # 4a. Active specialist roster
    agent_dict = {name: None for name in AGENT_CONFIGS.keys()}
    handoffs = create_handoff_tools(agent_dict)
    from src.infrastructure.tools.web_search_routing import (
        DEEP_RESEARCH_AGENT_SUPERVISOR_ROSTER,
        BROWSER_AGENT_SUPERVISOR_ROSTER,
    )
    from src.infrastructure.tools.file_routing import TRAVERSAL_AGENT_SUPERVISOR_ROSTER
    _AGENT_DESCRIPTIONS = {
        "ui_automation_agent": "Clicks/types in local Windows apps and executes UI automation flows.",
        "browser_agent": BROWSER_AGENT_SUPERVISOR_ROSTER,
        "code_agent": "Light in-turn coding and UI artifacts...",
        "system_agent": "Manages local files, checks system stats, and adjusts OS settings.",
        "traversal_agent": TRAVERSAL_AGENT_SUPERVISOR_ROSTER,
        "deep_research_agent": DEEP_RESEARCH_AGENT_SUPERVISOR_ROSTER,
        "canvas_agent": "Canvas LMS specialist...",
    }
    _roster = "\n".join([
        f"- **{name}**: {_AGENT_DESCRIPTIONS.get(name, 'Specialist agent.')}"
        for name in agent_dict.keys() if name != "research_agent"
    ])
    active_specialist_block = (
        f"\n\n### 🤖 ACTIVE SPECIALIST TEAM\n"
        f"Only the following specialists are available right now. "
        f"Do NOT attempt to delegate to any agent not on this list:\n{_roster}"
    )
    print(f"  {'Active Specialist Team':25s}: {len(active_specialist_block):6d} chars | {tok(active_specialist_block):5d} tokens")
    
    # 4b. Skill catalog (on-demand)
    loader = get_skill_loader()
    if loader.is_empty:
        await loader.initialize()
    skill_catalog = loader.build_catalog_prompt(query=query)
    print(f"  {'Skill Catalog Prompt':25s}: {len(skill_catalog):6d} chars | {tok(skill_catalog):5d} tokens")
    
    # 4c. MCP roster
    registry = ToolRegistry()
    mcp_roster = build_active_mcp_roster(tool_registry=registry)
    print(f"  {'Active MCP Roster':25s}: {len(mcp_roster):6d} chars | {tok(mcp_roster):5d} tokens")
    
    orch_total_chars = len(active_specialist_block) + len(skill_catalog) + len(mcp_roster)
    orch_total_tokens = tok(active_specialist_block) + tok(skill_catalog) + tok(mcp_roster)
    print(f"  {'SUBTOTAL ORCHESTRATION':25s}: {orch_total_chars:6d} chars | {orch_total_tokens:5d} tokens\n")
    
    # 5. Knowledge Boost (GraphRAG, Activity, Drive, Session, Calendar, Grounding)
    print("--- 4. KNOWLEDGE BOOST (Trailing context) ---")
    graph_client = GraphRAGClient()
    activity_client = ActivityRAGClient()
    drive_client = DriveCatalogClient()
    session_client = SessionHistoryClient()
    calendar_client = CalendarScheduleClient()
    entity_client = EntityGroundingClient(tool_registry=registry)
    
    boost_block = await fetch_knowledge_boost(
        query,
        graph_client,
        activity_client=activity_client,
        drive_client=drive_client,
        session_client=session_client,
        calendar_client=calendar_client,
        entity_client=entity_client,
    )
    print(f"  {'Knowledge Boost Block':25s}: {len(boost_block):6d} chars | {tok(boost_block):5d} tokens\n")
    
    # 6. Tools Bound to Supervisor
    print("--- 5. TOOL SCHEMAS BOUND TO MODEL ---")
    # 6a. Handoffs
    handoff_schemas = [convert_to_openai_tool(h) for h in handoffs]
    h_chars = sum(len(json.dumps(s)) for s in handoff_schemas)
    h_tokens = sum(tok(json.dumps(s)) for s in handoff_schemas)
    print(f"  {f'Specialist Handoffs ({len(handoffs)})':25s}: {h_chars:6d} chars | {h_tokens:5d} tokens")
    for h, s in zip(handoffs, handoff_schemas):
        print(f"    - {h.name:23s}: {len(json.dumps(s)):5d} chars | {tok(json.dumps(s)):4d} tokens")
        
    # 6b. Step management tools (if plan)
    from src.core.orchestration.nodes.supervisor_node import _STEP_MGMT_TOOLS
    step_tools = [registry.get_langchain_tool_by_name(n) for n in _STEP_MGMT_TOOLS if registry.get_langchain_tool_by_name(n)]
    step_schemas = [convert_to_openai_tool(t) for t in step_tools]
    st_chars = sum(len(json.dumps(s)) for s in step_schemas)
    st_tokens = sum(tok(json.dumps(s)) for s in step_schemas)
    print(f"  {f'Step Mgmt Tools ({len(step_tools)})':25s}: {st_chars:6d} chars | {st_tokens:5d} tokens")
    
    # 6c. Selected RAG / direct tools for this query
    from src.core.orchestration.turn.tool_selector import TurnToolSelector
    selector = TurnToolSelector(tool_registry=registry)
    # Let's inspect what tools get selected for this query
    sel = await selector.select(user_input=query, category="agent")
    selected_tool_schemas = sel.schemas or []
    print(f"  {f'Selected RAG Tools ({len(selected_tool_schemas)})':25s}:")
    rag_chars = 0
    rag_tokens = 0
    for ts in selected_tool_schemas:
        # ts might be dict or tool
        tool_name = ts.get("name") if isinstance(ts, dict) else getattr(ts, "name", str(ts))
        lt = registry.get_langchain_tool_by_name(tool_name)
        if lt:
            schema = convert_to_openai_tool(lt)
            c = len(json.dumps(schema))
            t = tok(json.dumps(schema))
            rag_chars += c
            rag_tokens += t
            print(f"    - {tool_name:23s}: {c:5d} chars | {t:4d} tokens")
        else:
            c = len(json.dumps(ts))
            t = tok(json.dumps(ts))
            rag_chars += c
            rag_tokens += t
            print(f"    - {tool_name:23s}: {c:5d} chars | {t:4d} tokens")
    print(f"  {'SUBTOTAL SELECTED TOOLS':25s}: {rag_chars:6d} chars | {rag_tokens:5d} tokens\n")
    
    total_tool_tokens = h_tokens + rag_tokens
    print(f"  {'TOTAL TOOL SCHEMAS BOUND':25s}: {h_chars + rag_chars:6d} chars | {total_tool_tokens:5d} tokens\n")

    # 7. GRAND SUMMARY
    total_prompt_tokens_est = (
        tok(full_sys_instr)
        + orch_total_tokens
        + tok(boost_block)
        + total_tool_tokens
        + tok(query)
    )
    print("=======================================================")
    print("PROMPT COMPONENT BREAKDOWN (First Message):")
    print(f"  1. System Persona (Core Docs):      {tok(full_sys_instr):5d} tokens ({tok(full_sys_instr)/total_prompt_tokens_est*100:4.1f}%)")
    print(f"  2. Supervisor Orchestration Logic: {orch_total_tokens:5d} tokens ({orch_total_tokens/total_prompt_tokens_est*100:4.1f}%)")
    print(f"     - Active Specialist Team:       {tok(active_specialist_block):5d} tokens")
    print(f"     - Skill Catalog Prompt:         {tok(skill_catalog):5d} tokens")
    print(f"     - MCP Roster:                   {tok(mcp_roster):5d} tokens")
    print(f"  3. Knowledge Boost (RAG/Memory):   {tok(boost_block):5d} tokens ({tok(boost_block)/total_prompt_tokens_est*100:4.1f}%)")
    print(f"  4. Bound Tool Schemas (OpenAI):    {total_tool_tokens:5d} tokens ({total_tool_tokens/total_prompt_tokens_est*100:4.1f}%)")
    print(f"     - 7 Specialist Handoffs:        {h_tokens:5d} tokens")
    print(f"     - Selected Direct Tools:        {rag_tokens:5d} tokens")
    print(f"  5. User Query & Framing:           {tok(query):5d} tokens ({tok(query)/total_prompt_tokens_est*100:4.1f}%)")
    print(f"  -----------------------------------------------------")
    print(f"  TOTAL ESTIMATED PROMPT:            {total_prompt_tokens_est:5d} tokens")
    print("=======================================================\n")
    
    return {
        "query": query,
        "persona_tokens": tok(full_sys_instr),
        "orch_tokens": orch_total_tokens,
        "skill_catalog_tokens": tok(skill_catalog),
        "specialist_team_tokens": tok(active_specialist_block),
        "mcp_roster_tokens": tok(mcp_roster),
        "knowledge_boost_tokens": tok(boost_block),
        "tool_schemas_tokens": total_tool_tokens,
        "handoff_tokens": h_tokens,
        "rag_tools_tokens": rag_tokens,
        "user_query_tokens": tok(query),
        "total_prompt_tokens": total_prompt_tokens_est,
    }

if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "What time is it right now?"
    asyncio.run(analyze_query(q))
