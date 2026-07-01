"""Agent 能力注册表。

App 的 `/api/v2/agent/kb` 会从这里读取能力列表。
当前能力目录来自 `knowledge/index.json`，完整知识内容由 `search_fitness_kb` 按需加载。
"""
from typing import Dict, List

from .knowledge_loader import public_catalog


def get_domain_catalog() -> List[Dict[str, str]]:
    """Return a copy of the public Agent domain catalog for API responses."""
    return public_catalog()


AGENT_DOMAINS: List[Dict[str, str]] = get_domain_catalog()
