package com.smartfitness.app.ui.training

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.AnimationUtils
import android.widget.ArrayAdapter
import android.widget.ImageView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.floatingactionbutton.FloatingActionButton
import com.google.gson.Gson
import com.smartfitness.app.R
import com.smartfitness.app.api.ApiClient
import com.smartfitness.app.api.WebSocketManager
import com.smartfitness.app.camera.MjpegClient
import com.smartfitness.app.model.CoachUpdate
import com.smartfitness.app.model.TrainingStartRequest
import com.smartfitness.app.model.TrainingStopRequest
import com.smartfitness.app.model.WorkoutSummaryRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Locale

class TrainingFragment : Fragment(), WebSocketManager.Listener, TextToSpeech.OnInitListener {

    private var esp32Preview: ImageView? = null
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

    private var mjpegClient: MjpegClient? = null
    private var ws: WebSocketManager? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private val gson = Gson()

    @Volatile private var latestFrame: Bitmap? = null
    @Volatile private var latestLandmarks: List<Map<String, Any?>>? = null
    @Volatile private var latestDetected: Boolean = false
    @Volatile private var renderBusy = false

    private var isTraining = false
    private var trainingStartMs = 0L
    private var trainingDeviceId: String? = null
    private var trainingExerciseKey: String? = null

    private var lastReps = 0
    private var lastScore: Double? = null
    private var scoreSum = 0.0
    private var scoreCount = 0
    private var maxReps = 0
    private var lastSpokenTip: String? = null
    private var lastSpokenAt = 0L

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
        btnToggle = view.findViewById(R.id.btn_training_toggle)
        fabSettings = view.findViewById(R.id.fab_settings)

        spinnerExercise?.adapter = ArrayAdapter(
            requireContext(),
            android.R.layout.simple_spinner_dropdown_item,
            exerciseOptions.map { it.second }
        )
        btnToggle?.setOnClickListener {
            if (isTraining) stopTraining() else startTraining()
        }
        fabSettings?.setOnClickListener { openSettings() }

        statusText?.text = "Connecting MJPEG..."
        tts = TextToSpeech(requireContext(), this)

        startMjpegStream()
    }

    private fun openSettings() {
        val devId = ApiClient.getOrCreateDeviceId()
        val prefs = requireContext().getSharedPreferences("sf_prefs", android.content.Context.MODE_PRIVATE)
        val currentIp = prefs.getString("esp32_ip", "192.168.72.20") ?: "192.168.72.20"
        val input = android.widget.EditText(requireContext())
        input.setText(currentIp)
        input.hint = "ESP32 IP (e.g. 192.168.72.20)"
        MaterialAlertDialogBuilder(requireContext())
            .setTitle("Settings")
            .setMessage(
                "Device ID: $devId\n" +
                "Backend: " + ApiClient.BASE_URL + "\n" +
                "WS status: " + (statusText?.text ?: "-") + "\n\n" +
                "ESP32 stream IP:"
            )
            .setView(input)
            .setPositiveButton("Save & Reconnect") { _, _ ->
                val newIp = input.text.toString().trim()
                if (newIp.isNotEmpty() && newIp != currentIp) {
                    prefs.edit().putString("esp32_ip", newIp).apply()
                    Toast.makeText(requireContext(), "ESP32 IP updated, reconnecting...", Toast.LENGTH_SHORT).show()
                    try { mjpegClient?.stop() } catch (_: Exception) {}
                    mjpegClient = null
                    startMjpegStream()
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun startTraining() {
        val pos = spinnerExercise?.selectedItemPosition ?: 0
        val exKey = exerciseOptions.getOrNull(pos)?.first ?: "squat"
        val exLabel = exerciseOptions.getOrNull(pos)?.second ?: exKey
        val devId = ApiClient.getOrCreateDeviceId()

        btnToggle?.isEnabled = false
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val resp = withContext(Dispatchers.IO) {
                    ApiClient.service.trainingStart(
                        TrainingStartRequest(deviceId = devId, exercise = exKey)
                    )
                }
                if (resp.ok) {
                    isTraining = true
                    trainingStartMs = System.currentTimeMillis()
                    trainingDeviceId = devId
                    trainingExerciseKey = exKey
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
                // fetch summary
                val summary = withContext(Dispatchers.IO) {
                    try {
                        ApiClient.service.workoutSummary(
                            WorkoutSummaryRequest(
                                deviceId = devId,
                                exercise = exKey,
                                reps = maxReps,
                                durationS = durSec,
                                avgFormScore = avgForm,
                            )
                        )
                    } catch (e: Exception) { null }
                }
                resetTrainingUI()
                showSummaryDialog(exKey, maxReps, durSec.toLong(), avgForm, summary)
            } catch (e: Exception) {
                Toast.makeText(requireContext(), "Stop failed: ${e.message}", Toast.LENGTH_LONG).show()
            } finally {
                btnToggle?.isEnabled = true
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
        // 保留骨架预览 (蓝半透明), 不清
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
        val mc = MjpegClient(
            url = url,
            scope = viewLifecycleOwner.lifecycleScope,
            onFrame = { bmp ->
                latestFrame = bmp
                paintLatest()
                activity?.runOnUiThread {
                    if (esp32Status?.visibility == View.VISIBLE) esp32Status?.visibility = View.GONE
                }
            },
            onError = { t ->
                activity?.runOnUiThread {
                    if (isAdded) esp32Status?.text = "MJPEG error: ${t.message}"
                }
            }
        )
        mjpegClient = mc
        mc.start()
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
                val reps = upd.repCount ?: 0
                val score = upd.formScore
                lastReps = reps
                if (reps > maxReps) maxReps = reps
                if (score != null) {
                    lastScore = score
                    scoreSum += score; scoreCount += 1
                }
                latestLandmarks = upd.landmarks
                latestDetected = upd.detected ?: false
                // Auto-classifier: if backend predicts a different exercise with high confidence, sync UI
                val predicted = upd.exercise ?: upd.exerciseType
                if (!predicted.isNullOrBlank() && predicted != trainingExerciseKey) {
                    val newIdx = exerciseOptions.indexOfFirst { it.first == predicted }
                    if (newIdx >= 0) {
                        activity?.runOnUiThread {
                            spinnerExercise?.setSelection(newIdx)
                            coachTipText?.text = "Detected: " + exerciseOptions[newIdx].second
                            coachTipCard?.visibility = View.VISIBLE
                        }
                        trainingExerciseKey = predicted
                    }
                }
                activity?.runOnUiThread {
                    hudReps?.text = reps.toString()
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
        try { mjpegClient?.stop() } catch (_: Exception) {}
        mjpegClient = null
        tts?.stop(); tts?.shutdown(); tts = null
        latestFrame = null; latestLandmarks = null
        esp32Preview = null; esp32Status = null
        statusDot = null; statusText = null; timerText = null
        hudLayout = null; hudReps = null; hudScore = null
        coachTipCard = null; coachTipText = null
        spinnerExercise = null; btnToggle = null; fabSettings = null
    }
}
