"""
Structured Logging Utility
Provides consistent logging across the voice assistant with debugging output.
"""

import logging
import sys
import queue
import os
import re
from typing import Any, Dict, Optional, Callable, Set
from datetime import datetime
from src.shared.debug_helper import is_debug_enabled


class LogTagFilter(logging.Filter):
    """
    Filters log messages based on tags in brackets (e.g., [ DISCOVERY ]) or logger names.
    Allows toggling specific categories via CHAD_DEBUG_TAGS environment variable.
    """
    def __init__(self):
        super().__init__()
        # Get enabled tags/loggers from environment (comma-separated)
        # Example: CHAD_DEBUG_TAGS=DISCOVERY,AudioHandler
        enabled_tags_env = os.getenv("CHAD_DEBUG_TAGS", "")
        if enabled_tags_env.upper() == "ALL":
            self.show_all = True
            self.enabled_tags = set()
        else:
            self.show_all = False
            self.enabled_tags = {tag.strip().upper() for tag in enabled_tags_env.split(",") if tag.strip()}
        
        # Tags that are suppressed by default if record level is below WARNING
        self.noisy_tags = {
            "DISCOVERY", "SYNC", "BRIDGE", "RAG", "REGISTRY", 
            "TOOL SELECTION", "TEXT MODE", "GRAPH", "FACADE", 
            "HISTORY", "LOCAL", "CONTEXT RAG", "SUCCESS",
            "METADATA", "PROMPT", "SCHEMA", "SYNC_ALL", "ORCHESTRATOR",
            "AUDIO", "SYSTEM", "SKILLS", "PERF", "HOOKS", "WATCHDOG",
            "WEBVIEW", "COMMAND"
        }

        # Mapping of logger base names to tags for implicit filtering
        self.logger_to_tag = {
            "MCPScanner": "DISCOVERY",
            "FilesystemScanner": "DISCOVERY",
            "SkillScanner": "DISCOVERY",
            "DiscoveryEngine": "DISCOVERY",
            "ToolDiscoveryOrchestrator": "DISCOVERY",
            "SubAgentSpawner": "DISCOVERY",
            "FileOpener": "DISCOVERY",
            "SupabaseToolSyncBridge": "SYNC",
            "AudioHandler": "AUDIO",
            "SoundManager": "AUDIO",
            "TTSPlayer": "AUDIO",
            "ToolPolicyManager": "SYSTEM",
            "HooksManager": "HOOKS",
            "ProviderFailover": "SYSTEM",
            "SessionManager": "SYSTEM",
            "Watchdog": "WATCHDOG",
            "ModelCoordinator": "SYSTEM",
            "ToolCoordinator": "SYSTEM",
            "ConversationOrchestrator": "ORCHESTRATOR",
            "LangGraphEngine": "GRAPH",
            "ReplyParser": "SYSTEM",
            "CommandHandler": "COMMAND",
            "ExecutorStrategy": "SYSTEM",
            "TextMode": "TEXT MODE",
            "WebViewAPI": "BRIDGE",
            "GUIChadLimb": "SYSTEM",
            "Benchmark": "PERF",
            "GUI": "GUI"
        }

    def filter(self, record):
        # Always allow WARNING and above
        if record.levelno >= logging.WARNING:
            return True
            
        # If user wants to see everything
        if self.show_all:
            return True
            
        message = record.getMessage()
        logger_base = record.name.split('.')[0]
        
        # 1. Identify the tag
        tag = None
        match = re.search(r'\[\s*([A-Z\s_]{3,})\s*\]', message)
        if match:
            tag = match.group(1).strip().upper()
        else:
            tag = self.logger_to_tag.get(logger_base)
            
        # 2. Check if explicitly enabled by TAG or LOGGER NAME
        if (tag and tag in self.enabled_tags) or \
           (logger_base.upper() in self.enabled_tags) or \
           (record.name.upper() in self.enabled_tags) or \
           (tag == "STREAM TRACE"):
            return True
            
        # 3. If it has a noisy tag or comes from a noisy logger, suppress it (since level < WARNING)
        if tag in self.noisy_tags or logger_base in self.logger_to_tag:
            return False
            
        # 4. Global DEBUG suppression for any known noisy sources even without tags
        if record.levelno == logging.DEBUG and logger_base in self.logger_to_tag:
            return False
                
        return True


