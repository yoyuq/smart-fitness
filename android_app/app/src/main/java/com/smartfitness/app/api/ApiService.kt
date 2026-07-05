package com.smartfitness.app.api

import com.smartfitness.app.model.*
import retrofit2.http.*

interface ApiService {

    // ---------- Auth ----------

    @POST("api/v2/auth/register")
    suspend fun register(@Body req: RegisterRequest): AuthResponse

    @POST("api/v2/auth/login")
    suspend fun login(@Body req: LoginRequest): AuthResponse

    @GET("api/v2/auth/profile")
    suspend fun profile(): ProfileResponse

    // ---------- Devices ----------

    @GET("api/v2/devices")
    suspend fun listDevices(): DevicesResponse

    @POST("api/v2/devices/register")
    suspend fun registerDevice(@Body req: DeviceRegisterRequest): DeviceRegisterResponse

    // ---------- Sessions ----------

    @GET("api/v2/sessions/history")
    suspend fun sessionHistory(@Query("user_id") userId: Long? = null): SessionHistoryResponse

    // ---------- Plans ----------

    @POST("api/v2/plans")
    suspend fun createPlan(@Body req: CreatePlanRequest): CreatePlanResponse

    @GET("api/v2/plans")
    suspend fun listPlans(): PlansResponse

    @POST("api/v2/plans/ai_draft")
    suspend fun draftPlanWithAi(@Body req: PlanAiDraftRequest): PlanAiDraftResponse

    @POST("api/v2/plans/{plan_id}/checkin")
    suspend fun checkinPlanItem(@Path("plan_id") planId: String, @Body req: PlanCheckinRequest): PlanCheckinResponse

    @PUT("api/v2/plans/{plan_id}")
    suspend fun updatePlan(@Path("plan_id") planId: String, @Body req: UpdatePlanRequest): UpdatePlanResponse

    @DELETE("api/v2/plans/{plan_id}")
    suspend fun deletePlan(@Path("plan_id") planId: String): DeletePlanResponse

    // ---------- Stats ----------

    @GET("api/v2/stats/daily")
    suspend fun statsDaily(): StatsResponse

    @GET("api/v2/stats/weekly")
    suspend fun statsWeekly(): StatsResponse

    // ---------- D-03 Body Metrics ----------

    @POST("api/v2/metrics/body")
    suspend fun addBodyMetric(@Body req: BodyMetricRequest): GenericOkResponse

    @GET("api/v2/metrics/body")
    suspend fun listBodyMetrics(@Query("limit") limit: Int = 30): BodyMetricListResponse

    @GET("api/v2/metrics/latest")
    suspend fun latestBodyMetric(): BodyMetricLatestResponse

    // ---------- D-04 Exercise Log ----------

    @POST("api/v2/exercise/log")
    suspend fun addExerciseLog(@Body req: ExerciseLogRequest): GenericOkResponse

    @GET("api/v2/exercise/log")
    suspend fun listExerciseLog(
        @Query("limit") limit: Int = 50,
        @Query("days") days: Int = 30
    ): ExerciseLogListResponse

    @GET("api/v2/exercise/summary")
    suspend fun exerciseSummary(@Query("days") days: Int = 7): ExerciseSummaryResponse

    @GET("api/v2/training/data")
    suspend fun trainingData(@Query("period") period: String = "week"): TrainingDataResponse

    @GET("api/v2/training/rep/{rep_id}/images")
    suspend fun trainingRepImages(@Path("rep_id") repId: Long): TrainingRepImageResponse

    @POST("api/v2/training/rep/{rep_id}/ai_coach")
    suspend fun trainingRepAiCoach(
        @Path("rep_id") repId: Long,
        @Body body: AiCoachRequest
    ): AiCoachResponse

    @POST("api/v2/training/session/{session_id}/ai_coach")
    suspend fun trainingSessionAiCoach(
        @Path("session_id") sessionId: String,
        @Body body: AiCoachRequest = AiCoachRequest()
    ): SessionAiCoachResponse

    // AI Coach reports archive
    @GET("api/v2/ai_coach/reports")
    suspend fun aiCoachReports(
        @retrofit2.http.Query("session_id") sessionId: String? = null,
        @retrofit2.http.Query("limit") limit: Int? = null
    ): AiCoachReportListResponse

    @GET("api/v2/ai_coach/reports/{report_id}")
    suspend fun aiCoachReport(
        @Path("report_id") reportId: String
    ): AiCoachReportDetail

    @retrofit2.http.PATCH("api/v2/ai_coach/reports/{report_id}")
    suspend fun aiCoachReportUpdate(
        @Path("report_id") reportId: String,
        @Body body: AiCoachReportNoteUpdate
    ): AiCoachReportUpdateResponse

    @retrofit2.http.DELETE("api/v2/ai_coach/reports/{report_id}")
    suspend fun aiCoachReportDelete(
        @Path("report_id") reportId: String
    ): AiCoachReportDeleteResponse

    @POST("api/v2/devices/bind")
    suspend fun bindDevice(@Body req: BindDeviceRequest): BindDeviceResponse

    @GET("api/v2/devices/bindings")
    suspend fun listBindings(): BindingListResponse

    @DELETE("api/v2/devices/bind/{device_id}")
    suspend fun unbindDevice(@Path("device_id") deviceId: String): GenericOkResponse

    // ---------- B-07 聚合推理 (2026-05-25 后端新增) ----------

