package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 一个**够用的** Markdown 渲染器。
 *
 * ★ 为什么不用 `com.mikepenz:multiplatform-markdown-renderer`（首选方案）：
 *   本机在沙箱里 HTTPS 全坏（`SEC_E_NO_CREDENTIALS`，与代理和目标站点无关），
 *   没法**先确认那个 AAR 的坐标与版本**；而它是**多平台**库，Android 变体的 artifact
 *   名（`-android` 后缀，还是 `-jvm`）只能靠拉 Gradle 元数据确认 —— 一次构建 1–3 分钟，
 *   赌错就要重来。任务书写得很清楚：**编译不过就退成纯文本 + 手工处理换行，
 *   不要为此升级工具链**。与其把整个交付押在一个拿不准的坐标上，
 *   不如自己把渲染做掉：零新依赖、行为完全可控、不会拖累构建。
 *   （模块 `:uikit` 是直接从源码 vendor 的，同理也没有引入该库。）
 *
 * 覆盖对话里真正会出现的语法：
 *   `# 标题` / `**粗体**` / `*斜体*` / `` `行内码` `` / ``` 代码块 ```
 *   / `- * 列表` / `1. 列表` / `> 引用` / `---` 分隔线 / `[文字](链接)` / 裸 URL
 *
 * 不认识的行**原样显示**（不会把内容吃掉）—— 对"读它说了什么"这件事，
 * 保留原文永远比猜错安全。
 */
private sealed interface MdBlock {
    data class Heading(val level: Int, val text: String) : MdBlock
    data class Paragraph(val text: String) : MdBlock
    data class Bullet(val text: String, val ordered: Boolean, val number: Int) : MdBlock
    data class Code(val text: String, val lang: String) : MdBlock
    data class Quote(val text: String) : MdBlock
    /** ★ **独立成行的公式**（`$$…$$` / `\[…\]`）—— 渲染成居中的一块，见 [Latex]。 */
    data class Math(val tex: String) : MdBlock
    data object Rule : MdBlock
}

@Composable
fun MarkdownText(
    markdown: String,
    modifier: Modifier = Modifier,
    color: androidx.compose.ui.graphics.Color = MaterialTheme.colorScheme.onSurface,
) {
    val blocks = remember(markdown) { parseMarkdown(markdown) }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        blocks.forEach { b ->
            when (b) {
                is MdBlock.Heading -> MdText(
                    src = b.text,
                    style = when (b.level) {
                        1 -> MaterialTheme.typography.titleLarge
                        2 -> MaterialTheme.typography.titleMedium
                        else -> MaterialTheme.typography.titleSmall
                    },
                    color = color,
                    fontWeight = FontWeight.SemiBold,
                )

                is MdBlock.Paragraph -> MdText(
                    src = b.text,
                    style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp),
                    color = color,
                )

                is MdBlock.Bullet -> Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = if (b.ordered) "${b.number}." else "•",
                        style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp),
                        color = color.copy(alpha = 0.7f),
                    )
                    MdText(
                        src = b.text,
                        style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp),
                        color = color,
                        modifier = Modifier.weight(1f),
                    )
                }

                is MdBlock.Code -> Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                ) {
                    Column {
                        if (b.lang.isNotBlank()) {
                            Text(
                                text = b.lang,
                                style = MaterialTheme.typography.labelSmall,
                                color = color.copy(alpha = 0.55f),
                            )
                        }
                        Text(
                            text = AnnotatedString(b.text),
                            style = MaterialTheme.typography.bodySmall.copy(
                                fontFamily = FontFamily.Monospace,
                                lineHeight = 18.sp,
                            ),
                            color = color,
                        )
                    }
                }

                is MdBlock.Quote -> Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Box(
                        modifier = Modifier
                            .width(3.dp)
                            .clip(RoundedCornerShape(2.dp))
                            .background(color.copy(alpha = 0.35f))
                            .padding(top = 2.dp, bottom = 2.dp),
                    ) {}
                    MdText(
                        src = b.text,
                        style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp),
                        color = color.copy(alpha = 0.85f),
                        modifier = Modifier.weight(1f),
                    )
                }

                MdBlock.Rule -> Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 2.dp)
                        .background(color.copy(alpha = 0.2f))
                        .padding(top = 0.5.dp),
                ) {}

                // ★ **公式**：**真排版**（`MathFormula.kt`，JLaTeXMath 渲染成位图），居中一块。
                //   ⛔ 不是"把 LaTeX 换成 Unicode"—— 复杂公式那样读不了（用户明确要求）。
                //   渲染不出来时 `MathDisplay` 自己会退回可读化文本（不吞内容）。
                is MdBlock.Math -> Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    MathDisplay(tex = b.tex, color = color)
                }
            }
        }
    }
}

