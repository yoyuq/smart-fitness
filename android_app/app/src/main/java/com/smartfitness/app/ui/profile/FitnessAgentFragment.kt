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
import com.smartfitness.app.model.AgentChatMessage
import com.smartfitness.app.model.AgentChatRequest
import com.smartfitness.app.model.AgentNutritionPlanRequest
import com.smartfitness.app.model.AgentToolApproval
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class FitnessAgentFragment : Fragment() {
    private lateinit var chatContainer: LinearLayout
    private lateinit var input: EditText
    private val history = mutableListOf<AgentChatMessage>()

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
        actionRow.addView(UiKit.outlinedButton(ctx, "清空对话") { confirmClearHistory() }.apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = UiKit.dp(ctx, 8)
            }
        })
        root.addView(actionRow)

        val intro = UiKit.card(ctx)
        intro.second.addView(UiKit.cardTitle(ctx, "一个懂你数据的健身助手"))
        intro.second.addView(UiKit.caption(ctx, "包含 AI 训练计划、AI 运动数据分析、AI 专属教练、AI 营养师。会读取你的身体指标、训练数据和训练计划。"))
        root.addView(intro.first)

        val quickRow1 = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        quickRow1.addView(quickButton("营养师规划") { askNutrition() })
        quickRow1.addView(quickButton("分析运动数据") { ask("analysis", "请分析我最近的运动数据，指出最主要的问题和下一步建议。") })
        root.addView(quickRow1)
        val quickRow2 = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }
        quickRow2.addView(quickButton("生成训练计划") { ask("plan", "请根据我的身体数据和训练记录，生成一个适合我的训练计划。") })
        quickRow2.addView(quickButton("专属教练建议") { ask("coach", "请以专属教练身份，结合我的动作表现给我今天的训练建议。") })
        root.addView(quickRow2)

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
        addUserMessage(msg)
        addAgentMessage("营养师正在读取你的身体数据、训练数据和计划…")
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) {
                    ApiClient.service.fitnessAgentNutrition(AgentNutritionPlanRequest("维持训练表现并优化体成分"))
                }
                replaceLastAgentMessage(res.reply ?: res.error ?: "生成失败")
                if (res.runStatus == "waiting_approval" && res.pendingApprovals.isEmpty()) {
                    loadPendingApprovals()
                }
                handleApprovals(res.pendingApprovals)
                history.add(AgentChatMessage("user", msg))
                history.add(AgentChatMessage("assistant", res.reply ?: ""))
            } catch (e: Exception) {
                replaceLastAgentMessage("请求失败: ${e.message}")
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

    private fun loadPendingApprovals() {
        lifecycleScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { ApiClient.service.fitnessAgentApprovals(limit = 10) }
                if (res.ok && res.approvals.isNotEmpty()) {
                    handleApprovals(res.approvals)
                }
            } catch (_: Exception) {
            }
        }
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

    private fun ask(mode: String, msg: String) {
        addUserMessage(msg)
        addAgentMessage("Agent 正在汇总你的数据和相关知识库…")
        lifecycleScope.launch {
            try {
                val req = AgentChatRequest(message = msg, mode = mode, history = history.takeLast(8))
                val res = withContext(Dispatchers.IO) { ApiClient.service.fitnessAgentChat(req) }
                val domains = if (res.domains.isNotEmpty()) "\n\n已调用知识库: ${res.domains.joinToString(" / ")}" else ""
                val reply = (res.reply ?: res.error ?: "生成失败") + domains
                replaceLastAgentMessage(reply)
                if (res.runStatus == "waiting_approval" && res.pendingApprovals.isEmpty()) {
                    loadPendingApprovals()
                }
                handleApprovals(res.pendingApprovals)
                history.add(AgentChatMessage("user", msg))
                history.add(AgentChatMessage("assistant", res.reply ?: ""))
            } catch (e: Exception) {
                replaceLastAgentMessage("请求失败: ${e.message}")
            }
        }
    }

    private fun handleApprovals(approvals: List<AgentToolApproval>) {
        approvals.forEach { approval -> showApprovalDialog(approval) }
    }

    private fun showApprovalDialog(approval: AgentToolApproval) {
        if (approval.approvalId.isBlank()) return
        val details = buildString {
            append(approval.summary.ifBlank { approval.reason })
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

    private fun replaceLastAgentMessage(text: String) {
        val last = chatContainer.getChildAt(chatContainer.childCount - 1) as? LinearLayout ?: return
        val body = last.getChildAt(1) as? TextView ?: return
        body.text = text
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
