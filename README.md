# kangsheng-tuzhi-moban

把**单页**供应链连接器工程 PDF/栅格原图制作为康生品牌图纸：同一 PDF 原件按冻结的认可配方精确复现；新原件先以完整原页通过 `SOURCE_INVENTORY_GATE`，再按已核定清单重排，并保留完整工程 QA 与独立发布门禁。

**已登记 SHA256 的原件无需模型再次排版。** 新型号、未知 SHA256 或不同内容的源文件仍需对照完整原件确认分组、裁切边界、排除区和布局，禁止套用其他图纸的裁切坐标。同型号原件是技术真源；用户明确认可的成品只提供品牌与版式依据，失败草稿不是样板。

冻结 PDF 配方仍由未改动的 `scripts/kangsheng.py` 精确重放，避免破坏其哈希绑定黄金样本。新源（含栅格供应链图）的 candidate/AI layout 适配器在生成前必须调用 `scripts/source_inventory_gate.py` 的整页源清单门禁、`scripts/source_color_roles.py` 的供应商/family 色义预检，生成后以实际 PDF 搬运组和实际输出字段再次联账；旧 `draft`/旧私有试跑均不是新产品交付通道。

| 场景 | 正式入口 | 通过后意味着什么 |
|---|---|---|
| 多份已认可原件的隔离批量重放 | `batch` + `kangsheng-products-v1` | 逐源 SHA/配方/认可基线的自动预检与精确视觉 QA；异常入队 |
| 同一原件重做认可视觉 | `replay` / `replay-batch` | 精确复现已冻结的认可配方，不是工程发布审核 |
| 认可成品尚未冻结 | `freeze-approved` | 计划重放与认可基线整页渲染一致，生成绑定凭证 |
| 新型号、未知 SHA256、工程发布 | v2 `init` → `draft` → `build` → `verify` | 独立审核及全部门禁通过后生成 `release.json` |

唯一生产 CLI 是 `scripts/kangsheng.py`，不再依赖历史会话中的一次性脚本。

新批量入口的输入契约、混合 PyMuPDF 运行时、异常队列及视觉/工程边界见 [确定性批量重放](references/product-batch.md)。此目录中的性能与版式扩展在隔离候选分支验证；未满足全部黄金回归前不得替换正式生产版本。

## 安装

Python 3.10+：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## 已认可原件：冻结一次，直接复现

```sh
python scripts/kangsheng.py freeze-approved PLAN.json \
  --baseline APPROVED.pdf --output RECIPE.json --approval-note '用户明确认可'
python scripts/kangsheng.py replay RECIPE.json --output WORK/replay
python scripts/kangsheng.py replay-batch --registry REGISTRY.json \
  --sources SOURCE_A.pdf SOURCE_B.pdf --output-root WORK/replay-batch
```

`freeze-approved` 绑定原件、配方、品牌资产、引擎、PyMuPDF/fitz 版本和整页认可基线渲染哈希；`replay` 按已冻结操作运行并核验，不调用模型重新裁切或排版。只有精确匹配原件 SHA256 的登记项才可重放；未知原件转入源审与建配方流程，不按文件名或外观猜匹配。

成功状态为 **`APPROVED_VISUAL_REPLAY_VERIFIED`**，始终标记 **`engineering_release=false`**，不产生 `release.json`。用户的视觉认可不替代源清单审核、独立终审或现有工程发布规则。完整契约见 [认可配方冻结与重放](references/approved-replay.md)。

冻结 exact replay 保留原配方颜色行为：`cyan-gold-v1` 和 `legacy-v1` 仅用于绑定旧认可基线的兼容重放，不全局改动黄金图纸。**新源 canonical** 规则不同：普通工程内容康生蓝；经该供应商/family 确认的强调技术角色（不论源为红、绿、青等）转低饱和康生金，未知色义须局部审阅。普通公差使用已认可英文疏排栏，逐字段从自己的源图核对；旧原表搬运仅是兼容模式。投影符号仍在专用格。

## 新原件：v2 工程制作

新任务使用 Manifest v2，Schema v1 仅兼容既有清单。

```sh
python scripts/kangsheng.py init INPUT.pdf \
  --manifest JOB.json --model '原图完整型号' --rotation 270
```

`init` 会生成：

- `JOB.json`：Manifest v2 草稿；
- `JOB-source.png`：完整旋转源页；
- `JOB-source-map.png`：带坐标网格和文字块编号的导航图；
- `JOB-inspection.json`：文字块、栅格连通域候选和绘图对象数量。

候选框只减少反复截图和找坐标的成本，不代表程序已理解图纸。按 [Manifest v2 规范](references/manifest.md) 完成人工分组后，先反复草拟：

```sh
python scripts/kangsheng.py draft JOB.json --output WORK/draft \
  --control-root WORK/control --cache WORK/cache
python scripts/kangsheng.py hashes JOB.json
```

旧 `draft` 不要求源清单已审核；配方和产物未变时复用，重生成会计数。同目录重生成会替换固定草稿产物，但保留逐次生成日志。**该产物只供隔离诊断，不是可交付候选；逐组 QA 通过不能宣称完整技术 QA 通过。** 所有新结构 candidate/AI layout 路径也须先以完整未裁切原页通过 `SOURCE_INVENTORY_GATE`，未知源对象不得自行排除。布局稳定后，由独立源审核者核对完整未裁切原页，把 `source_inventory_sha256`、审核者和独立 `reviewer_run_id` 写入 `review`，再构建：

现有 v2 的 `approved_tolerance_reflow` 兼容实现仅支持有逐型号原图字段映射且无表内额外条件的四档结构；源公差仍列在技术清单并保留原表像素差，不能列入非技术排除。新源多档、角度或额外条件须完整适配英文视觉并逐项核对，在支持前阻断而非删档/恢复旧视觉。可对既有候选只读复检，不增加生成次数：

