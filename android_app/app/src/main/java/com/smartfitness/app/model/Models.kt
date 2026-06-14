package com.smartfitness.app.model

import com.google.gson.annotations.SerializedName

// ---------------- Auth ----------------

data class RegisterRequest(
    val username: String,
    val password: String,
    @SerializedName("device_id") val deviceId: String
)

data class LoginRequest(
    val username: String,
    val password: String
)

data class AuthResponse(
    val ok: Boolean,
    val token: String? = null,
    @SerializedName("user_id") val userId: Long? = null,
    val username: String? = null,
    val message: String? = null
)

data class ProfileResponse(
    val ok: Boolean,
    val user: UserInfo? = null
)

data class UserInfo(
    val id: Long,
    val username: String,
    @SerializedName("display_name") val displayName: String?,
    val avatar: String?,
    @SerializedName("created_at") val createdAt: Double? = null  // Unix timestamp float
)

// ---------------- Devices ----------------

data class DevicesResponse(
    val ok: Boolean,
    val devices: List<Device> = emptyList()
)

data class Device(
    @SerializedName("device_id") val deviceId: String = "",
    @SerializedName("device_name") val deviceName: String? = null,
    @SerializedName("device_type") val deviceType: String? = null,
    @SerializedName("user_id") val userId: Long? = null,
    @SerializedName("is_active") val isActive: Int? = null,
    @SerializedName("last_seen") val lastSeen: Double? = null
)

data class DeviceRegisterRequest(
    @SerializedName("device_name") val deviceName: String,
    @SerializedName("device_type") val deviceType: String
)

data class DeviceRegisterResponse(
    val ok: Boolean,
    @SerializedName("device_id") val deviceId: String? = null,
    val message: String? = null
)

// ---------------- Sessions ----------------

data class SessionHistoryResponse(
    val sessions: List<TrainingSession> = emptyList()
)

data class TrainingSession(
    @SerializedName("session_id") val sessionId: String = "",
    @SerializedName("device_id") val deviceId: String? = null,
    @SerializedName("user_id") val userId: Long? = null,
    @SerializedName("exercise_type") val exerciseType: String? = null,
    @SerializedName("start_time") val startTime: Double? = null,
    @SerializedName("end_time") val endTime: Double? = null,
    @SerializedName("total_reps") val totalReps: Int? = null,
    @SerializedName("avg_form_score") val avgFormScore: Double? = null,
    val status: String? = null
)

// ---------------- Plans ----------------

data class CreatePlanRequest(
    val name: String,
    val exercises: String = "[]"
)

data class CreatePlanResponse(
    val ok: Boolean,
    @SerializedName("plan_id") val planId: String? = null,
    val name: String? = null,
    val message: String? = null
)

data class PlansResponse(
    val plans: List<WorkoutPlan> = emptyList()
)

data class WorkoutPlan(
    @SerializedName("plan_id") val planId: String,
    val name: String,
    val exercises: String? = null,
    @SerializedName("created_at") val createdAt: Double? = null
)

data class DeletePlanResponse(
    val ok: Boolean,
    val message: String? = null
)

// ---------------- Stats ----------------

data class StatsResponse(
    val ok: Boolean,
    val stats: StatsData? = null
)

data class StatsData(
    @SerializedName("sessions_count") val sessionsCount: Int = 0,
    @SerializedName("total_reps") val totalReps: Int = 0,
    @SerializedName("total_minutes") val totalMinutes: Double = 0.0,
    @SerializedName("avg_score") val avgScore: Double = 0.0,
    val sessions: List<TrainingSession> = emptyList()
)

// ---------------- WebSocket ----------------

data class WsMessage(
    val type: String,
    @SerializedName("session_id") val sessionId: String? = null,
    @SerializedName("device_id") val deviceId: String? = null,
    @SerializedName("exercise_type") val exerciseType: String? = null,
    @SerializedName("form_score") val formScore: Int? = null,
    @SerializedName("rep_count") val repCount: Int? = null,
    val timestamp: Double? = null,
    val message: String? = null
)

