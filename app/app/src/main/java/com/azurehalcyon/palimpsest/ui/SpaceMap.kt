package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * **空间**：把池子画成真正的三维分布，外加「随时间」那一张。
 *
 * ## 这张图为什么不是装饰
 *
 * 原点 = **底色**（"它自己是谁"）。编码器返回的向量本身是 L2 归一化的（实测范数 1±6e-8），
 * 于是 `‖v−c‖ = √(2−2cos(v,c))` —— **到原点的欧氏距离就等于余弦距离**，也就是 `Reader`
 * 正在用的那把尺子。所以图上"离中心多远"和"读取器觉得这条离它自己多远"是同一个量。
 *
 * ## 图上必须写字（否则图会骗人）
 *
 * 三个方向只装得下 `cum3`（这份数据实测 57%），其余方向**在视野外** ⇒ 投影半径一定
 * **偏小**，而且**各层偏小的程度不一样**。实测同一份数据（底色 6 / 情境 2 / 记忆 6）：
 *
 *     层        精确半径          图上半径
 *     底色      0.64 – 0.79      0.16 – 0.39     ← 压到 44%
 *     情境  0.96 – 1.00      0.67 – 0.85
 *     记忆      1.00 – 1.13      0.80 – 1.01
 *
 * **层的先后在两张图上都成立**（底色最内、记忆最外，两层完全不重叠），所以图上"谁近谁远"
 * 可信；但**幅度不可信**，而且内层被压得最狠。因此：
 *
 *  - ⛔ 这张图**不画"等距离圈"**。一个真的球在这个投影里没法摆 —— 拿真半径画圈、
 *    拿投影半径画点，就是在同一张图上混用两把尺子（第一版就是这么画的：圈画在 0.69，
 *    底色的点却在 0.30 处，看着像画错了）。精确半径改用**图例上的数字**给（见 [Legend]），
 *    以及「时间」那张图 —— 那张没有投影，纵轴就是精确值。
 *  - 选中某一条时给的也是**精确半径** `r`，不是屏幕上量出来的。
 *
 * ## 为什么是三维 + 可旋转
 *
 * 用户要看的是"数据是不是扁的"。一张固定的二维投影看不出扁 —— 转到侧面就一眼看出来了。
 *
 * ## 点多了会不会卡
 *
 * 图上最多 400 点（Python 侧 `MAX_POINTS`）；每帧只是 400 个 `drawCircle`。
 * 坐标**不是每帧算的**：只在 `yaw/pitch` 变了才重投影（`remember` 键控）。
 * 这个 Box 在 `verticalScroll` 里，拖动会被它吃掉（否则一转就把页面也滚了）——
 * 这是故意的，想滚页面就从图外面拖。
 */
@Composable
internal fun SpaceSection(v: PoolView) {
    // 老缓存里没有 `layout` 这个字段 ⇒ 整段不出现，也不编一句话出来。
    val lay = v.layout ?: return

    var mode by rememberSaveable { mutableStateOf(0) }          // 0 语义 · 1 时间
    var sel by rememberSaveable { mutableStateOf(-1) }
    var yaw by rememberSaveable { mutableStateOf(DEFAULT_YAW) }
    var pitch by rememberSaveable { mutableStateOf(DEFAULT_PITCH) }
    // ★ 缩放：双指捏合。球体原来**只能转不能放大**，而点都挤在同一层球壳上
    //   （半径 0.67–0.98），看不清细节 —— 必须能推近。
    var zoom by rememberSaveable { mutableStateOf(ZOOM_PROBE) }

    CollapsibleGroup(
        label = "空间",
        count = if (lay.ok) lay.nTotal else 0,
        initiallyOpen = true,
    ) {
        if (!lay.ok || lay.points.isEmpty()) {
            Text(
                text = lay.why.ifBlank { "这一版缓存里还算不出空间图。" },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            return@CollapsibleGroup
        }

        // 池子重载后旧的选中下标可能越界 —— 就地当没选，不在组合期回写状态。
        val selIdx = if (sel in lay.points.indices) sel else -1

        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                SegToggle(
                    labels = listOf("语义", "时间"),
                    index = mode,
                    onChange = { mode = it; sel = -1 },
                )
                Box(Modifier.weight(1f))
                // ★ 「复位视角」现在也管缩放：只转不缩的时候不必出现，
                //   但只要缩放被改过就要能一键回去（else 用户会卡在放大态出不来）。
                if (mode == 0 && (yaw != DEFAULT_YAW || pitch != DEFAULT_PITCH || zoom != 1f)) {
                    Text(
                        text = "复位视角" + if (zoom != 1f) "（${fmt(zoom.toDouble(), 1)}×）" else "",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .clickable { yaw = DEFAULT_YAW; pitch = DEFAULT_PITCH; zoom = 1f }
                            .padding(horizontal = 8.dp, vertical = 6.dp),
                    )
                }
            }

            if (lay.note.isNotBlank()) NoticeCard(lay.note, NoticeTone.Warn)

            if (mode == 0) {
                SemanticView(
                    lay = lay,
                    yaw = yaw,
                    pitch = pitch,
                    zoom = zoom,
                    sel = selIdx,
                    onSelect = { sel = it },
                    onSpin = { dx, dy ->
                        yaw += dx * SPIN
                        pitch = (pitch + dy * SPIN).coerceIn(-PITCH_LIMIT, PITCH_LIMIT)
                    },
                    onZoom = { zoom = (zoom * it).coerceIn(ZOOM_MIN, ZOOM_MAX) },
                )
            } else {
                TimeView(lay = lay, sel = selIdx, onSelect = { sel = it })
            }

            Legend(lay)
            Captions(lay, mode)
            SelectedDetail(lay, selIdx)
        }
    }
}

