import logging
import json
import re
from typing import Dict, Any, Callable
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger("ChadSupervisor")

def make_validator_node(model: Any) -> Callable:
    """Factory creating the validator node with the given LLM model."""
    
    async def validator_node(state: Dict[str, Any], config: RunnableConfig):
        """Verifies if the plan's overall goal was successfully achieved."""
        history = state.get("messages", [])
        
        from src.core.intelligence.prompt_builder import SystemPromptBuilder
        validator_prompt = SystemPromptBuilder.build(base_instruction="", scope="validator")
            
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=validator_prompt),
            MessagesPlaceholder(variable_name="messages"),
        ])
        
        chain = prompt | model
        response = await chain.ainvoke({"messages": history}, config=config)
        
        content = str(response.content).strip()
        
        # 1. Try JSON-first parsing
        json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        json_str = json_match.group(1).strip() if json_match else content
        
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict) and "result" in parsed:
                result = str(parsed.get("result", "")).strip().lower()
                feedback = str(parsed.get("feedback", "")).strip()
                if result == "passed":
                    return {
                        "validation_feedback": None,
                        "final_response": feedback or content,
                        "messages": [response]
                    }
                else:
                    return {
                        "validation_feedback": feedback or "Validation failed.",
                        "messages": [response]
                    }
        except Exception:
            pass
            
        # 2. Case-insensitive string matching fallback
        content_upper = content.upper()
        if "VALIDATION_PASSED" in content_upper:
            # Clean validation tags case-insensitively
            clean_res = re.sub(r"(?i)validation_passed", "", content)
            clean_res = clean_res.replace(":", "", 1).strip()
            return {
                "validation_feedback": None,
                "final_response": clean_res,
                "messages": [response]
            }
        else:
            clean_feedback = re.sub(r"(?i)validation_failed", "", content)
            clean_feedback = clean_feedback.replace(":", "", 1).strip()
            logger.warning(f"[ VALIDATION ] Validation failed with feedback: {clean_feedback}")
            return {
                "validation_feedback": clean_feedback,
                "messages": [response]
            }
            
    return validator_node
