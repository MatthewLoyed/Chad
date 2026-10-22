"""
Tool Policy Manager - Fine-grained tool access control (OpenClaw Pattern)

Provides profile-based tool policies with wildcard patterns, allow/deny lists,
and tool groups for context-specific restrictions.

Features:
- Profile-based policies (base, restricted, admin, custom)
- Wildcard pattern matching (e.g., "gmail_*", "*_send")
- Tool groups (e.g., "@office" = gmail + calendar)
- Cascading allow/deny evaluation
- Policy violation detection

Examples:
    # Basic usage
    manager = ToolPolicyManager()
    manager.load_policies("config/tool_policies.yaml")

    # Check if tool is allowed
    if manager.is_allowed("gmail_send", profile="restricted"):
        # Execute tool

    # Get filtered tool list
    allowed_tools = manager.filter_tools(all_tools, profile="base")

OpenClaw Pattern Source:
    - Profile-based policies from OpenClaw gateway architecture
    - Enables work/personal context switching, security restrictions, etc.
"""

import re
import yaml
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass
from pathlib import Path

from src.shared.logger import setup_logger

logger = setup_logger("ToolPolicyManager")


@dataclass
class ToolPolicy:
    """
    Tool policy configuration for a specific profile.

    Attributes:
        profile: Profile name (e.g., "base", "restricted", "admin")
        allow_patterns: List of allowed tool patterns (supports wildcards)
        deny_patterns: List of denied tool patterns (supports wildcards)
        tool_groups: Named groups of tools (e.g., "@office": ["gmail_*", "calendar_*"])
        description: Human-readable policy description
    """
    profile: str
    allow_patterns: List[str]
    deny_patterns: List[str]
    tool_groups: Dict[str, List[str]]
    description: str = ""

    def __post_init__(self):
        """Expand tool groups into patterns."""
        # Expand @group references in allow/deny patterns
        self.allow_patterns = self._expand_groups(self.allow_patterns)
        self.deny_patterns = self._expand_groups(self.deny_patterns)

    def _expand_groups(self, patterns: List[str]) -> List[str]:
        """
        Expand @group references into their constituent patterns.

        Example:
            patterns = ["brave_search", "@office"]
            tool_groups = {"office": ["gmail_*", "calendar_*"]}
            result = ["brave_search", "gmail_*", "calendar_*"]
        """
        expanded = []
        for pattern in patterns:
            if pattern.startswith('@'):
                # Group reference
                group_name = pattern[1:]
                if group_name in self.tool_groups:
                    expanded.extend(self.tool_groups[group_name])
                else:
                    logger.warning(f"Unknown tool group: @{group_name}")
            else:
                expanded.append(pattern)
        return expanded


