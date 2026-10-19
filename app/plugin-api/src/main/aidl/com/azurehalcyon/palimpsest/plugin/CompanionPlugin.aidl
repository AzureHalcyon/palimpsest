// ★★ 插件接口 —— **整个架构里唯一的那个"接口"**
//
// 为什么参数/返回都是 `String`（里面装 JSON），而不是 Parcelable：
//   1. AIDL + Parcelable 要为每个类型写一份 `.aidl` 声明 + `Parcelable` 实现，样板极多；
//   2. **本项目已有先例**：`host_glue.py` 给 Kotlin 传数据就是"先 JSON 化"，
//      理由写在那边（Chaquopy 的 Map 类型推不出来；而且**以后加字段不用改桥**）。
//      这里沿用同一条理由：**加字段不用改 AIDL**。
//   3. 代价：编译期不做结构校验 ⇒ 由 `Contract.kt` 里的键名常量 + 文档补上。
//
// ⛔ 只有三个方法。**不要往里加"事件订阅""推送"这类** —— 那会把插件变成数据源，
//    而设计稿 §5 已经定了：**工具结果只活在当轮**，长期记忆交给归档。

package com.azurehalcyon.palimpsest.plugin;

interface CompanionPlugin {

    /** 协议版本 + 插件身份。返回 JSON：`{protocol, id, name, version}`。
     *  本体据此决定认不认这个插件（版本不匹配要能干净地拒绝，而不是崩）。 */
    String hello();

    /** ★ 我能提供哪些读数（**能力协商**）。返回 JSON：`{metrics:[MetricInfo...]}`。
     *  ⛔ 少了这个方法，本体和 AI **都不知道能问什么**。
     *  MetricInfo: `{id, name, unit, desc, toolName, toolDesc, useWhen, grainMs, maxWindowMs, sources[]}`
     *  ★ `toolName/toolDesc/useWhen` 就是**给模型看的工具描述** ——
     *    由插件自己声明，本体不硬编码（设计稿 §2.6）。 */
    String describe();

    /** 取读数。返回 JSON：`{ok, metric, value, trend, coverage, sources[], at, note}`。
     *  ⛔ 失败也必须**正常返回**（`ok:false` + `note`），不要抛 ——
     *    本体宁可拿到"这个渠道现在没有数据"，也不要一个跨进程异常。 */
    String fetch(String metric, long fromMs, long toMs);
}
