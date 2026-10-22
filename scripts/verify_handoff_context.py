import re
from typing import Optional

def _extract_prompt_section(sys_instr: str, header_pattern: str) -> Optional[str]:
    """
    Extracts a specific section block from a compiled system prompt, 
    matching from the header until the next major markdown heading (###) or end of text.
    """
    if not sys_instr:
        return None
    pattern = rf"({header_pattern}.*?)(?=###|\Z)"
    match = re.search(pattern, sys_instr, re.DOTALL)
    return match.group(1).strip() if match else None

def run_test():
    sample_prompt = """### PERSONA COMPONENT: AGENT (Source: Cloud Master)
Instructions for the agent go here.
It has multiple lines.

### PERSONA COMPONENT: USER (Source: Cloud Master)
Name: Alex Example
Relationship Style: Bro / Peer
Timezone: CST

### PERSONAL CONTEXT
- User prefers concise answers with the conclusion first
- Primary dev machine runs Windows 11

"""

    print("=== EXTRACTOR UNIT TEST ===")
    
    # Test 1: User Profile extraction
    user = _extract_prompt_section(sample_prompt, r"### PERSONA COMPONENT: USER")
    print("\n[1] Extracted USER Section:")
    print("----------------------------")
    print(user)
    print("----------------------------")
    assert user is not None
    assert "Name: Alex Example" in user
    assert "### PERSONAL CONTEXT" not in user
    print("[+] Test 1 Passed")

    # Test 2: Memories extraction
    memories = _extract_prompt_section(sample_prompt, r"### PERSONAL CONTEXT")
    print("\n[2] Extracted MEMORIES Section:")
    print("----------------------------")
    print(memories)
    print("----------------------------")
    assert memories is not None
    assert "Primary dev machine" in memories
    assert "### PERSONA COMPONENT: USER" not in memories
    print("[+] Test 2 Passed")

    # Test 3: Missing section
    missing = _extract_prompt_section(sample_prompt, r"### PERSONA COMPONENT: PROMPT_INJECTION_DEFENSE")
    print("\n[3] Missing Section Extracted:")
    print(missing)
    assert missing is None
    print("[+] Test 3 Passed")

    # Test 4: Memory-only section
    memories_only_prompt = "### PERSONAL CONTEXT\nOnly memory\n"
    memories_only = _extract_prompt_section(memories_only_prompt, r"### PERSONAL CONTEXT")
    print("\n[4] Memories Only Section Extracted:")
    print("----------------------------")
    print(memories_only)
    print("----------------------------")
    assert memories_only == "### PERSONAL CONTEXT\nOnly memory"
    print("[+] Test 4 Passed")
    
    print("\n=== ALL REGEX EXTRACTION TESTS PASSED! ===")

if __name__ == "__main__":
    run_test()
