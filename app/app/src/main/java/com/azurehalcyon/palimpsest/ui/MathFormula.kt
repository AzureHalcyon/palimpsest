package com.azurehalcyon.palimpsest.ui

import android.graphics.Bitmap
import android.graphics.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.ui.text.Placeholder
import androidx.compose.ui.text.PlaceholderVerticalAlign
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import ru.noties.jlatexmath.JLatexMathDrawable

/**
 * ★★ **LaTeX 真排版**（2026-10-06）。
 *
 * 用户口径：「就让 markdown 也能渲染 Latex」「**不是做成 unicode，是真渲染**，
 * 要不然复杂公式可读性不好」。
 * ⇒ 引 `ru.noties:jlatexmath-android`（JLaTeXMath 的 Android 移植，**离线**：字体随 aar 打进 APK），
 *   把公式渲染成位图。⚠️ 依赖坐标与 API 都是**先确认再写**的（见 `app/build.gradle.kts` 里的注释）。
 *
 * 两条路：
 *  · [MathDisplay] —— **独立成行的公式**（`$$…$$` / `\[…\]`）：居中一块，位图直接画。
 *  · [inlineMath] —— **行内公式**（`$…$` / `\(…\)`）：走 `InlineTextContent`
 *    （Compose 里把图片塞进文字流的唯一正规做法）。
 *
 * ⛔ **渲染失败绝不吞内容**：LaTeX 写错是很常见的（模型给的东西不保证合法）⇒
 *   失败时退回 [Latex.toText] 的**可读化文本**（连原文一起看得见），绝不显示空白。
 * ⚠️ 位图**按 (tex, 字号, 颜色) 记忆化**（`remember`）⇒ 同一段文字重组时不重复渲染。
 */
internal object MathRender {

    /** 行内公式渲染用的字号（跟正文 `bodyMedium` 接近，略小一点免得撑行高）。 */
    const val INLINE_SP = 15f

    /** 独立成行公式的字号。 */
    const val DISPLAY_SP = 18f

    /**
     * ★★ **位图缓存**（2026-10-06）：键 = 公式 + 字号 + 颜色。
     *
     * 为什么必须有：`bitmap()` 原来**在组合期同步跑**，而 JLaTeXMath 首次渲染要
     * 解字体资源 + 画位图 —— 【真机实测】刚启动那一段（正好是用户"第一次点输入框"的时候）
     * 主线程一帧 **200–400ms**（89 帧 · 30% 卡顿 · p90 200ms / p99 400ms）。
     * ⇒ 现在：① 渲染挪到 `Dispatchers.Default`（见 [MathDisplay]）；② 结果按公式缓存，
     *    同一段文字重组/滚动时**一次都不重渲**。
     * ⚠️ 线程安全：`Bitmap`/`Canvas` 的软件绘制可以在后台线程做；缓存用并发 Map。
     */
    private val cache = java.util.concurrent.ConcurrentHashMap<String, Bitmap>()

    private fun key(tex: String, textSizePx: Float, argb: Int) =
        "$argb|${textSizePx.toInt()}|$tex"

    /**
     * 把一段 LaTeX 渲染成位图（**带缓存**）。**失败返回 null**（调用方负责退回文本）。
     * ⚠️ 会阻塞当前线程（首次渲染可能几十毫秒）⇒ **不要在组合期直接调**，
     *   走 [renderAsync] 或 [prewarm]。
     */
    fun bitmap(tex: String, textSizePx: Float, argb: Int): Bitmap? {
        val k = key(tex, textSizePx, argb)
        cache[k]?.let { return it }
        val bmp = render(tex, textSizePx, argb) ?: return null
        cache[k] = bmp
        return bmp
    }

    /** 只在后台用：把结果塞进缓存，供随后的同步取用。 */
    fun prewarm(tex: String, textSizePx: Float, argb: Int) {
        bitmap(tex, textSizePx, argb)
    }

    /** 同步取缓存（**不渲染**）；没热就返回 null ⇒ 调用方先用退路文本顶上。 */
    fun cached(tex: String, textSizePx: Float, argb: Int): Bitmap? =
        cache[key(tex, textSizePx, argb)]

