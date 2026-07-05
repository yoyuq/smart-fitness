"""Knowledge-base search tool."""
from typing import Any, Dict

from ..knowledge_loader import knowledge_ids, search_knowledge
from .registry import register_tool


@register_tool(
    name="search_fitness_kb",
    description="按 query/domains 从健身知识库按需加载相关片段。参数: query 必填或 domains 可选。只允许加载注册过的知识库 id。",
    args={"query": "string", "domains": "list optional"},
)
def search_fitness_kb(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    query = (args.get("query") or "").strip()
    raw_domains = args.get("domains") or []
    domains = [str(d) for d in raw_domains if str(d) in set(knowledge_ids())]
    return search_knowledge(query=query, domains=domains)