// --------------------------------------------------------------------------- 颜色
/**
 * 四个层的颜色**刻意不走主题**。
 *
 * ★ 为什么：`BaseTheme` 默认开动态取色（跟着壁纸走），而动态方案里
 *   `primary / secondary / tertiary / error` 是同一个种色的邻近调、色度低 ——
 *   实测在手机上图例那行四个层名看上去几乎同色，散点更分不出来。散点图靠颜色分类，
 *   这一处必须让位给可分辨性。
 * ★ 选的都是中间调：深色底（#1C1B1F）和浅色底（#FFFFFF）上都看得见。
 *   ⛔ 别换成 `primary` 那一族 —— 换回去就又会糊成一片。
 */
private val C_SELF = Color(0xFF8B7BFF)      // 底色 / 自我
private val C_SITUATIONAL = Color(0xFFFF5C6C)   // 情境（2026-10-05 从 `C_AVOID` 改名 —— 纯画图侧的常量名，不是数据键）
private val C_FACT = Color(0xFF23C4A8)      // 记忆 / 事实
private val C_TEMP = Color(0xFFF2A93B)      // 临时 / 短期记忆
// ★★ 2026-10-05：临时池拆成**三档**（短期 / 中期 / 悬着）之后，图上原来只画得出一种颜色 ——
//   新两档的 layer 落进 `else -> C_OTHER`（灰），看着就是"画不全"（用户原话）。
//   挑色依据：**在色轮上与已有四色尽量拉开**。已有 ≈ 紫 270° · 红 355° · 青 170° · 琥珀 38°，
//   空档最大的两个在 **~100°（绿）**与 **~220°（蓝）** ⇒ 就用它们。
//   ⚠️ 颜色只是**分档的提示**；真正认档靠图例那行与点开的文字（都走 `labelOf`）。
private val C_MID = Color(0xFF6FCF4A)       // 中期 / 中期记忆（慢衰减，一个季度不提才退）
private val C_OPEN = Color(0xFF4C8DFF)      // 悬着 / 悬着的事（不按时间退，只由 done 结束）
private val C_OTHER = Color(0xFF9A93A6)

private fun layerColor(layer: String): Color = when (layer) {
    "底色" -> C_SELF
    "情境" -> C_SITUATIONAL
    "记忆" -> C_FACT
    "临时" -> C_TEMP
    "中期" -> C_MID
    "悬着" -> C_OPEN
    else -> C_OTHER
}

