"""
Provider Failover System - Usage Examples

Demonstrates how to use multi-provider failover for improved reliability.

Features:
    - Automatic provider switching on failures
    - Credential rotation (multiple API keys)
    - Cooldown tracking
    - Health monitoring

Setup:
    1. Configure providers in config/provider_profiles.yaml
    2. Set API keys in environment variables
    3. Enable multiple providers
    4. Failover happens automatically on errors
"""

import asyncio
from src.core.intelligence.provider_failover import (
    get_failover_manager,
    ErrorType
)
from src.core.orchestration.coordinators.model_coordinator import ModelCoordinator


# ==============================================================================
# BASIC USAGE
# ==============================================================================

def example_basic_setup():
    """
    Basic failover setup with Gemini → OpenAI.
    """
    # Get failover manager
    manager = get_failover_manager()

    # Check which providers are available
    providers = manager.list_providers()
    print("Available providers:")
    for p in providers:
        status = "✓" if p['available'] else "✗"
        print(f"  {status} {p['provider']}: {p['model']} (priority: {p['priority']})")


async def example_automatic_failover(model_coordinator: ModelCoordinator):
    """
    Example of automatic failover on provider failure.
    """
    try:
        # Select model (uses primary provider)
        model = await model_coordinator.select_model(category='conversation')

        # Simulate model request that fails
        # In production, this would be actual model.generate() call
        raise Exception("429 rate limit exceeded")

    except Exception as e:
        print(f"Primary provider failed: {e}")

        # Attempt failover to next provider
        model = await model_coordinator.try_failover(
            category='conversation',
            error=e
        )

        if model:
            print("✓ Failover successful! Switched to backup provider.")
        else:
            print("✗ Failover failed - no backup providers available.")


# ==============================================================================
# MONITORING & HEALTH CHECKS
# ==============================================================================

def example_monitor_providers():
    """
    Monitor provider health and availability.
    """
    manager = get_failover_manager()
    providers = manager.list_providers()

    print("\n=== Provider Health Dashboard ===\n")

    for p in providers:
        print(f"Provider: {p['provider']}")
        print(f"  Model: {p['model']}")
        print(f"  Priority: {p['priority']}")
        print(f"  Enabled: {p['enabled']}")
        print(f"  Available: {p['available']}")
        print(f"  Failures: {p['failure_count']}")

        if p['cooldown_until']:
            print(f"  Cooldown Until: {p['cooldown_until']}")

        if p['last_success']:
            print(f"  Last Success: {p['last_success']}")

        print()


def example_check_specific_provider():
    """
    Check if a specific provider is available.
    """
    manager = get_failover_manager()

    # Get provider profile
    gemini = manager.get_provider('gemini')

    if gemini:
        if gemini.is_available():
            print("✓ Gemini is available")
            api_key = gemini.get_current_api_key()
            print(f"  Current API key: {api_key[:10]}...")
        else:
            print("✗ Gemini is in cooldown")
            if gemini.cooldown_until:
                print(f"  Available at: {gemini.cooldown_until}")


# ==============================================================================
# ERROR HANDLING & RECOVERY
# ==============================================================================

def example_error_detection():
    """
    Demonstrate error type detection.
    """
    manager = get_failover_manager()

    # Test different error types
    errors = [
        Exception("429 Too Many Requests"),
        Exception("401 Unauthorized - Invalid API key"),
        Exception("Quota exceeded for billing period"),
        Exception("Request timeout after 30s"),
        Exception("500 Internal Server Error")
    ]

    for error in errors:
        error_type = manager.detect_error_type(error)
        print(f"Error: {str(error)[:50]}")
        print(f"  Classified as: {error_type.value}\n")