// ---------------- D-03 Body Metrics ----------------
data class BodyMetricRequest(
    @SerializedName("weight_kg") val weightKg: Double? = null,
    @SerializedName("height_cm") val heightCm: Double? = null,
    @SerializedName("body_fat_pct") val bodyFatPct: Double? = null,
    @SerializedName("resting_hr") val restingHr: Int? = null,
    val notes: String? = null
)

data class BodyMetric(
    val id: Long? = null,
    val timestamp: Double? = null,
    @SerializedName("weight_kg") val weightKg: Double? = null,
    @SerializedName("height_cm") val heightCm: Double? = null,
    @SerializedName("body_fat_pct") val bodyFatPct: Double? = null,
    @SerializedName("resting_hr") val restingHr: Int? = null,
    val notes: String? = null,
    val bmi: Double? = null
)

data class BodyMetricLatestResponse(
    val ok: Boolean,
    val latest: BodyMetric? = null
)

data class BodyMetricListResponse(
    val ok: Boolean,
    val metrics: List<BodyMetric> = emptyList()
)

// ---------------- D-04 Exercise Log ----------------
data class ExerciseLogRequest(
    @SerializedName("exercise_type") val exerciseType: String,
    val reps: Int = 0,
    val sets: Int = 1,
    @SerializedName("duration_seconds") val durationSeconds: Double = 0.0,
    @SerializedName("avg_form_score") val avgFormScore: Double? = null,
    @SerializedName("calories_kcal") val caloriesKcal: Double? = null,
    @SerializedName("session_id") val sessionId: String? = null
)

data class ExerciseLogEntry(
    val id: Long? = null,
    @SerializedName("exercise_type") val exerciseType: String = "",
    val reps: Int = 0,
    val sets: Int = 1,
    @SerializedName("duration_seconds") val durationSeconds: Double = 0.0,
    @SerializedName("avg_form_score") val avgFormScore: Double? = null,
    @SerializedName("performed_at") val performedAt: Double = 0.0
)

data class ExerciseLogListResponse(val ok: Boolean, val log: List<ExerciseLogEntry> = emptyList())

data class ExerciseSummaryByType(
    @SerializedName("exercise_type") val exerciseType: String,
    @SerializedName("total_reps") val totalReps: Int = 0,
    val sessions: Int = 0,
    @SerializedName("total_seconds") val totalSeconds: Double = 0.0,
    @SerializedName("avg_form") val avgForm: Double? = null
)

data class ExerciseSummaryResponse(
    val ok: Boolean,
    val days: Int = 7,
    @SerializedName("by_type") val byType: List<ExerciseSummaryByType> = emptyList()
)

// ---------------- D-05 Device Bind ----------------
data class BindDeviceRequest(
    @SerializedName("device_id") val deviceId: String,
    val name: String? = null
)

data class BindDeviceResponse(
    val ok: Boolean,
    @SerializedName("device_id") val deviceId: String? = null,
    val token: String? = null,
    val message: String? = null
)

data class DeviceBinding(
    @SerializedName("device_id") val deviceId: String = "",
    @SerializedName("bound_at") val boundAt: Double? = null,
    @SerializedName("last_used_at") val lastUsedAt: Double? = null,
    val active: Int = 0
)

data class BindingListResponse(
    val ok: Boolean,
    val bindings: List<DeviceBinding> = emptyList()
)

data class GenericOkResponse(val ok: Boolean, val message: String? = null)

// ---------------- B-08 Coach WS payload ----------------
data class CoachUpdate(
    val type: String? = null,
    @SerializedName("session_id") val sessionId: String? = null,
    val timestamp: Double? = null,
    @SerializedName("exercise_type") val exerciseType: String? = null,
    @SerializedName("rep_count") val repCount: Int? = null,
    @SerializedName("form_score") val formScore: Double? = null,
    @SerializedName("coach_tip") val coachTip: String? = null,
    @SerializedName("form_feedback") val formFeedback: List<FormFeedback> = emptyList(),
    @SerializedName("plan_match") val planMatch: PlanMatch? = null,
    @SerializedName("body_context") val bodyContext: BodyContext? = null,
    // 后端同名字段兼容 (snake_case: exercise / feedback / detected / image_b64 / landmarks)
    val exercise: String? = null,
    val feedback: String? = null,
    val detected: Boolean? = null,
    @SerializedName("image_b64") val imageB64: String? = null,
    val landmarks: List<Map<String, Any?>>? = null,
    val ts: Double? = null
)

