"""Connector opt-in policy — MCP and native connector enablement."""

from src.infrastructure.connectors.policy import (
    ensure_connector_defaults,
    get_effective_mcp_servers,
    mcp_credentials_satisfied,
    merge_mcp_configs,
    normalize_mcp_env_for_storage,
    persist_connector_secrets,
    native_connector_status,
    NATIVE_CONNECTOR_IDS,
    SHIPPED_MCP_SERVER_IDS,
)
from src.infrastructure.connectors.schemas import (
    CONNECTOR_SCHEMAS,
    get_all_connector_schemas,
    get_connector_schema,
)

__all__ = [
    "ensure_connector_defaults",
    "get_effective_mcp_servers",
    "mcp_credentials_satisfied",
    "merge_mcp_configs",
    "normalize_mcp_env_for_storage",
    "persist_connector_secrets",
    "native_connector_status",
    "NATIVE_CONNECTOR_IDS",
    "SHIPPED_MCP_SERVER_IDS",
    "CONNECTOR_SCHEMAS",
    "get_all_connector_schemas",
    "get_connector_schema",
]

