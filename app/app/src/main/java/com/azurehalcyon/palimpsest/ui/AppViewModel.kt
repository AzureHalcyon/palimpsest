package com.azurehalcyon.palimpsest.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.azurehalcyon.palimpsest.CompanionService
import com.azurehalcyon.palimpsest.PyBridge
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * 三个屏的全部状态与动作。
 *
 * ★ **线程规矩（本文件存在的首要理由）**：所有 Python 调用都
 *   `withContext(PyBridge.dispatcher)` —— Chaquopy 的独占工作线程，**绝不是主线程**。
 *   理由：`chat_ask` 要跑一次模型、`chat_finish` 要跑几回合模型；Android 主线程
 *   堵约 5 秒就 ANR。界面在这一整个过程中保持可交互。
 */
/**
 * ★ **归档里的一段**（2026-10-07）：`phase` 是"这一步在干什么"（「归档：第 3/12 轮」），
 * `text` 是这一步的**推理原文**（边收边长）。
 * ⚠️ `text` 用 `mutableStateOf`：每来一个增量只写一个字段 ⇒ 不整表重画（几千个增量也不卡）。
 */
class ArchiveStep(val phase: String) {
    var text by mutableStateOf("")
}

class AppViewModel(private val app: Application) : ViewModel() {

    private val dataDir: String = app.filesDir.absolutePath

    // ==================================================================== 导航
    var tab by mutableStateOf(Tab.Chat)
        private set

    fun select(t: Tab) {
        if (tab == t) return
        tab = t
        when (t) {
            Tab.Chat -> Unit
            Tab.Pool -> if (pool == null && !poolLoading) loadPool()
        Tab.Activity -> if (activityLines.isEmpty() && !activityLoading) loadActivity()
            Tab.Settings -> if (knobs.isEmpty() && !settingsLoading) loadSettings()
        }
    }

    /** 调试用：打开某一屏（自动化验证用，不改变默认落点 = 对话）。 */
    fun selectTab(t: Tab) = select(t)

    // ==================================================================== 对话
    var chatPhase by mutableStateOf<ChatPhase>(ChatPhase.Idle)
        private set

    /** ★ 以"轮"为单位（照 zafiro 的 turns 列表）。 */
    var turns = mutableStateListOf<ChatTurn>()
        private set
    var chatInput by mutableStateOf("")
    var chatNotice by mutableStateOf("")
        private set
    var chatErr by mutableStateOf("")
        private set
    var chatSessionKey by mutableStateOf<Int?>(null)
        private set

    /**
     * ★ **手机上手改过、且现在还在池子里的**条目文本（界面给它们打记号）。
     * 来源是 `cache/pool_edits.jsonl`（那份账本是"手机上的临时改动"唯一凭据，
     * 因为桌面下次推池子会把池子整个覆盖）。
     */
    var editedTexts by mutableStateOf<Set<String>>(emptySet())
        private set

    /**
     * 把**提示**收掉（界面上定时调）。
     *
     * ★ 提示与错误分开：`chatNotice` 是"事情办好了"（已归档、上次补完了），
     *   看一眼就够，**留着自己走**；`chatErr` 是"出事了"，得一直摆着等处理。
     */
    fun dismissChatNotice() {
        chatNotice = ""
    }

    /**
     * 界面用的"请把第 n 轮滚进视野"信号。
     *
     * ★ 为什么放在 ViewModel：折叠行的展开发生在**列表项内部**，而滚动要落在
     *   `LazyListState` 上；两者相隔好几层。用一个可观察的整数当信号，
     *   列表那边 `LaunchedEffect` 一收到就把那一轮滚出来。
     */
    var expandRequest by mutableStateOf<Int?>(null)

    /** 收起信号：折叠行收完动画后，把列表重新贴底。 */
    var collapseRequest by mutableStateOf<Int?>(null)

    private var sid: String? = null
    private var nextTurnId = 0L

    /**
     * 撤回**进行中**的闸门。
     *
     * ⚠️ 为什么不用 `chatPhase`：撤回不跑模型、只回退状态（几十毫秒），
     *    借用 `Asking`/`Opening` 会让界面闪一下"正在想"。但它是**异步**的
     *    （要走 Python 那条线程）⇒ 连点两下会连着撤两轮 —— 那不一定是用户想要的。
     */
    private var withdrawing = false

    /**
     * 「手改池子里的一条」进行中的闸门（与 `withdrawing` 同样的道理：它是异步的，
     * 而且要调一次编码器，约 0.3–1 秒 ⇒ 连点两下会改两次）。
     */
    private var editingEntry = false

    /**
     * ★★ **「长按一条 ⇒ 就地改 / 删」那个弹窗的状态**（2026-10-07 从 `ChatScreen` 提到这里）。
     *
     * 为什么要提上来：**记忆页**（`PoolScreen`）也要用同一个弹窗。原先这套状态是
     * `ChatScreen` 的**局部** `remember` ⇒ 另一屏拿不到 —— 真机实测用户的原话是
     * 「记忆页面长按删不了，**哪一个都是**」（那一屏连长按手势都没挂，见 `PoolScreen`）。
     * ⛔ 只把**状态**提上来，裁决仍在 [editMemory] / [deleteMemory]（防两处逻辑漂移）。
     */
    var memoryTarget by mutableStateOf<String?>(null)
        private set
    var memoryDraft by mutableStateOf("")
    var memoryDeleting by mutableStateOf(false)
    var memoryIsLast by mutableStateOf(false)
        private set

    /** 这一次改 / 删是**从记忆页**发起的 ⇒ 提示落在 `poolNotice` / `poolErr`，并顺手重读池子。 */
    private var memoryFromPool = false

    /**
     * 打开那个弹窗。[isLast] = 这一条属于**最近一轮**吗（只有那时才给「保存并重新生成」——
     * 改的是池子，要进上下文得重跑那一轮）。
     */
    fun openMemoryEditor(text: String, isLast: Boolean, fromPool: Boolean = false) {
        memoryTarget = text
        memoryDraft = text
        memoryDeleting = false
        memoryIsLast = isLast
        memoryFromPool = fromPool
    }

