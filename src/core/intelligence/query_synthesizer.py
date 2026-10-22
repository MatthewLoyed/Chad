import logging
from typing import List, Dict, Any, Union, Optional
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage, SystemMessage

logger = logging.getLogger("QuerySynthesizer")

class QuerySynthesizer:
    """
    Centralized engine for query parsing, decomposition, and history enrichment.
    Ensures that Supabase vector search and tool retrieval share robust intent recognition.
    """

    @staticmethod
    def standardize_history(history: Optional[List[Union[Dict[str, Any], BaseMessage]]]) -> List[Dict[str, Any]]:
        """
        Standardizes incoming history lists into a canonical Dict format.
        Handles mixed formats containing raw Dicts and LangChain Message subclasses.
        """
        if not history:
            return []

        canonical = []
        for msg in history:
            if isinstance(msg, dict):
                # Already standard dict
                canonical.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })
            elif isinstance(msg, BaseMessage):
                # Map BaseMessage properties to roles
                role = "user"
                if isinstance(msg, AIMessage):
                    role = "assistant"
                elif isinstance(msg, ToolMessage):
                    role = "tool"
                elif isinstance(msg, SystemMessage):
                    role = "system"
                
                content = msg.content
                canonical.append({
                    "role": role,
                    "content": content
                })
            else:
                logger.warning(f"[ SYNTHESIZER ] Encountered unknown message type in history: {type(msg)}")
        
        return canonical

    @staticmethod
    def decompose(query: str) -> List[str]:
        """
        Splits compound queries into atomic sub-intents at natural linguistic boundaries (conjunctions).
        Prevents compound sentences from generating blurred semantic embeddings.
        
        Uses a verb-aware boundary heuristic to avoid fracturing parameter lists (e.g., "1, 2, and 3").
        """
        if not query:
            return []
            
        import re
        # Split by standard conjunction delimiters first: ' and ', ', and ', or ', '
        raw_parts = re.split(r',\s*and\s+|\s+and\s+|,\s*', query)
        
        parts = []
        current_segment = []
        
        # Common imperative verbs that strongly signal an intentional action clause boundary
        IMPERATIVES = {
            'check', 'find', 'list', 'get', 'open', 'run', 'create', 'write', 
            'delete', 'read', 'update', 'search', 'scrape', 'download', 'upload', 
            'start', 'stop', 'execute', 'analyze', 'do', 'show', 'tell', 'audit', 
            'sync', 'deploy', 'send', 'fetch', 'make', 'generate', 'push', 'pull'
        }
        
        for i, part in enumerate(raw_parts):
            trimmed = part.strip()
            if not trimmed:
                continue
                
            # First element initializes the accumulator
            if i == 0:
                current_segment.append(trimmed)
                continue
                
            # Check first word of candidate segment
            words = trimmed.split()
            first_word = words[0].lower().strip('\'"()[]{},.:;!?') if words else ""
            
            # Split only if candidate begins with an Action Verb OR is a distinct sentence segment
            if first_word in IMPERATIVES or (len(words) >= 3 and not first_word.isdigit()):
                if current_segment:
                    parts.append(" ".join(current_segment))
                current_segment = [trimmed]
            else:
                # Parameter/List cohesion: rejoin fragment into current clause
                current_segment.append(trimmed)
                
        if current_segment:
            parts.append(" ".join(current_segment))
            
        if not parts:
            return [query]
            
        logger.debug(f"[ SYNTHESIZER_DECOMP ] Decomposed compound query: {parts}")
        return parts

    @staticmethod
    def enrich_with_history(query: str, history: Optional[List[Union[Dict[str, Any], BaseMessage]]], max_turns: int = 5) -> str:
        """
        Prepends context from the most recent conversational turns into the retrieval intent,
        allowing the embedding vector to capture coreferences like 'it', 'that', or 'the car'.
        
        Truncates long historical turns to prevent token bloat inside embedding models.
        """
        if not history:
            return query
            
        # 1. Standardize history items
        canonical = QuerySynthesizer.standardize_history(history)
        
        # 2. Filter down to just dialogue roles to prevent tool outputs from skewing embeddings
        dialogue = [m for m in canonical if m["role"] in ["user", "assistant"]]
        
        # 3. Slice to last few turns for focus
        recent = dialogue[-max_turns:]
        if not recent:
            return query
            
        context_parts = []
        for msg in recent:
            role = msg["role"]
            content = msg["content"]
            
            # Extract text safe-guarding against multimodal lists
            if isinstance(content, list):
                text = next((b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"), "")
            else:
                text = str(content)
                
            # Truncate to prevent embedding bloat (200 chars is plenty for context anchoring)
            if len(text) > 200:
                text = text[:200] + "..."
                
            context_parts.append(f"{role}: {text}")
            
        # Append the immediate query as final anchor
        context_parts.append(f"current_intent: {query}")
        
        enriched = "\n".join(context_parts)
        logger.debug(f"[ SYNTHESIZER_ENRICH ] Successfully enriched query with {len(recent)} history turns.")
        return enriched
