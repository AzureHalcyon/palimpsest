package com.azurehalcyon.palimpsest.ui

/**
 * **LaTeX → 可读文本**（**退路**，不是主路径）。
 *
 * ⚠️ **主路径是 [`MathRender`] 的真排版**（2026-10-06 用户明确要求：「不是做成 unicode，
 *   是真渲染，要不然复杂公式可读性不好」）。这里只在**渲染失败**时兜底 ——
 *   LaTeX 写错很常见（模型给的东西不保证合法），那时把公式转成"能读的文本"，
 *   ⛔ 绝不显示空白、也绝不吞内容。
 *
 * 转的是那些**在纯文本里也有对应字符**的东西：
 *   `x^2`→`x²` · `\alpha`→`α` · `\leq`→`≤` · `\sum`→`∑` · `\frac{a}{b}`→`a/b` · `\sqrt{x}`→`√x`，
 *   剩下的**原样保留**（`\oint` 就显示 `\oint`）—— 对"读懂它写了什么"这件事，
 *   **保留原文永远比猜错安全**（同 `Markdown.kt` 里那条原则）。
 * ⚠️ 这不是排版：矩阵/多行对齐只是按行拆开，分数不倒排。
 */
internal object Latex {

    /** 上标字符表（能转的字符；转不了的**整体退回**成 `^(...)`）。 */
    private val SUP = mapOf(
        '0' to '⁰', '1' to '¹', '2' to '²', '3' to '³', '4' to '⁴', '5' to '⁵',
        '6' to '⁶', '7' to '⁷', '8' to '⁸', '9' to '⁹',
        '+' to '⁺', '-' to '⁻', '=' to '⁼', '(' to '⁽', ')' to '⁾',
        'n' to 'ⁿ', 'i' to 'ⁱ',
    )

    /** 下标字符表。 */
    private val SUB = mapOf(
        '0' to '₀', '1' to '₁', '2' to '₂', '3' to '₃', '4' to '₄', '5' to '₅',
        '6' to '₆', '7' to '₇', '8' to '₈', '9' to '₉',
        '+' to '₊', '-' to '₋', '=' to '₌', '(' to '₍', ')' to '₎',
        'i' to 'ᵢ', 'j' to 'ⱼ', 'n' to 'ₙ', 'k' to 'ₖ', 'x' to 'ₓ', 'a' to 'ₐ',
        'e' to 'ₑ', 'o' to 'ₒ', 'p' to 'ₚ', 's' to 'ₛ', 't' to 'ₜ', 'm' to 'ₘ',
    )

    /** 希腊字母与常见符号（带反斜杠的宏名 → 字符）。 */
    private val MACROS = mapOf(
        // 希腊字母（小写）
        "alpha" to "α", "beta" to "β", "gamma" to "γ", "delta" to "δ", "epsilon" to "ε",
        "varepsilon" to "ε", "zeta" to "ζ", "eta" to "η", "theta" to "θ", "vartheta" to "ϑ",
        "iota" to "ι", "kappa" to "κ", "lambda" to "λ", "mu" to "μ", "nu" to "ν", "xi" to "ξ",
        "pi" to "π", "rho" to "ρ", "sigma" to "σ", "tau" to "τ", "upsilon" to "υ",
        "phi" to "φ", "varphi" to "φ", "chi" to "χ", "psi" to "ψ", "omega" to "ω",
        // 希腊字母（大写）
        "Gamma" to "Γ", "Delta" to "Δ", "Theta" to "Θ", "Lambda" to "Λ", "Xi" to "Ξ",
        "Pi" to "Π", "Sigma" to "Σ", "Upsilon" to "Υ", "Phi" to "Φ", "Psi" to "Ψ",
        "Omega" to "Ω",
        // 运算与关系
        "times" to "×", "cdot" to "·", "div" to "÷", "pm" to "±", "mp" to "∓",
        "leq" to "≤", "le" to "≤", "geq" to "≥", "ge" to "≥", "neq" to "≠", "ne" to "≠",
        "approx" to "≈", "sim" to "∼", "equiv" to "≡", "propto" to "∝",
        "ll" to "≪", "gg" to "≫", "infty" to "∞",
        // 大算符
        "sum" to "∑", "prod" to "∏", "int" to "∫", "oint" to "∮", "iint" to "∬",
        "partial" to "∂", "nabla" to "∇", "sqrt" to "√",
        // 集合与逻辑
        "in" to "∈", "notin" to "∉", "subset" to "⊂", "subseteq" to "⊆", "supset" to "⊃",
        "cup" to "∪", "cap" to "∩", "emptyset" to "∅", "varnothing" to "∅",
        "forall" to "∀", "exists" to "∃", "nexists" to "∄", "neg" to "¬",
        "land" to "∧", "lor" to "∨",
        // 箭头
        "to" to "→", "rightarrow" to "→", "leftarrow" to "←", "leftrightarrow" to "↔",
        "Rightarrow" to "⇒", "Leftarrow" to "⇐", "Leftrightarrow" to "⇔",
        "mapsto" to "↦", "uparrow" to "↑", "downarrow" to "↓",
        // 点与省略号
        "ldots" to "…", "dots" to "…", "cdots" to "⋯", "vdots" to "⋮", "ddots" to "⋱",
        // 其它常用
        "degree" to "°", "angle" to "∠", "perp" to "⊥", "parallel" to "∥",
        "star" to "⋆", "ast" to "∗", "circ" to "∘", "bullet" to "•",
        "therefore" to "∴", "because" to "∵", "prime" to "′",
        // 排版类：这些**只起排版作用**，转成空/空格
        "left" to "", "right" to "", "displaystyle" to "", "limits" to "",
        "quad" to "  ", "qquad" to "    ", "," to " ", ";" to " ", "!" to "", ":" to " ",
    )

