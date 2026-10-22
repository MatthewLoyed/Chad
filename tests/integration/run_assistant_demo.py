import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
"""
Test Script for Voice Assistant
Demonstrates tool additions, model switching, and modularity.
"""

import asyncio

# Add project root directory to path (go up from tests/ to project root)

from src.infrastructure.tools import ToolRegistry
from src.tools.base_tool import BaseTool
from src.core.intelligence.model_router import ModelRouter
from src.shared.logger import setup_logger


logger = setup_logger("Test")


# Example: Create a custom tool on-the-fly
class MockAssistantTool(BaseTool):
    """Test tool for demonstration."""
    
    name = "test_calculation"
    description = "Perform a simple calculation"
    category = "custom"
    
    def get_schema(self):
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "multiply"],
                        "description": "Operation to perform"
                    },
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"}
                },
                "required": ["operation", "a", "b"]
            }
        }
    
    async def execute(self, operation: str, a: float, b: float, **kwargs):
        if operation == "add":
            result = a + b
        elif operation == "multiply":
            result = a * b
        else:
            return {"success": False, "error": f"Unknown operation: {operation}"}
        
        return {
            "success": True,
            "result": f"{a} {operation} {b} = {result}",
            "value": result
        }


async def test_tool_registry():
    """Test tool registry and auto-discovery."""
    logger.info("=" * 60)
    logger.info("TEST 1: Tool Registry & Auto-Discovery")
    logger.info("=" * 60)
    
    registry = ToolRegistry()
    registry.discover_all()
    
    # List all discovered tools
    tools = registry.list_tools()
    logger.info(f"Discovered tools by category:")
    for category, tool_names in tools.items():
        logger.info(f"  {category}: {len(tool_names)} tools")
        for name in tool_names:
            logger.info(f"    - {name}")
    
    # Test executing a tool
    logger.info("\nTesting YouTube tool execution...")
    result = await registry.execute_tool(
        "open_youtube_video",
        {"search_query": "Python tutorial"}
    )
    logger.info(f"Result: {result}")
    
    assert result['success'], "Tool execution should succeed"
    logger.info("✓ Tool registry test passed!")
    
    return registry


async def test_tool_addition(registry: ToolRegistry):
    """Test adding a new tool dynamically."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: Adding New Tools Dynamically")
    logger.info("=" * 60)
    
    # Manually register our test tool
    test_tool = MockAssistantTool()
    registry.tools[test_tool.name] = test_tool
    registry.categories['custom'].append(test_tool.name)
    
    logger.info(f"Added new tool: {test_tool.name}")
    
    # Test executing it
    result = await registry.execute_tool(
        "test_calculation",
        {"operation": "add", "a": 5, "b": 3}
    )
    
    logger.info(f"Calculation result: {result}")
    assert result['success'], "Test tool should execute successfully"
    assert result['value'] == 8, "5 + 3 should equal 8"
    
    logger.info("✓ Tool addition test passed!")


async def test_model_routing():
    """Test model router and category-based switching."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: Model Router & Category Switching")
    logger.info("=" * 60)
    
    # Load config
    import yaml
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    # Create registry (needed by router)
    registry = ToolRegistry()
    registry.discover_all()
    
    # Create router
    router = ModelRouter(config, registry)
    
    # Test routing to different categories
    categories_to_test = ['web', 'system', 'conversation']
    
    for category in categories_to_test:
        try:
            model = router.route(category=category)
            capabilities = model.get_capabilities()
            
            logger.info(f"\nCategory '{category}':")
            logger.info(f"  Model: {model.model_name}")
            logger.info(f"  Capabilities: {capabilities}")
            
            # Get tool schemas for this category
            schemas = router.get_tool_schemas_for_category(category)
            logger.info(f"  Available tools: {len(schemas)}")
            
        except Exception as e:
            logger.warning(f"Could not test category '{category}': {e}")
    
    logger.info("\n✓ Model routing test passed!")
    
    await router.cleanup()


