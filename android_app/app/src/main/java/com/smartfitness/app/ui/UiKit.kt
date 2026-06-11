package com.smartfitness.app.ui

import android.content.Context
import android.graphics.Typeface
import android.util.TypedValue
import android.widget.LinearLayout
import android.widget.TextView
import com.google.android.material.card.MaterialCardView
import com.smartfitness.app.R

/**
 * 设计规范工具 (Qwen-VL redesign 2026-06-12, docs/UI_REDESIGN_QWEN_VL.md)
 * 卡片 20dp 圆角 / 2dp elevation / surface 白底; 文字 on_surface 主 + on_surface_secondary 辅.
 */
object UiKit {

    fun dp(ctx: Context, v: Int): Int = (ctx.resources.displayMetrics.density * v).toInt()

    /** 标准卡片: 返回 (卡片, 内容容器) */
    fun card(ctx: Context): Pair<MaterialCardView, LinearLayout> {
        val inner = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(ctx, 16), dp(ctx, 16), dp(ctx, 16), dp(ctx, 16))
        }
        val cardView = MaterialCardView(ctx).apply {
            // Keep 风格: 扁平白卡 + 细描边, 不用阴影
            radius = dp(ctx, 16).toFloat()
            cardElevation = 0f
            strokeWidth = dp(ctx, 1)
            strokeColor = ctx.getColor(R.color.divider)
            setCardBackgroundColor(ctx.getColor(R.color.surface))
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dp(ctx, 12) }
            addView(inner)
        }
        return cardView to inner
    }

    fun cardTitle(ctx: Context, text: String): TextView = TextView(ctx).apply {
        this.text = text
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
        typeface = Typeface.create("sans-serif-medium", Typeface.BOLD)
        setTextColor(ctx.getColor(R.color.on_surface))
        setPadding(0, 0, 0, dp(ctx, 8))
    }

    fun body(ctx: Context, text: String, sizeSp: Float = 15f): TextView = TextView(ctx).apply {
        this.text = text
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp)
        setTextColor(ctx.getColor(R.color.on_surface))
    }

    fun caption(ctx: Context, text: String): TextView = TextView(ctx).apply {
        this.text = text
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        setTextColor(ctx.getColor(R.color.on_surface_secondary))
    }

    /** 次级动作: 描边按钮 (透明底 + 主绿描边文字), 避免满屏实心大绿块 */
    fun outlinedButton(ctx: Context, text: String, onClick: () -> Unit) =
        com.google.android.material.button.MaterialButton(
            ctx, null,
            com.google.android.material.R.attr.materialButtonOutlinedStyle
        ).apply {
            this.text = text
            cornerRadius = dp(ctx, 12)
            setTextColor(ctx.getColor(R.color.primary))
            strokeColor = android.content.res.ColorStateList.valueOf(ctx.getColor(R.color.primary))
            setOnClickListener { onClick() }
        }
}
