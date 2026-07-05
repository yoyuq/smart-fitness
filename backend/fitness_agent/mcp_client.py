"""Minimal MCP compatibility layer for Smart Fitness Agent.

Supports two directions:
1. Convert internal tool specs to MCP-like tool schemas for prompts/adapters.
2. Consume external MCP stdio servers and expose their tools to the Agent as
   namespaced tools: mcp__<server>__<tool>.

This module intentionally avoids shell=True. External MCP servers must be
explicitly configured by command + args and are approval-gated by default.
"""
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import AGENT_DIR, getenv

MCP_PROTOCOL_VERSION = os.environ.get("AI_AGENT_MCP_PROTOCOL_VERSION", "2024-11-05")
MCP_TOOL_PREFIX = "mcp__"
_DEFAULT_TIMEOUT = float(os.environ.get("AI_AGENT_MCP_TIMEOUT", "12"))
_LIST_CACHE_TTL = float(os.environ.get("AI_AGENT_MCP_LIST_CACHE_TTL", "60"))
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_LIST_CACHE: Dict[str, Any] = {"expires_at": 0.0, "tools": []}


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    enabled: bool = True
    read_only: bool = False
    read_only_tools: List[str] = field(default_factory=list)
    allow_tools: List[str] = field(default_factory=list)
    deny_tools: List[str] = field(default_factory=list)
    timeout: float = _DEFAULT_TIMEOUT

    def tool_allowed(self, tool_name: str) -> bool:
        if not self.enabled:
            return False
        if self.deny_tools and tool_name in self.deny_tools:
            return False
        if self.allow_tools and tool_name not in self.allow_tools:
            return False
        return True

    def tool_read_only(self, tool_name: str) -> bool:
        return bool(self.read_only or tool_name in self.read_only_tools)


def _slug(value: str) -> str:
    value = _SAFE_NAME_RE.sub("_", str(value or "").strip())
    return value.strip("_") or "server"


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"{MCP_TOOL_PREFIX}{_slug(server_name)}__{_slug(tool_name)}"


def parse_mcp_tool_name(name: str) -> Optional[Tuple[str, str]]:
    if not is_mcp_tool(name):
        return None
    rest = name[len(MCP_TOOL_PREFIX):]
    if "__" not in rest:
        return None
    server, tool = rest.split("__", 1)
    if not server or not tool:
        return None
    return server, tool


def is_mcp_tool(name: str) -> bool:
    return str(name or "").startswith(MCP_TOOL_PREFIX)


def _agent_mcp_config_path() -> Path:
    raw = getenv("AI_AGENT_MCP_CONFIG", "").strip()
    if raw:
        return Path(raw).expanduser()
    return AGENT_DIR / "mcp_servers.json"


def _load_raw_config() -> Any:
    inline = os.environ.get("AI_AGENT_MCP_SERVERS_JSON", "").strip()
    if inline:
        return json.loads(inline)
    path = _agent_mcp_config_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def load_mcp_server_configs() -> List[McpServerConfig]:
    """Load MCP stdio server config.

    Supported formats:
    - [{"name":"demo","command":"python","args":["server.py"]}]
    - {"servers": [{...}]}
    - {"servers": {"demo": {"command":"python", "args": [...]}}}
    """
    raw = _load_raw_config()
    if isinstance(raw, dict) and "servers" in raw:
        raw = raw["servers"]
    items: List[Dict[str, Any]] = []
    if isinstance(raw, dict):
        for name, cfg in raw.items():
            if isinstance(cfg, dict):
                item = dict(cfg)
                item.setdefault("name", name)
                items.append(item)
    elif isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]

    servers: List[McpServerConfig] = []
    seen = set()
    for item in items:
        name = _slug(item.get("name") or item.get("id") or item.get("command") or "server")
        if name in seen:
            raise ValueError(f"duplicate MCP server name: {name}")
        seen.add(name)
        command = str(item.get("command") or "").strip()
        if not command:
            continue
        args = item.get("args") or []
        if isinstance(args, str):
            args = [args]
        env = item.get("env") or {}
        servers.append(McpServerConfig(
            name=name,
            command=command,
            args=[str(a) for a in args],
            env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            cwd=str(item.get("cwd")) if item.get("cwd") else None,
            enabled=bool(item.get("enabled", True)),
            read_only=bool(item.get("read_only", False)),
            read_only_tools=[str(x) for x in item.get("read_only_tools") or []],
            allow_tools=[str(x) for x in item.get("allow_tools") or []],
            deny_tools=[str(x) for x in item.get("deny_tools") or []],
            timeout=float(item.get("timeout") or _DEFAULT_TIMEOUT),
        ))
    return servers


