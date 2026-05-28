package com.smartfitness.app.camera

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import kotlinx.coroutines.*
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.InputStream
import java.util.concurrent.TimeUnit

/**
 * MJPEG (multipart/x-mixed-replace) stream client.
 * 适配 ESP32-CAM 的 /stream endpoint, 每收到一帧 JPEG 就调 onFrame.
 *
 * 用法:
 *   val mj = MjpegClient("http://192.168.72.20:81/stream", lifecycleScope) { bmp ->
 *     imageView.setImageBitmap(bmp)
 *   }
 *   mj.start()
 *   // ...
 *   mj.stop()
 */
class MjpegClient(
    private val url: String,
    private val scope: CoroutineScope,
    private val onFrame: (Bitmap) -> Unit,
    private val onError: ((Throwable) -> Unit)? = null
) {
    @Volatile private var running = false
    @Volatile private var dropFlag = false
    private var job: Job? = null

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)  // stream 不能超时
        .build()

    fun start() {
        if (running) return
        running = true
        job = scope.launch(Dispatchers.IO) {
            try {
                val req = Request.Builder().url(url).build()
                val resp = client.newCall(req).execute()
                val body = resp.body ?: throw Exception("empty body")
                val input: InputStream = body.byteStream()
                readMjpegStream(input)
                body.close()
            } catch (e: Exception) {
                if (running) onError?.invoke(e)
            } finally {
                running = false
            }
        }
    }

    fun stop() {
        running = false
        job?.cancel()
        job = null
    }

    /** 设置丢帧标志: 上一帧 UI 还没显示完, 下一帧到了就跳过 decode */
    fun markBusy() { dropFlag = true }
    fun markReady() { dropFlag = false }

    /**
     * 解析 multipart/x-mixed-replace 流.
     * Boundary 在 sketch 里写死是 "123456789000000000000987654321".
     * 每帧结构:
     *   --boundary\r\n
     *   Content-Type: image/jpeg\r\n
     *   Content-Length: NNNN\r\n
     *   \r\n
     *   <JPEG bytes ...>
     *   \r\n
     */
    private suspend fun readMjpegStream(input: InputStream) {
        val bufSize = 64 * 1024
        val buf = ByteArray(bufSize)
        var acc = ByteArray(0)

        while (running) {
            val n = input.read(buf)
            if (n < 0) break
            acc += buf.copyOfRange(0, n)

            // 解 1 个或多个完整帧
            while (true) {
                val frame = extractFrame(acc) ?: break
                acc = frame.second  // 余下未处理数据
                if (!running) break
                if (dropFlag) continue  // UI 不要时丢帧
                try {
                    val bmp = BitmapFactory.decodeByteArray(frame.first, 0, frame.first.size)
                    if (bmp != null) {
                        withContext(Dispatchers.Main) {
                            if (running) onFrame(bmp)
                        }
                    }
                } catch (_: Exception) {}
            }
        }
    }

    /**
     * 从 buf 里抽出一个完整的 JPEG 帧, 返回 (jpegBytes, restBuf).
     * 找 SOI (FF D8) 到 EOI (FF D9), JPEG 自包含, 不依赖 Content-Length.
     * 这个方法简单粗暴, 但兼容性最好.
     */
    private fun extractFrame(buf: ByteArray): Pair<ByteArray, ByteArray>? {
        if (buf.size < 10) return null
        // SOI: FF D8
        var soi = -1
        for (i in 0 until buf.size - 1) {
            if (buf[i] == 0xFF.toByte() && buf[i + 1] == 0xD8.toByte()) {
                soi = i; break
            }
        }
        if (soi < 0) return null
        // EOI: FF D9
        var eoi = -1
        var i = soi + 2
        while (i < buf.size - 1) {
            if (buf[i] == 0xFF.toByte() && buf[i + 1] == 0xD9.toByte()) {
                eoi = i + 2; break
            }
            i++
        }
        if (eoi < 0) return null  // 帧还没传完, 留着等下一次 read
        val jpeg = buf.copyOfRange(soi, eoi)
        val rest = buf.copyOfRange(eoi, buf.size)
        return Pair(jpeg, rest)
    }
}
