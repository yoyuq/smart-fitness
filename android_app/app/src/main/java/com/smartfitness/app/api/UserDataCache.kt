package com.smartfitness.app.api

import android.content.Context
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.smartfitness.app.model.*

object UserDataCache {
    private const val PREFS = "sf_user_data_cache"
    private val gson = Gson()

    private fun prefs(ctx: Context) = ctx.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun lastSyncMs(ctx: Context): Long = prefs(ctx).getLong("last_sync_ms", 0L)

    private inline fun <reified T> get(ctx: Context, key: String): T? {
        val raw = prefs(ctx).getString(key, null) ?: return null
        return try { gson.fromJson(raw, object : TypeToken<T>() {}.type) } catch (_: Exception) { null }
    }

    private fun put(ctx: Context, key: String, value: Any?) {
        if (value == null) return
        prefs(ctx).edit().putString(key, gson.toJson(value)).apply()
    }

    fun markSynced(ctx: Context) {
        prefs(ctx).edit().putLong("last_sync_ms", System.currentTimeMillis()).apply()
    }

    fun profile(ctx: Context): ProfileResponse? = get(ctx, "profile")
    fun statsDaily(ctx: Context): StatsResponse? = get(ctx, "stats_daily")
    fun statsWeekly(ctx: Context): StatsResponse? = get(ctx, "stats_weekly")
    fun latestMetric(ctx: Context): BodyMetricLatestResponse? = get(ctx, "latest_metric")
    fun plans(ctx: Context): PlansResponse? = get(ctx, "plans")
    fun exerciseSummary7(ctx: Context): ExerciseSummaryResponse? = get(ctx, "summary_7")
    fun exerciseLog30(ctx: Context): ExerciseLogListResponse? = get(ctx, "log_30")
    fun calendar(ctx: Context): CalendarResponse? = get(ctx, "calendar")
    fun streak(ctx: Context): StreakResponse? = get(ctx, "streak")
    fun achievements(ctx: Context): AchievementsResponse? = get(ctx, "achievements")
    fun trainingData(ctx: Context, period: String): TrainingDataResponse? = get(ctx, "training_$period")

    suspend fun syncAll(ctx: Context): SyncResult {
        var okCount = 0
        val service = ApiClient.service
        val p = prefs(ctx)
        fun save(key: String, value: Any?) {
            if (value != null) {
                p.edit().putString(key, gson.toJson(value)).apply()
                okCount++
            }
        }
        val profile = service.profile(); save("profile", profile)
        val daily = service.statsDaily(); save("stats_daily", daily)
        save("stats_weekly", service.statsWeekly())
        save("latest_metric", service.latestBodyMetric())
        save("plans", service.listPlans())
        save("summary_7", service.exerciseSummary(7))
        save("log_30", service.listExerciseLog(limit = 200, days = 30))
        save("calendar", service.calendarDays())
        save("streak", service.streak())
        save("achievements", service.achievements())
        listOf("day", "week", "month", "year").forEach { period ->
            save("training_$period", service.trainingData(period))
        }
        markSynced(ctx)
        val month = trainingData(ctx, "month")
        return SyncResult(
            okCount = okCount,
            totalReps = month?.summary?.totalReps ?: daily.stats?.totalReps ?: 0,
            sessions = month?.summary?.sessionsCount ?: daily.stats?.sessionsCount ?: 0
        )
    }
}

data class SyncResult(
    val okCount: Int,
    val totalReps: Int,
    val sessions: Int
)
