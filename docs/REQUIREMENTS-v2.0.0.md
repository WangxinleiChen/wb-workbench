# WB Workbench v2.0.0-dev · 开发需求规格

面向执行者（Codex）的实现规格。**本文件是需求，不是已完成的描述。** 未经作者确认，不得把本文件中的任何计划当作已实现事实写进 README、DEVELOPMENT.md 或导出文件。

- 起点：`development/background-v1.1.0`（worktree `wb-workbench-dev`，提交 `36d343a`），回归基线 **61 项自动测试全过**。
- 标准版 `standard-v1.0.0`（worktree `wb-workbench`，提交 `0ee2919`，27 项全过）**不得改动**，它是唯一回退点。
- 验收通过后，v2.0.0-dev 才可能被提升为 Standard v2.0.0；提升是作者的决定，不是本次实现的一部分。

---

## 0. 已裁定的三项决定

作者已于 2026-09-20 裁定如下。**这三条是既定前提，不要再回头讨论或自行更改。**

| 编号 | 决定 | 裁定结果 | 落实位置 |
| --- | --- | --- | --- |
| D1 | 新建实验的默认定量模式 | **模式 C（全图共享背景框）** | §4.0 |
| D2 | 「全部复核确认」按钮 | **保留，但降级为次要按钮**，逐项引导成为主路径 | §3.4 |
| D3 | 双语是否覆盖导出文件 | **覆盖**，机器可读键永远固定英文 | §5.3 第三步 |

D1 有一个必须理解正确的实现细节，见 §4.0；照字面把 `mode` 初始值设成 `shared` 是错的。

---

## 1. 工作环境与隔离

### 1.1 位置

新建分支与 worktree，**不要在 `wb-workbench-dev` 里直接改**，v1.1.0-dev 要保持可运行以便对照：

```sh
git -C <repo> branch development/v2.0.0 development/background-v1.1.0
git -C <repo> worktree add ../wb-workbench-dev2 development/v2.0.0
```

目标目录：`outputs/wb-workbench-dev2/`。

### 1.2 端口与数据

- 8765 = 标准版，8766 = dev v1.1.0，**8767 = dev v2.0.0**。
- 沿用 v1.1.0 已有的硬约束并扩展：`server.py` 启动时拒绝 8765 与 8766；拒绝 `--data-dir` 落到本目录之外。
- 独立 `.venv`（按 `ENVIRONMENT.json` 记录的基础 Python 重建），独立 `data/`、`logs/`、`exports/`。
- **禁止**把 v2.0 的 `data/` 复制回标准版或 v1.1.0；v2.0 新增字段两者都不认。

### 1.3 依赖

**不得新增任何第三方依赖。** 仍然只用 Python 标准库 + NumPy + Pillow，前端仍是无构建步骤的原生 JS（没有 Node.js、没有打包器、没有 CDN、没有外部字体）。openpyxl 仅限测试。

---

## 2. 不可破坏的既有约束（硬性）

违反以下任意一条即视为本次实现失败：

1. `analysis.py` 现有的 `measure_roi` 对外行为**逐位不变**。模式 A 的任何一个数值都不允许变。
2. 不删除模式 A（`local`）和模式 B（`model`）。`state.json` 里已经存了 `mode` 字段，删模式会让已有实验读不出来。
3. 读取旧数据不做自动迁移：`exp` 没有 `background` 字段 → 模式 A；没有新字段 → 用默认值，不回写。
4. 「未确认不发布比值」的语义不变。任何新功能都不得绕过人工确认。
5. 不截断非正净值，不做任何隐式裁零。现有浮点容差语义（`numericalTolerance`）原样沿用。
6. 不新增任何生物学结论、显著性检验或「已验证/通过/正确」类措辞。新加的阈值一律是**复核提示阈值**，必须在界面和文档里明说不是生物学阈值。
7. 原图永不覆盖、永不改写。所有坐标仍是原图像素。
8. 仅监听 `127.0.0.1`，保留现有的 Host/Origin/Content-Type 校验。
9. 61 项既有测试必须保持全过。若某项因为新功能必须调整，**先在 PR 说明里逐条列出原因并等作者确认**，不得自行改测试来迁就实现。

