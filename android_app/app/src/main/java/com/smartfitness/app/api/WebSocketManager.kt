package com.smartfitness.app.api

import android.util.Log
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * WebSocket 封装。
 *
 * 2026-05-25 后端新增 `/ws/coach/{user_id}` 端点，按 user_id 跨 session 广播
 * coach_update 事件。旧 `/api/v2/ws/session/{session_id}` 端点继续支持，
 * 用 [connectSession] 切回。
 *
 * 特性:
 *  - 自动指数退避重连 (1s → 2s → 4s → 8s → 16s, 上限 30s)
 *  - close() 主动关闭后不再重连
 *  - 30s 一次心跳 ping (与 OkHttp pingInterval 配合)
 */
class WebSocketManager(
    private val listener: Listener
) {

    interface Listener {
        fun onOpen()
        fun onMessage(text: String)
        fun onClosed(code: Int, reason: String)
        fun onFailure(t: Throwable)
    }

    private var webSocket: WebSocket? = null
    private var lastUrl: String? = null
    private val tag = "SmartFitnessWS"

    private val manuallyClosed = AtomicBoolean(false)
    private val reconnectAttempts = AtomicInteger(0)

    /**
     * 按 user_id 订阅当前账号下所有 session 的实时推理结果。
     * 这是 2026-05-25 后端新增的推荐通道。
     */
    fun connectCoach(userId: Long) {
        val baseHost = ApiClient.BASE_URL
            .removePrefix("http://")
            .removePrefix("https://")
            .trimEnd('/')
        val scheme = if (ApiClient.BASE_URL.startsWith("https")) "wss" else "ws"
        connectInternal("$scheme://$baseHost/ws/coach/$userId")
    }

    /** 按 session_id 订阅（旧通道，需手动传 sid）。 */
    fun connectSession(sessionId: String) {
        val baseHost = ApiClient.BASE_URL
            .removePrefix("http://")
            .removePrefix("https://")
            .trimEnd('/')
        val scheme = if (ApiClient.BASE_URL.startsWith("https")) "wss" else "ws"
        connectInternal("$scheme://$baseHost/api/v2/ws/session/$sessionId")
    }

    /** 兼容旧调用 connect(sessionId)。 */
    @Deprecated("use connectSession or connectCoach", ReplaceWith("connectSession(sessionId)"))
    fun connect(sessionId: String) = connectSession(sessionId)

    private fun connectInternal(url: String) {
        manuallyClosed.set(false)
        lastUrl = url
        val builder = Request.Builder().url(url)
        ApiClient.token?.let { builder.header("Authorization", "Bearer $it") }
        Log.d(tag, "Connecting to $url (attempt=${reconnectAttempts.get()})")
        webSocket = ApiClient.okHttpClient.newWebSocket(builder.build(), socketListener)
    }

    fun send(text: String): Boolean = webSocket?.send(text) ?: false

    fun close(code: Int = 1000, reason: String = "client closing") {
        manuallyClosed.set(true)
        webSocket?.close(code, reason)
        webSocket = null
    }

    private fun scheduleReconnect() {
        if (manuallyClosed.get()) return
        val url = lastUrl ?: return
        val attempts = reconnectAttempts.incrementAndGet()
        if (attempts > 12) {
            Log.w(tag, "Reconnect giving up after $attempts attempts")
            return
        }
        val backoffMs = (1000L shl minOf(attempts - 1, 4)).coerceAtMost(30_000L)
        Log.d(tag, "Reconnect scheduled in ${backoffMs}ms (attempt=$attempts)")
        ApiClient.okHttpClient.dispatcher.executorService.execute {
            try {
                Thread.sleep(backoffMs)
                if (!manuallyClosed.get()) connectInternal(url)
            } catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
            }
        }
    }

    private val socketListener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.d(tag, "WebSocket open")
            reconnectAttempts.set(0)
            listener.onOpen()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            listener.onMessage(text)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            listener.onMessage(bytes.utf8())
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            Log.d(tag, "WS closed: $code $reason")
            listener.onClosed(code, reason)
            // 服务端主动关或异常关 (非 1000) 时也尝试重连
            if (code != 1000 && !manuallyClosed.get()) scheduleReconnect()
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.e(tag, "WS failure", t)
            listener.onFailure(t)
            scheduleReconnect()
        }
    }
}
