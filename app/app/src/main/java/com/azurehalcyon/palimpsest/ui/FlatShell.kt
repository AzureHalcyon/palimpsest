package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.niki914.uikit.infra.ProvideLiquidScreenContentForPreview

/** 工具栏高度（不含状态栏）。 */
private val ToolbarHeight = 52.dp

/** 底部导航高度（不含导航栏 inset）。 */
private val BottomBarHeight = 56.dp
/**
 * 扁平外壳。
 *
 * ★ **为什么不再用 `uikit` 的 `LiquidScreen`**：
 *   它的顶栏是**浮层**（`Box` 里 zIndex 2/3 的覆盖层），内容层只拿到
 *   `actionBarHeight` 当"建议留白"，既没有强制 inset、也不画遮罩 ——
 *   所以内容在几何上**必然**从它底下穿过去。用户实测：顶栏占 y=0..350px（117dp），
 *   而首条正文起点在 y=60px ⇒ 真的被压住了（不是"淡出不好看"）。
 *   既然要求全部扁平化，就不要再玩浮层：**顶栏改成占布局空间的普通工具栏**，
 *   内容从它下面开始，"最上方被遮挡"从根上消失，也不再需要 scrim。
 *
 * ★ 仍然满足 `uikit` 的硬约束：内容包在提供 `LocalLiquidScreenContentContext` 的树里
 *   （`SettingsListPageContent` 会读 `liquidScreenTopPadding()`，缺了直接 `error()`）。
 *   这里用真实的 `topPadding` / `bottomPadding` 提供，正好把工具坞与底栏的高度算进去。
 *
 * ★ **键盘弹出时不再出现两层底栏**：`imePadding()` 让整棵树让出键盘高度，
 *   而底栏本身**不在** `imePadding` 之内 —— 于是视觉上就是"键盘顶掉了底栏"，
 *   不会出现"导航条叠在键盘上"。导航栏 inset 在键盘弹出时也随之失效（被键盘替代）。
 */