---

## 3. WP1 · 确认引导（优先级最高，先做）

### 3.1 目标

现在需要确认的地方太多，用户不知道先做哪个。给出一个确定性的「下一步」提示。

### 3.2 性质

**纯派生功能。** `Store.results()` 已经产出完整状态机（`等待选区` / `选区或信号需复核` / `待人工确认` / `净信号非正，需复核` / `已确认` / `已确认 · 对照待复核` / `已确认 · 无对照`），加上实验级 `controlReady` 和 `warnings`。WP1 只从这些已有信息推导顺序，**不得新增任何测量、不得改动确认语义、不得改动任何数值**。

### 3.3 优先级阶梯

在 `results()` 返回值中新增 `nextAction` 与 `checklist`。按下列顺序取**第一个未满足项**：

| 级 | 条件 | code | 指向 |
| --- | --- | --- | --- |
| 1 | `exp["images"][role]` 为空 | `next.image_missing` | role |
| 2 | `exp["settings"][role]["region"]` 为空，或该 role 无任何 ROI | `next.region_missing` | role |
| 3 | 该 role 的 `modeChosenBy == "default"` 且尚未应用共享背景框（D1 的直接后果，见 §4.0） | `next.shared_background_unset` | role |
| 4 | 存在 `measured["valid"] == False` 的样本 | `next.measurement_invalid` | role + sampleId |
| 5 | 模式 C 下均匀性判定为 `unsupported` | `next.background_unsupported` | role |
| 6 | 没有任何样本 `control == True` | `next.control_missing` | null |
| 7 | 存在未确认样本 | `next.sample_unconfirmed` | role + sampleId（按泳道 x 坐标升序取第一个） |
| 8 | 指定对照存在但 `controlReady == False` | `next.control_not_ready` | sampleId |
| 9 | 全部满足 | `next.ready_to_export` | null |

第 3 级只在用户从未显式选过模式时出现。用户一旦显式应用或恢复过任一模式（`modeChosenBy` 变为 `"user"`），第 3 级对该 role 永久跳过，即使他选的是模式 A。**不得强迫用户使用模式 C。**

同级内的次序：先 `pho` 后 `total`；同一 role 内按条带框 `x` 升序。**必须是确定性的**，相同状态必须永远给出相同的下一步。

返回结构：

```json
"nextAction": {
  "code": "next.measurement_invalid",
  "role": "pho",
  "sampleId": "s_xxx",
  "detailCodes": ["roi.background_covers_other_band"],
  "remainingConfirm": 4,
  "totalSamples": 6
},
"checklist": [
  {"code": "next.image_missing", "state": "done"},
  {"code": "next.measurement_invalid", "state": "blocked", "count": 2},
  {"code": "next.sample_unconfirmed", "state": "pending", "count": 4}
]
```

`state` 取值：`done` / `pending` / `blocked` / `skipped`（例如实验本就无对照时第 5、7 级为 `skipped`）。

### 3.4 界面

- 结果表上方一行：「下一步：修正 Ctr2 的 PHO 背景框（背景框压到 Trt1 条带）」，点击直接选中该样本并展开对应 ROI 编辑器。
- 现有 `count/exp.samples.length` 升级为进度条 + `checklist` 悬浮明细。
- **措辞边界**：提示只排顺序，不得暗示软件已替用户判断对错。禁止出现「已验证」「通过」「正确」「无误」。用「待你复核」「需修正」「顺序建议」这类词。
- **「全部复核确认」按钮（D2 落实）**：保留功能，但降级为次要样式（`button small plain`，移出主按钮位），放在引导行的右侧末尾而不是表头主位；点击后的确认对话框保持现有的逐条提示文案不变。引导行给出的单项「去确认」按钮成为主按钮。目的是让逐项复核成为默认路径，同时不剥夺已经核对完的用户一次性确认的能力。

