package com.smartfitness.app.ui.profile

import android.app.AlertDialog
import android.content.Context
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.button.MaterialButton
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Locale
import kotlin.math.abs

/**
 * 我的目标详情页 (Keep 式: 进度可视化 + 编辑次级动作)。
 * 目标存 SharedPrefs("sf_goals")，进度用真实训练数据计算。
 */
class GoalsFragment : Fragment() {

    private lateinit var ctx: Context
    private lateinit var cardsContainer: LinearLayout

    private fun prefs() = requireContext().getSharedPreferences("sf_goals", Context.MODE_PRIVATE)

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?
    ): View {
        ctx = inflater.context
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 24))
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }
        with(root) {
            addView(UiKit.topBar(ctx, "我的目标") { findNavController().popBackStack() })
            cardsContainer = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
            }
            addView(cardsContainer)
            addView(MaterialButton(ctx).apply {
                text = "编辑目标"
                textSize = 16f
                cornerRadius = UiKit.dp(ctx, 24)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 50)
                ).apply { topMargin = UiKit.dp(ctx, 4) }
                setOnClickListener { showEditDialog() }
            })
        }
        return ScrollView(ctx).apply {
            setBackgroundColor(ctx.getColor(R.color.bg))
            addView(root)
        }
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        loadGoals()
    }

    private fun loadGoals() {
        val p = prefs()
        val targetWeight = p.getString("target_weight", null)?.toDoubleOrNull()
        val weeklyWorkouts = p.getInt("weekly_workouts", 0)
        val dailyReps = p.getInt("daily_reps", 0)

        cardsContainer.removeAllViews()
        if (targetWeight == null && weeklyWorkouts == 0 && dailyReps == 0) {
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "还没有设置目标"))
                inner.addView(UiKit.caption(ctx, "设置体重 / 每周训练 / 每日 reps 目标，训练后这里会显示完成进度"))
                cardsContainer.addView(cardView)
            }
            return
        }

        // 周训练次数 & 每日 reps 用真实数据
        lifecycleScope.launch {
            val weekSessions = try {
                withContext(Dispatchers.IO) { ApiClient.service.exerciseSummary(7) }
                    .byType.sumOf { it.sessions }
            } catch (_: Exception) { 0 }
            val todayReps = try {
                withContext(Dispatchers.IO) { ApiClient.service.statsDaily() }.stats?.totalReps ?: 0
            } catch (_: Exception) { 0 }
            // 体重: 取首条(起点)与最新(当前)
            var startW: Double? = null; var curW: Double? = null
            try {
                val ms = withContext(Dispatchers.IO) { ApiClient.service.listBodyMetrics(30) }
                    .metrics.filter { it.weightKg != null }
                curW = ms.firstOrNull()?.weightKg
                startW = ms.lastOrNull()?.weightKg
            } catch (_: Exception) {}

            if (!isAdded) return@launch
            cardsContainer.removeAllViews()

            if (targetWeight != null) {
                val (pct, sub) = weightProgress(startW, curW, targetWeight)
                cardsContainer.addView(goalCard(
                    "目标体重", "${fmt1(targetWeight)} kg",
                    curW?.let { "当前 ${fmt1(it)} kg" } ?: "未记录体重", sub, pct))
            }
            if (weeklyWorkouts > 0) {
                val pct = (weekSessions * 100 / weeklyWorkouts).coerceIn(0, 100)
                cardsContainer.addView(goalCard(
                    "每周训练", "$weeklyWorkouts 次/周",
                    "本周已训练 $weekSessions 次",
                    if (weekSessions >= weeklyWorkouts) "已达标 🎉" else "还差 ${weeklyWorkouts - weekSessions} 次",
                    pct))
            }
            if (dailyReps > 0) {
                val pct = (todayReps * 100 / dailyReps).coerceIn(0, 100)
                cardsContainer.addView(goalCard(
                    "每日 reps", "$dailyReps 个/天",
                    "今日已完成 $todayReps 个",
                    if (todayReps >= dailyReps) "已达标 🎉" else "还差 ${dailyReps - todayReps} 个",
                    pct))
            }
        }
    }

    /** 体重进度: 起点→目标的完成度; 无历史时回退到 0/差值提示 */
    private fun weightProgress(start: Double?, cur: Double?, target: Double): Pair<Int, String> {
        if (cur == null) return 0 to "记录体重后显示进度"
        val diff = cur - target
        if (start == null || abs(start - target) < 0.1) {
            return (if (abs(diff) < 0.5) 100 else 0) to
                (if (abs(diff) < 0.5) "已达标 🎉" else "距目标 ${fmt1(abs(diff))} kg")
        }
        val pct = (((start - cur) / (start - target)) * 100).toInt().coerceIn(0, 100)
        val sub = if (abs(diff) < 0.5) "已达标 🎉" else "距目标还差 ${fmt1(abs(diff))} kg"
        return pct to sub
    }

    private fun fmt1(v: Double) = String.format(Locale.getDefault(), "%.1f", v)

    /** 单个目标卡: 标题 + 目标值 + 进度条 + 当前/剩余 */
    private fun goalCard(title: String, targetText: String, currentText: String, subText: String, pct: Int): View {
        val (cardView, inner) = UiKit.card(ctx)
        val titleRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        titleRow.addView(TextView(ctx).apply {
            text = title
            textSize = 16f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        titleRow.addView(TextView(ctx).apply {
            text = targetText
            textSize = 14f
            setTextColor(ctx.getColor(R.color.primary_dark))
        })
        inner.addView(titleRow)

        inner.addView(progressBar(pct).also {
            (it.layoutParams as LinearLayout.LayoutParams).topMargin = UiKit.dp(ctx, 12)
        })

        val infoRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, UiKit.dp(ctx, 8), 0, 0)
        }
        infoRow.addView(TextView(ctx).apply {
            text = currentText
            textSize = 13f
            setTextColor(ctx.getColor(R.color.on_surface_secondary))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        })
        infoRow.addView(TextView(ctx).apply {
            text = "$subText  ·  $pct%"
            textSize = 13f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(ctx.getColor(if (pct >= 100) R.color.primary else R.color.on_surface_secondary))
        })
        inner.addView(infoRow)
        return cardView
    }

    /** 圆角进度条 (两段加权 LinearLayout 实现) */
    private fun progressBar(pct: Int): View {
        val p = pct.coerceIn(0, 100)
        val track = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 10)
            )
            background = GradientDrawable().apply {
                cornerRadius = UiKit.dp(ctx, 5).toFloat()
                setColor(ctx.getColor(R.color.divider))
            }
        }
        if (p > 0) {
            track.addView(View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, p.toFloat())
                background = GradientDrawable().apply {
                    cornerRadius = UiKit.dp(ctx, 5).toFloat()
                    setColor(ctx.getColor(R.color.primary))
                }
            })
        }
        if (p < 100) {
            track.addView(View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, (100 - p).toFloat())
            })
        }
        return track
    }

    private fun showEditDialog() {
        val container = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 8)
        }
        val twEt = EditText(ctx).apply {
            hint = "目标体重 (kg, 可空)"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            setText(prefs().getString("target_weight", ""))
        }
        val wwEt = EditText(ctx).apply {
            hint = "每周训练次数 (可空)"
            inputType = InputType.TYPE_CLASS_NUMBER
            prefs().getInt("weekly_workouts", 0).takeIf { it > 0 }?.let { setText(it.toString()) }
        }
        val drEt = EditText(ctx).apply {
            hint = "每日 reps 目标 (可空)"
            inputType = InputType.TYPE_CLASS_NUMBER
            prefs().getInt("daily_reps", 0).takeIf { it > 0 }?.let { setText(it.toString()) }
        }
        container.addView(twEt); container.addView(wwEt); container.addView(drEt)
        AlertDialog.Builder(ctx)
            .setTitle("设置目标")
            .setView(container)
            .setPositiveButton("保存") { _, _ ->
                val ed = prefs().edit()
                val tw = twEt.text.toString().trim()
                ed.putString("target_weight", tw.ifEmpty { null })
                ed.putInt("weekly_workouts", wwEt.text.toString().toIntOrNull() ?: 0)
                ed.putInt("daily_reps", drEt.text.toString().toIntOrNull() ?: 0)
                ed.apply()
                loadGoals()
            }
            .setNegativeButton("取消", null)
            .show()
    }
}
