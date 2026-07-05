# Smart Fitness 专属健身 Agent

这个目录是 App 里“健身Agent”的后端实现入口。当前已从简单聊天助手升级为 Fitness Agent Runtime 骨架：单一 Agent Loop 不变，周围补齐 run 状态、按需知识库、权限、hooks、todo 规划和历史持久化。

## 目录结构

```text
fitness_agent/
├── .env                 # Agent 专属环境变量: provider keys、模型名、调用链、超时
├── config.py            # 加载 fitness_agent/.env
├── __init__.py          # 对外导出，保持 import fitness_agent 兼容
├── core.py              # 旧兼容/fallback：意图识别、保底营养回答
├── runtime.py           # 新入口：创建 run、调用 loop、持久化状态
├── state.py             # agent_runs / agent_run_events 状态表
├── loop.py              # Agent Loop：LLM -> tool_calls -> tool_results -> final
├── prompts.py           # system prompt 分段组装，避免大 f-string
├── compact.py           # s08 风格上下文压缩：旧聊天摘要 + trace 压缩
├── memory.py            # 分层长期记忆：goal/preference/injury/diet/training_pattern/observation/run_summary
├── knowledge_loader.py  # s07 风格按需知识加载，只允许注册 id
├── web_search.py        # 受控联网搜索，LLM guard 判断是否属于健身知识
├── knowledge/           # markdown 知识库 + index.json 目录
├── todos.py             # s05 风格 Mini TodoWrite
├── history.py           # 对话持久化
├── hooks.py             # UserPromptSubmit / PreToolUse / PostToolUse / Stop
├── permissions.py       # deny / ask / allow，写工具生成待审批请求
├── tools.py             # 工具兼容导出：TOOL_SPECS / execute_tool
├── toolkit/             # 模块化内部工具 registry + 各类 handler
├── mcp_client.py        # MCP 兼容层：外部 stdio MCP server tools/list + tools/call
├── mcp_server.py        # stdio MCP server：把内部工具按 MCP tools 暴露出去
├── mcp_servers.example.json # 外部 MCP server 配置示例
├── registry.py          # App 能力目录，从 knowledge/index.json 生成
└── README.md
```

## Runtime 流程

`POST /api/v2/agent/chat` 现在调用 `fitness_agent.start_run()`：

1. `state.create_run()` 创建 `run_id`，写入 `agent_runs`。
2. `loop.respond_with_loop()` 执行最多 `AI_AGENT_LOOP_MAX_TURNS` 轮。
3. 工具调用经过 `hooks.py` 和 `permissions.py`。
4. 结果、trace、todos、pending approvals 写回 `agent_runs`。
5. API 返回原有字段，并额外返回：
   - `run_id`
   - `run_status`：`completed` 或 `waiting_approval`

新增 run 查询 API：

- `GET /api/v2/agent/runs?limit=20`
- `GET /api/v2/agent/runs/{run_id}`

## 知识库按需加载

知识库不再继续堆进 system prompt。system prompt 只放 catalog，完整内容由工具按需加载。

```text
knowledge/
├── index.json
├── nutrition.md
├── coach.md
├── plan.md
└── analysis.md
```

添加新知识域时：

1. 新增 `knowledge/<id>.md`
2. 在 `knowledge/index.json` 添加 id/name/description/keywords/path
3. 如需增强意图识别，再更新 `core.INTENT_KEYWORDS`

`registry.py` 和 `/api/v2/agent/kb` 会从 `knowledge/index.json` 自动返回能力目录。

## 工具分类

### 只读观察工具

- `get_user_context_snapshot`
- `get_body_metrics`
- `get_recent_workouts`
- `get_exercise_summary`
- `get_active_plans`
- `get_coach_memory`
- `get_memory_snapshot`
- `search_fitness_kb`
- `search_fitness_web`

`search_fitness_web` 是受控联网搜索，不是通用网络工具：

- 由 LLM guard 判断 query 是否属于健身/营养/训练/恢复/运动健康范围。
- 硬安全层仍会直接拒绝密钥、密码、破解、赌博、成人等明显不该搜索的 query。
- 不接受 URL、HTTP method 或任意网页抓取。
- 默认最多 5 条结果，只返回标题、URL、domain、snippet。
- 可用 `AI_AGENT_WEB_SEARCH_ENABLED=false` 关闭。
- 可用 `AI_AGENT_WEB_SEARCH_LLM_GUARD=false` 关闭 LLM 判断，退回保守关键词 fallback。
- 最终回答使用搜索结果时必须列出来源 URL，并提醒优先参考权威来源。

### 规划工具

- `todo_write`：只更新本次 Agent run 的规划状态，不修改用户健身数据。

### 需用户审批的写工具

