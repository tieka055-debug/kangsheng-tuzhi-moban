# 新任务：增加第二个目标品牌「润擎」（RunQing）

目标：同一套流水线，输出图框从「康生」换成「润擎」。供应商原图 → 自动判图框（现有 46 个模板不动）→ 去掉供应商图框 → 搬进**润擎图框** → 回填飞书。
样张（用户用 GPT 做的）：`/Users/vill/Desktop/RunQing_ND-D27M-12Pin-R2_A版原模板_矢量样张.pdf`（原图是飞书里的 D27M-12Pin-R2.pdf）。**样张不进仓库**，只读它。

## 现状：哪些能复用，哪些是康生专用
- 能复用（不要改）：`pipeline/frame_match.py` + `families/cad_templates.json` + `families/cad_signatures.json`（源图图框判定与去除供应商标题栏/修订栏）、颜色/线宽处理、视图/右栏排版逻辑。
- 康生专用（要抽成「品牌配置」）：
  - `engine/frame.py`：`BLUE`、`FRAME`、`TITLE_BOX`、`TOLERANCE_BOX`、`PROJECTION_BOX`、`draw_frame_and_title()`（画框、格号、标题栏、品牌条图片）。
  - `pipeline/cad_family.py`：`RESERVED`（康生标题栏+公差栏区域）、`RAIL`（右栏 600,38,814,444）、`GOLD`、右栏加宽上限 524、底部槽位等写死的坐标；输出文件名里的「康生图纸」。
  - `assets/brand-strip.png`、`engine/dynamic_tolerance.py`（康生英文疏排公差栏）。

## 润擎样张的版面（从样张读出来的）
- 页面 A4 横 841.89×595.28；外框橘色（≈#D7974C），内框深青蓝（≈#214E68，样张里 95% 的线条是这个色）；上下左右有格号 1–7、A–E。
- 左上：标题「CONNECTOR ENGINEERING DRAWING」+ 下一行「型号 | 电压 / 电流」（橘色细线下划）。
- 右上：REV / DESCRIPTION / DRAW / DATE 修订栏；其下「SPECIFICATIONS / 技术规格」、ELECTRICAL CHARACTERISTICS、RoHS COMPLIANT 徽标、MATERIAL / PLATING。
- 右中：BILL OF MATERIALS / 材料明细（NO./PART/MATERIAL/QTY/FINISH）。
- 右下：标题栏——润擎 logo（样张里是嵌入图片）+「东莞市润擎电子科技有限公司 / Dongguan Runqing Electronics Co., Ltd.」，PART NAME、SOURCE REF. P/N、UNIT/SCALE/REV/SOURCE DATE/PAGE，GENERAL TOLERANCE（≤5 ±0.2、>5-30 ±0.3、>30 ±0.5、ANGLE 0°±3°）。
- 左下：COPYRIGHT RESERVED, PLEASE DO NOT COPY.；右下角投影符号。

## 必须先让用户拍板的一件事（别自己定）
样张右栏的 SPECIFICATIONS 和 BOM 是**重新排版的英文文字**，不是把原图那块矢量搬过来。本项目有铁律「技术内容只搬运原矢量，不重打，不跨型号补值」。两种做法：
- **A（推荐，先做）**：和康生一样，只换图框；原图的规格/材料/零件表原样搬进润擎右栏。安全，不会抄错。右上「SPECIFICATIONS」等标题栏位按原图有什么放什么。
- **B（以后可选）**：规格和 BOM 由**飞书表的结构化字段**（额定电流/额定电压/耐压/接触电阻/绝缘电阻/工作温度/端子材质/胶壳材质/电镀…）重新生成。表里数据「还很乱」，必须逐条人工复核，不要默认开。

## 做法
1. 先读 `docs/HANDOFF.md`、`SKILL.md`、`pipeline/cad_family.py`、`engine/frame.py`。
2. 新增品牌配置（例如 `brands/kangsheng.json`、`brands/runqing.json`，或 Python 里一个 `BRANDS` 字典）：frame 坐标、标题栏/公差栏位置、右栏范围 `RAIL`、保留区 `RESERVED`、主色/辅色、logo 资源、输出后缀。**康生配置必须和现在写死的值完全一致。**
3. `cad_family.py` 加 `--brand kangsheng|runqing`（默认 kangsheng，不传 = 行为完全不变）。`engine/frame.py` 的画框函数按品牌配置画；润擎版新建 `draw_runqing_frame_and_title()`，样张的 logo 图片从样张里导出为 `assets/runqing-logo.png`。
4. **每改一步都跑回归**：`python tools/regress.py <回归包目录>`（回归包在 `~/Documents/康生图纸模版/regression_pack.zip`，解压后用），必须保持 **26 个任务 0 差异**，和 `python -m unittest discover -s tests`。康生输出一个字节都不能变。
5. 用样张验收：原图 `D27M-12Pin-R2.pdf` 跑 `--brand runqing`，和样张逐项对照（格号、颜色、标题栏、logo、公差表、右栏位置），把「原图|润擎图」对照图发给用户看。
6. 先对 5–10 张不同图框的图出润擎版，用户确认版式后，再让 GPT 批量（见下）。

## 环境
- `export PATH=$HOME/gs-env/bin:$PATH`（Ghostscript）；仓库 `.venv` 缺依赖，另建环境：`python3 -m venv ~/ks-venv && ~/ks-venv/bin/pip install -r requirements.txt`。
- 提交信息末尾带 `Co-Authored-By` 署名行；用户已授权本项目验证后提交并推送。

---

# 给 GPT 的批量指令（品牌功能做好、用户确认版式后才发）
沿用 `docs/GPT_BATCH_PROMPT.md` 的全部规则，仅有三处不同：
1. 命令加 `--brand runqing`。
2. 文件名后缀、回填目标字段换成「润擎图纸」字段（让用户在飞书里新建一个附件字段，并把字段 ID 给你）；`feishu_backfill.py` 的 `--target-field-name` 同步改。
3. 仍然是**先 5 条试跑、对照图发用户确认、每 20 条一批确认后才回填**；认不出图框的记入待处理清单，不要硬做。