// --------------------------------------------------------------------------- 视角
private const val DEFAULT_YAW = 0.6f
private const val DEFAULT_PITCH = 0.32f
private const val SPIN = 0.011f
private val PITCH_LIMIT = 1.45f
// ★ 缩放范围：**下限 0.1×** —— 初始是 1×，必须能**从初始状态往小缩**
//   （用户原话："现在是能放大，没有从初始状态缩小，可以做到 0.1X"）。
//   缩到 0.1× 时整团只占画布一角，用来"退远看整体形状"。
//   上限 6×：点都挤在同一层球壳上（半径 0.67–0.98），6× 够看清局部。
private const val ZOOM_MIN = 0.1f
private const val ZOOM_MAX = 6f
// ⚠️ **临时探针**：`adb shell input` 只支持单指，捏合没法脚本化 ⇒ 靠这个常量把初始缩放改掉、
//   截图看"轴有没有跟着一起缩"。**正常必须是 1f**（2026-10-05 用 0.35f 验过一次：轴与点同步缩放）。
private const val ZOOM_PROBE = 1f

/** 画布高度：够转得开，又不至于把下面三层记忆整屏顶出去。 */
private val CANVAS_H = 250.dp

/** 点中的判定半径。手指比点粗，给大一点。 */
private val HIT_R = 24.dp

/**
 * 点的半径（**dp**，画的时候才换成像素）。
 *
 * ★ 被读取过越多次画得越大 —— 但半径主要作用是可见性，不是数量级：
 *   靠它比"谁更重要"会读错（差一次只差 0.32dp）。
 * ⛔ 第一版直接按像素给了 2.4 —— 在 3x 屏上是 0.8dp，点几乎看不见。`DrawScope`
 *   里的单位是**像素**，凡是从 dp 来的数都必须 `.toPx()`。
 */
private fun dotRadiusDp(hits: Int): Float = 2.6f + min(hits, 6) * 0.34f

// --------------------------------------------------------------------------- 语义（3D）
/**
 * 一次投影的产物。
 *
 * ★ `index` 是 `lay.points` 里的下标 —— 选中状态存的就是它，
 *   两张图（语义/时间）共用同一套下标，切视图不会串位。
 */
private class Proj(
    val index: Int,
    val layer: String,
    val hits: Int,
    val sx: Float,
    val sy: Float,
    /** 旋转后的 z（**数据单位**，和 x/y/z 同单位）—— 只用来排序和调透明度。 */
    val depth: Float,
)

/** 一次投影产物里的一根**骨架射线**（定轴重心图）。 */
private class RayProj(
    val id: String,
    val name: String,
    /** 屏幕上的两端（这条轴是**直径**：两端就是语义相反的两极）。 */
    val ax: Float, val ay: Float,
    val bx: Float, val by: Float,
    /** 手标的「空」—— 当前语料没表态的那几维。 */
    val empty: Boolean,
)

private class Frame(
    val pts: List<Proj>,
    /** 数据半径 → 像素。 */
    val k: Float,
    val cx: Float,
    val cy: Float,
    val maxR: Float,
    /** 三根坐标轴在屏幕上的方向（已按固定长度缩放），只为让旋转看得出来。 */
    val axes: List<Offset>,
    /** ★ 定轴重心图的骨架：36 根双极轴（空轴也在里面，只是画淡）。 */
    val rays: List<RayProj> = emptyList(),
)

/**
 * 把 `(x, y, z)` 转到屏幕。**正交投影**：没有透视，所以"屏幕上离中心多远"和数据里的
 * 半径是线性关系 —— 差别只在被压掉的那 43% 方向（见文件头）。
 *
 * ⚠️ 这里三根轴是 SVD 基，**符号是任意的**（换一批数据整张图可能镜像过来）。
 *   所以轴上不带任何标签、也不当刻度用，只当"这是个三维空间"的提示。
 */
