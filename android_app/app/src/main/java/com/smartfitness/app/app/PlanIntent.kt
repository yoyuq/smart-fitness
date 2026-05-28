package com.smartfitness.app.app

/**
 * \u8de8 Fragment \u8f7b\u91cf\u4f20\u9012\uff1aPlansFragment \u70b9\u51fb\u300c\u4e00\u952e\u5f00\u59cb\u300d\u540e\uff0c\n * TrainingFragment onResume() \u68c0\u67e5 \u5e76\u81ea\u52a8\u586b\u5145 sessionId\u3002
 */
object PlanIntent {
    @Volatile var planId: String? = null
    @Volatile var planName: String? = null
    @Volatile var firstExerciseType: String? = null  // \u9996\u9879\u52a8\u4f5c\u7c7b\u578b (squat/pushup/...)\uff0c\u4f9b\u63d0\u793a\u4f7f\u7528
    @Volatile var firstExerciseReps: Int? = null

    fun set(id: String, name: String, exType: String?, reps: Int?) {
        planId = id; planName = name; firstExerciseType = exType; firstExerciseReps = reps
    }

    /** \u8bfb\u53d6\u540e\u6e05\u96f6\uff0c\u907f\u514d\u4e0b\u6b21\u91cd\u590d\u4ec5\u9971\u4ee5\u540e\u7e7c\u7eed */
    fun consume(): Triple<String, String, Pair<String?, Int?>>? {
        val id = planId ?: return null
        val n = planName ?: return null
        val ex = firstExerciseType
        val r = firstExerciseReps
        planId = null; planName = null; firstExerciseType = null; firstExerciseReps = null
        return Triple(id, n, ex to r)
    }
}
