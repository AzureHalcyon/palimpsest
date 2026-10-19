package com.azurehalcyon.palimpsest.plugin

/**
 * 契约里的**常量与 JSON 键名**。
 *
 * 为什么要有这个文件：AIDL 传的是 JSON 字符串 ⇒ **编译期不校验结构**，
 * 于是键名一旦两边各写各的就会静默读空。把键名收在这里，两边都引用它。
 *
 * 协议版本：插件 `hello()` 里回，本体不认就拒绝（不崩、也不猜）。
 */
object Contract {

    /** 自定义**签名级**权限（定义在 plugin-api 的 manifest 里）。 */
    const val PERMISSION = "com.azurehalcyon.palimpsest.permission.PLUGIN"

    /** 插件 Service 的 action。本体用这个 action 发现插件（配合 manifest 里的 `<queries>`）。 */
    const val ACTION = "com.azurehalcyon.palimpsest.plugin.BIND"

    /** ★ 协议版本。**只在破坏性改动时 +1**；插件版本低于它，本体直接不认。 */
    const val PROTOCOL = 1

    // ---- hello() ----
    const val K_PROTOCOL = "protocol"
    const val K_ID = "id"
    const val K_NAME = "name"
    const val K_VERSION = "version"

    // ---- describe() ----
    const val K_METRICS = "metrics"

    /** MetricInfo 的字段。★ `TOOL_NAME/TOOL_DESC/USE_WHEN` 是**给模型看的**那一份（设计稿 §2.6）。 */
    const val K_M_ID = "id"
    const val K_M_NAME = "name"
    const val K_M_UNIT = "unit"
    const val K_M_DESC = "desc"
    const val K_M_TOOL_NAME = "toolName"
    const val K_M_TOOL_DESC = "toolDesc"
    const val K_M_USE_WHEN = "useWhen"
    const val K_M_GRAIN_MS = "grainMs"
    const val K_M_MAX_WINDOW_MS = "maxWindowMs"
    const val K_M_SOURCES = "sources"

    /**
     * ★★ **读数的性质**（2026-10-06 加）：`事件型` / `单调累加` / `滚动窗口` / `瞬时噪声` / `均值慢变` / `触发派生`。
     *
     * 为什么要有这个：用户的决定是「**判断交给 AI，我们只给好工具**」——
     * 那么 AI 需要知道"**这个数为什么会变**"，否则它只能看到"变了"而无法判断这算不算一件事：
     *   · `滚动窗口`（如"最近一小时亮屏多久"）**结构上就会一直涨** ⇒ 单独变化通常不是事件；
     *   · `瞬时噪声`（如环境光）抖一下很正常；
     *   · `事件型`（如日程开始、显著运动触发）变一次就是真的发生了一件事。
     * ⛔ 这**不是判断**，是**事实声明**（由插件给，因为只有它知道这个数是怎么来的）。
     */
    const val K_M_NATURE = "nature"

    // ---- read() ----
    const val K_OK = "ok"
    const val K_METRIC = "metric"
    const val K_VALUE = "value"
    const val K_TREND = "trend"
    const val K_COVERAGE = "coverage"
    const val K_SOURCES = "sources"
    const val K_AT = "at"
    const val K_NOTE = "note"

    // ---- trend ----
    const val K_T_N = "n"
    const val K_T_MEAN = "mean"
    const val K_T_MIN = "min"
    const val K_T_MAX = "max"
    const val K_T_LAST = "last"
    const val K_T_FIRST = "first"
    const val K_T_SLOPE = "slope"
    const val K_T_PCT_OF_SELF = "pctOfSelf"
}