data class FormFeedback(
    val severity: String? = null,
    @SerializedName("message_cn") val messageCn: String? = null,
    @SerializedName("message_en") val messageEn: String? = null,
    @SerializedName("affected_angle") val affectedAngle: String? = null
)

data class PlanMatch(
    @SerializedName("plan_id") val planId: String? = null,
    @SerializedName("plan_name") val planName: String? = null,
    val matched: Boolean? = null
)

data class BodyContext(
    val bmi: Double? = null,
    @SerializedName("weight_kg") val weightKg: Double? = null,
    @SerializedName("height_cm") val heightCm: Double? = null,
    @SerializedName("recommended_intensity") val recommendedIntensity: String? = null
)

// ---------------- B-07 Vision Infer (Full) ----------------
data class VisionInferRequest(
    val image: String,                                  // base64 JPEG
    @SerializedName("device_id") val deviceId: String? = null,
    @SerializedName("session_id") val sessionId: String? = null,
    @SerializedName("user_id") val userId: Long? = null,
    val backend: String? = null,                        // "mediapipe" | "yolo"
    @SerializedName("exercise") val exercise: String? = null,  // target exercise from spinner
    @SerializedName("source") val source: String? = null       // esp32cam | phone | pc
)

data class VisionLandmark(
    val id: Int? = null,
    val name: String? = null,
    val x: Double? = null,
    val y: Double? = null,
    val z: Double? = null,
    val visibility: Double? = null,
    @SerializedName("pixel_x") val pixelX: Int? = null,
    @SerializedName("pixel_y") val pixelY: Int? = null
)

data class VisionInferResponse(
    val ok: Boolean = false,
    val detected: Boolean? = null,
    val landmarks: List<VisionLandmark> = emptyList(),
    val angles: Map<String, Double?> = emptyMap(),
    @SerializedName("inference_ms") val inferenceMs: Double? = null,
    @SerializedName("exercise_type") val exerciseType: String? = null,
    @SerializedName("rep_count") val repCount: Int? = null,
    @SerializedName("form_score") val formScore: Double? = null,
    @SerializedName("form_feedback") val formFeedback: List<FormFeedback> = emptyList(),
    @SerializedName("coach_tip") val coachTip: String? = null,
    @SerializedName("user_id") val userId: Long? = null,
    @SerializedName("plan_match") val planMatch: PlanMatch? = null,
    @SerializedName("body_context") val bodyContext: BodyContext? = null,
    val error: String? = null
)

data class InferSummary(
    val status: String? = null,       // ok | needs_correction | no_pose | unauthorized | rate_limited | error
    val level: String? = null,        // info | warn | bad
    @SerializedName("text_cn") val textCn: String? = null,
    @SerializedName("tts_hint") val ttsHint: Boolean? = null
)

data class VisionInferFullResponse(
    val ok: Boolean = false,
    val detected: Boolean? = null,
    val landmarks: List<VisionLandmark> = emptyList(),
    val angles: Map<String, Double?> = emptyMap(),
    @SerializedName("inference_ms") val inferenceMs: Double? = null,
    @SerializedName("exercise_type") val exerciseType: String? = null,
    @SerializedName("rep_count") val repCount: Int? = null,
    @SerializedName("form_score") val formScore: Double? = null,
    @SerializedName("form_feedback") val formFeedback: List<FormFeedback> = emptyList(),
    @SerializedName("coach_tip") val coachTip: String? = null,
    @SerializedName("user_id") val userId: Long? = null,
    @SerializedName("plan_match") val planMatch: PlanMatch? = null,
    @SerializedName("body_context") val bodyContext: BodyContext? = null,
    val summary: InferSummary? = null,
    val feedback: String? = null,
    val error: String? = null
)