private fun buildFrame(lay: Layout, yaw: Float, pitch: Float, zoom: Float, w: Float, h: Float): Frame {
    val viewR = min(w, h) / 2f * 0.92f
    // 按**精确**最大半径定标：这样"谁离中心远"在屏幕上的比例关系不会被再改一次。
    val maxR = lay.maxR.coerceAtLeast(1e-3).toFloat()
    // ★ 缩放只改"数据半径 → 像素"这一个比例，**不动坐标本身** ⇒
    //   放大后点与轴的相对关系不变、点击命中也自动跟着走（命中用的是投影后的 sx/sy）。
    //   `maxR` 仍然报未缩放的精确值（图上"远近"那句话不能因为放大就变意思）。
    val k = viewR / maxR * zoom
    val cx = w / 2f
    val cy = h / 2f
    val cyaw = cos(yaw); val syaw = sin(yaw)
    val cpit = cos(pitch); val spit = sin(pitch)

    fun rot(x: Float, y: Float, z: Float): FloatArray {
        val x1 = x * cyaw + z * syaw
        val z1 = -x * syaw + z * cyaw
        val y1 = y * cpit - z1 * spit
        val z2 = y * spit + z1 * cpit
        return floatArrayOf(x1, y1, z2)
    }

    val pts = lay.points.mapIndexed { i, p ->
        val r = rot(p.x.toFloat(), p.y.toFloat(), p.z.toFloat())
        Proj(i, p.layer, p.hits, cx + r[0] * k, cy - r[1] * k, r[2])
    }

    // ⛔ 轴长**必须由 `k` 算出来，不能再写一个独立的 `viewR * 系数`**：
    //   第一版就是这么写的 ⇒ 缩放时点跟着变了、轴却纹丝不动（用户报："缩放的时候，轴还是原来的显示"）。
    //   ⇒ 用 `maxR * k` 就是"缩放到像素"的同一个比例尺，两者**结构上不可能再脱钩**。
    val axisLen = maxR * k * 0.72f
    val axes = (0..2).map { axis ->
        val e = FloatArray(3).also { it[axis] = 1f }
        val r = rot(e[0], e[1], e[2])
        val len = hypot(r[0], r[1])
        if (len < 1e-4f) Offset.Zero else Offset(r[0] / len * axisLen, -r[1] / len * axisLen)
    }

    // ★ 定轴重心图的骨架：**36 根双极轴**都画（含还没有内容落上去的）。
    //   每根轴是**一条直径**（两端 = 语义相反的两极），所以画的是 `−u·L` 到 `+u·L`。
    //   轴长统一 —— 轴上不带刻度含义（长度只表示"这是个方向"，不表示量），
    //   但**长度要跟点用同一把尺子**（同上，由 `k` 算）。
    val rayLen = maxR * k * 0.86f
    val rays = lay.axes.map { a ->
        val r = rot(a.x.toFloat(), a.y.toFloat(), a.z.toFloat())
        val len = hypot(r[0], r[1])
        val ux = if (len < 1e-4f) 0f else r[0] / len
        val uy = if (len < 1e-4f) 0f else -r[1] / len
        RayProj(a.id, a.name,
                cx + ux * rayLen, cy + uy * rayLen,
                cx - ux * rayLen, cy - uy * rayLen,
                a.empty)
    }
    return Frame(pts, k, cx, cy, maxR, axes, rays)
}

