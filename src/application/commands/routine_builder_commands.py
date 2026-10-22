"""Routine builder follows the supervisor model."""

from dataclasses import dataclass
from typing import Any, Dict

from src.application.commands.base_command import Command
from src.shared.config_manager import ConfigManager


@dataclass
class GetRoutineBuilderModelCommand(Command):
    async def execute(self, facade) -> Dict[str, Any]:
        return {
            "model": ConfigManager.get_routine_builder_model(),
            "default": ConfigManager.get_active_model(),
            "source": "supervisor",
        }


@dataclass
class SetRoutineBuilderModelCommand(Command):
    model_id: str

    async def execute(self, facade) -> bool:
        if not self.model_id or not self.model_id.strip():
            return False
        return ConfigManager.set_routine_builder_model(self.model_id.strip())
