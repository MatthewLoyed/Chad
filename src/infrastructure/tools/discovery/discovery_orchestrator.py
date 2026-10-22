import logging
from pathlib import Path
from typing import Any, Dict, Optional
from src.shared.logger import setup_logger
from .filesystem_scanner import FilesystemScanner
from .skill_scanner import SkillScanner
from .mcp_scanner import MCPScanner

logger = setup_logger("ToolDiscoveryOrchestrator")

class ToolDiscoveryOrchestrator:
    """
    Coordinates various scanners to find and register tools across the project.
    """

    def __init__(self, registry, config: Optional[Dict[str, Any]] = None):
        """
        :param registry: The ToolRegistry instance that scanners will register tools into.
        :param config: Configuration to pass to skill instances.
        """
        self._registry = registry
        self._config = config or {}
        
        # Initialize scanners with the registry reference
        self._fs_scanner = FilesystemScanner(registry)
        self._skill_scanner = SkillScanner(registry, config=self._config)
        self._mcp_scanner = MCPScanner(registry)

    def run_discovery(self, use_cache: bool = True):
        """Execute all scanning processes."""
        import time
        from .cache_manager import ToolCacheManager
        cache = ToolCacheManager()

        # Always register core utilities concretely first so they are never loaded as stubs.
        t_core = time.time()
        self._register_core_utilities()
        logger.warning(f"[ DISCOVERY ] Core utilities registered in {time.time() - t_core:.4f}s")

        if use_cache and cache.is_cache_valid():
            t_cache = time.time()
            logger.warning("[ CACHE ] Valid tool cache found. Loading stubs...")
            cached_tools = cache.get_cached_tools()
            for name, meta in cached_tools.items():
                self._registry.register_stub_tool(
                    name=name,
                    schema=meta['schema'],
                    load_info=meta['load_info'],
                    category=meta['category'],
                    source=meta['source']
                )
            logger.warning(f"[ SUCCESS ] Loaded {len(cached_tools)} tools from cache in {time.time() - t_cache:.4f}s.")
            try:
                from src.shared.startup_timing import set_flag
                set_flag("tool_discovery_cache", f"HIT ({len(cached_tools)} tools)")
            except Exception:
                pass
            return
        else:
            try:
                from src.shared.startup_timing import set_flag
                set_flag("tool_discovery_cache", "MISS (full filesystem+skill scan)")
            except Exception:
                pass

        t_disc = time.time()
        logger.warning("[ DISCOVERY ] Starting global tool discovery...")
        
        t_fs = time.time()
        # 2. Local Filesystem (Native Tools)
        # Scans src/tools by default
        self._fs_scanner.scan_directory()
        logger.warning(f"[ DISCOVERY ] Filesystem scan complete in {time.time() - t_fs:.4f}s")
        
        t_skill = time.time()
        # 3. Agentic Skills (Complex Logic)
        # Scans src/skills by default
        self._skill_scanner.scan_directory()
        logger.warning(f"[ DISCOVERY ] Skill scan complete in {time.time() - t_skill:.4f}s")
        
        # 4. Save to cache
        t_save = time.time()
        self._save_to_cache(cache)
        logger.warning(f"[ DISCOVERY ] Cache save complete in {time.time() - t_save:.4f}s")
        
        logger.warning(f"[ SUCCESS ] Global tool discovery TOTAL TIME: {time.time() - t_disc:.4f}s")

    def _save_to_cache(self, cache: Any):
        """Extract metadata from current registry and save to cache."""
        tools_data = {}
        # We only cache local and skill tools (MCP is dynamic)
        for name, tool in self._registry._tools.items():
            source = self._registry._tool_sources.get(name)
            if source not in ['local', 'skill']:
                continue
                
            # If it's already a stub, we can just save it
            if isinstance(tool, dict) and tool.get('is_stub'):
                tools_data[name] = {
                    'schema': tool['schema'],
                    'load_info': tool['load_info'],
                    'category': self._get_category_for_tool(name),
                    'source': source
                }
                continue

            # Otherwise, extract metadata from instance
            try:
                schema = self._registry._extract_schema(name, tool)
                class_name = tool.__class__.__name__
                # Try to find file path - prioritize explicit _source_path if attached during discovery
                source_path = getattr(tool, '_source_path', getattr(tool.__class__, '_source_path', None))
                if source_path and Path(source_path).exists():
                    file_path = str(source_path)
                else:
                    import inspect
                    try:
                        file_path = inspect.getfile(tool.__class__)
                    except (TypeError, ValueError) as inspect_err:
                        # Fallback for modules loaded via importlib without proper sys.modules entry
                        module = inspect.getmodule(tool.__class__)
                        file_path = getattr(module, '__file__', 'unknown') if module else 'unknown'
                        
                        if file_path == 'unknown':
                            logger.warning(f"[ CACHE ] Could not find file path for {name} ({class_name}): {inspect_err}")
                
                tools_data[name] = {
                    'schema': schema,
                    'load_info': {
                        'file_path': str(file_path),
                        'class_name': class_name
                    },
                    'category': self._get_category_for_tool(name),
                    'source': source
                }
            except Exception as e:
                logger.error(f"[ CACHE ] Critical failure extracting info for {name}: {e}", exc_info=True)

        cache.save_cache(tools_data)

    def _get_category_for_tool(self, name: str) -> str:
        """Helper to find category for a tool name."""
        for cat, names in self._registry._categories.items():
            if name in names:
                return cat
        return 'custom'

    def _register_core_utilities(self):
        """Register critical system tools that are required for operation."""
        try:
            # System Monitor
            from src.tools.system.system_monitor import SystemMonitorTool
            self._registry.register_tool(SystemMonitorTool(), category='system', force=True)
            
            # Tool Search (RAG/Keyword Bridge)
            from src.tools.system.utilities.tool_search import ToolSearchTool
            self._registry.register_tool(ToolSearchTool(registry=self._registry), category='meta', force=True)

            # Tool Configuration (Presets/Context)
            from src.tools.system.utilities.tool_config import ManageToolConfigs
            self._registry.register_tool(ManageToolConfigs(), category='system', force=True)
            
        except ImportError as e:
            logger.warning(f"Failed to register some core utilities: {e}")
        except Exception as e:
            logger.error(f"Error during core utility registration: {e}")
