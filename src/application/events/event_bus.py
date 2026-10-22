"""
EventBus - Simple pub/sub event system for decoupled communication

This replaces direct callback wiring with a centralized event bus.
"""

import weakref
import asyncio
from typing import Dict, List, Callable, Any, Type, TypeVar
from dataclasses import dataclass
import logging
from src.shared.logger import setup_logger

logger = setup_logger("EventBus")

T = TypeVar('T', bound='Event')


@dataclass
class Event:
    """Base class for all events."""
    pass


class EventBus:
    """
    Centralized event bus for pub/sub communication.

    Features:
    - Typed events (subscribe to specific event classes)
    - WeakRef subscriptions (automatic cleanup on garbage collection)
    - Thread-safe subscription management
    - No memory leaks from forgotten subscriptions

    Usage:
        bus = EventBus()

        # Subscribe
        def handler(event: StateChanged):
            print(f"State: {event.new_state}")

        bus.subscribe(StateChanged, handler)

        # Publish
        bus.publish(StateChanged(new_state="thinking"))
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(EventBus, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        """Initialize empty event bus."""
        # Map event type -> list of weak references to handlers
        self._subscribers: Dict[Type[Event], List] = {}
        self._lock = None  # Will use asyncio.Lock if needed
        self._initialized = True

    @classmethod
    def get_instance(cls) -> 'EventBus':
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    def subscribe(self, event_type: Type[T], handler: Callable[[T], Any]) -> None:
        """
        Subscribe to events of a specific type.

        Args:
            event_type: The event class to listen for
            handler: Callback function that accepts the event

        Note:
            Uses weak references to prevent memory leaks.
            If the handler is garbage collected, subscription is auto-removed.
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []

        # Try to create weak reference
        # If handler is a bound method, we need special handling
        try:
            if hasattr(handler, '__self__'):
                # Bound method - store weak ref to object and method name
                obj = handler.__self__
                method_name = handler.__name__
                weak_ref = weakref.ref(obj, self._make_cleanup(event_type))
                self._subscribers[event_type].append(('method', weak_ref, method_name))
            else:
                # Regular function - store weak ref directly
                weak_ref = weakref.ref(handler, self._make_cleanup(event_type))
                self._subscribers[event_type].append(('func', weak_ref, None))
        except TypeError:
            # Can't create weak ref (e.g., lambda) - store strong ref
            logger.warning(f"Cannot create weak reference for handler {handler}, using strong ref")
            self._subscribers[event_type].append(('strong', handler, None))

    def _make_cleanup(self, event_type: Type[Event]) -> Callable:
        """Create a cleanup callback for when weak ref is deleted."""
        def cleanup(weak_ref):
            # Remove dead references
            if event_type in self._subscribers:
                self._subscribers[event_type] = [
                    sub for sub in self._subscribers[event_type]
                    if not (sub[0] in ('func', 'method') and sub[1]() is None)
                ]
        return cleanup

    def unsubscribe(self, event_type: Type[T], handler: Callable[[T], Any]) -> None:
        """
        Unsubscribe from events.

        Args:
            event_type: The event class to stop listening for
            handler: The callback function to remove
        """
        if event_type not in self._subscribers:
            return

        # Find and remove matching handler
        new_subs = []
        for sub_type, ref, method_name in self._subscribers[event_type]:
            if sub_type == 'strong':
                if ref != handler:
                    new_subs.append((sub_type, ref, method_name))
            elif sub_type == 'func':
                if ref() != handler:
                    new_subs.append((sub_type, ref, method_name))
            elif sub_type == 'method':
                obj = ref()
                if obj is not None:
                    if getattr(obj, method_name, None) != handler:
                        new_subs.append((sub_type, ref, method_name))

        self._subscribers[event_type] = new_subs

    def _run_coroutine_safely(self, coro: Any, event_type_name: str) -> None:
        """Run a coroutine in a background task and log errors on completion."""
        task = asyncio.create_task(coro)
        
        def done_callback(fut: asyncio.Future) -> None:
            try:
                fut.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error(
                    f"[ EVENT_BUS ] : Unhandled exception in async handler for {event_type_name}: {exc}",
                    exc_info=True
                )
                
        task.add_done_callback(done_callback)

    def publish(self, event: Event) -> None:
        """
        Publish an event to all subscribers.

        Args:
            event: The event instance to publish
        """
        event_type = type(event)

        if event_type not in self._subscribers:
            return

        # Clean up dead references and call handlers
        alive_subs = []
        for sub_type, ref, method_name in self._subscribers[event_type]:
            try:
                if sub_type == 'strong':
                    # Strong reference
                    result = ref(event)
                    if asyncio.iscoroutine(result):
                        self._run_coroutine_safely(result, event_type.__name__)
                    alive_subs.append((sub_type, ref, method_name))
                elif sub_type == 'func':
                    # Weak reference to function
                    handler = ref()
                    if handler is not None:
                        result = handler(event)
                        if asyncio.iscoroutine(result):
                            self._run_coroutine_safely(result, event_type.__name__)
                        alive_subs.append((sub_type, ref, method_name))
                elif sub_type == 'method':
                    # Weak reference to object with method name
                    obj = ref()
                    if obj is not None:
                        method = getattr(obj, method_name, None)
                        if method is not None:
                            result = method(event)
                            if asyncio.iscoroutine(result):
                                self._run_coroutine_safely(result, event_type.__name__)
                        alive_subs.append((sub_type, ref, method_name))
            except Exception as e:
                logger.error(f"Error in event handler for {event_type.__name__}: {e}", exc_info=True)

        # Update subscribers list with only alive ones
        self._subscribers[event_type] = alive_subs

    def clear(self) -> None:
        """Clear all subscriptions."""
        self._subscribers.clear()

    def subscriber_count(self, event_type: Type[Event]) -> int:
        """Get the number of subscribers for an event type."""
        if event_type not in self._subscribers:
            return 0
        return len(self._subscribers[event_type])
