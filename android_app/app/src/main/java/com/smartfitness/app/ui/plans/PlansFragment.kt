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
import com.google.android.material.button.MaterialButton
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.app.PlanIntent
import com.smartfitness.app.model.CreatePlanRequest
import com.smartfitness.app.model.WorkoutPlan
import kotlinx.coroutines.launch
import org.json.JSONArray

class PlansFragment : Fragment() {

    private lateinit var nameInput: EditText
    private lateinit var exercisesInput: EditText
    private lateinit var plansContainer: LinearLayout

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        val ctx = inflater.context
        return LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 48, 48, 48)
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )

            addView(TextView(ctx).apply {
                text = getString(R.string.add_plan)
                textSize = 20f
                setPadding(0, 0, 0, 16)
            })

            nameInput = EditText(ctx).apply {
                hint = "Plan name"
                inputType = InputType.TYPE_CLASS_TEXT
            }.also { addView(it) }

            exercisesInput = EditText(ctx).apply {
                hint = "Exercises JSON (e.g. [{\"type\":\"squat\",\"reps\":10}])"
                inputType = InputType.TYPE_CLASS_TEXT
            }.also { addView(it) }

            addView(MaterialButton(ctx).apply {
                text = getString(R.string.create)
                setOnClickListener { createPlan() }
            })

            addView(MaterialButton(ctx).apply {
                text = "✨ AI Generate Plan"
                setOnClickListener { showAiGenerateDialog() }
            })

            addView(MaterialButton(ctx).apply {
                text = getString(R.string.refresh)
                setOnClickListener { loadPlans() }
            })

            addView(TextView(ctx).apply {
                text = getString(R.string.my_plans)
                textSize = 20f
                setPadding(0, 32, 0, 16)
            })

            plansContainer = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
            }.also { addView(it) }
        }
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        loadPlans()
    }

    private fun createPlan() {
        val name = nameInput.text?.toString()?.trim().orEmpty()
        val ex = exercisesInput.text?.toString()?.trim().orEmpty().ifEmpty { "[]" }
        if (name.isEmpty()) {
            Toast.makeText(requireContext(), "Name required", Toast.LENGTH_SHORT).show()
            return
        }
        lifecycleScope.launch {
            try {
                val resp = ApiClient.service.createPlan(CreatePlanRequest(name, ex))
                if (resp.ok) {
                    Toast.makeText(requireContext(), "Plan created: ${resp.name}", Toast.LENGTH_SHORT).show()
                    nameInput.setText("")
                    exercisesInput.setText("")
                    loadPlans()
                } else {
                    Toast.makeText(requireContext(), resp.message ?: "Create failed", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Error: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun loadPlans() {
        lifecycleScope.launch {
            try {
                val list = ApiClient.service.listPlans().plans
                plansContainer.removeAllViews()
                if (list.isEmpty()) {
                    plansContainer.addView(TextView(requireContext()).apply {
                        text = getString(R.string.no_plans)
                    })
                } else {
                    list.forEach { plan ->
                        val ctx = requireContext()
                        val row = LinearLayout(ctx).apply {
                            orientation = LinearLayout.HORIZONTAL
                            setPadding(0, 16, 0, 16)
                        }
                        row.addView(TextView(ctx).apply {
                            text = plan.name
                            textSize = 16f
                            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                        })
                        // 一键开始训练
                        row.addView(MaterialButton(ctx).apply {
                            text = "开始训练"
                            setOnClickListener { startTrainingWithPlan(plan) }
                        })
                        row.addView(MaterialButton(ctx).apply {
                            text = getString(R.string.delete)
                            setOnClickListener {
                                lifecycleScope.launch {
                                    try {
                                        ApiClient.service.deletePlan(plan.planId)
                                        Toast.makeText(ctx, "Deleted", Toast.LENGTH_SHORT).show()
                                        loadPlans()
                                    } catch (e: Exception) {
                                        Toast.makeText(ctx, "Delete failed: ${e.message}", Toast.LENGTH_SHORT).show()
                                    }
                                }
                            }
                        })
                        plansContainer.addView(row)
                    }
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Load failed: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun startTrainingWithPlan(plan: com.smartfitness.app.model.WorkoutPlan) {
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
            hint = "Goal (e.g. fat-loss / strength / cardio)"
            setText("strength")
        }
        val weeksInput = EditText(ctx).apply {
            hint = "Weeks (1-8)"
            inputType = InputType.TYPE_CLASS_NUMBER
            setText("2")
        }
        container.addView(goalInput)
        container.addView(weeksInput)
        com.google.android.material.dialog.MaterialAlertDialogBuilder(ctx)
            .setTitle("✨ AI Generate Plan")
            .setMessage("LLM 会生成 N 周训练计划. 需 30-60 秒.")
            .setView(container)
            .setPositiveButton("Generate") { _, _ ->
                val goal = goalInput.text.toString().trim().ifEmpty { "strength" }
                val weeks = (weeksInput.text.toString().toIntOrNull() ?: 2).coerceIn(1, 8)
                doAiGenerate(goal, weeks)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun doAiGenerate(goal: String, weeks: Int) {
        val ctx = requireContext()
        val progress = android.app.ProgressDialog(ctx).apply {
            setMessage("LLM is generating... (~30s)")
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
                    // 把生成的计划全部写成一个 Plan 存起来
                    val name = "AI $goal x$weeks weeks"
                    val exercises = resp.plans.joinToString(",", prefix = "[", postfix = "]") {
                        """{"type":"${it.exerciseType}","sets":${it.targetSets},"reps":${it.targetReps},"note":"${it.intensityNote ?: ""}"}"""
                    }
                    ApiClient.service.createPlan(
                        com.smartfitness.app.model.CreatePlanRequest(name, exercises)
                    )
                    Toast.makeText(ctx, "AI plan created: ${resp.plans.size} days", Toast.LENGTH_LONG).show()
                    loadPlans()
                } else {
                    Toast.makeText(ctx, "AI generate failed: ${resp.message ?: ""}", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                progress.dismiss()
                Toast.makeText(ctx, "Error: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

}

