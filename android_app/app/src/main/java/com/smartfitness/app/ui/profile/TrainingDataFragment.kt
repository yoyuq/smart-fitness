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
        row.addView(UiKit.outlinedButton(ctx, "查看详细训练图像") { showRepImages(rep) }.apply {
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
}