### 3.5 测试（新增 `tests/test_next_action.py`，≥ 9 项）

每一级各一项；再加「同级内排序确定性」「无对照实验跳过第 6/8 级」「用户显式选过模式后第 3 级永久跳过」「全部满足时为 `ready_to_export`」。

---

## 4. WP2 · 共享背景框模式 C（核心）

### 4.0 默认模式（D1 落实）

作者裁定：新建实验的默认定量模式为 **C**。

**照字面把 `exp["background"][role]["mode"]` 初始值设成 `"shared"` 是错的。** 新建实验此时既没有图像、也没有分析区域，更没有背景框和代表值 `B`；若 `mode` 一开始就是 `shared`，所有样本的测量都会立刻变成 invalid，整个实验无法进行。

正确的落实方式是「**默认引导到 C**」，而不是「默认值是 C」：

1. `exp["background"][role]` 的 `mode` 初始值仍为 `"local"`，并新增字段 `"modeChosenBy": "default"`。
2. 用户在该图上显式执行过任意 `apply` 或 `restore` 后，`modeChosenBy` 置为 `"user"`，此后不再引导。
3. 当某图已有分析区域和候选 ROI、且 `modeChosenBy == "default"` 时：
   - WP1 的阶梯第 3 级把「设置共享背景框」排为下一步；
   - 界面自动给出建议矩形（§4.6）和预览（§4.5 的均匀性判定），但**不自动应用**；
   - 应用仍需用户显式点击，且应用对话框必须先显示均匀性判定。
4. 用户可以在任何时候改用模式 A 或 B，引导不得阻拦，也不得反复提示。
5. 读取旧数据时，没有 `modeChosenBy` 字段的实验一律视为 `"user"`，**不要对已有实验发起引导**。

一句话：默认路径是 C，默认值不是 C。任何情况下都不得在用户未确认的前提下改变实际参与计算的背景方法。

### 4.1 需求来源与结论

作者提出：用一个矩形框选背景，把该值用于所有条带。

**结论：方向正确，可以实现，但必须按 4.2 的公式、4.4 的校验和 4.5 的均匀性检验来做。**

三点认定，写在这里是为了让实现者理解边界：

1. 模式 C 在数学上是模式 B（`robust-quadratic-v1`）的**零阶特例**（一次项、二次项全为 0，只保留常数项）。因此不需要新算法框架，但也因此**不要去复用 B 的派生数组流水线**——C 的结果是一个标量，不需要 `data/derived/` 下的 NPY/PNG。
2. 模式 C 在作者自己的截图数据上**可能比模式 B 更安全**：`DEVELOPMENT.md` 已记录 WBTEST 的 total 图默认参数保留了 100% 采样，存在真实条带被吸进背景曲面的风险；一个人工指定、明确避开条带的矩形没有这个问题，而且一个标量可以手算复核。
3. 模式 C 的有效性**完全依赖「背景在条带所在区域近似均匀」这一假设**。膜有梯度、照度不均、边缘效应、污点，或框到截图黑边时，一个常数会系统性地抬高一侧、压低另一侧。所以 4.5 的均匀性检验是本 WP 的**必做项，不是可选项**。

### 4.2 计算定义（`shared-roi-v1`）

设共享背景代表值为 `B`（每像素，原始编码单位），对每个条带 ROI：

```
area   = band.w * band.h
rawSum = Σ I over band
bgContribution = area * B
net = bgContribution - rawSum      # dark
net = rawSum - bgContribution      # bright
```

**关键约束：减的是「每像素代表值 × 条带面积」，不是「背景框内像素总和」。** 背景框面积与条带框面积不相等时，后者量纲错误。这是本需求最容易实现错的一点，4.9 有专门的回归测试。

