"""Hardened checkpointer variants that validate serializability before persisting.

Extracted from langgraph_engine.py (#302). SafeMemorySaver and SafeAsyncPostgresSaver
shared identical validation logic; it now lives once in _SerializationGuardMixin.
"""
import json
import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger("LangGraphEngine")


class _SerializationGuardMixin:
    """
    Intercepts aput/aput_writes to run a fast json.dumps serialization
    validation on the state/writes, warning (not raising) on failures so
    persistent checkpointers like AsyncPostgresSaver don't silently corrupt state.
    """
    def _validate_serializable(self, value: Any, context: str):
        from langchain_core.load.serializable import Serializable
        from langchain_core.messages import BaseMessage
        from langchain_core.load.dump import dumpd

        def default_serializer(obj):
            if isinstance(obj, BaseException):
                return str(obj)
            if isinstance(obj, (Serializable, BaseMessage)):
                try:
                    return dumpd(obj)
                except Exception:
                    pass
            if hasattr(obj, "to_json"):
                try:
                    return obj.to_json()
                except Exception:
                    pass
            if hasattr(obj, "dict"):
                try:
                    return obj.dict()
                except Exception:
                    pass
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

        try:
            # Quick serialization check using json.dumps with custom defaults
            json.dumps(value, default=default_serializer)
        except Exception as e:
            logger.warning(
                f"⚠️ [ HARDENING ] Non-serializable data detected in {context}! "
                f"This will cause issues in persistent database checkpointers like AsyncPostgresSaver. "
                f"Error: {e}. Value: {str(value)[:300]}"
            )

    async def aput(self, config, checkpoint, metadata, new_versions):
        # Validate checkpoint channels/values
        if checkpoint and "channel_values" in checkpoint:
            for channel, val in checkpoint["channel_values"].items():
                self._validate_serializable(val, f"channel '{channel}'")
        return await super().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id):
        # Validate writes
        for w in writes:
            if isinstance(w, tuple) and len(w) == 2:
                channel, val = w
                self._validate_serializable(val, f"write to channel '{channel}'")
        return await super().aput_writes(config, writes, task_id)


class SafeMemorySaver(_SerializationGuardMixin, MemorySaver):
    """A hardening subclass of MemorySaver — see _SerializationGuardMixin."""
    pass


class SafeAsyncPostgresSaver(_SerializationGuardMixin, AsyncPostgresSaver):
    """A hardening subclass of AsyncPostgresSaver — see _SerializationGuardMixin."""
    pass


class SafeAiosqliteSaver(_SerializationGuardMixin, AsyncSqliteSaver):
    """A hardening subclass of AsyncSqliteSaver — see _SerializationGuardMixin.

    Durable local-only checkpointer (issue #416): used instead of SafeAsyncPostgresSaver
    when Supabase isn't configured, so turn/graph state survives a Brain restart without
    requiring Postgres. Unlike SafeMemorySaver (in-process dict, lost on restart), this
    persists to a sqlite file on disk.
    """
    pass
