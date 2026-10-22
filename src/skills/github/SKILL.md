---
name: github
description: 'Advanced GitHub operations via Model Context Protocol for repository discovery, code auditing, issue tracking, and PR lifecycles. Triggers on: "create an issue on github", "open a PR", "search repository", "push files to github", "read file from github".'
metadata:
  version: 1.0.0
  category: systems
  tags: [github, mcp, tools, code-review, automation]
  difficulty: expert
---

All GitHub operations are orchestrated via the **GitHub Model Context Protocol (MCP) Server**. This provides enterprise-grade access directly to the GitHub REST & GraphQL APIs without needing local Git or GitHub CLI installations.

### Prerequisites
- **Auth**: Requires `GITHUB_PERSONAL_ACCESS_TOKEN` to be set in the environment configuration.
- **Runtime**: Managed by the system's Node execution environment via `npx`.

---

## 🛠️ GitHub Toolbelt Index

Use this index to quickly map your intent to the exact MCP tool designed for it.

| User Intent | Tool & Method | Guidance |
| :--- | :--- | :--- |
| **Discover Repository** | `search_repositories` | Find public or private repos by name, topics, or owner. |
| **Read File Contents** | `get_file_contents` | Fetch raw content of files or list directory trees. |
| **Atomic Code Search** | `search_code` | Run GitHub's high-speed indexed search (e.g., finding classes). |
| **Batch Multi-File Commit** | `push_files` | **PREFER THIS** for multi-file updates in a single atomic commit. |
| **Single File Update** | `create_or_update_file` | Good for quick single-file additions or edits. |
| **Track Work / Bugs** | `issue_write`, `issue_read` | Perform CRUD operations on GitHub Issues. |
| **Automated Reviews** | `pull_request_review_write` | Approve, comment, or request changes on standard Pull Requests. |
| **Ship Code** | `create_pull_request`, `merge_pull_request` | End-to-end PR lifecycle orchestration. |

---

## 📖 Workflow Playbooks

### 1. The Atomic Multi-File Commit (`push_files`)
When the user requests changes across multiple files, **never** make multiple serial API calls using `create_or_update_file`. Instead, aggregate all modifications and execute them simultaneously inside `push_files`.

**Workflow Pattern:**
1. **Gather Content**: Obtain the finalized code for all files you wish to commit.
2. **Define Mapping**: Construct a list of objects, each with `path` and `content`.
3. **Execute Push**:
   ```json
   {
     "owner": "owner-name",
     "repo": "repo-name",
     "branch": "feature-branch",
     "message": "feat: implement unified personal search capability",
     "files": [
       { "path": "src/main.py", "content": "..." },
       { "path": "src/utils/helpers.py", "content": "..." }
     ]
   }
   ```

### 2. Complete Pull Request Lifecycle
Use this canonical flow when tasked with submitting code to an existing repository:
1. **Branching**: Call `create_branch` from the default base branch (e.g., `main`).
2. **Pushes**: Use `push_files` or `create_or_update_file` to populate your new branch.
3. **Submission**: Call `create_pull_request` specifying the `head` (your branch) and `base` (e.g., `main`).
4. **Post-Checks**: Call `pull_request_read` with `method: "get_check_runs"` to verify status checks.

### 3. High-Resolution Code Audits
When reading codebase structures before doing deep architecture work:
- **Step 1**: Run `search_code` with query syntax like `repo:owner/name filename:config.py` to quickly jump to specific definitions.
- **Step 2**: Use `get_file_contents` to extract the logic.
- **Step 3**: Inspect history via `list_commits` to understand recent regressions or changes.

---

## ⚖️ Calibration Rules

1. **Never Poll**: When waiting for CI or PR reviews, do not repeatedly call list/status tools in a tight loop. Use the system notifications to inform the user if things are pending.
2. **Atomic Changes**: Group related edits into a single commit message following conventional commit guidelines (`feat:`, `fix:`, `chore:`).
3. **Path Safety**: Paths for `get_file_contents` and `push_files` are **relative to the repository root**. Never include absolute system paths or leading slashes.
4. **Filter Early**: Always specify `owner` and `repo` scope parameters immediately. Broadcasting queries globally will exhaust API limits and slow down responses.

---

## ⚠️ Limitations & Constraints
- **Binary File Management**: While you can write text, avoid using API endpoints to upload massive binary assets (videos, datasets). Use proper object storage if available.
- **Rebase / Merge Conflicts**: Standard GitHub APIs do not support interactive rebase with conflict markers natively. In the event of a hard merge conflict, report the conflict files directly to the user.
- **Pagination Guardrails**: API responses capped at `perPage: 100`. For massive lists, always utilize `pageInfo` cursors to step through datasets incrementally.