`B` 的估计量：

- `median`（默认）：`float(np.median(bgPixels))`，对尘点、划痕稳健。
- `mean`：`float(bgPixels.mean())`，与模式 A 的 `backgroundMean` 同定义，便于与 A 对照。
- 两个值**都要计算并记录**，只有被选中的那个参与定量。

数值容差沿用 `measure_roi` 的现有公式，把 `bg.size` 换成共享背景框像素数。端点像素计数、极性校验、非正净值警告全部沿用现有语义。

### 4.3 实现方式（防回归）

不要复制粘贴 `measure_roi`。做法：

1. 在 `analysis.py` 中把 `measure_roi` 的核心抽成私有 `_measure_core(pixels, bg_value, bg_pixel_count, polarity, metadata, warnings)`。
2. `measure_roi` 改为读完两个框后调用它，**对外返回字段与数值完全不变**。
3. 新增 `measure_shared(path, band_rect, shared_info, polarity)` 调用同一核心。
4. 新增一项测试：对固定测试图，重构前后 `measure_roi` 的全部返回字段逐位相等（把重构前的期望值硬编码进测试）。

### 4.4 数据模型

`exp["background"][role]` 扩展为：

```json
{
  "mode": "local" | "model" | "shared",
  "modeChosenBy": "default" | "user",
  "preview": <modelArtifact|null>,
  "applied": <modelArtifact|null>,
  "sharedPreview": <sharedArtifact|null>,
  "sharedApplied": <sharedArtifact|null>
}
```

`sharedArtifact`：

```json
{
  "algorithm": "shared-roi-v1",
  "algorithmVersion": 1,
  "key": "<sha256(sourceSha256|algorithm|version|rect|estimator|polarity)>",
  "sourceSha256": "<原图 SHA-256>",
  "rect": {"x": 0, "y": 0, "w": 0, "h": 0},
  "region": {"x": 0, "y": 0, "w": 0, "h": 0},
  "polarity": "dark",
  "estimator": "median",
  "value": 0.0,
  "median": 0.0,
  "mean": 0.0,
  "sd": 0.0,
  "pixelCount": 0,
  "endpointCount": 0,
  "origin": "manual" | "suggested",
  "uniformity": { "...": "见 4.5" },
  "computedAt": "<ISO8601>"
}
```

失效判定沿用 `artifact_matches` 的思路：`key`、`sourceSha256`、`region`、`polarity` 任一与当前实验状态不符即失效。

**共享背景框不进入 `roi["background"]`。** 每个 ROI 原有的 `background` 字段在模式 C 下保留但不参与计算，只供「恢复原方法」用（与模式 B 的既有规则 4 一致）。

### 4.5 均匀性检验（必做）

在 `shared-preview` 时计算，结果存进 `sharedArtifact["uniformity"]`。

**探针（probe）的取法**（确定性，无随机）：

1. 取该图分析区域 `region`。
2. 取该图全部条带框，按 `x` 排序，得到相邻条带之间的间隙。
3. 每个间隙生成一个候选条带：`x` 方向两侧各收缩 `max(2, 0.1 * gapWidth)` 像素，`y` 方向取 `region` 的完整高度。
4. 若 `region` 高于条带行，再在条带行上方、下方各生成一个候选条带（同样收缩 2 px）。
5. 丢弃面积 < 200 px 或与任一条带框重叠的候选。

**指标**：

- `probeMedians[i] = np.median(probe_i)`
- `spreadSd = np.std(probeMedians, ddof=1)`（需 ≥ 2 个探针）
- `gradientX = np.polyfit(probeCenterX, probeMedians, 1)[0]`，单位「原始编码单位 / 像素」；`gradientY` 同理（探针数足够时）
- `maxAbsDeviation = max(|probeMedians - value|)`

**影响度与判定**：

对每个样本计算 `sensitivity_i = spreadSd * band_area_i / |net_i|`，取最大值 `maxSensitivity`。

