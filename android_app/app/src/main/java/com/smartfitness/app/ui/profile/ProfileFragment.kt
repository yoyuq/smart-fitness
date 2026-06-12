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
    private lateinit var avatarView: TextView
    private lateinit var chipsRow: LinearLayout
    private lateinit var weekBarsRow: LinearLayout
    private lateinit var recentContainer: LinearLayout

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

        // 不再上屏的旧容器: 仅为兼容既有 loaders 的 lateinit
        devicesContainer = LinearLayout(ctx)
        bindingsContainer = LinearLayout(ctx)
        goalsView = TextView(ctx)
        achievementsView = TextView(ctx)

        with(root) {
            // ===== 头部: 头像 + 昵称 + 数据胶囊 (Keep 式) =====
            val header = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
                setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 8), 0, UiKit.dp(ctx, 12))
            }
            avatarView = TextView(ctx).apply {
                textSize = 22f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.primary_dark))
                gravity = android.view.Gravity.CENTER
                layoutParams = LinearLayout.LayoutParams(UiKit.dp(ctx, 56), UiKit.dp(ctx, 56))
                background = android.graphics.drawable.GradientDrawable().apply {
                    shape = android.graphics.drawable.GradientDrawable.OVAL
                    setColor(ctx.getColor(R.color.primary_alpha10))
                    setStroke(UiKit.dp(ctx, 2), ctx.getColor(R.color.primary))
                }
            }
            header.addView(avatarView)
            val nameCol = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(UiKit.dp(ctx, 12), 0, 0, 0)
            }
            usernameView = TextView(ctx).apply {
                textSize = 20f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.on_surface))
            }.also { nameCol.addView(it) }
            createdAtView = TextView(ctx).apply {
                textSize = 12f
                setTextColor(ctx.getColor(R.color.on_surface_tertiary))
                setPadding(0, 2, 0, 0)
            }.also { nameCol.addView(it) }
            header.addView(nameCol)
            addView(header)

            // 数据胶囊行: 连续 / 累计 / 成就
            chipsRow = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(UiKit.dp(ctx, 4), 0, 0, UiKit.dp(ctx, 12))
            }.also { addView(it) }

            // ===== AI 私人教练横幅 (Keep 会员位) =====
            val banner = com.google.android.material.card.MaterialCardView(ctx).apply {
                radius = UiKit.dp(ctx, 16).toFloat()
                cardElevation = 0f
                setCardBackgroundColor(ctx.getColor(R.color.on_surface))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                ).apply { bottomMargin = UiKit.dp(ctx, 12) }
            }
            val bannerRow = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
                setPadding(UiKit.dp(ctx, 20), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16))
            }
            val bannerCol = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            }
            bannerCol.addView(TextView(ctx).apply {
                text = "AI 私人教练"
                textSize = 16f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(0xFFFFFFFF.toInt())
            })
            bannerCol.addView(TextView(ctx).apply {
                text = "懂你数据和身体的专属教练"
                textSize = 12f
                setTextColor(0xFFA1A1B5.toInt())
                setPadding(0, 2, 0, 0)
            })
            bannerRow.addView(bannerCol)
            bannerRow.addView(MaterialButton(ctx).apply {
                text = "去复盘"
                textSize = 13f
                cornerRadius = UiKit.dp(ctx, 18)
                minWidth = 0
                minimumWidth = 0
                setPadding(UiKit.dp(ctx, 20), 0, UiKit.dp(ctx, 20), 0)
                setOnClickListener { showCoachReview() }
            })
            banner.addView(bannerRow)
            addView(banner)

            // ===== 功能宫格 (4 列 x 2 行) =====
            UiKit.card(ctx).let { (cardView, inner) ->
                val grid = android.widget.GridLayout(ctx).apply { columnCount = 4 }
                fun cell(iconRes: Int, label: String, tintRes: Int = R.color.on_surface_secondary,
                         onClick: () -> Unit) {
                    val cellBox = LinearLayout(ctx).apply {
                        orientation = LinearLayout.VERTICAL
                        gravity = android.view.Gravity.CENTER
                        setPadding(0, UiKit.dp(ctx, 10), 0, UiKit.dp(ctx, 10))
                        layoutParams = android.widget.GridLayout.LayoutParams(
                            android.widget.GridLayout.spec(android.widget.GridLayout.UNDEFINED, 1f),
                            android.widget.GridLayout.spec(android.widget.GridLayout.UNDEFINED, 1f)
                        ).apply { width = 0 }
                        isClickable = true
                        setOnClickListener { onClick() }
                    }
                    cellBox.addView(android.widget.ImageView(ctx).apply {
                        setImageResource(iconRes)
                        imageTintList = android.content.res.ColorStateList.valueOf(ctx.getColor(tintRes))
                        layoutParams = LinearLayout.LayoutParams(UiKit.dp(ctx, 24), UiKit.dp(ctx, 24))
                    })
                    cellBox.addView(TextView(ctx).apply {
                        text = label
                        textSize = 12f
                        setTextColor(ctx.getColor(R.color.on_surface_secondary))
                        setPadding(0, UiKit.dp(ctx, 6), 0, 0)
                    })
                    grid.addView(cellBox)
                }
                cell(R.drawable.ic_g_body, "身体指标") { showBodyMetricDialog() }
                cell(R.drawable.ic_g_goal, "我的目标") { showGoalsDialog() }
                cell(R.drawable.ic_g_memory, "教练记忆") { showAddMemoryDialog() }
                cell(R.drawable.ic_g_trophy, "成就") { showAchievementsDialog() }
                cell(R.drawable.ic_g_device, "绑定设备") { showBindDialog() }
                cell(R.drawable.ic_g_export, "导出数据") { exportCsv() }
                cell(R.drawable.ic_g_server, "服务器") { showBaseUrlDialog() }
                cell(R.drawable.ic_g_logout, "退出登录", R.color.error) {
                    ApiClient.clearAuth()
                    findNavController().navigate(R.id.loginFragment, null,
                        NavOptions.Builder().setPopUpTo(R.id.loginFragment, true).build())
                }
                inner.addView(grid)
                addView(cardView)
            }

            // ===== 数据横排: 本周打卡 + 体重 =====
            val dataRow = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT
                )
            }
            // 左: 本周打卡条形图
            UiKit.card(ctx).let { (cardView, inner) ->
                (cardView.layoutParams as LinearLayout.LayoutParams).apply {
                    width = 0; weight = 1f; rightMargin = UiKit.dp(ctx, 6)
                }
                inner.addView(UiKit.caption(ctx, "本周打卡"))
                weekBarsRow = LinearLayout(ctx).apply {
                    orientation = LinearLayout.HORIZONTAL
                    gravity = android.view.Gravity.BOTTOM
                    setPadding(0, UiKit.dp(ctx, 10), 0, 0)
                }.also { inner.addView(it) }
                dataRow.addView(cardView)
            }
            // 右: 体重数据
            UiKit.card(ctx).let { (cardView, inner) ->
                (cardView.layoutParams as LinearLayout.LayoutParams).apply {
                    width = 0; weight = 1f; leftMargin = UiKit.dp(ctx, 6)
                }
                inner.addView(UiKit.caption(ctx, "体重数据"))
                bodyMetricView = TextView(ctx).apply {
                    text = "-- 公斤"
                    textSize = 22f
                    setTypeface(android.graphics.Typeface.create("sans-serif-condensed",
                        android.graphics.Typeface.BOLD))
                    setTextColor(ctx.getColor(R.color.on_surface))
                    setPadding(0, UiKit.dp(ctx, 8), 0, 0)
                }.also { inner.addView(it) }
                cardView.isClickable = true
                cardView.setOnClickListener { showBodyMetricDialog() }
                dataRow.addView(cardView)
            }
            addView(dataRow)

            // ===== 最新记录 =====
            UiKit.card(ctx).let { (cardView, inner) ->
                inner.addView(UiKit.cardTitle(ctx, "最新记录"))
                recentContainer = LinearLayout(ctx).apply {
                    orientation = LinearLayout.VERTICAL
                }.also { inner.addView(it) }
                addView(cardView)
            }
        }
        return scroll
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        loadProfile()
        loadBodyMetric()
        loadGoals()
        loadAchievements()
        loadHeaderChips()
        loadWeekBars()
        loadRecent()
    }

    // ---------- Keep 式头部/数据区加载 ----------

    private fun addChip(text: String) {
        val ctx = requireContext()
        chipsRow.addView(TextView(ctx).apply {
            this.text = text
            textSize = 12f
            setTextColor(ctx.getColor(R.color.on_surface_secondary))
            background = android.graphics.drawable.GradientDrawable().apply {
                cornerRadius = UiKit.dp(ctx, 14).toFloat()
                setColor(ctx.getColor(R.color.surface))
                setStroke(UiKit.dp(ctx, 1), ctx.getColor(R.color.divider))
            }
            setPadding(UiKit.dp(ctx, 12), UiKit.dp(ctx, 6), UiKit.dp(ctx, 12), UiKit.dp(ctx, 6))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { rightMargin = UiKit.dp(ctx, 8) }
        })
    }

    private fun loadHeaderChips() {
        lifecycleScope.launch {
            try {
                val s = ApiClient.service.streak()
                val ach = ApiClient.service.achievements()
                if (!isAdded) return@launch
                chipsRow.removeAllViews()
                addChip("⚡ 连续 ${s.currentStreak} 天")
                addChip("最长 ${s.longestStreak} 天")
                addChip("🏆 成就 ${ach.achievements.count { it.unlocked }}")
            } catch (_: Exception) {}
        }
    }

    private fun loadWeekBars() {
        lifecycleScope.launch {
            try {
                val resp = ApiClient.service.calendarDays()
                if (!isAdded) return@launch
                val ctx = requireContext()
                val byDay = resp.days.associateBy { it.d }
                val fmt = java.text.SimpleDateFormat("yyyy-MM-dd", Locale.getDefault())
                val cal = java.util.Calendar.getInstance()
                cal.add(java.util.Calendar.DAY_OF_YEAR, -6)
                weekBarsRow.removeAllViews()
                for (i in 0 until 7) {
                    val reps = byDay[fmt.format(cal.time)]?.reps ?: 0
                    val h = if (reps > 0) 36 else 18
                    weekBarsRow.addView(View(ctx).apply {
                        layoutParams = LinearLayout.LayoutParams(0, UiKit.dp(ctx, h), 1f).apply {
                            leftMargin = UiKit.dp(ctx, 2); rightMargin = UiKit.dp(ctx, 2)
                        }
                        background = android.graphics.drawable.GradientDrawable().apply {
                            cornerRadius = UiKit.dp(ctx, 3).toFloat()
                            setColor(ctx.getColor(if (reps > 0) R.color.primary else R.color.divider))
                        }
                    })
                    cal.add(java.util.Calendar.DAY_OF_YEAR, 1)
                }
            } catch (_: Exception) {}
        }
    }

    private fun loadRecent() {
        lifecycleScope.launch {
            try {
                val raw = ApiClient.service.listExerciseLog(limit = 3, days = 90)
                if (!isAdded) return@launch
                val ctx = requireContext()
                recentContainer.removeAllViews()
                if (raw.log.isEmpty()) {
                    recentContainer.addView(UiKit.caption(ctx, "还没有训练记录, 去完成第一次吧"))
                    return@launch
                }
                val fmt = java.text.SimpleDateFormat("MM-dd", Locale.getDefault())
                raw.log.take(3).forEach { e ->
                    val row = LinearLayout(ctx).apply {
                        orientation = LinearLayout.HORIZONTAL
                        gravity = android.view.Gravity.CENTER_VERTICAL
                        setPadding(0, UiKit.dp(ctx, 8), 0, UiKit.dp(ctx, 8))
                    }
                    row.addView(View(ctx).apply {
                        layoutParams = LinearLayout.LayoutParams(UiKit.dp(ctx, 10), UiKit.dp(ctx, 10)).apply {
                            rightMargin = UiKit.dp(ctx, 10)
                        }
                        background = android.graphics.drawable.GradientDrawable().apply {
                            shape = android.graphics.drawable.GradientDrawable.OVAL
                            setColor(ctx.getColor(R.color.primary))
                        }
                    })
                    val col = LinearLayout(ctx).apply {
                        orientation = LinearLayout.VERTICAL
                        layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                    }
                    col.addView(UiKit.body(ctx, "${e.exerciseType}  ${e.reps} 个", 15f).apply {
                        setTypeface(typeface, android.graphics.Typeface.BOLD)
                    })
                    val form = e.avgFormScore?.let { "评分 ${it.toInt()}" } ?: ""
                    col.addView(UiKit.caption(ctx, form))
                    row.addView(col)
                    row.addView(UiKit.caption(ctx, fmt.format(java.util.Date((e.performedAt * 1000).toLong()))))
                    recentContainer.addView(row)
                }
            } catch (_: Exception) {}
        }
    }

    private fun showAchievementsDialog() {
        AlertDialog.Builder(requireContext())
            .setTitle("我的成就")
            .setMessage(achievementsView.text)
            .setPositiveButton("OK", null)
            .show()
    }

    // ---------- AI 私人教练管家 ----------

    private fun showCoachReview() {
        val loading = AlertDialog.Builder(requireContext())
            .setTitle("AI 教练复盘")
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

        // BottomSheet 拖拽指示条 (32x4dp, 圆角, #E0E0E0)
        box.addView(View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(UiKit.dp(ctx, 32), UiKit.dp(ctx, 4)).apply {
                gravity = android.view.Gravity.CENTER_HORIZONTAL
                bottomMargin = UiKit.dp(ctx, 12)
            }
            background = android.graphics.drawable.GradientDrawable().apply {
                cornerRadius = UiKit.dp(ctx, 2).toFloat()
                setColor(0xFFE0E0E0.toInt())
            }
        })

        box.addView(TextView(ctx).apply {
            text = "AI 教练复盘"
            textSize = 20f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
        })
        box.addView(UiKit.caption(ctx, "基于你的真实训练数据生成").apply {
            setPadding(0, 4, 0, UiKit.dp(ctx, 8))
        })

        fun section(title: String, bodyText: String?, panel: Boolean = false) {
            if (bodyText.isNullOrBlank()) return
            box.addView(TextView(ctx).apply {
                text = title
                textSize = 16f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.on_surface))
                setPadding(0, UiKit.dp(ctx, 16), 0, UiKit.dp(ctx, 4))
            })
            box.addView(UiKit.body(ctx, bodyText).apply {
                setLineSpacing(0f, 1.2f)
                if (panel) {
                    // 浅色底板包裹 (下周建议)
                    background = android.graphics.drawable.GradientDrawable().apply {
                        cornerRadius = UiKit.dp(ctx, 12).toFloat()
                        setColor(ctx.getColor(R.color.bg))
                    }
                    setPadding(UiKit.dp(ctx, 12), UiKit.dp(ctx, 10),
                               UiKit.dp(ctx, 12), UiKit.dp(ctx, 10))
                }
            })
        }

        if (rv != null) {
            section("趋势", rv.trend)
            section("动作平衡", rv.balance)
            section("弱点", rv.weakness)
            section("计划执行", rv.adherence)
            rv.nextWeek?.takeIf { it.isNotEmpty() }?.let { nw ->
                section("下周建议", nw.joinToString("\n") { "•  $it" }, panel = true)
            }
            rv.encouragement?.let { enc ->
                // 暗色正文, 仅数字/得分橙色加粗 (SpannableString)
                val sp = android.text.SpannableString(enc)
                Regex("\\d+(\\.\\d+)?[分天次个秒]?").findAll(enc).forEach { m ->
                    sp.setSpan(android.text.style.ForegroundColorSpan(ctx.getColor(R.color.highlight_orange)),
                        m.range.first, m.range.last + 1, 0)
                    sp.setSpan(android.text.style.StyleSpan(android.graphics.Typeface.BOLD),
                        m.range.first, m.range.last + 1, 0)
                }
                box.addView(TextView(ctx).apply {
                    text = sp
                    textSize = 15f
                    setTextColor(ctx.getColor(R.color.text_dark))
                    setLineSpacing(0f, 1.2f)
                    setPadding(0, UiKit.dp(ctx, 16), 0, 0)
                })
            }
        } else {
            box.addView(UiKit.body(ctx, fallback ?: "(无内容)"))
        }

        box.addView(MaterialButton(ctx).apply {
            text = "收到"
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
            .setTitle("告诉教练")
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
                    createdAtView.text = "加入于 ${formatTimestamp(it.createdAt)}"
                    avatarView.text = it.username.take(1).uppercase()
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
                bodyMetricView.text = if (m?.weightKg == null) "点击记录"
                                      else "${m.weightKg} 公斤"
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

