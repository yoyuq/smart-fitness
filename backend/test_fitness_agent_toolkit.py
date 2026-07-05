import json

from fitness_agent.mcp_client import clear_mcp_cache
from fitness_agent.tools import TOOL_SPECS, execute_tool, tool_specs_for_prompt
from fitness_agent.toolkit.registry import tool_names


EXPECTED_TOOLS = {
    "get_user_context_snapshot",
    "get_body_metrics",
    "get_recent_workouts",
    "get_exercise_summary",
    "get_session_rep_scores",
    "get_rep_analysis",
    "get_last_training_analysis",
    "get_scoring_evidence",
    "get_active_plans",
    "get_coach_memory",
    "get_memory_snapshot",
    "search_fitness_kb",
    "search_fitness_web",
    "analyze_rep_image",
    "analyze_rep_two_stage",
    "analyze_session_two_stage",
    "vision_providers_status",
    "analyze_pose_image_url",
    "todo_write",
    "save_coach_memory",
    "update_body_metrics",
    "draft_workout_plan",
    "create_workout_plan",
    "delete_workout_plan",
}


def test_modular_tool_registry_exports_expected_tools():
    spec_names = {item["name"] for item in TOOL_SPECS}
    assert spec_names == EXPECTED_TOOLS
    assert set(tool_names()) == EXPECTED_TOOLS
    assert len(TOOL_SPECS) == len(EXPECTED_TOOLS)


def test_tool_prompt_spec_is_valid_json(monkeypatch):
    monkeypatch.delenv("AI_AGENT_MCP_SERVERS_JSON", raising=False)
    clear_mcp_cache()
    data = json.loads(tool_specs_for_prompt())
    assert {item["name"] for item in data} == EXPECTED_TOOLS
    assert all("description" in item and "read_only" in item for item in data)


def test_unknown_tool_still_rejected():
    result = execute_tool(None, 1, "not_allowed", {})
    assert result["ok"] is False
    assert "not allowed" in result["error"]
