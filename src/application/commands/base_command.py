"""
Base Command Pattern Implementation

Provides the foundation for command-based architecture.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, Dict
from dataclasses import dataclass
import logging
from src.shared.logger import setup_logger

logger = setup_logger("CommandHandler")


class Command(ABC):
    """
    Abstract base class for all commands.

    Commands encapsulate an action and its parameters.
    They are executed by the CommandHandler which provides
    logging, validation, and error handling.
    """

    @abstractmethod
    async def execute(self, facade) -> Any:
        """
        Execute the command.

        Args:
            facade: AssistantFacade instance to execute against

        Returns:
            Command result (type varies by command)
        """
        pass

    def validate(self) -> tuple[bool, Optional[str]]:
        """
        Validate command parameters before execution.

        Returns:
            Tuple of (is_valid, error_message)
        """
        return (True, None)

    def __str__(self) -> str:
        """String representation for logging."""
        return self.__class__.__name__


class CommandHandler:
    """
    Handler for executing commands.

    Provides centralized logging, validation, and error handling.
    """

    def __init__(self, facade):
        """
        Initialize command handler.

        Args:
            facade: AssistantFacade instance to execute commands against
        """
        self.facade = facade

    async def execute(self, command: Command) -> Any:
        """
        Execute a command with logging and error handling.

        Args:
            command: Command to execute

        Returns:
            Command result
        """
        # Validate command
        is_valid, error = command.validate()
        if not is_valid:
            logger.error(f"Command validation failed: {command} - {error}")
            return {"success": False, "error": error}

        # Log command execution
        logger.debug(f"Executing command: {command}")

        try:
            # Execute command
            result = await command.execute(self.facade)
            logger.debug(f"Command completed: {command}")
            return result

        except Exception as e:
            logger.error(f"Command execution failed: {command} - {e}", exc_info=True)
            return {"success": False, "error": str(e)}
