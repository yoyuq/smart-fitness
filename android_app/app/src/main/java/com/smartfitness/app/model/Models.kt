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

data class PlanAiDraftRequest(
    val prompt: String,
    val weeks: Int = 2,
    @SerializedName("plan_name") val planName: String? = null,
    val categories: List<String> = emptyList(),
    @SerializedName("selected_items") val selectedItems: List<Map<String, String>> = emptyList(),
    @SerializedName("weekly_training_days") val weeklyTrainingDays: Int? = null,
    @SerializedName("session_minutes") val sessionMinutes: Int? = null
)

data class PlanExerciseItem(
    val type: String = "",
    val title: String = "",
    val category: String = "custom",
    val week: Int? = null,
    val day: Int? = null,
    val sets: Int = 0,
    val reps: Int = 0,
    @SerializedName("duration_min") val durationMin: Int = 0,
    @SerializedName("distance_km") val distanceKm: Double = 0.0,
    val intensity: String = "",
    val note: String = ""
)

data class PlanAiDraftResponse(
    val ok: Boolean = false,
    val draft: Boolean = true,
    val name: String? = null,
    val reason: String? = null,
    val exercises: List<PlanExerciseItem> = emptyList(),
    val message: String? = null
)

data class PlanCheckinRequest(
    val item: Map<String, Any>,
    val note: String? = null
)

data class PlanCheckinResponse(
    val ok: Boolean = false,
    val message: String? = null,
    @SerializedName("exercise_type") val exerciseType: String? = null
)

data class CreatePlanRequest(
    val name: String,
    val exercises: Any = "[]"
)

data class CreatePlanResponse(
    val ok: Boolean,
    @SerializedName("plan_id") val planId: String? = null,
    val name: String? = null,
    val message: String? = null,
    val plan: WorkoutPlan? = null
)

data class UpdatePlanRequest(
    val name: String? = null,
    val exercises: Any? = null
)

