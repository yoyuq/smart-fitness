"""Restricted network tools."""
from typing import Any, Dict

from ..web_search import search_fitness_web as _search_fitness_web
from .base import clamp_int
from .registry import register_tool


@register_tool(
    name="search_fitness_web",
    description=(
        "受控联网搜索健身/营养/训练相关公开信息。只接受搜索 query，不接受 URL。"
        "academic=true 时会限定到 PubMed / ACSM / NSCA / DOI 白名单、结果更权威但等待时间更长。"
        " 最终回答必须引用来源 URL。"
    ),
    args={
        "query": "fitness/nutrition/exercise related string",
        "limit": "int optional, 1-5",
        "academic": "bool optional, default false; true = restrict to PubMed/DOI/NSCA/ACSM",
    },
    network="restricted_search_only",
)
def search_fitness_web(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    query = (args.get("query") or "").strip()
    limit = clamp_int(args.get("limit"), 5, 1, 5)
    academic_raw = args.get("academic")
    academic = False
    if isinstance(academic_raw, bool):
        academic = academic_raw
    elif isinstance(academic_raw, str):
        academic = academic_raw.strip().lower() in {"1", "true", "yes", "on", "academic"}
    return _search_fitness_web(query=query, limit=limit, academic=academic)
