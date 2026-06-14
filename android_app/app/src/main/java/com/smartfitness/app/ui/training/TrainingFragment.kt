package com.smartfitness.app.ui.training

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Bundle
import android.util.Base64
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.AnimationUtils
import android.widget.ArrayAdapter
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.camera.view.PreviewView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.floatingactionbutton.FloatingActionButton
import com.google.gson.Gson
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.api.WebSocketManager
import com.smartfitness.app.camera.MjpegClient
import com.smartfitness.app.camera.CameraCapture
import com.smartfitness.app.model.CoachUpdate
import com.smartfitness.app.model.TrainingStartRequest
import com.smartfitness.app.model.TrainingStopRequest
import com.smartfitness.app.model.WorkoutSummaryRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.util.Locale

class TrainingFragment : Fragment(), WebSocketManager.Listener, TextToSpeech.OnInitListener {

    private var esp32Preview: ImageView? = null
    private var phonePreview: PreviewView? = null
    private var esp32Status: TextView? = null
    private var statusDot: View? = null
    private var statusText: TextView? = null
    private var timerText: TextView? = null
    private var hudLayout: View? = null
    private var hudReps: TextView? = null
    private var hudScore: TextView? = null
    private var coachTipCard: MaterialCardView? = null
    private var coachTipText: TextView? = null
    private var spinnerExercise: Spinner? = null
    private var spinnerCameraSource: Spinner? = null
    private var btnToggle: MaterialButton? = null
    private var fabSettings: FloatingActionButton? = null

    private val exerciseOptions = listOf(
        "squat" to "Squat",
        "push_up" to "Push-up",
        "lunge" to "Lunge",
        "plank" to "Plank",
        "bicep_curl" to "Bicep Curl",
        "shoulder_press" to "Shoulder Press",
        "jumping_jack" to "Jumping Jack"
    )

    private enum class CameraSource(val key: String, val label: String) {
        ESP32("esp32cam", "ESP32-CAM"),
        PHONE("phone", "Phone Camera"),
        PC("pc", "PC Camera")
    }
    private val cameraSources = CameraSource.values().toList()
    private var currentCameraSource: CameraSource = CameraSource.ESP32
    private var mjpegClient: MjpegClient? = null
    private var phoneCameraCapture: CameraCapture? = null
    private var ws: WebSocketManager? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private val gson = Gson()

    @Volatile private var latestFrame: Bitmap? = null
    @Volatile private var latestLandmarks: List<Map<String, Any?>>? = null
    @Volatile private var latestDetected: Boolean = false
    @Volatile private var renderBusy = false
    @Volatile private var mjpegFrameCount: Long = 0
    @Volatile private var wsUpdateCount: Long = 0
    @Volatile private var previewInferBusy = false
    @Volatile private var previewInferCount: Long = 0
    @Volatile private var lastPreviewInferAt: Long = 0L
    private val previewInferEveryMs: Long = 500L
    private var currentStreamUrl: String = ""

    private var isTraining = false
    private var trainingStartMs = 0L
    private var trainingDeviceId: String? = null
    private var trainingExerciseKey: String? = null
    private var trainingSessionId: String? = null
    private var trainingMode: String = "complete"   // guidance(指导动作) | complete(完整运动)
    private var spinnerMode: Spinner? = null
    // (key, 中文标签)
    private val modeOptions = listOf("complete" to "完整运动", "guidance" to "指导动作")

    private var lastReps = 0
    private var lastScore: Double? = null
    private var scoreSum = 0.0
    private var scoreCount = 0
    private var maxReps = 0
    private var lastSpokenTip: String? = null
    private var lastSpokenAt = 0L
    private val cameraPermissionRequestCode = 4201