data class UpdatePlanResponse(
    val ok: Boolean = false,
    val plan: WorkoutPlan? = null,
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

data class TrainingDataResponse(
    val ok: Boolean,
    val period: String = "week",
    val since: Double = 0.0,
    val summary: TrainingDataSummary = TrainingDataSummary(),
    @SerializedName("by_type") val byType: List<ExerciseSummaryByType> = emptyList(),
    val sessions: List<TrainingDataSession> = emptyList(),
    val error: String? = null
)

data class TrainingDataSummary(
    @SerializedName("sessions_count") val sessionsCount: Int = 0,
    @SerializedName("total_reps") val totalReps: Int = 0,
    @SerializedName("total_minutes") val totalMinutes: Double = 0.0,
    @SerializedName("avg_score") val avgScore: Double = 0.0
)

data class TrainingDataSession(
    @SerializedName("session_id") val sessionId: String = "",
    @SerializedName("device_id") val deviceId: String? = null,
    @SerializedName("exercise_type") val exerciseType: String? = null,
    @SerializedName("start_time") val startTime: Double = 0.0,
    @SerializedName("end_time") val endTime: Double? = null,
    @SerializedName("total_reps") val totalReps: Int = 0,
    @SerializedName("avg_form_score") val avgFormScore: Double? = null,
    val status: String? = null,
    @SerializedName("rep_count") val repCount: Int = 0,
    @SerializedName("has_images") val hasImages: Boolean = false,
    val reps: List<TrainingRep> = emptyList()
)

data class TrainingRep(
    val id: Long = 0,
    @SerializedName("session_id") val sessionId: String? = null,
    @SerializedName("rep_index") val repIndex: Int? = null,
    val exercise: String? = null,
    val total: Double? = null,
    val depth: Double? = null,
    val control: Double? = null,
    val symmetry: Double? = null,
    @SerializedName("peak_angle") val peakAngle: Double? = null,
    @SerializedName("duration_s") val durationS: Double? = null,
    val feedback: String? = null,
    val ts: Double? = null,
    @SerializedName("true_label") val trueLabel: String? = null,
    @SerializedName("error_type") val errorType: String? = null,
    @SerializedName("has_images") val hasImages: Boolean = false,
    @SerializedName("image_count") val imageCount: Int = 0,
    val keyframes: List<String> = emptyList()
)


data class TrainingRepImageResponse(
    val ok: Boolean,
    val rep: TrainingRep? = null,
    val keyframes: List<String> = emptyList(),
    val frames: List<String> = emptyList(),
    @SerializedName("frame_count") val frameCount: Int = 0,
    @SerializedName("has_images") val hasImages: Boolean = false,
    val error: String? = null
)

// ==================== AI Coach two-stage analysis ====================

data class AiCoachFramesUsed(
    val requested: Int? = null,
    val actual: Int? = null,
    val indices: List<Int>? = null,
    @SerializedName("bottom_index") val bottomIndex: Int? = null,
    val source: String? = null
)

data class AiCoachObservation(
    @SerializedName("exercise_visible") val exerciseVisible: String? = null,
    @SerializedName("alignment_cues") val alignmentCues: List<String> = emptyList(),
    @SerializedName("observed_angles_deg") val observedAnglesDeg: Map<String, Any>? = null,
    val tempo: String? = null,
    val confidence: Double? = null,
    @SerializedName("camera_angle") val cameraAngle: String? = null,
    val provider: String? = null,
    val model: String? = null,
    @SerializedName("frames_sent") val framesSent: Int? = null
)

data class AiCoachIssue(
    val issue: String? = null,
    val severity: String? = null,
    val evidence: List<String>? = null
)

data class AiCoachPostureAssessment(
    val summary: String? = null,
    val issues: List<AiCoachIssue>? = null,
    val strengths: List<String>? = null
)

data class AiCoachCompletionScore(
    @SerializedName("overall_score") val overallScore: Double? = null,
    val depth: Double? = null,
    val control: Double? = null,
    val symmetry: Double? = null,
    val notes: String? = null
)

data class AiCoachGuidance(
    val action: String? = null,
    val cue: String? = null,
    val evidence: List<String>? = null,
    val target: String? = null
)

data class AiCoachAnalysis(
    @SerializedName("posture_assessment") val postureAssessment: AiCoachPostureAssessment? = null,
    @SerializedName("completion_score") val completionScore: AiCoachCompletionScore? = null,
    @SerializedName("immediate_next_rep") val immediateNextRep: List<AiCoachGuidance>? = null,
    @SerializedName("next_session") val nextSession: List<AiCoachGuidance>? = null,
    val cautions: List<String>? = null
)

data class AiCoachResponse(
    val ok: Boolean,
    @SerializedName("rep_id") val repId: Long? = null,
    @SerializedName("frames_used") val framesUsed: AiCoachFramesUsed? = null,
    @SerializedName("stage1_conflicts") val stage1Conflicts: List<Map<String, Any?>>? = null,
    val observation: AiCoachObservation? = null,
    val analysis: AiCoachAnalysis? = null,
    val note: String? = null,
    val error: String? = null
)

data class AiCoachRequest(
    val frames: Int? = null
)


// ==================== Session-level AI coach ====================

data class SessionAiCoachIssue(
    val issue: String? = null,
    val severity: String? = null,
    @SerializedName("affected_reps") val affectedReps: List<Int>? = null,
    val evidence: String? = null
)

data class SessionAiCoachOverallAssessment(
    val strengths: List<String>? = null,
    @SerializedName("common_issues") val commonIssues: List<SessionAiCoachIssue>? = null,
    val inconsistencies: List<String>? = null,
    @SerializedName("reps_with_concern") val repsWithConcern: List<Int>? = null,
    @SerializedName("overall_score") val overallScore: Double? = null,
    @SerializedName("performance_rating") val performanceRating: String? = null
)

data class SessionAiCoachGuidance(
    @SerializedName("immediate_corrections") val immediateCorrections: List<String>? = null,
    @SerializedName("next_session_focus") val nextSessionFocus: List<String>? = null,
    @SerializedName("progression_or_regression") val progressionOrRegression: String? = null,
    val cautions: List<String>? = null
)

data class SessionAiCoachStage1Result(
    @SerializedName("rep_index") val repIndex: Int? = null,
    val ok: Boolean? = null,
    val error: String? = null,
    val provider: String? = null,
    val model: String? = null
)

data class SessionAiCoachResponse(
    val ok: Boolean,
    @SerializedName("session_id") val sessionId: String? = null,
    val exercise: String? = null,
    @SerializedName("rep_count") val repCount: Int? = null,
    @SerializedName("reps_count") val repsCount: Int? = null,
    @SerializedName("overall_assessment") val overallAssessment: SessionAiCoachOverallAssessment? = null,
    @SerializedName("rep_by_rep_notes") val repByRepNotes: List<String>? = null,
    val guidance: SessionAiCoachGuidance? = null,
    @SerializedName("data_gaps") val dataGaps: List<String>? = null,
    val confidence: Double? = null,
    @SerializedName("stage1_results") val stage1Results: List<SessionAiCoachStage1Result>? = null,
    @SerializedName("stage1_ok_count") val stage1OkCount: Int? = null,
    @SerializedName("stage1_total") val stage1Total: Int? = null,
    val error: String? = null,
    val note: String? = null,
    @SerializedName("report_id") val reportId: String? = null,
    val saved: Boolean? = null
)

// AI Coach Reports archive
data class AiCoachReportSummary(
    @SerializedName("report_id") val reportId: String,
    @SerializedName("session_id") val sessionId: String? = null,
    val exercise: String? = null,
    @SerializedName("rep_count") val repCount: Int? = null,
    @SerializedName("frames_per_rep") val framesPerRep: Int? = null,
    @SerializedName("overall_score") val overallScore: Double? = null,
    @SerializedName("performance_rating") val performanceRating: String? = null,
    @SerializedName("stage1_ok_count") val stage1OkCount: Int? = null,
    @SerializedName("stage1_total") val stage1Total: Int? = null,
    val note: String? = null,
    @SerializedName("created_at") val createdAt: Double? = null
)

data class AiCoachReportListResponse(
    val ok: Boolean,
    val reports: List<AiCoachReportSummary> = emptyList()
)

data class AiCoachReportDetail(
    val ok: Boolean,
    @SerializedName("report_id") val reportId: String,
    @SerializedName("session_id") val sessionId: String? = null,
    val exercise: String? = null,
    @SerializedName("rep_count") val repCount: Int? = null,
    @SerializedName("frames_per_rep") val framesPerRep: Int? = null,
    @SerializedName("overall_score") val overallScore: Double? = null,
    @SerializedName("performance_rating") val performanceRating: String? = null,
    @SerializedName("stage1_ok_count") val stage1OkCount: Int? = null,
    @SerializedName("stage1_total") val stage1Total: Int? = null,
    val note: String? = null,
    @SerializedName("created_at") val createdAt: Double? = null,
    val report: SessionAiCoachResponse? = null
)

data class AiCoachReportNoteUpdate(val note: String? = null)
data class AiCoachReportUpdateResponse(
    val ok: Boolean,
    @SerializedName("report_id") val reportId: String? = null,
    val note: String? = null
)
data class AiCoachReportDeleteResponse(
    val ok: Boolean,
    @SerializedName("report_id") val reportId: String? = null,
    val deleted: Boolean? = null
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
    @SerializedName("import_to_plans") val importToPlans: Boolean = true,
)

data class AiPlanGenerateResponse(
    val ok: Boolean = false,
    val plans: List<AiPlanDay> = emptyList(),
    val message: String? = null,
    @SerializedName("plan_name") val planName: String? = null,
    @SerializedName("plan_id") val planId: String? = null,
    val draft: Boolean? = null,
)

data class AiPlanDay(
    val week: Int = 1,
    val day: Int = 1,
    @com.google.gson.annotations.SerializedName("exercise_type") val exerciseType: String = "",
    @com.google.gson.annotations.SerializedName("target_reps") val targetReps: Int = 0,
    @com.google.gson.annotations.SerializedName("target_sets") val targetSets: Int = 0,
    @com.google.gson.annotations.SerializedName("intensity_note") val intensityNote: String? = null,
)

data class AgentChatRequest(
    val message: String,
    val mode: String = "auto",
    val history: List<AgentChatMessage> = emptyList()
)

data class AgentNutritionPlanRequest(
    val goal: String = "维持训练表现并优化体成分"
)

data class AgentChatMessage(
    val role: String,
    val content: String
)

data class AgentChatHistoryMessage(
    val id: Long = 0,
    val role: String = "user",
    val content: String = "",
    val mode: String = "auto",
    val domains: List<String> = emptyList(),
    @SerializedName("created_at") val createdAt: Long? = null
)

data class AgentChatHistoryResponse(
    val ok: Boolean = false,
    val messages: List<AgentChatHistoryMessage> = emptyList(),
    val error: String? = null
)

data class AgentToolApproval(
    @SerializedName("approval_id") val approvalId: String = "",
    @SerializedName("tool_name") val toolName: String = "",
    val args: Map<String, Any?> = emptyMap(),
    val reason: String = "",
    val status: String = "pending",
    val summary: String = "",
    @SerializedName("created_at") val createdAt: Long? = null,
    @SerializedName("run_id") val runId: String? = null
)

data class AgentApprovalListResponse(
    val ok: Boolean = false,
    val approvals: List<AgentToolApproval> = emptyList(),
    val error: String? = null
)

data class AgentApprovalActionResponse(
    val ok: Boolean = false,
    @SerializedName("approval_id") val approvalId: String? = null,
    val result: Map<String, Any?>? = null,
    val message: String? = null,
    val error: String? = null,
    val reply: String? = null,
    @SerializedName("run_id") val runId: String? = null,
    @SerializedName("run_status") val runStatus: String? = null
)

data class AgentPlanExercise(
    val type: String = "",
    val title: String = "",
    val category: String = "custom",
    val sets: Int = 0,
    val reps: Int = 0,
    val note: String = "",
    val week: Int? = null,
    val day: Int? = null,
    @SerializedName("duration_min") val durationMin: Int = 0,
    @SerializedName("distance_km") val distanceKm: Double = 0.0,
    val intensity: String = ""
)

data class AgentPlanDraft(
    val name: String = "Agent 生成计划",
    val goal: String? = null,
    val weeks: Int? = null,
    val reason: String? = null,
    val exercises: List<AgentPlanExercise> = emptyList()
)

data class AgentChatResponse(
    val ok: Boolean = false,
    val mode: String? = null,
    val domains: List<String> = emptyList(),
    val reply: String? = null,
    @SerializedName("pending_approvals") val pendingApprovals: List<AgentToolApproval> = emptyList(),
    @SerializedName("run_id") val runId: String? = null,
    @SerializedName("run_status") val runStatus: String? = null,
    @SerializedName("agent_loop") val agentLoop: AgentLoopInfo? = null,
    @SerializedName("plan_draft") val planDraft: AgentPlanDraft? = null,
    val error: String? = null
)

data class AgentLoopInfo(
    val enabled: Boolean = false,
    val turns: Int? = null,
    @SerializedName("forced_tool") val forcedTool: Boolean? = null,
    val fallback: Boolean? = null,
    val todos: List<AgentTodo> = emptyList(),
    val recovery: List<AgentRecoveryEvent> = emptyList(),
    @SerializedName("max_turns_reached") val maxTurnsReached: Boolean? = null,
    @SerializedName("total_timeout_reached") val totalTimeoutReached: Boolean? = null
)

data class AgentRecoveryEvent(
    val event: String = "",
    val provider: String? = null,
    val tool: String? = null,
    val reason: String? = null,
    @SerializedName("error_type") val errorType: String? = null,
    val message: String? = null,
    @SerializedName("circuit_opened") val circuitOpened: Boolean? = null,
    @SerializedName("cooldown_until") val cooldownUntil: Long? = null,
    val ok: Boolean? = null,
    val turns: Int? = null
)

data class AgentTodo(
    val content: String = "",
    val status: String = "pending"
)

data class AgentRunListResponse(
    val ok: Boolean = false,
    val runs: List<AgentRun> = emptyList(),
    val error: String? = null
)

data class AgentRunDetailResponse(
    val ok: Boolean = false,
    val run: AgentRun? = null,
    val error: String? = null
)

data class AgentRun(
    @SerializedName("run_id") val runId: String = "",
    val status: String = "",
    val mode: String? = null,
    @SerializedName("user_message") val userMessage: String? = null,
    @SerializedName("final_text") val finalText: String? = null,
    val domains: List<String> = emptyList(),
    val todos: List<AgentTodo> = emptyList(),
    val trace: List<Any> = emptyList(),
    val error: Any? = null,
    @SerializedName("pending_approval_ids") val pendingApprovalIds: List<String> = emptyList(),
    @SerializedName("created_at") val createdAt: Long? = null,
    @SerializedName("updated_at") val updatedAt: Long? = null,
    @SerializedName("completed_at") val completedAt: Long? = null
)

data class AgentHealthResponse(
    val ok: Boolean = false,
    val providers: List<AgentProviderHealth> = emptyList(),
    val recent: AgentRecentStats? = null,
    val error: String? = null
)

data class AgentKnowledgeResponse(
    val ok: Boolean = false,
    val domains: List<Map<String, Any>> = emptyList(),
    val error: String? = null
)

data class AgentProviderHealth(
    val provider: String = "",
    @SerializedName("consecutive_failures") val consecutiveFailures: Int = 0,
    @SerializedName("cooldown_until") val cooldownUntil: Long = 0,
    @SerializedName("last_error_type") val lastErrorType: String? = null,
    @SerializedName("success_count") val successCount: Int = 0,
    @SerializedName("failure_count") val failureCount: Int = 0,
    @SerializedName("cooling_down") val coolingDown: Boolean = false
)

data class AgentRecentStats(
    @SerializedName("window_sec") val windowSec: Int = 3600,
    @SerializedName("total_runs") val totalRuns: Int = 0,
    @SerializedName("by_status") val byStatus: Map<String, Int> = emptyMap(),
    val events: Map<String, Int> = emptyMap()
)

data class AgentBackgroundRunRequest(
    val job: String = "daily_checkin"
)

data class AgentBackgroundItemListResponse(
    val ok: Boolean = false,
    val items: List<AgentBackgroundItem> = emptyList(),
    val error: String? = null
)

data class AgentBackgroundRunResponse(
    val ok: Boolean = false,
    val job: String? = null,
    val created: Int = 0,
    val items: List<AgentBackgroundItem> = emptyList(),
    val error: String? = null
)

data class AgentBackgroundItem(
    @SerializedName("item_id") val itemId: String = "",
    @SerializedName("user_id") val userId: Long? = null,
    val job: String = "",
    val kind: String = "",
    val title: String = "",
    val message: String = "",
    val status: String = "pending",
    @SerializedName("requires_approval") val requiresApproval: Boolean = false,
    @SerializedName("created_at") val createdAt: Long? = null,
    @SerializedName("updated_at") val updatedAt: Long? = null,
    @SerializedName("read_at") val readAt: Long? = null,
    val payload: Map<String, Any?> = emptyMap()
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
