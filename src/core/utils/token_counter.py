import logging
from typing import List, Dict, Any, Optional
import json

logger = logging.getLogger("TokenCounter")

class TokenCounter:
    """
    Utility to estimate token counts for various context components
    without making API calls (using estimation for speed/cost).
    
    NOTE: These are ESTIMATES (approx 4 chars/token). 
    Used for client-side budgeting before hitting the API.
    """
    
    TOOLS_OVERHEAD = 1000  # Base overhead for enabling tools
    
    @staticmethod
    def count_text(text: str) -> int:
        """Estimate tokens in a string (approx 4 chars per token)."""
        if not text:
            return 0
        return len(text) // 4
        
    @staticmethod
    def count_message(message: Dict[str, Any]) -> int:
        """Count tokens in a message object (role + content)."""
        count = 0
        
        # Role overhead
        count += 5 
        
        # 1. Check parts (Gemini style)
        parts = message.get("parts")
        if parts:
            if isinstance(parts, str):
                count += TokenCounter.count_text(parts)
            elif isinstance(parts, list):
                for part in parts:
                    if isinstance(part, str):
                        count += TokenCounter.count_text(part)
                    elif isinstance(part, dict):
                        if "text" in part:
                            count += TokenCounter.count_text(part["text"])
            return count

        # 2. Check content (OpenAI style)
        content = message.get("content")
        if content:
            if isinstance(content, str):
                count += TokenCounter.count_text(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            count += TokenCounter.count_text(block.get("text", ""))
            return count
            
        # 3. Check content_blocks (direct internal representation)
        content_blocks = message.get("content_blocks")
        if content_blocks and isinstance(content_blocks, list):
            for block in content_blocks:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        count += TokenCounter.count_text(block.get("text", ""))
            return count
        
        return count

    @staticmethod
    def count_tools(tools: List[Dict[str, Any]]) -> int:
        """Estimate tokens used by tool definitions."""
        if not tools:
            return 0
        
        # Serialize to JSON to estimate size
        try:
            tools_json = json.dumps(tools)
            return TokenCounter.count_text(tools_json) + TokenCounter.TOOLS_OVERHEAD
        except:
            return TokenCounter.TOOLS_OVERHEAD

    @staticmethod
    def count_total(
        system_instruction: str,
        memory_block: str,
        history: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] = None
    ) -> int:
        """Calculate total estimated context usage."""
        total = 0
        
        # 1. System Instruction
        total += TokenCounter.count_text(system_instruction)
        
        # 2. Memory Block
        total += TokenCounter.count_text(memory_block)
        
        # 3. Tools
        total += TokenCounter.count_tools(tools)
        
        # 4. History
        for msg in history:
            total += TokenCounter.count_message(msg)
            
        return total
