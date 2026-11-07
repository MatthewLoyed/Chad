"""Selected observation bindings without weakening planner control transitions."""


def selected_reads(state, registry):
    # Task tools are described in the confirmed menu; only Scout executes them.
    return []


def valid_phase_batch(calls, read_names, control_names, restricted):
    names = [call.get("name") for call in calls]
    if all(name in read_names for name in names):
        return True
    if restricted:
        return len(names) == 1 and names[0] in control_names
    return all(name in read_names or name in control_names for name in names)


def prepare_bindings(state, registry, planning, decision, coverage, decision_required, coverage_required):
    reads = selected_reads(state, registry)
    read_names = {tool.name for tool in reads}
    planning = planning + reads
    decision = decision + reads
    coverage = coverage + reads
    bound = decision if decision_required else coverage if coverage_required else planning
    return planning, decision, coverage, bound, read_names


def planning_history(state, gather_brief, evidence_context):
    from langchain_core.messages import ToolMessage
    history = list(state.get("messages") or [])
    if gather_brief and history:
        import json
        updated_history = []
        for msg in history:
            if isinstance(msg, ToolMessage) and (
                getattr(msg, "name", "") in ("dispatch_scout", "base_tool")
                or "Scout dispatched" in str(msg.content)
            ):
                updated_history.append(
                    ToolMessage(
                        content=json.dumps({"success": True, "result": "See SCOUT EVIDENCE for findings and gaps."} if evidence_context else {"success": True, "gather_brief": gather_brief, "result": gather_brief}),
                        tool_call_id=msg.tool_call_id,
                        name=getattr(msg, "name", "dispatch_scout"),
                    )
                )
            else:
                updated_history.append(msg)
        history = updated_history
    return history