    private fun render(tex: String, textSizePx: Float, argb: Int): Bitmap? = try {
        val d = JLatexMathDrawable.builder(tex)
            .textSize(textSizePx)
            .color(argb)
            .align(JLatexMathDrawable.ALIGN_CENTER)
            .padding(6)
            .build()
        val w = d.intrinsicWidth.coerceAtLeast(1)
        val h = d.intrinsicHeight.coerceAtLeast(1)
        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        d.setBounds(0, 0, w, h)
        d.draw(Canvas(bmp))
        bmp
    } catch (t: Throwable) {
        // 公式非法（或字体缺字）⇒ 交给调用方退回文本
        null
    }
}

/**
 * **独立成行的公式**：居中显示，渲染不出来就退回可读化文本。
 *
 * ★★ 渲染**在后台线程**做（`produceState` + `Dispatchers.Default`），组合期只查缓存 ——
 *    否则公式会在第一帧同步渲染完，把启动那一段的主线程帧拖到 200–400ms
 *    （实测就是用户说的"刚启动第一次点输入框奇慢无比"）。没热之前先显示退路文本。
 */
@Composable
internal fun MathDisplay(tex: String, color: Color, modifier: Modifier = Modifier) {
    val density = LocalDensity.current
    val px = with(density) { MathRender.DISPLAY_SP.sp.toPx() }
    val argb = color.toArgb()
    val bmp by produceState(initialValue = MathRender.cached(tex, px, argb), tex, px, argb) {
        if (value == null) {
            value = withContext(Dispatchers.Default) { MathRender.bitmap(tex, px, argb) }
        }
    }
    if (bmp != null) {
        val wDp = with(density) { bmp!!.width.toDp() }
        val hDp = with(density) { bmp!!.height.toDp() }
        Image(
            bitmap = bmp!!.asImageBitmap(),
            contentDescription = tex,
            contentScale = ContentScale.Fit,
            modifier = modifier
                .fillMaxWidth()
                .size(width = minOf(wDp, 320.dp), height = hDp),
        )
    } else {
        Text(
            text = Latex.toText(tex),
            style = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
            color = color,
            modifier = modifier.fillMaxWidth(),
        )
    }
}

/**
 * **行内公式** → `(占位符, InlineTextContent)`。
 *
 * 为什么用占位符：Compose 的文字流里塞图片只有这一条正规路
 * （`AnnotatedString` 放一个 `\uFFFC`，再由 `Text(inlineContent = …)` 把那个位置换成组件）。
 * ⚠️ 占位符的尺寸单位是 `TextUnit`（sp）⇒ 位图 px 要按 **fontScale** 折算回去，
 *   否则系统字体调大时公式会和文字错位。
 * ⛔ 渲染失败 ⇒ 返回 null，调用方照旧当普通文本处理（不吞内容）。
 */
internal fun inlineMath(
    tex: String,
    density: androidx.compose.ui.unit.Density,
    color: Color,
): Pair<String, InlineTextContent>? {
    val px = with(density) { MathRender.INLINE_SP.sp.toPx() }
    // ★ 这里**只查缓存**、不渲染：行内公式是在 `inline()` 里**同步**拼 AnnotatedString 时用到的，
    //   在这儿渲染等于把代价压回组合期（那正是"刚启动第一次点输入框奇慢"的成因）。
    //   没热 ⇒ 返回 null，调用方按普通文本处理；后台 `prewarm` 热好之后，
    //   `MdText` 里那个 `warmTick` 会触发重建，这次就能取到图。
    val bmp = MathRender.cached(tex, px, color.toArgb()) ?: return null
    val wSp = (bmp.width / density.fontScale / density.density).sp
    val hSp = (bmp.height / density.fontScale / density.density).sp
    val key = "\uFFFC" + tex.hashCode()
    val content = InlineTextContent(
        Placeholder(width = wSp, height = hSp, placeholderVerticalAlign = PlaceholderVerticalAlign.Center),
    ) {
        Image(
            bitmap = bmp.asImageBitmap(),
            contentDescription = tex,
            contentScale = ContentScale.Fit,
            alignment = Alignment.Center,
            modifier = Modifier.fillMaxWidth(),
        )
    }
    return key to content
}
