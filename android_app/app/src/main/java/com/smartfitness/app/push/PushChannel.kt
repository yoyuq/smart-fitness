package com.smartfitness.app.push

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.smartfitness.app.MainActivity
import com.smartfitness.app.R

/**
 * E-10 推送通知 — 本地通知通道封装。
 *
 * 当前实现:
 *   - 训练提醒 (定时本地通知)
 *   - 训练完成提示
 *   - 设备离线告警
 *
 * 未来接 FCM 时:
 *   - 在 MyFirebaseMessagingService.onMessageReceived 内调用 show()
 *   - 在登录成功后上报 FCM token 到后端 /api/v2/devices/push-token
 */
object PushChannel {
    private const val CHANNEL_ID = "smart_fitness_default"
    private const val CHANNEL_NAME = "训练通知"
    private const val CHANNEL_DESC = "训练提醒、完成通知、设备状态"

    fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                val ch = NotificationChannel(
                    CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_DEFAULT
                ).apply {
                    description = CHANNEL_DESC
                }
                nm.createNotificationChannel(ch)
            }
        }
    }

    fun show(
        ctx: Context,
        title: String,
        body: String,
        notifId: Int = (System.currentTimeMillis() and 0x7fffffff).toInt()
    ) {
        ensureChannel(ctx)
        val intent = Intent(ctx, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pendingFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        else
            PendingIntent.FLAG_UPDATE_CURRENT
        val pi = PendingIntent.getActivity(ctx, 0, intent, pendingFlags)

        val notif = NotificationCompat.Builder(ctx, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()

        try {
            NotificationManagerCompat.from(ctx).notify(notifId, notif)
        } catch (_: SecurityException) {
            // Android 13+ 用户未授予 POST_NOTIFICATIONS, 静默忽略
        }
    }

    /** 训练计划提醒 (常用) */
    fun trainingReminder(ctx: Context, planName: String) {
        show(ctx, "训练时间到了", "今日计划: $planName, 准备好开始了吗?")
    }

    /** 训练完成通知 */
    fun sessionCompleted(ctx: Context, reps: Int, avgScore: Int) {
        show(ctx, "训练完成", "本次完成 $reps 个动作, 平均评分 $avgScore 分")
    }
}
