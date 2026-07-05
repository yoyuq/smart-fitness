package com.smartfitness.app.app

/**
 * 跨 Fragment 传递计划草稿：FitnessAgentFragment 生成 plan_draft 后，
 * 跳转到 PlanBuilderFragment 的第 5 步复用同一套草稿编辑/导入 UI。
 */
object PlanBuilderDraftHolder {
    @Volatile var name: String? = null
    @Volatile var goal: String? = null
    @Volatile var weeks: Int? = null
    @Volatile var reason: String? = null
    @Volatile var exercises: List<Map<String, Any>>? = null
    @Volatile var openFromAgent: Boolean = false

    fun setDraft(
        name: String?,
        goal: String?,
        weeks: Int?,
        reason: String?,
        exercises: List<Map<String, Any>>,
        openFromAgent: Boolean = false
    ) {
        this.name = name
        this.goal = goal
        this.weeks = weeks
        this.reason = reason
        this.exercises = exercises
        this.openFromAgent = openFromAgent
    }

    fun clear() {
        name = null
        goal = null
        weeks = null
        reason = null
        exercises = null
        openFromAgent = false
    }
}