    private val handler = Handler(Looper.getMainLooper())
    private val timerRunnable = object : Runnable {
        override fun run() {
            if (isTraining) {
                val elapsed = (System.currentTimeMillis() - trainingStartMs) / 1000
                val mm = elapsed / 60
                val ss = elapsed % 60
                timerText?.text = String.format("%02d:%02d", mm, ss)
                handler.postDelayed(this, 1000)
            }
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View = inflater.inflate(R.layout.fragment_training, container, false)


    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        esp32Preview = view.findViewById(R.id.esp32_preview)
        phonePreview = view.findViewById(R.id.phone_preview)
        esp32Status = view.findViewById(R.id.esp32_status)
        statusDot = view.findViewById(R.id.status_dot)
        statusText = view.findViewById(R.id.status_text)
        timerText = view.findViewById(R.id.training_timer)
        hudLayout = view.findViewById(R.id.hud_layout)
        hudReps = view.findViewById(R.id.hud_reps)
        hudScore = view.findViewById(R.id.hud_score)
        coachTipCard = view.findViewById(R.id.coach_tip_card)
        coachTipText = view.findViewById(R.id.coach_tip_text)
        spinnerExercise = view.findViewById(R.id.spinner_exercise)
        spinnerCameraSource = view.findViewById(R.id.spinner_camera_source)
        btnToggle = view.findViewById(R.id.btn_training_toggle)
        fabSettings = view.findViewById(R.id.fab_settings)

        spinnerCameraSource?.adapter = ArrayAdapter(
            requireContext(),
            android.R.layout.simple_spinner_dropdown_item,
            cameraSources.map { it.label }
        )
        spinnerCameraSource?.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                val next = cameraSources.getOrNull(position) ?: CameraSource.ESP32
                if (next != currentCameraSource) switchCameraSource(next)
            }
            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
        }
        spinnerExercise?.adapter = ArrayAdapter(
            requireContext(),
            android.R.layout.simple_spinner_dropdown_item,
            exerciseOptions.map { it.second }
        )
        spinnerMode = view.findViewById(R.id.spinner_mode)
        spinnerMode?.adapter = ArrayAdapter(
            requireContext(),
            android.R.layout.simple_spinner_dropdown_item,
            modeOptions.map { it.second }
        )
        spinnerMode?.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                trainingMode = modeOptions.getOrNull(position)?.first ?: "complete"
            }
            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
        }
        btnToggle?.setOnClickListener {
            if (isTraining) stopTraining() else startTraining()
        }
        fabSettings?.setOnClickListener { openSettings() }

        statusText?.text = "Connecting camera..."
        tts = TextToSpeech(requireContext(), this)

        switchCameraSource(currentCameraSource)
    }

    private fun openSettings() {
        val phoneDevId = ApiClient.getOrCreateDeviceId()
        val prefs = requireContext().getSharedPreferences("sf_prefs", android.content.Context.MODE_PRIVATE)
        val currentIp = prefs.getString("esp32_ip", "192.168.72.20") ?: "192.168.72.20"
        val currentEspDev = prefs.getString("esp32_device_id", "esp32cam-001") ?: "esp32cam-001"
        val currentPcDev = prefs.getString("pc_device_id", "pc-camera-001") ?: "pc-camera-001"
        val box = android.widget.LinearLayout(requireContext()).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(32, 0, 32, 0)
        }
        val ipInput = android.widget.EditText(requireContext()).apply {
            setText(currentIp)
            hint = "ESP32 IP (e.g. 192.168.72.20)"
        }
        val devInput = android.widget.EditText(requireContext()).apply {
            setText(currentEspDev)
            hint = "ESP32 device_id (e.g. esp32cam-001)"
        }
        val pcDevInput = android.widget.EditText(requireContext()).apply {
            setText(currentPcDev)
            hint = "PC camera device_id (e.g. pc-camera-001)"
        }
        box.addView(ipInput)
        box.addView(devInput)
        box.addView(pcDevInput)
        MaterialAlertDialogBuilder(requireContext())
            .setTitle("Settings")
            .setMessage(
                "Phone Device ID: $phoneDevId\n" +
                "Backend: " + ApiClient.BASE_URL + "\n" +
                "WS status: " + (statusText?.text ?: "-") + "\n\n" +
                "Camera sources: ESP32 / Phone / PC\n" +
                "Phone Camera ID: ${getPhoneCameraDeviceId()}\n\n" +
                "ESP32 stream IP + ESP32 device_id + PC device_id:"
            )
            .setView(box)
            .setPositiveButton("Save & Reconnect") { _, _ ->
                val newIp = ipInput.text.toString().trim().ifEmpty { "192.168.72.20" }
                val newDev = devInput.text.toString().trim().ifEmpty { "esp32cam-001" }
                val newPcDev = pcDevInput.text.toString().trim().ifEmpty { "pc-camera-001" }
                prefs.edit()
                    .putString("esp32_ip", newIp)
                    .putString("esp32_device_id", newDev)
                    .putString("pc_device_id", newPcDev)
                    .apply()
                Toast.makeText(requireContext(), "Camera settings saved", Toast.LENGTH_SHORT).show()
                mjpegFrameCount = 0
                switchCameraSource(currentCameraSource)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun startTraining() {
        val pos = spinnerExercise?.selectedItemPosition ?: 0
        val exKey = exerciseOptions.getOrNull(pos)?.first ?: "squat"
        val exLabel = exerciseOptions.getOrNull(pos)?.second ?: exKey
        // Training control must use the currently selected camera device_id.
        // start/stop and infer/full have to use the same id, otherwise reps/session mismatch.
        val devId = getCurrentCameraDeviceId()

        btnToggle?.isEnabled = false
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = withContext(Dispatchers.IO) {
                    ApiClient.service.trainingStart(
                        TrainingStartRequest(
                            deviceId = devId,
                            exercise = exKey,
                            userId = ApiClient.userId.takeIf { it > 0L },
                            source = currentCameraSource.key,
                            mode = trainingMode
                        )
                    )
                }
                if (resp.ok) {
                    isTraining = true
                    trainingStartMs = System.currentTimeMillis()
                    trainingDeviceId = devId
                    trainingExerciseKey = exKey
                    trainingSessionId = resp.sessionId
                    spinnerMode?.isEnabled = false
                    lastReps = 0; maxReps = 0; lastScore = null
                    scoreSum = 0.0; scoreCount = 0
                    btnToggle?.text = "Stop"
                    btnToggle?.setBackgroundColor(0xFFEF4444.toInt())
                    hudLayout?.visibility = View.VISIBLE
                    coachTipText?.text = "Get ready: $exLabel"
                    coachTipCard?.visibility = View.VISIBLE
                    handler.post(timerRunnable)
                    autoConnectCoachIfNeeded()
                    Toast.makeText(requireContext(), "Training: $exLabel", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(requireContext(), "Start failed: ${resp.error}", Toast.LENGTH_LONG).show()
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Network error: ${e.message}", Toast.LENGTH_LONG).show()
            } finally {
                btnToggle?.isEnabled = true
            }
        }
    }

    private fun stopTraining() {
        val devId = trainingDeviceId ?: return
        val exKey = trainingExerciseKey ?: "unknown"
        val durSec = ((System.currentTimeMillis() - trainingStartMs) / 1000).toDouble()
        val avgForm = if (scoreCount > 0) (scoreSum / scoreCount) else null

        btnToggle?.isEnabled = false
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    ApiClient.service.trainingStop(TrainingStopRequest(deviceId = devId))
                }
                val sid = trainingSessionId
                if (trainingMode == "complete" && !sid.isNullOrEmpty()) {
                    // 模式2 完整运动: 出 AI 训练报告 (结合本次+历史)
                    resetTrainingUI()
                    val loading = MaterialAlertDialogBuilder(requireContext())
                        .setTitle("AI 教练分析中")
                        .setMessage("正在分析本次训练并对照历史… (约 30 秒)")
                        .setCancelable(true).show()
                    val report = withContext(Dispatchers.IO) {
                        try { ApiClient.service.workoutReport(
                            com.smartfitness.app.model.WorkoutReportRequest(sessionId = sid)) }
                        catch (e: Exception) { null }
                    }
                    if (isAdded) {
                        loading.dismiss()
                        showReportSheet(exKey, maxReps, durSec.toLong(), avgForm, report)
                    }
                } else {
                    // 模式1 指导动作: 简要总结即可 (逐次矫正训练中已实时给过)
                    val summary = withContext(Dispatchers.IO) {
                        try {
                            ApiClient.service.workoutSummary(
                                WorkoutSummaryRequest(
                                    deviceId = devId, exercise = exKey,
                                    reps = maxReps, durationS = durSec, avgFormScore = avgForm,
                                )
                            )
                        } catch (e: Exception) { null }
                    }
                    resetTrainingUI()
                    showSummaryDialog(exKey, maxReps, durSec.toLong(), avgForm, summary)
                }
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Stop failed: ${e.message}", Toast.LENGTH_LONG).show()
            } finally {
                btnToggle?.isEnabled = true
            }
        }
    }

    private fun getEsp32DeviceId(): String {
        val prefs = requireContext().getSharedPreferences("sf_prefs", android.content.Context.MODE_PRIVATE)
        return prefs.getString("esp32_device_id", "esp32cam-001") ?: "esp32cam-001"
    }

    private fun getPhoneCameraDeviceId(): String = "phone-" + ApiClient.getOrCreateDeviceId()

    private fun getPcCameraDeviceId(): String {
        val prefs = requireContext().getSharedPreferences("sf_prefs", android.content.Context.MODE_PRIVATE)
        return prefs.getString("pc_device_id", "pc-camera-001") ?: "pc-camera-001"
    }

    private fun getCurrentCameraDeviceId(): String = when (currentCameraSource) {
        CameraSource.ESP32 -> getEsp32DeviceId()
        CameraSource.PHONE -> getPhoneCameraDeviceId()
        CameraSource.PC -> getPcCameraDeviceId()
    }

    private fun switchCameraSource(source: CameraSource) {
        if (isTraining) {
            Toast.makeText(requireContext(), "Stop training before switching camera", Toast.LENGTH_SHORT).show()
            spinnerCameraSource?.setSelection(cameraSources.indexOf(currentCameraSource).coerceAtLeast(0))
            return
        }
        stopCameraSources()
        currentCameraSource = source
        resetCountersForSource()
        latestFrame = null
        latestLandmarks = null
        latestDetected = false
        esp32Preview?.setImageBitmap(null)
        when (source) {
            CameraSource.ESP32 -> {
                phonePreview?.visibility = View.GONE
                esp32Preview?.visibility = View.VISIBLE
                statusText?.text = "ESP32-CAM"
                startMjpegStream()
            }
            CameraSource.PHONE -> {
                esp32Preview?.visibility = View.GONE
                phonePreview?.visibility = View.VISIBLE
                statusText?.text = "Phone Camera"
                startPhoneCamera()
            }
            CameraSource.PC -> {
                phonePreview?.visibility = View.GONE
                esp32Preview?.visibility = View.VISIBLE
                esp32Status?.visibility = View.VISIBLE
                esp32Status?.text = "PC camera mode\nStart pc_simulator/pc_camera_agent.py on this PC"
                statusText?.text = "PC Camera: waiting for updates"
                autoConnectCoachIfNeeded()
            }
        }
    }

    private fun resetCountersForSource() {
        lastReps = 0
        maxReps = 0
        lastScore = null
        scoreSum = 0.0
        scoreCount = 0
        hudReps?.text = "0"
        hudScore?.text = "--"
        previewInferBusy = false
        previewInferCount = 0
        lastPreviewInferAt = 0L
    }

    private fun stopCameraSources() {
        try { mjpegClient?.stop() } catch (_: Exception) {}
        mjpegClient = null
        try { phoneCameraCapture?.stop() } catch (_: Exception) {}
        phoneCameraCapture = null
    }

    private fun startPhoneCamera() {
        val preview = phonePreview ?: return
        if (phoneCameraCapture != null) return
        if (ContextCompat.checkSelfPermission(requireContext(), Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            esp32Status?.visibility = View.VISIBLE
            esp32Status?.text = "Camera permission needed"
            requestPermissions(arrayOf(Manifest.permission.CAMERA), cameraPermissionRequestCode)
            return
        }
        esp32Status?.visibility = View.VISIBLE
        esp32Status?.text = "Starting phone camera..."
        val cap = CameraCapture(requireContext(), viewLifecycleOwner, preview)
        phoneCameraCapture = cap
        cap.start(
            intervalMs = 500L,
            maxWidth = 640,
            jpegQuality = 58,
            onFrame = { jpegB64 -> requestPhonePose(jpegB64) },
            onError = { t ->
                activity?.runOnUiThread {
                    esp32Status?.visibility = View.VISIBLE
                    esp32Status?.text = "Phone camera issue: ${t.message?.take(80)}"
                }
            }
        )
        esp32Status?.visibility = View.GONE
    }

    private fun requestPhonePose(jpegB64: String) {
        if (previewInferBusy) return
        previewInferBusy = true
        val deviceId = getPhoneCameraDeviceId()
        val uid = ApiClient.userId.takeIf { it > 0L }
        viewLifecycleOwner.lifecycleScope.launch(Dispatchers.IO) {
            try {
                val resp = ApiClient.service.visionInferFull(
                    com.smartfitness.app.model.VisionInferRequest(
                        image = jpegB64,
                        deviceId = deviceId,
                        userId = uid,
                        backend = "mediapipe",
                        exercise = trainingExerciseKey ?: exerciseOptions.getOrNull(spinnerExercise?.selectedItemPosition ?: 0)?.first ?: "squat",
                        source = CameraSource.PHONE.key
                    )
                )
                latestLandmarks = resp.landmarks.mapIndexed { idx, lm ->
                    mapOf<String, Any?>(
                        "id" to (lm.id ?: idx), "name" to lm.name,
                        "x" to lm.x, "y" to lm.y, "z" to lm.z,
                        "visibility" to lm.visibility,
                        "pixel_x" to lm.pixelX, "pixel_y" to lm.pixelY
                    )
                }
                latestDetected = resp.detected ?: latestLandmarks?.isNotEmpty() == true
                previewInferCount += 1
                withContext(Dispatchers.Main) {
                    if (!isAdded) return@withContext
                    val incomingReps = resp.repCount
                    val reps = if (latestDetected && incomingReps != null) maxOf(lastReps, incomingReps) else lastReps
                    val score = if (latestDetected) resp.formScore else null
                    lastReps = reps
                    if (reps > maxReps) maxReps = reps
                    if (latestDetected && score != null) {
                        lastScore = score
                        scoreSum += score
                        scoreCount += 1
                    }
                    hudReps?.text = reps.toString()
                    hudScore?.text = score?.let { String.format("%.0f", it) } ?: "--"
                    if (latestDetected) {
                        val tip = resp.coachTip ?: resp.formFeedback.firstOrNull()?.messageCn ?: resp.formFeedback.firstOrNull()?.messageEn
                        if (!tip.isNullOrBlank()) {
                            coachTipText?.text = tip
                            coachTipCard?.visibility = View.VISIBLE
                        }
                    } else {
                        coachTipText?.text = "未检测到人体，请站到画面中央"
                        coachTipCard?.visibility = View.VISIBLE
                    }
                    if (!isTraining) statusText?.text = if (latestDetected) "Phone pose detected" else "Phone: no person"
                }
            } catch (e: Exception) {
                activity?.runOnUiThread {
                    if (!isTraining) statusText?.text = "Phone infer error: ${e.message?.take(40)}"
                }
            } finally {
                previewInferBusy = false
            }
        }
    }

    private fun updateDebugOverlay(width: Int?, height: Int?, error: String?) {
        activity?.runOnUiThread {
            if (!isAdded) return@runOnUiThread
            if (error != null) {
                esp32Status?.visibility = View.VISIBLE
                esp32Status?.text = "Camera connection issue\n" + currentStreamUrl
            } else {
                // Demo UI: hide verbose diagnostics such as MJPEG frames / Preview infer / WS / landmarks / detected / device_id.
                esp32Status?.visibility = View.GONE
                esp32Status?.text = ""
            }
        }
    }

    private fun resetTrainingUI() {
        isTraining = false
        handler.removeCallbacks(timerRunnable)
        btnToggle?.text = "Start"
        btnToggle?.setBackgroundColor(0xFF22C55E.toInt())
        hudLayout?.visibility = View.GONE
        coachTipCard?.visibility = View.GONE
        timerText?.text = "00:00"
        trainingDeviceId = null
        trainingExerciseKey = null
        spinnerMode?.isEnabled = true
        // 保留骨架预览 (蓝半透明), 不清
    }

    /** 模式2: 完整运动 AI 报告 BottomSheet (总结/亮点/问题/历史对比/建议). */
    private fun showReportSheet(
        exKey: String, reps: Int, durationS: Long, avgForm: Double?,
        resp: com.smartfitness.app.model.WorkoutReportResponse?
    ) {
        val ctx = requireContext()
        val sheet = com.google.android.material.bottomsheet.BottomSheetDialog(ctx)
        val scroll = android.widget.ScrollView(ctx)
        val dp = { v: Int -> (ctx.resources.displayMetrics.density * v).toInt() }
        val box = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(0xFFFFFFFF.toInt())
            setPadding(dp(24), dp(20), dp(24), dp(24))
        }
        scroll.addView(box)
        box.addView(View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(dp(32), dp(4)).apply {
                gravity = android.view.Gravity.CENTER_HORIZONTAL; bottomMargin = dp(12)
            }
            background = android.graphics.drawable.GradientDrawable().apply {
                cornerRadius = dp(2).toFloat(); setColor(0xFFE0E0E0.toInt())
            }
        })
        box.addView(TextView(ctx).apply {
            text = "本次训练报告"; textSize = 22f
            setTypeface(typeface, android.graphics.Typeface.BOLD); setTextColor(0xFF14142B.toInt())
        })
        val mm = durationS / 60; val ss = durationS % 60
        box.addView(TextView(ctx).apply {
            text = "$exKey · $reps 次 · ${String.format("%02d:%02d", mm, ss)}" +
                    (avgForm?.let { " · 均分 ${String.format("%.0f", it)}" } ?: "")
            textSize = 13f; setTextColor(0xFF9094A6.toInt()); setPadding(0, dp(4), 0, dp(8))
        })

        val rep = resp?.report
        fun section(title: String, body: String?) {
            if (body.isNullOrBlank()) return
            box.addView(TextView(ctx).apply {
                text = title; textSize = 16f
                setTypeface(typeface, android.graphics.Typeface.BOLD); setTextColor(0xFF14142B.toInt())
                setPadding(0, dp(12), 0, dp(2))
            })
            box.addView(TextView(ctx).apply {
                text = body; textSize = 15f; setTextColor(0xFF2D2D2D.toInt()); setLineSpacing(0f, 1.2f)
            })
        }
        if (rep != null) {
            section("📊 总结", rep.summary)
            section("✅ 亮点", rep.highlights)
            section("🎯 待改进", rep.problems)
            section("📈 对比历史", rep.vsHistory)
            rep.recommendations?.takeIf { it.isNotEmpty() }?.let {
                section("🗓 下次建议", it.joinToString("\n") { r -> "• $r" })
            }
            rep.encouragement?.let {
                box.addView(TextView(ctx).apply {
                    text = "🔥 $it"; textSize = 15f
                    setTypeface(typeface, android.graphics.Typeface.BOLD); setTextColor(0xFFE67E22.toInt())
                    setPadding(0, dp(12), 0, 0)
                })
            }
        } else {
            section("报告", resp?.reportText ?: "本次未生成报告 (可能动作太少或 AI 暂不可用)。")
        }
        box.addView(com.google.android.material.button.MaterialButton(ctx).apply {
            text = "完成"; cornerRadius = dp(12)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(16) }
            setOnClickListener { sheet.dismiss() }
        })
        sheet.setContentView(scroll)
        sheet.show()
    }


    private fun showSummaryDialog(
        exKey: String,
        reps: Int,
        durationS: Long,
        avgForm: Double?,
        summary: com.smartfitness.app.model.WorkoutSummaryResponse?
    ) {
        val mm = durationS / 60
        val ss = durationS % 60
        val formStr = avgForm?.let { String.format("%.0f", it) } ?: "-"
        val kcal = summary?.kcalEst?.let { String.format("%.1f", it) } ?: "-"
        val remark = summary?.coachRemark ?: "Completed $reps reps. Keep it up!"
        val badges = summary?.badges?.joinToString(", ") { it.name } ?: ""

        val msg = buildString {
            append("Exercise: $exKey\n")
            append("Reps: $reps\n")
            append("Time: ${String.format("%02d:%02d", mm, ss)}\n")
            append("Avg Form: $formStr\n")
            append("Kcal: $kcal\n\n")
            append("Coach: $remark")
            if (badges.isNotEmpty()) append("\n\nBadges: $badges")
        }

        MaterialAlertDialogBuilder(requireContext())
            .setTitle("Workout Summary")
            .setMessage(msg)
            .setPositiveButton("Done", null)
            .setNeutralButton("Share") { _, _ ->
                Toast.makeText(requireContext(), "Share coming soon", Toast.LENGTH_SHORT).show()
            }
            .show()
    }

    private fun autoConnectCoachIfNeeded() {
        val uid = ApiClient.userId
        if (uid <= 0L) return
        ws?.close()
        ws = WebSocketManager(this)
        ws?.connectCoach(uid)
    }

    private fun startMjpegStream() {
        if (mjpegClient != null) return
        val prefs = requireContext().getSharedPreferences("sf_prefs", android.content.Context.MODE_PRIVATE)
        val ip = prefs.getString("esp32_ip", "192.168.72.20") ?: "192.168.72.20"
        val url = "http://$ip:81/stream"
        currentStreamUrl = url
        activity?.runOnUiThread {
            esp32Status?.visibility = View.VISIBLE
            esp32Status?.text = "Connecting camera..."
        }
        val mc = MjpegClient(
            url = url,
            scope = viewLifecycleOwner.lifecycleScope,
            onFrame = { bmp ->
                latestFrame = bmp
                mjpegFrameCount += 1
                requestPreviewPoseIfNeeded(bmp)
                paintLatest()
                updateDebugOverlay(bmp.width, bmp.height, null)
            },
            onError = { t ->
                updateDebugOverlay(null, null, "MJPEG error: ${t.message}")
            }
        )
        mjpegClient = mc
        mc.start()
    }

    private fun requestPreviewPoseIfNeeded(frame: Bitmap) {
        val now = System.currentTimeMillis()
        if (previewInferBusy) return
        if (now - lastPreviewInferAt < previewInferEveryMs) return
        lastPreviewInferAt = now
        previewInferBusy = true
        val deviceId = getCurrentCameraDeviceId()
        val uid = ApiClient.userId.takeIf { it > 0L }
        viewLifecycleOwner.lifecycleScope.launch(Dispatchers.IO) {
            try {
                val jpegB64 = bitmapToBase64Jpeg(frame, quality = 52)
                val resp = ApiClient.service.visionInferFull(
                    com.smartfitness.app.model.VisionInferRequest(
                        image = jpegB64,
                        deviceId = deviceId,
                        userId = uid,
                        backend = "mediapipe",
                        exercise = trainingExerciseKey ?: exerciseOptions.getOrNull(spinnerExercise?.selectedItemPosition ?: 0)?.first ?: "squat",
                        source = currentCameraSource.key
                    )
                )
                val maps = resp.landmarks.mapIndexed { idx, lm ->
                    mapOf<String, Any?>(
                        "id" to (lm.id ?: idx),
                        "name" to lm.name,
                        "x" to lm.x,
                        "y" to lm.y,
                        "z" to lm.z,
                        "visibility" to lm.visibility,
                        "pixel_x" to lm.pixelX,
                        "pixel_y" to lm.pixelY
                    )
                }
                latestLandmarks = maps
                latestDetected = resp.detected ?: maps.isNotEmpty()
                previewInferCount += 1
                withContext(Dispatchers.Main) {
                    if (!isAdded) return@withContext
                    val incomingReps = resp.repCount
                    val reps = if (latestDetected && incomingReps != null) maxOf(lastReps, incomingReps) else lastReps
                    val score = if (latestDetected) resp.formScore else null
                    lastReps = reps
                    if (reps > maxReps) maxReps = reps
                    if (latestDetected && score != null) {
                        lastScore = score
                        scoreSum += score
                        scoreCount += 1
                    }
                    hudReps?.text = reps.toString()
                    hudScore?.text = score?.let { String.format("%.0f", it) } ?: "--"
                    if (latestDetected) {
                        val tip = resp.coachTip ?: resp.formFeedback.firstOrNull()?.messageCn ?: resp.formFeedback.firstOrNull()?.messageEn
                        if (!tip.isNullOrBlank()) {
                            coachTipText?.text = tip
                            coachTipCard?.visibility = View.VISIBLE
                        }
                    } else {
                        coachTipText?.text = "未检测到人体，请站到画面中央"
                        coachTipCard?.visibility = View.VISIBLE
                    }
                    if (!isTraining) {
                        val status = if (latestDetected) "Pose detected" else "No person detected"
                        statusText?.text = status
                    }
                    updateDebugOverlay(frame.width, frame.height, null)
                    paintLatest()
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    if (!isAdded) return@withContext
                    if (!isTraining) statusText?.text = "Preview infer error: ${e.message?.take(40)}"
                }
            } finally {
                previewInferBusy = false
            }
        }
    }

    private fun bitmapToBase64Jpeg(bitmap: Bitmap, quality: Int): String {
        val out = ByteArrayOutputStream()
        val src = if (bitmap.width > 640) {
            val newW = 640
            val newH = (bitmap.height * (newW.toFloat() / bitmap.width)).toInt().coerceAtLeast(1)
            Bitmap.createScaledBitmap(bitmap, newW, newH, true)
        } else bitmap
        src.compress(Bitmap.CompressFormat.JPEG, quality, out)
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    private fun paintLatest() {
        val imgView = esp32Preview ?: return
        if (!isAdded) return
        if (renderBusy) return
        val frame = latestFrame ?: return
        val landmarks = latestLandmarks
        val detected = latestDetected
        renderBusy = true
        viewLifecycleOwner.lifecycleScope.launch(Dispatchers.Default) {
            try {
                val bmp = try { frame.copy(Bitmap.Config.ARGB_8888, true) } catch (_: Exception) { frame }
                if (landmarks != null && landmarks.isNotEmpty()) {
                    try {
                        val c = Canvas(bmp)
                        val w = bmp.width.toFloat(); val h = bmp.height.toFloat()
                        // 预览态半透明蓝, 训练态绿色
                        val dotColor = if (isTraining) Color.parseColor("#22C55E") else Color.parseColor("#5593FF")
                        val lineColor = if (isTraining) Color.parseColor("#22C55E") else Color.parseColor("#555599")
                        val dot = Paint().apply {
                            color = if (detected) dotColor else Color.GRAY
                            style = Paint.Style.FILL
                        }
                        val line = Paint().apply {
                            color = if (detected) lineColor else Color.parseColor("#666666")
                            strokeWidth = 4f
                            alpha = if (isTraining) 255 else 140
                        }
                        val conns = intArrayOf(
                            11,12, 11,13, 13,15, 12,14, 14,16,
                            11,23, 12,24, 23,24,
                            23,25, 25,27, 24,26, 26,28
                        )
                        fun pt(i:Int): Pair<Float,Float>? {
                            if (i<0||i>=landmarks.size) return null
                            val lm = landmarks[i]
                            val x = (lm["x"] as? Number)?.toFloat() ?: return null
                            val y = (lm["y"] as? Number)?.toFloat() ?: return null
                            return Pair(x*w, y*h)
                        }
                        var i=0
                        while (i<conns.size) {
                            val a=pt(conns[i]); val b=pt(conns[i+1])
                            if (a!=null && b!=null) c.drawLine(a.first,a.second,b.first,b.second,line)
                            i+=2
                        }
                        for (k in landmarks.indices) {
                            pt(k)?.let { c.drawCircle(it.first, it.second, 5f, dot) }
                        }
                    } catch (_: Exception) {}
                }
                withContext(Dispatchers.Main) {
                    if (!isAdded) return@withContext
                    imgView.setImageBitmap(bmp)
                }
            } finally { renderBusy = false }
        }
    }


    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == cameraPermissionRequestCode) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                if (currentCameraSource == CameraSource.PHONE) startPhoneCamera()
            } else {
                Toast.makeText(requireContext(), "Camera permission denied", Toast.LENGTH_LONG).show()
                spinnerCameraSource?.setSelection(cameraSources.indexOf(CameraSource.ESP32))
            }
        }
    }

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts?.language = Locale.SIMPLIFIED_CHINESE
            ttsReady = true
        }
    }

    override fun onOpen() {
        activity?.runOnUiThread {
            statusText?.text = "Connected"
            statusDot?.setBackgroundResource(R.drawable.dot_online)
        }
    }

    override fun onMessage(text: String) {
        try {
            val upd = gson.fromJson(text, CoachUpdate::class.java)
            if (upd?.type == "coach_update") {
                val incomingReps = upd.repCount
                val score = upd.formScore
                latestLandmarks = upd.landmarks
                latestDetected = upd.detected ?: false
                wsUpdateCount += 1
                updateDebugOverlay(latestFrame?.width, latestFrame?.height, null)
                // Keep the user-selected exercise fixed. Do not auto-switch the Spinner
                // based on single-frame classifier output; this page is for the selected workout.
                activity?.runOnUiThread {
                    val displayReps = if (latestDetected && incomingReps != null) maxOf(lastReps, incomingReps) else lastReps
                    lastReps = displayReps
                    if (displayReps > maxReps) maxReps = displayReps
                    hudReps?.text = displayReps.toString()
                    if (latestDetected) {
                        if (score != null) {
                            lastScore = score
                            scoreSum += score; scoreCount += 1
                        }
                        hudScore?.text = score?.let { String.format("%.0f", it) } ?: "--"
                        val tip = upd.coachTip ?: upd.feedback
                        if (!tip.isNullOrBlank()) {
                            coachTipText?.text = tip
                            coachTipCard?.visibility = View.VISIBLE
                            val now = System.currentTimeMillis()
                            if (ttsReady && (tip != lastSpokenTip || now - lastSpokenAt > 8000)) {
                                tts?.speak(tip, TextToSpeech.QUEUE_FLUSH, null, "coach")
                                lastSpokenTip = tip; lastSpokenAt = now
                            }
                        }
                    } else {
                        hudScore?.text = "--"
                        coachTipText?.text = "未检测到人体，请站到画面中央"
                        coachTipCard?.visibility = View.VISIBLE
                    }
                }
                paintLatest()
                return
            }
        } catch (_: Exception) {}
    }

    override fun onClosed(code: Int, reason: String) {
        activity?.runOnUiThread {
            statusText?.text = "Disconnected"
            statusDot?.setBackgroundResource(R.drawable.dot_offline)
        }
    }

    override fun onFailure(t: Throwable) {
        activity?.runOnUiThread {
            statusText?.text = "Error: ${t.message?.take(40)}"
            statusDot?.setBackgroundResource(R.drawable.dot_offline)
        }
    }

    override fun onPause() {
        super.onPause()
        // 避免用户切 tab 后忘记停训练 → ESP32 仍以高频上传
        if (isTraining) {
            stopTraining()
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        handler.removeCallbacks(timerRunnable)
        ws?.close(); ws = null
        stopCameraSources()
        tts?.stop(); tts?.shutdown(); tts = null
        latestFrame = null; latestLandmarks = null
        previewInferBusy = false
        esp32Preview = null; phonePreview = null; esp32Status = null
        statusDot = null; statusText = null; timerText = null
        hudLayout = null; hudReps = null; hudScore = null
        coachTipCard = null; coachTipText = null
        spinnerExercise = null; spinnerCameraSource = null; btnToggle = null; fabSettings = null
    }
}
