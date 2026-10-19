package com.azurehalcyon.palimpsest.ui

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/**
 * JSON 读取助手。**只在 JSON 由我们自己（Python 的 `host_glue`）产出时使用。**
 *
 * ⚠️ 为什么不用 kotlinx-serialization：桥那边已经有 `org.json`（Android 框架自带，
 *    零新依赖）；再加一套序列化插件+runtime 只为了读 4 个固定结构，不划算。
 *    ★ 规矩不变：**嵌套结构一律走 JSON 字符串**，Kotlin 侧按键取值 —— 这样
 *      以后 Python 侧加字段不用动桥。
 */
internal fun parseObj(raw: String?): JSONObject? = try {
    val t = raw?.trim().orEmpty()
    if (t.isEmpty()) null else JSONObject(t)
} catch (t: Throwable) {
    null
}

internal fun JSONObject.sub(key: String): JSONObject? =
    if (isNull(key)) null else optJSONObject(key)

internal fun JSONObject.arr(key: String): JSONArray? =
    if (isNull(key)) null else optJSONArray(key)

internal fun JSONObject.str(key: String, fallback: String = ""): String =
    if (isNull(key)) fallback else optString(key, fallback)

internal fun JSONObject.bool(key: String, fallback: Boolean = false): Boolean =
    if (isNull(key)) fallback else optBoolean(key, fallback)

internal fun JSONObject.int(key: String, fallback: Int = 0): Int =
    if (isNull(key)) fallback else optInt(key, fallback)

internal fun JSONObject.dbl(key: String, fallback: Double = 0.0): Double =
    if (isNull(key)) fallback else optDouble(key, fallback)

internal fun JSONObject.strList(key: String): List<String> {
    val a = arr(key) ?: return emptyList()
    return (0 until a.length()).map { i -> a.optString(i, "") }.filter { it.isNotEmpty() }
}

// --------------------------------------------------------------------------- 数字排版
/** 该用几位小数：越小越要留有效位（0.0231 不能被压成 0.02）。 */
internal fun decimalsFor(v: Double): Int = when {
    v == 0.0 -> 2
    kotlin.math.abs(v) >= 100.0 -> 0
    kotlin.math.abs(v) >= 10.0 -> 1
    kotlin.math.abs(v) >= 1.0 -> 2
    kotlin.math.abs(v) >= 0.1 -> 3
    else -> 4
}

internal fun fmt(v: Double, decimals: Int = decimalsFor(v)): String =
    String.format(Locale.US, "%.${decimals}f", v)

/** 去掉尾部多余的 0（0.70 → 0.7）；整数值不显示小数点。 */
internal fun trimNum(s: String): String =
    if (!s.contains('.')) s else s.trimEnd('0').trimEnd('.').ifEmpty { "0" }

/** 把一个 JSON 标量转成界面用的字符串（`23` → `"23"`，`0.7` → `"0.7"`）。 */
internal fun scalarToString(o: Any?): String = when (o) {
    null, JSONObject.NULL -> ""
    is Double -> if (o == Math.floor(o) && !o.isInfinite()) o.toLong().toString() else trimNum(fmt(o, 6))
    is Float -> scalarToString(o.toDouble())
    is Number -> o.toString()
    is Boolean -> o.toString()
    else -> o.toString()
}
