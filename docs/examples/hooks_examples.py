"""
Example Hooks for ConversationOrchestrator

Demonstrates common hook patterns for customizing assistant behavior
without modifying core logic.

Hook Types:
    - before_turn_start: Before turn begins
    - after_turn_start: After turn initialized
    - before_model_resolve: Before model selection (can override model)
    - after_model_resolve: After model selected
    - before_prompt_build: Before prompt construction
    - after_prompt_build: After prompt built
    - before_tool_execute: Before tool execution
    - after_tool_execute: After tool completes
    - before_turn_complete: Before turn finalization
    - after_turn_complete: After turn finalized

Register hooks via:
    orchestrator.hooks_manager.register('hook_type', handler_function, priority=50)
"""

from datetime import datetime, time as datetime_time
from src.core.orchestration.hooks_manager import HookContext
import logging

logger = logging.getLogger("HooksExamples")


# ==============================================================================
# TIME-BASED HOOKS
# ==============================================================================

def time_based_model_selection(context: HookContext) -> HookContext:
    """
    Select different models based on time of day.

    Night (10PM-6AM): Use Flash (faster, cheaper)
    Day (6AM-10PM): Use Pro (better quality)
    """
    current_hour = datetime.now().hour

    if 22 <= current_hour or current_hour < 6:
        # Night time - use fast model
        context.metadata['preferred_model'] = 'gemini-2.5-flash'
        context.metadata['reason'] = 'Night mode - using faster model'
        logger.info("Time-based hook: Night mode activated")
    else:
        # Day time - use quality model
        context.metadata['preferred_model'] = 'gemini-2.5-pro'
        context.metadata['reason'] = 'Day mode - using quality model'

    return context


def work_hours_tool_restrictions(context: HookContext) -> HookContext:
    """
    Restrict certain tools during work hours.

    Work hours (9AM-5PM weekdays): Disable entertainment tools
    """
    now = datetime.now()
    is_weekday = now.weekday() < 5  # Monday=0, Friday=4
    is_work_hours = 9 <= now.hour < 17

    if is_weekday and is_work_hours:
        context.metadata['tool_policy'] = 'work'
        context.metadata['reason'] = 'Work hours - limited tool access'
        logger.info("Work hours hook: Restricted tool policy activated")
    else:
        context.metadata['tool_policy'] = 'personal'
        context.metadata['reason'] = 'Personal time - full tool access'

    return context


# ==============================================================================
# CONTEXT INJECTION HOOKS
# ==============================================================================

def inject_location_context(context: HookContext) -> HookContext:
    """
    Inject user's location into context for location-aware responses.
    """
    # In production, get from GPS/IP/user preferences
    context.metadata['location'] = {
        'city': 'San Francisco',
        'timezone': 'America/Los_Angeles',
        'coordinates': (37.7749, -122.4194)
    }
    logger.debug("Location context injected")
    return context


def inject_weather_context(context: HookContext) -> HookContext:
    """
    Inject current weather into context for weather-aware responses.
    """
    # In production, fetch from weather API
    context.metadata['weather'] = {
        'temperature': 72,
        'condition': 'sunny',
        'humidity': 65
    }
    logger.debug("Weather context injected")
    return context


def inject_user_preferences(context: HookContext) -> HookContext:
    """
    Inject user preferences into context for personalized responses.
    """
    # In production, load from database
    context.metadata['user_preferences'] = {
        'language': 'en',
        'formality': 'casual',
        'verbosity': 'concise',
        'topics_of_interest': ['technology', 'music', 'travel']
    }
    logger.debug("User preferences injected")
    return context


# ==============================================================================
# PROFILE-SPECIFIC HOOKS
# ==============================================================================

