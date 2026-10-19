package com.azurehalcyon.palimpsest.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.niki914.uikit.infra.component.SettingExpandableTextItem
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch

/** composer 距内容区底部的间距（目标：输入框底 → 底栏 ≤8dp）。 */
/** 输入框与底部（底栏 / 键盘）之间的空隙。 */
private val ComposerBottomGap = 6.dp

/**
 * ★★ **输入框与上方列表之间的空隙**（2026-10-06，用户报的："输入框上侧疑似被裁剪了"，
 * 进一步说明："你看输入框和消息气泡交界处，是**直角**" + "绿色输入框直接和消息气泡相接，
 * 没有像和输入法那样隔一小段"）。
 *
 * 真因：列表的**可视区下沿 = 输入框上沿**，中间**一个像素的缝都没有** ⇒
 * 滚动到"某条气泡只露一半"时，气泡被视口切出的那条**直线**正好贴着输入框上沿 ⇒
 * 看着就像"输入框上沿被裁成直角"。而输入框**下面**（对键盘/底栏那一侧）有
 * `ComposerBottomGap` ⇒ 两边不对称，更显得上面那处是坏的。
 *
 * ⛔ 别用"给列表加 `contentPadding.bottom`"来补：那只管**内容末尾**，
 *    滚动到中间时被切的那条边仍然贴着输入框。
 * ⇒ 必须**在输入框上方留出真实的空隙**（背景从缝里透出来，两件东西才分得开）。
 */
private val ComposerTopGap = 10.dp

/**
 * 屏 1：对话。
 *
 * 骨架照 zafiro（出处见 [ChatComponents] 的文件注释），**排版密度按本项目真机实测重定**：
 * 轮间距 16dp、块间距 8dp、气泡 12dp 圆角无尾巴、composer 单行 48dp。
 * 依据是用户量的旧版数值：输入框 86dp 装一行 19dp 的字（4.5:1）、轮间距 68dp、
 * 首条正文起点落在顶栏区域内（被压住）。
 *
 * ⛔ **本屏最重要的一条**：`chat_ask` 回来的 `push`（这一轮**想起的**东西）
 *    **默认绝不显示给正在打字的人** —— 看见"它想起了什么"就会被带着走，数据就被污染了。
 *    所以每轮只留一个折叠行，**只有主动点开才展开**，且**永不自动展开**。
 */