@Composable
private fun SemanticView(
    lay: Layout,
    yaw: Float,
    pitch: Float,
    zoom: Float,
    sel: Int,
    onSelect: (Int) -> Unit,
    onSpin: (Float, Float) -> Unit,
    onZoom: (Float) -> Unit,
) {
    val shellC = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.16f)
    val centerC = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.40f)
    val density = LocalDensity.current

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxWidth()
            .height(CANVAS_H),
    ) {
        val wPx = with(density) { maxWidth.toPx() }
        val hPx = with(density) { maxHeight.toPx() }
        // ★ 只在视角变了才重投影 —— 拖一下就重算几百个点是这里的全部开销。
        val frame = remember(lay, yaw, pitch, zoom, wPx, hPx) {
            buildFrame(lay, yaw, pitch, zoom, wPx, hPx)
        }
        val frameNow = rememberUpdatedState(frame)
        val hitPx = with(density) { HIT_R.toPx() }
        val baseRx = with(density) { 3.dp.toPx() }

        Canvas(
            modifier = Modifier
                .fillMaxSize()
                // ★ 换成 `detectTransformGestures`：它一次给 pan + zoom，
                //   于是"单指拖 = 转"和"双指捏 = 缩放"能共存（原来只有 detectDragGestures，
                //   根本没有缩放来源）。旋转仍按原来的口径映射到 yaw/pitch。
                .pointerInput(Unit) {
                    detectTransformGestures { _, pan, gestureZoom, _ ->
                        if (gestureZoom != 1f) onZoom(gestureZoom)
                        if (pan.x != 0f || pan.y != 0f) onSpin(pan.x, pan.y)
                    }
                }
                .pointerInput(Unit) {
                    detectTapGestures { off ->
                        val f = frameNow.value
                        var best = -1
                        var bd = hitPx * hitPx
                        for (q in f.pts) {
                            val dx = q.sx - off.x
                            val dy = q.sy - off.y
                            val d = dx * dx + dy * dy
                            if (d < bd) { bd = d; best = q.index }
                        }
                        onSelect(best)
                    }
                },
        ) {
            val f = frame
            // ★ 骨架：36 根双极轴（定轴重心图）。
            //   **空轴照画**，只是画得更淡 —— 用户原话："空轴本身要画出图，
            //   但目前空间没有那一部分"。它现在有了方向（约定几何），所以画得出来。
            if (f.rays.isNotEmpty()) {
                // ★ **36 根一视同仁地画**，不给任何一根贴标签、也不画淡。
                //   用户原话要的是「空轴本身要画出图，但目前空间没有那一部分」—— 要点是
                //   **它得在图里存在（有方向）**，而这件事已经由"几何方向=约定"办到了。
                //
                //   ⛔ 曾经按手标的 `empty` 把 6 根画淡，并在图下写「这 6 根是空轴」。
                //      2026-10-05 用户否掉了，理由是对的：**那是拿当前语料的状况写死的**，
                //      等到有新东西进来，它可能就不是空轴了 ⇒ 图会挂着一条过期的断言。
                //      （数据层也算不出来：实测 36 根的 use 全在 0.11–0.18，没有一根显著低。）
                //   ⇒ 只在数据里留存 `empty`（溯源用），**界面上不显示**。
                f.rays.forEach { ray ->
                    drawLine(shellC.copy(alpha = 0.30f),
                             Offset(ray.ax, ray.ay), Offset(ray.bx, ray.by), strokeWidth = 1f)
                }
            } else {
                // 老缓存（没有 axes.npz）：退回"三根坐标轴当参照"
                f.axes.forEach { a ->
                    if (a != Offset.Zero) {
                        drawLine(shellC, Offset(f.cx - a.x, f.cy - a.y),
                                 Offset(f.cx + a.x, f.cy + a.y), strokeWidth = 1f)
                    }
                }
            }
            // 原点 = 它自己是谁。
            drawCircle(centerC, radius = baseRx, center = Offset(f.cx, f.cy), style = Stroke(width = 1.5f))

            // ★ 远的先画：否则近处的点会被后面的盖掉，深度就看不出来了。
            //   透明度跟着深度走 —— 这是屏幕上唯一能表达"前后"的手段。
            //   注意 depth 是**数据单位**（和 x/y/z 同单位）⇒ 用 maxR 归一化，
            //   不能拿画布尺寸：那是像素，量纲不一样。
            val dz = f.maxR.coerceAtLeast(1e-3f)
            f.pts.sortedBy { it.depth }.forEach { q ->
                val t = (q.depth / dz).coerceIn(-1f, 1f)
                val a = 0.42f + 0.46f * (t + 1f) / 2f
                drawCircle(
                    color = layerColor(q.layer).copy(alpha = a),
                    radius = dotRadiusDp(q.hits).dp.toPx(),
                    center = Offset(q.sx, q.sy),
                )
            }
            // 选中的那条：画在最上层，套一圈。
            f.pts.firstOrNull { it.index == sel }?.let { q ->
                drawCircle(
                    color = layerColor(q.layer),
                    radius = dotRadiusDp(q.hits).dp.toPx() + 2f,
                    center = Offset(q.sx, q.sy),
                    style = Stroke(width = 2f),
                )
            }
        }
    }
}

// --------------------------------------------------------------------------- 时间（2D）
/**
 * 时间那一张。
 *
 * ★ **纵轴给的是精确半径 `r`**，不是图上量的 —— 这张图没有投影，所以它比空间图准，
 *   两张图因此能互相校验（空间图那个"内层被压到 44%"在这里能看出来）。
 * ★ 种子条目没有时间（`t == null`）⇒ 不进图，但**在下面说清楚有几条没进来**。
 */
