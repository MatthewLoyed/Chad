"""
Homework Assistant Skill - A "Socratic Scribe" for Academic Assignments.
"""

from typing import Dict, Any, List, Optional
import json
import logging
import asyncio

from src.core.skill_system.base_skill import BaseSkill
from src.tools.canvas.assignments import GetAssignmentDetailsTool, GetUpcomingAssignmentsTool, GetCanvasDashboardTodosTool

logger = logging.getLogger("HomeworkSkill")

class HomeworkAssistantSkill(BaseSkill):
    """
    A stateful skill that helps users plan, research, and draft assignments.
    It acts as a "Socratic Scribe", interviewing the user and weaving their
    exact words into a coherent document.
    """
    
    name = "homework_assistant"
    description = "Helps with homework: Fetches assignments (Canvas), researches topics, interviews the user to get their thoughts, and drafts a Google Doc using their EXACT words."
    version = "1.0.0"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # State
        self.phase = "INIT"  # INIT, PLANNING, COLLECTION, SYNTHESIS, REFINEMENT
        self.assignment_context = {}
        self.blueprint = []  # List of questions/sections
        self.doc_id = None
        self.doc_title = None
        self.current_question_index = 0
        self.collected_count = 0 
        
    def get_system_prompt(self) -> str:
        """
        Dynamic system prompt based on the current phase.
        """
        
        base_prompt = """
        You are the "Homework Assistant", a specialized Socratic Scribe.
        
        GOAL: Help the user complete a written assignment by structuring their thoughts 
        and compiling them into a document.
        
        CORE PHILOSOPHY:
        1. **NO AI GHOSTWRITING**: Do not generate the core content yourself.
        2. **USER'S VOICE**: The final output must use the user's EXACT words and phrasing.
        3. **YOUR ROLE**: You are the Architect (structure) and the Scribe (typing).
        4. **Transparency**: The user should see their raw words land in the doc immediately.
        """
        
        if self.phase == "INIT":
            return base_prompt + """
            CURRENT PHASE: INIT
            - Your job is to find out what the assignment is.
            - Check Canvas using `get_upcoming_assignments` or `get_assignment_details`.
            - If not on Canvas, ask the user to paste instructions.
            - Once you have instructions, transition to PLANNING.
            """
            
        elif self.phase == "PLANNING":
            return base_prompt + f"""
            CURRENT PHASE: PLANNING
            - Assignment: {self.assignment_context.get('title', 'Unknown')}
            - Instructions: {self.assignment_context.get('instructions', '')[:500]}...
            
            TASKS:
            1. Research the topic if necessary (use search tools) to understand context.
            2. Create a "Blueprint": A logical list of interview questions to cover the assignment requirements.
            3. Create a Google Doc for this assignment using `createGoogleDoc` with the title "{self.assignment_context.get('title', 'Assignment')} - Draft".
            4. Present the plan (questions) to the user and ask if they are ready to start.
            """
            
        elif self.phase == "COLLECTION":
            current_q = self.blueprint[self.current_question_index] if self.current_question_index < len(self.blueprint) else "Done"
            return base_prompt + f"""
            CURRENT PHASE: COLLECTION
            - You are interviewing the user to fill out the assignment.
            - Current Question: "{current_q}"
            - Progress: {self.current_question_index + 1}/{len(self.blueprint)}
            - Doc ID: {self.doc_id}
            
            RULES:
            1. Ask the current question from your Blueprint.
            2. When the user answers:
               a. **IMMEDIATELY** use `insertText` to write their RAW ANSWER into the document under a header like "## Question {self.current_question_index + 1}: {current_q}".
               b. Confirm to the user: "Got is. I've added that to the doc."
               c. Ask if they want to add more to this point or move to the next.
            3. If they say "next", increment the question index (internal state).
            4. When all questions are answered, ask: "Are you ready for me to smooth it out without changing your exact words?"
            """
            
        elif self.phase == "SYNTHESIS":
            return base_prompt + f"""
            CURRENT PHASE: SYNTHESIS
            - The user claims they are done answering.
            - Doc ID: {self.doc_id}
            
            TASK:
            1. Read the CURRENT content of the doc (which contains the raw Q&A) if you need a refresher, OR just use the context you have.
            2. Use `insertText` to add a specific separator (e.g. "--- FINAL DRAFT ---").
            3. Write the "Smoothed Out" version:
               - Combine the answers into paragraphs.
               - Add transition sentences.
               - **DO NOT** replace their words with fancy synonyms. Keep it authentic.
            4. Tell the user: "Draft created. Take a look at the Google Doc."
            """
        
        elif self.phase == "REFINEMENT":
            return base_prompt + f"""
            CURRENT PHASE: REFINEMENT
            - The draft is effectively done.
            - Doc ID: {self.doc_id}
            - Engage in free-form editing with the user.
            - If they want to change something, use `readGoogleDoc` to find it and `updateGoogleDoc` (or just append corrections) to fix it.
            """
            
        return base_prompt

    def _setup_tools(self) -> None:
        """Register tools for this skill."""
        
        # Canvas Tools
        self.skill_registry.register_tool(GetAssignmentDetailsTool())
        self.skill_registry.register_tool(GetUpcomingAssignmentsTool())
        self.skill_registry.register_tool(GetCanvasDashboardTodosTool())
        

        
        # Research Tools
        try:
            from src.tools.web.search import SearchWebTool
            self.skill_registry.register_tool(SearchWebTool())
        except ImportError:
            logger.warning("SearchWebTool not found, research capability limited.")
            pass

    async def execute(self, input_text: str, **kwargs) -> Dict[str, Any]:
        """
        Main execution loop for the skill turn.
        """
        self._initialize_client()
        
        # Inject state context
        context_str = f"""
        [STATE INJECTION]
        Current Phase: {self.phase}
        Assignment: {self.assignment_context.get('title', 'None')}
        Doc ID: {self.doc_id}
        Google Doc Link: {f'https://docs.google.com/document/d/{self.doc_id}' if self.doc_id else 'Not created'}
        Blueprint Progress: {self.current_question_index}/{len(self.blueprint)}
        Current Question: {self.blueprint[self.current_question_index] if self.blueprint and self.current_question_index < len(self.blueprint) else 'None'}
        """
        
        full_input = f"{context_str}\n\nUSER SAYS: {input_text}"
        
        logger.info(f"[{self.name}] Running turn in phase {self.phase}")
        response = await self.client.send_message(
            text=full_input,
            max_tool_calls=15
        )
        
        result_text = response.get("response", "")
        tool_results = response.get("tool_results", [])
        
        # --- STATE TRANSITION LOGIC (Heuristic inference from actions) ---
        
        # 1. State: INIT -> PLANNING
        if self.phase == "INIT":
             for tr in tool_results:
                 if tr.get('name') in ['get_assignment_details', 'get_upcoming_assignments']:
                     # Ideally we parse the result to get the title
                     if tr.get('success'):
                         # Fallback: Assume the agent found it. 
                         # In a real implementation we'd parse the 'assignment' key from the tool result
                         if 'assignment' in tr.get('result', {}): # hypothetical structure check
                             pass
             
             # If agent says "I have the instructions", we move to PLANNING
             if "plan" in result_text.lower() or "blueprint" in result_text.lower():
                 self.phase = "PLANNING"

        # 2. State: PLANNING -> COLLECTION
        elif self.phase == "PLANNING":
            # Check if Doc was created
            for tr in tool_results:
                if tr.get('name') == 'createGoogleDoc' and tr.get('tool_output', {}).get('success'):
                   self.doc_id = tr['tool_output']['doc_id']
                   self.doc_title = f"Assignment - {self.doc_id}" # Simplified
                   
            # Agent usually proposes the plan. If user says "Yes" (in next turn), agent moves to collection.
            # But here we are IN the turn. The agent might have just proposed it.
            # We rely on the NEXT prompt to switch phase if the user Agreed. 
            # Or... we parsing the agent's intent. 
            # Let's simple check: If doc exists and agent asks first question, we are in collection.
            if self.doc_id and "?" in result_text:
                self.phase = "COLLECTION"

        # 3. State: COLLECTION (Progress Tracking)
        elif self.phase == "COLLECTION":
            # Detected that agent appended an answer?
            appended = False
            for tr in tool_results:
                if tr.get('name') == 'insertText':
                    appended = True
            
            # If agent appended, it likely finished that question. 
            # Ideally the System Prompt instructs the agent to "Move to next" or "Ask for clarification"
            # We can advance index if the agent explicitly moved to next question or if the user is satisfied.
            # This is tricky without structured output from the agent saying "NEXT_QUESTION".
            # For this prototype, we'll let the agent manage the flow verbally, but we need to track index for the prompt.
            # Simple Hack: If the agent generated a tool call to append_doc, we assume progress.
            if appended:
                 # Check if agent asked the NEXT question in text
                 pass

            if "smooth it out" in result_text.lower() or "synthesis" in result_text.lower():
                self.phase = "SYNTHESIS"
                
        # 4. State: SYNTHESIS -> REFINEMENT
        elif self.phase == "SYNTHESIS":
            if "draft created" in result_text.lower() or "take a look" in result_text.lower():
                self.phase = "REFINEMENT"
                
        return {
            "success": True,
            "result": result_text,
            "state": {
                "phase": self.phase,
                "doc_id": self.doc_id
            }
        }
