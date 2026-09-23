# 批量执行与跨任务接力（v2）

## 目标

批次只负责本地生成、状态持久化和放行门禁，不上传飞书。每项使用已核验的 `identity.record_id`，并与 `jobs[].id` 一致；草稿和正式阶段分别最多 2 次实际生成。直接入口与批次必须指向**同一个项目级 `--control-root`**，更换输出目录、清单布局或入口不重置次数。

## 输入

`JOBS.json`：

```json
{
  "jobs": [
    {"id": "record-001", "manifest": "manifests/001.json", "pilot": true},
    {"id": "record-002", "manifest": "manifests/002.json"}
  ]
}
```

- `id` 必须唯一且只含字母、数字、点、下划线或连字符。
- `manifest` 相对路径以 `JOBS.json` 所在目录为基准。
- 每批至少选 1 个代表性 `pilot`；不同结构应分别设置先导项。

## 推荐流程

1. 先对全部原件执行 `init`，一次性保存 source map、文字框和候选组件索引。
2. 用 `batch --mode draft` 快速检查布局；草稿可在同一目录调整，但实际重生成会计数，同配方且完整的产物直接复用。草稿不消耗源清单复审。
3. 源清单定稿后填写 source reviewer、完整覆盖和 `source_inventory_sha256`，再运行 build 批次。
4. build 模式先只生成 pilot。对 pilot 的 `review-board.png` 与 `review-details.png` 做独立终审，并执行 `verify` 生成 `release.json`。
5. 重跑同一 batch；只有全部 pilot 均为 `RELEASE_READY`，普通项才会放行。

```sh
python scripts/kangsheng.py batch JOBS.json \
  --output-root WORK/batch --control-root WORK/control --cache WORK/cache --mode build

python scripts/kangsheng.py draft JOB.json --output WORK/draft \
  --control-root WORK/control --cache WORK/cache
python scripts/kangsheng.py build JOB.json --output WORK/build-new \
  --control-root WORK/control --cache WORK/cache
```

## 状态与目录

- 唯一运行账本：`WORK/control/batch-state.json`（schema v2）；详细生成日志在各运行目录。旧 schema v1 不自动迁移，原文件保持不变并停止运行。
- 汇总：`WORK/batch/job-summary.json`。
- 每次 build 使用独立目录：`JOB_ID/INVENTORY_HASH-aN/`；失败证据不会被覆盖。
- 常见状态：`PREFLIGHT_FAIL`、`AUTO_QA_FAIL`、`DRAFT_QA_PASS`、`AUTO_QA_PASS`、`BLOCKED_PILOT_GATE`、`RELEASE_READY`、`NEEDS_REVIEW_RETRY_LIMIT`。
- 未变化的失败不会重复烧算力；修改布局会改变配方哈希，但不会清零按 `record_id` + 已核验源 SHA256 绑定的次数。同配方第二次生成需显式 `--allow-retry`；此参数和 `--max-attempts` 不能突破两次上限。
- 如确需人工重置，用独立 `reset-attempts --control-root ... --record-id ... --source-sha256 ... --stage draft|build --operator ... --reason ...` 命令；原历史留在事件中。不得在普通批次中重置。正常 CLI 只输出短摘要，细节查证据路径。

## 交接最小包

交接只带：`JOBS.json`、manifest、`batch-state.json`、当前 attempt 目录、源文件及其 hash。不要把聊天记录、一次性脚本或旧 build 目录当作状态真源。

## 发布边界

只有同时存在且哈希互相匹配的 `drawing.pdf`、`verify.json`、`review.snapshot.json` 和 `release.json` 才可发布。远端发布仍按 [publishing.md](publishing.md) 先上传、下载回读校验，再替换旧附件。