@Composable
fun ChatScreen(
    vm: AppViewModel,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()
    val density = LocalDensity.current
    val isGenerating = vm.chatPhase == ChatPhase.Asking || vm.chatPhase == ChatPhase.Opening

    val composerHeight = remember { mutableStateOf(COMPOSER_SINGLE_LINE_HEIGHT) }
    val bottomThresholdPx = with(density) { 24.dp.roundToPx() }
    var expandedPush by remember { mutableStateOf<Int?>(null) }

    // ★★ **长按"想起的那一条" ⇒ 就地改/删**（2026-10-06，用户要求 (a)–(d)）
    //   ⚠️ 弹窗状态现在在 **VM 里**（`memoryTarget/memoryDraft/memoryDeleting/memoryIsLast`）——
    //      记忆页（`PoolScreen`）要用**同一个**弹窗，局部 `remember` 那一屏拿不到。
    //      ⇒ 真机实测踩过的正是这个：那一屏连长按都没挂（用户："哪一个都是"删不了）。

    val lastTurn = vm.turns.lastOrNull()
    val bottomContentVersion = remember(
        vm.turns.size,
        lastTurn?.id,
        lastTurn?.reply,
        composerHeight.value,
        expandedPush,
    ) {
        listOf(
            vm.turns.size,
            lastTurn?.id,
            lastTurn?.reply,
            composerHeight.value,
            expandedPush,
        )
    }

    val isAtBottom by remember(listState, bottomThresholdPx) {
        derivedStateOf {
            val layoutInfo = listState.layoutInfo
            val lastVisibleItem =
                layoutInfo.visibleItemsInfo.lastOrNull() ?: return@derivedStateOf true
            val viewportEnd = layoutInfo.viewportEndOffset
            lastVisibleItem.index == layoutInfo.totalItemsCount - 1 &&
                lastVisibleItem.offset + lastVisibleItem.size <= viewportEnd + bottomThresholdPx
        }
    }
    val followBottomState = rememberScrollFollowState(
        interactionSource = listState.interactionSource,
        isScrollInProgress = { listState.isScrollInProgress },
        isAtEnd = { isAtBottom },
    )
    var followBottom by followBottomState
    val scope = rememberCoroutineScope()

    LaunchedEffect(bottomContentVersion) {
        if (followBottom) listState.scrollToItem(vm.turns.size)
    }

    // ★★ **可视高度变了也要重新贴底**（2026-10-06，用户报的："输入框上侧疑似被裁剪了"）。
    //   真因：键盘弹起 ⇒ 内容区变矮 ⇒ **视口变小**，而列表只跟着**内容**变、不跟着**视口**变
    //   ⇒ 最后那条被切在输入框上沿；同时"回到底部"那个浮钮冒出来（机器认为已不在底部了）。
    //   【真机取证】键盘弹起时裁图：积分公式被切在输入框上沿，且 ↓ 浮钮出现在公式上面。
    //   ⚠️ 用 `snapshotFlow` 读 `layoutInfo`：这是**布局阶段**的量。若在组合期读，
    //      会像上一轮那样让整棵对话树每帧重组。
    //   ⛔ 只在 `followBottom` 为真时追 —— 用户手动往上翻的时候不许抢他的位置。
    LaunchedEffect(listState) {
        snapshotFlow { listState.layoutInfo.viewportSize.height }
            .distinctUntilChanged()
            // ★ **等它安静下来再贴底**（2026-10-06）：键盘动画期间视口每帧都在变，
            //   逐帧 `scrollToItem` 会把那一帧的活堆得很重（实测 p90 61ms）。
            //   `collectLatest` 在新值到来时取消上一次等待 ⇒ 只有动画停下后那次真正执行。
            .collectLatest { h ->
                delay(60)
                if (h > 0 && followBottom) listState.scrollToItem(vm.turns.size)
            }
    }
    LaunchedEffect(vm.expandRequest) {
        val req = vm.expandRequest ?: return@LaunchedEffect
        expandedPush = req
        if (vm.turns.isEmpty()) return@LaunchedEffect
        delay(32)
        listState.animateScrollToItem((req + 1).coerceIn(0, vm.turns.lastIndex))
    }
    LaunchedEffect(vm.collapseRequest) {
        if (vm.collapseRequest == null) return@LaunchedEffect
        expandedPush = null
        followBottom = true
    }

    // ★ 输入框**占布局空间**，不再浮在列表上。
    //   原先它是绝对定位 + 列表底部留一大块 `bottomClearance`；键盘一开内容区收缩，
    //   列表底部贴到键盘上沿，而输入框比它高一点点 ⇒ 中间露出 **5.7dp 的半截气泡**。
    //   实测：输入框 523.3..571.3dp、气泡残条 571.3..577.0dp、键盘上沿 577.0dp。
    //   改成正常布局流之后**物理上不可能再有缝**，也不再需要算 composerHeight 去补底部。
    val composerReserve = composerHeight.value + ComposerBottomGap + 6.dp

    // ★ 提示**自己走**：看一眼就够了，不用用户动手关（「睡醒了。」一直挂着会变成噪音）。
    //   ⛔ 出错那条（`chatErr`）不在这里 —— 它得摆着等人处理。
    LaunchedEffect(vm.chatNotice) {
        if (vm.chatNotice.isNotBlank()) {
            delay(5000)
            vm.dismissChatNotice()
        }
    }

    Box(modifier = modifier.fillMaxSize()) {
      Column(Modifier.fillMaxSize()) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(
                start = 16.dp,
                // 顶栏已是**占布局空间**的工具栏（见 FlatShell），内容从它下面开始，
                // 所以这里只留一点呼吸，不需要大 inset、也不需要遮罩。
                top = 12.dp,
                end = 16.dp,
                bottom = 12.dp,
            ),
        ) {
            if (vm.chatNotice.isNotBlank()) {
                item(key = "notice") {
                    Box(Modifier.padding(bottom = BlockSpacing)) {
                        NoticeCard(vm.chatNotice, NoticeTone.Info)
                    }
                }
            }
            if (vm.chatErr.isNotBlank()) {
                item(key = "err") {
                    Box(Modifier.padding(bottom = BlockSpacing)) {
                        NoticeCard(vm.chatErr, NoticeTone.Error)
                    }
                }
            }
            itemsIndexed(items = vm.turns, key = { _, turn -> turn.id }) { index, turn ->
                val position = userBubblePosition(vm.turns, index)
                val thisIsLast = index == vm.turns.lastIndex
                val busy = isGenerating && thisIsLast
                ChatTurnItem(
                    turn = turn,
                    position = position,
                    isGenerating = busy,
                    onDisclosureExpand = { vm.expandRequest = index },
                    onDisclosureCollapse = { vm.collapseRequest = index },
                    // ★ 长按自己的气泡 ⇒ **贴着手指**弹出的两行小卡片（2026-10-06）。
                    //   ⛔ **每一条都挂**（不只是最后一条）：不能撤的那些，长按会如实说
                    //      "只能从最近的一句开始往回撤" —— 挂了却不响才是更糟的体验。
                    //   ★ 这里只算"能不能撤"和**为什么不能**；真正的裁决仍在
                    //      `AppViewModel.withdraw`（防两处逻辑漂移）。
                    menuUnavailable = when {
                        !thisIsLast ->
                            "只能从最近的一句开始往回撤 —— 这一轮后面还有 ${vm.turns.lastIndex - index} 轮"
                        busy || !turn.hasContent -> "这一轮还没答完，等它说完再撤。"
                        else -> null
                    },
                    onWithdrawEdit = { vm.withdraw(index, regen = false) },
                    onRegenerate = { vm.withdraw(index, regen = true) },
                    // ⛔ 2026-10-17：条目上的"长按就地改/删"跟着旧池子一起删了
                    //   （它写的那份账本 `pool_edits.jsonl` 已经没有任何代码在写）。
                    //   改/删现在在**记忆页的件**上（长按件 → 剪出 / 删）。
                    // ★ 流式状态只给最后一轮（归档/生成时那三个才有值）
                    streamPhase = if (thisIsLast) vm.streamPhase else "",
                    streamReasoning = if (thisIsLast) vm.streamReasoning else "",
                    streamReply = if (thisIsLast) vm.streamReply else "",
                    modifier = turnTopPadding(position, index == 0).fillMaxWidth(),
                )
            }
            item(key = "bottom_anchor") { Box(Modifier.fillMaxWidth().height(1.dp)) }

            // ★★ 上一场归档的全过程（2026-10-07）—— 跑完**不清掉**：
            //   用户口径：「归档现在只是能看见，但是我还没看完就消失了（归档完成了）」
            //   ★ 位置：**必须在 `turns` 之后** —— 用户口径「归档应该在对话**下面**，
            //     跑到最上面了」。按他的设计（归档 = 接着刚才那场对话的特殊对话），
            //     它本来就是**接在对话后面**的一段。
            if (vm.archiveLog.isNotEmpty()) {
                item(key = "archiveLog") {
                    Box(Modifier.padding(top = BlockSpacing)) {
                        ArchiveLogInline(vm.archiveLog)
                    }
                }
            }
        }

        // ★★ **归档期**（2026-10-07，用户设计）：草案已经当它自己的话摆出来了，
        //   输入框照常能说话（对它提意见 ⇒ 它改草案，**池子仍然一字不动**）；
        //   满意就按这个 —— **到这一步才真正写池子**。
        //   ⚠️ 它在 `if (归档中)` **外面**（归档期里 `chatPhase == Idle`）——
        //     上一版插进了那个分支里，里外条件互相排斥 ⇒ **按钮永远不显示**（真机踩到）。
        if (vm.archiveDraft != null && vm.chatPhase == ChatPhase.Idle) {
            Button(
                onClick = { vm.archiveCommit() },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, end = 16.dp, top = ComposerTopGap, bottom = 2.dp)
                    .height(44.dp),
                shape = RoundedCornerShape(FlatRadius),
            ) {
                Text("完成（就这样记下来）", style = MaterialTheme.typography.labelLarge)
            }
        }

        if (vm.chatPhase == ChatPhase.Archiving) {
            // ★ 归档期间**把输入框整块换掉**。
            //   旧版是"进度条浮在输入框上面"，两样东西叠在同一块地方 —— 用户说"有点乱"。
            //   归档时输入框本来也没意义（发了也没人接），换掉最干净。
            ArchivingBar(
                phase = vm.streamPhase,
                reasoning = vm.streamReasoning,
                modifier = Modifier.padding(
                    start = 16.dp, end = 16.dp,
                    top = ComposerTopGap, bottom = ComposerBottomGap,
                ),
            )
        } else {
            FlatChatComposer(
                value = vm.chatInput,
                onValueChange = vm::updateInput,
                onSendClick = {
                    if (vm.chatInput.isNotBlank()) {
                        followBottom = true
                        vm.send()
                    }
                },
                isGenerating = isGenerating,
                modifier = Modifier
                    .fillMaxWidth()
                    // ★ 上下都要留缝：上面那道缝是"列表被切出来的直线"与输入框之间的分界
                    //   （没有它，半截气泡会严丝合缝贴在输入框上沿，看着像输入框被裁了）。
                    .padding(
                        start = 16.dp, end = 16.dp,
                        top = ComposerTopGap, bottom = ComposerBottomGap,
                    )
                    .onSizeChanged { size ->
                        composerHeight.value = with(density) { size.height.toDp() }
                    },
            )
        }
      }

        ScrollToBottomButton(
            visible = !followBottom && !isAtBottom,
            onClick = {
                followBottom = true
                scope.launch { listState.animateScrollToItem(vm.turns.size) }
            },
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = composerReserve),
        )
    }

    // ⛔ 2026-10-17：`MemoryEditDialog`（长按条目就地改/删）整块删掉了 ——
    //   它改的是**旧池子里的条目**，而池子已经不存在（`chat_edit_entry` 也删了）。
    //   改 / 删现在在**记忆页的件**上（长按件 → 剪出 / 删）。
}

