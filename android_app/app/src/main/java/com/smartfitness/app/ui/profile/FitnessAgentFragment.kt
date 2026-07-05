package com.smartfitness.app.ui.profile

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.button.MaterialButton
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.app.PlanBuilderDraftHolder
import com.smartfitness.app.model.AgentBackgroundItem
import com.smartfitness.app.model.AgentBackgroundRunRequest
import com.smartfitness.app.model.AgentChatMessage
import com.smartfitness.app.model.AgentChatRequest
import com.smartfitness.app.model.AgentNutritionPlanRequest
import com.smartfitness.app.model.AgentPlanDraft
import com.smartfitness.app.model.AgentPlanExercise
import com.smartfitness.app.model.AgentRun
import com.smartfitness.app.model.AgentLoopInfo
import com.smartfitness.app.model.AgentToolApproval
import com.smartfitness.app.model.CreatePlanRequest
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class FitnessAgentFragment : Fragment() {
    private data class RetryRequest(
        val mode: String,
        val message: String,
        val nutrition: Boolean = false
    )

    private lateinit var chatContainer: LinearLayout
    private lateinit var input: EditText
    private lateinit var agentStatusText: TextView
    private lateinit var approvalsButton: MaterialButton
    private val history = mutableListOf<AgentChatMessage>()
    private var lastRequest: RetryRequest? = null
    private var lastRunId: String? = null

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        val ctx = inflater.context
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 24), UiKit.dp(ctx, 28), UiKit.dp(ctx, 24), UiKit.dp(ctx, 16))
            setBackgroundColor(ctx.getColor(R.color.bg))
        }
        root.addView(UiKit.topBar(ctx, "专属健身 Agent") { findNavController().popBackStack() })

        val actionRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.END
        }
        actionRow.addView(UiKit.outlinedButton(ctx, "运行记录") { showRunsDialog() }.apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                rightMargin = UiKit.dp(ctx, 8)
                bottomMargin = UiKit.dp(ctx, 8)
            }
        })
        approvalsButton = UiKit.outlinedButton(ctx, "刷新审批") { loadPendingApprovals(showEmptyToast = true) }
        actionRow.addView(approvalsButton.apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                rightMargin = UiKit.dp(ctx, 8)
                bottomMargin = UiKit.dp(ctx, 8)
            }
        })
        actionRow.addView(UiKit.outlinedButton(ctx, "Agent健康") { showAgentHealthDialog() }.apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = UiKit.dp(ctx, 8)
            }
        })
        root.addView(actionRow)

        val actionRow2 = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.END
        }
        actionRow2.addView(UiKit.outlinedButton(ctx, "主动提醒") { showBackgroundInbox() }.apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                rightMargin = UiKit.dp(ctx, 8)
                bottomMargin = UiKit.dp(ctx, 8)
            }
        })
        actionRow2.addView(UiKit.outlinedButton(ctx, "清空对话") { confirmClearHistory() }.apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = UiKit.dp(ctx, 8)
            }
        })
        root.addView(actionRow2)

        agentStatusText = UiKit.caption(ctx, "Agent 状态：检查中…")
        agentStatusText.setPadding(0, 0, 0, UiKit.dp(ctx, 8))
        root.addView(agentStatusText)

        val intro = UiKit.card(ctx)
        intro.second.addView(UiKit.cardTitle(ctx, "一个懂你数据的健身助手"))
        intro.second.addView(UiKit.caption(ctx, "包含 AI 训练计划、AI 运动数据分析、AI 专属教练、AI 营养师。会读取你的身体指标、训练数据和训练计划；保存长期记忆、改身体指标、创建/删除计划前都会先弹窗让你确认。"))
        root.addView(intro.first)

        val quickRow1 = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        quickRow1.addView(quickButton("营养师规划") { askNutrition() })
        quickRow1.addView(quickButton("分析运动数据") { ask("analysis", "请分析我最近的运动数据，指出最主要的问题和下一步建议。") })
        root.addView(quickRow1)
        val quickRow2 = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        quickRow2.addView(quickButton("生成训练计划") { ask("plan", "请根据我的身体数据和训练记录，生成一个适合我的训练计划。") })
        quickRow2.addView(quickButton("专属教练建议") { ask("coach", "请以专属教练身份，结合我的动作表现给我今天的训练建议。") })
        root.addView(quickRow2)
        val quickRow3 = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        quickRow3.addView(quickButton("记住偏好") { showMemoryDialog() })
        quickRow3.addView(quickButton("审批写入") { loadPendingApprovals(showEmptyToast = true) })
        root.addView(quickRow3)

        val scroll = ScrollView(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f)
        }
        chatContainer = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, UiKit.dp(ctx, 8), 0, UiKit.dp(ctx, 8))
        }
        scroll.addView(chatContainer)
        root.addView(scroll)
        loadHistory()
        loadAgentHealth()

        val sendRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }
        input = EditText(ctx).apply {
            hint = "输入你的健身问题…"
            minLines = 1
            maxLines = 3
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        sendRow.addView(input)
        sendRow.addView(MaterialButton(ctx).apply {
            text = "发送"
            cornerRadius = UiKit.dp(ctx, 14)
            setOnClickListener {
                val msg = input.text?.toString()?.trim().orEmpty()
                if (msg.isNotEmpty()) {
                    input.setText("")
                    ask("auto", msg)
                }
            }
        })
        root.addView(sendRow)
        return root
    }

    private fun quickButton(text: String, onClick: () -> Unit): MaterialButton =
        MaterialButton(requireContext()).apply {
            this.text = text
            textSize = 13f
            cornerRadius = UiKit.dp(requireContext(), 14)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                leftMargin = UiKit.dp(requireContext(), 3)
                rightMargin = UiKit.dp(requireContext(), 3)
                bottomMargin = UiKit.dp(requireContext(), 6)
            }
            setOnClickListener { onClick() }
        }

    private fun askNutrition() {
        val msg = "帮我规划饮食，先给各类营养目标量，再给具体食堂三餐和加餐建议。"
        val retryRequest = RetryRequest("nutrition", msg, nutrition = true)
        lastRequest = retryRequest
        addUserMessage(msg)
        addAgentMessage("营养师正在读取你的身体数据、训练数据和计划…")
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) {
                    ApiClient.service.fitnessAgentNutrition(AgentNutritionPlanRequest("维持训练表现并优化体成分"))
                }
                val showRetry = shouldOfferRetry(res.ok, res.error, res.agentLoop)
                rememberRunId(res.runId)
                val recovery = formatRecoveryStatus(res.agentLoop)
                val retryHint = if (showRetry) "\n\n提示：本次已降级、达到上限或请求失败，可以点下方按钮重试。" else ""
                replaceLastAgentMessage(
                    (res.reply ?: res.error ?: "生成失败") + recovery + retryHint,
                    retryRequest = retryRequest.takeIf { showRetry },
                    runId = res.runId
                )
                if (res.runStatus == "waiting_approval" && res.pendingApprovals.isEmpty()) {
                    loadPendingApprovals()
                }
                handleApprovals(res.pendingApprovals)
                loadAgentHealth()
                history.add(AgentChatMessage("user", msg))
                history.add(AgentChatMessage("assistant", res.reply ?: ""))
            } catch (e: Exception) {
                replaceLastAgentMessage("请求失败: ${e.message}", retryRequest = retryRequest)
            }
        }
    }

    private fun loadHistory() {
        chatContainer.removeAllViews()
        addAgentMessage("正在恢复之前的 Agent 对话…")
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { ApiClient.service.fitnessAgentHistory(limit = 80) }
                chatContainer.removeAllViews()
                history.clear()
                if (res.ok && res.messages.isNotEmpty()) {
                    res.messages.forEach { item ->
                        if (item.role == "assistant") {
                            addAgentMessage(item.content)
                        } else {
                            addUserMessage(item.content)
                        }
                        history.add(AgentChatMessage(item.role, item.content))
                    }
                } else {
                    addAgentMessage("你可以直接问我：帮我规划饮食 / 分析最近训练 / 生成下周计划 / 深蹲哪里要改。")
                }
                loadPendingApprovals()
            } catch (e: Exception) {
                chatContainer.removeAllViews()
                addAgentMessage("历史对话加载失败: ${e.message}\n\n你可以继续提问，新对话会在网络恢复后由后端保存。")
            }
        }
    }

    private fun loadPendingApprovals(showEmptyToast: Boolean = false) {
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { ApiClient.service.fitnessAgentApprovals(limit = 10) }
                if (res.ok && res.approvals.isNotEmpty()) {
                    approvalsButton.text = "审批写入(${res.approvals.size})"
                    agentStatusText.text = "Agent 状态：有 ${res.approvals.size} 项待审批，请确认后继续"
                    handleApprovals(res.approvals)
                } else {
                    approvalsButton.text = "审批写入"
                    if (showEmptyToast && isAdded) {
                        Toast.makeText(requireContext(), "暂无待审批操作", Toast.LENGTH_SHORT).show()
                    }
                    loadAgentHealth()
                }
            } catch (e: Exception) {
                approvalsButton.text = "审批写入(?)"
                if (showEmptyToast && isAdded) {
                    Toast.makeText(requireContext(), "审批加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }

    private fun loadAgentHealth(showDialog: Boolean = false) {
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { ApiClient.service.fitnessAgentHealth(windowSec = 3600) }
                if (!res.ok) {
                    agentStatusText.text = "Agent 状态：健康信息暂不可用"
                    return@launch
                }
                val cooling = res.providers.count { it.coolingDown }
                val recent = res.recent
                val completed = recent?.byStatus?.get("completed") ?: 0
                val failed = recent?.byStatus?.get("failed") ?: 0
                agentStatusText.text = when {
                    cooling > 0 -> "Agent 状态：${cooling} 个备用模型冷却中；近1小时完成 $completed 次 / 失败 $failed 次"
                    failed > 0 -> "Agent 状态：近1小时完成 $completed 次 / 失败 $failed 次，可点 Agent健康查看"
                    else -> "Agent 状态：正常；近1小时完成 $completed 次"
                }
                if (showDialog) showAgentHealthResult(res)
            } catch (e: Exception) {
                agentStatusText.text = "Agent 状态：健康检查失败"
                if (showDialog && isAdded) Toast.makeText(requireContext(), "健康检查失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun showAgentHealthDialog() = loadAgentHealth(showDialog = true)

    private fun showBackgroundInbox() {
        lifecycleScope.launch {
            try {
                val run = withContext(Dispatchers.IO) {
                    ApiClient.service.runFitnessAgentBackground(AgentBackgroundRunRequest("all"))
                }
                val list = withContext(Dispatchers.IO) {
                    ApiClient.service.fitnessAgentBackgroundItems(status = "pending", limit = 20)
                }
                if (!run.ok && !list.ok) {
                    Toast.makeText(requireContext(), run.error ?: list.error ?: "主动提醒加载失败", Toast.LENGTH_SHORT).show()
                    return@launch
                }
                showBackgroundInboxDialog(list.items, run.created)
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "主动提醒加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun showBackgroundInboxDialog(items: List<AgentBackgroundItem>, created: Int) {
        if (items.isEmpty()) {
            AlertDialog.Builder(requireContext())
                .setTitle("主动提醒")
                .setMessage("后台检查完成，本次新增 ${created} 条；当前暂无待处理提醒。")
                .setPositiveButton("确定", null)
                .show()
            return
        }
        val labels = items.map { item ->
            val time = item.createdAt?.let { formatTime(it) } ?: "未知时间"
            "${item.title}\n${time} · ${item.kind}"
        }.toTypedArray()
        AlertDialog.Builder(requireContext())
            .setTitle("主动提醒（新增 ${created} 条）")
            .setItems(labels) { _, which -> showBackgroundItemDetail(items[which]) }
            .setNegativeButton("关闭", null)
            .show()
    }

    private fun showBackgroundItemDetail(item: AgentBackgroundItem) {
        val text = buildString {
            append(item.message)
            append("\n\n类型：").append(item.kind)
            append("\n状态：").append(item.status)
            append("\n需要审批：").append(if (item.requiresApproval) "是" else "否")
            item.createdAt?.let { append("\n时间：").append(formatTime(it)) }
        }
        AlertDialog.Builder(requireContext())
            .setTitle(item.title)
            .setMessage(text)
            .setNegativeButton("关闭", null)
            .setPositiveButton("标记已读") { _, _ -> markBackgroundItemRead(item) }
            .show()
    }

    private fun markBackgroundItemRead(item: AgentBackgroundItem) {
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { ApiClient.service.markFitnessAgentBackgroundRead(item.itemId) }
                if (res.ok) {
                    Toast.makeText(requireContext(), "已标记已读", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(requireContext(), res.message ?: "操作失败", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "操作失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun showAgentHealthResult(res: com.smartfitness.app.model.AgentHealthResponse) {
        val text = buildString {
            append("近1小时运行：\n")
            val recent = res.recent
            append("- 总运行：").append(recent?.totalRuns ?: 0).append('\n')
            recent?.byStatus?.forEach { (k, v) -> append("- ").append(k).append(": ").append(v).append('\n') }
            append("\nProvider：\n")
            if (res.providers.isEmpty()) append("- 暂无 provider 记录\n")
            res.providers.forEach { p ->
                append("- ").append(p.provider)
                if (p.coolingDown) append(" 冷却中") else append(" 正常")
                append("，成功").append(p.successCount).append(" / 失败").append(p.failureCount)
                if (!p.lastErrorType.isNullOrBlank()) append("，最近错误：").append(p.lastErrorType)
                append('\n')
            }
        }
        AlertDialog.Builder(requireContext())
            .setTitle("Agent 健康状态")
            .setMessage(text)
            .setPositiveButton("确定", null)
            .show()
    }

    private fun confirmClearHistory() {
        AlertDialog.Builder(requireContext())
            .setTitle("清空 Agent 对话？")
            .setMessage("只会清空当前账号的健身 Agent 聊天记录，不影响训练数据和计划。")
            .setNegativeButton("取消", null)
            .setPositiveButton("清空") { _, _ -> clearHistory() }
            .show()
    }

    private fun clearHistory() {
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { ApiClient.service.clearFitnessAgentHistory() }
                if (res.ok) {
                    history.clear()
                    chatContainer.removeAllViews()
                    addAgentMessage("对话已清空。你可以重新问我：饮食规划 / 训练分析 / 下周计划 / 动作建议。")
                    Toast.makeText(requireContext(), "已清空对话", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(requireContext(), res.message ?: "清空失败", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "清空失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun showMemoryDialog() {
        val edit = EditText(requireContext()).apply {
            hint = "例如：我不喜欢空腹跑步 / 膝盖偶尔疼 / 食堂偏好高碳水"
            minLines = 2
            maxLines = 4
        }
        AlertDialog.Builder(requireContext())
            .setTitle("让 Agent 记住什么？")
            .setMessage("提交后不会立刻写入，会先创建一条审批，确认后才保存到长期记忆。")
            .setView(edit)
            .setNegativeButton("取消", null)
            .setPositiveButton("提交") { _, _ ->
                val note = edit.text?.toString()?.trim().orEmpty()
                if (note.isNotEmpty()) ask("coach", "记住：$note")
            }
            .show()
    }

    private fun showRunsDialog() {
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { ApiClient.service.fitnessAgentRuns(limit = 10) }
                if (!res.ok) {
                    Toast.makeText(requireContext(), res.error ?: "运行记录加载失败", Toast.LENGTH_SHORT).show()
                    return@launch
                }
                if (res.runs.isEmpty()) {
                    Toast.makeText(requireContext(), "暂无运行记录", Toast.LENGTH_SHORT).show()
                    return@launch
                }
                val items = res.runs.map { run ->
                    val time = run.createdAt?.let { formatTime(it) } ?: "未知时间"
                    "${time} · ${run.status}\n${run.userMessage.orEmpty().take(36)}"
                }.toTypedArray()
                AlertDialog.Builder(requireContext())
                    .setTitle("Agent 运行记录")
                    .setItems(items) { _, which -> showRunDetail(res.runs[which]) }
                    .setNegativeButton("关闭", null)
                    .show()
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "运行记录加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun showRunDetail(run: AgentRun) {
        lifecycleScope.launch {
            try {
                val detail = withContext(Dispatchers.IO) { ApiClient.service.fitnessAgentRunDetail(run.runId) }
                val r = detail.run ?: run
                val text = buildString {
                    append("状态：").append(r.status).append('\n')
                    append("模式：").append(r.mode ?: "auto").append('\n')
                    append("时间：").append(r.createdAt?.let { formatTime(it) } ?: "未知").append("\n\n")
                    append("用户请求：\n").append(r.userMessage ?: "").append("\n\n")
                    if (r.domains.isNotEmpty()) append("领域：").append(r.domains.joinToString(" / ")).append("\n\n")
                    if (r.todos.isNotEmpty()) {
                        append("TODO：\n")
                        r.todos.forEach { append("- [").append(it.status).append("] ").append(it.content).append('\n') }
                        append('\n')
                    }
                    if (r.pendingApprovalIds.isNotEmpty()) append("待审批：").append(r.pendingApprovalIds.size).append(" 项\n\n")
                    if (r.trace.isNotEmpty()) append("工具调用：").append(r.trace.size).append(" 次\n\n")
                    if (r.error != null) append("错误信息：\n").append(r.error.toString()).append("\n\n")
                    append("最终回复：\n").append(r.finalText ?: "")
                }
                AlertDialog.Builder(requireContext())
                    .setTitle("运行详情")
                    .setMessage(text)
                    .setPositiveButton("确定", null)
                    .show()
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "详情加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun formatTime(ts: Long): String =
        SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date(ts * 1000))

    private fun ask(mode: String, msg: String) {
        val retryRequest = RetryRequest(mode, msg)
        lastRequest = retryRequest
        addUserMessage(msg)
        addAgentMessage("Agent 正在汇总你的数据和相关知识库…")
        lifecycleScope.launch {
            try {
                val req = AgentChatRequest(message = msg, mode = mode, history = history.takeLast(8))
                val res = withContext(Dispatchers.IO) { ApiClient.service.fitnessAgentChat(req) }
                val domains = if (res.domains.isNotEmpty()) "\n\n已调用知识库: ${res.domains.joinToString(" / ")}" else ""
                val status = if (!res.runStatus.isNullOrBlank()) "\n运行状态: ${res.runStatus}" else ""
                val todos = res.agentLoop?.todos?.takeIf { it.isNotEmpty() }?.joinToString("\n", prefix = "\n\n任务进度:\n") { "- [${it.status}] ${it.content}" } ?: ""
                val showRetry = shouldOfferRetry(res.ok, res.error, res.agentLoop)
                rememberRunId(res.runId)
                val recovery = formatRecoveryStatus(res.agentLoop)
                val retryHint = if (showRetry) "\n\n提示：本次已降级、达到上限或请求失败，可以点下方按钮重试。" else ""
                val reply = (res.reply ?: res.error ?: "生成失败") + domains + status + todos + recovery + retryHint
                replaceLastAgentMessage(reply, retryRequest = retryRequest.takeIf { showRetry }, runId = res.runId, planDraft = res.planDraft)
                if (res.runStatus == "waiting_approval" && res.pendingApprovals.isEmpty()) {
                    loadPendingApprovals()
                }
                handleApprovals(res.pendingApprovals)
                loadAgentHealth()
                history.add(AgentChatMessage("user", msg))
                history.add(AgentChatMessage("assistant", res.reply ?: ""))
            } catch (e: Exception) {
                replaceLastAgentMessage("请求失败: ${e.message}", retryRequest = retryRequest)
            }
        }
    }

    private fun formatRecoveryStatus(loop: AgentLoopInfo?): String {
        val events = loop?.recovery.orEmpty()
        if (events.isEmpty() && loop?.fallback != true && loop?.maxTurnsReached != true && loop?.totalTimeoutReached != true) return ""
        val lines = mutableListOf<String>()
        events.forEach { ev ->
            when (ev.event) {
                "provider_error" -> lines.add("${ev.provider ?: "模型"} 调用失败，正在切备用模型")
                "provider_empty" -> lines.add("${ev.provider ?: "模型"} 返回为空，正在切备用模型")
                "provider_recovered" -> lines.add("已切换到备用模型 ${ev.provider ?: ""} 并继续生成")
                "provider_skipped" -> lines.add("已跳过冷却中的模型 ${ev.provider ?: ""}")
                "tool_exception" -> lines.add("工具 ${ev.tool ?: ""} 执行失败，已按保守结果继续")
                "tool_timeout" -> lines.add("工具 ${ev.tool ?: ""} 响应超时，已按保守结果继续")
                "json_repair" -> lines.add(if (ev.ok == true) "已自动修复模型返回格式" else "模型返回格式修复失败，已降级处理")
                "total_timeout" -> lines.add("本次 Agent 已达到总耗时上限")
                "max_turns_reached" -> lines.add("已达到本次工具调用上限")
            }
        }
        if (loop?.fallback == true) lines.add("已启用保守降级回复")
        if (loop?.totalTimeoutReached == true && lines.none { it.contains("总耗时上限") }) lines.add("本次 Agent 已达到总耗时上限")
        if (loop?.maxTurnsReached == true && lines.none { it.contains("工具调用上限") }) lines.add("已达到本次工具调用上限")
        if (lines.isEmpty()) return ""
        return lines.distinct().joinToString("\n", prefix = "\n\n恢复状态:\n") { "- $it" }
    }

    private fun shouldOfferRetry(ok: Boolean, error: String?, loop: AgentLoopInfo?): Boolean =
        !ok || !error.isNullOrBlank() || loop?.fallback == true || loop?.maxTurnsReached == true || loop?.totalTimeoutReached == true

    private fun rememberRunId(runId: String?) {
        if (!runId.isNullOrBlank()) lastRunId = runId
    }

    private fun retryAgentRequest(request: RetryRequest) {
        lastRequest = request
        if (request.nutrition) askNutrition() else ask(request.mode, request.message)
    }

    private fun showLatestRunDetail() {
        val runId = lastRunId
        if (runId.isNullOrBlank()) {
            Toast.makeText(requireContext(), "暂无可查看的运行详情", Toast.LENGTH_SHORT).show()
            return
        }
        showRunDetail(AgentRun(runId = runId))
    }

    private fun handleApprovals(approvals: List<AgentToolApproval>) {
        approvals.forEach { approval -> showApprovalDialog(approval) }
    }

    private fun showApprovalDialog(approval: AgentToolApproval) {
        if (approval.approvalId.isBlank()) return
        val details = buildString {
            append(approval.summary.ifBlank { approval.reason })
            if (!approval.runId.isNullOrBlank()) append("\n\nRun: ").append(approval.runId)
            if (approval.args.isNotEmpty()) {
                append("\n\n参数：")
                approval.args.forEach { (k, v) -> append("\n- ").append(k).append(": ").append(v) }
            }
        }
        AlertDialog.Builder(requireContext())
            .setTitle("允许 Agent 修改数据？")
            .setMessage(details)
            .setNegativeButton("拒绝") { _, _ -> decideApproval(approval, false) }
            .setPositiveButton("允许执行") { _, _ -> decideApproval(approval, true) }
            .show()
    }

    private fun decideApproval(approval: AgentToolApproval, allow: Boolean) {
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) {
                    if (allow) ApiClient.service.approveFitnessAgentTool(approval.approvalId)
                    else ApiClient.service.denyFitnessAgentTool(approval.approvalId)
                }
                if (res.ok) {
                    val reply = res.reply?.takeIf { it.isNotBlank() }
                    val msg = when {
                        reply != null -> reply
                        allow -> "已执行：${approval.summary}"
                        else -> "已拒绝：${approval.summary}"
                    }
                    addAgentMessage(msg)
                    history.add(AgentChatMessage("assistant", msg))
                    Toast.makeText(requireContext(), if (allow) "已执行" else "已拒绝", Toast.LENGTH_SHORT).show()
                    loadPendingApprovals()
                    loadAgentHealth()
                } else {
                    Toast.makeText(requireContext(), res.error ?: res.message ?: "操作失败", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "审批失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun addUserMessage(text: String) = addBubble("我", text, true)
    private fun addAgentMessage(text: String) = addBubble("Agent", text, false)

    private fun replaceLastAgentMessage(
        text: String,
        retryRequest: RetryRequest? = null,
        runId: String? = null,
        planDraft: AgentPlanDraft? = null
    ) {
        val last = chatContainer.getChildAt(chatContainer.childCount - 1) as? LinearLayout ?: return
        val body = last.getChildAt(1) as? TextView ?: return
        body.text = text
        updateBubbleActions(last, retryRequest, runId, planDraft)
    }

    private fun updateBubbleActions(box: LinearLayout, retryRequest: RetryRequest?, runId: String?, planDraft: AgentPlanDraft? = null) {
        while (box.childCount > 2) box.removeViewAt(2)
        val cleanRunId = runId?.takeIf { it.isNotBlank() }
        if (retryRequest == null && cleanRunId == null && planDraft == null) return

        val ctx = box.context
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.END
            setPadding(0, UiKit.dp(ctx, 8), 0, 0)
        }
        if (planDraft != null) {
            row.addView(bubbleActionButton("编辑导入") { openPlanDraftInBuilder(planDraft) })
        }
        if (retryRequest != null) {
            row.addView(bubbleActionButton("重试") { retryAgentRequest(retryRequest) })
        }
        if (cleanRunId != null) {
            row.addView(bubbleActionButton("运行详情") {
                lastRunId = cleanRunId
                showLatestRunDetail()
            })
        }
        box.addView(row)
    }

    private fun bubbleActionButton(text: String, onClick: () -> Unit): MaterialButton {
        val ctx = requireContext()
        return MaterialButton(ctx, null, com.google.android.material.R.attr.materialButtonOutlinedStyle).apply {
            this.text = text
            textSize = 12f
            minHeight = 0
            minimumHeight = 0
            cornerRadius = UiKit.dp(ctx, 12)
            setPadding(UiKit.dp(ctx, 10), UiKit.dp(ctx, 4), UiKit.dp(ctx, 10), UiKit.dp(ctx, 4))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { leftMargin = UiKit.dp(ctx, 8) }
            setOnClickListener { onClick() }
        }
    }

    private fun openPlanDraftInBuilder(draft: AgentPlanDraft) {
        val exercises = draft.exercises.map { item ->
            mapOf(
                "type" to item.type,
                "title" to item.title.ifBlank { item.type },
                "category" to item.category,
                "week" to (item.week ?: 1),
                "day" to (item.day ?: 1),
                "sets" to item.sets,
                "reps" to item.reps,
                "duration_min" to item.durationMin,
                "distance_km" to item.distanceKm,
                "intensity" to item.intensity,
                "note" to item.note
            )
        }
        if (exercises.isEmpty()) {
            Toast.makeText(requireContext(), "计划草稿没有可导入项目", Toast.LENGTH_SHORT).show()
            return
        }
        PlanBuilderDraftHolder.setDraft(
            name = draft.name,
            goal = draft.goal,
            weeks = draft.weeks,
            reason = draft.reason,
            exercises = exercises,
            openFromAgent = true
        )
        try {
            findNavController().navigate(R.id.planBuilderFragment)
        } catch (e: Exception) {
            Toast.makeText(requireContext(), "打开计划编辑页失败: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun showPlanDraftEditor(draft: AgentPlanDraft) {
        val ctx = requireContext()
        val container = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 18), UiKit.dp(ctx, 8), UiKit.dp(ctx, 18), 0)
        }
        val nameInput = EditText(ctx).apply {
            hint = "计划名"
            setText(draft.name)
            setSingleLine(true)
        }
        val exercisesInput = EditText(ctx).apply {
            hint = "每行一个动作：动作,组数,次数,备注"
            minLines = 8
            maxLines = 14
            setText(renderPlanLines(draft.exercises))
        }
        container.addView(nameInput)
        container.addView(exercisesInput)
        AlertDialog.Builder(ctx)
            .setTitle("编辑并导入训练计划")
            .setMessage("可先改计划名和动作。格式：动作,组数,次数,备注")
            .setView(container)
            .setNegativeButton("取消", null)
            .setPositiveButton("导入") { _, _ ->
                val name = nameInput.text?.toString()?.trim().orEmpty().ifBlank { "Agent 生成计划" }
                val exercises = parsePlanLines(exercisesInput.text?.toString().orEmpty())
                if (exercises.isEmpty()) {
                    Toast.makeText(ctx, "至少保留一个动作", Toast.LENGTH_SHORT).show()
                } else {
                    importPlanDraft(name, exercises)
                }
            }
            .show()
    }

    private fun renderPlanLines(items: List<AgentPlanExercise>): String =
        items.joinToString("\n") { item ->
            listOf(item.type, item.sets.toString(), item.reps.toString(), item.note).joinToString(",")
        }

    private fun parsePlanLines(text: String): List<Map<String, Any>> {
        return text.lines().mapNotNull { line ->
            val parts = line.split(",", limit = 4).map { it.trim() }
            val type = parts.getOrNull(0).orEmpty()
            if (type.isBlank()) return@mapNotNull null
            mapOf(
                "type" to type,
                "sets" to (parts.getOrNull(1)?.toIntOrNull() ?: 1).coerceAtLeast(0),
                "reps" to (parts.getOrNull(2)?.toIntOrNull() ?: 0).coerceAtLeast(0),
                "note" to parts.getOrNull(3).orEmpty()
            )
        }
    }

    private fun importPlanDraft(name: String, exercises: List<Map<String, Any>>) {
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { ApiClient.service.createPlan(CreatePlanRequest(name, exercises)) }
                if (res.ok) {
                    val msg = "已导入训练计划：${res.name ?: name}。你可以在训练计划页继续编辑或开始训练。"
                    addAgentMessage(msg)
                    history.add(AgentChatMessage("assistant", msg))
                    Toast.makeText(requireContext(), "已导入训练计划", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(requireContext(), res.message ?: "导入失败", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "导入失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun addBubble(who: String, text: String, mine: Boolean) {
        val ctx = requireContext()
        val box = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 14), UiKit.dp(ctx, 10), UiKit.dp(ctx, 14), UiKit.dp(ctx, 10))
            background = android.graphics.drawable.GradientDrawable().apply {
                cornerRadius = UiKit.dp(ctx, 14).toFloat()
                setColor(if (mine) ctx.getColor(R.color.primary_alpha10) else ctx.getColor(R.color.surface))
                setStroke(UiKit.dp(ctx, 1), ctx.getColor(R.color.divider))
            }
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = UiKit.dp(ctx, 8)
                leftMargin = if (mine) UiKit.dp(ctx, 42) else 0
                rightMargin = if (mine) 0 else UiKit.dp(ctx, 42)
            }
        }
        box.addView(TextView(ctx).apply {
            this.text = who
            textSize = 12f
            setTextColor(ctx.getColor(R.color.on_surface_secondary))
        })
        box.addView(TextView(ctx).apply {
            this.text = text
            textSize = 15f
            setTextColor(ctx.getColor(R.color.on_surface))
            setPadding(0, UiKit.dp(ctx, 4), 0, 0)
        })
        chatContainer.addView(box)
    }
}
