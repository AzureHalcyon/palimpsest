package com.azurehalcyon.palimpsest.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import com.azurehalcyon.palimpsest.R
import kotlinx.coroutines.delay

/**
 * ★★ **开屏**（2026-10-06 用户要求）。
 *
 * 用户口径：「可以开屏先显示一个开屏动画（比如给 APP 画的 logo 扩成一个图），
 * 给后台一点时间先加载，等一两秒，**把时间错开**」。
 *
 * 做法上有意**不挡加载**：开屏是**盖在外壳之上的一层**（`MainActivity` 里并排摆，
 * 不是 `if (开屏) 显示它 else 显示外壳`）—— 外壳照常组合、照常跑
 * `chat_open` / `poolView` 那些加载，只是这一层先挡着
 * ⇒ 用户看到的是 logo 动画，后台在这 1–2 秒里把"打开这一场 + 起 Python"做完。
 * ⛔ 若写成"先只显示开屏、加载完再显示外壳"，那就**把加载推后了**，正好相反。
 *
 * ★ 点一下可以跳过（用户手动跳过比等动画快）。
 * ⚠️ logo 用的是自适应图标的前景层（透明底 PNG，另一会话画的）——
 *   `painterResource` 会按当前密度自动取最合适的那份。
 */
@Composable
internal fun SplashOverlay(onDone: () -> Unit) {
    var visible by remember { mutableStateOf(true) }
    val fade by animateFloatAsState(
        targetValue = if (visible) 1f else 0f,
        animationSpec = tween(durationMillis = SplashFadeMs),
        label = "splashFade",
    )
    val scale = remember { Animatable(SplashFromScale) }
    LaunchedEffect(Unit) {
        scale.animateTo(1f, tween(durationMillis = SplashGrowMs, easing = FastOutSlowInEasing))
        delay(SplashHoldMs.toLong())      // ← 后台在这段里把加载做完
        visible = false
        delay(SplashFadeMs.toLong())      // 等淡出走完再摘掉这一层
        onDone()
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .graphicsLayer { alpha = fade }
            .background(MaterialTheme.colorScheme.background)
            // 点一下跳过：用户想快就快（动画只是"顺路给后台一点时间"，不是仪式）
            .clickable { visible = false },
        contentAlignment = Alignment.Center,
    ) {
        Image(
            painter = painterResource(R.mipmap.ic_launcher_foreground),
            contentDescription = null,
            modifier = Modifier
                .size(SplashLogoSize)
                .graphicsLayer {
                    scaleX = scale.value
                    scaleY = scale.value
                }
                .alpha(fade),
        )
    }
}

/** logo 起始缩放（从略小放大到 1 ⇒ 有"长出来"的观感）。 */
private const val SplashFromScale = 0.86f

/** 放大时长。 */
private const val SplashGrowMs = 650

/** 停在放大后那一下的时长 —— **这一段就是给后台的时间**。 */
private const val SplashHoldMs = 700

/** 淡出时长。 */
private const val SplashFadeMs = 320

/** logo 在开屏上的尺寸（比图标大得多：用户说"扩成一个图"）。 */
private val SplashLogoSize = 220.dp