async def example_manual_provider_switching(model_coordinator: ModelCoordinator):
    """
    Manually switch providers without waiting for failure.
    """
    manager = get_failover_manager()

    # Get current provider status
    current = model_coordinator._current_provider
    print(f"Current provider: {current}")

    # Manually select fallback
    fallback = manager.get_next_provider(current_provider=current)

    if fallback:
        print(f"Switching to: {fallback.provider}")

        # Manually trigger switch
        model = await model_coordinator.try_failover(category='conversation')

        if model:
            print("✓ Manual switch successful")


# ==============================================================================
# CREDENTIAL ROTATION
# ==============================================================================

def example_api_key_rotation():
    """
    Demonstrate API key rotation on auth failures.
    """
    manager = get_failover_manager()
    gemini = manager.get_provider('gemini')

    if not gemini or len(gemini.api_keys) <= 1:
        print("⚠ Need multiple API keys for rotation example")
        return

    print(f"Provider has {len(gemini.api_keys)} API keys")

    # Simulate auth failure (triggers rotation)
    auth_error = Exception("401 Unauthorized - Invalid API key")
    manager.mark_failure('gemini', auth_error)

    # Key should be rotated
    new_key = gemini.get_current_api_key()
    print(f"Rotated to key: {new_key[:10]}...")


# ==============================================================================
# MAINTENANCE OPERATIONS
# ==============================================================================

def example_reset_provider():
    """
    Reset provider state to clear cooldowns.
    """
    manager = get_failover_manager()

    # Reset specific provider
    success = manager.reset_provider('gemini')
    if success:
        print("✓ Gemini provider reset")

    # Or reset all providers
    manager.reset_all()
    print("✓ All providers reset")


def example_enable_disable_failover():
    """
    Enable/disable failover system.
    """
    manager = get_failover_manager()

    # Disable failover (use only primary provider)
    manager.disable()
    print("✗ Failover disabled - using primary only")

    # Re-enable failover
    manager.enable()
    print("✓ Failover enabled")


# ==============================================================================
# ADVANCED SCENARIOS
# ==============================================================================

async def example_cascade_failover(model_coordinator: ModelCoordinator):
    """
    Demonstrate cascading failover through multiple providers.

    Gemini → OpenAI → Claude
    """
    manager = get_failover_manager()

    print("=== Cascade Failover Test ===\n")

    # Simulate Gemini failure
    print("1. Primary (Gemini) fails...")
    gemini_error = Exception("429 rate limit exceeded")
    model_coordinator.mark_provider_failure(gemini_error, 'gemini')

    # Try OpenAI
    print("2. Failing over to secondary (OpenAI)...")
    model = await model_coordinator.try_failover(category='conversation')

    if not model:
        print("   OpenAI unavailable")
        return

    # Simulate OpenAI failure
    print("3. Secondary (OpenAI) fails...")
    openai_error = Exception("Quota exceeded")
    model_coordinator.mark_provider_failure(openai_error, 'openai')

    # Try Claude
    print("4. Failing over to tertiary (Claude)...")
    model = await model_coordinator.try_failover(category='conversation')

    if model:
        print("✓ Final failover successful - using Claude")
    else:
        print("✗ All providers exhausted")


async def example_time_based_provider_selection(model_coordinator: ModelCoordinator):
    """
    Use different providers based on time of day (via hooks).

    Day: Use Pro model (quality)
    Night: Use Flash model (speed)
    """
    from datetime import datetime

    hour = datetime.now().hour

    if 22 <= hour or hour < 6:
        # Night time - use fast/cheap model
        print("Night mode: Using Gemini Flash")
        # Would trigger via hook in production
    else:
        # Day time - use quality model
        print("Day mode: Using Gemini Pro or OpenAI")


def example_cost_optimization():
    """
    Optimize costs by prioritizing cheaper providers.

    Priority order: Flash → GPT-4o-mini → Claude Haiku
    """
    manager = get_failover_manager()

    # Providers sorted by cost (cheapest first)
    providers = manager.list_providers()

    print("=== Cost-Optimized Provider Order ===\n")
    for p in providers:
        print(f"Priority {p['priority']}: {p['provider']} - {p['model']}")