- `save_coach_memory`
- `update_body_metrics`
- `create_workout_plan`
- `delete_workout_plan`

写工具会创建 `agent_tool_approvals` 记录，并绑定 `run_id`。当前版本已支持 Approval Resume：用户批准后，后端执行写工具，把 tool_result 追加回 run trace，再让 Agent 生成一条后续总结回复；用户拒绝时也会写回 run 并生成取消说明。

### 永久拒绝

- `bash`
- `raw_sql`
- `read_file`
- `write_file`
- `http_get`
- `fetch_url`
- `open_url`
- `delete_all_user_data`

健身 Agent 不是通用代码 Agent，不能给模型 Bash、文件系统、任意 SQL 或任意 URL 抓取能力。需要外部知识时只能走 `search_fitness_web` 这个受控搜索工具。

## MCP 兼容

当前 Agent 已支持最小 MCP 兼容层：

1. **接入别人做的 MCP stdio server**：配置后，外部工具会出现在 Agent 工具清单里，命名格式为：

```text
mcp__<server>__<tool>
```

2. **把内部 Smart Fitness 工具暴露为 MCP server**：可用 `python -m fitness_agent.mcp_server` 通过 stdio 提供标准 `tools/list` / `tools/call`。

### 接入外部 MCP 工具

复制示例配置：

```text
fitness_agent/mcp_servers.example.json -> fitness_agent/mcp_servers.json
```

示例：

```json
{
  "servers": {
    "example": {
      "enabled": true,
      "command": "python",
      "args": ["path/to/mcp_server.py"],
      "cwd": "path/to/server/workdir",
      "env": {},
      "timeout": 12,
      "read_only": false,
      "read_only_tools": ["safe_search", "get_status"],
      "allow_tools": [],
      "deny_tools": []
    }
  }
}
```

也可以用环境变量指定配置：

```env
AI_AGENT_MCP_CONFIG=C:\\path\\to\\mcp_servers.json
AI_AGENT_MCP_TIMEOUT=12
AI_AGENT_MCP_LIST_CACHE_TTL=60
```

安全策略：

- 外部 MCP 工具默认 **需要 App 审批**。
- 配置 `read_only=true` 或 `read_only_tools` 的工具才会直接执行。
- 可用 `allow_tools` / `deny_tools` 控制某个 server 暴露哪些工具。
- Agent 不使用 shell 拼接命令启动 MCP server，只按 `command + args` 用 `shell=False` 启动。

### 暴露内部工具为 MCP server

```powershell
$env:SMART_FITNESS_MCP_DB="C:\Users\hjl\Projects\smart_fitness\backend\fitness.db"
$env:SMART_FITNESS_MCP_USER_ID="31"
python -m fitness_agent.mcp_server
```

支持方法：

- `initialize`
- `tools/list`
- `tools/call`

注意：这个 server 是本地用户作用域工具 server。给外部客户端使用时，应只在可信本机环境运行。

## 当前 API

- `POST /api/v2/agent/chat`
- `POST /api/v2/agent/nutrition_plan`
- `GET /api/v2/agent/history?limit=50`
- `DELETE /api/v2/agent/history`
- `GET /api/v2/agent/kb`
- `GET /api/v2/agent/runs?limit=20`
- `GET /api/v2/agent/runs/{run_id}`
- `GET /api/v2/agent/approvals`
- `POST /api/v2/agent/approvals/{approval_id}/approve`
- `POST /api/v2/agent/approvals/{approval_id}/deny`

## Approval Resume

审批通过流程：

```text
POST /api/v2/agent/approvals/{approval_id}/approve
→ execute_tool()
→ mark_approval(executed/failed)
→ resume_run_after_approval()
→ tool_result 追加到 agent_runs.trace_json
→ run_status 更新为 completed / failed / waiting_approval
→ 生成新的 assistant reply 并保存到聊天历史
```

拒绝流程：

```text
POST /api/v2/agent/approvals/{approval_id}/deny
→ mark_approval(denied)
→ resume_run_after_denial()
→ run trace 记录 denied
→ 生成“已取消，不会修改数据”的 assistant reply
```

当前 resume 是保守版：批准后只让 Agent 总结已执行结果，不允许在 resume 中继续发起新的写操作。需要进一步修改数据时，必须由用户再发起新请求或再次确认。

## 受控联网搜索

Agent 可以调用：

```text
search_fitness_web(query, limit=5)
```

用途是补充本地知识库没有的新健身/营养/训练知识。它不是自由网络访问：模型不能传 URL，不能读取网页正文，不能访问任意 API，只能得到搜索结果摘要。是否属于当前 Agent 所需知识由 LLM guard 语义判断；关键词只作为 LLM 不可用时的 fallback，另有硬安全词直接拒绝。

