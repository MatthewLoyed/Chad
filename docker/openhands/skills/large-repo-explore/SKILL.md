---
name: large-repo-explore
description: When a codebase is mounted at /repo and codegraph_* MCP tools are available, they are your primary way to understand the CODE — a pre-built symbol graph that returns real source in one call, far cheaper than find/grep/cat on a large repo.
---

# Large-Repo Exploration

`/repo` has already been **indexed** by codegraph — a symbol graph of every function, class, and
the call/import edges between them. The `codegraph_*` MCP tools query that index and hand you back
**real source code**, pre-filtered to what's structurally relevant. That is far cheaper than raw
`find`/`grep`/`cat`, where you re-read files and re-derive structure the graph already computed.

**Use the graph to understand code. Use the terminal only for non-code files.**

## Optimal flow — follow this order

1. **`codegraph_files`** — get the project's real file/package layout from the index (cheap, no
   code yet). This orients you: which packages actually exist, where the substantial modules are.
   **Call it with NO `path` argument to get the whole tree** (or a project-RELATIVE subdir like
   `"core"` — NEVER an absolute path like `/repo`; that filters to files under `/repo/repo` and
   returns nothing). Use this real layout for your architecture section — do not guess package
   names from symbols you saw; cite the directories/files the index actually reports.
2. **`codegraph_explore(query)`** — THE main tool. Pass a natural-language question ("how does the
   memory system work") OR a handful of symbol/file names ("MemoryEngine GraphRAGClient"). It walks
   the graph and returns the **verbatim source of the relevant symbols across multiple files in ONE
   call** — so you understand a whole area without opening files one by one. This is where the real
   savings are: it replaces a long grep → read → grep-again → read-again chase. Reach for it
   repeatedly, once per area you need to understand.
3. **`codegraph_callers` / `codegraph_callees` / `codegraph_impact`** — only when you need to trace
   beyond what `explore` returned: "what calls this", "what does this call", "what would changing
   this break". Multi-hop questions raw grep can't answer in one shot.
4. **`codegraph_node(symbol)`** — only for one specific symbol whose full body `explore` trimmed,
   or to disambiguate an overloaded name.
5. **`find` / `grep` / `cat`** — ONLY for things the graph does NOT index: README/config files,
   data, docs, or a language codegraph doesn't parse. NOT for general code exploration.

So: **`codegraph_files` → `codegraph_explore` (repeat) → callers/callees/impact if needed → node if
needed → terminal only for non-code.** Most of your understanding should come from step 2.

**Symbol search:** to find where a named symbol lives, run **`codegraph query <name>`** in the
terminal (add `-k class|function|method` to filter, `-j` for JSON) — same index, so it's exact, not
a text grep. (There is no `codegraph_search` MCP tool in your toolset; the CLI `query` is its
equivalent.) Prefer `codegraph_explore` when you want the source, not just the location.

## Why this matters

A prior explore-and-report run on a large repo with no graph retrieval burned ~412K tokens and
~19 minutes doing raw `find`/`grep`/`cat`. `codegraph_explore` turns "grep the whole tree" into a
handful of targeted calls that each return real, relevant source — build the map first, read narrowly.

## Anti-patterns
- ❌ Reaching for `ls -R`/`find`/`grep -r` to map the code when `codegraph_files` +
  `codegraph_explore` already have it indexed.
- ❌ `cat`-ing files one by one to understand an area instead of one `codegraph_explore` call that
  returns all the relevant source together.
- ❌ Stopping at `codegraph_files` and then falling back to raw file reading — `codegraph_explore`
  is the tool that actually saves you the reading. Use it.
- ❌ If a `codegraph_*` call errors (e.g. the index isn't ready), fall back to terminal tools and
  keep going — don't get stuck retrying.
