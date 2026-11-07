"""Audited operations for the legacy GitHub MCP server (2025.4.8).

Verified against installed dist/index.js handlers and operations/*.js HTTP methods.
Exact names only; changes/new operations require review, not prefix inference.
"""
GITHUB_READS = frozenset({
    "search_repositories", "get_file_contents", "list_commits", "list_issues",
    "search_code", "search_issues", "search_users", "get_issue", "get_pull_request",
    "list_pull_requests", "get_pull_request_files", "get_pull_request_status",
    "get_pull_request_comments", "get_pull_request_reviews",
})
GITHUB_WRITES = frozenset({
    "create_or_update_file", "create_repository", "push_files", "create_issue",
    "create_pull_request", "fork_repository", "create_branch", "update_issue",
    "add_issue_comment", "create_pull_request_review", "merge_pull_request",
    "update_pull_request_branch",
})


def read_allowed(server, tool):
    annotations = tool.get("annotations") or {}
    if annotations.get("readOnlyHint") is False or annotations.get("destructiveHint") is True:
        return False
    command = getattr(server, "command", [])
    package = "@modelcontextprotocol/server-github"
    legacy = getattr(server, "name", None) == "github" and any(
        arg in (package, package + "@2025.4.8") for arg in command)
    if legacy:
        return tool.get("name") in GITHUB_READS
    return annotations.get("readOnlyHint") is True
