---
name: kangsheng-tuzhi-moban
description: 将单页供应链连接器工程 PDF 按已审核清单重排为康生品牌图纸；同一原件按已认可配方精确复现，新原件先源审建配方，并保留独立工程发布门禁。
---

# 康生图纸：认可配方复现与技术内容保全

冻结 PDF 配方的入口是 `scripts/kangsheng.py`。**已认可原件走确定性重放；新原件走清单驱动的搬运与验证。** 新源 candidate/AI layout 适配器（包括栅格原图）须在写成品前调用 `scripts/source_inventory_gate.py` 与 `scripts/source_color_roles.py`，成品后联账核验；旧私有试跑脚本不是生产入口。同一原件无需模型再次分组、找坐标或排版；程序不会自动理解任意新图纸。

> 同型号供应链原件是技术真源；用户明确认可的成品是品牌与版式参考。失败草稿不是样板。视觉认可不等于工程发布审核，不跨型号复用裁切坐标、技术值或 PASS 结论。

## 当前供应商修订入口（先读）

当前质源供应商的新生产沿用同一矢量引擎，增加 **完整源字段账本与独立嵌入字体**。
同类图先读 [供应商字段与跨模型接力](references/supplier-fields.md)，再执行 `scripts/kangsheng.py`；
不得运行旧私人 `render_dynamic_batch.py` 的正则公差提取器。源公差可能是曲线字，PDF可搜索文字只是子集。
新作业声明 `supplier_id: zhiyuan-precision`，必须提供 `source_fields`；配方绑定原件SHA，未知原件仍需自身清单。
接力包先读 `HANDOFF.json` 并执行 `handoff-check`，不要翻聊天或重新搭建流水线。

## 先选入口

1. **同一原件、SHA256 已登记且有冻结配方**：单张使用 `replay`，多张隔离批量使用 `batch` 的 `kangsheng-products-v1` 输入，直接复现已认可版式，不重新排版。详见 [认可配方冻结与重放](references/approved-replay.md) 与 [确定性批量重放](references/product-batch.md)。
2. **已有明确认可的成品及对应计划，但尚未冻结**：使用 `freeze-approved`，由程序重放计划并核对整页认可基线，再登记到本机私有注册表。不要凭外观相似直接登记。
3. **新型号、未知 SHA256、缺少完整配方或验证不一致**：先核对完整原件，执行下述 `SOURCE_INVENTORY_GATE`，再用 Manifest v2（PDF 矢量）或经门禁的栅格 candidate 适配器建清单、审源与调布局。禁止套用已知图纸的裁切坐标；新配方需要实际审阅时间。旧 `draft` 产物只供诊断，不是通过完整源清单门禁的可交付候选。

跨窗口先找项目 `handoff/认可版快速接力.md` 及本机 `~/.local/share/kangsheng-tuzhi/registry.json`，核对原件 SHA256、配方和认可基线，再决定是否重放。该注册表及接力材料仅供本机私有工作使用；不要上传源稿、成品、业务记录 ID 或绝对私有路径。涉及完整工程批次时再读 [批量执行与接力](references/batch-handoff.md)。不要翻历史聊天猜配方。

## 不变量

- 原 PDF 只读并绑定 SHA256；型号、零件号、标题、单位、比例、页码、公差、投影符号及全部技术图表逐图核对，不跨型号猜补。
- 技术数值和图形搬运原矢量，不用 OCR 重打。`view`/`pcb` 默认保持 1:1；只有逐组绑定同源认可布局和成品哈希、且图框标为 `NTS` 的已审核个案，才允许单一等比缩放；禁止横纵拉伸。
- v2 把旋转后的整页视为待保全范围，只允许排除有类型、有理由、经审核的非技术页框/标题家具；组裁切不得进入排除区。
- 多页输入、身份冲突、缺失原件或 QA 失败均停止该项。自动 QA 是门禁，不是工程认证。
- 源清单审核与最终输出审核必须由不同审核者/独立运行完成；脚本不替人填写 PASS。
- 重放验证通过只表示绑定原件和环境下复现认可视觉。状态为 `APPROVED_VISUAL_REPLAY_VERIFIED`、`engineering_release=false`，不产生 `release.json`，不替代 v2 工程门禁。