def focus_mode_restrictions(context: HookContext) -> HookContext:
    """
    Enable 'focus mode' that minimizes distractions.

    - Disables entertainment tools
    - Shortens responses
    - Disables proactive suggestions
    """
    focus_enabled = context.metadata.get('focus_mode', False)

    if focus_enabled:
        context.metadata['tool_policy'] = 'minimal'
        context.metadata['response_style'] = 'terse'
        context.metadata['disable_suggestions'] = True
        logger.info("Focus mode hook: Distraction-free mode activated")

    return context


def presentation_mode(context: HookContext) -> HookContext:
    """
    Enable 'presentation mode' for demos/presentations.

    - Read-only tools only
    - Faster responses
    - No side effects
    """
    presentation_enabled = context.metadata.get('presentation_mode', False)

    if presentation_enabled:
        context.metadata['tool_policy'] = 'read_only'
        context.metadata['preferred_model'] = 'gemini-2.5-flash'
        context.metadata['disable_side_effects'] = True
        logger.info("Presentation mode hook: Demo-safe mode activated")

    return context


# ==============================================================================
# ANALYTICS & LOGGING HOOKS
# ==============================================================================

def audit_logging_hook(context: HookContext) -> HookContext:
    """
    Log all turn activities for audit/compliance.
    """
    log_entry = {
        'timestamp': context.timestamp.isoformat(),
        'turn_id': context.turn_id,
        'initiator': context.initiator,
        'category': context.category,
        'session_key': context.session_key
    }

    # In production, write to audit log database
    logger.info(f"Audit log: {log_entry}")

    return context


def performance_tracking_hook(context: HookContext) -> HookContext:
    """
    Track performance metrics for analytics.
    """
    if context.hook_type.value == 'after_turn_complete':
        duration = context.metadata.get('duration', 0)
        tool_count = context.metadata.get('tool_count', 0)

        # In production, send to analytics service
        logger.info(f"Performance: turn_id={context.turn_id}, duration={duration:.2f}s, tools={tool_count}")

    return context


def user_interaction_tracking(context: HookContext) -> HookContext:
    """
    Track user interactions for usage analytics.
    """
    # Track turn types, categories, common queries
    analytics_data = {
        'turn_id': context.turn_id,
        'category': context.category,
        'initiator': context.initiator,
        'has_user_input': bool(context.user_input)
    }

    # In production, send to analytics platform
    logger.debug(f"User interaction: {analytics_data}")

    return context


# ==============================================================================
# ERROR HANDLING HOOKS
# ==============================================================================

def error_notification_hook(context: HookContext) -> HookContext:
    """
    Send notifications when errors occur.
    """
    if context.hook_type.value == 'on_error':
        error_details = context.metadata.get('error', 'Unknown error')

        # In production, send to monitoring service (Sentry, etc.)
        logger.error(f"Error notification: {error_details}")

        # Could also send email, Slack notification, etc.

    return context


def error_recovery_hook(context: HookContext) -> HookContext:
    """
    Attempt automatic error recovery.
    """
    if context.hook_type.value == 'on_error':
        error_type = context.metadata.get('error_type', 'unknown')

        if error_type == 'rate_limit':
            # Switch to different model/provider
            context.metadata['fallback_model'] = 'gemini-2.5-flash'
            logger.info("Error recovery: Switched to fallback model")

        elif error_type == 'quota_exceeded':
            # Notify user, suggest waiting
            context.metadata['user_message'] = "API quota exceeded. Please try again later."

    return context


# ==============================================================================
# SECURITY HOOKS
# ==============================================================================

def sensitive_data_filter(context: HookContext) -> HookContext:
    """
    Filter sensitive data from inputs before processing.
    """
    if context.user_input:
        # In production, use regex/ML to detect sensitive data
        import re

        # Redact potential credit card numbers
        context.user_input = re.sub(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[REDACTED]', context.user_input)

        # Redact potential SSNs
        context.user_input = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED]', context.user_input)

        logger.debug("Sensitive data filtered from input")

    return context


