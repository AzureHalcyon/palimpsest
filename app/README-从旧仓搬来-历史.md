# companion-android —— 手机上那个壳

**一句话**：`companion/`（v0.1 业务实现，纯 Python）的 Android 宿主。
**业务逻辑一行都不重写** —— 构建时由 [Chaquopy](https://chaquo.com/chaquopy/) 把 `companion/`
这个包**原样嵌进 APK**，所以手机上跑的就是桌面上跑的那份。

> **当前状态（2026-10-05）**：**技术路线已验通，并且已经侧载到真机**。
> 小米 14（Android 16 / SDK 36 / arm64-v8a）上：APK 装得上、跑得起来，
> ★ **跨平台数值与桌面完全一致**（四个"必须一致"的摘要全对，只有允许不同的 `exact_digest` 有 ~8e-16 的差）。
> UI 设计系统（vendored 的 `uikit/`）已经接进工程并**构建通过**。
> **但它现在还只是个"自检屏"，不是能聊天攒数据的 App** —— 会话 / 池子 / 设置三个屏是下一步。
> 逐条见下面「已完成 / 未完成」。

---

## 已完成（每条都有实测证据）

| 事项 | 证据 |
|---|---|
| 工具链在 D 盘 | JDK 17.0.20.1 · SDK android-36 · build-tools 36.1.0 · platform-tools 37.0.1 · Gradle 8.9 · emulator 37.2.12 · android-36 x86_64 系统镜像 |
| APK 编得出来 | `build.ps1 :app:assembleDebug` → **70.8 MB**，**arm64-v8a + x86_64 双 ABI**（接进 UI 设计系统后从 63.6 MB 涨上来，主因是 `material-icons-extended`；正式包开 R8 minify + 只留 arm64 能瘦一大圈） |
| ★ **单一实现**（这条最关键） | 拆开 APK：`assets/chaquopy/app.imy` 里 **15 个 `.py`**，含 `companion/` 全部 14 个模块（`config.py` 13773 B 等）。它是**穿过 junction 抓进去的** —— 磁盘上只有一份源码，不存在"两份实现漂移" |
| 依赖 | `requirements-common.imy`：**numpy 1.26.2**（963 项）+ **PyYAML 6.0.3**，两个 ABI 各一份 |
| ★ **在 Android 上真跑起来** | 模拟器实测：`CPython 3.13.9 @ x86_64` · `numpy 1.26.2` · `companion 0.1.0 · 池子 3 条 · 检索到 1 条（截断前 1）` |
| ★ **侧载到真机并跑通** | `adb install` 成功 → 小米 14（`23127PN0CC` / Android 16 / SDK 36 / **arm64-v8a**）实测：`aarch64 · Python 3.13.9 · numpy 1.26.2`，探针与自检都能出结果 |
| ★ **跨平台数值一致（真机 ARM）** | **四个"必须一致"的摘要全对**：`discrete_digest` / `picked_digest` / `rounded_digest` / `hashing_digest` 均 `一致 ✓`；只有 `exact_digest` 不同 —— 那是**本来就允许**的一项，起因只是 BLAS 归约顺序的最后几个 ulp（同一条余弦 `0.9999999999999994` vs 桌面 `1.0000000000000002`，差 ~8e-16），**不改变任何离散结果**。<br>⇒ 直接回答了"手机与桌面会不会算出两套结果"：**不会。** |
| 路径语义 | `set_paths()` 之后 `凭证路径 = /data/user/0/com.azurehalcyon.palimpsest/files/credentials.yaml`（`config.cred_path()` 改成调用时解析后才对） |
| 归档可恢复（Python 侧） | `pending.json` + `applied_sids` 幂等 + `Engine.resume_pending()` + `Session.restore()`；v0.1 自检 **375 项全过** |
| ★ **后端端到端真跑通** | 2026-10-05 在**桥**上（不是绕过它）跑完整条链：`chat_open` → 两句 `chat_ask` → `chat_finish` → 池子 → 底片。<br>证据：开一场 `k=3 · long=14 · temp=2 · clock=2.44`；两句回复分别 **1.2 s / 0.9 s**；注入 `k=1` 命中底色「你累的时候我未必给建议」而回复正是「那就先不说话。我在这。」（**读取→注入→生成这条链真的在工作**）；每轮 `pending.json` 落盘（1 轮 / `msgs` 5 条）；归档 **7.0 s** → `dream_rounds=2 · kept_added=1`，备注写着「这一场只有他累着、不想被劝、只想有人陪着的状态留下」；归档后**临时池 2 → 3 条**，新增 `[他当时的状态]`，长期池 14 条不动；底片 `mobile_transcripts.jsonl` **一场一行** |
| ★ **底片仓取回通道** | `backup.ps1`：从应用私有目录取回 `cache/` + 凭证 + 设置，实测 **52 KB**；取回的 `pool.npz` 与手机原始文件 **SHA256 一致** |
| ★ **杀掉进程的两关都过了** | 脚本 `_ref/stability.ps1`（可重跑）。<br>**① 聊到一半杀**（`am force-stop`）：`pending.json` 活下来（7 轮 / `msgs` 23）→ 重开**接回同一场**、被杀前那句与回复都在屏幕上、且**还能继续发**（8→9 轮）。<br>**② 点「收场」后归档途中杀**（2 秒后 `force-stop`）：`pending.json` 在、**场次没 +1**、转写**没多行**（中间态正确）→ 重开**把归档补做完**：场次 2→3、临时池 2→6 条、转写**恰好一行**（12 轮）、`pending.json` 清掉，并提示「上一次收工时被打断，这次打开已经补完了」。<br>⇒ 「每轮落盘 + 场次可续 + 归档幂等」三条都拿到了实测证据。 |

| ★ **三个屏都能日用了** | 对话（气泡 + token 分别标注 + 归档进度）/ 记忆（分栏 + **懒加载** + 变化标注）/ 设置（凭证 · 模型 · 提示词可编辑 · 诊断）。前台服务保活：`am kill` 前后 pid 不变（`isForeground=true`）。 |
| ★ **对话可以退回去改**（2026-10-06，**真机已验**） | **长按自己的气泡** ⇒ **贴着手指**弹出的小卡片（**不是**屏幕底部的大弹窗）：**撤回并编辑** / **重新生成**。撤回 = 把最后一轮**整轮退回**（他那句回到输入框，改完自己发）—— 退回的是**四处**：上下文 `msgs` · 轮次记录 · 记账 · **注入冷却掩膜**（业务侧 `Session.withdraw_last`，为什么要四处一起退见 `../companion/CHANGELOG.md`）。<br>可**连续**往回撤；⛔ 只能从最近一句撤（撤中间那轮会在历史里剪一个洞）—— 点更早那些气泡时，卡片只给一行理由（「只能从最近的一句开始往回撤 —— 这一轮后面还有 N 轮」）。<br>★ **卡片位置是自己算的**（`PressAnchoredMenu`）：`Popup` 默认只能相对父节点摆，而长按点通常在气泡内部；定位器拿得到 `anchorBounds`（**气泡在窗口里的边界**）+ 手势给的**按下点** ⇒ 算出绝对坐标，再用 `popupContentSize` **夹回屏内**（用户气泡右对齐，最容易撞右墙；实测手指在 x=1047 时卡片左边缘落在 `1200−36−卡片宽`，正好贴住）。<br>★ 卡片宽度 = **最宽那一行**（`IntrinsicSize.Max`，Material `DropdownMenu` 的同一招），**再压一个 280dp 上限让长句换行** —— 否则那句拒绝文案会撑出屏幕被裁掉。<br>证据：离线 `companion/tools/selftest.py` **441 通过 · 0 失败**（含 `t33_withdraw_last`）；**小米 14（`14d824f2`）真机四个分支全通** —— ①「撤回并编辑」：整轮从界面消失、他那句回到输入框、提示「撤回来了 —— 改完再发。」；② 改内容重发后**只针对新内容**回话，且**又想起 20 条**（冷却掩膜确实被退掉，否则这里会接近 0）；③ 两轮时点第 1 轮 ⇒ 只给那一行理由；④「重新生成」：末轮换了一条新回复（输出 169→623），第 1 轮原封不动。<br>★ **手机上的 `pending.json` 是硬证据**：撤回后 `turns: []` · `push_n: []` · `push_comp` 全 0 · **`cd_mask: {}`** · `msgs` 只剩两条 system 前缀 · `withdraws` 留着被撤那轮的原文；同时 `state.json` 里 **`sessions: 0` · `temp: []`** ⇒ **池子一点没动**。 |
| ★★ **Markdown + LaTeX 公式真排版**（2026-10-06，**真机已验**） | 助手的回复按 Markdown 渲染（`#` 标题 / `**粗**` / `*斜*` / `` `码` `` / 代码块 / 列表 / 引用 / 分隔线 / 链接 / 裸 URL），**公式走真排版**：`$$…$$` 与 `\[…\]` 渲染成**居中的一整块**，行内 `$…$` 与 `\(…\)` 渲染进文字流。<br>★ **为什么不是"把 LaTeX 换成 Unicode"**：用户明确否掉了——「不是做成 unicode，**是真渲染**，要不然复杂公式可读性不好」。⇒ 引 `ru.noties:jlatexmath-android:0.2.0`（JLaTeXMath 的 Android 移植）。**坐标与 API 都是先确认再写的**：POM/AAR 从 Maven Central 拉下来、`javap` 读出 `JLatexMathDrawable.builder(tex).textSize(px).color(argb).align(…).padding(…).build()` 与 `intrinsicWidth/Height/draw(Canvas)`；字体资源随 aar 打进 APK（`assets/org/scilab/forge/jlatexmath/`）⇒ **离线可用**；aar 自带 `JLatexMathInitProvider` ⇒ 不用手工 init。许可：GPL-2 **带 Classpath Exception**（只是链接）。<br>★ 行内公式用 **`InlineTextContent`**（Compose 里把图片塞进文字流的唯一正规做法）；⚠️ 它的类名与包极易写错，实测两个都对不上：真名是 **`androidx.compose.foundation.text.InlineTextContent`**（小写 l、在 **foundation** 包）—— 最后是从 Gradle 缓存的 jar 里翻出来的，不是猜的。<br>⛔ **渲染失败绝不吞内容**：LaTeX 写错很常见 ⇒ 退回 `Latex.kt` 的**可读化文本**（`x^2`→`x²`、`\frac{a}{b}`→`a/b`、`\sqrt{x}`→`√x`，不认识的宏原样留）。<br>★ 位图按 `(公式, 字号, 颜色)` **记忆化**（`remember`）⇒ 重组不重渲。<br>证据：真机实测一条「用LaTeX写这四个公式…」⇒ 模型给出 `\frac{-b\pm\sqrt{b^2-4ac}}{2a}` / `e^{i\pi}+1=0` / `\begin{pmatrix}…\end{pmatrix}` / `\int_a^b f(x)\,dx`，**四个都渲染成真排版**（分数线、根号、真上标、矩阵括号、带上下限的积分号），截图 `.probe/m7.png`。<br>⚠️ 踩坑留痕：`am start --es prefill "有空格的一句话"` 经**设备侧 shell 再拆一次** ⇒ 句子在空格处被截断（那一轮模型还回了一句「第三个你写了一半——"一个"后面没了」，正好证明它读到了截断的输入）。 |
| ★★ **想起的条目可以就手改 / 删**（2026-10-06，**真机已验**） | 「想起 N 条」与「再查一次记忆」展开后的**每一条**都能**长按**：弹窗 = 改文本 / **删除这条**（二次确认）/ 取消 / **保存并重新生成**（只在该条属于**最近一轮**时给）。改的是**池子里的那一条**（底色 / 情境 / 记忆都允许）—— **就地换文本 + 重算那一行向量**，条目数不变。<br>★ 为什么必须"顺手重新生成"：改的是池子，而**这一轮已经发出去的上下文里还是旧文本** ⇒ 不重跑那一轮就看不到效果（所以弹窗里直接给这个按钮，复用已有的撤回路径）。<br>★ **删除也允许碰底色/情境**（与归档那条路不同：归档不许模型碰手写层，而这一层本来就是用户自己写的）。分层是**按下标区间**算的 ⇒ 删一条会让后面整体前移一格，实现是**先把每条的层定下来、再逐条删并逐条减对应计数**（`LongPool.drop_any`）—— 离线自检里有专门一条断言"删完归属不变"。<br>★ **账本** `cache/pool_edits.jsonl`（`{ts, action, where, layer, old, new}`）：桌面下次推池子会**整个覆盖**池子，手改只活在手机里 ⇒ 这份账本是唯一凭据，`backup.ps1` 会取回，之后在电脑上整理。<br>★ **改过的条目带「改」记号**（`Engine.edited_texts`，只标现在还在池子里的）—— 否则分不清原稿与自己临时改的。<br>证据：离线 **473 通过 · 0 失败**（新增 `t35_user_edit_and_delete` 17 项，含 `★★ 分层不移位`）；**小米 14 真机逐条截图**：展开 → 长按第 1 条 → 弹窗（**默认展开、已预填、光标在末尾**）→ 保存并重新生成 ⇒ 那一轮真被撤回重跑；账本写出 `{"action":"edit","where":"长期池","layer":"底色"}`；条目右边出现**「改」**；删除走到二次确认后账本**没有**多记录（没误删）。⚠️ 真机抓到两个只有上手才看得见的错：UI 文案里的 markdown `**` 会被原样显示；uikit 的 `SettingExpandableTextItem` 默认折叠、要多点一下才改（已改 `initiallyExpanded`）。 |
| ★ **记忆屏的「空间」** | 3D 分布图（Compose `Canvas` 手写正交投影 + 拖动旋转 + 点选）+ 语义/时间两视图。原点 = 底色，半径 = 余弦距离（与 `Reader` 同一把尺子）。<br>真机实测：点选「事实 #11」给出 `离它自己 1.024`，与离线按同一份 `pool.npz` 算出的 `r_true=1.024` 一致；旋转、复位视角、切视图均通过。<br>★ **图上不画等距离圈** —— 实测投影把内层压到 **44%**、外层只压到 84%，圈（真半径）和点（投影半径）会混两把尺子；精确半径改由图例的数字和「时间」视图给（那张没有投影）。详见 `../04_实验记录/第三十四轮-空间图-报告.md` |

## 未完成（= 下一步，按顺序）

1. ✅ **建真的底色 / 情境 / 记忆池 + 装机 + 推池（2026-10-05 全部完成）** ——
   桌面真池 **252 条**（底色 239 + 情境 13 + 记忆 0，1024 维，真编码器）；
   `assembleDebug` → `adb install -r` → `push_pool.ps1` 四文件 SHA-256 一致。
   端到端截图实测：**长期 252 · 事实 0 · 情境 13 · 自我 239**，层显示名 =「情境」。
   ⚠️ **构建要提权**：Chaquopy 的低完整性 pip 产物在沙箱下删不掉
   （`Unable to delete directory … Access is denied`）⇒ 先 `gradlew --stop` → 清 `app/build/python/pip` → 再构建。
   回滚：旧池 **243 条**留在 `companion-data/.cache-243/`。
2. **正式包瘦身**：现在 70.8 MB、arm64-v8a + x86_64 双 ABI。只留 arm64 + 开 R8
   （`material-icons-extended` 是主因）能瘦一大圈。
3. **底片仓取回常态化**：`backup.ps1` 已经有了（实测 SHA256 一致），要**定期跑** ——
   数据不能只活在手机里。
4. **流式输出（SSE）** —— ⚠️ **优先级已下调**。原先按"一次生成静默 8–9 秒"估的，
   但 2026-10-05 端到端实测（`deepseek-flash` / `effort=low`）：
   **一次对话往返 1.2 秒**、**一次归档 7.0 秒**。这个量级下"看起来卡死"的担心不成立。
   **但要注意这两个数与模型/档位强相关**，换模型要重新量。

## UI 设计系统（`uikit/`，vendored 自 `niki914/zafiro`）

**为什么抄**：从零画一套好看的 Compose 界面是纯支出。`niki914/zafiro`（MIT，218★）**和我们同一套技术栈**
（Chaquopy 17 嵌 Python，`agent-runtime/src/main/python/`），而它把 UI 单独做成了一个
**零业务耦合**的模块 `ui-kit/`：`LiquidScreen` 外壳、`LiquidTextField` / `LiquidSecretTextField`
（密钥框）/ `OptionSheet` / `LiquidDialog`、**规格驱动的设置页**（`SettingsPageSpec` + `SettingsRowSpec`
+ `SettingsSpecPageContent`）、轻量导航（`Navigator` + `Page`，不依赖 Navigation Compose）。
玻璃质感来自 `Capsule`（G2 连续曲率形状）+ `backdrop`（模糊 / 透镜 / 高光）。

**只改了三处，源码逐字未改**（详见 [`uikit/NOTICE.md`](uikit/NOTICE.md)）：

1. 删掉 `base/ComposeMVIViewModel.kt` —— 全模块**唯一**依赖上游自家 `:libs:logging` 的文件，且无人引用；
2. 构建插件改走本工程的版本目录（上游写死 AGP 9.1.1 / Kotlin 2.2.0）；
3. `material3` 不写版本，交给 Compose BOM。

**★ 关键实测：不必升工具链。** 上游钉 `material3 1.5.0-alpha22`，而那一版的 AAR 硬要求
`minCompileSdk=37` + `minAndroidGradlePluginVersion=9.1.0`。但逐个数过上游真正用到的
**19 个 material3 符号**（`MaterialTheme` / `Text` / `Surface` / `ModalBottomSheet` /
`SegmentedButton` / `dynamicLightColorScheme` …）—— **它们在 `1.4.0` 里全部存在**。
alpha22 只是上游顺手跟最新，不是功能需要。⇒ 于是 AGP / Gradle / Kotlin / compileSdk **一个都不用动**。

依赖门槛（2026-10-05 读各 AAR 里的 `META-INF/.../aar-metadata.properties` 得到，不是猜的）：

| 依赖 | `minCompileSdk` | `minAGP` | 仓库 |
|---|---|---|---|
| `backdrop:2.0.0-alpha03` | **36**（正好是我们的） | 1.0.0 | Maven Central |
| `Capsule:2.1.0` | 1 | 1.0.0 | **JitPack**（`settings.gradle.kts` 里必须有 jitpack） |
| `material-kolor:2.1.1` | 1 | 1.0.0 | Maven Central |
| `material-icons-extended`（BOM 1.7.8） | 34 | 1.0.0 | Google Maven |

**两条"靠元数据推断会判错"的实测结论**（值得记住）：

- `backdrop` 的 `@kotlin.Metadata(mv=[2,3,0])` 比我们的 Kotlin **2.2.21 高一个小版本**，
  但**能读** —— 用 `kotlin-compiler-embeddable-2.2.21` 单独编一次，`exit=0`；
- `Capsule` 的 class 文件是 **Java 21 字节码**（major 65），Kotlin 编译与
  `d8`（build-tools 36.1.0）**都能吃**（实测产出 `classes.dex`）。
  ⇒ **依赖能不能用，不能靠读元数据推断，得真的编一次。**

**踩到的坑**：`ui-kit` 用到 `Visibility` / `VisibilityOff`（密钥框的眼睛按钮）与
`Description` / `Folder` / `Image` / `PhotoCamera`（附件预览），这些**只在 `material-icons-extended` 里**，
不在 `material-icons-core`。第一次编译就是栽在这上面。

---

## 目录结构

```
companion-ai/
├── companion/           ← v0.1 业务实现（Python，自带 git 仓库）
└── companion-android/   ← 本工程（自带 git 仓库，外层 .gitignore 排除）
    ├── build.ps1                     构建入口（把 TEMP 与 Gradle 缓存都指到 D 盘）
    ├── gradle/libs.versions.toml      版本集中在这里（都有依据，见下）
    └── app/src/main/
        ├── python/
        │   ├── host_glue.py          宿主胶水（自检 / 路径 / 状态 / 在途场次；**不是业务逻辑**）
        │   └── companion/            ← **目录 junction**，指向 ../../../companion/companion
        ├── java/com/azurehalcyon/companion/
        │   ├── MainActivity.kt       界面（当前只有自检屏）
        │   └── PyBridge.kt           Kotlin ↔ Python 的**唯一**通道
        └── res/values/themes.xml
```

`app/src/main/python/companion` 是**目录 junction**（不是拷贝）：磁盘上只有一份 Python 源码，
改 `companion/` 立刻反映到手机上。

**桥的规矩（别让它长胖）**：只传字符串 / 布尔 / 字符串列表。
⚠️ 踩过：`PyObject.asMap()` 给的是 `Map<PyObject, PyObject>`，Kotlin 侧按键取值会
`Type inference failed` ⇒ 在 Python 那边就拼成字符串。

## 工具链（全在 D 盘，不占 C 盘）

| 组件 | 版本 | 位置 |
|---|---|---|
| JDK | Temurin 17.0.20.1 | `D:\Android\jdk-17` |
| Android SDK | android-36 + build-tools 36.1.0 | `D:\Android\Sdk` |
| platform-tools (adb) | 37.0.1 | `D:\Android\Sdk\platform-tools\adb.exe` |
| Gradle | 8.9（工程用 wrapper 8.13，腾讯云镜像） | `D:\Android\gradle-8.9` / 缓存在工作区 `.android-build\gradle-home` |
| emulator | 37.2.12（**WHPX 可用**） | `D:\Android\Sdk\emulator\emulator.exe` |
| 系统镜像 | android-36 google_apis x86_64（4.27 GB） | `D:\Android\Sdk\system-images\...` |
| 环境脚本 | — | `D:\Android\env.ps1` |

模拟器 AVD：**`companion36`**（存在 `D:\Android\user-home\avd`）。

## 构建与验证（命令照抄就能跑）

```powershell
# 1) 构建（脚本已设好 JAVA_HOME/ANDROID_HOME，并把 TEMP 指到 D:\Android\tmp）
cd companion-ai\companion-android
pwsh -File build.ps1 :app:assembleDebug
#   产物：app\build\outputs\apk\debug\app-debug.apk

# 2) 起模拟器（无窗口；已在跑就别重复起）
& 'D:\Android\Sdk\emulator\emulator.exe' -avd companion36 -no-window -no-audio `
    -no-boot-anim -gpu swiftshader_indirect

# 3) 装 + 启动
$adb = 'D:\Android\Sdk\platform-tools\adb.exe'
& $adb install -r app\build\outputs\apk\debug\app-debug.apk
& $adb shell am start -n com.azurehalcyon.palimpsest/.MainActivity
```

**自动点按钮读结果**：工作区根的 `_emu_check.ps1` / `_emu_check2.ps1`。
做法是 `uiautomator dump` 出 XML，**按按钮文字找 `bounds` 中心**再 `input tap`
（比猜像素稳）。改界面后文字变了要同步改脚本里的按钮名。

**装到真机**：拷 APK 过去点安装（要允许"安装未知应用"），或
`adb install -r <apk>`（手机开 USB 调试）。

## 底片仓取回通道（`backup.ps1`）—— 数据不能只活在手机里

**为什么必须有**：原则是「手机是唯一入口，桌面是实验台 + 底片仓」。
这条原则有个直接后果：**攒下来的数据只存在于手机里**。手机丢了 / 刷了 / 坏了，全没。
所以"能把数据整包取回来"不是一个便利功能，是**这套设计能不能长期成立的前提**。

```
pwsh -File backup.ps1                                  # 默认存到 dsh-workplace\companion-backups\<时间戳>\
pwsh -File backup.ps1 -OutDir 'E:\companion-底片仓'    # 也可以指到别处
```

它只取**数据**：`files/cache/`（池子 / 状态 / 底片转写 / 在途场次）+ `credentials.yaml` + `settings.yaml`。
实测 **52 KB**。

> ⚠️ **不要连 `files/chaquopy/` 一起打包。** 那是 Chaquopy 每次安装重新解出来的 Python 运行时
> （stdlib + numpy + 一堆 `.so`），实测一起打是 **29.5 MB** 而我们真正要保的只有 52 KB。
> 备它既掩盖真正的变化，又让人懒得备。

实现上有三个已经踩过的点（都实测过）：

1. **`run-as` 只能读 debuggable 的包** —— 自用侧载就是 debug 包，正好；
2. ★ **`cmd /c "... > 文件"` 而不是 PowerShell 的 `>`** ——
   PowerShell 会把二进制当字符串处理、**损坏文件**。实测走 cmd 这条，取回的 `pool.npz`
   与手机上原始文件 **SHA256 一致**（49,593 字节）；
3. **不要 `adb pull /data/local/tmp/xxx`** —— 那样 tar 包归应用 uid 所有，`adb shell` 反而读不了它。

脚本还会报两件该留意的事：**底片转写有几行**，以及**有没有在途的场次**（`pending.json` 存在
= 上一场没归档完，下次启动 App 会补做）。

---

## 版本为什么是这几个（2026-10-04 实查，不是抄来的）

- `compileSdk = 36` ⇒ AGP 需 ≥ 8.9.1（AGP 8.7.3 实测**也能配过**，只是官方未测到 36）；
- **Chaquopy 17.0.0 官方支持 AGP 7.3.x–9.2.x** ⇒ 不上 AGP 9（也顺带避开 Gradle 9）；
- AGP 8.13.x ⇒ 要求 Gradle 8.13，故 wrapper 用 8.13（`distributionUrl` 指腾讯云镜像）；
- Kotlin / Compose BOM / AndroidX 取**同期**版本，避免"新库 + 旧编译器"的元数据不兼容；
- Chaquopy 硬要求：**构建机 Python 与 app 的 Python 同主次版本** ⇒ app 用 3.13，
  构建机就是 `E:\Python3.13\python.exe`（3.13.0）；
- Chaquopy 走 **Python 3.13** 的另一个理由：Android 15+ 的 **16 KB 页内核**机型要求
  Python 3.13+ 与最新 wheel，否则 `.so` 加载崩溃；
- `x86_64` 只是为了**本机模拟器验证**；出正式自用包可以去掉，能省十几 MB。

## 为什么没有 fork RikkaHub（想抄 UI 壳的那条路）

实读过它的构建配置（commit `85af5b9`，2026-10-04）：**Gradle 9.6.0 + AGP 9.4.0 +
Kotlin 2.4.20**，**717 个 `.kt` / 126,051 行**，12 个 Gradle 模块 + `build-logic`
convention 插件，还有一个需要 `pnpm run build` 的 `web` 模块和一个 git submodule。

1. **它装不下 Chaquopy**：AGP 9.4.0 超出 Chaquopy 的 9.2 上限，要嵌 Python 就得先降 AGP，
   还要改掉两处 AGP 9 专有 DSL。
2. **从 126,051 行里剥出一个单屏对话，比自己写那个屏更贵。** 只借它的**设计**：
   "每轮请求前注入"与"生成结束后回调"分两段钩子 —— 这个分层是对的，本工程照此设计
   `ask()` 与 `finish()`。

（另注：RikkaHub 是 **AGPL-3.0**，且 README 明写不接受 PR。自用无碍，但别把它的代码
抄进一个将来可能开源的工程。）

## 踩过的坑（每条都实测过；完整版在 `~/.dsh/工具详情.md#android-sdk`）

1. **`sdkmanager` 在 cmdline-tools 23.0 已废弃** ⇒ 用 `android.exe sdk install <包名>`，
   包名用**斜杠**（`platforms/android-36`）；老的分号写法会被拆成两个包报 not found。
2. **`android.exe` / `adb` / `emulator` 每次调用都要提权**（写 `D:\Android\user-home`）。
3. **Chaquopy 的 pip 主索引必须是 `https://chaquo.com/pypi-13.1`**：国内镜像**没有**
   `android_24_arm64_v8a` 轮子，设成国内镜像会直接 `No matching distribution found`。
4. **`TEMP`/`TMP` 不能放在工作区里**：工作区目录带一条继承的 `Everyone:(I)(CI)(DENY)(DC)`
   （沙箱 ACL 运行器留下的），pip 下载完轮子要重命名/清理时 `[Errno 13]`，
   还会留下删不掉的 `pip-*` 残骸 ⇒ 构建脚本把 TEMP 指到 `D:\Android\tmp`。
5. **忘了写 `dependencies { }`**：版本目录里定义了库却没引用 ⇒ Compose 全线
   `Unresolved reference`（这类错误看着像环境问题，其实是构建脚本漏了）。
6. **`PyObject.asMap()` 类型推不出来** ⇒ 桥只传字符串/字符串列表。
7. **Gradle 官方分发超时**（`services.gradle.org` / `downloads.gradle.org`）⇒ 用腾讯云镜像。
8. **清华 Adoptium 镜像对简陋请求头返回 403**（HEAD 却是 200）⇒ 下载要带完整浏览器头。
9. **`android sdk install` 的下载不可续传**，且它**边下边往目标目录写**：
   1.8 GB 系统镜像实测在 95% 处断过一次；⚠️ **别用"目录里没有文件在变大"判断它卡住**
   —— 我曾据此杀掉一个正在正常下载的任务（13 分钟白费）。

## 已知限制

- **双 ABI**（arm64-v8a 给手机、x86_64 给模拟器）；出正式自用包时可只留 arm64-v8a。
- **debug 签名**：自用侧载够用；要给别人装再换 keystore。
- `local.properties`（`sdk.dir`）是机器相关的，已被 `.gitignore` 排除。
- 自用**不进商店**，所以 `targetSdk` 不必跟 Play 政策走。
- ⚠️ **x86_64 模拟器测不了 ARM（arm64）的数值与性能行为** —— 那只能靠真机。
  跨平台探针在真机上点一下就能给出结论（当前 APK 里已经有那个按钮）。