配置项在 `fitness_agent/.env`：

```env
AI_AGENT_WEB_SEARCH_ENABLED=true
AI_AGENT_WEB_SEARCH_LLM_GUARD=true
AI_AGENT_WEB_SEARCH_GUARD_CHAIN=deepseek,qwen,volc-coding,hunyuan
AI_AGENT_WEB_SEARCH_MAX_RESULTS=5
AI_AGENT_WEB_SEARCH_TIMEOUT=8
```

## Memory 分层

`memory.py` 继续兼容原 `coach_memory` 表，同时补充这些列：

- `kind`：`goal` / `preference` / `injury` / `diet` / `training_pattern` / `observation` / `run_summary` / `general`
- `source`：记忆来源，例如 `approved_tool`、`run_summary`
- `confidence`：0~1 置信度
- `active`：软删除/停用标记，默认 1
- `run_id`：关联 Agent run
- `metadata_json`：扩展元数据

读取：

- `get_coach_memory(limit, kinds)`：按 kind 过滤读取。
- `get_memory_snapshot(limit_per_kind)`：按 kind 分组读取摘要。

写入：

- 用户事实、目标、伤病、偏好、饮食、训练模式等长期记忆仍通过 `save_coach_memory`，必须走 App 审批。
- `run_summary` 是低风险运行摘要，只记录“用户请求 / 工具 / 最终回复”，用于下次理解上下文，不代表新的用户事实。
- 旧表已有 `category` 会迁移/映射到 `kind`，不破坏旧接口。

## Context Compact

当前第一版上下文压缩包含：

- `agent_context_summaries`：保存旧聊天摘要。
- `prepare_llm_history_with_summary()`：把旧消息压成一条摘要，只保留最近 10 条对话给 LLM。
- `compact_trace()`：run trace 入库前压缩长字符串、长列表和大字典，避免工具结果撑爆上下文。
- 配置项：`AI_AGENT_CONTEXT_RECENT_TURNS`、`AI_AGENT_CONTEXT_SUMMARIZE_THRESHOLD`、`AI_AGENT_CONTEXT_SUMMARY_CHARS`、`AI_AGENT_COMPACT_TOOL_STRING`。

压缩只减少上下文体积，不改变用户健身数据，也不绕过权限系统。

## 下一阶段

1. Error Recovery：JSON 解析失败、工具失败、超时、max turns reached 分策略恢复。✅
2. Tool Registry：把工具 schema/权限级别/分类从 `tools.py` 拆出去。✅
3. MCP 兼容：外部 MCP stdio server 接入 + 内部工具 MCP server 暴露。✅
4. App UI：展示 run 状态、todo、工具调用进度、联网搜索来源。✅
5. Background/Cron：周报、周计划建议、连续未训练提醒，默认只提醒不自动修改数据。✅

## Background / Cron

当前实现是保守版“主动教练 inbox”：

- `fitness_agent.background.run_background_checks(conn, user_id, job)` 是 durable 业务入口。
- `fitness_agent.background_scheduler` 在 FastAPI 启动后每隔一段时间 best-effort 扫描用户并运行后台检查。
- 后台任务只写入 `agent_background_items`，不会直接修改身体数据、长期记忆或正式训练计划。
- 下周计划只是草案 payload；用户要导入正式计划时，仍应走现有 Agent 写工具审批。

支持 job：

- `daily_checkin`：每天生成一次训练提醒；今天没练则提醒恢复连续性，已练则给恢复建议。
- `weekly_review`：每周生成一次 7 天训练周报 + 下周训练计划草案。
- `all`：同时运行以上两个。

API：

- `GET /api/v2/agent/background/items?status=pending&limit=20`
- `POST /api/v2/agent/background/run`，body: `{ "job": "daily_checkin|weekly_review|all" }`
- `POST /api/v2/agent/background/items/{item_id}/read`
- `POST /api/v2/agent/background/items/{item_id}/dismiss`

配置：

```env
AI_AGENT_BACKGROUND_ENABLED=true
AI_AGENT_BACKGROUND_INTERVAL_SEC=1800
```

验证：

```powershell
python -m pytest test_fitness_agent_background.py -q
```

## 验证命令

在 `backend` 目录执行：

```powershell
python -m py_compile fitness_agent\knowledge_loader.py fitness_agent\web_search.py fitness_agent\compact.py fitness_agent\memory.py fitness_agent\state.py fitness_agent\prompts.py fitness_agent\todos.py fitness_agent\runtime.py fitness_agent\loop.py fitness_agent\tools.py fitness_agent\permissions.py fitness_agent\hooks.py fitness_agent\registry.py fitness_agent\__init__.py main_v2_extra.py
python -m pytest test_fitness_agent_api.py -q
```