def rate_limiting_hook(context: HookContext) -> HookContext:
    """
    Implement custom rate limiting per session.
    """
    session_key = context.session_key

    # In production, track requests in Redis/database
    # For now, just log
    logger.debug(f"Rate limit check for session: {session_key}")

    # Could set metadata to block turn if rate limit exceeded
    # context.metadata['rate_limit_exceeded'] = True

    return context


# ==============================================================================
# REGISTRATION EXAMPLES
# ==============================================================================

def register_all_hooks(orchestrator):
    """
    Example function to register all hooks with the orchestrator.

    Usage:
        from examples.hooks_examples import register_all_hooks
        register_all_hooks(orchestrator)
    """

    # Time-based hooks
    orchestrator.hooks_manager.register('before_model_resolve', time_based_model_selection, priority=10)
    orchestrator.hooks_manager.register('before_turn_start', work_hours_tool_restrictions, priority=20)

    # Context injection
    orchestrator.hooks_manager.register('before_turn_start', inject_location_context, priority=30)
    orchestrator.hooks_manager.register('before_turn_start', inject_weather_context, priority=30)
    orchestrator.hooks_manager.register('before_turn_start', inject_user_preferences, priority=30)

    # Analytics & logging
    orchestrator.hooks_manager.register('after_turn_start', audit_logging_hook, priority=100)
    orchestrator.hooks_manager.register('after_turn_complete', performance_tracking_hook, priority=100)
    orchestrator.hooks_manager.register('after_turn_complete', user_interaction_tracking, priority=100)

    # Security
    orchestrator.hooks_manager.register('before_turn_start', sensitive_data_filter, priority=5)
    orchestrator.hooks_manager.register('before_turn_start', rate_limiting_hook, priority=5)

    logger.info("All example hooks registered")


def register_minimal_hooks(orchestrator):
    """
    Register only essential hooks for production use.
    """
    # Only critical hooks
    orchestrator.hooks_manager.register('before_turn_start', sensitive_data_filter, priority=5)
    orchestrator.hooks_manager.register('after_turn_complete', performance_tracking_hook, priority=100)
    orchestrator.hooks_manager.register('on_error', error_notification_hook, priority=50)

    logger.info("Minimal hooks registered")


# ==============================================================================
# ADVANCED HOOK EXAMPLES
# ==============================================================================

async def async_context_enrichment_hook(context: HookContext) -> HookContext:
    """
    Example of async hook that fetches data from external APIs.
    """
    import asyncio

    # Simulate API call
    await asyncio.sleep(0.1)

    # In production, fetch from real APIs
    context.metadata['enriched_data'] = {
        'news_headlines': ['Tech news 1', 'Tech news 2'],
        'stock_prices': {'AAPL': 150.25, 'GOOGL': 140.50}
    }

    logger.debug("Async context enrichment completed")
    return context


def conditional_hook_example(context: HookContext) -> HookContext:
    """
    Example of hook that only runs under specific conditions.
    """
    # Only run for specific categories
    if context.category in ['web', 'research']:
        context.metadata['enable_web_search'] = True
        logger.debug("Web search enabled for category: " + context.category)

    return context


# ==============================================================================
# USAGE NOTES
# ==============================================================================
"""
Hook Priorities:
    - 1-10: Critical security hooks (data filtering, auth)
    - 11-30: Context modification hooks (time-based, profile-based)
    - 31-60: Context injection hooks (location, weather, preferences)
    - 61-90: Business logic hooks
    - 91-100: Analytics and logging hooks

Hook execution order:
    - Hooks are executed in priority order (lower = earlier)
    - Multiple hooks of same priority execute in registration order
    - Failed hooks don't stop execution (logged and skipped)

Best Practices:
    1. Keep hooks fast - avoid blocking operations
    2. Use async hooks for I/O operations
    3. Handle errors gracefully - don't break orchestration
    4. Log hook activities for debugging
    5. Test hooks independently before registration
    6. Document hook dependencies and requirements
    7. Use priority to control execution order
    8. Return modified context to pass changes downstream
"""
