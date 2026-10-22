import pytest
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from src.core.intelligence.query_synthesizer import QuerySynthesizer

def test_standardize_history_empty():
    """Verify empty history produces an empty list safely."""
    assert QuerySynthesizer.standardize_history([]) == []
    assert QuerySynthesizer.standardize_history(None) == []

def test_standardize_history_mixed_types():
    """Verify it handles both dicts and LangChain base messages."""
    history = [
        {"role": "user", "content": "Hello"},
        HumanMessage(content="How are you?"),
        AIMessage(content="I am great."),
        SystemMessage(content="System override"),
        ToolMessage(content="Success", tool_call_id="123")
    ]
    
    canonical = QuerySynthesizer.standardize_history(history)
    
    assert len(canonical) == 5
    assert canonical[0] == {"role": "user", "content": "Hello"}
    assert canonical[1] == {"role": "user", "content": "How are you?"}
    assert canonical[2] == {"role": "assistant", "content": "I am great."}
    assert canonical[3] == {"role": "system", "content": "System override"}
    assert canonical[4] == {"role": "tool", "content": "Success"}

def test_decompose_simple_query():
    """Simple queries should yield single item list."""
    assert QuerySynthesizer.decompose("Check my email") == ["Check my email"]
    assert QuerySynthesizer.decompose("") == []

def test_decompose_compound_query():
    """Should split on 'and' and ','."""
    compound = "Check my email and list files on Desktop, run setup"
    parts = QuerySynthesizer.decompose(compound)
    
    # Note: logic replaces ',' with ' and ', then splits
    assert len(parts) == 3
    assert "Check my email" in parts
    assert "list files on Desktop" in parts
    assert "run setup" in parts

def test_enrich_with_history_basic():
    """Ensure previous message is prepended into query context."""
    history = [
        HumanMessage(content="What is fuel economy of a Civic?"),
        AIMessage(content="It is 35 MPG.")
    ]
    query = "Write that to a file"
    
    enriched = QuerySynthesizer.enrich_with_history(query, history)
    
    assert "user: What is fuel economy of a Civic?" in enriched
    assert "assistant: It is 35 MPG." in enriched
    assert "current_intent: Write that to a file" in enriched

def test_enrich_with_history_truncation():
    """Verify long history elements are truncated to avoid embedding bloat."""
    long_text = "A" * 500
    history = [
        HumanMessage(content=long_text)
    ]
    query = "Summarize it"
    
    enriched = QuerySynthesizer.enrich_with_history(query, history)
    
    # Length should be truncated to ~200 + ellipses + role tag
    assert len(enriched) < 300
    assert "A" * 200 + "..." in enriched

def test_enrich_with_history_filters_tools():
    """Assert that tool and system messages are excluded to prevent embedding skew."""
    history = [
        HumanMessage(content="Generate a sales chart"),
        ToolMessage(content='{"success": true, "chart_id": "img_129381"}', tool_call_id="1"),
        SystemMessage(content="Memory cleanup complete"),
        AIMessage(content="Done, I generated it.")
    ]
    query = "email it to bob"
    
    enriched = QuerySynthesizer.enrich_with_history(query, history)
    
    # Validate human/ai exist
    assert "user: Generate a sales chart" in enriched
    assert "assistant: Done, I generated it." in enriched
    assert "current_intent: email it to bob" in enriched
    
    # Validate tools and system instructions are excluded
    assert "tool:" not in enriched
    assert "system:" not in enriched
    assert "img_129381" not in enriched
    assert "Memory cleanup complete" not in enriched