## 当前版式约定

- 第 14–18 行认可视觉是冻结黄金基准。保留认可的原版金属渐变 K 标志与公司栏，不重新画 Logo，也不通过插值放大冒充高分辨率原件。
- 康生输出按**语义 slot** 排列：Pin/Part No./DIM 型号表 `RIGHT_TOP_TABLE`，性能块 `RIGHT_MIDDLE_PERFORMANCE`（在表下、左右边界统一），工程视图和 PCB 在 `MAIN_ENGINEERING`；Logo/公司栏/标题栏固定右下，英文疏排公差栏固定底部。供应链原页坐标只用于寻找内容，不能决定成品位置；仅源位置变化不构成 `NEW_LAYOUT`。区域与等比/可读性规则见 [canonical output layout](supported-layouts/canonical-output-layout.json)。
- 普通工程线和文字转康生蓝；**经该供应商/family 证据确认的技术强调角色**转低饱和康生金。强调色可来自红、绿、青等，不能只凭 `cyan` 判断，也不能把所有非黑内容转金。初遇不明颜色语义为 `COLOR_SEMANTIC_REVIEW`，不得静默变蓝后 PASS。`cyan-gold-v1` 与 `legacy-v1` 只用于哈希绑定的历史配方精确重放，不是新源 canonical 颜色政策。
- 新源如有普通公差，使用已认可的 `UNLESS OTHERWISE` / `SPECIFIED, TOLERANCE:` 英文疏排模板；**每一原技术字段**经 `AUTHORIZED_TRANSFORM` 账本映射到自己的输出字段，再逐字段 QA。公差原区仍计入源技术清单，绝不当作非技术排除。四档且无表内附加条件的旧 `approved_tolerance_reflow` 是现有 v2 的兼容实现边界；多档、角度或额外条件须在同一视觉语言下完整容纳、逐项核对，若程序未支持就阻断并局部审阅，不能删成四行或套旧原表作为 canonical 成品。
- 投影符号仍从该型号原图搬到右下格。技术数据、图形、尺寸、PCB、说明、性能及全部表行以该型号自己的供应链原页为准，不跨型号填值；禁止横纵拉伸。
- 标题和型号使用支持中文的清晰中等字重字体，保留完整系列名。终审必须放大查看这些文字；可搜索文本存在或确定性像素比较通过，不等于字形清晰。

## 新源不可绕过的质量门禁

无论路由为 `SUPPORTED_EXACT`、`SUPPORTED_COMPATIBLE`、`AI_LOCAL_REVIEW` 或 `NEW_LAYOUT`，最终布局生成**之前**先完成 `SOURCE_INVENTORY_GATE`：从完整、未裁切供应链原页清点全部视图、尺寸与标注、PCB、Pin/Part No. 表、性能、公差、投影、技术说明、额外条件及其他技术图表。每个源对象必须被归入**已搬运**、**有逐字段账本的 `AUTHORIZED_TRANSFORM`**，或**明确理由且经审核的非技术排除**；未知对象不能排除。没有完整源清单证明时状态为 `SOURCE_INVENTORY_BLOCKED`，不得生成可交付候选，也不得报告技术 QA 通过。

候选 QA 的分母是**完整原页技术范围**，不是已选 `groups`/裁片；必须显示合法非技术排除、已搬运内容、授权语义替换及未解释技术对象/墨迹数量，后者必须为 **0**。否则 `FAIL_SOURCE_COMPLETENESS` 并指出缺失位置。逐组像素搬运一致只证明被选部分未变，不证明源页完整。颜色另给 `COLOR_SEMANTIC_PASS` / `COLOR_SEMANTIC_REVIEW` / `COLOR_SEMANTIC_FAIL`；布局另给 `LAYOUT_QA_PASS`。只有 `SOURCE_INVENTORY_PASS` 且颜色已通过或明确批准、布局通过，样本才可进入 family 候选学习；不得从错误候选学习。历史精确重放的视觉核验不自动成为新产品的这些证明。

