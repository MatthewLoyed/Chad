"""
Base Tool Abstract Class
Defines the interface that all tools must implement for the voice assistant.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseTool(ABC):
    """
    Abstract base class for all tool implementations.
    Tools are automatically discovered and registered by the ToolRegistry.
    """
    
    # Class-level metadata (override in subclasses)
    name: str = "base_tool"
    description: str = "Base tool - override this"
    category: str = "custom"  # 'web', 'system', or 'custom'
    preferred_model: Optional[str] = None  # Suggest which model works best (optional)

    # Idempotency flag: True if tool has side effects (sends emails, messages, creates files, etc.)
    # Tools with side effects are cached to prevent duplicate execution on retries.
    # Safe default is True (assumes side effects). Set to False for read-only tools.
    is_side_effecting: bool = True

    # List of required configuration keys (e.g., ['HUBSPACE_USERNAME', 'HUBSPACE_PASSWORD'])
    # These can be dot-paths for ConfigManager.get_secret()
    requirements: list[str] = []

    # Human-in-the-loop gate: when True, the executor pauses this tool's first
    # call and surfaces a decision request to the user instead of executing.
    # See src/core/orchestration/approval_store.py for the resume mechanics.
    requires_approval: bool = False
    approval_decision_type: str = "accept_decline"  # "accept_decline" | "choice" | "questions"
    # Static prompt template; may reference call params, e.g. "Delete {path}?"
    approval_prompt: Optional[str] = None

    def wants_approval(self, params: Dict[str, Any]) -> bool:
        """
        Whether THIS call should go through the approval gate. Default just
        returns the static `requires_approval` flag; override for tools whose
        need for approval depends on the call's params; override for conditional gating.
        """
        return self.requires_approval

    def approval_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build the human-facing approval request for this call.

        Override for dynamic prompts/options (e.g. a "choice" tool computing
        its options from params). Default formats `approval_prompt` against
        the call params and has no options (accept/decline shape).
        """
        template = self.approval_prompt or f"Allow {self.name} to proceed?"
        try:
            prompt = template.format(**params)
        except (KeyError, IndexError):
            prompt = template
        return {"prompt": prompt, "options": None}

    def is_available(self) -> bool:
        """
        Check if the tool is available for use.
        Checks: 1. If the tool is toggled ON in preferences. 2. If all requirements are met.
        """
        from src.shared.config_manager import ConfigManager
        
        # 1. Check preference toggle (defaults to True if not set)
        if not ConfigManager.get(f"enabled_tools.{self.name}", True):
            return False
            
        # 2. Check requirements
        for req in self.requirements:
            if not ConfigManager.get_secret(req):
                # logger.debug(f"Tool {self.name} unavailable: missing {req}")
                return False
                
        return True
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the tool with optional configuration.
        
        Args:
            config: Tool-specific configuration dictionary
        """
        self.config = config or {}
    
    @abstractmethod
    def get_schema(self) -> Dict[str, Any]:
        """
        Return OpenAPI-style function schema for this tool.

        This is the **canonical contract** the LLM sees at runtime. ``ToolRegistry``
        wraps native tools as LangChain ``StructuredTool`` instances for LangGraph,
        but ``ChatChad`` forwards the full ``parameters`` block from this method
        to the model (nested ``array`` / ``object`` ``items`` and ``properties``
        included). Do not rely on the auto-generated Pydantic args model for
        nested shapes — it only preserves top-level param types.

        Returns:
            Dictionary containing:
            {
                "name": str,
                "description": str,
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }

        See ``src/infrastructure/tools/README.md`` → "Tool schema fidelity".
        """
        pass
    
    @abstractmethod
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        Execute the tool with the provided parameters.
        
        Args:
            **kwargs: Tool-specific parameters
            
        Returns:
            Dictionary with execution result:
            {
                "success": bool,
                "result": Any,
                "error": str (if success=False)
            }
        """
        pass
    
    def validate_params(self, **kwargs) -> tuple[bool, Optional[str]]:
        """
        Validate parameters before execution.
        
        Args:
            **kwargs: Parameters to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Default implementation: always valid
        # Override in subclass for custom validation
        return True, None

    def format_action(self, params: Dict[str, Any]) -> str:
        """
        Return a short, present-tense label for the chat UI tool bubble.

        **Required for every new tool.** Describe what is happening using the
        call parameters (e.g. ``Searching Drive for "budget"``, ``Running git status``).
        Avoid generic ``Executing {tool_name}`` labels — users see this in the
        sidebar stream while the tool runs.

        Also add a matching entry in ``src/clients/web/src/utils/tool-ui.tsx``
        (``TOOL_METADATA`` plus a param-based branch in ``resolveToolUI`` when
        the label depends on arguments). See ``src/infrastructure/tools/README.md``.

        Alternatives: ``@tool_display("…")`` on the class, or a helper in
        ``src/tools/display_helpers.py`` for shared wording.
        """
        label = self.name.replace("_", " ").title()
        return f"Running {label}"

    @classmethod
    def has_display_formatter(cls) -> bool:
        """True when the tool defines a custom UI label (not the BaseTool default)."""
        if getattr(cls, "display_template", None) is not None:
            return True
        return cls.format_action is not BaseTool.format_action
    
    async def before_execute(self, **kwargs) -> Dict[str, Any]:
        """
        Hook called before execute(). Can modify parameters.
        
        Args:
            **kwargs: Original parameters
            
        Returns:
            Modified parameters (or original if no changes)
        """
        return kwargs
    
    async def after_execute(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Hook called after execute(). Can modify result.
        
        Args:
            result: Original execution result
            
        Returns:
            Modified result (or original if no changes)
        """
        return result
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}', category='{self.category}')>"

