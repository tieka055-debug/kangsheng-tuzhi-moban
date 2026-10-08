# 润擎（RunQing）续作入口（2026-10-08）

新窗口先读这一页，再读 `SKILL.md`（「目标品牌」一节）、`docs/HANDOFF.md` 末尾几段、`docs/RUNQING_BRAND_TASK.md`。

## 1. 范围（别搞错表）
- 只做飞书「全图纸」表的「润擎图纸」字段（康生同表的「康生图纸」字段另算）。
- 同一个 base 里的「产品表 / 产品表2 / 产品表 Copy / 图纸」是旧库，**不在本流程**，不要动，也不要拿里面的润擎图当成果（2026-10-08 用户就是被那里面别人/旧做法生成的字段重排版润擎图误导，白折腾了一轮；那一轮的代码改动已撤销，见 HANDOFF）。
- 润擎只做「做法 A」：只换图框，原图矢量原样搬；**不用飞书字段重排规格/BOM（做法 B）**。

## 2. 流程（一直在用的）
1. GPT 按指令批量出图：一批 50 条，先不回填，按「候选 / HOLD / 跳过」三组交对照图合集。
2. Claude 复查：先用程序查（SHA、型号前缀、乱码、避让区、report.json 的 `table_side_restored` / `table_base_restored` / `caption_unglued` / `images_placed`），再逐页看对照图（放大到 300dpi 看字有没有尖刺，表格四边是否完整）；公差回到原图放大逐行核对。
3. 能放行的用 `tools/feishu_backfill.py` 回填（只追加到「润擎图纸」，回读 SHA256）；有问题的先修规则/模板，更新 `SKILL.md`，再让 GPT 重出。
4. 每次改 `pipeline/cad_family.py` 或品牌配置：`python -m unittest discover -s tests`，再 `python tools/regress.py <回归包目录>`（康生 26 个任务必须 0 差异；基线见 `tools/regress.py` 默认值）。新规则尽量做成**品牌开关**，只对润擎生效；要动康生输出必须先让用户看对比图并确认。
5. 删除/改名飞书附件：只在用户明确授权、且列出具体记录和 file_token 时才做；脚本只追加，不覆盖同名。

## 3. 用户已定的规则（都已写进 SKILL.md，这里是摘要）
- 润擎右栏顺序跟原图（「Pin 表在右上」只是康生的规则）；黑白原图输出全青蓝可接受。
- 型号去掉供应商前缀 `TF-` `ND-` `BG-`（图上和文件名都去，程序自动）；`DC-` `BC-` `PJ-` `FP-` 等是产品类型，保留。文件名一律用 `cad_family.py` 输出的名字。
- 品名：原图有用原图的；没有但型号里写着品类（公座、母座、插座、插头……）就用型号里的；都没有写「连接器」（不 HOLD）。
- 原图公差有空值：按字面照抄（`additional_tolerance_conditions`）；原图写法疑点（单位缺字、温度少负号、尺寸自相矛盾）照搬并回填，另列清单给供应商。
- 一条记录有两份不同原图：都出，文件名加 `-source00` / `-source01`；一页画了两张图：`-sheet00` / `-sheet01`，且每张的 PART NO 要和记录型号对得上。
- 原图已是康生图框的照常转润擎；判框 AMBIGUOUS 且前两名正好是 `TF_tufu` 和 `N_nd_letter`（同一套框）时允许指定 `TF_tufu`，其余认不出的不硬做。
- 备注只追加，润擎用 `tools/feishu_note.py --tag 【润擎图纸】`。

## 4. 进度和待办（截至 2026-10-08）
- 「全图纸」表 1129 条记录（用户还在往里加新产品/图纸，**整理好会通知再继续，别自己开跑**）；润擎图纸字段已有 170 条记录 / 179 个附件，康生图纸 185 条 / 239 个附件。
- 已回填的 38 份里，能找到 job 和原图的 17 份，用当前代码重出，矢量与线上版逐份一致（其余 21 份当时的 job 没留存）。
- 321–370 批：325、326 source01 已回填；345 的 BOM 左边框上两行仍缺（未修）；354 的黑色双线已修（`skip_unstroked_base`，康生同步开启，用户确认过）但未回填，未给用户看对照图。
- 大量 HOLD 是「判框 UNKNOWN_FRAME / AMBIGUOUS」：要补图框指纹或模板（`families/cad_signatures.json`、`cad_templates.json`，只登记看过对照图确认干净的样本）。清单分散在各批汇报里，汇总见 HANDOFF。
- 其他已知问题：SU_sunup 底部 MATERIAL/COLOR 小格缺边、开关图 BOM 右外边只补一半（补边规则只认「三条以上同起点横线」，小格和分段边补不全，需要做表格轮廓闭合检查）；一页两图裁切后丢尺寸/说明（78）；旧公司名残留（100、110）。

## 5. 环境
```bash
export PATH=$HOME/gs-env/bin:$PATH          # Ghostscript
# 虚拟环境：~/ks-venv（PyMuPDF 必须 1.26.5）；中文字体 /System/Library/Fonts/STHeiti Medium.ttc，加 --font-index 0
# 回归包：~/Documents/康生图纸模版/regression_pack.zip（解压到临时目录后 python tools/regress.py <目录>）
python pipeline/cad_family.py job.json --out <目录> --font "<字体>" --font-index 0 --brand runqing
```
- lark-cli 一律 `--as user`；下载路径用相对路径。base / 表 / 字段 ID 不写进仓库（本机 `~/Documents/康生图纸模版/work/runqing-指令/新窗口开场白.md` 里有）。