// --------------------------------------------------------------------------- 解析
private fun parseMarkdown(src: String): List<MdBlock> {
    val out = mutableListOf<MdBlock>()
    val lines = src.replace("\r\n", "\n").split('\n')
    val para = StringBuilder()
    var i = 0

    fun flushPara() {
        if (para.isNotEmpty()) {
            out += MdBlock.Paragraph(para.toString().trim())
            para.setLength(0)
        }
    }

    while (i < lines.size) {
        val raw = lines[i]
        val line = raw.trimEnd()
        val trimmed = line.trimStart()
        when {
            // ``` 代码块（到下一个 ``` 或文件末尾）
            trimmed.startsWith("```") -> {
                flushPara()
                val lang = trimmed.removePrefix("```").trim()
                val body = StringBuilder()
                i++
                while (i < lines.size && !lines[i].trimStart().startsWith("```")) {
                    body.append(lines[i]).append('\n')
                    i++
                }
                if (i < lines.size) i++          // 吃掉收尾的 ```
                out += MdBlock.Code(body.toString().trimEnd('\n'), lang)
            }

            trimmed.isEmpty() -> {
                flushPara()
                i++
            }

            // ★ **独立成行的公式**：`$$…$$`（可跨行）与 `\[…\]`
            //   ⚠️ 必须排在 `$` 行内处理之前（这里处理的是"整行就是公式"那种）。
            trimmed.startsWith("$$") || trimmed.startsWith("\\[") -> {
                flushPara()
                val open = if (trimmed.startsWith("$$")) "$$" else "\\["
                val close = if (open == "$$") "$$" else "\\]"
                val rest = trimmed.removePrefix(open)
                val body = StringBuilder()
                if (rest.contains(close)) {
                    body.append(rest.substringBefore(close))
                    i++
                } else {
                    body.append(rest)
                    i++
                    while (i < lines.size) {
                        val cur = lines[i]
                        if (cur.contains(close)) {
                            body.append('\n').append(cur.substringBefore(close))
                            i++
                            break
                        }
                        body.append('\n').append(cur)
                        i++
                    }
                }
                out += MdBlock.Math(body.toString().trim())
            }

            trimmed == "---" || trimmed == "***" || trimmed == "___" -> {
                flushPara()
                out += MdBlock.Rule
                i++
            }

            trimmed.startsWith("#") -> {
                flushPara()
                val level = trimmed.takeWhile { it == '#' }.length.coerceIn(1, 6)
                out += MdBlock.Heading(level, trimmed.drop(level).trim())
                i++
            }

            trimmed.startsWith(">") -> {
                flushPara()
                out += MdBlock.Quote(trimmed.drop(1).trim())
                i++
            }

            isBullet(trimmed) -> {
                flushPara()
                val ordered = trimmed.first().isDigit()
                val text = trimmed.dropWhile { it == '-' || it == '*' || it == '+' || it.isDigit() || it == '.' || it == ')' }.trim()
                val num = if (ordered) {
                    trimmed.takeWhile { it.isDigit() }.toIntOrNull() ?: 1
                } else 0
                out += MdBlock.Bullet(text, ordered, num)
                i++
            }

            else -> {
                if (para.isNotEmpty()) para.append(' ')
                para.append(trimmed)
                i++
            }
        }
    }
    flushPara()
    return out
}

private fun isBullet(s: String): Boolean {
    if (s.startsWith("- ") || s.startsWith("* ") || s.startsWith("+ ")) return true
    // 有序列表：`1. ` / `1) `
    val digits = s.takeWhile { it.isDigit() }
    if (digits.isEmpty() || digits.length > 2) return false
    val rest = s.drop(digits.length)
    return rest.startsWith(". ") || rest.startsWith(") ")
}

// --------------------------------------------------------------------------- 行内
private val URL_RE = Regex("""https?://[^\s<>()\[\]「」，。；]+""")

