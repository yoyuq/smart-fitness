package com.smartfitness.app.ui.login

import android.app.AlertDialog
import android.content.Context
import android.text.InputType
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import com.google.android.material.button.MaterialButton
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.model.BodyMetricRequest
import com.smartfitness.app.model.CreatePlanRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Task 3: 注册新用户后的初次引导:
 *   1. 收集 年龄 / 性别 / 身高 / 体重 / 目标 (减脂/塑形/增肌/保持)
 *   2. POST /api/v2/metrics/body
 *   3. 根据目标 + BMI 自动生成第一个计划 (POST /api/v2/plans)
 */
object OnboardingHelper {

    private const val PREFS = "sf_onboarding"

    fun isCompleted(ctx: Context): Boolean =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("done", false)

    fun markCompleted(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("done", true).apply()
    }

    fun show(ctx: Context, scope: CoroutineScope, onFinish: () -> Unit) {
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 32, 48, 32)
        }
        fun addLabel(t: String) {
            root.addView(TextView(ctx).apply { text = t; textSize = 14f; setPadding(0, 16, 0, 4) })
        }
        addLabel("年龄")
        val ageEt = EditText(ctx).apply {
            hint = "如 22"; inputType = InputType.TYPE_CLASS_NUMBER
        }.also { root.addView(it) }
        addLabel("性别")
        val genderGroup = RadioGroup(ctx).apply { orientation = RadioGroup.HORIZONTAL }
        val rbM = RadioButton(ctx).apply { id = 1; text = "男" }
        val rbF = RadioButton(ctx).apply { id = 2; text = "女" }
        genderGroup.addView(rbM); genderGroup.addView(rbF); rbM.isChecked = true
        root.addView(genderGroup)
        addLabel("身高 (cm)")
        val hEt = EditText(ctx).apply {
            hint = "如 170"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        }.also { root.addView(it) }
        addLabel("体重 (kg)")
        val wEt = EditText(ctx).apply {
            hint = "如 55"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
        }.also { root.addView(it) }
        addLabel("目标")
        val goalGroup = RadioGroup(ctx).apply { orientation = RadioGroup.VERTICAL }
        val goals = listOf("减脂" to "fatloss", "塑形" to "tone", "增肌" to "muscle", "保持" to "maintain")
        goals.forEachIndexed { i, (zh, _) ->
            goalGroup.addView(RadioButton(ctx).apply { id = 100 + i; text = zh })
        }
        (goalGroup.getChildAt(0) as RadioButton).isChecked = true
        root.addView(goalGroup)

        AlertDialog.Builder(ctx)
            .setTitle("欢迎，先认识一下你 🐱")
            .setView(root)
            .setCancelable(false)
            .setPositiveButton("开始我的健身之旅") { _, _ ->
                val age = ageEt.text.toString().toIntOrNull()
                val isMale = genderGroup.checkedRadioButtonId == 1
                val h = hEt.text.toString().toDoubleOrNull()
                val w = wEt.text.toString().toDoubleOrNull()
                val goalIdx = (goalGroup.checkedRadioButtonId - 100).coerceIn(0, goals.size - 1)
                val goalCode = goals[goalIdx].second

                if (h == null || w == null) {
                    Toast.makeText(ctx, "身高/体重必填", Toast.LENGTH_SHORT).show()
                    return@setPositiveButton
                }
                scope.launch {
                    try {
                        // 1. 上传身体指标
                        withContext(Dispatchers.IO) {
                            ApiClient.service.addBodyMetric(BodyMetricRequest(weightKg = w, heightCm = h))
                        }
                        // 2. 生成第一个计划
                        val plan = recommendPlan(goalCode, w, h, age, isMale)
                        withContext(Dispatchers.IO) {
                            ApiClient.service.createPlan(CreatePlanRequest(plan.first, plan.second))
                        }
                        markCompleted(ctx)
                        Toast.makeText(ctx, "已为你定制：${plan.first}", Toast.LENGTH_LONG).show()
                    } catch (e: Exception) {
                        Toast.makeText(ctx, "初始化失败 (可在 Profile 重试): ${e.message}", Toast.LENGTH_LONG).show()
                    } finally {
                        onFinish()
                    }
                }
            }
            .setNegativeButton("稍后再说") { _, _ ->
                markCompleted(ctx)
                onFinish()
            }
            .show()
    }

    /** 返回 (plan_name, exercises_json) */
    fun recommendPlan(goal: String, weight: Double, heightCm: Double, age: Int?, isMale: Boolean): Pair<String, String> {
        val bmi = weight / Math.pow(heightCm / 100.0, 2.0)
        val low = bmi < 18.5
        val high = bmi >= 24
        // 根据目标 + 体型挑动作
        return when (goal) {
            "fatloss" -> {
                val exer = if (high) {
                    // 高 BMI: 低冲击有氧 + 核心
                    """[{"type":"squat","reps":15,"sets":3},{"type":"plank","duration_sec":30,"sets":3},{"type":"march_in_place","duration_sec":60,"sets":2}]"""
                } else {
                    // 正常: HIIT-ish
                    """[{"type":"squat","reps":20,"sets":3},{"type":"pushup","reps":10,"sets":3},{"type":"jump","reps":15,"sets":3}]"""
                }
                "减脂启动方案" to exer
            }
            "muscle" -> {
                val pushReps = if (isMale) 12 else 8
                val exer = if (low) {
                    // 偏瘦增肌: 大重量低次数
                    """[{"type":"pushup","reps":$pushReps,"sets":4},{"type":"squat","reps":12,"sets":4},{"type":"pullup","reps":6,"sets":3}]"""
                } else {
                    """[{"type":"pushup","reps":$pushReps,"sets":3},{"type":"squat","reps":15,"sets":3},{"type":"pullup","reps":8,"sets":3}]"""
                }
                "增肌基础方案" to exer
            }
            "tone" -> {
                "塑形维度方案" to """[{"type":"squat","reps":15,"sets":3},{"type":"lunge","reps":10,"sets":3},{"type":"plank","duration_sec":45,"sets":3},{"type":"pushup","reps":8,"sets":3}]"""
            }
            else -> {  // maintain
                "保持习惯方案" to """[{"type":"squat","reps":12,"sets":3},{"type":"pushup","reps":10,"sets":3},{"type":"plank","duration_sec":30,"sets":2}]"""
            }
        }
    }
}