| `maxSensitivity` | verdict | 含义 |
| --- | --- | --- |
| < 0.05 | `supported` | 本图的背景起伏相对最弱条带信号很小 |
| 0.05 – 0.20 | `check` | 需人工判断是否接受常数背景 |
| ≥ 0.20 | `unsupported` | 本图不支持常数背景假设 |

边界情况：探针 < 3 个、任一 `net` 不可用或非正 → `verdict = "check"`，附 code 说明原因，**不得静默给出 `supported`**。

阈值 0.05 / 0.20 与探针最小面积 200 px 必须集中定义在一处常量（例如 `background.UNIFORMITY_THRESHOLDS`），并在界面与文档中明确标注：**这是复核提示阈值，不是生物学阈值，也不代表定量有效性已被验证。**

`verdict == "unsupported"` 时：**不阻止**用户应用模式 C（作者仍可能有理由这么做），但必须在应用对话框中显著提示，并在 WP1 的阶梯第 4 级把它排成下一步，同时写进导出。

### 4.6 自动建议背景框

作者要求「自选框或你自动建议」。自动建议必须是确定性的，不使用任何学习模型：

1. 用 4.5 的探针候选集合。
2. 按下列顺序打分选一个：① 不与任何条带框重叠（硬条件）；② 框内 SD 最小；③ 中位数最接近全部候选中位数的中位数（排除污点、黑边）；④ 面积更大者优先。
3. 返回所选矩形 + 选择依据（code + 具体数值）。

**自动建议只产生草稿**，与现有 `suggest` 一致，必须经过预览和明确应用，不得直接生效。

### 4.7 接口

沿用现有端点 `POST /api/experiments/<id>/background/<role>`，请求体新增 `method` 字段：

```json
{ "action": "preview" | "apply" | "restore" | "suggest", "method": "model" | "shared", ... }
```

- `method` 缺省为 `"model"`，保证 v1.1.0 的既有行为与 10 项工作流测试不受影响。
- `method: "shared"` + `action: "suggest"` → 返回建议矩形草稿，不改正式模式。
- `method: "shared"` + `action: "preview"` → 给定 `rect` 与 `estimator`，计算 `sharedArtifact`（含均匀性），存入 `sharedPreview`，**不改 `mode`、不撤销确认**。
- `method: "shared"` + `action: "apply"` → 需带 `key` 与 `sharedPreview` 匹配；设 `mode = "shared"`，`sharedApplied = sharedPreview`；**撤销该图全部样本确认**。
- `action: "restore"` → 回到 `mode = "local"`，清空 `sharedApplied`；从非 `local` 回到 `local` 时撤销该图全部确认（沿用现有行为）。

### 4.8 失效与撤销规则

| 操作 | 后果 |
| --- | --- |
| 修改共享背景框或估计量并应用 | 该图**全部**样本确认撤销（因为它影响每一条带） |
| 仅预览草稿 | 不改正式模式，不撤销确认 |
| 修改分析区域 / 极性 / 替换原图 | `sharedPreview` 与 `sharedApplied` 全部失效；**不自动回退到 A**，界面提示需重新预览应用或显式恢复 |
| 修改单个条带框 | 只撤销该样本确认（现有行为；模式 C 下 `roi["background"]` 的变化不再触发撤销） |
| 仅缩放/切换显示 | 不撤销确认 |

`server.py` 中 `changed` 的判断（现为 `mode == "local"` 时才比较 `old["background"]`）保持不变即可满足上表，但要补一项测试锁死这个行为。

### 4.9 导出

- `row[role + "Method"]` 取值扩展为 `local` / `model` / `shared`。
- 新增列（XLSX「测量明细」与 CSV 同步）：背景算法、背景代表值、估计量、背景框 xywh、背景像素数、背景 SD、均匀性判定、maxSensitivity、探针数。
- **模式 C 的 XLSX 净信号必须写成可在 Excel 里重算的公式**：`= 背景代表值 * 条带像素数 - 条带像素和`（暗条带；亮条带反号）。这是模式 C 相对模式 B 的一个实际优势（B 的背景贡献只能给缓存值），要在「方法与来源」页写明。
- 新增敏感度列（见 4.10）。
- 模式 A、B 的现有列位置与公式**一律不动**，新列一律追加在尾部。