/**
 * 行内标记：`**粗**` / `*斜*` / `` `码` `` / `[文字](url)` / `$公式$` / `\(公式\)`，外加裸 URL 变可点。
 *
 * ★ 公式（2026-10-06）：给了 [density] 与 [out] 时，行内公式走
 *   **[InlineTextContent] 真渲染**（把 JLaTeXMath 渲出来的位图塞进文字流）；
 *   不给就退回**可读化文本**（[Latex.toText]）—— 那是给"不方便放图片"的调用点留的退路。
 */
internal fun inline(
    src: String,
    density: Density? = null,
    spanColor: Color = Color.Unspecified,
    out: MutableMap<String, InlineTextContent>? = null,
): AnnotatedString = buildAnnotatedString {
    val urlRanges = URL_RE.findAll(src).toList()
    var i = 0
    while (i < src.length) {
        val urlHit = urlRanges.firstOrNull { it.range.first == i }
        if (urlHit != null) {
            val url = urlHit.value
            withLink(LinkAnnotation.Url(url)) {
                withStyle(
                    SpanStyle(
                        color = androidx.compose.ui.graphics.Color(0xFF4C8DFF),
                        textDecoration = androidx.compose.ui.text.style.TextDecoration.Underline,
                    ),
                ) { append(url) }
            }
            i = urlHit.range.last + 1
            continue
        }
        when {
            src.startsWith("**", i) -> {
                val end = src.indexOf("**", i + 2)
                if (end > 0) {
                    withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
                        append(inline(src.substring(i + 2, end), density, spanColor, out))
                    }
                    i = end + 2
                } else {
                    append(src[i]); i++
                }
            }

            src[i] == '`' -> {
                val end = src.indexOf('`', i + 1)
                if (end > 0) {
                    withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) {
                        append(src.substring(i + 1, end))
                    }
                    i = end + 1
                } else {
                    append(src[i]); i++
                }
            }

            // ★ **行内公式**：`$…$` 与 `\(…\)` —— **真渲染**（走 `InlineTextContent` 塞位图）。
            //   ⚠️ `$` 的判定刻意收窄（照 KaTeX 那套）：开 `$` 后**不能紧跟空格**、
            //      闭 `$` 前**不能是空格**、内容里不能再有 `$`。否则「花了 $5 和 $10」
            //      这种日常句子会被误当成公式。
            //   ⛔ 渲染不出来（LaTeX 非法）⇒ 退回**可读化文本**，绝不吞内容。
            src.startsWith("\\(", i) -> {
                val end = src.indexOf("\\)", i + 2)
                val tex = if (end > 0) src.substring(i + 2, end) else null
                val rendered = if (tex != null && out != null && density != null) {
                    inlineMath(tex, density, spanColor)
                } else null
                when {
                    rendered != null && tex != null -> {
                        out!![rendered.first] = rendered.second
                        append(rendered.first)
                        i = end + 2
                    }
                    tex != null -> {
                        withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) {
                            append(Latex.toText(tex))
                        }
                        i = end + 2
                    }
                    else -> {
                        append(src[i]); i++
                    }
                }
            }

            src[i] == '$' -> {
                val body = matchInlineMath(src, i)
                val rendered = if (body != null && out != null && density != null) {
                    inlineMath(body, density, spanColor)
                } else null
                when {
                    rendered != null && body != null -> {
                        out!![rendered.first] = rendered.second
                        append(rendered.first)
                        i += body.length + 2
                    }
                    body != null -> {
                        withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) {
                            append(Latex.toText(body))
                        }
                        i += body.length + 2
                    }
                    else -> {
                        append(src[i]); i++
                    }
                }
            }

            // 认识的位置参数（[文字](url)）与裸 URL 之后的兜底：照抄

            src.startsWith("[", i) -> {
                val close = src.indexOf(']', i)
                if (close > 0 && close + 1 < src.length && src[close + 1] == '(') {
                    val end = src.indexOf(')', close + 2)
                    if (end > 0) {
                        val label = src.substring(i + 1, close)
                        val href = src.substring(close + 2, end)
                        withLink(LinkAnnotation.Url(href)) {
                            withStyle(
                                SpanStyle(
                                    color = androidx.compose.ui.graphics.Color(0xFF4C8DFF),
                                    textDecoration = androidx.compose.ui.text.style.TextDecoration.Underline,
                                ),
                            ) { append(label) }
                        }
                        i = end + 1
                    } else {
                        append(src[i]); i++
                    }
                } else {
                    append(src[i]); i++
                }
            }

            // 单个 * 当斜体；`**` 已在上面处理
            src[i] == '*' -> {
                val end = src.indexOf('*', i + 1)
                if (end > i + 1) {
                    withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                        append(inline(src.substring(i + 1, end), density, spanColor, out))
                    }
                    i = end + 1
                } else {
                    append(src[i]); i++
                }
            }

            else -> {
                append(src[i]); i++
            }
        }
    }
}

