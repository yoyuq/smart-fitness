package com.smartfitness.app.ui.plans

import android.app.AlertDialog
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
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
import com.google.android.material.button.MaterialButton
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.app.PlanDetailHolder
import com.smartfitness.app.app.PlanIntent
import com.smartfitness.app.model.PlanCheckinRequest
import com.smartfitness.app.model.UpdatePlanRequest
import com.smartfitness.app.model.WorkoutPlan
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.launch
import org.json.JSONArray

/** 开放式计划详情：日期计划、跑步/游泳/拉伸打卡、可识别动作继续开始训练。 */
class PlanDetailFragment : Fragment() {

    private val labels = mapOf(
        "squat" to "深蹲", "push_up" to "俯卧撑", "pushup" to "俯卧撑",
        "plank" to "平板支撑", "lunge" to "弓步蹲", "jumping_jack" to "开合跳",
        "bicep_curl" to "二头弯举", "shoulder_press" to "肩上推举",
        "running" to "跑步", "swimming" to "游泳", "stretch" to "拉伸", "mobility" to "灵活性", "rest" to "休息"
    )
    private val trackable = setOf("squat", "push_up", "pushup", "lunge", "plank", "jumping_jack", "bicep_curl", "shoulder_press")

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        val ctx = inflater.context
        val plan = PlanDetailHolder.plan
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 24))
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        root.addView(UiKit.topBar(ctx, "计划详情") { findNavController().popBackStack() })

        if (plan == null) {
            root.addView(UiKit.caption(ctx, "计划信息丢失, 请返回重试"))
            return ScrollView(ctx).apply { setBackgroundColor(ctx.getColor(R.color.bg)); addView(root) }
        }

        val items = parseExercises(plan.exercises)
        with(root) {
            UiKit.card(ctx).let { (card, inner) ->
                inner.addView(TextView(ctx).apply {
                    text = plan.name
                    textSize = 22f
                    setTypeface(typeface, Typeface.BOLD)
                    setTextColor(ctx.getColor(R.color.on_surface))
                })
                inner.addView(UiKit.caption(ctx, "${items.size} 个项目 · ${estimateMinutes(items)} 分钟 · ${summarizeDays(items)}"))
                addView(card)
            }

            UiKit.card(ctx).let { (card, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "日期计划"))
                if (items.isEmpty()) inner.addView(UiKit.caption(ctx, "这是一个空白计划。"))
                else items.groupBy { it.day }.toSortedMap().forEach { (day, dayItems) ->
                    inner.addView(TextView(ctx).apply {
                        text = "第 $day 天"
                        textSize = 15f
                        setTypeface(typeface, Typeface.BOLD)
                        setTextColor(ctx.getColor(R.color.primary_dark))
                        setPadding(0, UiKit.dp(ctx, 10), 0, UiKit.dp(ctx, 4))
                    })
                    dayItems.forEachIndexed { i, item -> inner.addView(exerciseRow(ctx, i + 1, item, plan)) }
                }
                addView(card)
            }

            addView(MaterialButton(ctx).apply {
                text = "开始可识别动作训练"
                textSize = 17f
                cornerRadius = UiKit.dp(ctx, 26)
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 52)).apply { topMargin = UiKit.dp(ctx, 4) }
                setOnClickListener { startTraining(plan, items) }
            })

            addView(UiKit.outlinedButton(ctx, "编辑计划") { showEditDialog(plan, items) }.apply {
                cornerRadius = UiKit.dp(ctx, 26)
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = UiKit.dp(ctx, 8) }
            })

            addView(UiKit.outlinedButton(ctx, "删除计划") { confirmDelete(plan) }.apply {
                cornerRadius = UiKit.dp(ctx, 26)
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = UiKit.dp(ctx, 8) }
            })
        }
        return ScrollView(ctx).apply { setBackgroundColor(ctx.getColor(R.color.bg)); addView(root) }
    }

    private data class Item(
        val type: String, val title: String, val category: String, val week: Int, val day: Int,
        val durationMin: Int, val distanceKm: Double, val sets: Int, val reps: Int, val intensity: String, val note: String
    )

    private fun parseExercises(raw: String?): List<Item> {
        val out = mutableListOf<Item>()
        try {
            val arr = JSONArray(raw ?: "[]")
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                val type = o.optString("type", o.optString("exercise_type", "custom"))
                out += Item(
                    type = type,
                    title = o.optString("title", labels[type] ?: type),
                    category = o.optString("category", inferCategory(type)),
                    week = o.optInt("week", 1),
                    day = o.optInt("day", 1),
                    durationMin = o.optInt("duration_min", 0),
                    distanceKm = o.optDouble("distance_km", 0.0),
                    sets = o.optInt("sets", o.optInt("target_sets", 0)),
                    reps = o.optInt("reps", o.optInt("target_reps", 0)),
                    intensity = o.optString("intensity", ""),
                    note = o.optString("note", o.optString("intensity_note", ""))
                )
            }
        } catch (_: Exception) {}
        return out
    }

    private fun estimateMinutes(items: List<Item>): Int = items.sumOf { if (it.durationMin > 0) it.durationMin else (it.sets.takeIf { s -> s > 0 } ?: 1) * 3 }.coerceAtLeast(10)
    private fun summarizeDays(items: List<Item>): String = items.map { it.day }.distinct().sorted().joinToString("/", prefix = "第 ", postfix = " 天")

    private fun exerciseRow(ctx: android.content.Context, idx: Int, item: Item, plan: WorkoutPlan): View {
        val box = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; setPadding(0, UiKit.dp(ctx, 8), 0, UiKit.dp(ctx, 8)) }
        val row = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        row.addView(TextView(ctx).apply {
            text = idx.toString(); textSize = 13f; gravity = Gravity.CENTER; setTypeface(typeface, Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.primary_dark))
            layoutParams = LinearLayout.LayoutParams(UiKit.dp(ctx, 28), UiKit.dp(ctx, 28)).apply { rightMargin = UiKit.dp(ctx, 12) }
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(ctx.getColor(R.color.primary_alpha10)) }
        })
        val col = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f) }
        col.addView(TextView(ctx).apply { text = item.title.ifBlank { labels[item.type] ?: item.type }; textSize = 15f; setTypeface(typeface, Typeface.BOLD); setTextColor(ctx.getColor(R.color.on_surface)) })
        col.addView(UiKit.caption(ctx, specText(item)))
        if (item.note.isNotBlank()) col.addView(UiKit.caption(ctx, item.note))
        row.addView(col)
        if (trackable.contains(item.type.lowercase())) {
            row.addView(smallButton(ctx, "开始") { startTraining(plan, listOf(item)) })
        } else {
            row.addView(smallButton(ctx, "完成打卡") { checkin(plan, item) })
        }
        box.addView(row)
        return box
    }

    private fun smallButton(ctx: android.content.Context, textValue: String, onClick: () -> Unit): MaterialButton =
        MaterialButton(ctx).apply {
            text = textValue; textSize = 12f; cornerRadius = UiKit.dp(ctx, 16); minWidth = 0; minimumWidth = 0
            setPadding(UiKit.dp(ctx, 12), 0, UiKit.dp(ctx, 12), 0); setOnClickListener { onClick() }
        }

    private fun specText(item: Item): String {
        val parts = mutableListOf<String>()
        parts.add(item.category)
        if (item.durationMin > 0) parts.add("${item.durationMin}分钟")
        if (item.distanceKm > 0) parts.add("${item.distanceKm}km")
        if (item.sets > 0 && item.reps > 0) parts.add("${item.sets}组×${item.reps}") else if (item.reps > 0) parts.add("${item.reps}次")
        if (item.intensity.isNotBlank()) parts.add(item.intensity)
        return parts.joinToString(" · ").ifBlank { "自由完成" }
    }

    private fun startTraining(plan: WorkoutPlan, items: List<Item>) {
        val first = items.firstOrNull { trackable.contains(it.type.lowercase()) } ?: items.firstOrNull()
        PlanIntent.set(plan.planId, plan.name, first?.type, first?.reps?.takeIf { it > 0 })
        try { findNavController().navigate(R.id.trainingFragment) }
        catch (e: Exception) { Toast.makeText(requireContext(), "跳转失败: ${e.message}", Toast.LENGTH_SHORT).show() }
    }

    private fun checkin(plan: WorkoutPlan, item: Item) {
        lifecycleScope.launch {
            try {
                val res = ApiClient.service.checkinPlanItem(plan.planId, PlanCheckinRequest(item = itemToMap(item)))
                if (res.ok) Toast.makeText(requireContext(), "已打卡：${item.title}", Toast.LENGTH_SHORT).show()
                else Toast.makeText(requireContext(), res.message ?: "打卡失败", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) { Toast.makeText(requireContext(), "打卡失败: ${e.message}", Toast.LENGTH_SHORT).show() }
        }
    }

    private fun showEditDialog(plan: WorkoutPlan, items: List<Item>) {
        val ctx = requireContext()
        val container = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; setPadding(UiKit.dp(ctx, 18), UiKit.dp(ctx, 8), UiKit.dp(ctx, 18), 0) }
        val nameInput = EditText(ctx).apply { hint = "计划名"; setText(plan.name); setSingleLine(true) }
        val exercisesInput = EditText(ctx).apply {
            hint = "格式：天,项目名,类别,type,分钟,公里,组数,次数,强度,备注"
            minLines = 10; maxLines = 16; setText(renderPlanLines(items))
        }
        container.addView(nameInput); container.addView(exercisesInput)
        AlertDialog.Builder(ctx)
            .setTitle("编辑训练计划")
            .setMessage("每行一项；删除/新增行即可删改项目。")
            .setView(container)
            .setNegativeButton("取消", null)
            .setPositiveButton("保存") { _, _ ->
                val name = nameInput.text?.toString()?.trim().orEmpty().ifBlank { plan.name }
                val exercises = parsePlanLines(exercisesInput.text?.toString().orEmpty())
                if (exercises.isEmpty()) Toast.makeText(ctx, "至少保留一个项目", Toast.LENGTH_SHORT).show()
                else savePlanEdit(plan, name, exercises)
            }.show()
    }

    private fun renderPlanLines(items: List<Item>): String = items.joinToString("\n") { item ->
        listOf(item.day, item.title, item.category, item.type, item.durationMin, item.distanceKm, item.sets, item.reps, item.intensity, item.note).joinToString(",")
    }

    private fun parsePlanLines(text: String): List<Map<String, Any>> = text.lines().mapNotNull { line ->
        val p = line.split(",", limit = 10).map { it.trim() }
        val title = p.getOrNull(1).orEmpty()
        val type = p.getOrNull(3).orEmpty().ifBlank { title.ifBlank { "custom" } }
        if (title.isBlank() && type.isBlank()) return@mapNotNull null
        mapOf(
            "day" to (p.getOrNull(0)?.toIntOrNull() ?: 1), "title" to title.ifBlank { type }, "category" to p.getOrNull(2).orEmpty().ifBlank { inferCategory(type) },
            "type" to type, "duration_min" to (p.getOrNull(4)?.toIntOrNull() ?: 0), "distance_km" to (p.getOrNull(5)?.toDoubleOrNull() ?: 0.0),
            "sets" to (p.getOrNull(6)?.toIntOrNull() ?: 0), "reps" to (p.getOrNull(7)?.toIntOrNull() ?: 0), "intensity" to p.getOrNull(8).orEmpty(), "note" to p.getOrNull(9).orEmpty()
        )
    }

    private fun itemToMap(item: Item): Map<String, Any> = mapOf(
        "type" to item.type, "title" to item.title, "category" to item.category, "week" to item.week, "day" to item.day,
        "duration_min" to item.durationMin, "distance_km" to item.distanceKm, "sets" to item.sets, "reps" to item.reps,
        "intensity" to item.intensity, "note" to item.note
    )

    private fun savePlanEdit(plan: WorkoutPlan, name: String, exercises: List<Map<String, Any>>) {
        lifecycleScope.launch {
            try {
                val res = ApiClient.service.updatePlan(plan.planId, UpdatePlanRequest(name = name, exercises = exercises))
                if (res.ok) {
                    PlanDetailHolder.plan = res.plan ?: plan.copy(name = name, exercises = JSONArray(exercises).toString())
                    Toast.makeText(requireContext(), "已保存", Toast.LENGTH_SHORT).show()
                    findNavController().popBackStack()
                } else Toast.makeText(requireContext(), res.message ?: "保存失败", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) { Toast.makeText(requireContext(), "保存失败: ${e.message}", Toast.LENGTH_SHORT).show() }
        }
    }

    private fun confirmDelete(plan: WorkoutPlan) {
        AlertDialog.Builder(requireContext())
            .setTitle("删除计划")
            .setMessage("确定删除「${plan.name}」?")
            .setPositiveButton("删除") { _, _ ->
                lifecycleScope.launch {
                    try { ApiClient.service.deletePlan(plan.planId); Toast.makeText(requireContext(), "已删除", Toast.LENGTH_SHORT).show(); findNavController().popBackStack() }
                    catch (e: Exception) { if (isAdded) Toast.makeText(requireContext(), "删除失败: ${e.message}", Toast.LENGTH_SHORT).show() }
                }
            }.setNegativeButton("取消", null).show()
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
}
