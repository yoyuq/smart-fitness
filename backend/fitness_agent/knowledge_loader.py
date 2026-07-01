"""On-demand knowledge loading for the Smart Fitness Agent.

This is the fitness-domain version of s07 skill loading:
- a small catalog is safe to place in the system prompt
- full knowledge markdown is loaded only through a whitelisted tool
- callers pass registered ids, never arbitrary file paths
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent
KNOWLEDGE_DIR = ROOT / "knowledge"
INDEX_PATH = KNOWLEDGE_DIR / "index.json"
MAX_KNOWLEDGE_CHARS = 8000


@lru_cache(maxsize=1)
def load_catalog() -> List[Dict[str, Any]]:
    if not INDEX_PATH.exists():
        return []
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    out: List[Dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        kid = str(item.get("id") or "").strip()
        path = str(item.get("path") or "").strip()
        if not kid or not path or ".." in path or path.startswith(('/', '\\')):
            continue
        out.append({
            "id": kid,
            "name": str(item.get("name") or kid),
            "description": str(item.get("description") or ""),
            "keywords": [str(k) for k in (item.get("keywords") or [])],
            "path": path,
        })
    return out


def catalog_for_prompt() -> str:
    lines = []
    for item in load_catalog():
        lines.append(f"- {item['id']} / {item['name']}: {item.get('description', '')}")
    return "\n".join(lines)


def public_catalog() -> List[Dict[str, str]]:
    return [
        {"id": item["id"], "name": item["name"], "desc": item.get("description", "")}
        for item in load_catalog()
    ]


def knowledge_ids() -> List[str]:
    return [item["id"] for item in load_catalog()]


def detect_knowledge_ids(message: str, default: Optional[List[str]] = None) -> List[str]:
    msg = (message or "").lower()
    found: List[str] = []
    for item in load_catalog():
        kws = [str(k).lower() for k in item.get("keywords") or []]
        if any(k and k in msg for k in kws):
            found.append(item["id"])
    if not found:
        found = list(default or ["coach", "analysis"])
    if "nutrition" in found:
        for extra in ("analysis", "plan"):
            if extra in knowledge_ids() and extra not in found:
                found.append(extra)
    return found[:4]


def _safe_path_for_id(kid: str) -> Optional[Path]:
    item = next((x for x in load_catalog() if x["id"] == kid), None)
    if not item:
        return None
    path = (KNOWLEDGE_DIR / item["path"]).resolve()
    try:
        path.relative_to(KNOWLEDGE_DIR.resolve())
    except ValueError:
        return None
    return path


def load_knowledge(ids: Iterable[str], max_chars: int = MAX_KNOWLEDGE_CHARS) -> List[Dict[str, str]]:
    seen = set()
    snippets: List[Dict[str, str]] = []
    remaining = max(500, int(max_chars or MAX_KNOWLEDGE_CHARS))
    for raw in ids:
        kid = str(raw or "").strip()
        if not kid or kid in seen:
            continue
        seen.add(kid)
        path = _safe_path_for_id(kid)
        if not path or not path.exists():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if remaining <= 0:
            break
        piece = content[:remaining]
        remaining -= len(piece)
        snippets.append({"domain": kid, "content": piece})
    return snippets


def search_knowledge(query: str = "", domains: Optional[List[str]] = None, max_chars: int = MAX_KNOWLEDGE_CHARS) -> Dict[str, Any]:
    valid = set(knowledge_ids())
    selected = [str(d) for d in (domains or []) if str(d) in valid]
    if not selected:
        selected = detect_knowledge_ids(query)
    return {
        "ok": True,
        "query": query or "",
        "domains": selected,
        "snippets": load_knowledge(selected, max_chars=max_chars),
    }