/**
 * 判断 `src[at]` 这个 `$` 是不是**行内公式的开头**：是就返回公式体（不含两个 `$`），否则 null。
 *
 * 判定刻意收窄（照 KaTeX auto-render 那套规则）——为的是别把日常句子里的钱数当成公式：
 *   ① 开 `$` 后**不能**紧跟空格或 `$`；
 *   ② 内容里**不能再有** `$`，也不能跨行；
 *   ③ 闭 `$` 前**不能是空格**（「花了 $5 和 $10」里那个就死在这条）；
 *   ④ 闭 `$` 后**不能紧跟数字**。
 */
private fun matchInlineMath(src: String, at: Int): String? {
    if (at + 1 >= src.length) return null
    val nxt = src[at + 1]
    if (nxt.isWhitespace() || nxt == '$') return null
    var j = at + 1
    while (j < src.length) {
        val c = src[j]
        if (c == '\n') return null
        if (c == '$') {
            if (j == at + 1) return null
            if (src[j - 1].isWhitespace()) return null
            if (j + 1 < src.length && src[j + 1].isDigit()) return null
            return src.substring(at + 1, j)
        }
        j++
    }
    return null
}

/**
 * ★ **一段 markdown → 一个 Text**（行内公式走真渲染）。
 *
 * 为什么要包一层：行内公式要在文字流里放**图片**（`InlineTextContent`），
 * 而 `AnnotatedString` 与那张"占位符 → 组件"的表**必须成对**地算出来。
 * ⚠️ 两者都用 `remember(src, …)` 记住：`inline()` 原来每次重组都重跑一遍（白做功），
 *   而且 `inlineContent` 每次新建会**逼 Text 重新布局**。
 */
@Composable
private fun MdText(
    src: String,
    style: TextStyle,
    color: Color,
    modifier: Modifier = Modifier,
    fontWeight: FontWeight? = null,
) {
    val density = LocalDensity.current
    // ★★ 公式位图**先在后台热**，热好了再重建一次（`warmTick`）：热之前按普通文本显示
    //   ⇒ **组合期一次渲染都不做**。
    //   【为什么必须这样】旧写法在组合期同步渲染，实测刚启动那一段（正好是用户
    //   "第一次点输入框"的时候）主线程一帧 **200–400ms**（p90 200 / p99 400）。
    var warmTick by remember(src) { mutableIntStateOf(0) }
    LaunchedEffect(src, density.density, density.fontScale, color) {
        val snips = inlineMathSnippets(src)
        if (snips.isNotEmpty()) {
            val px = with(density) { MathRender.INLINE_SP.sp.toPx() }
            val argb = color.toArgb()
            withContext(Dispatchers.Default) {
                snips.forEach { MathRender.prewarm(it, px, argb) }
            }
            warmTick++
        }
    }
    val built = remember(src, density.density, density.fontScale, color, warmTick) {
        val out = mutableMapOf<String, InlineTextContent>()
        inline(src, density, color, out) to out
    }
    Text(
        text = built.first,
        style = style,
        color = color,
        modifier = modifier,
        fontWeight = fontWeight,
        inlineContent = built.second,
    )
}

/**
 * 扫出这段 markdown 里所有**行内公式**的 LaTeX 源码（给后台预热用）。
 * ⚠️ 扫法与 inline() 里那套必须一致（同一组分隔符规则），否则会热了不用的、或用了没热的。
 */
internal fun inlineMathSnippets(src: String): List<String> {
    val out = mutableListOf<String>()
    var i = 0
    while (i < src.length) {
        when {
            src.startsWith("\\(", i) -> {
                val end = src.indexOf("\\)", i + 2)
                if (end > 0) {
                    out += src.substring(i + 2, end)
                    i = end + 2
                } else {
                    i++
                }
            }
            src[i] == '\$' -> {
                val body = matchInlineMath(src, i)
                if (body != null) {
                    out += body
                    i += body.length + 2
                } else {
                    i++
                }
            }
            else -> i++
        }
    }
    return out
}