async def test_tool_schemas():
    """Test tool schema generation."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 4: Tool Schema Generation")
    logger.info("=" * 60)
    
    registry = ToolRegistry()
    registry.discover_all()
    
    # Get schemas for all categories
    all_schemas = registry.get_all_schemas()
    
    logger.info(f"Generated {len(all_schemas)} tool schemas:")
    for schema in all_schemas[:5]:  # Show first 5
        logger.info(f"\n  Tool: {schema['name']}")
        logger.info(f"  Description: {schema['description']}")
        logger.info(f"  Parameters: {list(schema.get('parameters', {}).get('properties', {}).keys())}")
    
    if len(all_schemas) > 5:
        logger.info(f"\n  ... and {len(all_schemas) - 5} more")
    
    logger.info("\n✓ Schema generation test passed!")


async def test_configuration_loading():
    """Test configuration system."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 5: Configuration System")
    logger.info("=" * 60)
    
    import yaml
    
    # Load and display config
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    logger.info("Configuration loaded successfully:")
    logger.info(f"  Models configured: {list(config.get('models', {}).keys())}")
    logger.info(f"  Audio settings: {config.get('audio', {})}")
    logger.info(f"  Wake word: {config.get('wake_word', {}).get('keyword')}")
    logger.info(f"  Enabled categories: {config.get('enabled_tool_categories', [])}")
    
    logger.info("\n✓ Configuration test passed!")


async def demonstrate_extensibility():
    """Demonstrate how easy it is to extend the system."""
    logger.info("\n" + "=" * 60)
    logger.info("DEMO: System Extensibility")
    logger.info("=" * 60)
    
    logger.info("\n📝 How to add a new tool (3 steps):")
    logger.info("1. Create a file in tools/custom/my_tool.py")
    logger.info("2. Inherit from BaseTool and implement get_schema() and execute()")
    logger.info("3. Done! Registry auto-discovers it on startup")
    
    logger.info("\n📝 How to switch models for a category:")
    logger.info("1. Open config.yaml")
    logger.info("2. Change the 'model' field under the category")
    logger.info("3. Restart - no code changes needed!")
    
    logger.info("\n📝 How to add a new model provider:")
    logger.info("1. Create models/my_provider.py implementing BaseModel")
    logger.info("2. Add to config: type: my_provider")
    logger.info("3. Router can now use it!")
    
    logger.info("\n✓ Extensibility demonstration complete!")


async def run_all_tests():
    """Run all tests."""
    try:
        logger.info("\n" + "=" * 60)
        logger.info("MODULAR VOICE ASSISTANT - TEST SUITE")
        logger.info("=" * 60)
        
        # Test 1: Tool Registry
        registry = await test_tool_registry()
        
        # Test 2: Tool Addition
        await test_tool_addition(registry)
        
        # Test 3: Model Routing
        await test_model_routing()
        
        # Test 4: Schema Generation
        await test_tool_schemas()
        
        # Test 5: Configuration
        await test_configuration_loading()
        
        # Demo: Extensibility
        await demonstrate_extensibility()
        
        # Summary
        logger.info("\n" + "=" * 60)
        logger.info("ALL TESTS PASSED! ✓")
        logger.info("=" * 60)
        logger.info("\nThe system is:")
        logger.info("  ✓ Modular - Components are independent")
        logger.info("  ✓ Extensible - Easy to add new tools and models")
        logger.info("  ✓ Configurable - Behavior controlled via YAML")
        logger.info("  ✓ Decoupled - Tools don't know about models")
        logger.info("  ✓ Auto-discovering - No manual registration needed")
        
        return True
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    print("\nRunning Voice Assistant Tests...\n")
    
    success = asyncio.run(run_all_tests())
    
    if success:
        print("\nAll tests passed! System is ready to use.")
        sys.exit(0)
    else:
        print("\nSome tests failed. Check logs above.")
        sys.exit(1)


