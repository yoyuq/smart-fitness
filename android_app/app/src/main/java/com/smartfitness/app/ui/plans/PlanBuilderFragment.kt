package com.smartfitness.app.ui.plans

import android.app.ProgressDialog
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.GridLayout
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
import com.smartfitness.app.app.PlanBuilderDraftHolder
import com.smartfitness.app.model.CreatePlanRequest
import com.smartfitness.app.model.PlanAiDraftRequest
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.launch

/** 制定训练计划：周期 + 科学运动分类 + 详细动作 + AI 草稿 + 编辑导入。 */
class PlanBuilderFragment : Fragment() {

    private lateinit var content: LinearLayout
    private var step = 1

    private var planName = ""
    private var weeks = 4
    private var weeklyTrainingDays: Int? = 4
    private var sessionMinutes: Int? = 45
    private var userGoal = ""
    private var limits = ""
    private var draftReason: String? = null
    private var draftItems: List<Map<String, Any>> = emptyList()
    private var openedFromAgent = false

    private val selectedCategoryIds = linkedSetOf<String>()
    private val selectedOptionIds = linkedSetOf<String>()

    private data class ExerciseOption(
        val id: String,
        val title: String,
        val type: String,
        val category: String,
        val duration: Int = 0,
        val distance: Double = 0.0,
        val sets: Int = 0,
        val reps: Int = 0,
        val intensity: String = "",
        val note: String = "",
        val trackable: Boolean = false
    )

    private data class ExerciseCategory(
        val id: String,
        val title: String,
        val subtitle: String,
        val icon: String,
        val options: List<ExerciseOption>
    )

