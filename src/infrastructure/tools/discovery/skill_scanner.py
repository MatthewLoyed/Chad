"""
Skill Scanner - Discovers BaseSkill implementations from directories

Scans Python files for BaseSkill subclasses (sub-agents).
"""

import importlib.util
import inspect
from pathlib import Path
from typing import Dict, Any, Optional
import logging

from src.core.skill_system.base_skill import BaseSkill
from src.shared.logger import setup_logger

logger = setup_logger("SkillScanner")


class SkillScanner:
    """
    Scans directories for BaseSkill implementations.
    """

    def __init__(self, registry, config: Optional[Dict[str, Any]] = None):
        """
        Initialize scanner.

        Args:
            registry: ToolRegistry instance to register skills into
            config: Configuration to pass to skill instances
        """
        self.registry = registry
        self.config = config or {}

    def scan_directory(self, directory_path: str = "src/skills") -> None:
        """
        Scan a directory for BaseSkill implementations.

        Args:
            directory_path: Path to skills directory
        """
        abs_path = Path(directory_path).resolve()

        if not abs_path.exists():
            logger.warning(f"Skills directory not found: {abs_path}")
            return

        if not abs_path.is_dir():
            logger.warning(f"Skills path {directory_path} is not a directory")
            return

        # Scan Python files in first-level subdirectories (shallow scan)
        for folder in abs_path.iterdir():
            if folder.is_dir():
                for py_file in folder.glob("*.py"):
                    if py_file.name.startswith("_"):
                        continue
                    self._load_skills_from_file(py_file)

    def _load_skills_from_file(self, file_path: Path) -> None:
        """
        Load skill classes from a Python file.

        Args:
            file_path: Path to Python file
        """
        module_name = file_path.stem

        try:
            # Create module from file location
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Find BaseSkill subclasses
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) and
                        issubclass(obj, BaseSkill) and
                        obj is not BaseSkill):
                        self._register_skill_class(obj)

        except Exception as e:
            logger.error(f"Failed to load skill module {module_name} from {file_path}: {e}")

    def _register_skill_class(self, skill_class: type) -> None:
        """
        Instantiate and register a skill class.

        Args:
            skill_class: Skill class to register
        """
        try:
            # Instantiate skill
            skill_instance = skill_class(config=self.config)

            # Register via ToolRegistry (will auto-assign to 'skills' category)
            self.registry.register_tool(skill_instance, category='skills')

        except Exception as e:
            logger.error(f"Failed to register skill {skill_class}: {e}")
