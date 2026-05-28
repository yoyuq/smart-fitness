"""
voice_coach.py - AI Voice Coach
================================
Generates Chinese voice feedback using edge-tts (Microsoft Edge TTS).
Provides pre-defined phrases per exercise type and per-rep analysis.

edge-tts: https://github.com/rany2/edge-tts (MIT License)
"""
import asyncio
import base64
import os
import time
from typing import Optional, List, Dict

from logger import get_logger

log = get_logger("voice_coach")

# ── Exercise-specific feedback phrases ────────────────────────────────────────

EXERCISE_PHRASES: Dict[str, Dict[str, List[str]]] = {
    "squat": {
        "praise": ["下蹲很标准！", "膝盖角度很好！", "深蹲动作到位！", "保持这个节奏！"],
        "correct": ["再蹲深一点！", "腰背挺直！", "膝盖别过脚尖！", "重心放脚后跟！"],
        "encourage": ["再来一个！", "加油！", "坚持住！", "很好！"],
    },
    "push_up": {
        "praise": ["俯卧撑很标准！", "身体保持直线！", "动作干净利落！"],
        "correct": ["身体要成一条直线！", "下去再深一点！", "核心收紧！"],
        "encourage": ["再来一个！", "加油！", "坚持住！", "你可以的！"],
    },
    "lunge": {
        "praise": ["弓步很标准！", "膝盖角度很好！", "保持平衡！"],
        "correct": ["前腿膝盖别过脚尖！", "后腿蹬直！", "上身保持直立！"],
        "encourage": ["再来一个！", "加油！", "坚持住！"],
    },
    "bicep_curl": {
        "praise": ["弯举动作标准！", "手臂发力很好！", "控制节奏！"],
        "correct": ["大臂贴紧身体！", "手腕保持中立位！", "别借力甩起来！"],
        "encourage": ["再来一个！", "加油！", "继续！"],
    },
    "shoulder_press": {
        "praise": ["推举动作标准！", "肩部发力很好！", "控制下降速度！"],
        "correct": ["核心收紧！", "手肘别太靠后！", "保持肩胛稳定！"],
        "encourage": ["再来一个！", "加油！", "坚持住！"],
    },
    "plank": {
        "praise": ["平板支撑很稳！", "核心发力很好！", "保持这个姿势！"],
        "correct": ["臀部别抬太高！", "腰别塌下去！", "收紧腹部！"],
        "encourage": ["坚持住！", "加油！", "还有十秒！"],
    },
    "jumping_jack": {
        "praise": ["开合跳很标准！", "节奏很好！", "手脚配合不错！"],
        "correct": ["手臂举过头顶！", "双脚跳开再合拢！", "保持呼吸节奏！"],
        "encourage": ["再来一个！", "加油！", "坚持住！"],
    },
}

# Fallback for any unrecognized exercise
DEFAULT_PHRASES = {
    "praise": ["动作很标准！", "做得很好！", "保持这个水平！"],
    "correct": ["注意姿势！", "放慢速度感受发力！", "保持身体稳定！"],
    "encourage": ["再来一个！", "加油！", "坚持住！", "你可以的！"],
}

_WORKOUT_COMPLETION = {
    "good": "训练完成！本次表现优秀，继续加油！",
    "average": "训练完成！整体不错，注意动作规范性会更好！",
    "needs_work": "训练完成！建议多关注动作标准度，慢一点感受发力。",
}


def _get_phrases(exercise: str, category: str) -> List[str]:
    """Get phrases for an exercise type and category."""
    ex_key = exercise.lower().replace(" ", "_")
    phrases = EXERCISE_PHRASES.get(ex_key, DEFAULT_PHRASES).get(category, DEFAULT_PHRASES[category])
    return phrases


def pick_phrase(exercise: str, category: str, seed: Optional[int] = None) -> str:
    """Pick a random phrase deterministically if seed provided."""
    phrases = _get_phrases(exercise, category)
    if seed is not None:
        return phrases[seed % len(phrases)]
    # Use current ms for pseudo-random
    idx = int(time.time() * 1000) % len(phrases)
    return phrases[idx]


def generate_workout_summary(
    exercise: str, total_reps: int, avg_score: float, rep_scores: List[Dict]
) -> str:
    """Generate a Chinese workout summary voice script."""
    score_level = "好" if avg_score >= 80 else ("中" if avg_score >= 60 else "待提高")
    grade = "优秀" if avg_score >= 85 else ("良好" if avg_score >= 70 else ("一般" if avg_score >= 60 else "需要加油"))
    
    summary = f"训练报告。你完成了{total_reps}次{exercise}。"
    summary += f"平均得分{avg_score:.0f}分，表现{grade}。"
    
    if rep_scores:
        best = max(rep_scores, key=lambda r: r["score"])
        worst = min(rep_scores, key=lambda r: r["score"])
        summary += f"最好的一次是第{best['rep']}次，{best['score']:.0f}分。"
        summary += f"需要改进的是第{worst['rep']}次，{worst['score']:.0f}分。"
    
    if avg_score >= 80:
        summary += " " + _WORKOUT_COMPLETION["good"]
    elif avg_score >= 60:
        summary += " " + _WORKOUT_COMPLETION["average"]
    else:
        summary += " " + _WORKOUT_COMPLETION["needs_work"]
    
    return summary


async def text_to_speech(text: str, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%") -> Optional[str]:
    """
    Convert text to speech using edge-tts.
    Returns base64-encoded MP3 audio, or None on failure.
    """
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]
        if audio_bytes:
            return base64.b64encode(audio_bytes).decode()
        return None
    except Exception as e:
        log.warning(f"TTS failed: {e}")
        return None


if __name__ == "__main__":
    # Test
    async def test():
        audio = await text_to_speech("测试语音指导功能。深蹲训练开始，请保持背部挺直。", rate="-10%")
        print(f"TTS OK: {len(audio) if audio else 0} bytes")
        print(generate_workout_summary("深蹲", 12, 78.5, [
            {"rep": 1, "score": 85}, {"rep": 2, "score": 72},
            {"rep": 3, "score": 90}, {"rep": 4, "score": 65},
        ]))
    asyncio.run(test())
