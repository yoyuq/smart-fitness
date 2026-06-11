package com.smartfitness.app.ui.profile

import android.app.AlertDialog
import android.os.Bundle
import android.text.InputType
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.navigation.NavOptions
import androidx.navigation.fragment.findNavController
import com.google.android.material.button.MaterialButton
import com.smartfitness.app.R
import com.smartfitness.app.ui.UiKit
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.model.BindDeviceRequest
import com.smartfitness.app.model.BodyMetricRequest
import com.smartfitness.app.model.DeviceRegisterRequest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers
import java.text.SimpleDateFormat
import java.util.*

class ProfileFragment : Fragment() {

    private lateinit var usernameView: TextView
    private lateinit var createdAtView: TextView
    private lateinit var bodyMetricView: TextView
    private lateinit var devicesContainer: LinearLayout
    private lateinit var bindingsContainer: LinearLayout
    private lateinit var goalsView: TextView
    private lateinit var achievementsView: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        val ctx = inflater.context
        val scroll = android.widget.ScrollView(ctx).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        }
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 48, 48, 48)
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }
        scroll.addView(root)
        scroll.setBackgroundColor(ctx.getColor(R.color.bg))
        with(root) {

            usernameView = TextView(ctx).apply {
                textSize = 24f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.on_surface))
            }.also { addView(it) }
            createdAtView = TextView(ctx).apply {
                textSize = 13f
                setTextColor(ctx.getColor(R.color.on_surface_secondary))
                setPadding(0, 4, 0, UiKit.dp(ctx, 16))
            }.also { addView(it) }

            // 身体指标卡片
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "📏 身体指标"))
                bodyMetricView = UiKit.body(ctx, "(未记录)").also { inner.addView(it) }
                inner.addView(MaterialButton(ctx).apply {
                    text = "记录体重 / 身高"
                    cornerRadius = UiKit.dp(ctx, 12)
                    setOnClickListener { showBodyMetricDialog() }
                })
                addView(cardView)
            }

            // 设备卡片 (手机注册 + ESP32 绑定)
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "📱 " + getString(R.string.devices)))
                devicesContainer = LinearLayout(ctx).apply {
                    orientation = LinearLayout.VERTICAL
                }.also { inner.addView(it) }
                inner.addView(MaterialButton(ctx).apply {
                    text = getString(R.string.register_device)
                    cornerRadius = UiKit.dp(ctx, 12)
                    setOnClickListener { registerDevice() }
                })
                inner.addView(UiKit.caption(ctx, "ESP32 绑定").apply {
                    setPadding(0, UiKit.dp(ctx, 8), 0, 0)
                })
                bindingsContainer = LinearLayout(ctx).apply {
                    orientation = LinearLayout.VERTICAL
                }.also { inner.addView(it) }
                inner.addView(MaterialButton(ctx).apply {
                    text = "绑定 ESP32 设备"
                    cornerRadius = UiKit.dp(ctx, 12)
                    setOnClickListener { showBindDialog() }
                })
                addView(cardView)
            }

            // 目标卡片
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "🎯 我的目标"))
                goalsView = UiKit.body(ctx, "(未设置)").also { inner.addView(it) }
                inner.addView(MaterialButton(ctx).apply {
                    text = "设置目标"
                    cornerRadius = UiKit.dp(ctx, 12)
                    setOnClickListener { showGoalsDialog() }
                })
                addView(cardView)
            }

            // 成就卡片
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "🏆 成就"))
                achievementsView = UiKit.body(ctx, "加载中...", 14f).also { inner.addView(it) }
                addView(cardView)
            }

            // AI 私人教练卡片 (核心卖点)
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "🤖 AI 私人教练"))
                inner.addView(UiKit.caption(ctx, "你的专属健身伙伴 · 懂你的数据和身体"))
                inner.addView(MaterialButton(ctx).apply {
                    text = "🧠 教练复盘 (分析我的数据)"
                    cornerRadius = UiKit.dp(ctx, 12)
                    setOnClickListener { showCoachReview() }
                })
                inner.addView(MaterialButton(ctx).apply {
                    text = "💬 告诉教练 (伤病/目标/偏好)"
                    cornerRadius = UiKit.dp(ctx, 12)
                    setOnClickListener { showAddMemoryDialog() }
                })
                addView(cardView)
            }

            // 数据与账号卡片
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "⚙️ 数据与账号"))
                inner.addView(MaterialButton(ctx).apply {
                    text = "📊 导出我的数据 (CSV)"
                    cornerRadius = UiKit.dp(ctx, 12)
                    setOnClickListener { exportCsv() }
                })
                inner.addView(MaterialButton(ctx).apply {
                    text = "服务器地址 (高级)"
                    cornerRadius = UiKit.dp(ctx, 12)
                    setOnClickListener { showBaseUrlDialog() }
                })
                inner.addView(MaterialButton(ctx).apply {
                    text = getString(R.string.logout)
                    cornerRadius = UiKit.dp(ctx, 12)
                    setBackgroundColor(ctx.getColor(R.color.error))
                    setOnClickListener {
                        ApiClient.clearAuth()
                        findNavController().navigate(R.id.loginFragment, null, NavOptions.Builder().setPopUpTo(R.id.loginFragment, true).build())
                    }
                })
                addView(cardView)
            }
        }
        return scroll
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        loadProfile()
        loadDevices()
        loadBodyMetric()
        loadBindings()
        loadGoals()
        loadAchievements()
    }

    // ---------- AI 私人教练管家 ----------

    private fun showCoachReview() {
        val loading = AlertDialog.Builder(requireContext())
            .setTitle("🧠 AI 教练复盘")
            .setMessage("教练正在分析你的训练数据…(约 30 秒)")
            .setCancelable(true)
            .show()
        lifecycleScope.launch {
            try {
                val r = withContext(Dispatchers.IO) { ApiClient.service.coachReview() }
                if (!isAdded) return@launch
                loading.dismiss()
                if (!r.ok) {
                    Toast.makeText(requireContext(), "复盘失败: ${r.error ?: "AI 暂不可用"}", Toast.LENGTH_LONG).show()
                    return@launch
                }
                showReviewSheet(r.review, r.reviewText)
            } catch (e: Exception) {
                if (isAdded) {
                    loading.dismiss()
                    Toast.makeText(requireContext(), "复盘失败: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    /** 复盘结果 BottomSheet (设计规范: 24dp 圆角, 图标+标题+正文三段式, 激励句强调色) */
    private fun showReviewSheet(rv: com.smartfitness.app.model.CoachReview?, fallback: String?) {
        val ctx = requireContext()
        val sheet = com.google.android.material.bottomsheet.BottomSheetDialog(ctx)
        val scroll = android.widget.ScrollView(ctx)
        val box = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(ctx.getColor(R.color.surface))
            setPadding(UiKit.dp(ctx, 24), UiKit.dp(ctx, 20), UiKit.dp(ctx, 24), UiKit.dp(ctx, 24))
        }
        scroll.addView(box)

        box.addView(TextView(ctx).apply {
            text = "🧠 AI 教练复盘"
            textSize = 22f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
        })
        box.addView(UiKit.caption(ctx, "基于你的真实训练数据生成").apply {
            setPadding(0, 4, 0, UiKit.dp(ctx, 8))
        })

        fun section(title: String, bodyText: String?) {
            if (bodyText.isNullOrBlank()) return
            box.addView(TextView(ctx).apply {
                text = title
                textSize = 16f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.on_surface))
                setPadding(0, UiKit.dp(ctx, 10), 0, 2)
            })
            box.addView(UiKit.body(ctx, bodyText))
        }

        if (rv != null) {
            section("📈 趋势", rv.trend)
            section("⚖️ 动作平衡", rv.balance)
            section("🎯 弱点", rv.weakness)
            section("📋 计划执行", rv.adherence)
            rv.nextWeek?.takeIf { it.isNotEmpty() }?.let { nw ->
                section("🗓 下周建议", nw.joinToString("\n") { "• $it" })
            }
            rv.encouragement?.let {
                box.addView(TextView(ctx).apply {
                    text = "🔥 $it"
                    textSize = 15f
                    setTypeface(typeface, android.graphics.Typeface.BOLD)
                    setTextColor(ctx.getColor(R.color.warning))
                    setPadding(0, UiKit.dp(ctx, 12), 0, 0)
                })
            }
        } else {
            box.addView(UiKit.body(ctx, fallback ?: "(无内容)"))
        }

        box.addView(MaterialButton(ctx).apply {
            text = "收到 💪"
            cornerRadius = UiKit.dp(ctx, 12)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = UiKit.dp(ctx, 16) }
            setOnClickListener { sheet.dismiss() }
        })

        sheet.setContentView(scroll)
        sheet.show()
    }

    private fun showAddMemoryDialog() {
        val input = EditText(requireContext()).apply {
            hint = "如: 膝盖有旧伤 / 目标3个月减5kg"
            inputType = InputType.TYPE_CLASS_TEXT
        }
        AlertDialog.Builder(requireContext())
            .setTitle("💬 告诉教练")
            .setMessage("教练会长期记住这条信息, 在复盘/对话/排计划时考虑它")
            .setView(input)
            .setPositiveButton("保存") { _, _ ->
                val note = input.text.toString().trim()
                if (note.isEmpty()) return@setPositiveButton
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) {
                            ApiClient.service.addCoachMemory(com.smartfitness.app.model.CoachMemoryAddRequest(note))
                        }
                        if (isAdded) Toast.makeText(requireContext(), "教练记住了 ✅", Toast.LENGTH_SHORT).show()
                    } catch (e: Exception) {
                        if (isAdded) Toast.makeText(requireContext(), "保存失败: ${e.message}", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNeutralButton("查看记忆") { _, _ -> showMemories() }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun showMemories() {
        lifecycleScope.launch {
            try {
                val r = withContext(Dispatchers.IO) { ApiClient.service.coachMemories() }
                if (!isAdded) return@launch
                val text = if (r.memories.isEmpty()) "(教练还没有记忆)"
                else r.memories.joinToString("\n\n") { "• [${it.category ?: "general"}] ${it.note}" }
                AlertDialog.Builder(requireContext())
                    .setTitle("教练的记忆")
                    .setMessage(text)
                    .setPositiveButton("OK", null)
                    .show()
            } catch (e: Exception) {
                if (isAdded) Toast.makeText(requireContext(), "加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun formatTimestamp(ts: Double?): String {
        if (ts == null) return ""
        val sdf = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())
        return sdf.format(Date((ts * 1000).toLong()))
    }

    private fun loadProfile() {
        lifecycleScope.launch {
            try {
                val p = ApiClient.service.profile()
                p.user?.let {
                    usernameView.text = it.username
                    createdAtView.text = "Joined: ${formatTimestamp(it.createdAt)}"
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Profile load failed: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun loadDevices() {
        lifecycleScope.launch {
            try {
                val list = ApiClient.service.listDevices().devices
                devicesContainer.removeAllViews()
                if (list.isEmpty()) {
                    devicesContainer.addView(TextView(requireContext()).apply {
                        text = getString(R.string.no_devices)
                    })
                } else {
                    list.forEach { d ->
                        devicesContainer.addView(TextView(requireContext()).apply {
                            text = "• ${d.deviceName ?: "Unnamed"} (${d.deviceType ?: "?"})"
                            setPadding(0, 12, 0, 12)
                        })
                    }
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Devices load failed: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun registerDevice() {
        lifecycleScope.launch {
            try {
                val name = "Phone-${(1000..9999).random()}"
                val r = ApiClient.service.registerDevice(
                    DeviceRegisterRequest(deviceName = name, deviceType = "phone")
                )
                if (r.ok) {
                    Toast.makeText(requireContext(), "Registered: ${r.deviceId}", Toast.LENGTH_SHORT).show()
                    loadDevices()
                } else {
                    Toast.makeText(requireContext(), r.message ?: "Register failed", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    // E-04: 加载最近体重 / BMI
    private fun loadBodyMetric() {
        lifecycleScope.launch {
            try {
                val r = ApiClient.service.latestBodyMetric()
                val m = r.latest
                bodyMetricView.text = if (m == null) {
                    "(未记录)"
                } else {
                    "体重 ${m.weightKg ?: "-"}kg / 身高 ${m.heightCm ?: "-"}cm / BMI ${m.bmi ?: "-"}"
                }
            } catch (_: Exception) {}
        }
    }

    // E-04: 弹窗输入体重 / 身高 以后调 D-03 endpoint
    private fun showBodyMetricDialog() {
        val ctx = requireContext()
        val container = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 32, 48, 32)
        }
        val weightEt = EditText(ctx).apply {
            hint = "体重 (kg)"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        }
        val heightEt = EditText(ctx).apply {
            hint = "身高 (cm)"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        }
        container.addView(weightEt)
        container.addView(heightEt)
        AlertDialog.Builder(ctx)
            .setTitle("记录身体指标")
            .setView(container)
            .setPositiveButton("保存") { _, _ ->
                val w = weightEt.text.toString().toDoubleOrNull()
                val h = heightEt.text.toString().toDoubleOrNull()
                if (w == null && h == null) {
                    Toast.makeText(ctx, "请填写至少一项", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    try {
                        ApiClient.service.addBodyMetric(BodyMetricRequest(weightKg = w, heightCm = h))
                        Toast.makeText(ctx, "已保存", Toast.LENGTH_SHORT).show()
                        loadBodyMetric()
                    } catch (e: Exception) {
                        Toast.makeText(ctx, "保存失败: ${e.message}", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    // E-07: ESP32 绑定
    private fun loadBindings() {
        lifecycleScope.launch {
            try {
                val r = ApiClient.service.listBindings()
                bindingsContainer.removeAllViews()
                if (r.bindings.isEmpty()) {
                    bindingsContainer.addView(TextView(requireContext()).apply {
                        text = "暂未绑定设备"
                    })
                } else {
                    r.bindings.forEach { b ->
                        bindingsContainer.addView(TextView(requireContext()).apply {
                            text = "• ${b.deviceId} (激活)"
                            setPadding(0, 12, 0, 12)
                        })
                    }
                }
            } catch (_: Exception) {}
        }
    }

    private fun showBindDialog() {
        val ctx = requireContext()
        val et = EditText(ctx).apply {
            hint = "设备 ID (例: esp32-001)"
        }
        AlertDialog.Builder(ctx)
            .setTitle("绑定 ESP32 设备")
            .setView(et)
            .setPositiveButton("绑定") { _, _ ->
                val id = et.text.toString().trim()
                if (id.isEmpty()) {
                    Toast.makeText(ctx, "设备 ID 不能为空", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    try {
                        val r = ApiClient.service.bindDevice(BindDeviceRequest(deviceId = id))
                        if (r.ok && r.token != null) {
                            AlertDialog.Builder(ctx)
                                .setTitle("绑定成功")
                                .setMessage("设备 token (复制到 ESP32 固件):\n\n${r.token}")
                                .setPositiveButton("好", null)
                                .show()
                            loadBindings()
                        } else {
                            Toast.makeText(ctx, r.message ?: "绑定失败", Toast.LENGTH_SHORT).show()
                        }
                    } catch (e: Exception) {
                        Toast.makeText(ctx, "错误: ${e.message}", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun showBaseUrlDialog() {
        val ctx = requireContext()
        val container = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 32, 48, 32)
        }
        container.addView(TextView(ctx).apply {
            text = "当前: ${ApiClient.BASE_URL}\n\n留空使用默认 (模拟器→ 10.0.2.2; 真机→ ${ApiClient.DEFAULT_BASE_URL_REAL})"
            textSize = 12f
        })
        val et = EditText(ctx).apply {
            hint = "例: http://192.168.1.100:8080"
            setText(requireContext().getSharedPreferences("smart_fitness_prefs", 0).getString("base_url", ""))
        }
        container.addView(et)
        AlertDialog.Builder(ctx)
            .setTitle("服务器地址")
            .setView(container)
            .setPositiveButton("保存并重启") { _, _ ->
                ApiClient.setBaseUrl(et.text.toString().trim().ifEmpty { null })
                Toast.makeText(ctx, "已保存，三秒后重启 app", Toast.LENGTH_LONG).show()
                view?.postDelayed({
                    activity?.finishAffinity()
                    android.os.Process.killProcess(android.os.Process.myPid())
                }, 3000)
            }
            .setNeutralButton("恢复默认") { _, _ ->
                ApiClient.setBaseUrl(null)
                Toast.makeText(ctx, "已恢复，三秒后重启", Toast.LENGTH_LONG).show()
                view?.postDelayed({
                    activity?.finishAffinity()
                    android.os.Process.killProcess(android.os.Process.myPid())
                }, 3000)
            }
            .setNegativeButton("取消", null)
            .show()
    }

    // ---------- Task 2: 个人目标 (存 SharedPrefs) ----------
    private fun prefs() = requireContext().getSharedPreferences("sf_goals", android.content.Context.MODE_PRIVATE)

    private fun loadGoals() {
        val p = prefs()
        val tw = p.getString("target_weight", null)
        val ww = p.getInt("weekly_workouts", 0)
        val dr = p.getInt("daily_reps", 0)
        if (tw == null && ww == 0 && dr == 0) {
            goalsView.text = "(未设置)"
        } else {
            val parts = mutableListOf<String>()
            if (tw != null) parts += "目标体重 ${tw}kg"
            if (ww > 0) parts += "周训练 ${ww} 次"
            if (dr > 0) parts += "每日 ${dr} reps"
            goalsView.text = parts.joinToString(" · ")
        }
    }

    private fun showGoalsDialog() {
        val ctx = requireContext()
        val container = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 32, 48, 32)
        }
        val twEt = EditText(ctx).apply {
            hint = "目标体重 (kg, 可空)"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            setText(prefs().getString("target_weight", ""))
        }
        val wwEt = EditText(ctx).apply {
            hint = "每周训练次数 (可空)"
            inputType = InputType.TYPE_CLASS_NUMBER
            val v = prefs().getInt("weekly_workouts", 0)
            if (v > 0) setText(v.toString())
        }
        val drEt = EditText(ctx).apply {
            hint = "每日 reps 目标 (可空)"
            inputType = InputType.TYPE_CLASS_NUMBER
            val v = prefs().getInt("daily_reps", 0)
            if (v > 0) setText(v.toString())
        }
        container.addView(twEt)
        container.addView(wwEt)
        container.addView(drEt)
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
                loadAchievements()
            }
            .setNegativeButton("取消", null)
            .show()
    }

    // ---------- Task 2: 成就 (基于 stats + goals 计算) ----------
    private fun loadAchievements() {
        lifecycleScope.launch {
            try {
                val daily = ApiClient.service.statsDaily()
                val weekly = try { ApiClient.service.statsWeekly() } catch (_: Exception) { null }
                val s7 = try { ApiClient.service.exerciseSummary(7) } catch (_: Exception) { null }
                val s30 = try { ApiClient.service.exerciseSummary(30) } catch (_: Exception) { null }

                val total30Reps = s30?.byType?.sumOf { it.totalReps } ?: 0
                val total30Sessions = s30?.byType?.sumOf { it.sessions } ?: 0
                val avgScore30 = s30?.byType?.mapNotNull { it.avgForm }?.average()?.takeIf { !it.isNaN() } ?: 0.0
                val total7Reps = s7?.byType?.sumOf { it.totalReps } ?: 0
                val total7Sessions = s7?.byType?.sumOf { it.sessions } ?: 0

                val list = mutableListOf<String>()
                // 基本里程碑 (总 reps)
                listOf(10, 50, 100, 500, 1000, 5000).forEach { milestone ->
                    if (total30Reps >= milestone) list += "✅ 近 30 日累计 $milestone reps"
                }
                // 近 7 日加不带二创
                if (total7Sessions >= 3) list += "✅ 本周已训练 $total7Sessions 次"
                if (total7Sessions >= 5) list += "✅ 本周 5+ 次劤奋鬼"
                if (total7Sessions >= 7) list += "✨ 连续一周不断"
                // 动作质量
                if (avgScore30 >= 80) list += "✅ 30 日平均得分 $${String.format("%.0f", avgScore30)} (优秀)"
                else if (avgScore30 >= 60) list += "✅ 30 日平均得分 ${String.format("%.0f", avgScore30)} (及格)"
                // 目标进度
                val p = prefs()
                val ww = p.getInt("weekly_workouts", 0)
                if (ww > 0) {
                    val pct = (total7Sessions * 100 / ww).coerceAtMost(999)
                    val mark = if (total7Sessions >= ww) "✅" else "⏳"
                    list += "$mark 周训练目标 $total7Sessions/$ww ($pct%)"
                }
                val dr = p.getInt("daily_reps", 0)
                if (dr > 0) {
                    val todayReps = daily.stats?.totalReps ?: 0
                    val pct = (todayReps * 100 / dr).coerceAtMost(999)
                    val mark = if (todayReps >= dr) "✅" else "⏳"
                    list += "$mark 今日 reps 目标 $todayReps/$dr ($pct%)"
                }
                val tw = p.getString("target_weight", null)?.toDoubleOrNull()
                if (tw != null) {
                    try {
                        val latest = ApiClient.service.latestBodyMetric().latest
                        val cur = latest?.weightKg
                        if (cur != null) {
                            val diff = cur - tw
                            list += if (kotlin.math.abs(diff) < 0.5) "✅ 体重达标 (差距 ${String.format("%.1f", diff)}kg)" 
                                    else "⏳ 距标 ${String.format("%+.1f", diff)}kg"
                        }
                    } catch (_: Exception) {}
                }

                achievementsView.text = if (list.isEmpty()) "开始训练解锁成就吧！" else list.joinToString("\n")
            } catch (e: Exception) {
                achievementsView.text = "计算成就失败: ${e.message}"
            }
        }
    }

    private fun exportCsv() {
        val ctx = requireContext()
        lifecycleScope.launch {
            try {
                val client = okhttp3.OkHttpClient.Builder()
                    .connectTimeout(10, java.util.concurrent.TimeUnit.SECONDS)
                    .readTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
                    .build()
                val url = ApiClient.BASE_URL.trimEnd('/') + "/api/v2/export/csv?days=365"
                val req = okhttp3.Request.Builder()
                    .url(url)
                    .header("Authorization", "Bearer " + (ApiClient.token ?: ""))
                    .build()
                val resp = withContext(kotlinx.coroutines.Dispatchers.IO) { client.newCall(req).execute() }
                if (!resp.isSuccessful) {
                    Toast.makeText(ctx, "Export failed: HTTP " + resp.code, Toast.LENGTH_LONG).show()
                    return@launch
                }
                val bytes = withContext(kotlinx.coroutines.Dispatchers.IO) { resp.body?.bytes() } ?: ByteArray(0)
                if (bytes.isEmpty()) {
                    Toast.makeText(ctx, "Empty CSV (no data)", Toast.LENGTH_SHORT).show()
                    return@launch
                }
                // 保存到 Downloads
                val name = "workout_" + System.currentTimeMillis() + ".csv"
                val downloads = android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_DOWNLOADS)
                if (!downloads.exists()) downloads.mkdirs()
                val outFile = java.io.File(downloads, name)
                withContext(kotlinx.coroutines.Dispatchers.IO) {
                    outFile.writeBytes(bytes)
                }
                Toast.makeText(ctx, "Saved: Downloads/" + name + "  (" + bytes.size + " bytes)", Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                Toast.makeText(ctx, "Export error: " + e.message, Toast.LENGTH_LONG).show()
            }
        }
    }

}