### 4.10 背景敏感度列

对每个样本，令各图背景各自扰动 ±`spreadSd`，在 4 种符号组合下计算 R：

```
dark:   net(δ) = area * (B + δ) - rawSum
bright: net(δ) = rawSum - area * (B + δ)
R(δp, δt) = netP(δp) / netT(δt)
```

输出 `ratioSensitivityLow` = min、`ratioSensitivityHigh` = max、`ratioSensitivitySpan` = (high − low) / R。

若任一分母在扰动下 ≤ 0 → 三个字段返回 `null` 并附 code，**不得外推**。

必须在界面与导出中标注：**这是对已测背景起伏的传播，不是置信区间，也不是统计检验。**

### 4.11 测试（新增 `tests/test_shared_background.py`，≥ 18 项）

1. 恒定背景合成图：`value` 精确、`net` 与解析值相对误差 < 1e-9。
2. **背景框与条带框面积不等时，结果等于「均值 × 条带面积」而非「背景框总和」**（防 4.2 那个坑，必做）。
3. 与模式 A 在同一背景框下的数值一致性（`estimator = "mean"` 时应逐位相同）。
4. 含尘点时 `median` 与 `mean` 的差异符合预期。
5. 8 位 / 16 位各一项。
6. dark / bright 各一项。
7. 背景框与任一条带框重叠 → 拒绝（valid = False）。
8. 背景框越界 → 拒绝。
9. 背景框在分析区域之外 → 允许但产生警告。
10. 线性梯度背景 → `verdict == "unsupported"`。
11. 均匀背景 → `verdict == "supported"`。
12. 探针 < 3 → `verdict == "check"`，不得为 `supported`。
13. 应用模式 C 后该图全部确认被撤销；修改分析区域后 artifact 失效且 `mode` 不自动回退。
14. 分母在扰动下可能 ≤ 0 时敏感度返回 `null`。
15. 自动建议在同一输入上两次运行结果完全相同（确定性）。
16. 新建实验的 `mode` 初始值为 `"local"`、`modeChosenBy` 为 `"default"`；未应用前所有测量仍按模式 A 正常产出（防 §4.0 的陷阱）。
17. 用户显式 `restore` 到模式 A 后，`modeChosenBy` 变为 `"user"`，引导不再出现。
18. 读取无 `modeChosenBy` 字段的旧实验时视为 `"user"`，不触发引导。

---

## 5. WP3 · 中英双语（最后做）

### 5.1 为什么放最后

WP2 会新增一整套背景相关的界面文案与警告。先做双语等于把同一批文案翻译两遍。**必须在 WP1、WP2 合并之后再开始 WP3。**

### 5.2 真正的难点：中文已经写进数据里了

实测现状：

| 文件 | 中文片段 | 去重 |
| --- | --- | --- |
| `static/app.js` | 528 | 462 |
| `exports.py` | 240 | 184 |
| `server.py` | 145 | 132 |
| `analysis.py` | 120 | 111 |
| `background.py` | 75 | 70 |

更关键的是：`data/audit.jsonl` 的 `action` 字段是中文自由文本（`"创建实验"`、`"导入图像"`、`"生成候选选区"`），warnings 也是中文字符串，`data/state.json` 里有 3487 个中文片段。

**只在界面做翻译是不够的**：英文界面会漏出中文警告和中文历史，英文导出会中英混杂。

### 5.3 做法

**第一步：消息 code 化（不改任何功能）**

