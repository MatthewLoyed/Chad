"""
Base Skill - Foundation for Agentic Skills (Recursive Sub-Agents).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging
import asyncio

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("BaseSkill")

class BaseSkill(BaseTool, ABC):
    """
    Abstract Base Class for Agentic Skills.
    
    A "Skill" is a specialized Agent that acts as a Tool.
    It has:
    1. A Specialized Persona (System Prompt)
    2. A Restricted Toolset (Sandboxed)
    3. Its own ReAct Loop (TextModeClient)
    
    When execute() is called, it spins up a new independent context,
    performs the task using its own tools, and returns the result.
    """
    
    # Metadata to be defined by subclasses
    name: str = "base_skill"
    description: str = "Abstract base skill"
    version: str = "1.0.0"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.config = config or {}
        
        # Skill-specific components
        self.client: Optional[TextModeClient] = None
        # Integrated registry for THIS skill (Lazy import to avoid circularity)
        from src.infrastructure.tools.registry import ToolRegistry
        self.skill_registry = ToolRegistry() 
        
        # Initialize the skill's specific tools
        self._setup_tools()
        
    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the specialized system prompt (SOP) for this skill."""
        pass
        
    @abstractmethod
    def _setup_tools(self) -> None:
        """Register the specific tools this skill is allowed to use."""
        pass
        
    def get_schema(self) -> Dict[str, Any]:
        """Return the Gemini function schema for this skill."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "input_text": {
                        "type": "string",
                        "description": "The specific instruction or task for the skill to perform."
                    }
                },
                "required": ["input_text"]
            }
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        """Default label for skill sub-agents; input_text drives most skill schemas."""
        label = self.name.replace("_", " ").title()
        task = (params.get("input_text") or "").strip()
        if task:
            if len(task) > 40:
                task = task[:37] + "..."
            return f'{label}: "{task}"'
        return f"Running {label}"
        
    def _initialize_client(self):
        """Lazy initialization of the TextModeClient (Sub-Brain)."""
        if self.client:
            return

        from src.core.interfaces.text_mode import TextModeClient
        logger.info(f"[{self.name}] Initializing Sub-Agent Brain...")
        
        # Get standard components, but isolated for this skill
        # Note: We do NOT share memory_engine by default to keep context clean.
        # Skills should be stateless/transactional unless specifically designed otherwise.
        
        from src.core.tools.tool_executor import ToolExecutor
        from src.shared.config_manager import ConfigManager
        from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
        import os

        # Determine model name and build adapter matching active settings
        model_name = self.config.get("model_name")
        if not model_name:
            if ConfigManager.is_local_model_active():
                local_cfg = ConfigManager.get_local_model_config()
                provider = local_cfg.get("provider", "lmstudio")
                provider_cfg = ConfigManager.get_local_provider_config(provider)
                
                model_name = local_cfg.get("name", "")
                if model_name and "/" not in model_name:
                    model_name = f"{provider}/{model_name}"

                adapter = LiteLLMAdapter(
                    model_name=model_name,
                    base_url=local_cfg.get("base_url") or provider_cfg.get("base_url"),
                    api_key=local_cfg.get("api_key") or provider_cfg.get("api_key"),
                    system_instruction=self.get_system_prompt()
                )
            else:
                model_name = ConfigManager.get_active_model()
                adapter = LiteLLMAdapter(
                    model_name=model_name,
                    api_key=os.getenv('GEMINI_API_KEY') if 'gemini' in model_name.lower() else None,
                    system_instruction=self.get_system_prompt()
                )
        else:
            # Custom model name specified in self.config
            if ConfigManager.is_local_model_active():
                local_cfg = ConfigManager.get_local_model_config()
                provider = local_cfg.get("provider", "lmstudio")
                provider_cfg = ConfigManager.get_local_provider_config(provider)
                
                if "/" not in model_name:
                    model_name = f"{provider}/{model_name}"
                adapter = LiteLLMAdapter(
                    model_name=model_name,
                    base_url=local_cfg.get("base_url") or provider_cfg.get("base_url"),
                    api_key=local_cfg.get("api_key") or provider_cfg.get("api_key"),
                    system_instruction=self.get_system_prompt()
                )
            else:
                adapter = LiteLLMAdapter(
                    model_name=model_name,
                    api_key=os.getenv('GEMINI_API_KEY') if 'gemini' in model_name.lower() else None,
                    system_instruction=self.get_system_prompt()
                )

        # Create a dedicated executor for this skill's registry
        # We pass None for app_reference as skills shouldn't touch GUI directly usually
        executor = ToolExecutor(tool_registry=self.skill_registry)
        
        self.client = TextModeClient(
            adapter=adapter,
            tool_registry=self.skill_registry,
            tool_executor=executor,
            memory_engine=None, # Clean slate
        )
        
    async def execute(self, input_text: str, **kwargs) -> Dict[str, Any]:
        """
        Execute the skill.
        
        Args:
            input_text: The instructions for the skill.
            **kwargs: Additional context if needed.
            
        Returns:
            Dict with 'success' and 'result' (the agent's final answer).
        """
        try:
            self._initialize_client()
            
            logger.info(f"[{self.name}] ACTIVATED. Input: {input_text[:50]}...")
            
            # Formulate the prompt for the sub-agent
            # We can inject kwargs into the prompt if useful
            prompt = f"TASK: {input_text}\n"
            if kwargs:
                prompt += f"CONTEXT: {kwargs}\n"
            prompt += "\nExecute this task according to your SOP and return the final result."
            
            # Run the agent loop
            # We allow a reasonable default limit for skill steps (e.g., 10)
            # Skills use 'high' thinking level by default for expert reasoning
            response = await self.client.send_message(
                text=prompt,
                max_tool_calls=15,
                thinking_level='high'
            )
            
            if response.get("success"):
                logger.info(f"[{self.name}] COMPLETED. Result: {response.get('response')[:50]}...")
                return {
                    "success": True,
                    "result": response.get("response"),
                    "details": {
                        "tool_calls": response.get("tool_call_count", 0),
                        "usage": response.get("usage") # If available
                    }
                }
            else:
                logger.warning(f"[{self.name}] FAILED: {response.get('response')}")
                return {
                    "success": False,
                    "error": response.get("response", "Unknown error in skill execution")
                }
                
        except Exception as e:
            logger.error(f"[{self.name}] CRITICAL ERROR: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Skill execution crashed: {str(e)}"
            }
