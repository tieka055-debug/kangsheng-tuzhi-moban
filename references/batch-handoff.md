# 批量执行与跨任务接力（v2）

## 目标

批次只负责本地生成、状态持久化和放行门禁，不上传飞书。每项使用稳定业务 ID；同一配方不会隐式重跑，失败默认最多尝试 2 次。

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
2. 用 `batch --mode draft` 快速检查布局；草稿可在同一目录反复覆盖，不消耗源清单复审。
3. 源清单定稿后填写 source reviewer、完整覆盖和 `source_inventory_sha256`，再运行 build 批次。
4. build 模式先只生成 pilot。对 pilot 的 `review-board.png` 与 `review-details.png` 做独立终审，并执行 `verify` 生成 `release.json`。
5. 重跑同一 batch；只有全部 pilot 均为 `RELEASE_READY`，普通项才会放行。

```sh
python scripts/kangsheng.py batch JOBS.json \
  --output-root WORK/batch --cache WORK/cache --mode build --max-attempts 2
```

## 状态与目录

- 唯一真源：`WORK/batch/batch-state.json`。
- 汇总：`WORK/batch/job-summary.json`。
- 每次 build 使用独立目录：`JOB_ID/INVENTORY_HASH-aN/`；失败证据不会被覆盖。
- 常见状态：`PREFLIGHT_FAIL`、`AUTO_QA_FAIL`、`AUTO_QA_PASS`、`BLOCKED_PILOT_GATE`、`RELEASE_READY`、`NEEDS_REVIEW_RETRY_LIMIT`。
- 未变化的失败不会重复烧算力；修正 manifest 会产生新配方哈希。确需重试同一配方时显式加 `--allow-retry`。

## 交接最小包

交接只带：`JOBS.json`、manifest、`batch-state.json`、当前 attempt 目录、源文件及其 hash。不要把聊天记录、一次性脚本或旧 build 目录当作状态真源。

## 发布边界

只有同时存在且哈希互相匹配的 `drawing.pdf`、`verify.json`、`review.snapshot.json` 和 `release.json` 才可发布。远端发布仍按 [publishing.md](publishing.md) 先上传、下载回读校验，再替换旧附件。
