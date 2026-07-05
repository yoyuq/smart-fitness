import ai_planner


def test_workout_coach_remark_uses_short_timeout(monkeypatch):
    seen = {}

    def fake_call_llm(messages, max_tokens=600, temperature=0.6, prefer=None, chain=None, timeout=None):
        seen["timeout"] = timeout
        seen["chain"] = chain
        return "点评"

    monkeypatch.setattr(ai_planner, "REMARK_TIMEOUT", 2)
    monkeypatch.setattr(ai_planner, "REMARK_MAX_PROVIDERS", 1)
    monkeypatch.setenv("AI_REMARK_CHAIN", "deepseek,qwen")
    monkeypatch.setattr(ai_planner, "_call_llm", fake_call_llm)

    assert ai_planner.workout_coach_remark("squat", 10, 60, 80) == "点评"
    assert seen["timeout"] == 2
    assert seen["chain"] == "deepseek"


def test_workout_coach_remark_falls_back_when_llm_fails(monkeypatch):
    def fail_call_llm(*args, **kwargs):
        raise TimeoutError("slow provider")

    monkeypatch.setattr(ai_planner, "_call_llm", fail_call_llm)
    out = ai_planner.workout_coach_remark("squat", 10, 60, 55)
    assert "55" in out
    assert "下次" in out
