package com.smartfitness.app.ui.plans

import android.app.AlertDialog
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
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
import com.smartfitness.app.model.WorkoutPlan
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.launch
import org.json.JSONArray

/**
 * 计划详情页 (Keep 式: 卡片化动作列表 + 大号开始训练按钮)。
 * 选中的计划经 PlanDetailHolder 传入。
 */
class PlanDetailFragment : Fragment() {

    private val labels = mapOf(
        "squat" to "深蹲", "push_up" to "俯卧撑", "pushup" to "俯卧撑",
        "plank" to "平板支撑", "lunge" to "弓步蹲", "jumping_jack" to "开合跳",
        "bicep_curl" to "二头弯举", "shoulder_press" to "肩上推举"
    )

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View {
        val ctx = inflater.context
        val plan = PlanDetailHolder.plan

        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 24))
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }
        root.addView(UiKit.topBar(ctx, "计划详情") { findNavController().popBackStack() })

        if (plan == null) {
            root.addView(UiKit.caption(ctx, "计划信息丢失, 请返回重试"))
            return ScrollView(ctx).apply { setBackgroundColor(ctx.getColor(R.color.bg)); addView(root) }
        }

        val items = parseExercises(plan.exercises)

        with(root) {
            // 标题卡
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(TextView(ctx).apply {
                    text = plan.name
                    textSize = 22f
                    setTypeface(typeface, Typeface.BOLD)
                    setTextColor(ctx.getColor(R.color.on_surface))
                })
                inner.addView(UiKit.caption(ctx, "${items.size} 个动作  ·  预计 ${estimateMinutes(items)} 分钟").apply {
                    setPadding(0, UiKit.dp(ctx, 4), 0, 0)
                })
                addView(cardView)
            }

            // 动作列表
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "动作列表"))
                if (items.isEmpty()) {
                    inner.addView(UiKit.caption(ctx, "这是一个空白计划, 还没有动作。直接开始可自由训练。"))
                } else {
                    items.forEachIndexed { i, it -> inner.addView(exerciseRow(ctx, i + 1, it)) }
                }
                addView(cardView)
            }

            // 开始训练 (主按钮)
            addView(MaterialButton(ctx).apply {
                text = "开始训练"
                textSize = 17f
                cornerRadius = UiKit.dp(ctx, 26)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 52)
                ).apply { topMargin = UiKit.dp(ctx, 4) }
                setOnClickListener { startTraining(plan, items) }
            })

            // 删除 (次级)
            addView(UiKit.outlinedButton(ctx, "删除计划") { confirmDelete(plan) }.apply {
                cornerRadius = UiKit.dp(ctx, 26)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
                ).apply { topMargin = UiKit.dp(ctx, 8) }
            })
        }

        return ScrollView(ctx).apply { setBackgroundColor(ctx.getColor(R.color.bg)); addView(root) }
    }

    private data class Item(val type: String, val sets: Int, val reps: Int, val note: String)

    private fun parseExercises(raw: String?): List<Item> {
        val out = mutableListOf<Item>()
        try {
            val arr = JSONArray(raw ?: "[]")
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                out += Item(
                    type = o.optString("type", "?"),
                    sets = o.optInt("sets", 0),
                    reps = o.optInt("reps", 0),
                    note = o.optString("note", "")
                )
            }
        } catch (_: Exception) {}
        return out
    }

    private fun estimateMinutes(items: List<Item>): Int {
        if (items.isEmpty()) return 10
        // 粗略: 每组约 1 分钟 (含组间休息)
        return items.sumOf { (it.sets.takeIf { s -> s > 0 } ?: 1) }.coerceAtLeast(items.size)
    }

    private fun exerciseRow(ctx: android.content.Context, idx: Int, it: Item): View {
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, UiKit.dp(ctx, 10), 0, UiKit.dp(ctx, 10))
        }
        // 序号圆点
        row.addView(TextView(ctx).apply {
            text = idx.toString()
            textSize = 13f
            gravity = Gravity.CENTER
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.primary_dark))
            layoutParams = LinearLayout.LayoutParams(UiKit.dp(ctx, 28), UiKit.dp(ctx, 28)).apply {
                rightMargin = UiKit.dp(ctx, 12)
            }
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(ctx.getColor(R.color.primary_alpha10))
            }
        })
        val col = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        col.addView(TextView(ctx).apply {
            text = labels[it.type] ?: it.type
            textSize = 15f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
        })
        if (it.note.isNotBlank()) {
            col.addView(TextView(ctx).apply {
                text = it.note
                textSize = 12f
                setTextColor(ctx.getColor(R.color.on_surface_tertiary))
                setPadding(0, UiKit.dp(ctx, 2), 0, 0)
            })
        }
        row.addView(col)
        // 组数 × 次数
        val spec = when {
            it.sets > 0 && it.reps > 0 -> "${it.sets} 组 × ${it.reps}"
            it.reps > 0 -> "${it.reps} 次"
            it.sets > 0 -> "${it.sets} 组"
            else -> "自由"
        }
        row.addView(TextView(ctx).apply {
            text = spec
            textSize = 14f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.primary_dark))
        })
        return row
    }

    private fun startTraining(plan: WorkoutPlan, items: List<Item>) {
        val first = items.firstOrNull()
        PlanIntent.set(plan.planId, plan.name, first?.type, first?.reps?.takeIf { it > 0 })
        try {
            findNavController().navigate(R.id.trainingFragment)
        } catch (e: Exception) {
            Toast.makeText(requireContext(), "跳转失败: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun confirmDelete(plan: WorkoutPlan) {
        AlertDialog.Builder(requireContext())
            .setTitle("删除计划")
            .setMessage("确定删除「${plan.name}」?")
            .setPositiveButton("删除") { _, _ ->
                lifecycleScope.launch {
                    try {
                        ApiClient.service.deletePlan(plan.planId)
                        if (isAdded) {
                            Toast.makeText(requireContext(), "已删除", Toast.LENGTH_SHORT).show()
                            findNavController().popBackStack()
                        }
                    } catch (e: Exception) {
                        if (isAdded) Toast.makeText(requireContext(), "删除失败: ${e.message}", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }
}