class ToolPolicyManager:
    """
    Manages tool access policies with profile-based restrictions.

    Policy Evaluation Logic:
        1. If tool matches any deny pattern -> DENY
        2. If tool matches any allow pattern -> ALLOW
        3. Default: DENY (whitelist approach for security)

    Wildcard Support:
        - "*" matches any characters: "gmail_*" matches "gmail_send", "gmail_search"
        - "?" matches single character: "test_?" matches "test_1", "test_a"

    Tool Groups:
        - Define named groups: "@office" = ["gmail_*", "calendar_*", "drive_*"]
        - Reference in policies: allow_patterns = ["brave_search", "@office"]
    """

    def __init__(self):
        """Initialize policy manager with no policies loaded."""
        self._policies: Dict[str, ToolPolicy] = {}
        self._tool_groups: Dict[str, List[str]] = {}
        self._active_profile: str = "base"
        self._cache: Dict[str, bool] = {}  # (profile, tool_name) -> is_allowed

        # Risk-based human-in-the-loop approval gate. Patterns here force a tool
        # call through the approval handshake (src/core/orchestration/approval_gate.py)
        # regardless of allow/deny. Covers MCP/skill tools that have no Python
        # class to set requires_approval. Expanded with @group references.
        self._approval_enabled: bool = True
        self._approval_patterns: List[str] = []

        logger.debug("ToolPolicyManager initialized")

    def load_policies(self, config_path: str) -> None:
        """
        Load tool policies from YAML configuration file.

        Args:
            config_path: Path to tool_policies.yaml

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config format is invalid
        """
        config_file = Path(config_path)

        if not config_file.exists():
            logger.warning(f"Policy config not found: {config_path}, using default (allow all)")
            self._create_default_policy()
            return

        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)

            # Load global tool groups
            self._tool_groups = config.get('tool_groups', {})

            # Load risk-based approval gate config
            self._approval_enabled = config.get('approval_enabled', True)
            self._approval_patterns = self._expand_group_refs(config.get('approval', []) or [])

            # Load profiles
            profiles_config = config.get('profiles', {})
            for profile_name, profile_data in profiles_config.items():
                policy = ToolPolicy(
                    profile=profile_name,
                    allow_patterns=profile_data.get('allow', []),
                    deny_patterns=profile_data.get('deny', []),
                    tool_groups=self._tool_groups,
                    description=profile_data.get('description', '')
                )
                self._policies[profile_name] = policy

            # Set active profile from config or default to 'base'
            self._active_profile = config.get('active_profile', 'base')

            # Validate active profile exists
            if self._active_profile not in self._policies:
                logger.warning(
                    f"Active profile '{self._active_profile}' not found in config, "
                    f"defaulting to 'base'"
                )
                self._active_profile = 'base'
                if 'base' not in self._policies:
                    self._create_default_policy()

            logger.debug(
                f"Loaded {len(self._policies)} tool policies. "
                f"Active profile: {self._active_profile}"
            )

        except Exception as e:
            logger.error(f"Failed to load tool policies from {config_path}: {e}")
            self._create_default_policy()

    def _create_default_policy(self) -> None:
        """Create permissive default policy (allow all tools)."""
        self._policies['base'] = ToolPolicy(
            profile='base',
            allow_patterns=['*'],  # Allow all
            deny_patterns=[],
            tool_groups={},
            description='Default permissive policy'
        )
        self._active_profile = 'base'
        logger.info("Created default 'base' policy (allow all)")

    def is_allowed(self, tool_name: str, profile: Optional[str] = None) -> bool:
        """
        Check if a tool is allowed under the given profile.

        Args:
            tool_name: Name of the tool to check
            profile: Policy profile to use (defaults to active profile)

        Returns:
            True if tool is allowed, False otherwise

        Policy Evaluation:
            1. Check deny patterns (highest priority)
            2. Check allow patterns
            3. Default: deny (whitelist approach)
        """
        profile = profile or self._active_profile

        # Check cache
        cache_key = f"{profile}:{tool_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Get policy
        policy = self._policies.get(profile)
        if not policy:
            logger.warning(f"Policy '{profile}' not found, denying tool '{tool_name}'")
            result = False
        else:
            # Evaluate policy
            result = self._evaluate_policy(tool_name, policy)

        # Cache result
        self._cache[cache_key] = result
        return result

    def _evaluate_policy(self, tool_name: str, policy: ToolPolicy) -> bool:
        """
        Evaluate if tool_name matches policy rules.

        Logic:
            1. If matches deny pattern -> DENY
            2. If matches allow pattern -> ALLOW
            3. Default -> DENY
        """
        # Check deny patterns first (highest priority)
        for pattern in policy.deny_patterns:
            if self._match_pattern(tool_name, pattern):
                logger.debug(f"Tool '{tool_name}' denied by pattern '{pattern}' in profile '{policy.profile}'")
                return False

        # Check allow patterns
        for pattern in policy.allow_patterns:
            if self._match_pattern(tool_name, pattern):
                logger.debug(f"Tool '{tool_name}' allowed by pattern '{pattern}' in profile '{policy.profile}'")
                return True

        # Default: deny (whitelist approach)
        logger.debug(f"Tool '{tool_name}' not in allow list for profile '{policy.profile}', denying")
        return False

    def _match_pattern(self, tool_name: str, pattern: str) -> bool:
        """
        Check if tool_name matches pattern (supports wildcards).

        Examples:
            _match_pattern("gmail_send", "gmail_*") -> True
            _match_pattern("gmail_send", "*_send") -> True
            _match_pattern("brave_search", "gmail_*") -> False
        """
        # Convert wildcard pattern to regex
        regex_pattern = pattern.replace('*', '.*').replace('?', '.')
        regex_pattern = f"^{regex_pattern}$"

        return bool(re.match(regex_pattern, tool_name))

    def _expand_group_refs(self, patterns: List[str]) -> List[str]:
        """Expand @group references against the loaded global tool groups."""
        expanded: List[str] = []
        for pattern in patterns:
            if pattern.startswith('@'):
                group_name = pattern[1:]
                if group_name in self._tool_groups:
                    expanded.extend(self._tool_groups[group_name])
                else:
                    logger.warning(f"Unknown tool group in approval list: @{group_name}")
            else:
                expanded.append(pattern)
        return expanded

    def requires_approval(self, tool_name: str) -> bool:
        """
        Whether a tool call must pass through the human-in-the-loop approval
        gate, based on the risk-based `approval` patterns in tool_policies.yaml.

        This is independent of allow/deny (is_allowed): a tool can be allowed
        AND require approval. Returns False when the gate is globally disabled.
        """
        if not self._approval_enabled:
            return False
        return any(self._match_pattern(tool_name, p) for p in self._approval_patterns)

    def approval_prompt(self, tool_name: str) -> str:
        """Human-facing prompt for a policy-gated tool (no per-call params)."""
        pretty = tool_name.replace('_', ' ').strip()
        return f"Allow Chad to run “{pretty}”? This action may have side effects."

    def filter_tools(self, tools: List[str], profile: Optional[str] = None) -> List[str]:
        """
        Filter a list of tool names based on policy.

        Args:
            tools: List of tool names
            profile: Policy profile to use (defaults to active profile)

        Returns:
            Filtered list of allowed tool names
        """
        profile = profile or self._active_profile
        return [tool for tool in tools if self.is_allowed(tool, profile)]

    def get_available_tools(self, profile: Optional[str] = None) -> Set[str]:
        """
        Get set of explicitly allowed tool patterns for a profile.

        Note: This returns patterns, not actual tool names.
        Use filter_tools() to filter actual tools.

        Args:
            profile: Policy profile (defaults to active profile)

        Returns:
            Set of allowed tool patterns
        """
        profile = profile or self._active_profile
        policy = self._policies.get(profile)

        if not policy:
            return set()

        return set(policy.allow_patterns)

    def set_active_profile(self, profile: str) -> None:
        """
        Change the active policy profile.

        Args:
            profile: Profile name to activate

        Raises:
            ValueError: If profile doesn't exist
        """
        if profile not in self._policies:
            available = list(self._policies.keys())
            raise ValueError(
                f"Profile '{profile}' not found. "
                f"Available profiles: {available}"
            )

        self._active_profile = profile
        self._cache.clear()  # Clear cache when profile changes
        logger.info(f"Switched to policy profile: {profile}")

    def get_active_profile(self) -> str:
        """Get the currently active policy profile."""
        return self._active_profile

    def list_profiles(self) -> List[str]:
        """List all available policy profiles."""
        return list(self._policies.keys())

    def get_policy_info(self, profile: Optional[str] = None) -> Dict[str, Any]:
        """
        Get information about a policy profile.

        Args:
            profile: Profile name (defaults to active profile)

        Returns:
            Dictionary with policy details (description, allow/deny patterns)
        """
        profile = profile or self._active_profile
        policy = self._policies.get(profile)

        if not policy:
            return {}

        return {
            'profile': policy.profile,
            'description': policy.description,
            'allow_patterns': policy.allow_patterns,
            'deny_patterns': policy.deny_patterns,
            'tool_groups': policy.tool_groups
        }

    def clear_cache(self) -> None:
        """Clear the policy evaluation cache."""
        self._cache.clear()
        logger.debug("Policy evaluation cache cleared")


# Global singleton instance (lazy-loaded)
_policy_manager: Optional[ToolPolicyManager] = None


def get_policy_manager() -> ToolPolicyManager:
    """
    Get the global ToolPolicyManager instance.

    Returns:
        Singleton ToolPolicyManager instance
    """
    global _policy_manager

    if _policy_manager is None:
        _policy_manager = ToolPolicyManager()
        # Attempt to load default config
        try:
            _policy_manager.load_policies("config/tool_policies.yaml")
        except Exception as e:
            logger.warning(f"Failed to load default policies: {e}")

    return _policy_manager