@Composable
fun FlatShell(
    vm: AppViewModel,
    content: @Composable (Modifier) -> Unit,
) {
    val density = LocalDensity.current
    // ★ 只用来判断"键盘是不是开着" —— **不再手工算状态栏/导航栏高度去当 padding**。
    //   实测踩过：`WindowInsets.statusBars.getBottom()` 在这里读出来是 0，
    //   于是工具栏标题被画进了状态栏（量出来标题 y=13.3dp，而状态栏高 44.7dp）。
    //   手算 inset 再换算成 padding 极易错位；改成**让工具栏自己吃 inset**
    //   （`statusBarsPadding()`）、底栏自己吃（`navigationBarsPadding()`），
    //   内容靠 **Column 布局流**天然排在工具栏下方 —— 全程零手算 dp。
    // ★★ **2026-10-06：键盘 inset 不许在这个"包着内容"的组合里读。**
    //   旧写法在这里读 `WindowInsets.ime.getBottom()` 算出 `imeOpen` 去决定底栏显不显示 ——
    //   后果是**输入法动画的每一帧都让整个 FlatShell 重组**，而 `content(...)` 就在它里面
    //   ⇒ 整棵对话树（气泡 + markdown + 输入框）跟着每帧重组一次。
    //   ⇒ 那段读取挪进 `BottomTabsFollowKeyboard`（只包住底栏那一小块）。
    //   ⚠️ 这是本项目同一条原则的第三次应用：**别让 inset/尺寸的读取污染大范围的组合**
    //     （另外两处：工具栏自己吃 `statusBarsPadding`、输入框用 `onSizeChanged` 而不是组合期读）。

    var menuOpen by remember { mutableStateOf(false) }
    var confirmFinish by remember { mutableStateOf(false) }

    // ★★ **键盘让位：图层平移，不是改布局**（2026-10-06，用户："然后就是压绘制本身"）。
    //   依据是每帧阶段拆解：**测量+布局 0.07ms（可忽略）、录制绘制 7.6ms**。
    //   显示列表之所以每帧要重录，正是因为**布局尺寸在变**（`imePadding()` 每帧改内边距
    //   ⇒ LazyColumn 里每一项都要重新录制）。只做**图层变换**（GPU 侧）就绕开了这件事：
    //   布局一动不动 ⇒ 显示列表一直有效 ⇒ 每帧只剩一次图层位移。
    //   ⚠️ inset 只在**图层块里**读（`slide.value` 在 `graphicsLayer {}` 里）——
    //      这样每帧只重跑图层块，**不重组、不重新布局**
    //      （`ImeSlideProbe` 自己每帧重组，但它什么都不画）。
    //   ⚠️ 内容要 `clipToBounds()`：它被整体上移，不裁的话会画到工具栏上面去。
    val imePxState = remember { mutableStateOf(0) }
    val barSlotPx = remember { mutableStateOf(0) }
    ImeSlideProbe { imePxState.value = it }

    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Column(
            Modifier
                .fillMaxSize(),
        ) {
            FlatToolbar(
                title = vm.tab.title,
                showMenu = vm.tab == Tab.Chat,
                onMenuClick = { menuOpen = true },
            )
            // ★ 硬约束：**归档一开始、或者场次一结束，就把 ⋯ 菜单关掉。**
            //   实测踩到：点「收场」之后菜单并没有消失，归档跑完它还开着，
            //   正好盖在「已收场。」上面 —— 用户看到的就是一句被遮住的提示。
            //   菜单项自己的 `onClick` 里已经写了 `menuOpen = false`，但还是被重新打开了；
            //   与其猜是哪条路径，不如在这里钉死：这两个时刻它不该开着。
            LaunchedEffect(vm.chatPhase, vm.chatSessionKey) {
                if (vm.chatPhase != ChatPhase.Idle || vm.chatSessionKey == null) {
                    menuOpen = false
                }
            }
            // ★ 内容不给自己加任何 padding：`weight(1f)` 让它天然落在
            //   工具栏之下、底栏之上。滚动列表的第一行因此**完整可见**，无需遮罩。
            //   ★★ 键盘弹起时：整块内容**整体上移**（图层平移），到底让输入框停在键盘上沿。
            //   平移量 = 键盘高度 − 底栏占位（底栏那一条本来就在内容下面，不减就会高出一截）。
            Box(
                Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .clipToBounds()
                    .graphicsLayer {
                        val shift = (imePxState.value - barSlotPx.value).coerceAtLeast(0)
                        translationY = -shift.toFloat()
                    },
            ) {
                // ★ 用 uikit **公开**的 `ProvideLiquidScreenContentForPreview` 提供上下文。
                //   名字里带 ForPreview，但它做的正是这里需要的事：同时提供
                //   `LocalLiquidScreenContentContext`（`SettingsListPageContent` 读它，
                //   缺了直接 `error()`）与 `LocalNavigationEntry`
                //   （`staticCompositionLocalOf { error() }`，缺了也直接崩）。
                //   ⛔ 不要试图自己 `LiquidScreenContentContext(...)` —— 构造函数是
                //   `internal`，实测编译不过（`Cannot access 'constructor'`）。
                //   两个 padding 都给 0：位置已经由布局流决定了，再给就是双重留白。
                ProvideLiquidScreenContentForPreview(topPadding = 0.dp, bottomPadding = 0.dp) {
                    content(Modifier.fillMaxSize())
                }
            }
            // ★★ 底栏：键盘弹起时**整体下滑到键盘底下**（同样是图层平移，不改布局）。
            //   ⚠️ 它的高度要量下来（`barSlotPx`）—— 内容的上移量要减掉这一条，
            //      否则输入框会停在键盘上方一整个底栏高度的地方（会看出空档）。
            Box(
                Modifier
                    .graphicsLayer { translationY = imePxState.value.toFloat() }
                    .onSizeChanged { barSlotPx.value = it.height },
            ) {
                BottomTabs(current = vm.tab, onSelect = { vm.select(it) })
            }
        }

        // 「收场」走工具栏右侧的菜单：它是整场的动作，且一场只用一次，
        // 不该常驻在最显眼的输入框旁边。
        // ★ 下拉菜单自己吃状态栏 inset —— 和工具栏同一个来源，不会错位。
        Box(
            Modifier
                .align(Alignment.TopEnd)
                .windowInsetsPadding(WindowInsets.statusBars)
                .padding(top = ToolbarHeight),
        ) {
            DropdownMenu(
                expanded = menuOpen,
                onDismissRequest = { menuOpen = false },
            ) {
                DropdownMenuItem(
                    text = { Text("归档") },
                    enabled = vm.chatSessionKey != null,
                    onClick = {
                        menuOpen = false
                        confirmFinish = true
                    },
                )
            }
        }
    }

    if (confirmFinish) {
        FinishConfirmDialog(
            onDismiss = { confirmFinish = false },
            onConfirm = {
                confirmFinish = false
                vm.finish()
            },
        )
    }

    LaunchedEffect(vm.tab) {
        when (vm.tab) {
            Tab.Chat -> vm.openChatIfNeeded()
            Tab.Pool -> vm.loadPool()
            Tab.Settings -> vm.loadSettings()
            Tab.Activity -> vm.loadActivity()
        }
    }
}