/** 一轮：用户气泡 → 助手正文 → 折叠行（紧贴正文下方 [DisclosureGap]，不当孤儿）。 */@Composable
private fun ChatTurnItem(
    turn: ChatTurn,
    position: UserBubblePosition,
    isGenerating: Boolean,
    onDisclosureExpand: () -> Unit,
    onDisclosureCollapse: () -> Unit,
    menuUnavailable: String?,
    onWithdrawEdit: () -> Unit,
    onRegenerate: () -> Unit,
    // ★ 流式（2026-10-07）：只有**最后一轮**才拿得到这三个（别处一律空）。
    streamPhase: String = "",
    streamReasoning: String = "",
    streamReply: String = "",
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(BlockSpacing)) {
        if (turn.userText.isNotEmpty()) {
            UserMessageBubble(
                text = turn.userText,
                position = position,
                footer = usageIn(turn.usage),
                // ★ 长按弹出的那张小卡片：**贴着手指**（位置由气泡自己算，见
                //   `PressAnchoredMenu`）。不能撤的时候只剩一行理由 ——
                //   ⛔ 不偷偷把后面几轮一起撤掉（那是把历史剪一个洞）。
                menu = { dismiss ->
                    FlatContextMenu(
                        items = if (menuUnavailable != null) {
                            listOf(menuUnavailable to dismiss)
                        } else {
                            listOf(
                                "撤回并编辑" to { dismiss(); onWithdrawEdit() },
                                "重新生成" to { dismiss(); onRegenerate() },
                            )
                        },
                    )
                },
            )
        }

        if (turn.hasContent || isGenerating) {
            // ★★ **流式（2026-10-07）**：还在生成时，正文**边收边显示**；
            //   正文还没来时，就把**推理（CoT）**显示出来 —— 用户口径："让 COT 也可见"。
            //   ⛔ 只在最后一轮、且确实在生成时才动这些；别的地方一个字都不显示。
            if (isGenerating && turn.reply == null) {
                if (streamReply.isNotBlank()) {
                    AssistantMessageBubble(footer = null) {
                        Text(
                            text = streamReply,
                            style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp),
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                } else if (streamReasoning.isNotBlank() || streamPhase.isNotBlank()) {
                    ThinkingRow(phase = streamPhase, reasoning = streamReasoning)
                } else {
                    ThinkingRow()
                }
            }

            if (turn.failed) {
                Text(
                    text = "这句没发出去。再发一次。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }

            // ★ 折叠块摆在回复**上方**（类似普通 AI 对话里 CoT 的位置），**每轮都显示**。
            //   为什么每轮都要有：**想起几条是每条消息各不相同的** —— 它本身就是信息。
            //   （旧版把它挂在正文下面、而且只给最新一轮，那是理解错了。）
            //   ⛔ 默认折叠、**永不自动展开**：不点开就一个字都不显示。
            val pushItems = turn.push.ifEmpty { turn.replyRetrieval?.push.orEmpty() }
            if (pushItems.isNotEmpty()) {
                // ★ 条数**必须从 `pushItems` 来**，不能从 `turn.push` 来。
                //   这两个不是同一个字段：刚做完那一轮的 `push` 是空的、内容在
                //   `replyRetrieval.push` 里 ⇒ 旧写法会出现"明明有内容、却写着想起 0 条"
                //   （实测：当场显示 0 条，重开之后同一条变成 1 条）。
                PushDisclosure(
                    summary = "想起 ${pushItems.size} 条",
                    push = pushItems,
                    onExpand = onDisclosureExpand,
                    onCollapse = onDisclosureCollapse,
                )
            }

            // ★ 这一轮的**推理**（CoT）——摆在"想起/工具调用"这一片里，**与它们同形**
            //   （用户口径：「思维链形式做成和工具调用一样的，点了才展开，格式统一」）。
            turn.reasoning?.takeIf { it.isNotBlank() }?.let { cot ->
                CotRow(cot = cot)
            }

            // 模型自己"再查一次记忆"：按**工具调用**的样子摆在同一区域（在回复上方）。
            turn.pulls.forEach { p ->
                ToolCallRow(
                    query = p.query,
                    got = p.got,
                    回 = p.回,
                    kind = p.kind,
                    onExpand = onDisclosureExpand,
                    onCollapse = onDisclosureCollapse,
                )
            }

            turn.reply?.takeIf { it.isNotBlank() }?.let { reply ->
                // ★ 助手回复要有**可见的气泡**（用户反馈：旧版是裸文字，看不出这是它说的话）。
                // ★ token 作为气泡**自己的脚注**传进去（用户指出：它得贴着这一条气泡，
                //   而不是靠屏幕右边 —— 那看起来像页面级的说明，跟这条回复没关系）。
                AssistantMessageBubble(footer = usageOut(turn.usage)) {
                    MarkdownText(
                        markdown = reply,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        }
    }
}


// --------------------------------------------------------------------------- 分组
/** 连续用户气泡分组（照 zafiro 的 `userBubblePosition`）。 */
internal fun userBubblePosition(turns: List<ChatTurn>, index: Int): UserBubblePosition {
    val stickyUp = index > 0 && !turns[index - 1].hasContent
    val stickyDown = index < turns.lastIndex && !turns[index].hasContent
    return when {
        stickyUp && stickyDown -> UserBubblePosition.GroupMid
        stickyUp -> UserBubblePosition.GroupLast
        stickyDown -> UserBubblePosition.GroupFirst
        else -> UserBubblePosition.Single
    }
}

/** 组尾下标（照 zafiro 的 `userGroupTailIndex`）。 */
internal fun userGroupTailIndex(turns: List<ChatTurn>, index: Int): Int {
    var tail = index
    while (tail < turns.lastIndex && !turns[tail].hasContent) tail++
    return tail
}
