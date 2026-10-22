"""
Filesystem Scanner - Discovers BaseTool implementations from directories

Scans Python files for BaseTool subclasses and registers them automatically.
"""

import importlib.util
import inspect
from pathlib import Path
from typing import Optional
import logging

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("FilesystemScanner")


class FilesystemScanner:
    """
    Scans directories for BaseTool implementations.
    """

    def __init__(self, registry):
        """
        Initialize scanner.

        Args:
            registry: ToolRegistry instance to register tools into
        """
        self.registry = registry

    def scan_directory(self, base_directory: str = 'src/tools') -> None:
        """
        Scan a directory for BaseTool implementations.

        Args:
            base_directory: Root directory to scan
        """
        tools_path = Path(base_directory).resolve()
        print(f"[ DEBUG ] Scanning directory: {tools_path}")

        if not tools_path.exists():
            print(f"[ DEBUG ] Tools directory not found: {tools_path}")
            logger.warning(f"Tools directory not found: {tools_path}")
            return

        # Check if directory has subdirectories (category structure)
        has_subdirs = any(d.is_dir() and not d.name.startswith(('_', '.')) for d in tools_path.iterdir())
        print(f"[ DEBUG ] has_subdirs: {has_subdirs}")

        if has_subdirs:
            # Scan each category subdirectory
            for category_dir in tools_path.iterdir():
                if not category_dir.is_dir() or category_dir.name.startswith(('_', '.')):
                    continue
                print(f"[ DEBUG ] Scanning category: {category_dir.name}")
                self._scan_category_dir(category_dir)
        else:
            # Scan the directory directly
            self._scan_category_dir(tools_path)

    def _scan_category_dir(self, category_dir: Path) -> None:
        """
        Scan a category directory for tool files.

        Args:
            category_dir: Path to category directory
        """
        category = category_dir.name

        for py_file in category_dir.rglob('*.py'):
            if py_file.name.startswith('_'):
                continue
            self._load_tools_from_file(py_file, category)

    def _load_tools_from_file(self, file_path: Path, category: str) -> None:
        """
        Load tool classes from a Python file.

        Args:
            file_path: Path to Python file
            category: Category name for these tools
        """
        try:
            import sys
            # Use unique module name to avoid collision with standard library modules (e.g. calendar)
            module_name = f"src_tools_{category}_{file_path.stem}"

            # Use path-based loading
            logger.debug(f"[ FILESYSTEM_SCAN ] Loading file: {file_path}")
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                logger.debug(f"[ FILESYSTEM_SCAN ] Loaded file successfully: {file_path}")

                # Find all BaseTool subclasses
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # Ensure class belongs to this module
                    if obj.__module__ != module.__name__:
                        continue

                    # Check if it's a BaseTool subclass
                    is_bt = issubclass(obj, BaseTool)
                    
                    if (is_bt or any(c.__name__ == "BaseTool" for c in obj.__mro__)) and obj is not BaseTool:
                        # Attach source path for caching reliability
                        obj._source_path = str(file_path)
                        self._register_tool_class(obj, category)

        except Exception as e:
            logger.error(f"Failed to load tools from {file_path}: {e}")

    def _register_tool_class(self, tool_class: type, category: str) -> None:
        """
        Instantiate and register a tool class.

        Args:
            tool_class: Tool class to register
            category: Category from directory structure
        """
        try:
            # Try to instantiate
            try:
                tool_instance = tool_class()
            except TypeError as e:
                # Skip tools that require init arguments
                if "required positional argument" in str(e):
                    logger.debug(f"Skipping {tool_class.__name__} (requires init args)")
                    return
                raise

            # Use tool's declared category if available
            effective_category = getattr(tool_instance, 'category', category)

            # Register via ToolRegistry
            self.registry.register_tool(tool_instance, category=effective_category)

            if not tool_class.has_display_formatter():
                logger.warning(
                    f"Tool '{tool_instance.name}' ({tool_class.__name__}) has no display "
                    f"formatter — add format_action() or @tool_display. See "
                    f"src/infrastructure/tools/README.md"
                )

        except Exception as e:
            logger.error(f"Failed to register tool class {tool_class.__name__}: {e}")