```sh
python scripts/kangsheng.py recheck-candidate JOB.json WORK/draft/draft.pdf \
  --report WORK/draft/recheck.json --cache WORK/cache
```

```sh
python scripts/kangsheng.py build JOB.json --output WORK/run-001 \
  --control-root WORK/control --cache WORK/cache
```

确需重新生成的 `build` 使用新的输出目录；配方与完整产物未变时直接复用旧结果。成功状态是 `AUTO_QA_PASS`，不是交付完成。独立终审者查看两张审核图，填写生成的模板后验证：

```sh
cp WORK/run-001/final-review-template.json WORK/run-001/final-review.json
# 填 reviewer、与源审核不同的 reviewer_run_id、full_page_compared、verdict 和全部 checks
python scripts/kangsheng.py verify JOB.json WORK/run-001/drawing.pdf \
  --review WORK/run-001/final-review.json
```

只有 `verify` 生成 `release.json` 才可发布。省略 `--review` 只会重跑自动门禁，`release_ready` 为 false。

## v2 工程构建产物

| 产物 | 用途 |
|---|---|
| `drawing.pdf`, `preview.png` | 自动 QA 通过的候选成品及预览 |
| `audit.json`, `layout.json` | 自动检查、错误组件、位置和哈希 |
| `review-board.png` | 完整旋转源页与整张输出并排对照 |
| `review-details.png` | 各技术组的源/输出局部对照 |
| `final-review-template.json` | 已绑定输出、清单及两张审核图哈希的终审模板 |
| `manifest.snapshot.json`, `run.json` | 实际清单快照与引擎/依赖/资产/输出来源记录 |
| `review.snapshot.json`, `verify.json`, `release.json` | 终审快照、复验报告和发布门禁 |

自动 QA 失败时保留 `candidate.pdf`、`audit.json` 和审核图供定位，不产生 `drawing.pdf`。

## v2 质量门禁

- `SOURCE_INVENTORY_GATE` 在最终布局生成前清点完整原页：全部工程视图、尺寸/标注、PCB、型号表、性能、公差、投影、技术说明和额外条件；每项归入搬运、逐字段 `AUTHORIZED_TRANSFORM` 或经审核的非技术排除。未知对象、未放置技术对象/墨迹数必须为 0；否则 `SOURCE_INVENTORY_BLOCKED` / `FAIL_SOURCE_COMPLETENESS`，不能报告候选技术 QA 通过。
- 覆盖范围固定为旋转后的完整源页减去带类型的非技术排除区；其余技术墨迹必须由内容组、经审核的闭合线或授权语义替换账本承载。**已选组像素一致不是整页完整性。**
- 颜色 QA 单独给 `COLOR_SEMANTIC_PASS` / `COLOR_SEMANTIC_REVIEW` / `COLOR_SEMANTIC_FAIL`；布局 QA 单独给 `LAYOUT_QA_PASS`。只有源清单通过、颜色通过或获明确批准、布局通过，样本才可进入 family 候选学习。
- 检查裁切是否携带 `reviewed_source_extent` 外墨迹、是否切到文字、可提取技术文字缩放后是否低于 **4.75 pt**。
- 检查页面边界、保留区、真实墨迹重叠、表格/性能位置、标题与型号可搜索文本。
- 每片缺失和新增墨迹比例必须低于 **0.2%**；另对整张确定性输出做 expected-vs-actual 检查，可发现空白区或标题格中的增删改。
- 自动像素检查不理解工程语义，也不能证明供应商原件正确；完整源图和数值仍需独立人工/代理终审。

## 哈希分工

- `source_inventory_sha256`：绑定源文件内容与旋转、身份/字段、组类型及裁切、审核范围、排除区和表头；英文重排时还绑定该型号源字段映射哈希。不绑定源路径、`dst`、`scale` 或品牌资产。
- `inventory_sha256`：绑定除顶层 `review` 外的完整配方，以及实际引擎、画框、QA 规则版本和资产文件字节；配置或代码变化不得复用旧输出。

`inventory-hash` 保留为只输出完整配方哈希的兼容命令；新流程优先用 `hashes` 同时取得两者。

## v2 工程小批量

```sh
python scripts/kangsheng.py batch JOBS.json \
  --output-root WORK/batch --control-root WORK/control --cache WORK/cache --mode build
```

直接和批次共用 `WORK/control/batch-state.json`；批次另留 `job-summary.json`。至少指定一个先导项；先导项全部生成并独立 `verify` 出 `release.json` 后，重跑 `batch` 才会放行普通项。同一产品/阶段/已核验原图最多两次实际生成，旧账本不自动迁移。详见 [批量执行与接力](references/batch-handoff.md)。本包不含飞书上传实现；发布规则见 [references/publishing.md](references/publishing.md)。

## 跨窗口接力与耗时口径

优先读取项目 `handoff/认可版快速接力.md` 及本机 `~/.local/share/kangsheng-tuzhi/registry.json`，凭源 SHA256 找认可配方，不从历史聊天重新猜布局。该注册表是本地私有工作状态，不是要提交到公开仓库的配置。

同一原件直接重放；未知版式需要源审与建配方时间。报告时间时分别说明渲染、自动核验、人工/代理审阅，不把整套审计耗时写成 PDF 渲染耗时。

## 数据边界

仓库只包含程序、品牌资产、规范和合成测试，公开示例只用占位符。供应链原件、客户型号表、成品、私有配方及注册表、业务记录 ID、令牌、日志、绝对私有路径和逐行账本均保留在私有任务目录。
