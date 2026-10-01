"""Private stdio MCP adapter for immutable Zeno research snapshots.

The trusted host binds one state directory. Tool arguments cannot select files or
change the research cohort. Domain validation and metrics remain in corner_research.
"""

import asyncio
import json
import os
from pathlib import Path
import re
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from modelfc import corner_research as research

ID_PATTERN = r"^[0-9a-f]{32}$"
MAX_MCP_RESULT_BYTES = research.MAX_RESPONSE_BYTES
SAFE_ERRORS = frozenset({
    "INVALID_RESEARCH_ID", "INVALID_RESEARCH_STORAGE", "INVALID_RESEARCH_REFERENCE",
    "INVALID_RESEARCH_EVIDENCE", "INVALID_RESEARCH_SNAPSHOT", "INVALID_RESEARCH_TIMESTAMP",
    "INVALID_RESEARCH_RELEASE", "UNSUPPORTED_RESEARCH_COHORT", "UNSUPPORTED_RESEARCH_SEGMENT",
    "RESEARCH_EVIDENCE_UNAVAILABLE", "RESEARCH_LIMIT_EXCEEDED", "RESEARCH_OUTPUT_LIMIT",
    "PREDICTION_NOT_IN_SNAPSHOT", "INVALID_RESEARCH_ARGUMENTS", "UNSUPPORTED_RESEARCH_TOOL",
})
_ID = {"type": "string", "pattern": ID_PATTERN, "minLength": 32, "maxLength": 32}
_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_TOOLS = (
    Tool(name="create_research_snapshot", description=(
        "Freeze current validated E1 team-corners evidence once when the user requests current evidence; "
        "reuse its snapshot_id for follow-up questions."),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        outputSchema={"type": "object"}, annotations=_WRITE),
    Tool(name="research_summary", description=(
        "Summarize frozen champion vs 180-day shadow quality, coverage and private hypothetical "
        "market decisions for one existing snapshot. Decision snapshots are not placed bets."),
        inputSchema={"type": "object", "properties": {"snapshot_id": {**_ID, "description": "ID of the frozen research snapshot."}},
                     "required": ["snapshot_id"], "additionalProperties": False},
        outputSchema={"type": "object"}, annotations=_READ),
    Tool(name="segment_comparison", description=(
        "Compare champion and shadow across a fixed venue or frozen venue-history grouping in the same snapshot."),
        inputSchema={"type": "object", "properties": {"snapshot_id": _ID,
                     "segment": {"type": "string", "enum": list(research.SEGMENTS)}},
                     "required": ["snapshot_id", "segment"], "additionalProperties": False},
        outputSchema={"type": "object"}, annotations=_READ),
    Tool(name="inspect_fixture", description=(
        "Inspect one prediction in this snapshot: frozen forecasts, historical context, private market "
        "observations and decisions, and settlement if available."),
        inputSchema={"type": "object", "properties": {"snapshot_id": _ID, "prediction_id": _ID},
                     "required": ["snapshot_id", "prediction_id"], "additionalProperties": False},
        outputSchema={"type": "object"}, annotations=_READ),
)
_ARGUMENTS = {
    "create_research_snapshot": frozenset(),
    "research_summary": frozenset({"snapshot_id"}),
    "segment_comparison": frozenset({"snapshot_id", "segment"}),
    "inspect_fixture": frozenset({"snapshot_id", "prediction_id"}),
}


def _execute(state: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in _ARGUMENTS:
        raise research.ResearchError("UNSUPPORTED_RESEARCH_TOOL")
    if not isinstance(arguments, dict) or arguments.keys() != _ARGUMENTS[name]:
        raise research.ResearchError("INVALID_RESEARCH_ARGUMENTS")
    if name != "create_research_snapshot":
        for key in ("snapshot_id", "prediction_id"):
            if key in arguments and (not isinstance(arguments[key], str)
                                     or not re.fullmatch(ID_PATTERN, arguments[key])):
                raise research.ResearchError("INVALID_RESEARCH_ID")
    if name == "create_research_snapshot":
        info = research.create_snapshot(state)
        summary = research.research_summary(state, info["snapshot_id"])
        return {"snapshot_id": info["snapshot_id"], "created_at_utc": info["created_at_utc"],
                "evidence_cutoff_utc": summary["evidence_cutoff_utc"],
                "competition": summary["competition"], "model_family": summary["family"],
                "coverage": {**summary["coverage"],
                             "paired_champion_shadow_predictions": summary["forecast_quality"]["shadow_predictions"],
                             "missing_shadow_predictions": summary["forecast_quality"]["missing_shadow_predictions"],
                             "missing_assessments": summary["market_decisions"]["missing_assessments"],
                             "later_only_targets_excluded": summary["market_decisions"]["later_only_targets_excluded"]},
                "warnings": summary["warnings"], "limitations": summary["limitations"]}
    if name == "research_summary":
        return research.research_summary(state, arguments["snapshot_id"])
    if name == "segment_comparison":
        if arguments["segment"] not in research.SEGMENTS:
            raise research.ResearchError("UNSUPPORTED_RESEARCH_SEGMENT")
        return research.segment_comparison(state, arguments["snapshot_id"], arguments["segment"])
    return research.inspect_fixture(state, arguments["snapshot_id"], arguments["prediction_id"])


def _result(state: Path, name: str, arguments: dict[str, Any]) -> CallToolResult:
    try:
        output = _execute(state, name, arguments)
        encoded = json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_MCP_RESULT_BYTES:
            raise research.ResearchError("RESEARCH_OUTPUT_LIMIT")
        # JSON text supports older clients; structuredContent supports typed tool use.
        return CallToolResult(content=[TextContent(type="text", text=encoded)], structuredContent=output)
    except research.ResearchError as error:
        code = str(error)
        return CallToolResult(content=[TextContent(type="text", text=code if code in SAFE_ERRORS else
                                                   "RESEARCH_EVIDENCE_UNAVAILABLE")], isError=True)
    except Exception:
        # Never return raw domain/storage exceptions, filesystem paths or traces.
        return CallToolResult(content=[TextContent(type="text", text="RESEARCH_EVIDENCE_UNAVAILABLE")], isError=True)


def build_server(state_dir: Path) -> Server:
    """Bind the trusted state directory outside MCP's model-controlled schema."""
    state = Path(state_dir)

    async def list_tools(_context, _params):
        return ListToolsResult(tools=list(_TOOLS))

    async def call_tool(_context, params):
        return await asyncio.to_thread(_result, state, params.name, params.arguments or {})

    return Server("zeno-private-research", version="0.1.0", instructions=(
        "Private E1 team-corners research. Create one snapshot only when asked for current evidence, "
        "then reuse its snapshot_id for every question in the investigation. Missing evidence is "
        "not a negative model result. Market snapshots and hypothetical units are not placed bets."
    ), on_list_tools=list_tools, on_call_tool=call_tool)


async def serve() -> None:
    state = Path(os.environ.get("MODELFC_STATE_DIR", "/var/lib/modelfc/state"))
    server = build_server(state)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