def get_mcp_server_config(server_name: str) -> Optional[McpServerConfig]:
    for cfg in load_mcp_server_configs():
        if cfg.name == server_name and cfg.enabled:
            return cfg
    return None


class McpProtocolError(RuntimeError):
    pass


class McpStdioSession:
    def __init__(self, config: McpServerConfig):
        self.config = config
        env = os.environ.copy()
        env.update(config.env or {})
        self.proc = subprocess.Popen(
            [config.command, *config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=config.cwd or None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
        )
        self._next_id = 1
        self._reader = ThreadPoolExecutor(max_workers=1)

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        finally:
            self._reader.shutdown(wait=False, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _write(self, obj: Dict[str, Any]) -> None:
        if self.proc.stdin is None:
            raise McpProtocolError("MCP server stdin unavailable")
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _readline(self, timeout: float) -> str:
        if self.proc.stdout is None:
            raise McpProtocolError("MCP server stdout unavailable")
        fut = self._reader.submit(self.proc.stdout.readline)
        try:
            line = fut.result(timeout=max(0.1, timeout))
        except FuturesTimeoutError as exc:
            raise TimeoutError(f"MCP server timed out after {timeout}s") from exc
        if line == "":
            stderr = ""
            try:
                if self.proc.stderr is not None:
                    stderr = self.proc.stderr.read()[:500]
            except Exception:
                pass
            raise McpProtocolError(f"MCP server closed stdout. stderr={stderr}")
        return line

    def request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        deadline = time.time() + float(timeout or self.config.timeout or _DEFAULT_TIMEOUT)
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"MCP request {method} timed out")
            line = self._readline(remaining).strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != req_id:
                # Ignore notifications or responses for earlier messages.
                continue
            if msg.get("error"):
                raise McpProtocolError(json.dumps(msg["error"], ensure_ascii=False))
            result = msg.get("result")
            return result if isinstance(result, dict) else {"value": result}

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> Dict[str, Any]:
        result = self.request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "smart-fitness-agent", "version": "0.1"},
        })
        # MCP clients send initialized notification after initialize response.
        self.notify("notifications/initialized", {})
        return result


def _schema_args_summary(schema: Dict[str, Any]) -> Dict[str, str]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return {}
    required = set(schema.get("required") or [])
    out: Dict[str, str] = {}
    for key, value in props.items():
        if not isinstance(value, dict):
            out[str(key)] = "optional"
            continue
        typ = value.get("type") or "any"
        desc = value.get("description") or ""
        suffix = "required" if key in required else "optional"
        out[str(key)] = f"{typ} {suffix}" + (f", {desc}" if desc else "")
    return out


def list_tools_for_server(config: McpServerConfig) -> List[Dict[str, Any]]:
    if not config.enabled:
        return []
    with McpStdioSession(config) as session:
        session.initialize()
        result = session.request("tools/list", {}, timeout=config.timeout)
    tools = result.get("tools") or []
    out = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        original_name = str(tool.get("name") or "").strip()
        if not original_name or not config.tool_allowed(original_name):
            continue
        input_schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object", "properties": {}}
        out.append({
            "name": mcp_tool_name(config.name, original_name),
            "description": f"[MCP:{config.name}] {tool.get('description') or original_name}",
            "args": _schema_args_summary(input_schema),
            "inputSchema": input_schema,
            "read_only": config.tool_read_only(original_name),
            "requires_approval": not config.tool_read_only(original_name),
            "mcp": True,
            "server": config.name,
            "original_name": original_name,
        })
    return out


