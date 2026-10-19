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
import kotlinx.coroutines.delay
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
            Tab.Pool -> if (graph == null && !graphLoading) loadGraph()
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

    // ⛔ 2026-10-17：`memoryTarget` / `memoryDraft` / `memoryDeleting` / `memoryIsLast` /
    //   `openMemoryEditor` / `closeMemoryEditor` / `say` 这一整套（"长按条目就地改/删"的
    //   弹窗状态）都删掉了 —— 弹窗本身（`MemoryEdit.kt`）也删了。

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
                // ★★ 新线：这一轮注入的那段回忆（编写原文 + 用了哪几条 + 版本/耗时）
                recall = e.sub("回忆"),
            )
        }
    }

    private fun parsePulls(o: JSONObject): List<Pull> {
        val a = o.arr("pulls") ?: return emptyList()
        return (0 until a.length()).mapNotNull { i ->
            val e = a.optJSONObject(i) ?: return@mapNotNull null
            Pull(query = e.str("query"), got = e.strList("got"), 回 = e.str("回"))
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
    // ⛔ 2026-10-17：`editMemory` / `deleteMemory`（手改、手删**池子里的条目**）删掉了 ——
    //   它们调的 `chat_edit_entry` / `chat_delete_entry` 写的是已经不存在的池子。
    //   改 / 删现在在**记忆页的件**上（长按件 → 剪出 / 删）。

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
                // ★ 2026-10-19：优先用后端回传的整轮 reasoning；流式缓冲只作兜底
                //   （有工具往返时，流式那段会在调用边界丢掉）。
                val _cot = o.str("reasoning").trim()
                turn.reasoning = (_cot.ifBlank { streamReasoning.trim() }).ifBlank { null }
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
    /** ★★ 记忆页的数据（件浏览）—— 旧的四档池子在新线里不存在了。 */
    var graph by mutableStateOf<GraphView?>(null)
    var graphLoading by mutableStateOf(false)
    var graphErr by mutableStateOf("")

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

    /** ★ 记忆页长按某件 ⇒ 待处理（"剪出"还是"删掉"由弹窗问）。 */
    var 件待处理 by mutableStateOf<String?>(null)

    /** 件上的删改（`剪出` / `删`），做完重读图。 */
    fun graphAction(件id: String, action: String) {
        件待处理 = null
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching {
                PyBridge.graphActionJson(dataDir.trimEnd('/'), 件id, action)
            }.getOrNull()
            withContext(Dispatchers.Main) {
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                if (o == null || !o.optBoolean("ok", false)) {
                    graphErr = o?.optString("err").orEmpty().ifBlank { "没做成" }
                } else {
                    graph = null
                    loadGraph()
                }
            }
        }
    }

    /** 读**件浏览**（`graph_view_json`）。⛔ 只读，不改图。 */
    fun loadGraph() {
        if (graphLoading) return
        graphLoading = true
        graphErr = ""
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.graphViewJson(dataDir.trimEnd('/')) }.getOrNull()
            withContext(Dispatchers.Main) {
                graphLoading = false
                if (r.isNullOrBlank()) { graphErr = "读不到图"; return@withContext }
                val o = runCatching { JSONObject(r) }.getOrNull()
                if (o == null || !o.optBoolean("ok", false)) {
                    graphErr = o?.optString("err").orEmpty().ifBlank { "图读不出来" }
                    return@withContext
                }
                val 统计 = o.optJSONObject("统计")
                val 件们 = java.util.ArrayList<件View>()
                val arr = o.optJSONArray("件")
                if (arr != null) for (i in 0 until arr.length()) {
                    val j = arr.optJSONObject(i) ?: continue
                    val 成员 = java.util.ArrayList<件成员View>()
                    val ms = j.optJSONArray("成员")
                    if (ms != null) for (k in 0 until ms.length()) {
                        val m = ms.optJSONObject(k) ?: continue
                        成员.add(
                            件成员View(
                                场id = m.optString("场id"), 轮 = m.optInt("轮"),
                                文本 = m.optString("文本"), 点亮 = m.optBoolean("点亮"),
                            )
                        )
                    }
                    件们.add(
                        件View(
                            id = j.optString("件id"), 强度 = j.optDouble("强度", 0.0),
                            在标记集 = j.optBoolean("在标记集", false), 成员 = 成员,
                        )
                    )
                }
                val 待 = java.util.ArrayList<待观察View>()
                val ws = o.optJSONArray("待观察池")
                if (ws != null) for (i in 0 until ws.length()) {
                    val j = ws.optJSONObject(i) ?: continue
                    待.add(待观察View(j.optString("单元"), j.optString("为什么进来")))
                }
                // ★ 组（依赖簇）：归档时按场算出来的（`graph.组`）
                val 组们 = java.util.ArrayList<组View>()
                val gs = o.optJSONArray("组")
                if (gs != null) for (i in 0 until gs.length()) {
                    val j = gs.optJSONObject(i) ?: continue
                    val ms = j.optJSONArray("成员")
                    val 成员 = java.util.ArrayList<组成员View>()
                    if (ms != null) for (k in 0 until ms.length()) {
                        val m = ms.optJSONObject(k) ?: continue
                        成员.add(组成员View(m.optString("场id"), m.optInt("轮"), m.optString("文本")))
                    }
                    组们.add(
                        组View(
                            id = j.optString("id"), 场id = j.optString("场id"),
                            轮起 = j.optInt("轮起"), 轮止 = j.optInt("轮止"),
                            来源 = j.optString("来源"),
                            场们 = j.optJSONArray("场们")?.let { a ->
                                (0 until a.length()).map { i -> a.optString(i) }
                            }.orEmpty(),
                            成员 = 成员,
                        )
                    )
                }
                // ★ 势（读取路径的 EMA）：只带最亮的一批
                val 势们 = java.util.ArrayList<势View>()
                val ps = o.optJSONArray("势")
                if (ps != null) for (i in 0 until ps.length()) {
                    val j = ps.optJSONObject(i) ?: continue
                    势们.add(
                        势View(
                            单元 = j.optString("单元"), 标签 = j.optString("标签"),
                            文本 = j.optString("文本"),
                            势 = j.optDouble("势", 0.0), 唤醒 = j.optDouble("唤醒", 0.0),
                        )
                    )
                }
                graph = GraphView(
                    ok = true, 版本 = o.optInt("版本"),
                    cell数 = 统计?.optInt("cell") ?: 0, 件数 = 统计?.optInt("件") ?: 0,
                    组数 = 统计?.optInt("组") ?: 0,
                    标记集数 = 统计?.optInt("标记集") ?: 0, 势数 = 统计?.optInt("势") ?: 0,
                    待观察数 = 统计?.optInt("待观察池") ?: 0,
                    件们 = 件们, 组们 = 组们, 势们 = 势们, 待观察池 = 待,
                )
            }
        }
    }

    // ⛔ 2026-10-17：`loadPool` / `loadGroup` / `poolGroups` / `refreshPoolAfterEdit`
    //   整套**池子浏览**都删掉了 —— 它读的 `pool_view_json` / `pool_entries_json` 已经没了。
    //   记忆页现在读的是 `loadGraph()`（图里的件）。

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

    /** 按内部路径取当前值（二级页要显示「现在用的模型」这类东西）。 */
    fun knobValueOf(path: String): String =
        knobs.firstOrNull { it.rawPath == path }?.let { knobValue(it) } ?: ""

    /** 二级页用：确保凭证/旋钮已经读过一遍（`loadSettings` 自己会去重）。 */
    fun loadCreds() = loadSettings()

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
                // ★ 自检要跑 numpy —— 一样不许压主线程。
                Diag.SelfCheck -> {
                    // ⚠️ 先 `setPaths`：自检要在**应用私有目录**下干活，而 Python 侧得先知道那个目录。
                    val cred = withContext(PyBridge.dispatcher) { PyBridge.setPaths(dataDir) }
                    val a = withContext(PyBridge.dispatcher) { PyBridge.selfCheck(dataDir) }
                    val info = withContext(PyBridge.dispatcher) { PyBridge.pythonInfo() }
                    listOf("解释器　$info", "数据目录　$dataDir", "凭证路径　$cred", "", a)
                }

                Diag.Memory -> withContext(PyBridge.dispatcher) { PyBridge.stateLines(dataDir) }
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

    // ==================================================================== 参数账（可改）
    /**
     * ★★ 2026-10-18（作者）：参数账拆成**二级页面**，而且**每一项都能改**。
     * ⛔ 界面上不标「改过」—— 要看去「改动记录」那一页（作者口径）。
     */
    var 账 by mutableStateOf<List<账项>>(emptyList())
        private set
    var 账计数 by mutableStateOf(0)
        private set
    var 账加载中 by mutableStateOf(false)
        private set
    var 账错误 by mutableStateOf("")
        private set
    /** 正在改那一项的校验提示（键 → 一句话）。空 = 这项没问题。 */
    var 账提示 by mutableStateOf(mapOf<String, String>())
        private set

    fun loadLedger() {
        if (账加载中) return
        账加载中 = true
        账错误 = ""
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.ledgerJson(dataDir.trimEnd('/')) }.getOrNull()
            withContext(Dispatchers.Main) {
                账加载中 = false
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                if (o == null || !o.optBoolean("ok", false)) {
                    账错误 = o?.optString("err").orEmpty().ifBlank { "参数账读不出来" }
                    return@withContext
                }
                val arr = o.optJSONArray("项")
                val 出 = java.util.ArrayList<账项>()
                if (arr != null) for (i in 0 until arr.length()) {
                    val j = arr.optJSONObject(i) ?: continue
                    出.add(
                        账项(
                            键 = j.optString("键"), 组 = j.optString("组"),
                            组名 = j.optString("组名"), 名 = j.optString("名"),
                            值 = j.opt("值")?.toString().orEmpty(), 类型 = j.optString("类型"),
                            依据 = j.optString("依据"), 说明 = j.optString("说明"),
                            无读者 = j.optString("无读者"),
                            可改 = j.optBoolean("可改", true),
                            可填 = j.optJSONArray("可填")?.let { a ->
                                (0 until a.length()).map { x -> a.optString(x) }
                            }.orEmpty(),
                            提示 = j.optString("提示"),
                        )
                    )
                }
                账 = 出
                账计数 = o.optInt("计数", 出.size)
            }
        }
    }

    /** 改一项。成功 ⇒ 重新读一遍账（值由 Python 侧落定，界面不自己猜）。 */
    fun setLedger(键: String, 值: String, onDone: (String) -> Unit = {}) {
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.ledgerSet(dataDir.trimEnd('/'), 键, 值) }.getOrNull()
            withContext(Dispatchers.Main) {
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                val ok = o?.optBoolean("ok", false) == true
                val err = o?.optString("err").orEmpty()
                账提示 = if (ok) 账提示 - 键 else 账提示 + (键 to err.ifBlank { "没改成" })
                if (ok) loadLedger()
                onDone(if (ok) "" else err.ifBlank { "没改成" })
            }
        }
    }

    var 账改动 by mutableStateOf<List<账改动>>(emptyList())
        private set
    var 改动加载中 by mutableStateOf(false)
        private set
    var 改动错误 by mutableStateOf("")
        private set

    fun loadLedgerChanges() {
        if (改动加载中) return
        改动加载中 = true
        改动错误 = ""
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.ledgerChanges(dataDir.trimEnd('/')) }.getOrNull()
            withContext(Dispatchers.Main) {
                改动加载中 = false
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                if (o == null || !o.optBoolean("ok", false)) {
                    改动错误 = o?.optString("err").orEmpty().ifBlank { "改动记录读不出来" }
                    return@withContext
                }
                val arr = o.optJSONArray("记录")
                val 出 = java.util.ArrayList<账改动>()
                if (arr != null) for (i in 0 until arr.length()) {
                    val j = arr.optJSONObject(i) ?: continue
                    出.add(
                        账改动(
                            时间 = j.optString("时间"), 键 = j.optString("键"),
                            旧 = j.opt("旧")?.toString().orEmpty(),
                            新 = j.opt("新")?.toString().orEmpty(),
                        )
                    )
                }
                账改动 = 出
            }
        }
    }

    // ==================================================================== 提示词（按项）
    var 提示词组们 by mutableStateOf<List<提示词组>>(emptyList())
        private set
    var 提示词加载中 by mutableStateOf(false)
        private set
    var 提示词错误 by mutableStateOf("")
        private set

    fun loadPrompts() {
        if (提示词加载中) return
        提示词加载中 = true
        提示词错误 = ""
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.promptListJson(dataDir.trimEnd('/')) }.getOrNull()
            withContext(Dispatchers.Main) {
                提示词加载中 = false
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                if (o == null || !o.optBoolean("ok", false)) {
                    提示词错误 = o?.optString("err").orEmpty().ifBlank { "提示词清单读不出来" }
                    return@withContext
                }
                val gs = o.optJSONArray("组们")
                val 出 = java.util.ArrayList<提示词组>()
                if (gs != null) for (i in 0 until gs.length()) {
                    val g = gs.optJSONObject(i) ?: continue
                    val arr = g.optJSONArray("项")
                    val 项 = java.util.ArrayList<提示词项>()
                    if (arr != null) for (k in 0 until arr.length()) {
                        val j = arr.optJSONObject(k) ?: continue
                        项.add(
                            提示词项(
                                id = j.optString("id"), 名称 = j.optString("名称"),
                                占位符 = j.optJSONArray("占位符")?.let { a ->
                                    (0 until a.length()).map { x -> a.optString(x) }
                                }.orEmpty(),
                                说明 = j.optString("说明"), 已改 = j.optBoolean("已改"),
                                字数 = j.optInt("字数"),
                            )
                        )
                    }
                    出.add(提示词组(g.optString("组"), 项))
                }
                提示词组们 = 出
            }
        }
    }

    var 提示词屏 by mutableStateOf<提示词屏?>(null)
        private set
    var 提示词屏加载中 by mutableStateOf(false)
        private set

    fun loadPrompt(id: String) {
        提示词屏加载中 = true
        提示词屏 = null
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.promptGetJson(dataDir.trimEnd('/'), id) }.getOrNull()
            withContext(Dispatchers.Main) {
                提示词屏加载中 = false
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                if (o == null || !o.optBoolean("ok", false)) {
                    提示词错误 = o?.optString("err").orEmpty().ifBlank { "这一项读不出来" }
                    return@withContext
                }
                提示词屏 = 提示词屏(
                    id = o.optString("id"), 名称 = o.optString("名称"),
                    占位符 = o.optJSONArray("占位符")?.let { a ->
                        (0 until a.length()).map { x -> a.optString(x) }
                    }.orEmpty(),
                    说明 = o.optString("说明"), 已改 = o.optBoolean("已改"),
                    正文 = o.optString("正文"), 默认 = o.optString("默认"),
                )
            }
        }
    }

    /** 存一项（Python 侧先校验占位符；不过就不存）。`onDone("")` = 成功。 */
    fun savePrompt(id: String, 正文: String, onDone: (String) -> Unit) {
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.promptSaveOne(dataDir.trimEnd('/'), id, 正文) }.getOrNull()
            withContext(Dispatchers.Main) {
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                val ok = o?.optBoolean("ok", false) == true
                if (ok) { loadPrompt(id); loadPrompts() }
                onDone(if (ok) "" else o?.optString("err").orEmpty().ifBlank { "没存上" })
            }
        }
    }

    fun resetPrompt(id: String, onDone: (String) -> Unit) {
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.promptResetOne(dataDir.trimEnd('/'), id) }.getOrNull()
            withContext(Dispatchers.Main) {
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                val ok = o?.optBoolean("ok", false) == true
                if (ok) { loadPrompt(id); loadPrompts() }
                onDone(if (ok) "" else o?.optString("err").orEmpty().ifBlank { "没恢复成" })
            }
        }
    }

    // ==================================================================== 查一查（只读探针）
    var 查线索 by mutableStateOf("")
    var 查结果 by mutableStateOf<ProbeResult?>(null)
    var 查中 by mutableStateOf(false)
    var 查错误 by mutableStateOf("")

    fun probe() {
        val 句 = 查线索.trim()
        if (句.isBlank() || 查中) return
        查中 = true
        查错误 = ""
        viewModelScope.launch {
            // ★ 2026-10-19：没开着的场也能查 —— 先确保有一场（没有就开），再跑探针
            if (sid == null) {
                openChatIfNeeded()
                repeat(20) {
                    if (sid != null) return@repeat
                    delay(300)
                }
            }
            // ⚠️ 会话句柄是 `s<场>.<n>`（不是场次号）——探针要和 chat_ask 用同一个键
            val mySid = sid ?: ""
            val r = withContext(Dispatchers.IO) {
                runCatching { PyBridge.recallProbe(dataDir.trimEnd('/'), mySid, 句) }.getOrNull()
            }
            val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
            if (o?.optBoolean("ok", false) != true) {
                查错误 = o?.str("err")?.ifBlank { null } ?: "没查成"
                查结果 = null
            } else {
                val ca = o.arr("格") ?: JSONArray()
                val pa = o.arr("件") ?: JSONArray()
                查结果 = ProbeResult(
                    线索 = o.str("线索"),
                    激活数 = o.optInt("激活数", 0),
                    格 = (0 until ca.length()).mapNotNull { i ->
                        val e = ca.optJSONObject(i) ?: return@mapNotNull null
                        ProbeCell(
                            场 = e.str("场"),
                            轮 = e.optInt("轮", 0),
                            分 = e.optDouble("分", 0.0),
                            组 = e.str("组"),
                        )
                    },
                    件 = (0 until pa.length()).mapNotNull { i ->
                        val e = pa.optJSONObject(i) ?: return@mapNotNull null
                        ProbePiece(
                            件id = e.str("件id"),
                            最高 = e.optDouble("最高", 0.0),
                            段数 = e.optInt("段数", 0),
                            取用 = e.optBoolean("取用", false),
                        )
                    },
                )
            }
            查中 = false
        }
    }

    // ==================================================================== 旧场（只读）
    var 旧场们 by mutableStateOf<List<PastSession>>(emptyList())
        private set
    var 旧场详情 by mutableStateOf<PastDetail?>(null)
        private set
    var 旧场错误 by mutableStateOf("")
        private set

    fun loadPastList() {
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.pastListJson(dataDir.trimEnd('/')) }.getOrNull()
            withContext(Dispatchers.Main) {
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                val a = o?.arr("场们") ?: JSONArray()
                val list = (0 until a.length()).mapNotNull { i ->
                    val e = a.optJSONObject(i) ?: return@mapNotNull null
                    PastSession(
                        场id = e.str("场id"),
                        轮数 = e.optInt("轮数", 0),
                        ts = e.optDouble("ts", 0.0),
                    )
                }
                旧场们 = list
                旧场错误 = if (o?.optBoolean("ok", false) == true) "" else (o?.str("err") ?: "")
            }
        }
    }

    fun loadPast(sid: String) {
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.pastGetJson(dataDir.trimEnd('/'), sid) }.getOrNull()
            withContext(Dispatchers.Main) {
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                if (o?.optBoolean("ok", false) != true) {
                    旧场错误 = o?.str("err")?.ifBlank { null } ?: "这一场读不到"
                    旧场详情 = null
                    return@withContext
                }
                val a = o.arr("轮") ?: JSONArray()
                val turns = (0 until a.length()).mapNotNull { i ->
                    val e = a.optJSONObject(i) ?: return@mapNotNull null
                    val sa = e.arr("段") ?: JSONArray()
                    PastTurn(
                        轮 = e.optInt("轮", 0),
                        user = e.str("user"),
                        ai = e.str("ai"),
                        段 = (0 until sa.length()).mapNotNull { j ->
                            val c = sa.optJSONObject(j) ?: return@mapNotNull null
                            PastSeg(类 = c.str("类"), 文本 = c.str("文本"), 回 = c.str("回"))
                        },
                        撤回 = e.optBoolean("撤回", false),
                    )
                }
                val 归档o = o.optJSONObject("归档")
                val ja = 归档o?.arr("判") ?: JSONArray()
                val 判们 = (0 until ja.length()).mapNotNull { k ->
                    val e = ja.optJSONObject(k) ?: return@mapNotNull null
                    PastJudge(
                        轮 = e.optInt("轮", 0),
                        来源 = e.str("来源"),
                        归进 = e.str("归进"),
                        新建 = e.optBoolean("新建", false),
                        说法 = e.str("说法"),
                        记 = e.strList("记"),
                        err = e.str("err"),
                    )
                }
                val la = 归档o?.arr("层1") ?: JSONArray()
                val 层1行 = (0 until la.length()).flatMap { k ->
                    val arr = la.optJSONArray(k) ?: return@flatMap emptyList()
                    (0 until arr.length()).mapNotNull { j ->
                        val e = arr.optJSONObject(j) ?: return@mapNotNull null
                        val keys = e.keys()
                        buildList {
                            while (keys.hasNext()) {
                                val key = keys.next()
                                add("$key：${e.opt(key).toString()}")
                            }
                        }.joinToString("｜")
                    }
                }
                旧场详情 = PastDetail(
                    场id = o.str("场id"),
                    系统前缀 = o.strList("系统前缀"),
                    常驻 = o.str("常驻"),
                    在场 = o.str("在场"),
                    轮 = turns,
                    指纹 = runCatching { o.optJSONObject("指纹")?.toString(1) ?: "" }.getOrDefault(""),
                    归档 = PastArchive(判 = 判们, 层1 = 层1行),
                )
                旧场错误 = ""
            }
        }
    }

    fun closePast() { 旧场详情 = null }

    // ==================================================================== 可用模型（对话 / 嵌入 两家分开）
    var 模型们 by mutableStateOf<List<模型项>>(emptyList())
        private set
    var 嵌入模型们 by mutableStateOf<List<模型项>>(emptyList())
        private set
    var 模型端点 by mutableStateOf("")
        private set
    var 嵌入模型端点 by mutableStateOf("")
        private set
    var 模型错误 by mutableStateOf("")
        private set
    var 嵌入模型错误 by mutableStateOf("")
        private set
    var 拉模型中 by mutableStateOf(false)
        private set
    var 拉嵌入模型中 by mutableStateOf(false)
        private set

    /** 问端点要模型列表（★ 联网；只在用户点「拉取」时调）。`which` = `"embed"` / `"chat"`。 */
    fun loadModels(which: String = "chat") {
        val 嵌入 = which == "embed"
        if (if (嵌入) 拉嵌入模型中 else 拉模型中) return
        if (嵌入) { 拉嵌入模型中 = true; 嵌入模型错误 = "" } else { 拉模型中 = true; 模型错误 = "" }
        viewModelScope.launch(Dispatchers.IO) {
            val r = runCatching { PyBridge.modelsJson(dataDir.trimEnd('/'), which) }.getOrNull()
            withContext(Dispatchers.Main) {
                if (嵌入) 拉嵌入模型中 = false else 拉模型中 = false
                val o = runCatching { JSONObject(r.orEmpty()) }.getOrNull()
                if (o == null || !o.optBoolean("ok", false)) {
                    val 错 = o?.optString("err").orEmpty().ifBlank { "没拉到模型列表" }
                    if (嵌入) 嵌入模型错误 = 错 else 模型错误 = 错
                    return@withContext
                }
                val arr = o.optJSONArray("模型")
                val 出 = java.util.ArrayList<模型项>()
                if (arr != null) for (i in 0 until arr.length()) {
                    val j = arr.optJSONObject(i) ?: continue
                    出.add(模型项(j.optString("id"), j.optString("own")))
                }
                if (嵌入) {
                    嵌入模型们 = 出
                    嵌入模型端点 = o.optString("端点")
                } else {
                    模型们 = 出
                    模型端点 = o.optString("端点")
                }
            }
        }
    }
}

enum class Tab(val routeKey: String, val title: String) {
    Chat("chat", "对话"),
    // ★ 这一屏现在显示的是**图里的件**（2026-10-17）。键仍是 `pool`：
    //   它是深链里的老键（`Tab` 的 routeKey 已经进了别人的书签/脚本），
    //   为一个内部改名去动它没有好处，而改错了会让 `am start` 那类入口静默失效。
    Pool("pool", "记忆"),
    Settings("settings", "设置"),
    // ★ **它最近做了什么**（活动记录）。★ 用户定："另开一页"、**只可查不推送**。
    Activity("activity", "活动"),
}

enum class Diag(val label: String) {
    SelfCheck("环境自检"),
    Memory("记忆现状"),
    Pending("在途场次"),
}

data class DiagResult(val title: String, val lines: List<String>)
