---
name: kangsheng-tuzhi-moban
description: 将单页供应链连接器工程 PDF 按已审核清单重排为康生品牌图纸；同一原件按已认可配方精确复现，新原件先源审建配方，并保留独立工程发布门禁。
---

# 康生图纸：认可配方复现与技术内容保全

唯一生产入口是 `scripts/kangsheng.py`。**已认可原件走确定性重放；新原件走清单驱动的矢量搬运与验证。** 同一原件无需模型再次分组、找坐标或排版；程序不会自动理解任意新图纸。

> 同型号供应链原件是技术真源；用户明确认可的成品是品牌与版式参考。失败草稿不是样板。视觉认可不等于工程发布审核，不跨型号复用裁切坐标、技术值或 PASS 结论。

## 先选入口

1. **同一原件、SHA256 已登记且有冻结配方**：使用 `replay` 或 `replay-batch`，直接复现已认可版式，不重新排版。详见 [认可配方冻结与重放](references/approved-replay.md)。
2. **已有明确认可的成品及对应计划，但尚未冻结**：使用 `freeze-approved`，由程序重放计划并核对整页认可基线，再登记到本机私有注册表。不要凭外观相似直接登记。
3. **新型号、未知 SHA256、缺少完整配方或验证不一致**：先核对完整原件，再用 Manifest v2 建清单、审源与调布局。禁止套用已知图纸的裁切坐标；新配方需要实际审阅时间。

跨窗口先找项目 `handoff/认可版快速接力.md` 及本机 `~/.local/share/kangsheng-tuzhi/registry.json`，核对原件 SHA256、配方和认可基线，再决定是否重放。该注册表及接力材料仅供本机私有工作使用；不要上传源稿、成品、业务记录 ID 或绝对私有路径。涉及完整工程批次时再读 [批量执行与接力](references/batch-handoff.md)。不要翻历史聊天猜配方。

## 不变量

- 原 PDF 只读并绑定 SHA256；型号、零件号、标题、单位、比例、页码、公差、投影符号及全部技术图表逐图核对，不跨型号猜补。
- 技术数值和图形搬运原矢量，不用 OCR 重打。`view`/`pcb` 保持 1:1；同组多片使用同一等比变换。
- v2 把旋转后的整页视为待保全范围，只允许排除有类型、有理由、经审核的非技术页框/标题家具；组裁切不得进入排除区。
- 多页输入、身份冲突、缺失原件或 QA 失败均停止该项。自动 QA 是门禁，不是工程认证。
- 源清单审核与最终输出审核必须由不同审核者/独立运行完成；脚本不替人填写 PASS。
- 重放验证通过只表示绑定原件和环境下复现认可视觉。状态为 `APPROVED_VISUAL_REPLAY_VERIFIED`、`engineering_release=false`，不产生 `release.json`，不替代 v2 工程门禁。

## 当前版式约定

- 保留认可的原版金属渐变 K 标志与公司栏，不重新画 Logo，也不通过插值放大冒充高分辨率原件。
- 型号表右上、性能块右中；同批性能字号接近且清晰。产品视图与 PCB 利用其余区域，移动而非裁掉标注或拉伸填空。
- `cyan-gold-v1` 将原图青色针脚转为金色，其余非白色内容转为蓝色；`legacy-v1` 保留旧稿既有行为。颜色策略随配方冻结，不能全局改色后悄悄改变已认可旧稿。
- 原公差表只出现一次，保留原矢量单元格及闭合网格，置于底部公差槽并贴齐底边；不要转录公差值或以重画表格代替原格。投影符号在右下专用格内居中。具体边界与品牌资产规则见 [Manifest v2](references/manifest.md)。
- 公差表中的整空行/列，经核对确实只有网格线、没有数值符号及表头时可省略，并记录有理由的非技术排除区；保留所有有内容的格子。角度公差列不可因为其他型号为空就批量删除。
- 标题和型号使用支持中文的清晰中等字重字体，保留完整系列名。终审必须放大查看这些文字；可搜索文本存在或确定性像素比较通过，不等于字形清晰。

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
2. 按 [Manifest v2](references/manifest.md) 填写身份、技术组、`reviewed_source_extent`、位置和带类型排除区。使用 `draft` 在同一草稿目录反复调布局；`draft` 不要求源审核，也不产生可发布结论。
3. 布局稳定后运行 `hashes`。源审核只绑定 `source_inventory_sha256`；`dst`、`scale` 或品牌资产变化不会迫使重做源清单审核。完整配方与最终输出审核绑定 `inventory_sha256`。
4. 独立源审核者核对未裁切整页并填写 Manifest 的 `review` 后，在全新输出目录运行 `build`。查看 `review-board.png` 的整页源/输出对照及 `review-details.png` 的逐组对照。
5. 独立终审者填写生成的 `final-review-template.json`，保留两张审核图哈希并填写不同的 `reviewer_run_id`；再运行 `verify --review`。只有生成 `release.json` 才是可发布状态。

自动门禁包括：整页技术墨迹零漏放、裁切范围外墨迹、切字、可提取技术文字缩放后不小于 **4.75 pt**、内容真实墨迹重叠、逐片缺失/新增墨迹，以及整张成品 expected-vs-actual 比对。详情与字段契约见 [references/manifest.md](references/manifest.md)。

## 效率、批次与发布

- 已知原件只执行已冻结配方与核验，不重复模型排版。报告耗时时分开列出渲染、自动核验和人工/代理审阅；不要把整套审计耗时当作 PDF 渲染耗时，也不要把已知配方的速度承诺给未知版式。
- `replay-batch` 仅用于已登记原件的视觉重放；工程小批量继续使用 `batch` 的单一 JSON 账本、先导项门禁和默认最多 2 次尝试，读 [批量执行与接力](references/batch-handoff.md)。
- 发布到飞书前读 [发布顺序](references/publishing.md)，原有发布规则不变。本仓库不保存供应链原件、成品、私有注册表、记录 ID、令牌或上传代码。
