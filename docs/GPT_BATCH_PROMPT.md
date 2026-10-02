# 给 GPT/Codex 的批量转图指令（模板，ID 运行时填）

把下面整段发给 GPT。花括号里的内容替换成真实值：
`{BASE}` base token、`{TABLE}` 表 ID、`{F_PDF}` 「pdf格式图纸」字段 ID、`{F_KS}` 「康生图纸」字段 ID、`{F_NOTE}` 「备注」字段 ID。

---

你在帮我把供应商的连接器图纸转成「康生」自己的图纸，并回填到飞书多维表格。仓库在 `/Users/vill/Documents/康生图纸模版/kangsheng-tuzhi-moban`，**先读 `docs/HANDOFF.md` 和 `SKILL.md`，严格按里面的流程和规则做**。

## 环境（每次开新终端都要）
```bash
cd /Users/vill/Documents/康生图纸模版/kangsheng-tuzhi-moban
export PATH=$HOME/gs-env/bin:$PATH          # Ghostscript，没有它带文字层的 PDF 出不了图
git pull
```
仓库自带 `.venv` 缺依赖，请另建环境：`python3 -m venv ~/ks-venv && ~/ks-venv/bin/pip install -r requirements.txt`，然后用 `~/ks-venv/bin/python`。
中文字体：`/System/Library/Fonts/STHeiti Medium.ttc`（加 `--font-index 0`）。

## 数据位置
飞书多维表格 base `{BASE}`，表 `{TABLE}`（「全图纸」）。每条记录：
- 「型号」：用作输出图纸的型号。
- 「pdf格式图纸」`{F_PDF}`：**原图来源**（附件，用 `lark-cli base +record-download-attachment` 下载；下载路径用相对路径）。
- 「康生图纸」`{F_KS}`：**转好后回填到这里**（只追加）。
- 「备注」`{F_NOTE}`：缺图/做不了时在这里追加说明。
- 「供应商」只是参考，**可能为空或不准，不要依赖**。

## 对每一条记录的流程
1. 下载 pdf 原图。没有 pdf、或原图是扫描件/图片（`page.get_drawings()` 几乎为空）、或原图损坏 → **不出图**，用 `tools/feishu_note.py` 在备注追加一句原因，进入下一条。
2. 判图框：`python pipeline/frame_match.py 原图.pdf`。
   - 结果 `OK`：用它给的模板和旋转（job 里 `"template":"auto"` 即可）。
   - 结果 `AMBIGUOUS` / `UNKNOWN_FRAME` / `NO_FRAME`：**不要硬做、不要套最像的模板**。把这条记入「待处理清单」（记录行号、型号、结果、最像的模板），进入下一条。
3. 读公差：放大原图的公差格逐行照抄，**只抄本图自己的，不从别的型号抄**；原图写法照抄（含错别字）。档位名太长装不下就改短写（如 `0~5`），数值不变。
4. 写 job.json 并运行 `python pipeline/cad_family.py job.json --out 输出目录 --font <字体>`。标题用图纸自己的品名（DC电源插座、电池连接器、USB连接器…），看不出来就写「连接器」并在汇报里标出来。
5. **看「原图对照.png」逐项核对**：视图/尺寸/说明/零件表都在；没有残留供应商标题栏、修订栏、公司名、水印；标注是蓝色、端子焊盘等重点是金色。有问题不要回填，记入「待处理清单」并写明现象。
6. 合格的才回填：见下。

## 回填（只追加，不改别的）
```bash
python tools/feishu_backfill.py --base {BASE} --table {TABLE} --field {F_KS} \
  --source-field "pdf格式图纸" --target-field-name "康生图纸" --plan plan.json
```
`plan.json`：`[{"record_id":"...","source_key":"原图文件名的一部分","file":"本地康生图纸PDF路径"}]`。
它会核对原图名对得上才上传、已有同名文件会跳过、上传后回读校验。

## 铁律
- **只往「康生图纸」和「备注」追加**，不删除、不修改任何已有附件和其他字段。
- 不确定的（图框认不出、读不清公差、对照图有残影）一律**不回填**，进待处理清单，由我来决定。
- 不改 `pipeline/`、`engine/`、`families/` 里的任何文件（模板和指纹由我维护）；发现模板问题只汇报。
- 改任何东西之前先 `git pull`；不要提交或推送。

## 先做试跑（必须）
先只处理前 5 条能自动判 `OK` 的记录，**把每条的对照图发给我看**，我确认后你再批量做。批量时每 20 条汇报一次：成功回填几条、跳过几条及原因、待处理清单。

## 最后交付
1. 回填结果（`result.json` 汇总）。
2. 待处理清单（行号 / 型号 / 原因 / 最像模板），按原因分组，方便我决定要不要补模板。
3. 需要我确认的读数和标题。
