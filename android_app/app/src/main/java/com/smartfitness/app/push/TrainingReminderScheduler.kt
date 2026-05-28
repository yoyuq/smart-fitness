package com.smartfitness.app.push

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.content.ContextCompat
import java.util.Calendar

/**
 * E-10 训练提醒调度 — 用 AlarmManager 安排本地通知。
 *
 * 用法:
 *   TrainingReminderScheduler.scheduleDaily(ctx, 19, 0, "今日深蹲计划")
 *   TrainingReminderScheduler.cancel(ctx)
 */
object TrainingReminderScheduler {
    private const val REQ_CODE = 7301
    const val EXTRA_PLAN = "plan_name"

    fun scheduleDaily(ctx: Context, hour: Int, minute: Int, planName: String) {
        val am = ContextCompat.getSystemService(ctx, AlarmManager::class.java) ?: return
        val cal = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            if (timeInMillis <= System.currentTimeMillis()) {
                add(Calendar.DAY_OF_YEAR, 1)
            }
        }
        val intent = Intent(ctx, TrainingReminderReceiver::class.java).apply {
            putExtra(EXTRA_PLAN, planName)
        }
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        else
            PendingIntent.FLAG_UPDATE_CURRENT
        val pi = PendingIntent.getBroadcast(ctx, REQ_CODE, intent, flags)
        am.setInexactRepeating(
            AlarmManager.RTC_WAKEUP,
            cal.timeInMillis,
            AlarmManager.INTERVAL_DAY,
            pi
        )
    }

    fun cancel(ctx: Context) {
        val am = ContextCompat.getSystemService(ctx, AlarmManager::class.java) ?: return
        val intent = Intent(ctx, TrainingReminderReceiver::class.java)
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_NO_CREATE
        else
            PendingIntent.FLAG_NO_CREATE
        val pi = PendingIntent.getBroadcast(ctx, REQ_CODE, intent, flags) ?: return
        am.cancel(pi)
        pi.cancel()
    }
}

class TrainingReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val plan = intent.getStringExtra(TrainingReminderScheduler.EXTRA_PLAN) ?: "今日训练"
        PushChannel.trainingReminder(context, plan)
    }
}
