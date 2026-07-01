package com.smartfitness.app.app

import com.smartfitness.app.model.WorkoutPlan

/**
 * 跨 Fragment 轻量传递：PlansFragment 点击计划卡片 → 暂存选中的计划，
 * PlanDetailFragment onViewCreated 读取并渲染动作列表。
 */
object PlanDetailHolder {
    @Volatile var plan: WorkoutPlan? = null
}