class ColoredFormatter(logging.Formatter):
    """Custom formatter with optional ANSI colors for interactive terminal output."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'        # Reset
    }
    
    def __init__(self, fmt=None, datefmt=None, style='%', use_colors: Optional[bool] = None):
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)
        if use_colors is None:
            # Colorize if writing to an interactive terminal and NO_COLOR is not requested
            is_tty = getattr(sys.stdout, "isatty", lambda: False)()
            no_color = os.getenv("NO_COLOR", "").lower() in ("1", "true") or os.getenv("CHAD_NO_COLOR", "").lower() in ("1", "true")
            force_color = os.getenv("CHAD_FORCE_COLOR", "").lower() in ("1", "true")
            self.use_colors = force_color or (is_tty and not no_color)
        else:
            self.use_colors = use_colors

    def format(self, record):
        # Format timestamp
        record.asctime = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        
        orig_levelname = record.levelname
        if self.use_colors and orig_levelname in self.COLORS:
            record.levelname = f"{self.COLORS[orig_levelname]}{orig_levelname}{self.COLORS['RESET']}"
            result = super().format(record)
            record.levelname = orig_levelname
            return result
        
        return super().format(record)


def setup_logger(name: str = "VoiceAssistant", level: int = logging.INFO) -> logging.Logger:
    """
    Setup a logger with consistent formatting and debugging output.
    
    Args:
        name: Logger name
        level: Logging level (default: DEBUG for detailed output)
        
    Returns:
        Configured logger instance
    """
    
    # Ensure root logger has the filter for global coverage
    root = logging.getLogger()
    if not any(isinstance(f, LogTagFilter) for f in root.filters):
        root.addFilter(LogTagFilter())

    # Check environment variable for override
    env_level = os.getenv("CHAD_LOG_LEVEL")
    if env_level:
        level = getattr(logging, env_level.upper(), level)
    
    # If dev_debug is False, force at least WARNING level (suppress INFO/DEBUG)
    if not is_debug_enabled() and not env_level:
        level = max(level, logging.WARNING)

    # Silence noisy libraries and HTTP polling access logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google.ai.generativelanguage").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("webview").setLevel(logging.WARNING)
    logging.getLogger("pywebview").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    logging.getLogger("pycaw").setLevel(logging.WARNING)
        
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Ensure the tag filter is present on this specific logger
    if not any(isinstance(f, LogTagFilter) for f in logger.filters):
        logger.addFilter(LogTagFilter())
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # Console handler with colored output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    
    # Detailed format with module, function, and line number for debugging
    formatter = ColoredFormatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)s.%(module)s.%(funcName)s:%(lineno)d | %(message)s'
    )
    console_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    
    return logger


def log_dict(logger: logging.Logger, level: int, message: str, data: Dict[str, Any]) -> None:
    """
    Log a dictionary with pretty formatting.
    
    Args:
        logger: Logger instance
        level: Log level
        message: Message prefix
        data: Dictionary to log
    """
    logger.log(level, f"{message}:")
    for key, value in data.items():
        logger.log(level, f"  {key}: {value}")


# Create default logger instance
default_logger = setup_logger()


class GuiLogHandler(logging.Handler):
    """
    Custom logging handler that forwards filtered log messages to a GUI widget.
    Filters logs based on prefixes/content and forwards them via a callback.
    Thread-safe: uses queue to pass messages to GUI thread.
    """
    
    def __init__(self, callback: Optional[Callable[[str, str], None]] = None):
        """
        Initialize GUI log handler.
        
        Args:
            callback: Optional callback function(message, level) to receive filtered log messages
                     If None, messages are stored in internal queue (for later retrieval)
        """
        super().__init__()
        self.callback = callback
        self.log_queue = queue.Queue() if callback is None else None
        
        # Filter patterns for log messages we want to capture
        self.filter_patterns = [
            '[CHAD ACTION]',
            '[SYSTEM]',
            '[AUDIO]',
            '[ VOICE MODE ]',
            '[RAW TOOL CALL]',
            '[THINKING]',
            '[REASONING]',
            '[SUCCESS]',
            '[ERROR]',
            '[ASSISTANT]',
            '[USER]',
            'Tool execution',
            'Executing tool:',
        ]
        
        # Set level to capture INFO and above by default
        self.setLevel(logging.INFO)
    
    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record if it matches our filter criteria.
        
        Args:
            record: Log record to potentially emit
        """
        try:
            # Format the message
            message = self.format(record)
            
            # Check if message matches any filter pattern
            message_matches = any(pattern in message for pattern in self.filter_patterns)
            
            # Also capture ERROR level messages regardless of pattern
            if record.levelno >= logging.ERROR:
                message_matches = True
            
            if message_matches:
                # Forward to callback or store in queue
                if self.callback:
                    try:
                        self.callback(message, record.levelname)
                    except Exception:
                        # Don't let callback errors break logging
                        pass
                elif self.log_queue:
                    try:
                        self.log_queue.put_nowait((message, record.levelname))
                    except queue.Full:
                        # Queue full, drop oldest message
                        try:
                            self.log_queue.get_nowait()
                            self.log_queue.put_nowait((message, record.levelname))
                        except queue.Empty:
                            pass
        except Exception:
            # Don't let handler errors break logging
            self.handleError(record)
    
    def get_message(self, timeout: float = 0.0) -> Optional[tuple[str, str]]:
        """
        Get next log message from queue (if using queue mode).
        
        Args:
            timeout: Timeout in seconds (0 = non-blocking)
            
        Returns:
            Tuple of (message, levelname) or None if no message available
        """
        if self.log_queue is None:
            return None
        
        try:
            return self.log_queue.get(timeout=timeout)
        except queue.Empty:
            return None


def add_gui_handler(logger: logging.Logger, callback: Callable[[str, str], None]) -> GuiLogHandler:
    """
    Add a GUI log handler to an existing logger.
    
    Args:
        logger: Logger instance to add handler to
        callback: Callback function(message, level) to receive filtered log messages
        
    Returns:
        The created GuiLogHandler instance
    """
    handler = GuiLogHandler(callback=callback)
    logger.addHandler(handler)
    return handler