def clear_mcp_cache() -> None:
    _LIST_CACHE["expires_at"] = 0.0
    _LIST_CACHE["tools"] = []


def list_mcp_tool_specs(force: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    if not force and _LIST_CACHE.get("expires_at", 0) > now:
        return list(_LIST_CACHE.get("tools") or [])
    tools: List[Dict[str, Any]] = []
    for cfg in load_mcp_server_configs():
        if not cfg.enabled:
            continue
        try:
            tools.extend(list_tools_for_server(cfg))
        except Exception:
            # Do not break Agent startup/prompt rendering when an optional MCP
            # server is down. Actual calls will return a structured error.
            continue
    _LIST_CACHE["tools"] = tools
    _LIST_CACHE["expires_at"] = now + _LIST_CACHE_TTL
    return list(tools)


def get_mcp_tool_spec(name: str) -> Optional[Dict[str, Any]]:
    for spec in list_mcp_tool_specs():
        if spec.get("name") == name:
            return spec
    return None


def mcp_permission_decision(tool_name: str, args: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    parsed = parse_mcp_tool_name(tool_name)
    if not parsed:
        return None
    server, original = parsed
    cfg = get_mcp_server_config(server)
    if not cfg or not cfg.tool_allowed(original):
        return {"behavior": "deny", "reason": f"外部 MCP 工具未配置或不允许: {tool_name}"}
    if cfg.tool_read_only(original):
        return {"behavior": "allow", "reason": f"只读 MCP 工具，直接允许: {tool_name}"}
    return {"behavior": "ask", "reason": f"调用外部 MCP 工具: {server}/{original}"}


def call_mcp_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    parsed = parse_mcp_tool_name(tool_name)
    if not parsed:
        return {"ok": False, "error": f"not an MCP tool: {tool_name}"}
    server, original = parsed
    cfg = get_mcp_server_config(server)
    if not cfg or not cfg.tool_allowed(original):
        return {"ok": False, "error": f"MCP tool not configured or denied: {tool_name}", "error_type": "mcp_tool_denied"}
    try:
        with McpStdioSession(cfg) as session:
            session.initialize()
            result = session.request("tools/call", {"name": original, "arguments": arguments or {}}, timeout=cfg.timeout)
        is_error = bool(result.get("isError"))
        return {
            "ok": not is_error,
            "mcp": True,
            "server": server,
            "tool": original,
            "content": result.get("content") or [],
            "structuredContent": result.get("structuredContent"),
            "raw": result,
            "error_type": "mcp_tool_error" if is_error else None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "mcp": True,
            "server": server,
            "tool": original,
            "error": str(exc)[:500],
            "error_type": "mcp_exception",
            "exception_type": type(exc).__name__,
            "recoverable": True,
        }


def internal_spec_to_mcp_tool(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert existing internal spec shape to an MCP-style tool descriptor."""
    args = spec.get("args") if isinstance(spec.get("args"), dict) else {}
    properties: Dict[str, Any] = {}
    for name, desc in args.items():
        text = str(desc)
        lowered = text.lower()
        typ = "string"
        if "int" in lowered or "number" in lowered:
            typ = "number" if "number" in lowered else "integer"
        elif "list" in lowered or "array" in lowered:
            typ = "array"
        elif "bool" in lowered:
            typ = "boolean"
        prop: Dict[str, Any] = {"description": text}
        if typ == "array":
            prop.update({"type": "array", "items": {}})
        else:
            prop["type"] = typ
        properties[str(name)] = prop
    return {
        "name": spec.get("name"),
        "description": spec.get("description") or spec.get("name"),
        "inputSchema": {"type": "object", "properties": properties, "additionalProperties": True},
    }