    fun closeMemoryEditor() {
        memoryTarget = null
        memoryDeleting = false
    }

    /**
     * 提示 / 报错**按发起的那一屏**落。
     *
     * ⛔ 不能一律写 `chatNotice`：记忆页改完一条，人要是在对话页看到一句
     *   "改好了"（而且那条提示还会按 5 秒自动消失的时机乱掉），那是两个屏串了味。
     */
    private fun say(fromPool: Boolean, msg: String, err: Boolean = false) {
        if (err) {
            if (fromPool) poolErr = msg else chatErr = msg
        } else {
            if (fromPool) poolNotice = msg else chatNotice = msg
        }
    }

    private fun newTurn(userText: String, push: List<String> = emptyList()): ChatTurn =
        ChatTurn(id = nextTurnId++, userText = userText, push = push)

    /** 开屏就开一场（在途的那一场会接回来）。 */
    fun openChatIfNeeded() {
        if (chatPhase != ChatPhase.Idle || sid != null) return
        chatPhase = ChatPhase.Opening
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) { PyBridge.chatOpen(dataDir) }
            val o = parseObj(raw)
            if (o == null || !o.bool("ok")) {
                chatErr = o?.str("err")?.ifBlank { null } ?: "开不了场"
                chatPhase = ChatPhase.Idle
                return@launch
            }
            sid = o.str("sid")
            chatSessionKey = o.int("k")
            chatErr = ""
            // 上一场是"点了收场、归档跑到一半被杀" ⇒ 这次打开已经把它补归档了。
            // 必须说出来：不说的话，用户会以为那一场白聊了。
            if (o.bool("archived_pending")) {
                chatNotice = "上一次收工时被打断，这次打开已经补完了。"
            }
            // ★ 有一场在走了 ⇒ **主动保活**（聊到一半被系统回收是最亏的）。
            CompanionService.start(app, "第 ${chatSessionKey ?: 0} 场进行中")
            if (o.bool("resumed")) {
                val turnsRaw = withContext(PyBridge.dispatcher) {
                    PyBridge.chatTurnsJson(dataDir, sid.orEmpty())
                }
                turns.clear()
                parseOffMain { parseTurns(turnsRaw) }.forEach { t ->
                    if (t.user.isNotBlank()) {
                        val turn = newTurn(t.user, t.push)
                        turn.reply = t.ai.ifBlank { null }
                        turn.pulls = t.pulls
                        turn.usage = t.usage
                        // ★ 恢复出来的那一轮**也带 CoT**（用户口径：「聊天的 COT 呢？最好也记下来吧？」
                        //   —— 原先它只活在内存里，App 一被回收就没了）。
                        turn.reasoning = t.reasoning.trim().ifBlank { null }
                        turns.add(turn)
                    }
                }
            }
            // ★★ **审阅期被系统回收 ⇒ 接着审**（2026-10-07）。
            //   用户设计：「把归档看作接着刚才对话的特殊对话 …… 最后归档完成之后完成按钮用户点」
            //   ⇒ 没按「完成」就一直搁着、池子一字不动。草案与那几句对话都在 `pending.json` 里
            //     （`archive_revise` 刻意做成无状态，就是为这一步）⇒ 摆回对话、把「完成」按钮亮起来。
            //   ⛔ 别在这里"顺手补做" —— 那正是修掉的行为（还没审就被自动落地）。
            val review = o.sub("review")
            val rd = review?.sub("draft")
            if (review != null && rd != null) {
                // ★ 先把**那一段归档的步骤（CoT）**摆回来 —— 用户 2026-10-07 真机指出：
                //   「**COT 不见了**」（他正看着那几行，App 被回收一次就整块没了）。
                //   ⇒ 步骤随草案一起落盘（`host_glue._stash_steps`），这里照原样还原：
                //     与 `applyDeltas` 同一条分段规则（phase 开一段、reasoning 追加上去）。
                run {
                    val steps = review.arr("steps") ?: return@run
                    archiveLog.clear()
                    for (i in 0 until steps.length()) {
                        val e = steps.optJSONObject(i) ?: continue
                        archiveLog.add(ArchiveStep(e.str("phase")).also { it.text = e.str("text") })
                    }
                }
                review.strList("said").forEach { t -> turns.add(newTurn(t)) }
                archiveDraft = rd
                turns.add(newTurn("（归档）").also { it.reply = draftMarkdown(rd) })
                chatNotice = "上一场归档你还没按「完成」—— 接着审，池子还没动。"
            }
            // ★ 问一次：**现在池子里哪些条目是手机上手改过的** —— 界面给它们打记号
            //   （改过的要一眼看得出来，否则分不清原稿与自己临时改的；用户要求的 (d)）。
            val editsRaw = withContext(PyBridge.dispatcher) {
                PyBridge.chatEditsJson(dataDir, sid.orEmpty())
            }
            parseObj(editsRaw)?.let { eo ->
                if (eo.bool("ok")) editedTexts = eo.strList("edited").toSet()
            }
            chatPhase = ChatPhase.Idle
        }
    }

    private fun parseTurns(raw: String): List<RestoredTurn> {
        val o = parseObj(raw) ?: return emptyList()
        val a: JSONArray = o.arr("turns") ?: return emptyList()
        return (0 until a.length()).mapNotNull { i ->
            val e = a.optJSONObject(i) ?: return@mapNotNull null
            RestoredTurn(
                user = e.str("user"), ai = e.str("ai"),
                push = e.strList("push"),
                pulls = parsePulls(e),
                usage = parseIntMap(e.sub("usage")),
                // ★ 这一轮的 CoT（2026-10-07 用户要求「聊天的 COT 也记下来」）
                reasoning = e.str("reasoning"),
            )
        }
    }

    private fun parsePulls(o: JSONObject): List<Pull> {
        val a = o.arr("pulls") ?: return emptyList()
        return (0 until a.length()).mapNotNull { i ->
            val e = a.optJSONObject(i) ?: return@mapNotNull null
            Pull(query = e.str("query"), got = e.strList("got"))
        }
    }

    private fun parseIntMap(o: JSONObject?): Map<String, Int> {
        if (o == null) return emptyMap()
        val out = mutableMapOf<String, Int>()
        o.keys().forEach { k -> out[k] = o.optInt(k, 0) }
        return out
    }

    fun updateInput(s: String) {
        chatInput = s
    }

    /**
     * 调试用：把一个字符串直接灌进输入框（**不自动发送**）。
     *
     * 为什么需要它：真机输入法（MIUI 搜狗）会把 `adb shell input text` 送进去的字符
     * 留在**组字区**，不落到 `BasicTextField` 的值上 —— 自动化验证时输入框一直是空的。
     * 有了这个口子，`am start --es prefill "…"` 就能把要发的那句话放进去，
     * 之后**仍然由人点发送按钮**，走的是与手打完全相同的代码路径。
     */
    fun prefillInput(text: String) {
        chatInput = text
    }

    /** 发送。没开场就先开场（收场之后再发就走这条路）。 */
    // ------------------------------------------------------------------ 流式（CoT 可见）
    // ★★ 2026-10-07（用户要求：「聊天和归档都可以做成流式，让 COT 也可见」）。
    //   这三个只服务**显示**：⛔ 不进任何记录、不影响池子与上下文。
    /** 当前这一步（归档时是「第 3/5 轮」这类）；没在跑就是空。 */
    var streamPhase by mutableStateOf("")
        private set

    /** 模型**推理**的增量（CoT）。 */
    var streamReasoning by mutableStateOf("")
        private set

    /** 正文增量（边生成边显示）；跑完之后由 `turn.reply` 接手。 */
    var streamReply by mutableStateOf("")
        private set

    private val deltaFlow = MutableSharedFlow<Pair<String, String>>(extraBufferCapacity = 2048)

    /**
     * ★★ **上一次归档的全过程**（2026-10-07）。
     *
     * 用户口径：「归档现在只是能看见，但是我**还没看完就消失了**（归档完成了）」
     * ⇒ 归档跑完**不许清掉**：每一步（phase + 那一轮的推理）都留在这儿，事后按轮翻。
     * ⛔ 只在**下一次归档开始时**清空（那才是"新的一页"）。
     */
    val archiveLog = mutableStateListOf<ArchiveStep>()

    /**
     * ★★ **待审的归档草案**（2026-10-07）：非空 = 正处在"归档期"。
     *
     * 用户设计：「把归档看作**接着刚才对话的特殊对话** —— 模型发完消息之后用户可以接着发消息，
     * 最后归档完成之后**完成按钮用户点**」。
     * ⇒ 这段期间：输入框照常发消息（走 [archiveAsk]）、池子**一字不动**，
     *   直到用户按「完成」（[archiveCommit]）才落地。
     */
    var archiveDraft by mutableStateOf<JSONObject?>(null)
        private set

    /**
     * ★ 增量从 **Python 那条线程**来 ⇒ 先丢进流，再由**主线程**这条协程落到状态上。
     *   ⛔ 别在 sink 里直接改 `mutableStateOf`：跨线程写 Compose 状态是未定义行为。
     *   ⛔ 也别改成 `runBlocking`：那会把 Python 那条线程堵住，反而拖慢生成。
     */
    private fun applyDeltas() {
        viewModelScope.launch {
            deltaFlow.collect { (kind, text) ->
                when (kind) {
                    "phase" -> {
                        streamPhase = text
                        // ★ 归档：每报一个 phase 就**开一段**（事后按轮翻）
                        if (chatPhase == ChatPhase.Archiving) archiveLog.add(ArchiveStep(text))
                    }
                    "reasoning" -> {
                        streamReasoning += text
                        if (chatPhase == ChatPhase.Archiving) {
                            if (archiveLog.isEmpty()) archiveLog.add(ArchiveStep(""))
                            archiveLog[archiveLog.size - 1].text += text
                        }
                    }
                    "content" -> streamReply += text
                }
            }
        }
    }

    /** 给桥用的 sink（`tryEmit` 不挂起、线程安全 ✓）。 */
    private fun newSink() = PyBridge.StreamSink { k, t -> deltaFlow.tryEmit(k to t) }

    private fun resetStream() {
        streamPhase = ""
        streamReasoning = ""
        streamReply = ""
    }

    fun send() {
        val text = chatInput.trim()
        // ★ 归档期：这一段话是**对归档说的**（让它改草案），不是普通对话（2026-10-07）。
        if (archiveDraft != null) {
            archiveAsk(text)
            return
        }
        if (text.isEmpty() || chatPhase != ChatPhase.Idle) return
        if (sid == null) {
            chatPhase = ChatPhase.Opening
            viewModelScope.launch {
                val raw = withContext(PyBridge.dispatcher) { PyBridge.chatOpen(dataDir) }
                val o = parseObj(raw)
                if (o == null || !o.bool("ok")) {
                    chatErr = o?.str("err")?.ifBlank { null } ?: "开不了场"
                    chatPhase = ChatPhase.Idle
                    return@launch
                }
                sid = o.str("sid")
                chatSessionKey = o.int("k")
                chatPhase = ChatPhase.Idle
                askNow(text)
            }
            return
        }
        askNow(text)
    }

    /**
     * ★★ **撤回第 [index] 轮并重来**（2026-10-06）——「撤回上一次对话，重新编辑」。
     *
     * 用户口径：「就和 DeepSeek 网页对话一样的逻辑」。两种用法：
     *  - [regen] = false ⇒ **撤回并编辑**：这一对从界面消失、他那句**回到输入框**，他改完自己发；
     *  - [regen] = true  ⇒ **重新生成**：同一句原样再问一次（不编辑）。
     *
     * ★ **只有最后一轮能撤**（核心侧也是这么实现的）：撤掉中间那一轮会让它后面那些轮的
     *   上下文凭空少一截 —— 那不是"撤回"，那是把历史剪了个洞。
     *   ⛔ 所以这里**不偷偷顺带撤掉后面几轮**，而是**如实说清楚为什么不行**。
     *
     * ★ 界面退一步、核心侧退一步：**核心侧先成功，界面才动**。
     *   反过来的话，核心侧一旦拒绝（比如这一轮是旧版本记的），界面就已经把它删掉了，
     *   而它其实还在模型上下文里 —— 那才是真的对不上。
     */
    /**
     * ★★ **手改池子里的一条**（2026-10-06，用户要求）。
     *
     * 用户的场景原话：「对对话时展示的那些搜到的和 push 的条目，也加上长按修改功能，
     * 这样我使用时看到不合适的可以自己先手动修改，然后重新生成；之后再在电脑上整理更改。」
     *
     * ⚠️ 改的是**池子**，不是这一轮已经发出去的上下文 ⇒ **必须重新生成那一轮**，
     *    新文本才会进模型。所以这里有个 [regen]：为真时顺手把最近一轮撤了重跑
     *    （复用已有的撤回，不另造一条"重跑"路径）。
     */
    fun editMemory(oldText: String, newText: String, regen: Boolean) {
        if (editingEntry || chatPhase != ChatPhase.Idle) return
        val fromPool = memoryFromPool
        if (newText.isBlank()) {
            say(fromPool, "改成空的了 —— 要删就点「删除这条」", err = true)
            return
        }
        if (newText == oldText) return
        editingEntry = true
        if (!fromPool) chatErr = ""
        viewModelScope.launch {
            val o = parseObj(withContext(PyBridge.dispatcher) {
                // ⚠️ `sid ?: ""`：**没有在跑的那一场也要能改**（记忆页那条路不经过"开一场"）。
                //    核心侧 `_engine_for_pool_edits` 见空 sid 就退到"用盘上那份"。
                PyBridge.chatEditEntry(dataDir, sid.orEmpty(), oldText, newText)
            })
            editingEntry = false
            if (o == null || !o.bool("ok")) {
                say(fromPool, o?.str("err")?.ifBlank { null } ?: "没改成", err = true)
                return@launch
            }
            editedTexts = o.strList("edited").toSet()
            if (regen && turns.isNotEmpty()) {
                withdraw(turns.lastIndex, regen = true)
            } else if (fromPool) {
                say(true, "改好了 —— 池子里这一条已经换了。")
                refreshPoolAfterEdit()
            } else {
                // 改的是池子，这一轮的上下文里还是旧文本 ⇒ 如实告诉他怎么让它生效
                say(false, "改好了 —— 要让它用上，长按那句话选「重新生成」。")
            }
        }
    }

    /** ★ **手删池子里的一条**（底色/情境也允许）。删掉就不会再被想起来。 */
    fun deleteMemory(text: String) {
        if (editingEntry || chatPhase != ChatPhase.Idle) return
        val fromPool = memoryFromPool
        editingEntry = true
        if (!fromPool) chatErr = ""
        viewModelScope.launch {
            val o = parseObj(withContext(PyBridge.dispatcher) {
                PyBridge.chatDeleteEntry(dataDir, sid.orEmpty(), text)
            })
            editingEntry = false
            if (o == null || !o.bool("ok")) {
                say(fromPool, o?.str("err")?.ifBlank { null } ?: "没删成", err = true)
                return@launch
            }
            editedTexts = o.strList("edited").toSet()
            say(fromPool, "删了 —— 它不会再被想起来。原稿还在电脑上，这次改动记在 pool_edits.jsonl 里。")
            if (fromPool) refreshPoolAfterEdit()
        }
    }

    fun withdraw(index: Int, regen: Boolean) {
        if (chatPhase != ChatPhase.Idle || withdrawing) return
        val mySid = sid ?: return
        val lastIndex = turns.lastIndex
        if (lastIndex < 0) return
        if (index != lastIndex) {
            chatNotice = "只能从最近的一句开始往回撤 —— 那一轮后面还有 ${lastIndex - index} 轮。"
            return
        }
        val target = turns[lastIndex]
        if (!target.hasContent) {
            chatNotice = "这一轮还没答完，等它说完再撤。"
            return
        }
        withdrawing = true
        chatErr = ""
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) { PyBridge.chatWithdraw(dataDir, mySid) }
            withdrawing = false
            val o = parseObj(raw)
            if (o == null || !o.bool("ok")) {
                // ⚠️ 这一条要走 chatErr（出事了，得一直摆着等处理），不是看一眼就走的 chatNotice。
                chatErr = o?.str("err")?.ifBlank { null } ?: "撤不回来"
                return@launch
            }
            val back = o.str("user").ifBlank { target.userText }
            // ★ 核心侧已经退干净了，界面这一步**只是跟着退**
            //   （`turn.id` 不复用旧 id，否则 LazyColumn 会把新那一轮当成旧那条、动画错乱）。
            turns.removeAt(lastIndex)
            if (regen) {
                askNow(back)
            } else {
                chatInput = back
                chatNotice = "撤回来了 —— 改完再发。"
            }
        }
    }

    private fun askNow(text: String) {
        val mySid = sid ?: return
        chatInput = ""
        chatErr = ""
        resetStream()
        val turn = newTurn(text)
        turns.add(turn)
        chatPhase = ChatPhase.Asking
        viewModelScope.launch {
            // ⚠️ 这里要跑一次模型。主线程一无所知，界面照常响应。
            // ★ 带 sink ⇒ **流式**：推理（CoT）与正文边生成边显示。
            val raw = withContext(PyBridge.dispatcher) {
                PyBridge.chatAsk(dataDir, mySid, text, newSink())
            }
            val o = parseObj(raw)
            if (o == null || !o.bool("ok")) {
                turn.failed = true
                chatErr = o?.str("err")?.ifBlank { null } ?: "这一轮没成功"
            } else {
                turn.reply = o.str("reply")
                // ★ 把这一轮的 CoT 留在轮次上（生成时是"看得见"，事后也翻得到）。
                //   ⚠️ `ChatTurn` 不是可观察对象 ⇒ 靠下面 `chatPhase` 那次翻转触发重画。
                turn.reasoning = streamReasoning.trim().ifBlank { null }
                // ★ 想起的内容**挂在这一轮上但默认折叠**。用户不点，它一个字都不显示。
                turn.replyRetrieval = Retrieval(push = o.strList("push"))
                // 模型自己"再查一次"的调用 + 这一轮花的 token（都给用户看）。
                turn.pulls = parsePulls(o)
                turn.usage = parseIntMap(o.sub("usage"))
            }
            resetStream()
            chatPhase = ChatPhase.Idle
        }
    }

    /**
     * 收场。
     *
     * ⚠️ 全程在 [PyBridge.dispatcher] 上跑（几十秒的活儿），界面只显示进度。
     * 时长**不写进界面文案** —— 用户不关心，他关心的是要不要守着（答案：不用）。
     */
    fun finish() {
        val mySid = sid ?: run {
            chatErr = "还没开场"
            return
        }
        if (chatPhase != ChatPhase.Idle) return
        chatPhase = ChatPhase.Archiving
        chatErr = ""
        resetStream()
        // ★ 归档：**开新的一页**（上一场的全过程在这里被清掉，跑完则一直留着）
        archiveLog.clear()
        // ★ 归档这几十秒里**主动保活**：模型调用已经花掉了，这时候被系统回收最浪费。
        CompanionService.start(app, "正在归档这一场…")
        viewModelScope.launch {
            // ★★ 只有**出草案**（2026-10-07）：池子一字不动，草案摆出来给用户审。
            val raw = withContext(PyBridge.dispatcher) {
                PyBridge.chatArchiveOpen(dataDir, mySid, newSink())
            }
            val o = parseObj(raw)
            if (o == null || !o.bool("ok")) {
                chatErr = o?.str("err")?.ifBlank { null } ?: "没出成草案"
                resetStream()
            } else {
                // ★ 草案**当它自己的一条话**进对话（用户口径：归档 = 接着刚才对话的特殊对话）
                archiveDraft = o.sub("draft")
                turns.add(newTurn("（归档）").also {
                    it.reasoning = streamReasoning.trim().ifBlank { null }
                    it.reply = draftMarkdown(archiveDraft)
                })
                resetStream()
            }
            chatPhase = ChatPhase.Idle
        }
    }

    /** ★ 归档期：用户又说了一句 ⇒ 让它改草案（**池子仍然不动**）。 */
    fun archiveAsk(text: String) {
        val mySid = sid ?: return
        val t = text.trim()
        if (t.isEmpty() || chatPhase != ChatPhase.Idle || archiveDraft == null) return
        chatInput = ""
        resetStream()
        turns.add(newTurn(t))
        chatPhase = ChatPhase.Asking
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) {
                PyBridge.chatArchiveAsk(dataDir, mySid, t, newSink())
            }
            val o = parseObj(raw)
            if (o == null || !o.bool("ok")) {
                chatNotice = o?.str("err")?.ifBlank { null } ?: "这次没改成，草案保持原样"
            } else {
                archiveDraft = o.sub("draft")
            }
            turns.add(newTurn("（归档）").also {
                it.reasoning = streamReasoning.trim().ifBlank { null }
                it.reply = draftMarkdown(archiveDraft)
            })
            resetStream()
            chatPhase = ChatPhase.Idle
        }
    }

    /** ★★ **「完成」**：把草案落地（这一步才写池子）。 */
    fun archiveCommit() {
        val mySid = sid ?: run {
            chatErr = "还没开场"
            return
        }
        if (chatPhase != ChatPhase.Idle) return
        chatPhase = ChatPhase.Archiving
        chatErr = ""
        resetStream()
        CompanionService.start(app, "正在落地这一场…")
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) {
                PyBridge.chatArchiveCommit(dataDir, mySid, newSink())
            }
            resetStream()
            val o = parseObj(raw)
            if (o == null || !o.bool("ok")) {
                chatErr = o?.str("err")?.ifBlank { null } ?: "没收到场"
            } else {
                // ★ 收场失败**要显示出来**，不能假装成功：它是按空归档落地的，
                //   场次照样 +1，但这一场对池子没有贡献。
                val failed = o.bool("failed")
                chatNotice = if (failed) {
                    "这次没归档成，按空的记下了。下次会补做。"
                } else {
                    "睡醒了。"
                }
                // ★★ **落地了就把归档那几行收起来**（2026-10-07 用户真机指出：
                //   「点了完成了之后，显示睡醒了，但归档 COT 还在」）。
                //   ⚠️ 与"跑完不清掉"（用户原话：「我还没看完就消失了」）**不冲突**：
                //     那说的是**归档跑完后、等他审草案**那一段 —— 那一段照旧全留着；
                //     而按了「完成」= 这一场的事办完了，CoT 这种"过程"就该收走，
                //     屏幕上只剩「睡醒了。」这一句结果。
                //   ★ **落地失败的那次例外留着**：那时 CoT 是"它当时在想什么"的唯一痕迹
                //     （转写里只留结论：`archive` / `dream_repair` 那几个字段）。
                if (!failed) archiveLog.clear()
                archiveDraft = null
                turns.clear()
                // 场次句柄已经不在 Python 侧了（`chat_archive_commit` 把它 pop 掉了）。
                sid = null
                chatSessionKey = null
                // 收场之后**没有在途的东西了** ⇒ 把服务停掉，还给它。不做常驻监控。
                CompanionService.stop(app)
            }
            chatPhase = ChatPhase.Idle
        }
    }

    /**
     * ★ 草案**说成人话**（它自己想说的那几条），给用户审。
     * ⛔ 不把模型那些内部字段（`again` 的编号语义、`note` 的原文）直接铺出来 —— 编号要带上
     *   「#」才看得懂是"池子里第几条"。
     */
    private fun draftMarkdown(d: JSONObject?): String {
        if (d == null) return "（这次没出成草案）"
        // ★★ 新线草案（2026-10-17）：`摘要` = 「这一场认出 N 处 → 归进件X / 新建件Y」。
        //   ⛔ 别再去读 facts/kept（那两格在新线里是空的）。
        val 摘要 = d.str("摘要")
        if (摘要.isNotBlank()) {
            return buildString {
                append(摘要)
                val 判 = d.arr("判")
                if (判 != null) {
                    for (i in 0 until 判.length()) {
                        val p = 判.optJSONObject(i) ?: continue
                        if (p.bool("跳过")) continue
                        val 轮 = p.int("轮")
                        val 归进 = p.str("归进")
                        if (归进.isNotBlank()) {
                            append("\n- 第 ").append(轮).append(" 轮 → 归进 ").append(归进)
                        } else {
                            append("\n- 第 ").append(轮).append(" 轮 → 新建一件")
                        }
                    }
                }
                append("\n\n要改哪条你直接说（说「第 3 轮别记」就行）；就这样的话点「完成」。")
            }
        }
        return buildString {
            append("这一场我打算记下这些：\n")
            val facts = d.strList("facts")
            val kept = d.strList("kept")
            if (facts.isEmpty() && kept.isEmpty()) append("\n（没有要新记的）")
            facts.forEach { append("\n- **长期**：").append(it) }
            kept.forEach { append("\n- 短期：").append(it) }
            val again = jints(d, "again")
            if (again.isNotEmpty()) append("\n\n又提到的：").append(again.joinToString("、") { "#$it" })
            val done = jints(d, "done")
            if (done.isNotEmpty()) append("\n\n我当它过去了：").append(done.joinToString("、") { "#$it" })
            val note = d.str("note")
            if (note.isNotBlank()) append("\n\n").append(note)
            append("\n\n要改哪条你直接说；就这样的话点「完成」。")
        }
    }

    /** 草案里的编号数组（`again` / `done` / `promote`）。取不到就是空 —— ⛔ 不抛。 */
    private fun jints(o: JSONObject?, key: String): List<Int> {
        val a = o?.optJSONArray(key) ?: return emptyList()
        val out = mutableListOf<Int>()
        for (i in 0 until a.length()) {
            val v = a.optInt(i, -1)
            if (v > 0) out.add(v)
        }
        return out
    }

    // ==================================================================== 池子
    var pool by mutableStateOf<PoolView?>(null)
        private set
    var poolLoading by mutableStateOf(false)
        private set
    var poolErr by mutableStateOf("")
        private set

    /**
     * ★ 记忆页上的**提示**（"改好了" / "删了"）—— 与 [poolErr] 分开：
     *   提示看一眼就够、**自己走**；错误得摆着等人处理（和对话页那两件是一套道理）。
     */
    var poolNotice by mutableStateOf("")
        private set

    fun dismissPoolNotice() {
        poolNotice = ""
    }

    // ---- 活动记录（它最近做了什么）
    /**
     * ★ **可查，不可见**（设计稿 §5）：这本记录**不发通知**，只在用户自己翻的时候读。
     * ⛔ 别在这里顺手提醒他 —— 那会把"它有自己的生活"变成"它总在打扰我"。
     */
    var activityLines by mutableStateOf<List<String>>(emptyList())
        private set
    var activityLoading by mutableStateOf(false)
        private set
    var activityErr by mutableStateOf("")
        private set

    /**
     * ★★ **把"解析 / 组装"这类纯 CPU 的活挪出主线程**（2026-10-06）。
     *
     * 用户口径：「UI 的流水线可以优化……让负载平均一些」。
     * 为什么需要：`viewModelScope` 默认跑在 **Main** 上，而这里的解析量不小 ——
     * 空间图的布局就是**一条带 331 个点**的 JSON（`parseLayout`）、
     * 池子一页、设置页三个 JSON……它们全挤在"数据回来那一帧"里同步做完。
     * ⇒ 挪到 `Dispatchers.Default`，主线程只管渲染。
     *
     * ⛔ **只挪纯 CPU 的解析**：桥调用仍然走 `PyBridge.dispatcher` 那条**独占线程**
     *   （引擎侧不许并行 —— 那会让状态与算出来的东西对不上）。
     * ⛔ 解析结果要写进 `mutableStateOf` 的话，**赋值必须回到主线程**做
     *   （本函数只包"算"，不包"写状态"）。
     */
    private suspend fun <T> parseOffMain(block: () -> T): T =
        withContext(Dispatchers.Default) { block() }

    fun loadActivity() {
        if (activityLoading) return
        activityLoading = true
        activityErr = ""
        viewModelScope.launch {
            val lines = withContext(PyBridge.dispatcher) { PyBridge.activityLines(dataDir) }
            activityLines = lines
            if (lines.size == 1 && lines[0].startsWith("读不出")) activityErr = lines[0]
            activityLoading = false
        }
    }

    fun loadPool(reloadGroups: Boolean = false) {
        if (poolLoading) return
        poolLoading = true
        poolErr = ""
        // ★ 手改/手删之后要**把已经展开过的那几组重新取一遍**：条目文本与条数都变了。
        //   重读会把分页缓存清掉，不清的话那几组还摆着旧文本，而且"已经取过"的标志
        //   会让它们不再去取（`loadGroup` 对 `loaded` 直接返回）⇒ 只能在这儿补。
        val pages = if (reloadGroups) poolGroups.keys.toList() else emptyList()
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) { PyBridge.poolViewJson(dataDir) }
            // ★ 池子视图里带着**空间图的 331 个点** ⇒ 解析是一块实打实的同步开销
            val v = parseOffMain { parsePoolView(raw) }
            pool = v
            if (!v.ok) poolErr = v.err.ifBlank { "读不出池子" }
            // 分页缓存跟着一起作废：池子重读了，旧的分页就是旧数据
            poolGroups = emptyMap()
            poolLoading = false
            pages.forEach { loadGroup(it) }
        }
    }

    /**
     * ★ 记忆页上**改 / 删了一条之后**重读池子。
     *   池子视图本来就没取过（用户还没进过记忆页）就不用管 —— 一进那一屏就会重读。
     */
    private fun refreshPoolAfterEdit() {
        if (pool == null && poolGroups.isEmpty()) return
        loadPool(reloadGroups = true)
    }

    // ---- 池子分页（懒加载）
    /**
     * 每一组已取到哪儿了。键 = **代码里的层键**（底色 / 情境 / 记忆 / 临时）。
     *
     * ★ 为什么必须分页：池子只会越来越大，而条目要在**主线程**上解析成对象。
     *   一次吐上千条，卡的是解析，不是别的。
     */
    var poolGroups by mutableStateOf<Map<String, PoolGroup>>(emptyMap())
        private set

    /** 展开某组时取第一页；点「加载更多」取下一页。重复调用会被吃掉。 */
    fun loadGroup(layer: String, more: Boolean = false) {
        val cur = poolGroups[layer]
        if (cur?.loading == true) return
        if (!more && cur?.loaded == true) return
        val off = if (more) (cur?.nextOffset ?: 0) else 0
        poolGroups = poolGroups + (layer to (cur ?: PoolGroup()).copy(loading = true))
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) {
                PyBridge.poolEntriesJson(dataDir, layer, off)
            }
            val p = parseOffMain { parsePoolPage(raw) }
            val prev = poolGroups[layer] ?: PoolGroup()
            poolGroups = poolGroups + (layer to if (!p.ok) {
                prev.copy(loading = false, err = p.err)
            } else {
                PoolGroup(
                    loading = false, loaded = true, err = "",
                    total = p.total, nextOffset = p.nextOffset, hasMore = p.hasMore,
                    // 「加载更多」是**追加**；重新展开则是替换
                    long = if (more) prev.long + p.long else p.long,
                    temp = if (more) prev.temp + p.temp else p.temp,
                )
            })
        }
    }

    // ==================================================================== 设置
    var knobs by mutableStateOf<List<Knob>>(emptyList())
        private set
    var knobsErr by mutableStateOf("")
        private set
    var settingsLoading by mutableStateOf(false)
        private set

    /** 改过、**还没保存**的值：`path -> 字符串形式的值`。 */
    var overrides by mutableStateOf<Map<String, String>>(emptyMap())
        private set
    var settingsMsg by mutableStateOf("")
        private set

    var creds by mutableStateOf(CredsState())
        private set

    // ★ 密钥永远从空开始：`creds_state_json` 只给"有没有"，**绝不回显**。
    var embedBaseUrlDraft by mutableStateOf("")
        private set
    var embedKeyDraft by mutableStateOf("")
        private set
    var chatKeyDraft by mutableStateOf("")
        private set
    var chatUrlDraft by mutableStateOf("")
        private set

    private var credsLoadedOnce = false

    // ---- 提示词（会话前缀 system①②）
    // 默认值在 Python 侧的 `prompts.py` 里（**冻结稿**），这里编辑的是**覆盖**。
    // 草稿 = 界面上正在编辑的文本；没改过时它就是默认稿（让人能看着默认改）。
    var promptSys1 by mutableStateOf("")
        private set
    var promptSys2 by mutableStateOf("")
        private set
    var promptEdited by mutableStateOf(false)
        private set

    fun updatePromptSys1(s: String) {
        promptSys1 = s
    }

    fun updatePromptSys2(s: String) {
        promptSys2 = s
    }

    fun savePrompt() {
        settingsMsg = ""
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) {
                PyBridge.promptSave(dataDir, promptSys1, promptSys2)
            }
            val o = parseObj(raw)
            if (o == null || !o.bool("ok")) {
                settingsMsg = o?.str("err")?.ifBlank { null } ?: "保存失败"
            } else {
                settingsMsg = "已保存"
                applyPrompt(withContext(PyBridge.dispatcher) { PyBridge.promptJson(dataDir) })
            }
        }
    }

    /** 恢复默认：两边清空 ⇒ Python 侧删掉覆盖文件，回到 `prompts.py` 的冻结稿。 */
    fun restoreDefaultPrompt() {
        promptSys1 = ""
        promptSys2 = ""
        settingsMsg = ""
        viewModelScope.launch {
            withContext(PyBridge.dispatcher) { PyBridge.promptSave(dataDir, "", "") }
            applyPrompt(withContext(PyBridge.dispatcher) { PyBridge.promptJson(dataDir) })
            settingsMsg = "已恢复默认"
        }
    }

    private fun applyPrompt(raw: String) {
        // ⚠️ 这一处**不挪**：它只是两个字符串，挪出去省不了什么，却让"解析"分了两套写法。
        val o = parseObj(raw) ?: return
        if (!o.bool("ok")) return
        promptEdited = o.bool("edited")
        promptSys1 = o.str("sys1").ifBlank { o.str("default_sys1") }
        promptSys2 = o.str("sys2").ifBlank { o.str("default_sys2") }
    }

    fun loadSettings() {
        if (settingsLoading) return
        settingsLoading = true
        knobsErr = ""
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) { PyBridge.knobsJson(dataDir) }
            val (list, err) = parseOffMain { parseKnobs(raw) }
            knobs = list
            knobsErr = err
            val cRaw = withContext(PyBridge.dispatcher) { PyBridge.credsStateJson(dataDir) }
            creds = parseOffMain { parseCreds(cRaw) }
            if (!credsLoadedOnce) {
                // 地址与 chat_url 可以回显（它们不是秘密）；**两个 Key 保持空**。
                embedBaseUrlDraft = creds.embedBaseUrl
                chatUrlDraft = creds.chatUrl
                credsLoadedOnce = true
            }
            val pRaw = withContext(PyBridge.dispatcher) { PyBridge.promptJson(dataDir) }
            applyPrompt(pRaw)
            settingsLoading = false
        }
    }
    fun knobValue(k: Knob): String = overrides[k.rawPath] ?: k.value

    fun isDirty(k: Knob): Boolean = overrides[k.rawPath]?.let { it != k.value } ?: false

    fun setKnob(path: String, value: String) {
        overrides = overrides + (path to value)
        settingsMsg = ""
    }

    fun saveKnobs() {
        if (overrides.isEmpty()) {
            settingsMsg = "没有改动"
            return
        }
        val payload = JSONObject()
        knobs.forEach { k ->
            val v = overrides[k.rawPath] ?: return@forEach
            when (k.kind) {
                "int" -> payload.put(k.rawPath, v.toIntOrNull() ?: k.value.toIntOrNull() ?: 0)
                "float" -> payload.put(
                    k.rawPath,
                    v.toDoubleOrNull() ?: k.value.toDoubleOrNull() ?: 0.0,
                )

                "bool" -> payload.put(k.rawPath, v.toBooleanStrictOrNull() ?: false)
                else -> payload.put(k.rawPath, v)
            }
        }
        settingsLoading = true
        settingsMsg = "保存中…"
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) {
                PyBridge.settingsSave(dataDir, payload.toString())
            }
            val o = parseObj(raw)
            settingsLoading = false
            if (o == null || !o.bool("ok")) {
                settingsMsg = "没保存上：" + (o?.str("err")?.ifBlank { null } ?: "读不懂返回")
                return@launch
            }
            // ★ 用返回的 effective **重新读一遍**：那才是真正生效的值。
            val eff = o.sub("effective")
            if (eff != null) {
                knobs = knobs.map { k ->
                    val nv = if (eff.has(k.rawPath)) scalarToString(eff.opt(k.rawPath)) else k.value
                    if (nv == k.value) k else k.copy(value = nv, changed = true)
                }
            }
            overrides = emptyMap()
            settingsMsg = "已保存"
        }
    }

    fun updateEmbedBaseUrl(s: String) {
        embedBaseUrlDraft = s
    }

    fun updateEmbedKey(s: String) {
        embedKeyDraft = s
    }

    fun updateChatKey(s: String) {
        chatKeyDraft = s
    }

    fun updateChatUrl(s: String) {
        chatUrlDraft = s
    }

    /**
     * 保存凭证。
     * ★ 两个 Key 传**空字符串 = 这一项不动**（Python 侧就是这个语义），
     *   所以界面把留空当成「不改」，而不是「清空」。
     */
    fun saveCreds() {
        settingsLoading = true
        settingsMsg = "…"
        val base = embedBaseUrlDraft
        val ek = embedKeyDraft
        val ck = chatKeyDraft
        val cu = chatUrlDraft
        viewModelScope.launch {
            val raw = withContext(PyBridge.dispatcher) {
                PyBridge.credsSave(dataDir, base, ek, ck, cu)
            }
            val o = parseObj(raw)
            settingsLoading = false
            if (o == null || !o.bool("ok")) {
                settingsMsg = (o?.str("err")?.ifBlank { null } ?: "没写进去")
                return@launch
            }
            // 写成功就把密钥草稿清空（不留在内存里），并重读状态。
            embedKeyDraft = ""
            chatKeyDraft = ""
            val cRaw = withContext(PyBridge.dispatcher) { PyBridge.credsStateJson(dataDir) }
            creds = parseOffMain { parseCreds(cRaw) }
            embedBaseUrlDraft = creds.embedBaseUrl
            chatUrlDraft = creds.chatUrl
            settingsMsg = "已保存"
        }
    }

    // ==================================================================== 诊断
    var diagRunning by mutableStateOf(false)
        private set

    fun runDiag(kind: Diag) {
        if (diagRunning) return
        diagRunning = true
        viewModelScope.launch {
            val lines: List<String> = when (kind) {
                // ★ 自检/探针要跑 numpy —— 一样不许压主线程。
                Diag.SelfCheck -> {
                    val a = withContext(PyBridge.dispatcher) { PyBridge.selfCheck() }
                    val cred = withContext(PyBridge.dispatcher) { PyBridge.setPaths(dataDir) }
                    val info = withContext(PyBridge.dispatcher) { PyBridge.pythonInfo() }
                    listOf("解释器　$info", "数据目录　$dataDir", "凭证路径　$cred", "", a)
                }

                Diag.Precision -> withContext(PyBridge.dispatcher) { PyBridge.precisionLines() }
                Diag.PoolState -> withContext(PyBridge.dispatcher) { PyBridge.stateLines(dataDir) }
                Diag.Pending -> withContext(PyBridge.dispatcher) { PyBridge.pendingLines(dataDir) }
            }
            diagResult = DiagResult(kind.label, lines)
            diagRunning = false
        }
    }

    var diagResult by mutableStateOf<DiagResult?>(null)
        private set

    fun dismissDiag() {
        diagResult = null
    }

    init {
        // ★ 流式增量那条收集（主线程）—— 与下面"把数据目录交给 Python"无关，各自独立。
        applyDeltas()
    }

    init {
        // ★ 第一件事：把数据目录交给 Python，并且**在这条独占线程上**完成
        //   （`COMPANION_CRED_PATH` 是进程级环境变量，越早设越好）。
        viewModelScope.launch {
            withContext(PyBridge.dispatcher) { PyBridge.setPaths(dataDir) }
        }
    }
}

enum class Tab(val routeKey: String, val title: String) {
    Chat("chat", "对话"),
    // ★ 显示名与代码里的层名（`LAYERS` = 底色/情境/记忆/临时）是两件事：
    //   界面用正式的词（自我/情境/事实/短期记忆），代码键是**数据契约**的一部分。
    //   ⚠️ 2026-10-05 键整体改过一次（`情境负面`→`情境`）—— 有意的、带过渡别名的一次性改动，
    //   ⛔ 不是"以后可以随手改键"的许可：键一改，已有的池子就对不上。
    Pool("pool", "记忆"),
    Settings("settings", "设置"),
    // ★ **它最近做了什么**（活动记录）。★ 用户定："另开一页"、**只可查不推送**。
    Activity("activity", "活动"),
}

enum class Diag(val label: String) {
    SelfCheck("环境自检"),
    Precision("跨平台探针"),
    PoolState("池子状态"),
    Pending("在途场次"),
}

data class DiagResult(val title: String, val lines: List<String>)