// ---------- Training Control (\u63a7\u5236 ESP32 \u5f00\u59cb/\u505c\u6b62) ----------
data class TrainingStartRequest(
    @SerializedName("device_id") val deviceId: String,
    val exercise: String,
    @SerializedName("user_id") val userId: Long? = null,
    val source: String? = null,
    @SerializedName("session_id") val sessionId: String? = null,
    val mode: String? = null   // guidance(指导动作) | complete(完整运动)
)

data class TrainingStopRequest(
    @SerializedName("device_id") val deviceId: String
)

data class TrainingActiveItem(
    @SerializedName("device_id") val deviceId: String? = null,
    @SerializedName("user_id") val userId: Long? = null,
    val exercise: String? = null,
    @SerializedName("session_id") val sessionId: String? = null,
    @SerializedName("started_at") val startedAt: Double? = null
)

data class TrainingStartResponse(
    val ok: Boolean = false,
    val active: TrainingActiveItem? = null,
    @SerializedName("session_id") val sessionId: String? = null,
    val mode: String? = null,
    val error: String? = null
)

data class TrainingActiveResponse(
    val ok: Boolean = false,
    val items: List<TrainingActiveItem> = emptyList(),
    val error: String? = null
)


// =============================================================
// Workout Summary (post-training dialog) - 2026-05-28
// =============================================================
data class WorkoutSummaryRequest(
    @com.google.gson.annotations.SerializedName("device_id") val deviceId: String,
    val exercise: String,
    val reps: Int,
    @com.google.gson.annotations.SerializedName("duration_s") val durationS: Double,
    @com.google.gson.annotations.SerializedName("avg_form_score") val avgFormScore: Double? = null,
)

data class WorkoutSummaryResponse(
    val ok: Boolean,
    val totals: WorkoutTotals? = null,
    @com.google.gson.annotations.SerializedName("coach_remark") val coachRemark: String? = null,
    val badges: List<WorkoutBadge> = emptyList(),
    @com.google.gson.annotations.SerializedName("kcal_est") val kcalEst: Double? = null,
)

data class WorkoutTotals(
    val reps: Int = 0,
    @com.google.gson.annotations.SerializedName("duration_s") val durationS: Double = 0.0,
    @com.google.gson.annotations.SerializedName("avg_form_score") val avgFormScore: Double? = null,
    val exercise: String = "",
)

data class WorkoutBadge(
    val name: String = "",
    val icon: String? = null,
)

// =============================================================
// Calendar heatmap (Profile page) - 2026-05-28
// =============================================================
data class CalendarResponse(
    val days: List<CalendarDay> = emptyList(),
)

data class CalendarDay(
    val d: String = "",
    val reps: Int? = null,
    val dur: Double? = null,
    val sessions: Int? = null,
)


// =============================================================
// Personal Best / Streak / Achievements (2026-05-28 v8)
// =============================================================
data class PersonalBestResponse(
    val ok: Boolean = false,
    val pb: List<PersonalBest> = emptyList(),
)

data class PersonalBest(
    @com.google.gson.annotations.SerializedName("exercise_type") val exerciseType: String? = null,
    @com.google.gson.annotations.SerializedName("best_reps") val bestReps: Int? = null,
    @com.google.gson.annotations.SerializedName("best_form") val bestForm: Double? = null,
    @com.google.gson.annotations.SerializedName("longest_s") val longestS: Double? = null,
    @com.google.gson.annotations.SerializedName("total_sessions") val totalSessions: Int? = null,
)

data class StreakResponse(
    val ok: Boolean = false,
    @com.google.gson.annotations.SerializedName("current_streak") val currentStreak: Int = 0,
    @com.google.gson.annotations.SerializedName("longest_streak") val longestStreak: Int = 0,
    @com.google.gson.annotations.SerializedName("last_active") val lastActive: String? = null,
)

