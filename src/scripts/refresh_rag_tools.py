
import asyncio
import logging
import os
from src.infrastructure.tools.registry import ToolRegistry
from src.core.tools.mcp_registry import MCPRegistry
from src.shared.config_manager import ConfigManager
from src.infrastructure.tools.cloud.supabase_bridge import SupabaseToolSyncBridge
from src.infrastructure.connectors.policy import get_effective_mcp_servers, mcp_credentials_satisfied

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RefreshTools")


def _resolve_mcp_env(env: dict) -> dict:
    return {k: (os.path.expandvars(v) if isinstance(v, str) else v) for k, v in (env or {}).items()}


def mcp_servers_for_rag_scan() -> dict:
    """
    MCP servers eligible for RAG schema scanning.

    Skips disabled servers and those missing credentials (issue #403) so a
    token-less placeholder in config.yaml cannot abort the entire tool sync.
    """
    return get_effective_mcp_servers()


async def refresh_supabase_tools():
    # 1. Initialize Registry
    registry = ToolRegistry()
    registry.discover_all(sync_cloud=False) # Don't sync yet
    
    # 2. Connect MCP Servers to get REAL schemas
    print("\n[1/3] Connecting to MCP servers to fetch full schemas...")
    mcp_registry = MCPRegistry()
    mcp_config = mcp_servers_for_rag_scan()
    skipped: list[str] = []

    # Log servers present in config but skipped (missing tokens / disabled)
    all_configured = ConfigManager.get("mcp_servers", {}) or {}
    for name, cfg in all_configured.items():
        if name in mcp_config:
            continue
        ok, missing = mcp_credentials_satisfied(cfg)
        if not ok:
            skipped.append(f"{name} (missing: {', '.join(missing)})")
        else:
            skipped.append(f"{name} (disabled)")

    if skipped:
        print(f"  Skipping {len(skipped)} MCP server(s) without credentials or disabled:")
        for label in skipped:
            print(f"    - {label}")

    for name, cfg in mcp_config.items():
        resolved_env = _resolve_mcp_env(cfg.get('env', {}))
        mcp_registry.register(name, cfg['command'], resolved_env, cfg)

    # Scanned MCP servers will now OVERWRITE local stubs thanks to my registry fix
    # Using private _servers as MCPRegistry lacks a public getter
    failed_servers = []
    for server in mcp_registry._servers.values():
        try:
            print(f"  Scanning server: {server.name}...")
            from src.infrastructure.tools.discovery.mcp_scanner import MCPScanner
            scanner = MCPScanner(registry)
            await scanner.scan_server(server)
        except Exception as e:
            print(f"  Warning: Failed to scan {server.name}: {e}")
            failed_servers.append(server.name)

    if failed_servers:
        print(
            f"\n[ ABORT ] {len(failed_servers)} credentialed MCP server(s) failed to scan: {failed_servers}. "
            "Refusing to sync — a partial scan looks identical to real tool removals and will "
            "delete live tools from Supabase (see 2026-07-05 incident). Fix the failing server(s) "
            "and re-run."
        )
        return

    # 3. Perform Aggressive Sync
    print(f"\n[2/3] Preparing to sync {registry.get_tool_count()} tools to Supabase...")
    for name in registry._tools.keys():
        print(f"  - Ready to sync: {name}")
    sync_bridge = SupabaseToolSyncBridge()
    
    # This will now use the BOOSTED descriptions I added to the bridge!
    await sync_bridge.sync_all(registry._tools)
    
    print("\n[3/3] Sync complete. Testing 'open discord' score...")
    
    # 4. Test Resulting Score
    from src.infrastructure.tools.selection.semantic_selector import SemanticToolSelector
    selector = SemanticToolSelector()
    selector._ensure_model()
    query = "open discord"
    emb = selector._model.encode(f"search_query: {query}").tolist()
    
    from src.infrastructure.database.supabase_manager import get_supabase_manager
    supabase = get_supabase_manager()
    results = supabase.rpc("retrieve_relevant_tools", {
        "query_text": query,
        "query_embedding": emb,
        "match_count": 5
    })
    
    print(f"\nNew Top 5 for '{query}':")
    if not results:
        print("  No results found!")
    else:
        for i, r in enumerate(results):
            print(f"{i+1}. {r['name']} (Score: {r['similarity']:.4f})")
            print(f"   Desc: {r['description'][:150]}...")

if __name__ == "__main__":
    asyncio.run(refresh_supabase_tools())