# ==============================================================================
# INTEGRATION WITH HOOKS
# ==============================================================================

def example_failover_hook(context):
    """
    Hook that triggers failover on specific errors.

    Register via:
        orchestrator.hooks_manager.register('on_error', example_failover_hook)
    """
    error_type = context.metadata.get('error_type')

    if error_type in ['rate_limit', 'quota']:
        # Trigger failover
        context.metadata['trigger_failover'] = True
        print(f"Hook triggered failover for error: {error_type}")

    return context


# ==============================================================================
# TESTING & DEBUGGING
# ==============================================================================

async def example_test_failover_chain():
    """
    Test complete failover chain without real API calls.
    """
    manager = get_failover_manager()

    print("=== Failover Chain Test ===\n")

    # Simulate failures for each provider
    providers = ['gemini', 'openai', 'claude']

    for provider in providers:
        # Mark as failed
        error = Exception(f"Test failure for {provider}")
        manager.mark_failure(provider, error)

        # Check next available
        next_provider = manager.get_next_provider(current_provider=provider)

        if next_provider:
            print(f"{provider} failed → fallback to {next_provider.provider}")
        else:
            print(f"{provider} failed → no fallback available")

    # Reset for clean state
    manager.reset_all()
    print("\n✓ All providers reset")


def example_debug_provider_state():
    """
    Debug provider state for troubleshooting.
    """
    manager = get_failover_manager()

    print("=== Provider Debug Info ===\n")

    for provider_name in ['gemini', 'openai', 'claude']:
        profile = manager.get_provider(provider_name)

        if not profile:
            print(f"{provider_name}: Not configured")
            continue

        print(f"{provider_name}:")
        print(f"  Enabled: {profile.enabled}")
        print(f"  Available: {profile.is_available()}")
        print(f"  Failures: {profile.failure_count}/{profile.max_retries}")
        print(f"  API Keys: {len(profile.api_keys)}")
        print(f"  Current Key Index: {profile.current_key_index}")

        if profile.cooldown_until:
            from datetime import datetime
            remaining = (profile.cooldown_until - datetime.now()).total_seconds()
            print(f"  Cooldown: {remaining:.0f}s remaining")

        print()


# ==============================================================================
# USAGE NOTES
# ==============================================================================
"""
Configuration:
    1. Edit config/provider_profiles.yaml
    2. Set API keys in environment variables
    3. Enable desired providers
    4. Adjust priorities

Priorities:
    - Lower number = higher priority
    - 10 = primary (Gemini)
    - 20 = secondary (OpenAI)
    - 30 = tertiary (Claude)

Cooldown Durations:
    - rate_limit: 5 minutes
    - quota: 1 hour
    - auth: 15 minutes (or rotate key)
    - server_error: 2 minutes
    - timeout: 1 minute

Best Practices:
    1. Enable at least 2 providers for redundancy
    2. Use multiple API keys per provider
    3. Monitor provider health regularly
    4. Test failover before production
    5. Set appropriate priorities (cost vs quality)
    6. Reset cooldowns manually if needed

Integration:
    - Failover happens automatically in ModelCoordinator
    - Mark success after successful model calls
    - Mark failure on exceptions
    - try_failover() switches to backup provider
    - Hooks can trigger custom failover logic
"""


# ==============================================================================
# MAIN EXAMPLE
# ==============================================================================

async def main():
    """Run all examples."""
    print("=== Provider Failover Examples ===\n")

    # Basic setup
    example_basic_setup()

    # Monitoring
    example_monitor_providers()

    # Error detection
    example_error_detection()

    # Maintenance
    example_reset_provider()

    # Testing
    await example_test_failover_chain()

    print("\n✓ All examples completed")


if __name__ == "__main__":
    asyncio.run(main())