data class AchievementsResponse(
    val ok: Boolean = false,
    val achievements: List<Achievement> = emptyList(),
    val stats: AchievementStats? = null,
)

data class Achievement(
    val id: String = "",
    val name: String = "",
    val desc: String = "",
    val icon: String? = null,
    val unlocked: Boolean = false,
)

data class AchievementStats(
    @com.google.gson.annotations.SerializedName("total_reps") val totalReps: Int = 0,
    @com.google.gson.annotations.SerializedName("total_sessions") val totalSessions: Int = 0,
    @com.google.gson.annotations.SerializedName("total_duration_s") val totalDurationS: Double = 0.0,
    @com.google.gson.annotations.SerializedName("max_single_reps") val maxSingleReps: Int = 0,
    @com.google.gson.annotations.SerializedName("unique_exercises") val uniqueExercises: Int = 0,
)


// =============================================================
// AI Plan Generate (2026-05-28 v8)
// =============================================================
data class AiPlanGenerateRequest(
    val goal: String,
    val weeks: Int,
)

data class AiPlanGenerateResponse(
    val ok: Boolean = false,
    val plans: List<AiPlanDay> = emptyList(),
    val message: String? = null,
)

data class AiPlanDay(
    val week: Int = 1,
    val day: Int = 1,
    @com.google.gson.annotations.SerializedName("exercise_type") val exerciseType: String = "",
    @com.google.gson.annotations.SerializedName("target_reps") val targetReps: Int = 0,
    @com.google.gson.annotations.SerializedName("target_sets") val targetSets: Int = 0,
    @com.google.gson.annotations.SerializedName("intensity_note") val intensityNote: String? = null,
)

// =============================================================
// AI Coach Butler (2026-06-11)
// =============================================================
data class CoachReview(
    val trend: String? = null,
    val balance: String? = null,
    val weakness: String? = null,
    val adherence: String? = null,
    @com.google.gson.annotations.SerializedName("next_week") val nextWeek: List<String>? = null,
    val encouragement: String? = null,
)

data class CoachReviewResponse(
    val ok: Boolean = false,
    val review: CoachReview? = null,
    @com.google.gson.annotations.SerializedName("review_text") val reviewText: String? = null,
    @com.google.gson.annotations.SerializedName("memory_saved") val memorySaved: List<String>? = null,
    val error: String? = null,
)

data class CoachMemoryItem(
    val id: Long = 0,
    val category: String? = null,
    val note: String = "",
    @com.google.gson.annotations.SerializedName("created_at") val createdAt: Long? = null,
)

data class CoachMemoryListResponse(
    val ok: Boolean = false,
    val memories: List<CoachMemoryItem> = emptyList(),
)

data class CoachMemoryAddRequest(
    val note: String,
    val category: String = "general",
)

// =============================================================
// Workout Report (mode 2 完整运动报告) - 2026-06-14
// =============================================================
data class WorkoutReportRequest(
    @com.google.gson.annotations.SerializedName("session_id") val sessionId: String
)

data class WorkoutReport(
    val summary: String? = null,
    val highlights: String? = null,
    val problems: String? = null,
    @com.google.gson.annotations.SerializedName("vs_history") val vsHistory: String? = null,
    val recommendations: List<String>? = null,
    val encouragement: String? = null
)

data class WorkoutReportSession(
    val exercise: String? = null,
    @com.google.gson.annotations.SerializedName("total_reps") val totalReps: Int? = null,
    @com.google.gson.annotations.SerializedName("avg_score") val avgScore: Double? = null,
    @com.google.gson.annotations.SerializedName("duration_min") val durationMin: Double? = null,
    val issues: List<String>? = null
)

data class WorkoutReportResponse(
    val ok: Boolean = false,
    val report: WorkoutReport? = null,
    @com.google.gson.annotations.SerializedName("report_text") val reportText: String? = null,
    val session: WorkoutReportSession? = null,
    val error: String? = null
)
