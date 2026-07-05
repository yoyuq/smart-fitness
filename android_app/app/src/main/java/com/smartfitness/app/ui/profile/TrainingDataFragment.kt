package com.smartfitness.app.ui.profile

import android.app.AlertDialog
import android.graphics.BitmapFactory
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
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
import com.smartfitness.app.api.UserDataCache
import com.smartfitness.app.model.TrainingDataResponse
import com.smartfitness.app.model.TrainingDataSession
import com.smartfitness.app.model.TrainingRep
import com.smartfitness.app.model.AiCoachRequest
import com.smartfitness.app.model.AiCoachResponse
import com.smartfitness.app.model.SessionAiCoachResponse
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class TrainingDataFragment : Fragment() {
    private lateinit var summaryContainer: LinearLayout
    private lateinit var typeContainer: LinearLayout
    private lateinit var sessionsContainer: LinearLayout
    private val periods = listOf("day" to "日", "week" to "周", "month" to "月", "year" to "年")
    private var currentPeriod = "day"

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        val ctx = inflater.context
        val scroll = ScrollView(ctx).apply { setBackgroundColor(ctx.getColor(R.color.bg)) }
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 24), UiKit.dp(ctx, 28), UiKit.dp(ctx, 24), UiKit.dp(ctx, 28))
        }
        scroll.addView(root)
        root.addView(UiKit.topBar(ctx, "我的训练数据") { findNavController().popBackStack() })

        val periodRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 0, 0, UiKit.dp(ctx, 12))
        }
        periods.forEach { (key, label) ->
            periodRow.addView(MaterialButton(ctx).apply {
                text = label
                cornerRadius = UiKit.dp(ctx, 14)
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                    leftMargin = UiKit.dp(ctx, 3); rightMargin = UiKit.dp(ctx, 3)
                }
                setOnClickListener { currentPeriod = key; loadData() }
            })
        }
        root.addView(periodRow)

        UiKit.card(ctx).let { (card, inner) ->
            inner.addView(UiKit.cardTitle(ctx, "周期概览"))
            summaryContainer = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
            inner.addView(summaryContainer)
            root.addView(card)
        }
        UiKit.card(ctx).let { (card, inner) ->
            inner.addView(UiKit.cardTitle(ctx, "动作汇总"))
            typeContainer = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
            inner.addView(typeContainer)
            root.addView(card)
        }
        UiKit.card(ctx).let { (card, inner) ->
            inner.addView(UiKit.cardTitle(ctx, "训练明细"))
            sessionsContainer = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
            inner.addView(sessionsContainer)
            root.addView(card)
        }
        return scroll
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        loadData()
    }

    private fun loadData() {
        if (!isAdded) return
        summaryContainer.removeAllViews()
        typeContainer.removeAllViews()
        sessionsContainer.removeAllViews()
        summaryContainer.addView(UiKit.caption(requireContext(), "加载中…"))
        lifecycleScope.launch {
            try {
                val data = withContext(Dispatchers.IO) {
                    try {
                        val online = ApiClient.service.trainingData(currentPeriod)
                        UserDataCache.syncAll(requireContext())
                        online
                    } catch (_: Exception) {
                        UserDataCache.trainingData(requireContext(), currentPeriod)
                            ?: throw Exception("离线且没有已同步的训练数据")
                    }
                }
                if (!isAdded) return@launch
                render(data)
            } catch (e: Exception) {
                if (!isAdded) return@launch
                summaryContainer.removeAllViews()
                summaryContainer.addView(UiKit.caption(requireContext(), "加载失败: ${e.message}"))
            }
        }
    }

    private fun render(data: TrainingDataResponse) {
        val ctx = requireContext()
        summaryContainer.removeAllViews()
        typeContainer.removeAllViews()
        sessionsContainer.removeAllViews()
        val s = data.summary
        summaryContainer.addView(UiKit.body(ctx, "训练 ${s.sessionsCount} 次 · ${s.totalReps} 个 · ${String.format(Locale.getDefault(), "%.1f", s.totalMinutes)} 分钟", 16f))
        summaryContainer.addView(UiKit.caption(ctx, "平均评分 ${String.format(Locale.getDefault(), "%.1f", s.avgScore)} · 当前周期: ${periods.firstOrNull { it.first == data.period }?.second ?: data.period}"))

        if (data.byType.isEmpty()) {
            typeContainer.addView(UiKit.caption(ctx, "当前周期暂无动作汇总"))
        } else {
            data.byType.forEach { t ->
                typeContainer.addView(UiKit.body(ctx, "${t.exerciseType}: ${t.totalReps} 个 / ${t.sessions} 次 / ${String.format(Locale.getDefault(), "%.1f", t.totalSeconds / 60.0)} 分钟", 14f))
            }
        }

        if (data.sessions.isEmpty()) {
            sessionsContainer.addView(UiKit.caption(ctx, "当前周期暂无训练记录"))
        } else {
            data.sessions.forEach { addSessionCard(it) }
        }
    }

    private fun addSessionCard(session: TrainingDataSession) {
        val ctx = requireContext()
        val fmt = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())
        val box = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, UiKit.dp(ctx, 10), 0, UiKit.dp(ctx, 10))
        }
        val title = "${session.exerciseType ?: "训练"}  ${session.totalReps} 个"
        box.addView(UiKit.body(ctx, title, 16f).apply { setTypeface(typeface, android.graphics.Typeface.BOLD) })
        val score = session.avgFormScore?.let { "评分 ${String.format(Locale.getDefault(), "%.1f", it)}" } ?: "评分 --"
        box.addView(UiKit.caption(ctx, "${fmt.format(Date((session.startTime * 1000).toLong()))} · $score · ${session.repCount} 组动作"))
        // 组级 AI 分析入口（以组为单位上传分析）
        val btnRow = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, UiKit.dp(ctx, 4), 0, UiKit.dp(ctx, 8))
        }
        btnRow.addView(UiKit.outlinedButton(ctx, "🚀 AI 分析") { showSessionAiCoachPrecisionPicker(session) }.apply {
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                rightMargin = UiKit.dp(ctx, 6)
            }
            textSize = 14f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        btnRow.addView(UiKit.outlinedButton(ctx, "📂 历史报告") { showSessionReports(session) }.apply {
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            textSize = 13f
        })
        box.addView(btnRow)
        if (session.reps.isEmpty()) {
            box.addView(UiKit.caption(ctx, "暂无逐次动作图像"))
        } else {
            session.reps.forEach { rep -> box.addView(repRow(rep)) }
        }
        sessionsContainer.addView(box)
    }

    private fun repRow(rep: TrainingRep): View {
        val ctx = requireContext()
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
            setPadding(0, UiKit.dp(ctx, 6), 0, UiKit.dp(ctx, 6))
        }
        val label = rep.trueLabel ?: rep.errorType ?: "未标注"
        val score = rep.total?.let { String.format(Locale.getDefault(), "%.1f", it) } ?: "--"
        row.addView(UiKit.caption(ctx, "第${rep.repIndex ?: "?"}次 · $label · $score 分").apply {
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        })
        row.addView(UiKit.outlinedButton(ctx, "图像") { showRepImages(rep) }.apply {
            isEnabled = rep.hasImages
            textSize = 12f
        })
        return row
    }

    private fun showRepImages(rep: TrainingRep) {
        lifecycleScope.launch {
            val ctx = requireContext()
            val dialog = AlertDialog.Builder(ctx).setTitle("训练图像").setMessage("加载图像中…").show()
            try {
                val resp = withContext(Dispatchers.IO) { ApiClient.service.trainingRepImages(rep.id) }
                if (!isAdded) return@launch
                dialog.dismiss()
                val urls = (resp.keyframes + resp.frames).distinct()
                if (urls.isEmpty()) {
                    Toast.makeText(ctx, "这次训练没有保存图像", Toast.LENGTH_SHORT).show()
                    return@launch
                }
                showImageDialog(rep, urls)
            } catch (e: Exception) {
                dialog.dismiss()
                Toast.makeText(ctx, "图像加载失败: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showImageDialog(rep: TrainingRep, urls: List<String>) {
        val ctx = requireContext()
        val scroll = ScrollView(ctx)
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 12), UiKit.dp(ctx, 12), UiKit.dp(ctx, 12), UiKit.dp(ctx, 12))
        }
        scroll.addView(root)
        root.addView(UiKit.caption(ctx, "第${rep.repIndex ?: "?"}次动作，共 ${urls.size} 张图"))
        val client = ApiClient.okHttpClient
        urls.take(80).forEachIndexed { idx, rel ->
            val iv = ImageView(ctx).apply {
                adjustViewBounds = true
                scaleType = ImageView.ScaleType.CENTER_CROP
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 180)).apply {
                    topMargin = UiKit.dp(ctx, 8)
                }
                setBackgroundColor(0xFFEAEAEA.toInt())
            }
            root.addView(UiKit.caption(ctx, if (idx < 3) listOf("起始帧", "最深帧", "结束帧").getOrElse(idx) { "片段帧" } else "片段帧 ${idx - 2}"))
            root.addView(iv)
            lifecycleScope.launch {
                try {
                    val bytes = withContext(Dispatchers.IO) {
                        val url = ApiClient.BASE_URL.trimEnd('/') + rel
                        val req = okhttp3.Request.Builder().url(url).build()
                        client.newCall(req).execute().use { it.body?.bytes() }
                    }
                    if (bytes != null && isAdded) iv.setImageBitmap(BitmapFactory.decodeByteArray(bytes, 0, bytes.size))
                } catch (_: Exception) {}
            }
        }
        AlertDialog.Builder(ctx)
            .setTitle("详细训练图像")
            .setView(scroll)
            .setPositiveButton("关闭", null)
            .show()
    }

    // ---------------------------------------------------------------
    // AI 教练分析 - 两阶段视觉 + LLM 指导
    // ---------------------------------------------------------------

    private fun showAiCoachPrecisionPicker(rep: TrainingRep) {
        val ctx = requireContext()
        val options = arrayOf(
            "3 帧 - 最快 (~10s, 一杯奶茶 100+ 次)",
            "5 帧 - 推荐 (~15s, 一杯奶茶 ~40 次)",
            "7 帧 - 高精度 (~20s, 一杯奶茶 ~30 次)",
            "9 帧 - 最高精度 (~25s, 一杯奶茶 ~20 次)"
        )
        val values = intArrayOf(3, 5, 7, 9)
        AlertDialog.Builder(ctx)
            .setTitle("AI 教练分析精度")
            .setSingleChoiceItems(options, 1) { d, which ->
                d.dismiss()
                runAiCoach(rep, values[which])
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun runAiCoach(rep: TrainingRep, frames: Int) {
        val ctx = requireContext()
        val progress = AlertDialog.Builder(ctx)
            .setTitle("AI 教练分析中...")
            .setMessage("阶段一: 视觉观察 $frames 帧\n阶段二: 结合规则分 + 历史 + 文献排销幻觉\n预计 15-25 秒, 请稍候")
            .setCancelable(false)
            .create()
        progress.show()
        lifecycleScope.launch {
            try {
                val resp = withContext(Dispatchers.IO) {
                    ApiClient.service.trainingRepAiCoach(rep.id, AiCoachRequest(frames = frames))
                }
                progress.dismiss()
                if (!isAdded) return@launch
                if (!resp.ok) {
                    Toast.makeText(ctx, "AI 分析失败: ${resp.error ?: "未知错误"}", Toast.LENGTH_LONG).show()
                    return@launch
                }
                showAiCoachResult(rep, resp)
            } catch (e: Exception) {
                progress.dismiss()
                if (isAdded) Toast.makeText(ctx, "AI 分析失败: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showAiCoachResult(rep: TrainingRep, resp: AiCoachResponse) {
        val ctx = requireContext()
        val scroll = ScrollView(ctx)
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 14), UiKit.dp(ctx, 12), UiKit.dp(ctx, 14), UiKit.dp(ctx, 12))
        }
        scroll.addView(root)

        val fu = resp.framesUsed
        val obs = resp.observation
        val an = resp.analysis

        // Header: rep + provider + frames
        val header = StringBuilder("第${rep.repIndex ?: "?"}次 · ${rep.exercise ?: ""}")
        rep.total?.let { header.append(" · 规则分 ${String.format(Locale.getDefault(), "%.1f", it)}") }
        root.addView(UiKit.body(ctx, header.toString(), 16f))
        val meta = StringBuilder()
        fu?.let { meta.append("采帧: ${it.actual ?: "?"}/${it.requested ?: "?"} 张 · 底部帧=${it.bottomIndex ?: "?"}") }
        obs?.provider?.let { meta.append(" · $it") }
        obs?.model?.let { meta.append(" ($it)") }
        if (meta.isNotEmpty()) root.addView(UiKit.caption(ctx, meta.toString()))

        // Completion score
        an?.completionScore?.let { cs ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "⭐ 完成度评分"))
            val parts = mutableListOf<String>()
            cs.overallScore?.let { parts.add("总分 ${String.format(Locale.getDefault(), "%.1f", it)}") }
            cs.depth?.let { parts.add("深度 ${String.format(Locale.getDefault(), "%.0f", it)}") }
            cs.control?.let { parts.add("控制 ${String.format(Locale.getDefault(), "%.0f", it)}") }
            cs.symmetry?.let { parts.add("对称 ${String.format(Locale.getDefault(), "%.0f", it)}") }
            if (parts.isNotEmpty()) root.addView(UiKit.body(ctx, parts.joinToString(" · "), 15f))
            cs.notes?.takeIf { it.isNotBlank() }?.let { root.addView(UiKit.caption(ctx, it)) }
        }

        // Posture summary + issues
        an?.postureAssessment?.let { pa ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "🔍 姿态评估"))
            pa.summary?.takeIf { it.isNotBlank() }?.let { root.addView(UiKit.body(ctx, it, 14f)) }
            pa.issues?.forEach { iss ->
                val sev = when (iss.severity?.lowercase()) {
                    "high", "critical" -> "🔴 严重"
                    "medium", "warning" -> "🟡 中等"
                    else -> "🟢 轻微"
                }
                val line = StringBuilder("$sev · ${iss.issue ?: "?"}")
                iss.evidence?.takeIf { it.isNotEmpty() }?.let {
                    line.append("\n  文献: ").append(it.joinToString(", "))
                }
                root.addView(UiKit.caption(ctx, line.toString()).apply {
                    setPadding(0, UiKit.dp(ctx, 4), 0, 0)
                })
            }
            pa.strengths?.takeIf { it.isNotEmpty() }?.let { strs ->
                root.addView(UiKit.caption(ctx, "✓ 优点: ${strs.joinToString(", ")}").apply {
                    setPadding(0, UiKit.dp(ctx, 6), 0, 0)
                })
            }
        }

        // Immediate next rep guidance
        an?.immediateNextRep?.takeIf { it.isNotEmpty() }?.let { list ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "💡 下一次马上可用"))
            list.forEachIndexed { i, g ->
                val line = "• ${g.action ?: g.cue ?: ""}"
                root.addView(UiKit.body(ctx, line, 14f))
                g.target?.takeIf { it.isNotBlank() }?.let {
                    root.addView(UiKit.caption(ctx, "  目标: $it"))
                }
            }
        }

        // Next session
        an?.nextSession?.takeIf { it.isNotEmpty() }?.let { list ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "📅 下次训练建议"))
            list.forEach { g ->
                root.addView(UiKit.body(ctx, "• ${g.action ?: g.cue ?: ""}", 14f))
            }
        }

        // Cautions
        an?.cautions?.takeIf { it.isNotEmpty() }?.let { list ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "⚠️ 需要警惕"))
            list.forEach { root.addView(UiKit.body(ctx, "• $it", 14f)) }
        }

        // Vision observation (collapsed footer)
        obs?.let {
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "👁️ 视觉观察原始信息"))
            it.alignmentCues.takeIf { c -> c.isNotEmpty() }?.let { cues ->
                root.addView(UiKit.caption(ctx, "cues: ${cues.joinToString(", ")}"))
            }
            it.tempo?.let { t -> root.addView(UiKit.caption(ctx, "节奏: $t")) }
            it.cameraAngle?.let { a -> root.addView(UiKit.caption(ctx, "相机角度: $a")) }
            it.confidence?.let { conf -> root.addView(UiKit.caption(ctx, "视觉置信: ${String.format(Locale.getDefault(), "%.2f", conf)}")) }
        }

        // Stage 1 conflicts (transparency)
        resp.stage1Conflicts?.takeIf { it.isNotEmpty() }?.let { conflicts ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "🛡️ 已自动拦截的幻觉"))
            conflicts.forEach { conflict ->
                val text = conflict["note"]?.toString() ?: conflict["cue"]?.toString() ?: "已拦截"
                root.addView(UiKit.caption(ctx, "• $text"))
            }
        }

        resp.note?.takeIf { it.isNotBlank() }?.let {
            root.addView(spacer(ctx))
            root.addView(UiKit.caption(ctx, it))
        }

        AlertDialog.Builder(ctx)
            .setTitle("AI 教练分析")
            .setView(scroll)
            .setPositiveButton("关闭", null)
            .show()
    }

    // ===============================================================
    // 组级(整组) AI 教练 - 逐组视觉分析 + LLM 综合报告
    // ===============================================================

    private fun showSessionAiCoachPrecisionPicker(session: TrainingDataSession) {
        val ctx = requireContext()
        val sid = session.sessionId
        if (sid.isNullOrBlank()) {
            Toast.makeText(ctx, "该训练记录无 session_id，无法分析", Toast.LENGTH_SHORT).show()
            return
        }
        val options = arrayOf(
            "3 帧/组 - 快速 (~5s/组, 适合预览)",
            "5 帧/组 - 推荐 (~8s/组, 精度与速度均衡)",
            "7 帧/组 - 高精度 (~12s/组, 深入分析)",
            "9 帧/组 - 最高精度 (~15s/组, 专业级)"
        )
        val values = intArrayOf(3, 5, 7, 9)
        var selectedIdx = 1  // default 5 帧
        val estFor = { i: Int -> (values[i] * 3 + 3) * (session.repCount ?: session.reps.size) }
        AlertDialog.Builder(ctx)
            .setTitle("🚀 AI 分析 · 共 ${session.repCount ?: session.reps.size} 组")
            .setSingleChoiceItems(options, selectedIdx) { _, which ->
                selectedIdx = which
            }
            .setPositiveButton("开始分析") { d, _ ->
                d.dismiss()
                runSessionAiCoach(session, values[selectedIdx])
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun runSessionAiCoach(session: TrainingDataSession, framesPerRep: Int) {
        val sid = session.sessionId ?: return
        val ctx = requireContext()
        val repCount = session.repCount ?: session.reps.size
        val estSec = (framesPerRep * 3 + 3) * repCount.coerceAtLeast(1)
        val progress = AlertDialog.Builder(ctx)
            .setTitle("🧠 组级 AI 教练分析中")
            .setMessage("正在逐组分析 $repCount 组动作 (每组 $framesPerRep 帧)\n每个阶段均校验幻觉与规则冲突\n预计 $estSec 秒, 请稍候")
            .setCancelable(false)
            .create()
        progress.show()
        lifecycleScope.launch {
            try {
                val resp = withContext(Dispatchers.IO) {
                    ApiClient.service.trainingSessionAiCoach(
                        sid,
                        com.smartfitness.app.model.AiCoachRequest(frames = framesPerRep)
                    )
                }
                progress.dismiss()
                if (!isAdded) return@launch
                if (!resp.ok) {
                    Toast.makeText(ctx, "组级分析失败: ${resp.error ?: "未知错误"}", Toast.LENGTH_LONG).show()
                    return@launch
                }
                showSessionAiCoachResult(session, resp)
            } catch (e: Exception) {
                progress.dismiss()
                if (isAdded) Toast.makeText(ctx, "组级分析失败: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showSessionAiCoachResult(session: TrainingDataSession, resp: SessionAiCoachResponse) {
        val ctx = requireContext()
        val scroll = ScrollView(ctx)
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 14), UiKit.dp(ctx, 12), UiKit.dp(ctx, 14), UiKit.dp(ctx, 12))
        }
        scroll.addView(root)

        val oa = resp.overallAssessment
        val repNotes = resp.repByRepNotes
        val guidance = resp.guidance

        // Header: session info + confidence + stage1 status
        val header = StringBuilder("${resp.exercise ?: session.exerciseType ?: "训练"} · ")
        header.append("${resp.repsCount ?: resp.repCount ?: session.repCount ?: "?"} 组")
        header.append(" · 置信度 ${String.format(Locale.getDefault(), "%.0f", (resp.confidence ?: 0.0) * 100)}%")
        root.addView(UiKit.body(ctx, header.toString(), 16f).apply {
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        val fmt = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())
        root.addView(UiKit.caption(ctx,
            "${fmt.format(Date((session.startTime * 1000).toLong()))}" +
            " · Stage1: ${resp.stage1OkCount ?: 0}/${resp.stage1Total ?: 0} 组通过"))

        // ---------- 综合评分 ----------
        oa?.overallScore?.let { score ->
            val scoreInt = score.toInt().coerceIn(0, 100)
            val rating = oa.performanceRating ?: ""
            val scoreColor = when {
                scoreInt >= 80 -> "🟢"
                scoreInt >= 60 -> "🟡"
                else -> "🔴"
            }
            root.addView(spacer(ctx))
            root.addView(UiKit.body(ctx,
                "综合评分: $scoreColor $scoreInt" + (if (rating.isNotEmpty()) " ($rating)" else ""),
                24f).apply { setTypeface(typeface, android.graphics.Typeface.BOLD) })
        }

        // ---------- 优点 ----------
        oa?.strengths?.takeIf { it.isNotEmpty() }?.let { strengths ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "✅ 优点"))
            strengths.forEach { s ->
                root.addView(UiKit.body(ctx, "• $s", 14f).apply {
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 2), 0, UiKit.dp(ctx, 2))
                })
            }
        }

        // ---------- 需改进 ----------
        oa?.commonIssues?.takeIf { it.isNotEmpty() }?.let { issues ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "🎯 需改进"))
            issues.forEach { iss ->
                val sevEmoji = when (iss.severity) {
                    "critical" -> "🔴"
                    "major" -> "🟠"
                    else -> "🟡"
                }
                val affected = iss.affectedReps?.let {
                    " (第 ${it.joinToString(",")} 组)"
                } ?: ""
                root.addView(UiKit.body(ctx, "$sevEmoji ${iss.issue}$affected", 14f).apply {
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 3), 0, UiKit.dp(ctx, 1))
                })
                iss.evidence?.takeIf { it.isNotBlank() }?.let { ev ->
                    root.addView(UiKit.caption(ctx, ev).apply {
                        setPadding(UiKit.dp(ctx, 12), 0, 0, UiKit.dp(ctx, 2))
                    })
                }
            }
        }

        // ---------- 不一致 ----------
        oa?.inconsistencies?.takeIf { it.isNotEmpty() }?.let { incon ->
            root.addView(spacer(ctx))
            root.addView(sectionHeader(ctx, "⚠️ 不一致"))
            incon.forEach { i ->
                root.addView(UiKit.body(ctx, "• $i", 14f).apply {
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 2), 0, UiKit.dp(ctx, 2))
                })
            }
        }

        // ---------- 逐组备注 ----------
        repNotes?.takeIf { it.isNotEmpty() }?.let { notes ->
            root.addView(spacer(ctx))
            divider(ctx, root)
            root.addView(sectionHeader(ctx, "📋 逐组分析"))
            notes.forEachIndexed { idx, note ->
                root.addView(UiKit.body(ctx, "${idx + 1}. $note", 14f).apply {
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 4), 0, UiKit.dp(ctx, 2))
                })
            }
        }

        // ---------- 指导意见 ----------
        guidance?.let { g ->
            root.addView(spacer(ctx))
            divider(ctx, root)
            root.addView(sectionHeader(ctx, "💡 指导意见"))

            g.immediateCorrections?.takeIf { it.isNotEmpty() }?.let { corrections ->
                root.addView(UiKit.body(ctx, "立即纠正", 14f).apply {
                    setTypeface(typeface, android.graphics.Typeface.BOLD)
                    setTextColor(0xFFE67E22.toInt())
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 6), 0, UiKit.dp(ctx, 2))
                })
                corrections.forEach { c ->
                    root.addView(UiKit.body(ctx, "• $c", 14f).apply {
                        setPadding(UiKit.dp(ctx, 12), UiKit.dp(ctx, 2), 0, UiKit.dp(ctx, 2))
                    })
                }
            }

            g.nextSessionFocus?.takeIf { it.isNotEmpty() }?.let { focus ->
                root.addView(UiKit.body(ctx, "下次关注点", 14f).apply {
                    setTypeface(typeface, android.graphics.Typeface.BOLD)
                    setTextColor(0xFF3498DB.toInt())
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 6), 0, UiKit.dp(ctx, 2))
                })
                focus.forEach { f ->
                    root.addView(UiKit.body(ctx, "• $f", 14f).apply {
                        setPadding(UiKit.dp(ctx, 12), UiKit.dp(ctx, 2), 0, UiKit.dp(ctx, 2))
                    })
                }
            }

            g.progressionOrRegression?.let { prog ->
                root.addView(UiKit.body(ctx, "📊 $prog", 14f).apply {
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 6), 0, UiKit.dp(ctx, 2))
                })
            }

            g.cautions?.takeIf { it.isNotEmpty() }?.let { cautions ->
                cautions.forEach { caution ->
                    root.addView(UiKit.body(ctx, "⚠️ $caution", 13f).apply {
                        setTextColor(0xFFE67E22.toInt())
                        setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 2), 0, UiKit.dp(ctx, 2))
                    })
                }
            }
        }

        // ---------- 数据缺口 ----------
        resp.dataGaps?.takeIf { it.isNotEmpty() }?.let { gaps ->
            root.addView(spacer(ctx))
            root.addView(UiKit.caption(ctx, "📎 数据说明: ${gaps.joinToString("; ")}"))
        }

        // Stage1 详情 (折叠式)
        resp.stage1Results?.takeIf { it.isNotEmpty() }?.let { results ->
            root.addView(spacer(ctx))
            divider(ctx, root)
            root.addView(sectionHeader(ctx, "🔬 各阶段视觉诊断明细"))
            results.forEach { r ->
                val status = if (r.ok == true) "✅" else "❌"
                val info = StringBuilder("第${r.repIndex ?: "?"}组 $status")
                r.provider?.let { info.append(" · $it") }
                r.model?.let { info.append(" ($it)") }
                root.addView(UiKit.caption(ctx, info.toString()).apply {
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 3), 0, 0)
                })
                r.error?.let { err ->
                    root.addView(UiKit.caption(ctx, "  ↪ $err").apply {
                        setPadding(UiKit.dp(ctx, 12), 0, 0, UiKit.dp(ctx, 2))
                        setTextColor(0xFFEF4444.toInt())
                    })
                }
            }
        }

        resp.note?.takeIf { it.isNotBlank() }?.let {
            root.addView(spacer(ctx))
            root.addView(UiKit.caption(ctx, it))
        }

        val savedTag = if (resp.saved == true && !resp.reportId.isNullOrBlank()) {
            "✅ 已自动保存到报告档案 · report_id=${resp.reportId?.takeLast(12)}"
        } else if (resp.saved == false) {
            "⚠️ 本次报告未保存 (服务器存储异常)"
        } else null
        savedTag?.let {
            root.addView(spacer(ctx))
            root.addView(UiKit.caption(ctx, it).apply { setTextColor(0xFF4CAF50.toInt()) })
        }

        val builder = AlertDialog.Builder(ctx)
            .setTitle("🧠 组级 AI 教练分析")
            .setView(scroll)
            .setPositiveButton("关闭", null)
        if (resp.saved == true && !resp.reportId.isNullOrBlank()) {
            builder.setNeutralButton("添加备注") { _, _ ->
                showReportNoteEditor(resp.reportId!!, null)
            }
        }
        builder.show()
    }

    private fun showReportNoteEditor(reportId: String, existing: String?) {
        val ctx = requireContext()
        val et = android.widget.EditText(ctx).apply {
            hint = "例: 周末重点回顧 / 需告知教练"
            setText(existing ?: "")
            setSingleLine(false)
        }
        AlertDialog.Builder(ctx)
            .setTitle("添加备注")
            .setView(et)
            .setPositiveButton("保存") { _, _ ->
                val note = et.text?.toString()?.trim().orEmpty()
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) {
                            ApiClient.service.aiCoachReportUpdate(reportId,
                                com.smartfitness.app.model.AiCoachReportNoteUpdate(note = note))
                        }
                        Toast.makeText(ctx, "已保存备注", Toast.LENGTH_SHORT).show()
                    } catch (e: Exception) {
                        Toast.makeText(ctx, "保存失败: ${e.message}", Toast.LENGTH_LONG).show()
                    }
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun showSessionReports(session: TrainingDataSession) {
        val ctx = requireContext()
        val sid = session.sessionId
        if (sid.isNullOrBlank()) {
            Toast.makeText(ctx, "该训练记录无 session_id", Toast.LENGTH_SHORT).show()
            return
        }
        val progress = AlertDialog.Builder(ctx).setTitle("加载报告档案...").setCancelable(false).create()
        progress.show()
        lifecycleScope.launch {
            try {
                val list = withContext(Dispatchers.IO) {
                    ApiClient.service.aiCoachReports(sessionId = sid, limit = 50)
                }
                progress.dismiss()
                if (list.reports.isEmpty()) {
                    Toast.makeText(ctx, "这个训练还没有 AI 分析报告", Toast.LENGTH_SHORT).show()
                    return@launch
                }
                showReportListDialog(list.reports, session)
            } catch (e: Exception) {
                progress.dismiss()
                Toast.makeText(ctx, "抠告列表加载失败: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun showReportListDialog(reports: List<com.smartfitness.app.model.AiCoachReportSummary>, session: TrainingDataSession?) {
        val ctx = requireContext()
        val fmt = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())
        val items = reports.map { r ->
            val ts = r.createdAt?.let { fmt.format(Date((it * 1000).toLong())) } ?: "?"
            val score = r.overallScore?.let { String.format(Locale.getDefault(), "%.0f", it) } ?: "--"
            val rating = r.performanceRating ?: "?"
            "$ts · 评分 $score ($rating) · ${r.framesPerRep ?: 1}帧/组" +
                (r.note?.takeIf { it.isNotBlank() }?.let { "\n备注: $it" } ?: "")
        }.toTypedArray()
        AlertDialog.Builder(ctx)
            .setTitle("📂 AI 报告档案 · ${reports.size} 份")
            .setItems(items) { _, which ->
                val rid = reports[which].reportId
                loadAndShowReport(rid, session)
            }
            .setNegativeButton("关闭", null)
            .show()
    }

    private fun loadAndShowReport(reportId: String, session: TrainingDataSession?) {
        val ctx = requireContext()
        val progress = AlertDialog.Builder(ctx).setTitle("加载报告...").setCancelable(false).create()
        progress.show()
        lifecycleScope.launch {
            try {
                val det = withContext(Dispatchers.IO) { ApiClient.service.aiCoachReport(reportId) }
                progress.dismiss()
                val rep = det.report
                if (rep == null) {
                    Toast.makeText(ctx, "报告内容为空", Toast.LENGTH_SHORT).show()
                    return@launch
                }
                // Fake a session for display if not provided
                val sess = session ?: TrainingDataSession(
                    sessionId = det.sessionId ?: "",
                    exerciseType = det.exercise,
                    startTime = det.createdAt ?: 0.0,
                    totalReps = det.repCount ?: 0,
                    repCount = det.repCount ?: 0,
                    avgFormScore = det.overallScore,
                    reps = emptyList()
                )
                showSessionAiCoachResult(sess, rep)
            } catch (e: Exception) {
                progress.dismiss()
                Toast.makeText(ctx, "报告加载失败: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun divider(ctx: android.content.Context, parent: LinearLayout) {
        parent.addView(View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 1)
            ).apply { bottomMargin = UiKit.dp(ctx, 4) }
            setBackgroundColor(0xFFE8E8E8.toInt())
        })
    }

    private fun spacer(ctx: android.content.Context): View {
        return View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 10))
        }
    }

    private fun sectionHeader(ctx: android.content.Context, text: String): TextView {
        return UiKit.body(ctx, text, 15f).apply {
            setPadding(0, UiKit.dp(ctx, 4), 0, UiKit.dp(ctx, 4))
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }
    }
}
