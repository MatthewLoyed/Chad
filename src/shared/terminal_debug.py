"""
Terminal Debugging Utilities
Provides colored, formatted output for tool actions and debugging.
"""

import sys
import logging
from typing import Any, Dict, Optional

# ANSI color codes for terminal output
class Colors:
    """ANSI color codes for terminal output."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    # Action colors
    ACTION = '\033[94m'  # Bright blue
    SUCCESS = '\033[92m'  # Green
    ERROR = '\033[91m'  # Red
    WARNING = '\033[93m'  # Yellow
    THINKING = '\033[96m'  # Cyan
    DEBUG = '\033[90m'  # Gray

def log_action(message: str, logger: Optional[logging.Logger] = None, **kwargs) -> None:
    """
    Print a formatted, colored action message to the terminal.
    Also logs through logger if provided (for GUI log capture).
    
    Args:
        message: The action message to display
        logger: Optional logger instance to also emit message through
        **kwargs: Additional context to display
    """
    formatted = f"{Colors.ACTION}{Colors.BOLD}[CHAD ACTION]:{Colors.RESET} {Colors.ACTION}{message}{Colors.RESET}"
    
    if kwargs:
        # Format additional context
        context_parts = []
        for key, value in kwargs.items():
            context_parts.append(f"{key}={value}")
        if context_parts:
            formatted += f" {Colors.DEBUG}({', '.join(context_parts)}){Colors.RESET}"
    
    print(formatted, file=sys.stderr)  # Use stderr so it doesn't interfere with stdout
    
    # Also log through logger if provided (for GUI capture)
    # Use WARNING level to ensure it's always shown (logger level is set to WARNING)
    if logger:
        log_message = f"[CHAD ACTION]: {message}"
        if kwargs:
            context_str = ', '.join(f"{k}={v}" for k, v in kwargs.items())
            log_message += f" ({context_str})"
        logger.warning(log_message)

def log_tool_call(tool_name: str, params: Dict[str, Any], logger: Optional[logging.Logger] = None) -> None:
    """
    Print a formatted tool call to the terminal.
    Also logs through logger if provided (for GUI log capture).
    
    Args:
        tool_name: Name of the tool being called
        params: Parameters passed to the tool
        logger: Optional logger instance to also emit message through
    """
    print(f"{Colors.ACTION}{Colors.BOLD}[RAW TOOL CALL]:{Colors.RESET} {Colors.ACTION}{tool_name}{Colors.RESET}", file=sys.stderr)
    print(f"{Colors.DEBUG}  Arguments: {params}{Colors.RESET}", file=sys.stderr)
    
    # Also log through logger if provided
    if logger:
        logger.debug(f"Tool call: {tool_name} with params: {params}")

def log_thinking(message: str = "Chad is thinking...", logger: Optional[logging.Logger] = None) -> None:
    """
    Print a thinking indicator to the terminal.
    Also logs through logger if provided (for GUI log capture).
    
    Args:
        message: The thinking message to display
        logger: Optional logger instance to also emit message through
    """
    # #region agent log
    # import json as json_module
    # import time as time_module
    # #endregion
    
    print(f"{Colors.THINKING}{Colors.BOLD}[THINKING]:{Colors.RESET} {Colors.THINKING}{message}{Colors.RESET}", file=sys.stderr)
    
    # Also log through logger if provided
    if logger:
        logger.debug(f"Thinking: {message}")

def log_reasoning(reasoning: str, logger: Optional[logging.Logger] = None) -> None:
    """
    Print model reasoning/thoughts to the terminal.
    Also logs through logger if provided (for GUI log capture).
    
    Args:
        reasoning: The reasoning text from the model
        logger: Optional logger instance to also emit message through
    """
    # #region agent log
    # import json as json_module
    # import time as time_module
    # #endregion
    
    print(f"{Colors.THINKING}{Colors.BOLD}[REASONING]:{Colors.RESET} {Colors.THINKING}{reasoning}{Colors.RESET}", file=sys.stderr)
    
    # Also log through logger if provided
    if logger:
        logger.debug(f"Reasoning: {reasoning}")

def log_success(message: str, logger: Optional[logging.Logger] = None) -> None:
    """
    Print a success message to the terminal.
    Also logs through logger if provided (for GUI log capture).
    
    Args:
        message: The success message to display
        logger: Optional logger instance to also emit message through
    """
    print(f"{Colors.SUCCESS}{Colors.BOLD}[SUCCESS]:{Colors.RESET} {Colors.SUCCESS}{message}{Colors.RESET}", file=sys.stderr)
    
    # Also log through logger if provided
    if logger:
        logger.info(f"[SUCCESS]: {message}")

def log_error(message: str, logger: Optional[logging.Logger] = None) -> None:
    """
    Print an error message to the terminal.
    Also logs through logger if provided (for GUI log capture).
    
    Args:
        message: The error message to display
        logger: Optional logger instance to also emit message through
    """
    print(f"{Colors.ERROR}{Colors.BOLD}[ERROR]:{Colors.RESET} {Colors.ERROR}{message}{Colors.RESET}", file=sys.stderr)
    
    # Also log through logger if provided
    if logger:
        logger.error(f"[ERROR]: {message}")