@Composable
private fun TimeView(lay: Layout, sel: Int, onSelect: (Int) -> Unit) {
    val timed = remember(lay) { lay.points.withIndex().filter { it.value.t != null } }
    if (timed.size < 2) {
        Text(
            text = if (timed.isEmpty()) "还没有带时间的条目 —— 归档一场就会有。"
            else "只有一条带时间的条目，连不成走势。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }

    val frameC = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.16f)
    val refC = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.35f)
    val density = LocalDensity.current

    val t0 = timed.minOf { it.value.t!! }
    val t1 = timed.maxOf { it.value.t!! }
    val span = (t1 - t0).coerceAtLeast(1e-6)
    val maxR = lay.maxR.coerceAtLeast(1e-3)

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxWidth()
            .height(CANVAS_H),
    ) {
        val wPx = with(density) { maxWidth.toPx() }
        val hPx = with(density) { maxHeight.toPx() }
        val padX = with(density) { 12.dp.toPx() }
        val padY = with(density) { 16.dp.toPx() }
        val hitPx = with(density) { HIT_R.toPx() }
        val baseRx = with(density) { 3.dp.toPx() }

        val xy = remember(lay, wPx, hPx, t0, span, maxR) {
            timed.map { (i, p) ->
                val fx = ((p.t!! - t0) / span).toFloat()
                val fy = (p.r / maxR).toFloat().coerceIn(0f, 1f)
                Proj(
                    index = i, layer = p.layer, hits = p.hits,
                    sx = padX + fx * (wPx - 2 * padX),
                    sy = (hPx - padY) - fy * (hPx - 2 * padY),
                    depth = 0f,
                )
            }
        }
        val xyNow = rememberUpdatedState(xy)

        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(Unit) {
                    detectTapGestures { off ->
                        var best = -1
                        var bd = hitPx * hitPx
                        for (q in xyNow.value) {
                            val dx = q.sx - off.x
                            val dy = q.sy - off.y
                            val d = dx * dx + dy * dy
                            if (d < bd) { bd = d; best = q.index }
                        }
                        onSelect(best)
                    }
                },
        ) {
            drawRect(
                color = frameC, topLeft = Offset(padX, padY),
                size = Size(wPx - 2 * padX, hPx - 2 * padY),
                style = Stroke(width = 1f),
            )
            // ★ 这张图的纵轴没有投影 ⇒ 横线可以真的摆在"自我到它自己的平均距离"上。
            //   （空间图**不画**这个圈，原因见文件头。）
            val yRef = (hPx - padY) -
                (lay.baseR / maxR).toFloat().coerceIn(0f, 1f) * (hPx - 2 * padY)
            drawLine(refC, Offset(padX, yRef), Offset(wPx - padX, yRef), strokeWidth = 1f)

            xy.forEach { q ->
                drawCircle(
                    color = layerColor(q.layer).copy(alpha = 0.9f),
                    radius = dotRadiusDp(q.hits).dp.toPx(),
                    center = Offset(q.sx, q.sy),
                )
            }
            xy.firstOrNull { it.index == sel }?.let { q ->
                drawCircle(
                    color = layerColor(q.layer),
                    radius = dotRadiusDp(q.hits).dp.toPx() + baseRx,
                    center = Offset(q.sx, q.sy), style = Stroke(width = 2f),
                )
            }
        }
    }
}

// --------------------------------------------------------------------------- 说明
/**
 * 图例 **+ 每一层的精确距离区间**。
 *
 * ★ 两件事合成一行是有意的：图例本来就得说"什么颜色是谁"，而**这张图唯一能给准的数**
 *   就是每层的精确半径区间（图上位置只能读排序）。分开写会让人以为数是"从图上量的"。
 * ★ 顺序按平均距离从近到远 —— 排出来正好就是"从最像它自己到最不像"。
 */
@Composable
private fun Legend(lay: Layout) {
    val spans = remember(lay) { layerSpans(lay) }
    if (spans.isEmpty()) return
    val base = MaterialTheme.colorScheme.onSurfaceVariant
    Text(
        text = buildAnnotatedString {
            withStyle(SpanStyle(color = base)) { append("离它自己多远　") }
            spans.forEachIndexed { i, s ->
                if (i > 0) withStyle(SpanStyle(color = base)) { append("　") }
                withStyle(SpanStyle(color = layerColor(s.layer))) {
                    append("${labelOf(s.layer)} ${fmt(s.lo, 2)}–${fmt(s.hi, 2)}")
                }
            }
        },
        style = MaterialTheme.typography.labelMedium,
    )
}

private class LayerSpan(val layer: String, val lo: Double, val hi: Double, val mean: Double)

