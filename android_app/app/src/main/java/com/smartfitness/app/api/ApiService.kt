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

    // ---------- D-05 Device Binding ----------

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
}