1. 新增 `messages.py`，维护 `CODE → {"zh": ..., "en": ...}` 注册表，支持 `{name}` 占位符。
2. `analysis.py` / `background.py` / `server.py` 产生的用户可见消息，改为返回 `{"code": ..., "params": {...}}`。
3. 审计事件**追加** `actionCode` 字段，`action` 的中文文本**保留不动**（历史可读性 + 向后兼容）。
4. **历史记录一律不回改。** 渲染规则：有 `code` 用 `code`，没有 `code` 原样显示已存文本。
5. 这一步单独提交，且 61 项既有测试必须仍然全过。

**第二步：界面语言切换**

1. `static/i18n/zh.js`、`static/i18n/en.js`，各自定义一个纯对象挂到 `window`（沿用无构建步骤的现状，**不引入任何打包器**），由 `index.html` 用 `<script>` 引入。
2. `t(key, params)`：缺键时返回 key 本身并 `console.warn`。
3. 顶栏加语言切换按钮，选择存 `localStorage`；读取失败要能降级到默认中文，不得白屏。
4. `启动.command` 的提示改为中英各一行。

**第三步：导出语言（D3 落实：覆盖导出文件）**

1. 导出对话框加语言选择，默认跟随界面语言。
2. `provenance.json` 的**字段名永远是英文**，与界面语言无关。
3. ZIP 内固定包含 `schema.json`：稳定英文字段键 → 当次导出的本地化列名与列序号。下游脚本认 `schema.json`，不认列名。
4. CSV 始终用 `.` 作小数点、ISO 8601 日期，与界面语言无关。
5. 导出时把 `uiLocale` 与 `exportLocale` 写进 `provenance.json`。

### 5.4 不翻译的东西

用户输入的一切：实验名、样本名、分组名、备注。原图文件名。SHA-256。所有标识符。

### 5.5 测试（新增 `tests/test_i18n.py`，≥ 6 项）

1. `zh` 与 `en` 的键集合完全一致（差集为空）。
2. 代码中出现的每个 code 都在两个目录中存在。
3. 占位符在两种语言中数量与名称一致。
4. 无 `code` 的历史审计记录仍能正确渲染。
5. `schema.json` 的英文键在两种导出语言下完全相同。
6. CSV 在两种语言下小数点与日期格式一致。

---

## 6. WP4 · 文档同步

### 6.1 README 必修的 9 项

以下是在标准版 README 上实测发现的问题，v2.0 的 README 必须全部修正：

1. **测试命令失效**：现写 `python3 -m unittest discover -s tests -v`；本机 `python3` 指向 miniconda，无 NumPy/Pillow，实跑结果是 `Ran 2 tests / FAILED (errors=2)`。必须改为 `.venv/bin/python3 -m unittest discover -s tests -v`。
2. **版本信息缺失**：README 仍写「第一版」，而 `VERSION` 是 Standard v1.0.0。v2.0 的 README 标题、`VERSION`、`ENVIRONMENT.json` 三处必须一致。
3. **虚拟环境段落过期**：目录里已经有 `.venv`，README 却写得像还没建。改为「已附带；仅迁移到新电脑时才需重建」。
4. **端口说明不完整**：`launch.py` 实际扫描 8765–8784 并顺延。README 必须说明浏览器打开的地址不一定是默认端口，以及怎么确认（启动终端第一行，或 `/api/health` 的 `dataDir`）。同时把「不要多个实例指向同一个 data」从 `STANDARD_RELEASE.md` 搬进 README。
5. **启动器报错指向不存在的章节**：`启动.command` 说「请按 README.md 中的便携安装步骤」，README 没有这个词。两边必须对齐。
6. **样例缺失未说明**：`.gitignore` 排除了 `examples/`，源码包也不含它，所以恢复出来的新目录点「导入 WBTEST 样例」会直接报错。README 必须写明前提。
7. **补 `--data-dir` 与 `--verbose`**：这是唯一干净的「另开测试目录、不碰正式 data」的办法。
8. **补「实验无法删除」**：代码中没有任何 DELETE 路由，建错的实验只能留着或停服务手改 `state.json`。
9. **把「什么操作会撤销确认」做成完整清单**，含 WP2 新增的规则（4.8 的表）。