    /** 函数名：`\sin` 这类**不该**变成符号，去掉反斜杠写成正常字即可。 */
    private val FUNCS = setOf(
        "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
        "sinh", "cosh", "tanh", "log", "ln", "lg", "exp", "lim", "max", "min",
        "det", "dim", "gcd", "sup", "inf", "arg", "deg", "bmod", "pmod",
    )

    /**
     * 一个**竖排**环境（矩阵 / 对齐 / 分段）→ 按行拆开。
     * `&` 是列分隔，转成两个空格；`\\` 是换行。
     */
    private val ENVS = setOf("matrix", "pmatrix", "bmatrix", "vmatrix", "Bmatrix", "Vmatrix",
                             "aligned", "align", "align*", "cases", "array", "gathered", "split")

    /** 入口：把一段 LaTeX 转成可读文本。`display` 只影响换行/环境的分隔符。 */
    fun toText(tex: String): String {
        val sb = StringBuilder()
        var i = 0
        val s = tex.trim().removePrefix("\\displaystyle").trim()
        while (i < s.length) {
            val c = s[i]
            when {
                // \宏名 或 \符号
                c == '\\' -> {
                    // \\ = 换行；\[ \] \( \) 已在解析层剥掉，这里兜底当空
                    if (i + 1 < s.length && s[i + 1] == '\\') {
                        sb.append('\n'); i += 2; continue
                    }
                    val m = Regex("""^\\([A-Za-z]+|.)""").find(s.substring(i))
                    val name = m?.groupValues?.get(1).orEmpty()
                    if (name.isEmpty()) { sb.append('\\'); i++; continue }
                    i += 1 + name.length
                    when {
                        name == "frac" || name == "dfrac" || name == "tfrac" -> {
                            val a = readGroup(s, i); i = a.second
                            val b = readGroup(s, i); i = b.second
                            val na = toText(a.first); val nb = toText(b.first)
                            // 简单两侧直接 a/b；含运算符/空格时加括号保住优先级
                            val simple = { t: String -> t.length <= 3 && t.none { it == ' ' || it == '+' || it == '-' } }
                            sb.append(if (simple(na) && simple(nb)) "$na/$nb" else "($na)/($nb)")
                        }
                        name == "sqrt" -> {
                            // 可选次数 \sqrt[3]{x}
                            var deg = ""
                            if (i < s.length && s[i] == '[') {
                                val close = s.indexOf(']', i)
                                if (close > 0) { deg = toText(s.substring(i + 1, close)); i = close + 1 }
                            }
                            val g = readGroup(s, i); i = g.second
                            val body = toText(g.first)
                            val sign = when (deg) { "3" -> "∛"; "4" -> "∜"; "" -> "√"; else -> "${deg}√" }
                            sb.append(if (body.length <= 1) "$sign$body" else "$sign($body)")
                        }
                        name == "text" || name == "mathrm" || name == "operatorname" -> {
                            val g = readGroup(s, i); i = g.second
                            sb.append(g.first)
                        }
                        name == "begin" -> {
                            val g = readGroup(s, i); i = g.second
                            val env = g.first.trim()
                            val endTag = "\\end{$env}"
                            val stop = s.indexOf(endTag, i)
                            val body = if (stop >= 0) s.substring(i, stop) else s.substring(i)
                            i = if (stop >= 0) stop + endTag.length else s.length
                            if (env in ENVS) {
                                if (env == "cases") sb.append('{')
                                body.split("\\\\").forEachIndexed { k, row ->
                                    if (k > 0) sb.append('\n')
                                    sb.append(toText(row.replace("&", "  ")).trim())
                                }
                            } else {
                                sb.append(toText(body))
                            }
                        }
                        name in FUNCS -> sb.append(name)
                        MACROS.containsKey(name) -> sb.append(MACROS.getValue(name))
                        // ⛔ 不认识的宏：原样留下（保留原文比猜错安全）
                        else -> sb.append('\\').append(name)
                    }
                }

                // 上下标
                c == '^' || c == '_' -> {
                    val isUp = c == '^'
                    val g = readGroup(s, i + 1)
                    i = g.second
                    val body = toText(g.first).trim()
                    val table = if (isUp) SUP else SUB
                    val mapped = body.map { table[it] }
                    if (mapped.all { it != null }) {
                        mapped.forEach { sb.append(it) }
                    } else {
                        // 转不了就明写出来，别丢信息
                        sb.append(if (isUp) "↑(" else "↓(").append(body).append(')')
                    }
                }

                c == '{' -> {                       // 纯分组用的花括号：丢掉
                    val g = readGroup(s, i); i = g.second; sb.append(toText(g.first))
                }
                c == '}' -> i++                     // 落单的右括号：丢
                c == '~' -> { sb.append(' '); i++ }
                c == '&' -> { sb.append("  "); i++ }
                else -> { sb.append(c); i++ }
            }
        }
        return sb.toString().trim()
    }

    /** 读一个 `{...}`（支持嵌套）或单个字符；返回 `(内容, 新下标)`。 */
    private fun readGroup(s: String, from: Int): Pair<String, Int> {
        var i = from
        while (i < s.length && s[i] == ' ') i++
        if (i >= s.length) return "" to i
        if (s[i] != '{') return s[i].toString() to (i + 1)
        var depth = 0
        val start = i + 1
        while (i < s.length) {
            when (s[i]) {
                '{' -> depth++
                '}' -> {
                    depth--
                    if (depth == 0) return s.substring(start, i) to (i + 1)
                }
            }
            i++
        }
        return s.substring(start) to s.length
    }
}
