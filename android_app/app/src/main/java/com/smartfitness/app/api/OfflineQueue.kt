package com.smartfitness.app.api

import android.content.Context
import android.util.Log
import com.google.gson.Gson
import com.smartfitness.app.model.ExerciseLogRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean

/**
 * E-09 离线模式: 网络断开/失败时把待发请求落本地, 网络恢复后按 FIFO 重放。
 *
 * 设计原则:
 *  - 只缓存「幂等可重放」的写请求 (运动日志、身体指标), 不缓存读
 *  - 用 SharedPreferences 存 JSON 数组 (体量小, 无需 Room)
 *  - 单线程 flush, 重放失败的项重新入队, 指数退避
 *
 * 用法:
 *  OfflineQueue.init(ctx)
 *  OfflineQueue.enqueueExerciseLog(req)
 *  OfflineQueue.tryFlush()   // 网络回来时调
 */
object OfflineQueue {
    private const val PREFS = "smart_fitness_offline"
    private const val KEY_QUEUE = "pending_ops"
    private const val TAG = "OfflineQueue"
    private const val MAX_QUEUE = 500

    private val gson = Gson()
    private var ctx: Context? = null
    private val flushing = AtomicBoolean(false)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    data class Op(
        val type: String,                // "exercise_log" | "body_metric"
        val payload: String,             // JSON
        val createdAt: Long = System.currentTimeMillis(),
        val attempts: Int = 0,
    )

    fun init(context: Context) {
        ctx = context.applicationContext
    }

    private fun prefs() = ctx!!.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    @Synchronized
    private fun loadAll(): MutableList<Op> {
        val raw = prefs().getString(KEY_QUEUE, null) ?: return mutableListOf()
        return try {
            val arr = gson.fromJson(raw, Array<Op>::class.java)
            arr.toMutableList()
        } catch (e: Exception) {
            Log.w(TAG, "loadAll parse fail, reset: $e")
            mutableListOf()
        }
    }

    @Synchronized
    private fun saveAll(list: List<Op>) {
        prefs().edit().putString(KEY_QUEUE, gson.toJson(list)).apply()
    }

    fun pendingCount(): Int = loadAll().size

    fun enqueueExerciseLog(req: ExerciseLogRequest): Job = scope.launch {
        enqueue(Op("exercise_log", gson.toJson(req)))
    }

    fun enqueueBodyMetric(req: Any): Job = scope.launch {
        enqueue(Op("body_metric", gson.toJson(req)))
    }

    /**
     * 优先联网发送，失败/无网时走离线队列。
     * 返回 Pair<online_ok, op_id_or_null>
     */
    suspend fun sendOrEnqueueExerciseLog(
        req: ExerciseLogRequest,
        context: Context? = ctx,
    ): Boolean {
        val svc = ApiClient.service
        if (context != null && !NetworkMonitor.isOnline(context)) {
            enqueue(Op("exercise_log", gson.toJson(req)))
            return false
        }
        return try {
            val resp = svc.addExerciseLog(req)
            if (resp.ok) true else {
                enqueue(Op("exercise_log", gson.toJson(req)))
                false
            }
        } catch (e: Exception) {
            Log.w(TAG, "online send failed -> enqueue: $e")
            enqueue(Op("exercise_log", gson.toJson(req)))
            false
        }
    }

    @Synchronized
    private fun enqueue(op: Op) {
        val list = loadAll()
        if (list.size >= MAX_QUEUE) {
            Log.w(TAG, "queue full ($MAX_QUEUE), dropping oldest")
            list.removeAt(0)
        }
        list.add(op)
        saveAll(list)
        Log.i(TAG, "enqueued ${op.type}, queue=${list.size}")
    }

    /**
     * 按 FIFO 重放队列。失败的项重新入队(attempts+1)。
     * 同时只允许一个 flush 在跑。
     */
    fun tryFlush(): Job {
        return scope.launch {
            if (!flushing.compareAndSet(false, true)) {
                Log.d(TAG, "flush already running, skip")
                return@launch
            }
            try {
                val list = loadAll()
                if (list.isEmpty()) return@launch
                Log.i(TAG, "flush starting, ${list.size} ops")
                val remaining = mutableListOf<Op>()
                for (op in list) {
                    val ok = try {
                        replay(op)
                    } catch (e: Exception) {
                        Log.w(TAG, "replay error: $e")
                        false
                    }
                    if (!ok) {
                        val nextAttempts = op.attempts + 1
                        if (nextAttempts > 10) {
                            Log.w(TAG, "drop after 10 attempts: ${op.type}")
                        } else {
                            remaining.add(op.copy(attempts = nextAttempts))
                            // 指数退避: 失败后等一下再试下一项
                            delay(minOf(2000L * nextAttempts, 30_000L))
                        }
                    }
                }
                saveAll(remaining)
                Log.i(TAG, "flush done, remaining=${remaining.size}")
            } finally {
                flushing.set(false)
            }
        }
    }

    private suspend fun replay(op: Op): Boolean {
        val svc = ApiClient.service
        return try {
            when (op.type) {
                "exercise_log" -> {
                    val req = gson.fromJson(op.payload, ExerciseLogRequest::class.java)
                    val resp = svc.addExerciseLog(req)
                    resp.ok
                }
                "body_metric" -> {
                    val req = gson.fromJson(op.payload,
                        com.smartfitness.app.model.BodyMetricRequest::class.java)
                    val resp = svc.addBodyMetric(req)
                    resp.ok
                }
                else -> {
                    Log.w(TAG, "unknown op type: ${op.type}")
                    true  // 丢掉
                }
            }
        } catch (e: IOException) {
            Log.d(TAG, "network error: $e")
            false
        } catch (e: HttpException) {
            // 5xx 重试，其余视为永久失败 (避免死信)
            if (e.code() in 500..599) false else true
        } catch (e: Exception) {
            Log.w(TAG, "replay exception: $e")
            false
        }
    }
}
