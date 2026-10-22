# Scout input isolation — 2026-10-19

Audit of the actual node assembly found that Scout inherited all non-blocklisted
supervisor-selected tool schemas, while its prose menu included blocked tools too.
It appended the composite state.system_instruction (memory, graph and activity
retrieval) and loaded always-scoped persona fragments. A local Scout overlay also
overrode the newer baseline. Discovered schemas bypassed the initial bind filter;
schemas already selected by the supervisor could fail to bind during discovery.

Scout now starts with independently resolved web_search, manage_user_memory,
search_for_tool, read_file and list_directory (when registered). Its displayed menu
comes from those actual bindings. Role assembly retains only the Scout fragment
plus the prompt builder's date block; the node adds the planner brief and original
user request. Composite supervisor background is no longer copied. Baseline and
local overlay agree; the old overlay is backed up privately under
.agent/experiments/chad-scout-context/.

Instructions require personal-memory/contact sources for personal facts, public
sources for public entities, itemized facts/gaps, and no task execution. Memory
save/forget operations are rejected. Known desktop mutations, saved routines,
connector management and generic MCP execution are blocked, including at discovery.
Task-specific retrieval remains discoverable, including tools already in supervisor
state. This is a focused repair, not a complete read-only capability sandbox: other
unclassified discovered tools still need policy classification.

The main-model inheritance setting is retained. No model inference, live task replay
or manual Brain restart was performed. Thirty focused backend tests passed. A real
local PromptRegistry warmup confirmed the effective Scout role is 1,915 characters;
the node then adds the brief, request and actual core-tool menu. This check does not
establish what an already-running process has cached or prove model compliance.


## Supervisor progressive disclosure follow-up

The supervisor now receives a bounded native capability directory from registered,
supervisor-callable tools. Example names are marked LOADED or DISCOVER; at most two
examples per group keep it short. MCP tools and specialist-only atoms remain in
the existing connector and agent rosters rather than being advertised as native.
Tool/skill discovery meta-tools stay bound. Routing, MCP and skill prose was
consolidated in the baseline and local overlays; obsolete blanket delegation and
duplicate static specialist rosters were removed. Previous local overlays are
backed up under .agent/experiments/chad-supervisor-directory/overlay-before/.
This does not change backend permission or approval policies.
