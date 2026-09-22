# kangsheng-tuzhi-moban

把**单页**供应链连接器工程 PDF 按人工核定的内容清单重排为康生品牌图纸，并输出自动 QA、审核图和可追溯凭证。

本工具不是任意图纸的自动理解/自动排版器：首次分组、裁切边界、排除区和布局仍需对照完整原件确定。同型号供应链原件是技术真源；用户明确认可的第 14–18 行只作品牌与版式参考，失败草稿只作问题对照。不要跨型号复用裁切坐标、技术值或 PASS 结论。新任务使用 Manifest v2，Schema v1 仅兼容既有清单。

## 快速开始

Python 3.10+：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

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
python scripts/kangsheng.py draft JOB.json --output WORK/draft --cache WORK/cache
python scripts/kangsheng.py hashes JOB.json
```

`draft` 不要求源清单已审核，并会替换同目录中的固定草稿产物。它仍执行结构和自动 QA；不会生成发布凭证。布局稳定后，由独立源审核者核对完整未裁切原页，把 `source_inventory_sha256`、审核者和独立 `reviewer_run_id` 写入 `review`，再构建：

```sh
python scripts/kangsheng.py build JOB.json --output WORK/run-001 --cache WORK/cache
```

每次 `build` 使用新的输出目录。成功状态仍是 `AUTO_QA_PASS_REQUIRES_VISUAL_REVIEW`，不是交付完成。独立终审者查看两张审核图，填写生成的模板后验证：

```sh
cp WORK/run-001/final-review-template.json WORK/run-001/final-review.json
# 填 reviewer、与源审核不同的 reviewer_run_id、full_page_compared、verdict 和全部 checks
python scripts/kangsheng.py verify JOB.json WORK/run-001/drawing.pdf \
  --review WORK/run-001/final-review.json
```

只有 `verify` 生成 `release.json` 才可发布。省略 `--review` 只会重跑自动门禁，`release_ready` 为 false。

## 构建产物

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

- 覆盖范围固定为旋转后的完整源页减去带类型的非技术排除区；其余技术墨迹必须由内容组或经审核的闭合线承载。
- 检查裁切是否携带 `reviewed_source_extent` 外墨迹、是否切到文字、可提取技术文字缩放后是否低于 **4.75 pt**。
- 检查页面边界、保留区、真实墨迹重叠、表格/性能位置、标题与型号可搜索文本。
- 每片缺失和新增墨迹比例必须低于 **0.2%**；另对整张确定性输出做 expected-vs-actual 检查，可发现空白区或标题格中的增删改。
- 自动像素检查不理解工程语义，也不能证明供应商原件正确；完整源图和数值仍需独立人工/代理终审。

## 哈希分工

- `source_inventory_sha256`：绑定源文件内容与旋转、身份/字段、组类型及裁切、审核范围、排除区和表头；不绑定源路径、`dst`、`scale` 或品牌资产。只改布局时可沿用已完成的源清单审核。
- `inventory_sha256`：绑定除顶层 `review` 外的完整配方，用于输出、终审、批次运行目录和复验。

`inventory-hash` 保留为只输出完整配方哈希的兼容命令；新流程优先用 `hashes` 同时取得两者。

## 小批量

```sh
python scripts/kangsheng.py batch JOBS.json \
  --output-root WORK/batch --cache WORK/cache --mode build --max-attempts 2
```

批次使用一个 `batch-state.json` 账本和一个 `job-summary.json` 汇总；先导项全部生成并独立 `verify` 出 `release.json` 后，重跑 `batch` 才会放行普通项。详见 [批量执行与接力](references/batch-handoff.md)。本包不含飞书上传实现；发布规则见 [references/publishing.md](references/publishing.md)。

## 数据边界

仓库只包含程序、品牌资产、规范和合成测试。供应链原件、客户型号表、成品、业务记录 ID、令牌、日志和逐行账本均保留在私有任务目录。