/** 每层的精确半径区间。**从点里现算**，不写死 —— 池子一变这些数就变。 */
private fun layerSpans(lay: Layout): List<LayerSpan> =
    lay.points.groupBy { it.layer }
        .map { (layer, ps) ->
            val rs = ps.map { it.r }
            LayerSpan(layer, rs.min(), rs.max(), rs.average())
        }
        .sortedBy { it.mean }

/** 图下面必须跟的质量数字 —— 不写这几句，图就是在骗人。 */
@Composable
private fun Captions(lay: Layout, mode: Int) {
    val lines = remember(lay, mode) { captionLines(lay, mode) }
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        lines.forEach { line ->
            Text(
                text = line,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun captionLines(lay: Layout, mode: Int): List<String> {
    val out = mutableListOf<String>()
    if (mode == 0 && lay.mode == "frame") {
        // ★ 定轴重心图：这张图上的每根线都是**一根语义轴**（约定几何），
        //   点落在"它最像的那两根轴"之间，离中心的距离 = 离底色的余弦距离。
        out += if (lay.nDrawn < lay.nTotal) {
            "图上 ${lay.nDrawn} 点（一共 ${lay.nTotal} 点，只摆了被读到最多的一批）"
        } else {
            "图上 ${lay.nTotal} 点（全部）"
        }
        out += "${lay.nAxes} 根轴 · 线条就是轴 · 拖动旋转 · 双指缩放 · 点一下看是哪条"
        // ⛔ 这两行别写 markdown 星号：Compose 的 Text 是纯文本，`**…**` 会把星号原样画出来
        //   （2026-10-05 实测踩到：手机上显示成「方向是**分类约定**」）。
        out += "方向是分类约定（不是相似度）；离中心的距离 = 离它自己多远（精确值点开看）"
        // ⛔ 这里**不报"哪几根是空轴"**。那是手标的、拿当前语料写死的判断；
        //   等新记忆进来，某一维可能就不是空的了 ⇒ 写在这里会变成一条过期断言。
        //   图上的做法是：36 根都画出来（包括还没有内容落上去的），不贴标签。
        if (lay.axAlignMed > 0) {
            // ★ 这个数必须跟着图走：上一版落点与它的轴基本无关（0.217，与乱猜无异），
            //   这一版 top-2 混合是 0.797。低于 0.5 的那部分位置本来就不可信。
            out += "落点对齐度中位 ${fmt(lay.axAlignMed, 2)}" +
                (if (lay.axAlignLo > 0) "（${pct(lay.axAlignLo)} 的落点对齐度 <0.5，那些位置别当真）" else "")
        }
        if (lay.axSelAnchor > 0) out += "锚的选择性中位 ${fmt(lay.axSelAnchor, 2)}（>1 才有分辨力）"
    } else if (mode == 0) {
        out += if (lay.nDrawn < lay.nTotal) {
            "图上 ${lay.nDrawn} 点（一共 ${lay.nTotal} 点，只摆了被读到最多的一批）"
        } else {
            "图上 ${lay.nTotal} 点（全部）"
        }
        out += "拖动旋转 · 点一下看是哪条 · 中心就是它自己"
        out += "前三个方向占 ${pct(lay.cum3)}" + (lay.share3Random?.let {
            // ★ 这个对照**两条路都给得起**。没有它，"扁不扁"在 400 条以上就整句消失了
            //   （实测踩到过：幂迭代路径不报有效维度）。
            "，同样条数的随机向量是 ${pct(it)} ⇒ 数据挤在少数方向上"
        } ?: "")
        out += "其余方向在视野外 —— 图上的远近只可信先后，不可信幅度（内层被压得最狠）"
        if (lay.radiusCorr > 0) {
            out += "图上远近与精确远近相关 ${fmt(lay.radiusCorr, 2)}；精确值看上面那行，或点开某一条"
        }
        effLine(lay)?.let { out += it }
    } else {
        val timed = lay.points.count { it.t != null }
        // ★ 一条带时间的都没有时，图本身已经写了一句（"还没有带时间的条目…"）；
        //   再往下说"图上 0 条有时间""纵轴是精确距离""横线 0.69"就是复读，而且
        //   在**没有图**的时候讲纵轴横轴更是废话。整段说明就地收掉。
        if (timed < 2) return emptyList()
        val untimed = lay.nTotal - timed
        out += if (untimed > 0) "图上 $timed 条有时间，$untimed 条没有（种子条目）—— 不在这张图里"
        else "图上 $timed 条，全都有时间"
        val ts = lay.points.mapNotNull { it.t }
        if (ts.size >= 2) {
            out += "横轴 时间 ${fmt(ts.min(), 1)} → ${fmt(ts.max(), 1)} 天"
        }
        out += "纵轴就是精确距离（这张图没有投影，不用打折）"
        if (lay.baseR > 0) out += "横线 ${fmt(lay.baseR, 2)} = 自我到它自己的平均距离"
    }
    return out
}

/**
 * "扁不扁"的第二个量（有效维度）。
 *
 * ★ 结论由**上一行**（前三占比 vs 随机）给出 —— 那一行两条路都给得起，是主力；
 *   这一行只在全 SVD 那条路上有，是佐证。所以这里**不再重复**"数据是扁的"。
 * ⛔ 别拿"条数"当对照 —— 那只在条数远小于维度时成立（实测 n=400 时随机只有 287）。
 */
private fun effLine(lay: Layout): String? {
    val e = lay.effDim ?: return null
    val r = lay.effDimRandom
    return if (r != null && r > 0) {
        "有效维度 ${fmt(e, 1)}，同样条数的随机向量是 ${fmt(r, 1)}"
    } else {
        "有效维度 ${fmt(e, 1)}（一共 ${lay.nTotal} 条，越接近它越铺得开）"
    }
}

private fun pct(v: Double): String {
    val x = v * 100
    // ★ 随机对照会小到 0.8% —— 四舍五入成 "1%" 就把"差了六十倍"这句话抹掉了。
    return if (x >= 10) "${x.roundToInt()}%" else "${trimNum(fmt(x, 1))}%"
}

// --------------------------------------------------------------------------- 选中
@Composable
private fun SelectedDetail(lay: Layout, sel: Int) {
    val p = lay.points.getOrNull(sel) ?: return
    val c = layerColor(p.layer)
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(FlatRadius))
            .background(MaterialTheme.colorScheme.surfaceContainer)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Box(Modifier.size(7.dp).clip(RoundedCornerShape(4.dp)).background(c))
            Text(
                text = labelOf(p.layer) + if (!p.temp) "　#${p.i}" else "",
                style = MaterialTheme.typography.labelMedium,
                color = c,
            )
        }
        Text(
            text = p.text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Text(
            text = buildString {
                append("离它自己 ${fmt(p.r, 3)}")
                append("　被想起过 ${p.hits} 次")
                p.t?.let { append("　第 ${fmt(it, 1)} 天进来") }
            },
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        // ★ 定轴重心图才有：它靠哪两根轴、偏哪一端、混不混。
        //   「成分混杂」是这个设计要看的东西之一，所以混的条目要说得出口。
        if (p.axis.isNotBlank()) {
            val a1 = lay.axes.firstOrNull { it.id == p.axis }
            val a2 = lay.axes.firstOrNull { it.id == p.axis2 }
            Text(
                text = buildString {
                    append("最靠 ${p.axis} ${a1?.name ?: ""}（${p.pole} 端）")
                    append("　第二 ${p.axis2} ${a2?.name ?: ""}")
                    append("　占比 ${pct(p.w1)}：${pct(1 - p.w1)}")
                    if (p.mix > 0.25) append("　※ 成分混杂")
                    if (p.align < 0.5) append("　※ 落点对齐度低（${fmt(p.align, 2)}），图上位置别当真")
                },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// --------------------------------------------------------------------------- 小件
/** 两格切换（语义 / 时间）。没有现成的件，自己拼一个 —— 扁平的。 */
@Composable
private fun SegToggle(labels: List<String>, index: Int, onChange: (Int) -> Unit) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(FlatRadius))
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .padding(2.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        labels.forEachIndexed { i, s ->
            val on = i == index
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(FlatRadius - 2.dp))
                    .background(
                        if (on) MaterialTheme.colorScheme.surfaceContainerLow else Color.Transparent,
                    )
                    .clickable { onChange(i) }
                    .padding(horizontal = 16.dp, vertical = 7.dp),
            ) {
                Text(
                    text = s,
                    style = MaterialTheme.typography.labelLarge,
                    color = if (on) MaterialTheme.colorScheme.onSurface
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