新源适配器的强制调用顺序：`assess_source_inventory` → `require_source_inventory_pass` → `scan_source_colors`（未知为 REVIEW）→ slot 布局与生成 → `audit_pdf_color_semantics`（含源像素几何）→ `assess_candidate_completeness`（以实际 PDF 搬运组和实际输出字段联账）→ 布局 QA。失败时旧 draft 的 `DRAFT_QA_PASS` 名称也不得被提升为候选技术 QA PASS。第 42 行旧输出只保留为负回归，禁止继续作为成品或学习样本。

## 已认可原件：最短复现流程

```sh
python scripts/kangsheng.py freeze-approved PLAN.json \
  --baseline APPROVED.pdf --output RECIPE.json --approval-note '用户明确认可'
python scripts/kangsheng.py replay RECIPE.json --output WORK/replay
python scripts/kangsheng.py replay-batch --registry REGISTRY.json \
  --sources SOURCE_A.pdf SOURCE_B.pdf --output-root WORK/replay-batch
```

冻结配方使用 `recipe_schema: kangsheng-approved-recipe-v1`，绑定源、配方、资产、引擎、PyMuPDF/fitz 和整页认可基线渲染哈希；任何不匹配都不自动重排或覆盖认可基线。`source` 操作直接从原 PDF 复制；`text` 目前仅允许日期元数据，不重打技术内容。详见 [完整契约](references/approved-replay.md)。

## 新原件：完整工程流程

新任务使用 Manifest v2；Schema v1 仅兼容旧清单。

1. `init` 生成 v2 草稿、完整源页、坐标源图和检查索引。索引中的文字框/栅格候选只用于导航，不是自动识别结果。
2. 按 [Manifest v2](references/manifest.md) 填写身份、完整技术清单、`reviewed_source_extent`、位置、颜色语义和带类型排除区。`SOURCE_INVENTORY_PASS` 之前仅可生成不可交付的内部诊断草稿；旧 `draft` 不要求源审核、也不产生可发布结论，更不能因为已选组 QA 通过而标为候选技术 QA 通过。
3. 布局稳定后运行 `hashes`。源审核只绑定 `source_inventory_sha256`；`dst`、`scale` 或品牌资产变化不会迫使重做源清单审核。完整配方与最终输出审核绑定 `inventory_sha256`。
4. 独立源审核者核对未裁切整页并填写 Manifest 的 `review` 后，在全新输出目录运行 `build`。查看 `review-board.png` 的整页源/输出对照及 `review-details.png` 的逐组对照。
5. 独立终审者填写生成的 `final-review-template.json`，保留两张审核图哈希并填写不同的 `reviewer_run_id`；再运行 `verify --review`。只有生成 `release.json` 才是可发布状态。

自动门禁包括：整页技术墨迹零漏放、裁切范围外墨迹、切字、可提取技术文字缩放后不小于 **4.75 pt**、内容真实墨迹重叠、逐片缺失/新增墨迹，以及整张成品 expected-vs-actual 比对。详情与字段契约见 [references/manifest.md](references/manifest.md)。

## 效率、批次与发布

- 已知原件只执行已冻结配方与核验，不重复模型排版。报告耗时时分开列出渲染、自动核验和人工/代理审阅；不要把整套审计耗时当作 PDF 渲染耗时，也不要把已知配方的速度承诺给未知版式。
- `replay-batch` 仅用于已登记原件的视觉重放；工程小批量继续使用 `batch` 的单一 JSON 账本、先导项门禁和默认最多 2 次尝试，读 [批量执行与接力](references/batch-handoff.md)。
- 小批量与直接 `draft`/`build` 使用同一个项目级 `--control-root`，按已核验 record_id、原图 SHA 和阶段限制实际生成次数；旧账本不静默重置。
- 发布到飞书前读 [发布顺序](references/publishing.md)，原有发布规则不变。本仓库不保存供应链原件、成品、私有注册表、记录 ID、令牌或上传代码。
