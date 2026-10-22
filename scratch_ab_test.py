import asyncio
import sys
from src.core.orchestration.nodes.graphrag_node import fetch_knowledge_boost
from src.core.intelligence.graph_rag_client import GraphRAGClient
from src.core.intelligence.activity_rag_client import ActivityRAGClient
from src.core.intelligence.drive_catalog_client import DriveCatalogClient
from src.core.intelligence.calendar_schedule_client import CalendarScheduleClient
from src.core.intelligence.session_history_client import SessionHistoryClient
from src.core.intelligence.entity_grounding_client import EntityGroundingClient
from src.infrastructure.tools.registry import ToolRegistry
from src.infrastructure.tools.discovery.discovery_orchestrator import ToolDiscoveryOrchestrator
import src.infrastructure.tools.selection.llm_selector as mod

async def run_ab_test():
    sys.stdout.reconfigure(encoding='utf-8')
    reg = ToolRegistry()
    orchestrator = ToolDiscoveryOrchestrator(reg)
    orchestrator.run_discovery()

    query = 'use the browser to find me a cool anime poster from an anime i like on amazon.com, add it to my cart and email me the link'
    boost = await fetch_knowledge_boost(
        query,
        GraphRAGClient(),
        activity_client=ActivityRAGClient(),
        drive_client=DriveCatalogClient(),
        session_client=SessionHistoryClient(),
        calendar_client=CalendarScheduleClient(),
        entity_client=EntityGroundingClient(reg),
    )

    shortlist_names = [
        'play_youtube_music', 'add_task', 'clear_canvas', 'search_browser_history',
        'add_issue_comment', 'search_session_history', 'get_canvas_screenshot',
        'search_images', 'add_comment', 'search_gmail', 'separate-image-layers',
        'fetch_markdown', 'update_dashboard', 'search_groupme_messages', 'snapshot_scene',
        'fetch_youtube_transcript', 'add_project', 'youtube_toolkit', 'export_scene',
        'download_file', 'add_label', 'search_outlook_emails', 'export_to_image',
        'searxng_search', 'add_calendar_event', 'get-assets', 'describe_scene',
        'render_animation', 'add_section', 'set_viewport', 'get-design-thumbnail',
        'render_chart', 'list_tasks', 'get-export-formats', 'show_youtube_video',
        'show_map', 'manage_tool_configs', 'list-comments', 'generate_presentation',
        'google_drive_edit_doc', 'generate-image', 'kanban_board', 'read_groupme_messages',
        'import-design-from-url', 'search-designs', 'update_calendar_event',
        'homework_assistant', 'get_pull_request_reviews', 'create-upload-url',
        'restore_snapshot', 'playwright_search', 'update_element', 'list_projects',
        'help', 'get-separate-image-layers-job', 'read_outlook_emails', 'get-design',
        'search_repositories', 'open_file', 'system_settings'
    ]
    tools = {n: reg._tools[n] for n in shortlist_names if n in reg._tools}
    core_tools = list(reg._core_tool_names)

    # --- Test A: Current Prompt ---
    print('=== RUNNING TEST A (CURRENT PROMPT) ===')
    picks_a = reg._llm_selector.select(query, tools, knowledge_context=boost, core_tools=core_tools)
    print('Test A Picks:', picks_a)

    # --- Test B: Tightened Prompt ---
    print('\n=== RUNNING TEST B (TIGHTENED PROMPT) ===')
    tightened_sys = (
        "You are a tool selector for Chad, an AI assistant.\n\n"
        "Given the user query, what Chad already has available, and a catalog of candidate tools, "
        "return a JSON array of ONLY additional tools needed to perform explicit user requests.\n\n"
        "Rules:\n"
        "- Return ONLY a raw JSON array of strings, e.g. [\"tool_a\"] or []\n"
        "- No explanation, no markdown, no other text\n"
        "- Do NOT select tools to look up user preferences or facts already in context (e.g. do NOT pick search_browser_history or search_session_history if personal facts/preferences are already known or not requested)\n"
        "- Do NOT pick media tools (like show_youtube_video or play_youtube_music) unless the user explicitly requested to play or watch video/music\n"
        "- Do NOT pick any permanently available core tools or specialist agents\n"
        "- Pick ONLY tools needed for explicit actions in the request (e.g. sending emails, setting alarms/calendar, modifying documents)\n"
        "- If all actions are covered by Chad's permanently available tools and specialist agents, return []"
    )

    core_with_specialists = core_tools + [
        "delegate_to_browser_agent (handles browser navigation, web stores, and cart actions)",
        "delegate_to_code_agent",
        "delegate_to_research_agent",
    ]

    old_sys = mod.SYSTEM_PROMPT
    mod.SYSTEM_PROMPT = tightened_sys
    try:
        picks_b = reg._llm_selector.select(query, tools, knowledge_context=boost, core_tools=core_with_specialists)
        print('Test B Picks:', picks_b)
    finally:
        mod.SYSTEM_PROMPT = old_sys

if __name__ == '__main__':
    asyncio.run(run_ab_test())
