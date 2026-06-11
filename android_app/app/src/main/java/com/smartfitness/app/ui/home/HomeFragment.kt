package com.smartfitness.app.ui.home

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.ui.login.OnboardingHelper
import kotlinx.coroutines.launch

class HomeFragment : Fragment() {

    private lateinit var sessionsView: TextView
    private lateinit var repsView: TextView
    private lateinit var minutesView: TextView
    private lateinit var scoreView: TextView
    private lateinit var plansContainer: LinearLayout
    private lateinit var swipe: SwipeRefreshLayout
    private var calendarGrid: android.widget.GridLayout? = null
    private var calendarSummary: TextView? = null

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_home, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        sessionsView = view.findViewById(R.id.stat_sessions)
        repsView = view.findViewById(R.id.stat_reps)
        minutesView = view.findViewById(R.id.stat_minutes)
        scoreView = view.findViewById(R.id.stat_score)
        plansContainer = view.findViewById(R.id.plans_container)
        swipe = view.findViewById(R.id.swipe_refresh)
        calendarGrid = view.findViewById(R.id.calendar_grid)
        calendarSummary = view.findViewById(R.id.calendar_summary)

        view.findViewById<View?>(R.id.btn_start_training)?.setOnClickListener {
            requireActivity()
                .findViewById<com.google.android.material.bottomnavigation.BottomNavigationView>(R.id.bottom_nav)
                .selectedItemId = R.id.trainingFragment
        }

