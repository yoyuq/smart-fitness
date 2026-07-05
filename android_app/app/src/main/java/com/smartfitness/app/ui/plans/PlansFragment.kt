package com.smartfitness.app.ui.plans

import android.app.AlertDialog
import android.app.ProgressDialog
import android.os.Bundle
import android.text.InputType
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.app.PlanDetailHolder
import com.smartfitness.app.app.PlanIntent
import com.smartfitness.app.model.CreatePlanRequest
import com.smartfitness.app.model.PlanAiDraftRequest
import com.smartfitness.app.model.WorkoutPlan
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/** 我的计划页：开放式日期计划 + AI 自由草稿 + 导入前编辑。 */
class PlansFragment : Fragment() {

    private lateinit var plansContainer: LinearLayout
    private lateinit var swipe: SwipeRefreshLayout

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        val ctx = inflater.context
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16))
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }

        root.addView(TextView(ctx).apply {
            text = "我的计划"
            textSize = 22f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
            setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 8), 0, UiKit.dp(ctx, 12))
        })

        root.addView(buildPlanBuilderCard())

        root.addView(TextView(ctx).apply {
            text = "已导入计划"
            textSize = 16f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
            setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 14), 0, UiKit.dp(ctx, 8))
        })
        plansContainer = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        root.addView(plansContainer)

        val scroll = ScrollView(ctx).apply { setBackgroundColor(ctx.getColor(R.color.bg)); addView(root) }
        swipe = SwipeRefreshLayout(ctx).apply {
            setColorSchemeColors(ctx.getColor(R.color.primary))
            setOnRefreshListener { loadPlans() }
            addView(scroll)
        }
        return swipe
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) { loadPlans() }

    private fun buildPlanBuilderCard(): View {
        val ctx = requireContext()
        val (card, inner) = UiKit.card(ctx)
        inner.addView(UiKit.cardTitle(ctx, "制定训练计划"))
        inner.addView(UiKit.caption(ctx, "选择训练周期、运动分类和详细训练项，由 AI 生成可编辑草稿。支持跑步、游泳、力量、拉伸、恢复和自定义项目。"))
        inner.addView(MaterialButton(ctx).apply {
            text = "开始制定"
            textSize = 16f
            cornerRadius = UiKit.dp(ctx, 24)
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 50)).apply { topMargin = UiKit.dp(ctx, 12) }
            setOnClickListener {
                try { findNavController().navigate(R.id.planBuilderFragment) }
                catch (e: Exception) { Toast.makeText(ctx, "跳转失败: ${e.message}", Toast.LENGTH_SHORT).show() }
            }
        })
        return card
    }

    private fun buildCustomPlanCard(): View {
        val ctx = requireContext()
        val (card, inner) = UiKit.card(ctx)
        inner.addView(UiKit.cardTitle(ctx, "自定义训练计划"))
        inner.addView(UiKit.caption(ctx, "按日期安排跑步、游泳、力量、拉伸或恢复。保存前可自由增删改。"))
        inner.addView(UiKit.outlinedButton(ctx, "新建自定义计划") {
            showPlanEditor("自定义训练计划", defaultItems(), null)
        }.apply {
            cornerRadius = UiKit.dp(ctx, 24)
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 48)).apply { topMargin = UiKit.dp(ctx, 10) }
        })
        return card
    }

    private fun buildAiPlanCard(): View {
        val ctx = requireContext()
        val (card, inner) = UiKit.card(ctx)
        inner.addView(UiKit.cardTitle(ctx, "AI 规划训练计划"))
        inner.addView(UiKit.caption(ctx, "像问健身 Agent 一样自由输入：目标、时间、跑步/游泳/力量偏好、限制条件都可以写。AI 会结合身体数据、训练记录和知识库生成草稿。"))
        inner.addView(MaterialButton(ctx).apply {
            text = "打开 AI 计划输入窗口"
            textSize = 16f
            cornerRadius = UiKit.dp(ctx, 24)
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 50)).apply { topMargin = UiKit.dp(ctx, 10) }
            setOnClickListener { showAiPromptDialog() }
        })
        return card
    }

    private fun loadPlans() {
        swipe.isRefreshing = true
        lifecycleScope.launch {
            try {
                val list = ApiClient.service.listPlans().plans
                if (!isAdded) return@launch
                plansContainer.removeAllViews()
                val ctx = requireContext()
                if (list.isEmpty()) {
                    plansContainer.addView(UiKit.caption(ctx, "还没有导入计划。可以先制定一个训练计划，或让 AI 生成草稿后再导入。"))
                } else {
                    list.forEach { plansContainer.addView(buildPlanCard(it)) }
                }
            } catch (e: Exception) {
                if (isAdded) Toast.makeText(requireContext(), "加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            } finally { swipe.isRefreshing = false }
        }
    }

    private fun buildPlanCard(plan: WorkoutPlan): View {
        val ctx = requireContext()
        val items = parseItems(plan.exercises)
        val (card, inner) = UiKit.card(ctx)
        val row = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
        val textCol = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            isClickable = true
            setOnClickListener { openPlanDetail(plan) }
        }
        textCol.addView(TextView(ctx).apply {
            text = plan.name
            textSize = 16f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
        })
        val trackableExists = items.any { isTrackable(it["type"].toString()) }
        textCol.addView(UiKit.caption(ctx, "${items.size} 个项目${if (trackableExists) " · 可识别" else " · 手动打卡"} · ${summarizeDays(items)} › 查看详情"))
        row.addView(textCol)
        row.addView(MaterialButton(ctx).apply {
            text = "详情"
            textSize = 13f
            cornerRadius = UiKit.dp(ctx, 18)
            minWidth = 0; minimumWidth = 0
            setPadding(UiKit.dp(ctx, 18), 0, UiKit.dp(ctx, 18), 0)
            setOnClickListener { openPlanDetail(plan) }
        })
        inner.addView(row)
        return card
    }

    private fun openPlanDetail(plan: WorkoutPlan) {
        PlanDetailHolder.plan = plan
        try { findNavController().navigate(R.id.planDetailFragment) }
        catch (e: Exception) { Toast.makeText(requireContext(), "跳转失败: ${e.message}", Toast.LENGTH_SHORT).show() }
    }

    private fun startTrainingWithPlan(plan: WorkoutPlan, items: List<Map<String, Any>>) {
        val firstTrackable = items.firstOrNull { isTrackable(it["type"].toString()) }
        if (firstTrackable == null) {
            openPlanDetail(plan)
            return
        }
        PlanIntent.set(plan.planId, plan.name, firstTrackable["type"]?.toString(), (firstTrackable["reps"] as? Number)?.toInt()?.takeIf { it > 0 })
        try { findNavController().navigate(R.id.trainingFragment) }
        catch (e: Exception) { Toast.makeText(requireContext(), "跳转失败: ${e.message}", Toast.LENGTH_SHORT).show() }
    }

    private fun showAiPromptDialog() {
        val ctx = requireContext()
        val box = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; setPadding(UiKit.dp(ctx, 18), UiKit.dp(ctx, 8), UiKit.dp(ctx, 18), 0) }
        val promptInput = EditText(ctx).apply {
            hint = "例如：我是学生，每天跑5km，想增肌并提高引体向上，一周4练，食堂饮食，帮我安排跑步+力量+恢复。"
            minLines = 6
            maxLines = 10
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
        }
        val weeksInput = EditText(ctx).apply { hint = "周数 1-8"; inputType = InputType.TYPE_CLASS_NUMBER; setText("2") }
        box.addView(promptInput); box.addView(weeksInput)
        MaterialAlertDialogBuilder(ctx)
            .setTitle("AI 规划训练计划")
            .setMessage("输入越自由越好：目标、限制、可用时间、想加入跑步/游泳/力量都可以写。")
            .setView(box)
            .setNegativeButton("取消", null)
            .setPositiveButton("生成草稿") { _, _ ->
                val prompt = promptInput.text?.toString()?.trim().orEmpty()
                val weeks = (weeksInput.text?.toString()?.toIntOrNull() ?: 2).coerceIn(1, 8)
                if (prompt.isBlank()) Toast.makeText(ctx, "先写训练计划方向", Toast.LENGTH_SHORT).show()
                else requestAiDraft(prompt, weeks)
            }.show()
    }

    private fun requestAiDraft(prompt: String, weeks: Int) {
        val ctx = requireContext()
        val progress = ProgressDialog(ctx).apply { setMessage("AI 教练正在读取你的数据并生成草稿…"); setCancelable(false); show() }
        lifecycleScope.launch {
            try {
                val resp = ApiClient.service.draftPlanWithAi(PlanAiDraftRequest(prompt, weeks))
                progress.dismiss()
                if (resp.ok && resp.exercises.isNotEmpty()) {
                    val items = resp.exercises.map { item -> itemToMap(item.type, item.title, item.category, item.week ?: 1, item.day ?: 1, item.durationMin, item.distanceKm, item.sets, item.reps, item.intensity, item.note) }
                    showPlanEditor(resp.name ?: "AI 训练计划", items, resp.reason)
                } else Toast.makeText(ctx, resp.message ?: "生成失败", Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                progress.dismiss()
                Toast.makeText(ctx, "生成失败: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showPlanEditor(initialName: String, initialItems: List<Map<String, Any>>, reason: String?) {
        val ctx = requireContext()
        val box = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; setPadding(UiKit.dp(ctx, 16), UiKit.dp(ctx, 8), UiKit.dp(ctx, 16), 0) }
        val nameInput = EditText(ctx).apply { hint = "计划名"; setText(initialName); setSingleLine(true) }
        val itemsInput = EditText(ctx).apply {
            hint = "格式：天,项目名,类别,type,分钟,公里,组数,次数,强度,备注\n例：1,5km轻松跑,cardio,running,35,5,0,0,中等,跑后拉伸"
            minLines = 10; maxLines = 16
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setText(renderLines(initialItems))
        }
        box.addView(nameInput)
        if (!reason.isNullOrBlank()) box.addView(UiKit.caption(ctx, "生成理由：$reason"))
        box.addView(itemsInput)
        AlertDialog.Builder(ctx)
            .setTitle(if (reason == null) "编辑自定义计划" else "编辑 AI 计划草稿")
            .setMessage("每行一项；删除一行=删除项目，新增一行=新增项目。导入前可随便改。")
            .setView(box)
            .setNegativeButton("取消", null)
            .setPositiveButton("导入到我的计划") { _, _ ->
                val name = nameInput.text?.toString()?.trim().orEmpty().ifBlank { "我的训练计划" }
                val items = parseLines(itemsInput.text?.toString().orEmpty())
                if (items.isEmpty()) Toast.makeText(ctx, "至少保留一个计划项目", Toast.LENGTH_SHORT).show()
                else importPlan(name, items)
            }.show()
    }

    private fun importPlan(name: String, items: List<Map<String, Any>>) {
        lifecycleScope.launch {
            try {
                val res = ApiClient.service.createPlan(CreatePlanRequest(name, items))
                if (res.ok) {
                    Toast.makeText(requireContext(), "已导入：${res.name ?: name}", Toast.LENGTH_SHORT).show()
                    loadPlans()
                } else Toast.makeText(requireContext(), res.message ?: "导入失败", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) { Toast.makeText(requireContext(), "导入失败: ${e.message}", Toast.LENGTH_SHORT).show() }
        }
    }

    private fun defaultItems(): List<Map<String, Any>> = listOf(
        itemToMap("running", "轻松跑", "cardio", 1, 1, 30, 5.0, 0, 0, "中等", "保持能说完整句子的配速"),
        itemToMap("stretch", "跑后拉伸", "mobility", 1, 1, 15, 0.0, 0, 0, "恢复", "小腿、髋、腘绳肌")
    )

    private fun itemToMap(type: String, title: String, category: String, week: Int, day: Int, duration: Int, distance: Double, sets: Int, reps: Int, intensity: String, note: String): Map<String, Any> =
        mapOf("type" to type, "title" to title.ifBlank { type }, "category" to category.ifBlank { inferCategory(type) }, "week" to week, "day" to day, "duration_min" to duration, "distance_km" to distance, "sets" to sets, "reps" to reps, "intensity" to intensity, "note" to note)

    private fun renderLines(items: List<Map<String, Any>>): String = items.joinToString("\n") {
        listOf(it["day"] ?: 1, it["title"] ?: it["type"] ?: "", it["category"] ?: inferCategory(it["type"].toString()), it["type"] ?: "custom", it["duration_min"] ?: 0, it["distance_km"] ?: 0, it["sets"] ?: 0, it["reps"] ?: 0, it["intensity"] ?: "", it["note"] ?: "").joinToString(",")
    }

    private fun parseLines(text: String): List<Map<String, Any>> = text.lines().mapNotNull { line ->
        val p = line.split(",", limit = 10).map { it.trim() }
        val title = p.getOrNull(1).orEmpty()
        val type = p.getOrNull(3).orEmpty().ifBlank { title.ifBlank { "custom" } }
        if (title.isBlank() && type.isBlank()) return@mapNotNull null
        itemToMap(type, title.ifBlank { type }, p.getOrNull(2).orEmpty().ifBlank { inferCategory(type) }, 1, p.getOrNull(0)?.toIntOrNull() ?: 1, p.getOrNull(4)?.toIntOrNull() ?: 0, p.getOrNull(5)?.toDoubleOrNull() ?: 0.0, p.getOrNull(6)?.toIntOrNull() ?: 0, p.getOrNull(7)?.toIntOrNull() ?: 0, p.getOrNull(8).orEmpty(), p.getOrNull(9).orEmpty())
    }

    private fun parseItems(raw: String?): List<Map<String, Any>> {
        val out = mutableListOf<Map<String, Any>>()
        try {
            val arr = JSONArray(raw ?: "[]")
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                out += itemToMap(o.optString("type", "custom"), o.optString("title", o.optString("type", "自定义项目")), o.optString("category", inferCategory(o.optString("type"))), o.optInt("week", 1), o.optInt("day", 1), o.optInt("duration_min", 0), o.optDouble("distance_km", 0.0), o.optInt("sets", 0), o.optInt("reps", 0), o.optString("intensity", ""), o.optString("note", ""))
            }
        } catch (_: Exception) {}
        return out
    }

    private fun summarizeDays(items: List<Map<String, Any>>): String {
        val days = items.mapNotNull { (it["day"] as? Number)?.toInt() }.distinct().sorted()
        return if (days.isEmpty()) "未分日期" else "第 ${days.joinToString("/")} 天"
    }

    private fun inferCategory(type: String): String {
        val t = type.lowercase()
        return when {
            listOf("run", "swim", "bike", "cardio", "跑", "游泳").any { t.contains(it) } -> "cardio"
            listOf("stretch", "mobility", "yoga", "拉伸").any { t.contains(it) } -> "mobility"
            listOf("rest", "recovery", "休息", "恢复").any { t.contains(it) } -> "recovery"
            else -> "strength"
        }
    }

    private fun isTrackable(type: String): Boolean = setOf("squat", "push_up", "pushup", "lunge", "plank", "jumping_jack", "bicep_curl", "shoulder_press").contains(type.lowercase())
}
