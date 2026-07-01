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
├── knowledge_loader.py  # s07 风格按需知识加载，只允许注册 id
├── web_search.py        # 受控联网搜索，LLM guard 判断是否属于健身知识
├── knowledge/           # markdown 知识库 + index.json 目录
├── todos.py             # s05 风格 Mini TodoWrite
├── history.py           # 对话持久化
├── hooks.py             # UserPromptSubmit / PreToolUse / PostToolUse / Stop
├── permissions.py       # deny / ask / allow，写工具生成待审批请求
├── tools.py             # 白名单工具实现
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

## Context Compact

当前第一版上下文压缩包含：

- `agent_context_summaries`：保存旧聊天摘要。
- `prepare_llm_history_with_summary()`：把旧消息压成一条摘要，只保留最近 10 条对话给 LLM。
- `compact_trace()`：run trace 入库前压缩长字符串、长列表和大字典，避免工具结果撑爆上下文。
- 配置项：`AI_AGENT_CONTEXT_RECENT_TURNS`、`AI_AGENT_CONTEXT_SUMMARIZE_THRESHOLD`、`AI_AGENT_CONTEXT_SUMMARY_CHARS`、`AI_AGENT_COMPACT_TOOL_STRING`。

压缩只减少上下文体积，不改变用户健身数据，也不绕过权限系统。

## 下一阶段

1. Context Compact：旧聊天和旧 run 只保留摘要，工具结果限长。
2. Memory：把 coach memory 分成 goal/injury/preference/observation/run_summary。
3. Error Recovery：JSON 解析失败、工具失败、超时、max turns reached 分策略恢复。
4. Background/Cron：周报、周计划建议、连续未训练提醒，默认只提醒不自动修改数据。

## 验证命令

在 `backend` 目录执行：

```powershell
python -m py_compile fitness_agent\knowledge_loader.py fitness_agent\web_search.py fitness_agent\compact.py fitness_agent\state.py fitness_agent\prompts.py fitness_agent\todos.py fitness_agent\runtime.py fitness_agent\loop.py fitness_agent\tools.py fitness_agent\permissions.py fitness_agent\hooks.py fitness_agent\registry.py fitness_agent\__init__.py main_v2_extra.py
python -m pytest test_fitness_agent_api.py -q
```
