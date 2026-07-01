package com.smartfitness.app.ui.profile

import android.app.AlertDialog
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
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.fragment.findNavController
import com.google.android.material.button.MaterialButton
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.model.BodyMetric
import com.smartfitness.app.model.BodyMetricRequest
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * 身体数据详情页 (Keep 式: 查看为主, 录入为辅)。
 * 顶部当前体重大字 + BMI 分类徽章; 体重趋势柱状图; 历史记录列表; 底部"记录"按钮。
 */
class BodyMetricsFragment : Fragment() {

    private lateinit var ctx: android.content.Context
    private lateinit var headerWeight: TextView
    private lateinit var headerUnit: TextView
    private lateinit var bmiBadge: TextView
    private lateinit var subStats: TextView
    private lateinit var trendRow: LinearLayout
    private lateinit var trendHint: TextView
    private lateinit var historyContainer: LinearLayout

    private var lastKnownHeight: Double? = null

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
            addView(UiKit.topBar(ctx, "身体数据") { findNavController().popBackStack() })

            // ===== 概览卡: 大字体重 + BMI 徽章 =====
            UiKit.card(ctx).let { (cardView, inner) ->
                val topRow = LinearLayout(ctx).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.CENTER_VERTICAL
                }
                val weightCol = LinearLayout(ctx).apply {
                    orientation = LinearLayout.VERTICAL
                    layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                }
                weightCol.addView(UiKit.caption(ctx, "当前体重"))
                val wRow = LinearLayout(ctx).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.BOTTOM
                }
                headerWeight = TextView(ctx).apply {
                    text = "--"
                    setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD))
                    textSize = 44f
                    setTextColor(ctx.getColor(R.color.on_surface))
                }
                headerUnit = TextView(ctx).apply {
                    text = " kg"
                    textSize = 16f
                    setTextColor(ctx.getColor(R.color.on_surface_secondary))
                    setPadding(0, 0, 0, UiKit.dp(ctx, 8))
                }
                wRow.addView(headerWeight); wRow.addView(headerUnit)
                weightCol.addView(wRow)
                topRow.addView(weightCol)

                bmiBadge = TextView(ctx).apply {
                    text = "BMI --"
                    textSize = 13f
                    setTypeface(typeface, Typeface.BOLD)
                    setTextColor(ctx.getColor(R.color.primary_dark))
                    background = GradientDrawable().apply {
                        cornerRadius = UiKit.dp(ctx, 16).toFloat()
                        setColor(ctx.getColor(R.color.primary_alpha10))
                    }
                    setPadding(UiKit.dp(ctx, 14), UiKit.dp(ctx, 8), UiKit.dp(ctx, 14), UiKit.dp(ctx, 8))
                }
                topRow.addView(bmiBadge)
                inner.addView(topRow)

                subStats = TextView(ctx).apply {
                    text = "身高 -- · 体脂 -- · 较首次 --"
                    textSize = 13f
                    setTextColor(ctx.getColor(R.color.on_surface_secondary))
                    setPadding(0, UiKit.dp(ctx, 10), 0, 0)
                }
                inner.addView(subStats)
                addView(cardView)
            }

            // ===== 趋势卡: 体重柱状趋势 =====
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "体重趋势"))
                trendHint = UiKit.caption(ctx, "记录两次以上即可看到变化曲线").also { inner.addView(it) }
                trendRow = LinearLayout(ctx).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = Gravity.BOTTOM
                    setPadding(0, UiKit.dp(ctx, 14), 0, 0)
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 90)
                    )
                }
                inner.addView(trendRow)
                addView(cardView)
            }

            // ===== 历史记录卡 =====
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "历史记录"))
                historyContainer = LinearLayout(ctx).apply {
                    orientation = LinearLayout.VERTICAL
                }
                inner.addView(historyContainer)
                addView(cardView)
            }

            // ===== 记录按钮 (主动作) =====
            addView(MaterialButton(ctx).apply {
                text = "＋ 记录身体数据"
                textSize = 16f
                cornerRadius = UiKit.dp(ctx, 24)
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 50)
                ).apply { topMargin = UiKit.dp(ctx, 4) }
                setOnClickListener { showRecordDialog() }
            })
        }

        return ScrollView(ctx).apply {
            setBackgroundColor(ctx.getColor(R.color.bg))
            addView(root)
        }
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        loadMetrics()
    }

    private fun bmiCategory(bmi: Double): String = when {
        bmi < 18.5 -> "偏瘦"
        bmi < 24.0 -> "正常"
        bmi < 28.0 -> "超重"
        else -> "肥胖"
    }

    private fun computeBmi(weight: Double?, height: Double?): Double? {
        if (weight == null || height == null || height <= 0) return null
        val m = height / 100.0
        return weight / (m * m)
    }

    private fun loadMetrics() {
        lifecycleScope.launch {
            try {
                val resp = withContext(Dispatchers.IO) { ApiClient.service.listBodyMetrics(limit = 30) }
                if (!isAdded) return@launch
                val metrics = resp.metrics            // DESC (新→旧)
                lastKnownHeight = metrics.firstOrNull { it.heightCm != null }?.heightCm
                renderHeader(metrics)
                renderTrend(metrics)
                renderHistory(metrics)
            } catch (e: Exception) {
                if (isAdded) Toast.makeText(ctx, "加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun renderHeader(metrics: List<BodyMetric>) {
        val latest = metrics.firstOrNull()
        if (latest?.weightKg == null) {
            headerWeight.text = "--"
            bmiBadge.text = "未记录"
            subStats.text = "还没有数据, 点下方按钮记录第一条"
            return
        }
        headerWeight.text = String.format(Locale.getDefault(), "%.1f", latest.weightKg)
        val height = latest.heightCm ?: lastKnownHeight
        val bmi = latest.bmi ?: computeBmi(latest.weightKg, height)
        bmiBadge.text = if (bmi != null)
            "BMI ${String.format(Locale.getDefault(), "%.1f", bmi)} · ${bmiCategory(bmi)}"
        else "BMI --"

        val first = metrics.lastOrNull { it.weightKg != null }
        val delta = if (first?.weightKg != null) latest.weightKg - first.weightKg else null
        val parts = mutableListOf<String>()
        parts += if (height != null) "身高 ${String.format(Locale.getDefault(), "%.0f", height)}cm" else "身高 --"
        parts += if (latest.bodyFatPct != null) "体脂 ${String.format(Locale.getDefault(), "%.1f", latest.bodyFatPct)}%" else "体脂 --"
        if (delta != null) parts += "较首次 ${String.format(Locale.getDefault(), "%+.1f", delta)}kg"
        subStats.text = parts.joinToString("  ·  ")
    }

    private fun renderTrend(metrics: List<BodyMetric>) {
        trendRow.removeAllViews()
        val series = metrics.filter { it.weightKg != null }.reversed()   // 旧→新
            .takeLast(12)
        if (series.size < 2) {
            trendRow.visibility = View.GONE
            trendHint.visibility = View.VISIBLE
            return
        }
        trendRow.visibility = View.VISIBLE
        trendHint.visibility = View.GONE
        val weights = series.map { it.weightKg!! }
        val min = weights.min(); val max = weights.max()
        val range = (max - min).takeIf { it > 0.01 } ?: 1.0
        series.forEachIndexed { i, m ->
            val frac = ((m.weightKg!! - min) / range)          // 0..1
            val hDp = (18 + frac * 56).toInt()                 // 18..74 dp
            val col = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER_HORIZONTAL or Gravity.BOTTOM
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f).apply {
                    leftMargin = UiKit.dp(ctx, 3); rightMargin = UiKit.dp(ctx, 3)
                }
            }
            val isLast = i == series.size - 1
            col.addView(TextView(ctx).apply {
                text = String.format(Locale.getDefault(), "%.0f", m.weightKg)
                textSize = 9f
                setTextColor(ctx.getColor(if (isLast) R.color.primary else R.color.on_surface_tertiary))
            })
            col.addView(View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, hDp)
                ).apply { topMargin = UiKit.dp(ctx, 2) }
                background = GradientDrawable().apply {
                    cornerRadius = UiKit.dp(ctx, 4).toFloat()
                    setColor(ctx.getColor(if (isLast) R.color.primary else R.color.divider))
                }
            })
            trendRow.addView(col)
        }
    }

    private fun renderHistory(metrics: List<BodyMetric>) {
        historyContainer.removeAllViews()
        if (metrics.isEmpty()) {
            historyContainer.addView(UiKit.caption(ctx, "暂无记录"))
            return
        }
        val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())
        metrics.forEach { m ->
            val row = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(0, UiKit.dp(ctx, 10), 0, UiKit.dp(ctx, 10))
            }
            val ts = m.timestamp ?: 0.0
            row.addView(TextView(ctx).apply {
                text = fmt.format(java.util.Date((ts * 1000).toLong()))
                textSize = 13f
                setTextColor(ctx.getColor(R.color.on_surface_secondary))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            })
            val height = m.heightCm ?: lastKnownHeight
            val bmi = m.bmi ?: computeBmi(m.weightKg, height)
            val parts = mutableListOf<String>()
            if (m.weightKg != null) parts += "${String.format(Locale.getDefault(), "%.1f", m.weightKg)}kg"
            if (bmi != null) parts += "BMI ${String.format(Locale.getDefault(), "%.1f", bmi)}"
            if (m.bodyFatPct != null) parts += "体脂${String.format(Locale.getDefault(), "%.1f", m.bodyFatPct)}%"
            row.addView(TextView(ctx).apply {
                text = parts.joinToString("  ")
                textSize = 14f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.on_surface))
            })
            historyContainer.addView(row)
            historyContainer.addView(View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 1)
                setBackgroundColor(ctx.getColor(R.color.divider))
            })
        }
    }

    private fun showRecordDialog() {
        val container = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 8)
        }
        val weightEt = EditText(ctx).apply {
            hint = "体重 (kg)"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        }
        val heightEt = EditText(ctx).apply {
            hint = "身高 (cm" + (lastKnownHeight?.let { ", 上次 ${it.toInt()}" } ?: "") + ")"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            lastKnownHeight?.let { setText(it.toInt().toString()) }
        }
        val fatEt = EditText(ctx).apply {
            hint = "体脂率 % (可空)"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        }
        container.addView(weightEt); container.addView(heightEt); container.addView(fatEt)
        AlertDialog.Builder(ctx)
            .setTitle("记录身体数据")
            .setView(container)
            .setPositiveButton("保存") { _, _ ->
                val w = weightEt.text.toString().toDoubleOrNull()
                val h = heightEt.text.toString().toDoubleOrNull()
                val f = fatEt.text.toString().toDoubleOrNull()
                if (w == null && h == null && f == null) {
                    Toast.makeText(ctx, "请至少填写一项", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) {
                            ApiClient.service.addBodyMetric(
                                BodyMetricRequest(weightKg = w, heightCm = h, bodyFatPct = f)
                            )
                        }
                        if (!isAdded) return@launch
                        Toast.makeText(ctx, "已保存 ✅", Toast.LENGTH_SHORT).show()
                        loadMetrics()
                    } catch (e: Exception) {
                        if (isAdded) Toast.makeText(ctx, "保存失败: ${e.message}", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }
}