        swipe.setOnRefreshListener { loadAll() }
        loadAll()
        // Task 3: 没走过初次引导的老用户也弹一下 (也覆盖了 token 注入开发场景)
        if (!OnboardingHelper.isCompleted(requireContext()) && ApiClient.isLoggedIn()) {
            view.post {
                if (isAdded) OnboardingHelper.show(requireContext(), viewLifecycleOwner.lifecycleScope) { loadAll() }
            }
        }
    }

    private fun loadAll() {
        swipe.isRefreshing = true
        lifecycleScope.launch {
            try {
                val stats = ApiClient.service.statsDaily()
                stats.stats?.let { s ->
                    sessionsView.text = s.sessionsCount.toString()
                    repsView.text = s.totalReps.toString()
                    minutesView.text = String.format("%.1f", s.totalMinutes)
                    scoreView.text = String.format("%.1f", s.avgScore)
                }

                val plans = ApiClient.service.listPlans().plans
                plansContainer.removeAllViews()

                // E-05: 今日身体指标卡片 (BMI + 推荐强度)
                try {
                    val latest = ApiClient.service.latestBodyMetric()
                    latest.latest?.let { m ->
                        val card = TextView(requireContext())
                        val bmi = m.bmi ?: 0.0
                        val intensity = when {
                            bmi < 18.5 -> "偏瓦增肌"
                            bmi < 24   -> "正常强度"
                            bmi < 28   -> "控制饮食"
                            else        -> "低冲击运动"
                        }
                        card.text = "📊 体重 ${m.weightKg ?: "-"}kg  BMI ${bmi}  建议：$intensity"
                        card.textSize = 14f
                        card.setPadding(24, 16, 24, 8)
                        plansContainer.addView(card)
                    }
                } catch (_: Exception) {}

                // E-05: 7 日运动 summary
                try {
                    val sum = ApiClient.service.exerciseSummary(7)
                    if (sum.byType.isNotEmpty()) {
                        val title = TextView(requireContext())
                        title.text = "近 7 日运动"
                        title.textSize = 14f
                        title.setPadding(24, 16, 24, 8)
                        plansContainer.addView(title)
                        sum.byType.take(3).forEach { item ->
                            val tv = TextView(requireContext())
                            tv.text = "  ${item.exerciseType}: ${item.totalReps} reps / ${item.sessions} 组 / 平均分 ${String.format("%.0f", item.avgForm ?: 0.0)}"
                            tv.textSize = 13f
                            tv.setPadding(24, 4, 24, 4)
                            plansContainer.addView(tv)
                        }
                    }
                } catch (_: Exception) {}

                // E-06: 30 日趋势 (文本版, 按日聊 reps + 平均分)
                try {
                    val raw = ApiClient.service.listExerciseLog(limit = 200, days = 30)
                    if (raw.log.isNotEmpty()) {
                        val title = TextView(requireContext())
                        title.text = "近 30 日趋势"
                        title.textSize = 14f
                        title.setPadding(24, 16, 24, 8)
                        plansContainer.addView(title)
                        // 按日聚合 (UTC 日)
                        val byDay = raw.log.groupBy {
                            java.text.SimpleDateFormat("MM-dd", java.util.Locale.getDefault())
                                .format(java.util.Date((it.performedAt * 1000).toLong()))
                        }.toSortedMap(compareByDescending { it })
                        byDay.entries.take(7).forEach { (day, entries) ->
                            val reps = entries.sumOf { it.reps }
                            val avgForm = entries.mapNotNull { it.avgFormScore }.average().takeIf { !it.isNaN() } ?: 0.0
                            val bar = "■".repeat((reps / 5).coerceAtMost(20))
                            val tv = TextView(requireContext())
                            tv.text = "  $day  $reps reps  ${String.format("%.0f", avgForm)}分  $bar"
                            tv.textSize = 12f
                            tv.typeface = android.graphics.Typeface.MONOSPACE
                            tv.setPadding(24, 2, 24, 2)
                            plansContainer.addView(tv)
                        }
                    }
                } catch (_: Exception) {}

                if (plans.isEmpty()) {
                    val tv = TextView(requireContext())
                    tv.text = getString(R.string.no_plans)
                    tv.setPadding(24, 24, 24, 24)
                    plansContainer.addView(tv)
                } else {
                    val title = TextView(requireContext())
                    title.text = "训练计划"
                    title.textSize = 14f
                    title.setPadding(24, 16, 24, 8)
                    plansContainer.addView(title)
                    plans.take(5).forEach { p ->
                        val tv = TextView(requireContext())
                        tv.text = "• ${p.name}"
                        tv.textSize = 16f
                        tv.setPadding(24, 16, 24, 16)
                        plansContainer.addView(tv)
                    }
                }

                loadCalendar()
                loadStreakAndAchievements()
            } catch (e: Exception) {
                Toast.makeText(
                    requireContext(),
                    "Load failed: ${e.message}",
                    Toast.LENGTH_SHORT
                ).show()
            } finally {
                swipe.isRefreshing = false
            }
        }
    }

    private fun loadCalendar() {
        val grid = calendarGrid ?: return
        val sum = calendarSummary ?: return
        lifecycleScope.launch {
            try {
                val resp = ApiClient.service.calendarDays()
                val byDay = resp.days.associateBy { it.d }
                grid.removeAllViews()
                val ctx = requireContext()
                val px = (ctx.resources.displayMetrics.density * 14).toInt()
                val gap = (ctx.resources.displayMetrics.density * 2).toInt()
                val cal = java.util.Calendar.getInstance()
                cal.add(java.util.Calendar.DAY_OF_YEAR, -83)
                val fmt = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.getDefault())
                var activeDays = 0; var totalReps = 0
                for (col in 0 until 12) {
                    for (row in 0 until 7) {
                        val key = fmt.format(cal.time)
                        val day = byDay[key]
                        val reps = day?.reps ?: 0
                        if (reps > 0) { activeDays++; totalReps += reps }
                        val cell = View(ctx)
                        val lp = android.widget.GridLayout.LayoutParams().apply {
                            width = px; height = px
                            setMargins(gap, gap, gap, gap)
                        }
                        cell.layoutParams = lp
                        // Keep 绿色阶
                        val color = when {
                            reps == 0 -> 0xFFF0F0F2.toInt()
                            reps < 10 -> 0xFFB8EFD9.toInt()
                            reps < 30 -> 0xFF6FDDB0.toInt()
                            reps < 60 -> 0xFF24C789.toInt()
                            else -> 0xFF17835C.toInt()
                        }
                        cell.background = android.graphics.drawable.GradientDrawable().apply {
                            cornerRadius = ctx.resources.displayMetrics.density * 3
                            setColor(color)
                        }
                        grid.addView(cell)
                        cal.add(java.util.Calendar.DAY_OF_YEAR, 1)
                    }
                }
                sum.text = "近 12 周活跃 $activeDays 天 · 累计 $totalReps 次"
            } catch (e: Exception) {
                sum.text = "Calendar load failed: ${e.message}"
            }
        }
    }

    private fun loadStreakAndAchievements() {
        lifecycleScope.launch {
            try {
                val s = ApiClient.service.streak()
                val ach = ApiClient.service.achievements()
                val ctx = requireContext()
                val container = plansContainer
                // Insert at top of plansContainer
                val title = TextView(ctx)
                title.text = "连续训练"
                title.textSize = 15f
                title.setTypeface(title.typeface, android.graphics.Typeface.BOLD)
                title.setTextColor(ctx.getColor(com.smartfitness.app.R.color.on_surface))
                title.setPadding(24, 24, 24, 8)
                container.addView(title, 0)
                val streakTv = TextView(ctx)
                streakTv.text = "当前 ${s.currentStreak} 天 · 最长 ${s.longestStreak} 天 · 最近 ${s.lastActive ?: "-"}"
                streakTv.textSize = 13f
                streakTv.setTextColor(ctx.getColor(com.smartfitness.app.R.color.on_surface_secondary))
                streakTv.setPadding(24, 4, 24, 16)
                container.addView(streakTv, 1)

                val achTitle = TextView(ctx)
                val unlocked = ach.achievements.count { it.unlocked }
                achTitle.text = "成就 ($unlocked / ${ach.achievements.size})"
                achTitle.textSize = 15f
                achTitle.setTypeface(achTitle.typeface, android.graphics.Typeface.BOLD)
                achTitle.setTextColor(ctx.getColor(com.smartfitness.app.R.color.on_surface))
                achTitle.setPadding(24, 16, 24, 8)
                container.addView(achTitle, 2)
                ach.achievements.forEach { a ->
                    val tv = TextView(ctx)
                    val mark = if (a.unlocked) "✓" else "·"
                    tv.text = " $mark  ${a.name} - ${a.desc}"
                    tv.textSize = 13f
                    tv.setTextColor(ctx.getColor(
                        if (a.unlocked) com.smartfitness.app.R.color.primary_dark
                        else com.smartfitness.app.R.color.on_surface_secondary))
                    tv.setPadding(24, 10, 24, 10)
                    container.addView(tv, 3 + ach.achievements.indexOf(a))
                }
            } catch (e: Exception) {
                android.util.Log.w("Home", "streak/ach load fail: " + e.message)
            }
        }
    }
}
