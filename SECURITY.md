# Security Policy

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, use GitHub's private reporting:
**Security → Report a vulnerability** on this repository
(<https://github.com/MatthewLoyed/Chad/security/advisories/new>).

You'll get an acknowledgement as soon as possible. Please include:

- A description of the issue and its impact
- Steps to reproduce (or a proof-of-concept)
- Affected component (Brain server, Limb GUI, web client, a specific tool/skill)

## Scope & threat model

Chad is a **self-hosted** assistant. Each user runs their own Brain, holds their
own API keys, and grants their own OS/tool permissions. The most sensitive areas:

- **Brain WebSocket/HTTP server** (`src/remote/server.py`, port 8767) — arbitrary
  input from clients; the boundary between the network and tool execution.
- **Tool execution** (`src/infrastructure/tools/`, `src/tools/`) — tools can run
  code, touch the filesystem, and hit external APIs.
- **MCP servers** (`config/config.yaml → mcp_servers`) — third-party processes.
- **Secrets handling** (`config/secrets.yaml`, `ConfigManager`) — API keys.

Potentially destructive or high-privilege capabilities (computer use, system
automation, unsandboxed code execution) are **disabled by default** and must be
explicitly enabled by the operator.

## What is NOT a vulnerability

- A user enabling a documented high-privilege feature on their own machine.
- Missing hardening for multi-tenant/hosted deployment — Chad currently targets
  single-user self-hosting. Multi-tenant auth is tracked separately.

## Handling secrets

Never include real secrets in reports, issues, or PRs. If you believe a secret
was committed, report it privately so it can be rotated and purged.