### 6.2 新增/更新文档

- `RELEASE-NOTES-v2.0.0.md`：三项功能、变更的默认行为（D1 的结果）、数据兼容性说明。
- `DEVELOPMENT.md`：补模式 C 的方法、参数、局限；均匀性阈值必须明确标注为复核阈值。
- `TEST_REPORT.md`：重写，含真实测试数、真实浏览器验收步骤、以及 NOT RUN 清单。

### 6.3 文档纪律

所有文档中的数字（测试数、版本号、依赖版本、端口）必须来自实际执行结果。**不得预写「应该通过」的数字。** 未执行的验证一律列进 NOT RUN。

---

## 7. 交付顺序与验收

### 7.1 顺序

```
WP1（确认引导）→ 作者验收 → WP2（模式 C）→ 作者验收 → WP3（双语）→ WP4（文档）→ 整体验收
```

每个 WP 独立提交，可以单独回退。WP2 未通过验收前不要开始 WP3。

### 7.2 自动测试目标

| 阶段 | 目标 |
| --- | --- |
| 基线 | 61 |
| + WP1 | ≥ 70 |
| + WP2 | ≥ 88 |
| + WP3 | ≥ 94 |

### 7.3 人工验收脚本（作者执行）

1. 双击 `启动.command`，确认横幅显示 v2.0.0-dev、地址 8767；标准版与 v1.1.0 入口仍在原位且不受影响。
2. 载入 WBTEST 样例，确认 6 个样本、0 个已确认。
3. **WP1**：按「下一步」提示逐项操作，确认每一步指向都正确、可点击跳转；故意把一个背景框拖到别人的条带上，确认它被排到当前最高优先级。
4. **WP2**：两张图分别自动建议背景框 → 查看均匀性判定与探针数 → 应用 → 确认该图全部确认被撤销 → 重新确认 → 核对净信号、敏感度列。
5. **WP2 对照**：在同一 ROI 下把 `estimator` 设为 `mean`、背景框设为与模式 A 相同的框，确认数值与模式 A 逐位一致。
6. **WP2 失效**：修改分析区域，确认模式 C 失效且不自动回退到 A。
7. **WP3**：切换到英文，检查界面、警告、历史记录、导出四处都没有中文残留；切回中文确认可逆。
8. 导出 CSV / XLSX / ZIP：核对新列、`schema.json`、`provenance.json`、原图 SHA-256。
9. 重启服务，确认状态、模式、确认状态全部恢复。

### 7.4 提升为 Standard v2.0.0 的前置条件

全部满足才可提升，缺一项都不提：

1. 7.2 的测试目标达成且全过。
2. 7.3 的人工验收逐项通过，记录写进 `TEST_REPORT.md`。
3. v2.0 能正确读取 Standard v1.0.0 的 `data/`（无 `background` 字段 → 模式 A，数值不变）。
4. 文档中所有数字与实际一致；NOT RUN 清单完整。
5. 提升前完整备份现有 `data/`，并保留 `standard-v1.0.0` 标签与源码包不动。
6. 新建一个空目录，解压源码包，在无 `examples/`、无 `data/` 的状态下启动一次，确认所有提示都是可理解的，没有 traceback 泄漏。

---

## 8. 明确的非目标

本次**不做**：显著性检验、组间统计图、自动生物学结论、蛋白身份识别、分子量估计、多页 TIFF 支持、云端同步、跨电脑安装包、rolling ball / sliding paraboloid 等其他背景算法、任何形式的自动调参以优化组间差异。

模式 C 的均匀性判定、敏感度区间，以及模式 B 的拟合质量指标，**都不构成生物学定量有效性的证据**。p 与 total 来自不同膜这一根本限制，本次没有任何改变。