    @POST("api/v2/vision/infer/full")
    suspend fun visionInferFull(@Body req: VisionInferRequest): VisionInferFullResponse

    @POST("api/v2/vision/infer")
    suspend fun visionInfer(@Body req: VisionInferRequest): VisionInferResponse

    // ---------- Training Control (控制 ESP32 开始/停止) ----------
    @POST("api/v2/training/start")
    suspend fun trainingStart(@Body req: TrainingStartRequest): TrainingStartResponse

    @POST("api/v2/training/stop")
    suspend fun trainingStop(@Body req: TrainingStopRequest): GenericOkResponse

    @GET("api/v2/training/active")
    suspend fun trainingActive(): TrainingActiveResponse

    // ---------- Workout Summary & Calendar (2026-05-28) ----------
    @POST("api/v2/workout/summary")
    suspend fun workoutSummary(@Body req: WorkoutSummaryRequest): WorkoutSummaryResponse

    @GET("api/v2/stats/calendar")
    suspend fun calendarDays(): CalendarResponse

    // ---------- PB / Streak / Achievements (2026-05-28 v8) ----------
    @GET("api/v2/stats/pb")
    suspend fun personalBest(): PersonalBestResponse

    @GET("api/v2/stats/streak")
    suspend fun streak(): StreakResponse

    @GET("api/v2/achievements")
    suspend fun achievements(): AchievementsResponse

    // ---------- AI Plan Generate (LLM) ----------
    @POST("api/v2/ai/plan_generate")
    suspend fun aiGeneratePlan(@Body req: AiPlanGenerateRequest): AiPlanGenerateResponse

    // ---------- Dedicated Fitness Agent ----------
    @POST("api/v2/agent/chat")
    suspend fun fitnessAgentChat(@Body req: com.smartfitness.app.model.AgentChatRequest): com.smartfitness.app.model.AgentChatResponse

    @GET("api/v2/agent/history")
    suspend fun fitnessAgentHistory(@Query("limit") limit: Int = 50): com.smartfitness.app.model.AgentChatHistoryResponse

    @DELETE("api/v2/agent/history")
    suspend fun clearFitnessAgentHistory(): GenericOkResponse

    @GET("api/v2/agent/approvals")
    suspend fun fitnessAgentApprovals(@Query("limit") limit: Int = 20): com.smartfitness.app.model.AgentApprovalListResponse

    @POST("api/v2/agent/approvals/{approval_id}/approve")
    suspend fun approveFitnessAgentTool(@Path("approval_id") approvalId: String): com.smartfitness.app.model.AgentApprovalActionResponse

    @POST("api/v2/agent/approvals/{approval_id}/deny")
    suspend fun denyFitnessAgentTool(@Path("approval_id") approvalId: String): com.smartfitness.app.model.AgentApprovalActionResponse

    @POST("api/v2/agent/nutrition_plan")
    suspend fun fitnessAgentNutrition(@Body req: com.smartfitness.app.model.AgentNutritionPlanRequest): com.smartfitness.app.model.AgentChatResponse

    @GET("api/v2/agent/runs")
    suspend fun fitnessAgentRuns(@Query("limit") limit: Int = 20): com.smartfitness.app.model.AgentRunListResponse

    @GET("api/v2/agent/runs/{run_id}")
    suspend fun fitnessAgentRunDetail(@Path("run_id") runId: String): com.smartfitness.app.model.AgentRunDetailResponse

    @GET("api/v2/agent/kb")
    suspend fun fitnessAgentKb(): com.smartfitness.app.model.AgentKnowledgeResponse

    @GET("api/v2/agent/health")
    suspend fun fitnessAgentHealth(@Query("window_sec") windowSec: Int = 3600): com.smartfitness.app.model.AgentHealthResponse

    @GET("api/v2/agent/background/items")
    suspend fun fitnessAgentBackgroundItems(
        @Query("status") status: String = "pending",
        @Query("limit") limit: Int = 20
    ): com.smartfitness.app.model.AgentBackgroundItemListResponse

    @POST("api/v2/agent/background/run")
    suspend fun runFitnessAgentBackground(@Body req: com.smartfitness.app.model.AgentBackgroundRunRequest): com.smartfitness.app.model.AgentBackgroundRunResponse

    @POST("api/v2/agent/background/items/{item_id}/read")
    suspend fun markFitnessAgentBackgroundRead(@Path("item_id") itemId: String): GenericOkResponse

    @POST("api/v2/agent/background/items/{item_id}/dismiss")
    suspend fun dismissFitnessAgentBackgroundItem(@Path("item_id") itemId: String): GenericOkResponse

    // ---------- AI Coach Butler: 复盘 + 教练记忆 (2026-06-11) ----------
    @POST("api/v2/ai/coach_review")
    suspend fun coachReview(): CoachReviewResponse

    // ---------- 完整运动报告 (模式2, 2026-06-14) ----------
    @POST("api/v2/ai/workout_report")
    suspend fun workoutReport(@Body req: com.smartfitness.app.model.WorkoutReportRequest): com.smartfitness.app.model.WorkoutReportResponse

    @GET("api/v2/ai/memory")
    suspend fun coachMemories(): CoachMemoryListResponse

    @POST("api/v2/ai/memory")
    suspend fun addCoachMemory(@Body req: CoachMemoryAddRequest): GenericOkResponse
}