/** 内容与底栏之间的最小间距（用户要求 ≤8dp；这里取 4dp 更贴近"贴着"）。 */
private val BottomContentGap = 4.dp

/** 普通工具栏：**占据布局空间**，实心底 + 一条细线，不是浮层。 */
@Composable
private fun FlatToolbar(
    title: String,
    showMenu: Boolean,
    onMenuClick: () -> Unit,
) {
    Column(
        Modifier
            .fillMaxWidth()
            // ★ 顺序要紧：先铺实心底（连状态栏那一格一起铺），再用 `statusBarsPadding()`
            //   把内容推下来。这样状态栏区域有底、标题也不会和系统时钟/信号叠在一起。
            //   实测踩过：手工 `padding(top = statusBar)` 读出来是 0（标题被画进状态栏）。
            .background(MaterialTheme.colorScheme.surface)
            .statusBarsPadding(),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .height(ToolbarHeight)
                .padding(start = 20.dp, end = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleLarge.copy(fontSize = 20.sp),
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.weight(1f),
            )
            if (showMenu) {
                Box(
                    Modifier
                        .size(40.dp)
                        .clip(RoundedCornerShape(FlatRadius))
                        .clickable(onClick = onMenuClick),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector = Icons.Default.MoreHoriz,
                        contentDescription = "更多",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        FlatDivider()
    }
}

/**
 * ★★ **只订阅键盘高度、什么都不画**（2026-10-06）。
 *
 * 为什么要这么个小东西：`WindowInsets.ime` 在**组合期**读，会让读它的那个作用域每帧重组。
 * 我们的目标是"每帧只做图层位移" ⇒ 让**这个空组件**承担每帧重组，把值写进一个 `MutableState`；
 * 真正用它的人只在 `graphicsLayer {}`（**绘制阶段**）里读那个状态 —— 绘制阶段的读
 * **只会让图层失效**，不会重组、也不会重新布局。
 * ⛔ 别把 `WindowInsets.ime.getBottom()` 挪回 `FlatShell` 主体：那样整棵树每帧重组
 *   （实测过：输入法动画期间 81.5% 帧超预算）。
 */
@Composable
private fun ImeSlideProbe(onIme: (Int) -> Unit) {
    val d = LocalDensity.current
    val ime = WindowInsets.ime.getBottom(d)
    SideEffect { onIme(ime) }
}

/** 底部三个入口。选中态是 **8dp 圆角方块**（用户要求，不用大胶囊）。 */
@Composable
private fun BottomTabs(
    current: Tab,
    onSelect: (Tab) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            // 底栏自己吃导航栏 inset（和工具栏吃状态栏 inset 同一个做法，零手算）。
            .background(MaterialTheme.colorScheme.surface)
            .navigationBarsPadding(),
    ) {
        FlatDivider()
        Row(
            Modifier
                .fillMaxWidth()
                .height(BottomBarHeight)
                .padding(horizontal = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Tab.entries.forEach { t ->
                TabItem(
                    tab = t,
                    selected = current == t,
                    onClick = { onSelect(t) },
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
}

@Composable
private fun TabItem(
    tab: Tab,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val icon: ImageVector = when (tab) {
        Tab.Chat -> Icons.AutoMirrored.Filled.Chat
        Tab.Pool -> Icons.Default.Storage
        Tab.Settings -> Icons.Default.Tune
        // ★ 活动记录：一个"历史"的意思，不打断、不提示
        Tab.Activity -> Icons.Default.DateRange
    }
    val color = if (selected) {
        MaterialTheme.colorScheme.onSecondaryContainer
    } else {
        MaterialTheme.colorScheme.onSurfaceVariant
    }
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .then(
                if (selected) {
                    Modifier.background(MaterialTheme.colorScheme.secondaryContainer)
                } else {
                    Modifier
                },
            )
            .clickable(onClick = onClick)
            .padding(vertical = 6.dp),
        contentAlignment = Alignment.Center,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Icon(
                imageVector = icon,
                contentDescription = tab.title,
                modifier = Modifier.size(18.dp),
                tint = color,
            )
            Text(
                text = tab.title,
                style = MaterialTheme.typography.labelMedium,
                color = color,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            )
        }
    }
}

/** 工具：给需要一条竖分隔的地方用。 */
@Composable
internal fun VDivider() {
    Box(
        Modifier
            .width(1.dp)
            .height(16.dp)
            .background(MaterialTheme.colorScheme.outlineVariant),
    )
}