    private val categories = listOf(
        ExerciseCategory("running", "跑步 / 田径类", "心肺、耐力、速度", "🏃", listOf(
            ExerciseOption("easy_run", "轻松跑", "running", "cardio", 30, 5.0, intensity = "轻松", note = "保持能说完整句子的配速"),
            ExerciseOption("long_run", "长距离跑", "running", "cardio", 60, 8.0, intensity = "中低", note = "提高有氧耐力"),
            ExerciseOption("interval_run", "间歇跑", "running", "cardio", 35, 4.0, intensity = "高", note = "快慢交替，注意热身"),
            ExerciseOption("tempo_run", "节奏跑", "running", "cardio", 40, 6.0, intensity = "中高", note = "接近但低于比赛强度"),
            ExerciseOption("hill_run", "坡跑", "running", "cardio", 30, 3.0, intensity = "高", note = "提升力量和步频"),
            ExerciseOption("cooldown_jog", "跑后放松", "jogging", "cardio", 10, 1.0, intensity = "恢复", note = "降低心率")
        )),
        ExerciseCategory("strength", "健身 / 力量类", "增肌、核心、基础体能", "💪", listOf(
            ExerciseOption("squat", "深蹲", "squat", "strength", sets = 3, reps = 15, intensity = "中等", note = "膝盖对齐脚尖", trackable = true),
            ExerciseOption("push_up", "俯卧撑", "push_up", "strength", sets = 4, reps = 12, intensity = "中等", note = "核心收紧", trackable = true),
            ExerciseOption("plank", "平板支撑", "plank", "strength", duration = 3, sets = 3, intensity = "中等", note = "每组约 45-60 秒", trackable = true),
            ExerciseOption("lunge", "弓步蹲", "lunge", "strength", sets = 3, reps = 12, intensity = "中等", note = "左右腿均衡", trackable = true),
            ExerciseOption("pull_up", "引体向上", "pull_up", "strength", sets = 4, reps = 6, intensity = "高", note = "可用弹力带辅助"),
            ExerciseOption("core", "核心训练", "core", "strength", duration = 15, intensity = "中等", note = "卷腹、死虫、侧桥组合"),
            ExerciseOption("circuit", "全身循环训练", "circuit", "strength", duration = 25, intensity = "中高", note = "力量+心肺综合")
        )),
        ExerciseCategory("swim", "游泳 / 低冲击有氧类", "心肺、恢复、低冲击", "🏊", listOf(
            ExerciseOption("freestyle", "自由泳", "swimming", "cardio", 30, intensity = "中等", note = "技术稳定优先"),
            ExerciseOption("breaststroke", "蛙泳", "swimming", "cardio", 30, intensity = "中等", note = "注意膝盖舒适度"),
            ExerciseOption("swim_drill", "技术练习", "swim_drill", "cardio", 20, intensity = "轻松", note = "打腿、划水、换气"),
            ExerciseOption("relax_swim", "放松游", "swimming", "cardio", 25, intensity = "恢复", note = "低强度恢复"),
            ExerciseOption("interval_swim", "间歇游", "swimming", "cardio", 35, intensity = "中高", note = "分段完成")
        )),
        ExerciseCategory("cycling", "骑行 / 户外耐力类", "耐力、心肺、户外", "🚴", listOf(
            ExerciseOption("indoor_cycling", "室内骑行", "cycling", "cardio", 40, intensity = "中等", note = "稳定踏频"),
            ExerciseOption("road_cycling", "公路骑行", "cycling", "cardio", 60, intensity = "中等", note = "注意安全"),
            ExerciseOption("climb_cycling", "爬坡骑行", "cycling", "cardio", 45, intensity = "高", note = "强化腿部耐力"),
            ExerciseOption("recovery_ride", "恢复骑", "cycling", "cardio", 30, intensity = "恢复", note = "低阻力轻松骑"),
            ExerciseOption("long_ride", "长距离骑行", "cycling", "cardio", 90, intensity = "中低", note = "补水补能量")
        )),
        ExerciseCategory("mobility", "灵活性 / 拉伸 / 恢复类", "拉伸、瑜伽、放松", "🧘", listOf(
            ExerciseOption("dynamic_stretch", "动态拉伸", "stretch", "mobility", 10, intensity = "热身", note = "训练前激活"),
            ExerciseOption("static_stretch", "静态拉伸", "stretch", "mobility", 15, intensity = "恢复", note = "训练后放松"),
            ExerciseOption("hip_mobility", "髋部灵活性", "mobility", "mobility", 12, intensity = "轻松", note = "改善跑姿和深蹲活动度"),
            ExerciseOption("calf_stretch", "小腿拉伸", "stretch", "mobility", 8, intensity = "恢复", note = "跑后重点"),
            ExerciseOption("yoga_recovery", "瑜伽恢复", "yoga", "mobility", 25, intensity = "恢复", note = "低强度恢复日"),
            ExerciseOption("rest_day", "休息日", "rest", "recovery", 0, intensity = "恢复", note = "保证睡眠和补水")
        )),
        ExerciseCategory("other", "综合运动 / 其他类", "跳绳、球类、自定义", "✨", listOf(
            ExerciseOption("jump_rope", "跳绳", "jump_rope", "cardio", 15, intensity = "中高", note = "注意小腿负荷"),
            ExerciseOption("ball_game", "球类运动", "ball_sport", "sport", 45, intensity = "中等", note = "可作为兴趣训练"),
            ExerciseOption("hiking", "徒步", "hiking", "cardio", 90, intensity = "中低", note = "低冲击耐力"),
            ExerciseOption("climbing", "攀爬 / 攀岩", "climbing", "sport", 60, intensity = "中高", note = "上肢和核心"),
            ExerciseOption("custom", "自定义项目", "custom", "custom", 30, intensity = "自定", note = "导入前可编辑")
        ))
    )

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        val ctx = inflater.context
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 24))
        }
        root.addView(UiKit.topBar(ctx, "制定训练计划") {
            if (openedFromAgent) PlanBuilderDraftHolder.clear()
            findNavController().popBackStack()
        })
        content = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        root.addView(content)
        consumeExternalDraftIfAny()
        render()
        return ScrollView(ctx).apply { setBackgroundColor(ctx.getColor(R.color.bg)); addView(root) }
    }

    private fun consumeExternalDraftIfAny() {
        val items = PlanBuilderDraftHolder.exercises ?: return
        planName = PlanBuilderDraftHolder.name?.takeIf { it.isNotBlank() } ?: "Agent 生成计划"
        weeks = (PlanBuilderDraftHolder.weeks ?: weeks).coerceIn(1, 8)
        userGoal = PlanBuilderDraftHolder.goal.orEmpty()
        draftReason = PlanBuilderDraftHolder.reason
        draftItems = items
        openedFromAgent = PlanBuilderDraftHolder.openFromAgent
        step = 5
    }

    private fun render() {
        if (!::content.isInitialized || !isAdded) return
        content.removeAllViews()
        val ctx = requireContext()
        content.addView(UiKit.caption(ctx, "步骤 $step / 5"))
        when (step) {
            1 -> renderBasics()
            2 -> renderCategories()
            3 -> renderOptions()
            4 -> renderAiPrefs()
            else -> renderDraft()
        }
    }

    private fun renderBasics() {
        val ctx = requireContext()
        val (card, inner) = UiKit.card(ctx)
        inner.addView(UiKit.cardTitle(ctx, "1. 基础信息"))
        inner.addView(UiKit.caption(ctx, "先确定计划名称、训练周期和大致训练频率。"))
        val nameInput = labeledInput(ctx, inner, "计划名称", "例如：暑期体能提升计划", planName, singleLine = true)
        val weeksInput = labeledInput(ctx, inner, "训练周期（周）", "1-8 周，例如 4", weeks.toString(), number = true)
        val daysInput = labeledInput(ctx, inner, "每周训练天数", "例如 4 天/周", weeklyTrainingDays?.toString().orEmpty(), number = true)
        val minutesInput = labeledInput(ctx, inner, "单次训练时长（分钟）", "例如 45 分钟", sessionMinutes?.toString().orEmpty(), number = true)
        inner.addView(primaryButton("下一步：选择运动分类") {
            planName = nameInput.text?.toString()?.trim().orEmpty().ifBlank { "我的训练计划" }
            weeks = (weeksInput.text?.toString()?.toIntOrNull() ?: 4).coerceIn(1, 8)
            weeklyTrainingDays = daysInput.text?.toString()?.toIntOrNull()?.coerceIn(1, 7)
            sessionMinutes = minutesInput.text?.toString()?.toIntOrNull()?.coerceIn(5, 240)
            step = 2; render()
        })
        content.addView(card)
    }

    private fun renderCategories() {
        val ctx = requireContext()
        val (card, inner) = UiKit.card(ctx)
        inner.addView(UiKit.cardTitle(ctx, "2. 选择运动分类"))
        inner.addView(UiKit.caption(ctx, "可多选。分类参考有氧、力量、低冲击、恢复等训练原则。"))
        val grid = GridLayout(ctx).apply { columnCount = 2; setPadding(0, UiKit.dp(ctx, 10), 0, 0) }
        categories.forEach { cat -> grid.addView(categoryTile(cat)) }
        inner.addView(grid)
        inner.addView(navRow("上一步", "下一步：选择详细项目", { step = 1; render() }) {
            if (selectedCategoryIds.isEmpty()) Toast.makeText(ctx, "至少选择一个运动分类", Toast.LENGTH_SHORT).show()
            else { step = 3; render() }
        })
        content.addView(card)
    }

    private fun categoryTile(cat: ExerciseCategory): View {
        val ctx = requireContext()
        val selected = selectedCategoryIds.contains(cat.id)
        return LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 12), UiKit.dp(ctx, 10), UiKit.dp(ctx, 12), UiKit.dp(ctx, 10))
            background = roundedBg(if (selected) ctx.getColor(R.color.primary_light) else ctx.getColor(R.color.surface), if (selected) ctx.getColor(R.color.primary) else ctx.getColor(R.color.divider))
            isClickable = true
            setOnClickListener {
                if (selectedCategoryIds.contains(cat.id)) {
                    selectedCategoryIds.remove(cat.id)
                    cat.options.forEach { selectedOptionIds.remove(it.id) }
                } else selectedCategoryIds.add(cat.id)
                render()
            }
            layoutParams = ViewGroup.MarginLayoutParams((resources.displayMetrics.widthPixels - UiKit.dp(ctx, 68)) / 2, UiKit.dp(ctx, 96)).apply {
                setMargins(UiKit.dp(ctx, 4), UiKit.dp(ctx, 5), UiKit.dp(ctx, 4), UiKit.dp(ctx, 5))
            }
            addView(TextView(ctx).apply { text = cat.icon; textSize = 20f })
            addView(TextView(ctx).apply { text = cat.title; textSize = 14f; setTypeface(typeface, Typeface.BOLD); setTextColor(ctx.getColor(R.color.on_surface)) })
            addView(UiKit.caption(ctx, cat.subtitle))
        }
    }

    private fun renderOptions() {
        val ctx = requireContext()
        val (card, inner) = UiKit.card(ctx)
        inner.addView(UiKit.cardTitle(ctx, "3. 选择详细动作 / 训练项"))
        inner.addView(UiKit.caption(ctx, "可识别动作会在详情页显示“开始”；其他项目显示“完成打卡”。"))
        selectedCategories().forEach { cat ->
            inner.addView(TextView(ctx).apply {
                text = cat.title
                textSize = 15f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.primary_dark))
                setPadding(0, UiKit.dp(ctx, 14), 0, UiKit.dp(ctx, 6))
            })
            val wrap = GridLayout(ctx).apply { columnCount = 2 }
            cat.options.forEach { opt -> wrap.addView(optionChip(opt)) }
            inner.addView(wrap)
        }
        inner.addView(UiKit.caption(ctx, "已选 ${selectedOptions().size} 项"))
        inner.addView(navRow("上一步", "下一步：AI 偏好", { step = 2; render() }) {
            if (selectedOptionIds.isEmpty()) Toast.makeText(ctx, "至少选择一个训练项目", Toast.LENGTH_SHORT).show()
            else { step = 4; render() }
        })
        content.addView(card)
    }

    private fun optionChip(opt: ExerciseOption): View {
        val ctx = requireContext()
        val selected = selectedOptionIds.contains(opt.id)
        return MaterialButton(ctx).apply {
            text = opt.title + if (opt.trackable) " · 可识别" else ""
            textSize = 13f
            cornerRadius = UiKit.dp(ctx, 18)
            minWidth = 0; minimumWidth = 0
            setTextColor(ctx.getColor(if (selected) R.color.white else R.color.primary))
            setBackgroundColor(ctx.getColor(if (selected) R.color.primary else R.color.surface))
            strokeWidth = UiKit.dp(ctx, 1)
            strokeColor = android.content.res.ColorStateList.valueOf(ctx.getColor(if (selected) R.color.primary else R.color.divider))
            layoutParams = ViewGroup.MarginLayoutParams((resources.displayMetrics.widthPixels - UiKit.dp(ctx, 76)) / 2, UiKit.dp(ctx, 42)).apply {
                setMargins(UiKit.dp(ctx, 4), UiKit.dp(ctx, 4), UiKit.dp(ctx, 4), UiKit.dp(ctx, 4))
            }
            setOnClickListener {
                if (selectedOptionIds.contains(opt.id)) selectedOptionIds.remove(opt.id) else selectedOptionIds.add(opt.id)
                render()
            }
        }
    }

    private fun renderAiPrefs() {
        val ctx = requireContext()
        val (card, inner) = UiKit.card(ctx)
        inner.addView(UiKit.cardTitle(ctx, "4. AI 制定偏好"))
        inner.addView(UiKit.caption(ctx, "AI 会结合周期、分类、训练项和你的补充说明生成草稿。"))
        val goalInput = EditText(ctx).apply {
            hint = "目标：例如提高 5km 成绩，同时增肌，保持每天跑步习惯。"
            minLines = 4; maxLines = 8
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setText(userGoal)
        }
        val limitInput = EditText(ctx).apply {
            hint = "限制条件：例如膝盖不适、只能晚上练、每次 45 分钟。"
            minLines = 3; maxLines = 6
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setText(limits)
        }
        inner.addView(goalInput); inner.addView(limitInput)
        inner.addView(UiKit.caption(ctx, "已选：${selectedCategories().joinToString("、") { it.title }}；${selectedOptions().joinToString("、") { it.title }}"))
        inner.addView(navRow("上一步", "生成训练计划草稿", { step = 3; render() }) {
            userGoal = goalInput.text?.toString()?.trim().orEmpty()
            limits = limitInput.text?.toString()?.trim().orEmpty()
            requestDraft()
        })
        content.addView(card)
    }

    private fun requestDraft() {
        val ctx = requireContext()
        val selectedCats = selectedCategories()
        val selectedOpts = selectedOptions()
        val prompt = buildString {
            append(userGoal.ifBlank { "请根据我的选择制定训练计划。" })
            if (limits.isNotBlank()) append("\n限制条件: ").append(limits)
            append("\n计划名称: ").append(planName)
            append("\n训练周期: ").append(weeks).append("周")
            weeklyTrainingDays?.let { append("\n每周训练天数: ").append(it) }
            sessionMinutes?.let { append("\n单次训练时长: ").append(it).append("分钟") }
            append("\n运动分类: ").append(selectedCats.joinToString("、") { it.title })
            append("\n详细训练项: ").append(selectedOpts.joinToString("、") { it.title })
        }
        val progress = ProgressDialog(ctx).apply { setMessage("AI 教练正在生成草稿…"); setCancelable(false); show() }
        lifecycleScope.launch {
            try {
                val resp = ApiClient.service.draftPlanWithAi(
                    PlanAiDraftRequest(
                        prompt = prompt,
                        weeks = weeks,
                        planName = planName,
                        categories = selectedCats.map { it.title },
                        selectedItems = selectedOpts.map { mapOf("id" to it.id, "title" to it.title, "type" to it.type, "category" to it.category) },
                        weeklyTrainingDays = weeklyTrainingDays,
                        sessionMinutes = sessionMinutes
                    )
                )
                progress.dismiss()
                if (resp.ok && resp.exercises.isNotEmpty()) {
                    draftReason = resp.reason
                    draftItems = resp.exercises.map { itemToMap(it.type, it.title, it.category, it.week ?: 1, it.day ?: 1, it.durationMin, it.distanceKm, it.sets, it.reps, it.intensity, it.note) }
                    if (resp.name?.isNotBlank() == true) planName = resp.name
                    step = 5; render()
                } else Toast.makeText(ctx, resp.message ?: "生成失败", Toast.LENGTH_LONG).show()
            } catch (e: Exception) {
                progress.dismiss()
                Toast.makeText(ctx, "生成失败: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun renderDraft() {
        val ctx = requireContext()
        val (card, inner) = UiKit.card(ctx)
        inner.addView(UiKit.cardTitle(ctx, "5. 草稿预览与编辑"))
        if (!draftReason.isNullOrBlank()) inner.addView(UiKit.caption(ctx, "生成理由：$draftReason"))
        val nameInput = EditText(ctx).apply { hint = "计划名称"; setText(planName); setSingleLine(true) }
        val itemsInput = EditText(ctx).apply {
            hint = "格式：天,项目名,类别,type,分钟,公里,组数,次数,强度,备注"
            minLines = 10; maxLines = 16
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setText(renderLines(draftItems.ifEmpty { selectedOptions().mapIndexed { idx, opt -> itemToMap(opt.type, opt.title, opt.category, 1, idx + 1, opt.duration, opt.distance, opt.sets, opt.reps, opt.intensity, opt.note) } }))
        }
        inner.addView(nameInput); inner.addView(itemsInput)
        inner.addView(navRow("上一步", "导入到我的计划", { step = 4; render() }) {
            val name = nameInput.text?.toString()?.trim().orEmpty().ifBlank { "我的训练计划" }
            val items = parseLines(itemsInput.text?.toString().orEmpty())
            if (items.isEmpty()) Toast.makeText(ctx, "至少保留一个计划项目", Toast.LENGTH_SHORT).show()
            else importPlan(name, items)
        })
        content.addView(card)
    }

    private fun importPlan(name: String, items: List<Map<String, Any>>) {
        lifecycleScope.launch {
            try {
                val res = ApiClient.service.createPlan(CreatePlanRequest(name, items))
                if (res.ok) {
                    Toast.makeText(requireContext(), "已导入：${res.name ?: name}", Toast.LENGTH_SHORT).show()
                    PlanBuilderDraftHolder.clear()
                    if (openedFromAgent) {
                        findNavController().navigate(R.id.plansFragment)
                    } else {
                        findNavController().popBackStack()
                    }
                } else Toast.makeText(requireContext(), res.message ?: "导入失败", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) { Toast.makeText(requireContext(), "导入失败: ${e.message}", Toast.LENGTH_SHORT).show() }
        }
    }

    private fun selectedCategories(): List<ExerciseCategory> = categories.filter { selectedCategoryIds.contains(it.id) }
    private fun selectedOptions(): List<ExerciseOption> = categories.flatMap { it.options }.filter { selectedOptionIds.contains(it.id) }

    private fun labeledInput(
        ctx: android.content.Context,
        parent: LinearLayout,
        label: String,
        hintText: String,
        value: String,
        singleLine: Boolean = false,
        number: Boolean = false
    ): EditText {
        parent.addView(TextView(ctx).apply {
            text = label
            textSize = 14f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
            setPadding(0, UiKit.dp(ctx, 14), 0, UiKit.dp(ctx, 4))
        })
        return EditText(ctx).apply {
            hint = hintText
            setText(value)
            if (singleLine) setSingleLine(true)
            if (number) inputType = InputType.TYPE_CLASS_NUMBER
            background = roundedBg(ctx.getColor(R.color.surface), ctx.getColor(R.color.divider))
            setPadding(UiKit.dp(ctx, 12), 0, UiKit.dp(ctx, 12), 0)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 52))
            parent.addView(this)
        }
    }

    private fun primaryButton(textValue: String, onClick: () -> Unit): MaterialButton = MaterialButton(requireContext()).apply {
        text = textValue; textSize = 16f; cornerRadius = UiKit.dp(requireContext(), 24)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, UiKit.dp(requireContext(), 50)).apply { topMargin = UiKit.dp(requireContext(), 14) }
        setOnClickListener { onClick() }
    }

    private fun navRow(left: String, right: String, onLeft: () -> Unit, onRight: () -> Unit): LinearLayout {
        val ctx = requireContext()
        return LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, UiKit.dp(ctx, 14), 0, 0)
            addView(UiKit.outlinedButton(ctx, left, onLeft).apply { layoutParams = LinearLayout.LayoutParams(0, UiKit.dp(ctx, 50), 1f).apply { rightMargin = UiKit.dp(ctx, 8) } })
            addView(MaterialButton(ctx).apply {
                text = right; textSize = 15f; cornerRadius = UiKit.dp(ctx, 24)
                layoutParams = LinearLayout.LayoutParams(0, UiKit.dp(ctx, 50), 1.4f)
                setOnClickListener { onRight() }
            })
        }
    }

    private fun roundedBg(fill: Int, stroke: Int): GradientDrawable = GradientDrawable().apply {
        cornerRadius = UiKit.dp(requireContext(), 16).toFloat()
        setColor(fill)
        setStroke(UiKit.dp(requireContext(), 1), stroke)
    }

    private fun itemToMap(type: String, title: String, category: String, week: Int, day: Int, duration: Int, distance: Double, sets: Int, reps: Int, intensity: String, note: String): Map<String, Any> =
        mapOf("type" to type, "title" to title.ifBlank { type }, "category" to category, "week" to week, "day" to day, "duration_min" to duration, "distance_km" to distance, "sets" to sets, "reps" to reps, "intensity" to intensity, "note" to note)

    private fun renderLines(items: List<Map<String, Any>>): String = items.joinToString("\n") {
        listOf(it["day"] ?: 1, it["title"] ?: it["type"] ?: "", it["category"] ?: "custom", it["type"] ?: "custom", it["duration_min"] ?: 0, it["distance_km"] ?: 0, it["sets"] ?: 0, it["reps"] ?: 0, it["intensity"] ?: "", it["note"] ?: "").joinToString(",")
    }

    private fun parseLines(text: String): List<Map<String, Any>> = text.lines().mapNotNull { line ->
        val p = line.split(",", limit = 10).map { it.trim() }
        val title = p.getOrNull(1).orEmpty()
        val type = p.getOrNull(3).orEmpty().ifBlank { title.ifBlank { "custom" } }
        if (title.isBlank() && type.isBlank()) return@mapNotNull null
        itemToMap(type, title.ifBlank { type }, p.getOrNull(2).orEmpty().ifBlank { "custom" }, 1, p.getOrNull(0)?.toIntOrNull() ?: 1, p.getOrNull(4)?.toIntOrNull() ?: 0, p.getOrNull(5)?.toDoubleOrNull() ?: 0.0, p.getOrNull(6)?.toIntOrNull() ?: 0, p.getOrNull(7)?.toIntOrNull() ?: 0, p.getOrNull(8).orEmpty(), p.getOrNull(9).orEmpty())
    }
}
