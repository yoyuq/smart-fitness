package com.smartfitness.app.ui.plans

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
import androidx.navigation.fragment.findNavController
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.app.PlanIntent
import com.smartfitness.app.model.CreatePlanRequest
import com.smartfitness.app.model.WorkoutPlan
import com.smartfitness.app.ui.UiKit
import kotlinx.coroutines.launch
import org.json.JSONArray

/**
 * 计划页 v2 (Keep 风格, Gemini 三轮评审方案):
 * 砍掉 JSON 输入框; AI 生成为超级主按钮; 创建空白计划为次级动作;
 * 列表卡片化(名称+动作数+开始胶囊); 下拉刷新替代 Refresh 按钮.
 */
class PlansFragment : Fragment() {

    private lateinit var nameInput: TextInputEditText
    private lateinit var plansContainer: LinearLayout
    private lateinit var swipe: SwipeRefreshLayout

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        val ctx = inflater.context
        val root = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16), UiKit.dp(ctx, 16))
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }

        with(root) {
            addView(TextView(ctx).apply {
                text = "训练计划"
                textSize = 18f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.on_surface))
                setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 8), 0, UiKit.dp(ctx, 16))
            })

            // 新建计划卡片
            UiKit.card(ctx).let { (cardView, inner) ->
                val til = TextInputLayout(
                    ctx, null,
                    com.google.android.material.R.attr.textInputOutlinedStyle
                ).apply {
                    hint = "给计划起个名字（如：夏日燃脂）"
                    boxStrokeColor = ctx.getColor(R.color.primary)
                    setBoxCornerRadii(
                        UiKit.dp(ctx, 12).toFloat(), UiKit.dp(ctx, 12).toFloat(),
                        UiKit.dp(ctx, 12).toFloat(), UiKit.dp(ctx, 12).toFloat()
                    )
                }
                nameInput = TextInputEditText(til.context).apply {
                    inputType = InputType.TYPE_CLASS_TEXT
                    textSize = 15f
                }
                til.addView(nameInput)
                inner.addView(til)

                inner.addView(TextView(ctx).apply {
                    text = "AI 会结合你的目标、身体数据和训练历史生成动作列表"
                    textSize = 13f
                    setTextColor(ctx.getColor(R.color.on_surface_secondary))
                    setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 8), 0, UiKit.dp(ctx, 4))
                })

                inner.addView(MaterialButton(ctx).apply {
                    text = "✨ AI 生成计划"
                    textSize = 16f
                    cornerRadius = UiKit.dp(ctx, 24)
                    layoutParams = LinearLayout.LayoutParams(
                        LinearLayout.LayoutParams.MATCH_PARENT, UiKit.dp(ctx, 48)
                    ).apply { topMargin = UiKit.dp(ctx, 8) }
                    setOnClickListener { showAiGenerateDialog() }
                })

                inner.addView(UiKit.outlinedButton(ctx, "创建空白计划") { createPlan() }.apply {
                    cornerRadius = UiKit.dp(ctx, 24)
                })
                addView(cardView)
            }

            addView(TextView(ctx).apply {
                text = "我的计划"
                textSize = 16f
                setTypeface(typeface, android.graphics.Typeface.BOLD)
                setTextColor(ctx.getColor(R.color.on_surface))
                setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 12), 0, UiKit.dp(ctx, 8))
            })

            plansContainer = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
            }.also { addView(it) }
        }

        val scroll = android.widget.ScrollView(ctx).apply {
            setBackgroundColor(ctx.getColor(R.color.bg))
            addView(root)
        }
        swipe = SwipeRefreshLayout(ctx).apply {
            setColorSchemeColors(ctx.getColor(R.color.primary))
            setOnRefreshListener { loadPlans() }
            addView(scroll)
        }
        return swipe
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        loadPlans()
    }

    private fun createPlan() {
        val name = nameInput.text?.toString()?.trim().orEmpty()
        if (name.isEmpty()) {
            Toast.makeText(requireContext(), "先给计划起个名字", Toast.LENGTH_SHORT).show()
            return
        }
        lifecycleScope.launch {
            try {
                val resp = ApiClient.service.createPlan(CreatePlanRequest(name, "[]"))
                if (resp.ok) {
                    Toast.makeText(requireContext(), "已创建: ${resp.name}", Toast.LENGTH_SHORT).show()
                    nameInput.setText("")
                    loadPlans()
                } else {
                    Toast.makeText(requireContext(), resp.message ?: "创建失败", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "错误: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun loadPlans() {
        swipe.isRefreshing = true
        lifecycleScope.launch {
            try {
                val list = ApiClient.service.listPlans().plans
                if (!isAdded) return@launch
                plansContainer.removeAllViews()
                val ctx = requireContext()
                if (list.isEmpty()) {
                    plansContainer.addView(TextView(ctx).apply {
                        text = "还没有计划, 用上面的 AI 一键生成一份吧"
                        textSize = 14f
                        setTextColor(ctx.getColor(R.color.on_surface_tertiary))
                        setPadding(UiKit.dp(ctx, 4), UiKit.dp(ctx, 8), 0, UiKit.dp(ctx, 8))
                    })
                } else {
                    list.forEach { plan -> plansContainer.addView(buildPlanCard(plan)) }
                }
            } catch (e: Exception) {
                if (isAdded) Toast.makeText(requireContext(), "加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            } finally {
                swipe.isRefreshing = false
            }
        }
    }

    private fun buildPlanCard(plan: WorkoutPlan): View {
        val ctx = requireContext()
        val (cardView, inner) = UiKit.card(ctx)
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
        }
        val itemCount = try { JSONArray(plan.exercises ?: "[]").length() } catch (_: Exception) { 0 }

        val textCol = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }
        textCol.addView(TextView(ctx).apply {
            text = plan.name
            textSize = 16f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setTextColor(ctx.getColor(R.color.on_surface))
        })
        textCol.addView(TextView(ctx).apply {
            text = "$itemCount 个动作"
            textSize = 12f
            setTextColor(ctx.getColor(R.color.on_surface_tertiary))
            setPadding(0, UiKit.dp(ctx, 2), 0, 0)
        })
        row.addView(textCol)

        row.addView(MaterialButton(ctx).apply {
            text = "开始"
            textSize = 13f
            cornerRadius = UiKit.dp(ctx, 18)
            minWidth = 0
            minimumWidth = 0
            setPadding(UiKit.dp(ctx, 20), 0, UiKit.dp(ctx, 20), 0)
            setOnClickListener { startTrainingWithPlan(plan) }
        })

        row.addView(MaterialButton(
            ctx, null,
            com.google.android.material.R.attr.borderlessButtonStyle
        ).apply {
            text = "删除"
            textSize = 13f
            setTextColor(ctx.getColor(R.color.on_surface_tertiary))
            minWidth = 0
            minimumWidth = 0
            setOnClickListener {
                lifecycleScope.launch {
                    try {
                        ApiClient.service.deletePlan(plan.planId)
                        loadPlans()
                    } catch (e: Exception) {
                        Toast.makeText(ctx, "删除失败: ${e.message}", Toast.LENGTH_SHORT).show()
                    }
                }
            }
        })
        inner.addView(row)
        return cardView
    }

    private fun startTrainingWithPlan(plan: WorkoutPlan) {
        var exType: String? = null
        var exReps: Int? = null
        try {
            val arr = JSONArray(plan.exercises ?: "[]")
            if (arr.length() > 0) {
                val first = arr.getJSONObject(0)
                exType = first.optString("type", null)
                exReps = first.optInt("reps", 0).takeIf { it > 0 }
            }
        } catch (_: Exception) {}
        PlanIntent.set(plan.planId, plan.name, exType, exReps)
        try {
            findNavController().navigate(R.id.trainingFragment)
        } catch (e: Exception) {
            Toast.makeText(requireContext(), "跳转失败: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun showAiGenerateDialog() {
        val ctx = requireContext()
        val container = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 24, 48, 0)
        }
        val goalInput = EditText(ctx).apply {
            hint = "目标 (如: 减脂 / 增肌 / 体能)"
            setText("增肌")
        }
        val weeksInput = EditText(ctx).apply {
            hint = "周数 (1-8)"
            inputType = InputType.TYPE_CLASS_NUMBER
            setText("2")
        }
        container.addView(goalInput)
        container.addView(weeksInput)
        com.google.android.material.dialog.MaterialAlertDialogBuilder(ctx)
            .setTitle("✨ AI 生成计划")
            .setMessage("AI 教练会结合你的数据生成渐进式计划, 约 30-60 秒")
            .setView(container)
            .setPositiveButton("生成") { _, _ ->
                val goal = goalInput.text.toString().trim().ifEmpty { "增肌" }
                val weeks = (weeksInput.text.toString().toIntOrNull() ?: 2).coerceIn(1, 8)
                doAiGenerate(goal, weeks)
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun doAiGenerate(goal: String, weeks: Int) {
        val ctx = requireContext()
        val progress = android.app.ProgressDialog(ctx).apply {
            setMessage("AI 教练正在生成计划… (~30 秒)")
            setCancelable(false)
            show()
        }
        lifecycleScope.launch {
            try {
                val resp = ApiClient.service.aiGeneratePlan(
                    com.smartfitness.app.model.AiPlanGenerateRequest(goal = goal, weeks = weeks)
                )
                progress.dismiss()
                if (resp.ok && resp.plans.isNotEmpty()) {
                    val name = "AI·$goal ${weeks}周"
                    val exercises = resp.plans.joinToString(",", prefix = "[", postfix = "]") {
                        """{"type":"${it.exerciseType}","sets":${it.targetSets},"reps":${it.targetReps},"note":"${(it.intensityNote ?: "").replace("\"", "")}"}"""
                    }
                    ApiClient.service.createPlan(
                        com.smartfitness.app.model.CreatePlanRequest(name, exercises)
                    )
                    Toast.makeText(ctx, "AI 计划已生成: ${resp.plans.size} 项", Toast.LENGTH_LONG).show()
                    loadPlans()
                } else {
                    Toast.makeText(ctx, "生成失败: ${resp.message ?: ""}", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                progress.dismiss()
                Toast.makeText(ctx, "错误: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }
}
