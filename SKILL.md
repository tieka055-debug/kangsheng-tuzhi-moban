---
name: kangsheng-tuzhi-moban
description: 把供应商的连接器工程图（质源单页矢量 PDF、其他供应商的 cad2pdf/DWG）批量转成康生品牌图纸，并回填飞书。程序自动裁切排版；每张只需选模板、读一次公差、看一眼对照图。
---

# 康生图纸：自动出图（第 2 版，2026-09-25）

**一句话流程：** 跑 `draft` → 读公差小图，填 `tolerance.json` → 跑 `finish` → 看对照图。
同一供应商的新型号，**不再**由模型逐张找坐标、写清单、调布局。

## 1. 准备（一次）

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # PyMuPDF 必须是 1.26.5
```

标题字体用一个中文字体文件：

- Mac：`/System/Library/Fonts/STHeiti Medium.ttc`（加 `--font-index 0`）
- Linux：任意 Noto Sans CJK SC 字体

程序会按每张图的品名和图号自动截取字形，生成独立的 TTF。

## 2. 第 1 步：出草稿并分诊

```sh
python pipeline/run_batch.py draft <原图.pdf 或文件夹> --out <输出目录> --font <字体> [--font-index 0]
```

每张图约 10–30 秒。输出目录里每张图一个文件夹，内含 `review-sheet.png`（原图 | 草稿）、`tolerance.json`（待填）、`*/field-packet/`（公差格和图幅格小图）。
另有汇总表 `batch-summary.csv`。

| 分诊 | 含义 | 怎么做 |
|---|---|---|
| AUTO_OK | 全部门禁和检测器通过 | 继续第 3 步 |
| REVIEW | 门禁通过，但有值得看一眼的地方（原因写在表里） | 看 `review-sheet.png`，没问题就继续 |
| EXCEPTION | 引擎门禁没通过，程序停下 | 不要整页重做，见第 6 节 |

## 3. 读公差（每张约几秒，任何模型都能做）

打开 `*/field-packet/tolerance-cell.png` 和 `size-cell.png`，填写该图的 `tolerance.json`：

```json
{"status": "READ", "reader": "你的名字或模型名", "size": "A4",
 "linear_tolerances":  [{"tier": "X.", "value": "±0.35"}, {"tier": "X.X", "value": "±0.25"}, ...],
 "angular_tolerances": [{"tier": "X.°", "value": "±4°"}, ...],
 "additional_tolerance_conditions": []}
```

规则：

- **只读本图**，逐行照原图写，行数和顺序都保持原样。
- **不从相似型号抄值。**
- 原图的写法照抄，不自行"纠正"（例如 `X.XX` 重复出现、角度档写成 `X.XX ±2°`）。
- `text_layer_hint` 是程序从文字层读到的部分值，只作参考。公差数值多数是 CAD 线条画出来的，必须看图读。

## 4. 第 2 步：套英文公差栏，出最终草稿

```sh
python pipeline/run_batch.py finish <输出目录>
```

产出：

- `<图名>-康生草稿.pdf`
- `<图名>-原图对照.png`

引擎会重新跑全部门禁，并逐行核对英文公差栏与 `tolerance.json` 完全一致。

## 5. 看图（每张约 1 分钟）

打开 `<图名>-原图对照.png`，确认三件事：

1. 内容齐全，没有被拆散。
2. 版式合理：型号表在右上，性能/NOTES 在右中。
3. 公差栏与原图一致。

**程序只能核对"英文栏 = 你填的值"，核对不了"你填的值 = 原图"，所以第 3 项必须由人看。**

## 6. 例外怎么处理

程序会写明原因，常见几类：

- 原图技术内容压到了标题栏框线上。
- 图面过密，排不下。
- 型号表太宽，右栏放不下。

处理时只改出问题的那几个块：以该图 `single/manifest.json` 为起点，改对应组的 `clips`、`dst` 或 `scale`，再运行：

```sh
python engine/kangsheng.py draft <manifest> --output <新目录> --control-root <目录>
```

**不要为单张图改族配置。**

## 已确认的规则（用户 2026-09-25 认可）

- **颜色**：黑色、灰色 → 康生蓝；**所有其他颜色** → 康生金（配置名 `nonblack-gold-v1`）。
- **内容第一**：放不下时，先缩小右上型号表和右中性能块（性能块文字最低 6pt），**绝不删减**任何产品图形或尺寸。视图和 PCB 保持 1:1。
- **右栏加宽**：型号表太宽时，右栏可以向左加宽，最多到 x=524pt。
- **原图字号小于 4.75pt 时**：下限改为"不比原图更小"。
- **公差**：统一用英文疏排栏（`UNLESS OTHERWISE SPECIFIED, TOLERANCE:`）；有角度公差时排两列。
- **外框不搬**：型号表等区块贴着供应商外框时，只搬到内框线为止，外框线和格子编号（A–F、1–8）不带进康生图；若切掉会丢内容，保持原样并标 `BLOCK_ENTERS_FRAME_BAND` 供人看。
- **水印**：Autodesk 教育版水印文字对象会被精确删除。程序会验证其余 1000 多个矢量对象和全部文字都没有变化。

## 不变量

- 原图只读。技术内容只搬运原矢量，不重打，不跨型号补值。
- 引擎门禁不放宽：整页未放置墨迹为 0、不切字、不重叠、逐片像素保全、整页比对。
- 程序不写发布 PASS，也不产生 `release.json`。

## 换供应商

1. 复制 `families/zhiyuan.json`，改其中的标题栏标签词、水印词、区块提示词。**配置里只放相对规则，不放任何单张坐标。**
2. 先拿 10–20 张该供应商的图跑一遍，确认没有"门禁通过但内容有错"的图，才能投入生产。

## 修改代码或配置后

1. 运行 `python -m unittest discover -s tests`。
2. 在本机回归清单（私有，不放进仓库）上重跑，与上一版的分诊结果对比。**任何一张图变差，都不得合入。**

## 通用 CAD 路线（非质源供应商，或质源自动流程做不出来的图）

适用：cad2pdf 导出的 PDF（文字是线条）、DWG、带文字层的 CAD PDF。**每张图的判断只有 4 件事：选模板、读公差、定型号名、看对照图。** 不写坐标。

### 步骤

1. **拿到矢量原图。** 飞书「原始」字段里的 DWG/PDF。只有 PNG/JPG 或扫描 PDF（`page.get_drawings()` 为空）→ 不做，备注「缺原图」。
   DWG 先转：`python pipeline/dwg_to_pdf.py 原图.dwg 原图.pdf`（需要 LibreDWG 的 `dwg2dxf` 和 `ezdxf`）。
2. **一页里有几张图？**
   - 横排/竖排多张（DWG 常见）：`python pipeline/split_sheets.py 原图.pdf 拆分目录` 拆成单张。报 `WIDE_SEGMENT` 的是两张图框贴在一起，看一眼再定切分位置。
   - cad2pdf 一页并排两张：不拆，job 里加 `"clip": [x0,y0,x1,y1]`。
   - 拆出的每张要和飞书 2D 图逐张对上（图名、Pin 数），顺序不能想当然。
3. **选模板。** 先让程序自动判：`python pipeline/frame_match.py 原图.pdf`（按图框长线条的几何和已做过的图框比对，只用线条，不读文字），job 里 `"template": "auto"`（或不写）时 `cad_family.py` 自己调用它。结果 `OK` 才继续，模板和旋转都会自动用；`AMBIGUOUS`/`UNKNOWN_FRAME`/`NO_FRAME` → **不硬做**，按下面「新建模板」做模板，再用 `frame_match.py --learn 模板名 原图.pdf [--rotate N] [--search 0.3]` 登记；对不上的图不要套最像的模板（会留下供应商标题栏残影）。手动选也行：按下表的图框描述选，拿不准先用 `tools/grid_preview.py 原图.pdf 预览.png [--rotate N] [--search 0.3]` 看网格。
   - **标题**用图纸自己的品名（例如 DC 电源插座、电池连接器、USB 连接器），不要用默认的「连接器」；原图没写品名但型号里写着（公座、母座、插座、单边母座…）就用型号里的；都看不出来就问人。
   - 原图已经是康生图框（标题栏写「深圳市康生电子科技有限公司」）的，不用再转。
4. **读公差。**（供应商 CAD 字体有时把 ± 和 ° 编成 GBK 乱码，显示成 `¡À`、`¡ã`：这是编码错误，照抄成 `±`、`°`；程序也会自动还原，数值不变。） 放大原图的公差格（例如 `page.get_pixmap(dpi=600, clip=...)`），逐行照抄到 `tolerance`。字体不支持的符号（如 `≤`、`∠`）改写成 `0~5`、`ANG`，数值不变。
5. **写 job.json 并运行：**
   ```json
   {"source": "原图.pdf", "model": "型号", "title": "电池连接器", "template": "模板名",
    "tolerance": {"linear_tolerances": [{"tier": "X.", "value": "±0.3"}], "angular_tolerances": [{"tier": "ANGLE", "value": "±3°"}], "additional_tolerance_conditions": []}}
   ```
   ```sh
   python pipeline/cad_family.py job.json --out 输出目录 --font 字体 [--font-index 0]
   ```
   可选键：`rotate`（原图横放时 90/270）、`clip`、`frame_bottom`（`title_top`：标题栏整宽时内框底边取标题栏顶线；`inner_ring`：取外框往里第二条线）、`dominant_colour`（例如 `[1,0,0]`：指定哪种颜色当标注色转蓝；同系列多张图要统一）、`furniture_frac`（临时覆盖模板）。
6. **看图。** 打开 `*-原图对照.png`，逐项核对：视图、尺寸、说明/材料、型号表、PCB 布局都在；没有残留供应商标题栏/修订栏/RoHS；没有多余的长线；标注是蓝色、端子/焊盘等重点是金色。
   有问题先查原因（模板比例、`frame_bottom`、`dominant_colour`），不要为单张图改代码。
7. **回填飞书：** `python tools/feishu_backfill.py --base … --table … --field … --plan plan.json`（只追加，按 2D 图名核对，回读校验）。
   缺图/原图损坏的记录：`python tools/feishu_note.py --base … --table … --notes notes.json [--tag 【润擎图纸】]`（在「备注」后面追加说明，不改原内容；润擎必须加 `--tag 【润擎图纸】`）。
   base/table/字段 ID 属于业务数据，运行时传参，不写进仓库。

### 目标品牌（`--brand`，2026-10-02 起）

同一套流水线可以输出两个品牌的图框，**源图判图框（`frame_match.py`、`cad_templates.json`、`cad_signatures.json`）两个品牌共用，不分品牌**：

| 品牌 | 命令 | 输出 |
|---|---|---|
| 康生（默认，不传 `--brand`） | `python pipeline/cad_family.py job.json --out … --font …` | `<型号>-康生图纸.pdf`、`<型号>-原图对照.png` |
| 润擎 RunQing | `… --brand runqing`（必须显式写） | `<型号>-润擎图纸.pdf`、`<型号>-原图润擎对照.png` |

- 品牌配置在 `brands/<品牌>.json`（frame、标题栏/公差栏、右栏 `rail`、保留区 `reserved`、主色/辅色、logo、输出后缀），由 `engine/brand.py` 读取。**`brands/kangsheng.json` 的值必须与原先写死的完全一致**（`tests/test_brands.py` 守着）；改 `cad_family.py` 后仍跑 `tools/regress.py`，26 个任务 0 差异。
- 润擎图框由 `engine/frame.py` 的 `draw_runqing_frame_and_title()` 画：橘色外框(#D7974C)+青蓝内框(#214E68)、格号 1–7/A–E、左上 CONNECTOR ENGINEERING DRAWING+型号、右上空的 REV/DESCRIPTION/DRAW/DATE 修订栏、右下 logo(`assets/runqing-logo.png`)+公司名+PART NAME/SOURCE REF. P/N/UNIT·SCALE·REV·SOURCE DATE·PAGE+GENERAL TOLERANCE、左下 COPYRIGHT、投影符号。
- **做法 A（只换图框）**：规格/材料/零件表按原图矢量原样搬进右栏，不重打、不跨型号补值；样张里的 SPECIFICATIONS/BOM 英文重排、`12 V / 12 A`、SOURCE DATE、SCALE、REV 值不生成（飞书表字段重生成那条路 B 未开）。标题栏 PART NAME 用图纸自己的品名，SOURCE REF. P/N 和左上型号都用 job 的 `model`，**但供应商自己的型号前缀 `TF-`/`ND-`/`BG-` 由程序自动去掉**（`brands/runqing.json` 的 `model_strip_prefixes`，用户 2026-10-03 指定；`DC-`/`BC-`/`PJ-`/`FP-` 是产品类型，保留；输出文件名同样去掉，`report.json` 记 `model_in_job`/`model_on_sheet`）。原图矢量里画着的供应商型号（视图里、表格里）是技术内容，不改。
- 公差：写 job 的 `tolerance`（只抄本图），润擎公差栏宽，档位名可以写原图原样；康生栏窄，同一份 job 两个品牌都出时用 `0~5` 这类短写。
- 颜色映射同康生：黑/灰/主标注色→品牌主色，其他彩色→品牌辅色（润擎为橘色）。
- **润擎的右栏顺序跟原图走**（用户 2026-10-03 定）：「Pin 表在右上、说明紧接其下」是康生的固定规则，润擎不套用；原图右栏说明在上、表在下的，润擎照原图顺序，不算问题、不 HOLD。
- **黑白原图**（全部线条黑/灰、没有单独的端子颜色）：润擎输出全青蓝、没有橘色重点，是正常结果，不 HOLD（用户 2026-10-03 确认）。
- 润擎品牌开关 `caption_unglue`：图下方的说明（如 RECOMMENDED PCB LAYOUT）因为离下面的零件表太近被并进表格时，拆回给上方的图（report 记 `caption_unglued`）。康生不开：康生标题栏位置不同，开了会让视图变小。
- 润擎配置打开了 `line_extent_fix`（右栏顶上有修订栏，范围不能算小）并用 `keepout` 让视图避开投影符号；质源 `run_batch.py` 流程只支持康生。两个品牌请输出到不同目录。

### 图框模板（`families/cad_templates.json`）

| 模板 | 图框 |
|---|---|
| `A_letter_frame_parts_table` | 字母列头外框，底部整条标题栏，右上 SYMBOL/REVISION 修订栏（Y.C.Zhang 款）；RoHS 标记一并去掉（用户 2026-09-26 要求） |
| `B_yellow_grid_wjh` | 黄色 1–7 格外框，右下阶梯标题栏，右上 REV/DESCRIPTION 修订栏（W.J.H 款） |
| `A_rohs_top_left` | A 款变体：RoHS 标记在左上角（诺德/BG 系列） |
| `K_ktl_structure` | 凯拓林「结构图面」：右下 QUALITY/TOLERANCE 标题栏，右上「结构图面」+ 修订栏，左上 RoHS |
| `N_nd_letter` | 诺德/康生旧款：字母列头外框，底部整条公司名标题栏，右上 SYMBOL/REVISION 修订栏，左上 RoHS |
| `N_nd_dwg_sheet` | 诺德/北冠 DWG 多图单页：字母列头外框，底部标题栏+公司名在内框外，左上 RoHS，右上 SYMBOL/REVISION 修订栏；零件表保留（需 frame_bottom=title_top） |
| `N_nd_dwg_tall` | 诺德 DWG 单页（标题栏整宽横线，需 frame_bottom=inner_ring）：RoHS 与修订栏并排在右上，底部公司名+标题栏 |
| `BG_dwg_sheet` | BG DWG 多图单页：右上 SYMBOL/REVISION 修订栏，左上 RoHS，底部标题栏在内框外（需 frame_bottom=title_top） |
| `Z_zhiyuan_cad` | 质源 CAD 图框（横向阅读）：右下标题栏+公差栏；四边「由 Autodesk 教育版产品制作」水印；左下零件/尺寸表保留 |
| `Z_zhiyuan_cad2` | 质源 CAD 图框（cad2pdf 文字为线条的版本，标题栏略低）：同 Z_zhiyuan_cad |
| `D_dingduan` | 鼎端电子图框：右上 REV 修订栏，右下标题栏+公司 logo，左下 Recommended P.C.B Layout 说明保留 |
| `LF_lvfeng` / `Y_yonghui_dc` / `F_fuping_micro` / `GGD_gaogaoda` / `XZ_xiezhan` / `CY_chuangyue` / `AL_ailiante` | 绿丰、永辉DC、创勤阜平、高高达、协展、创业、艾联特 各家的图框（各自需要的 rotate/layout 见 `desc`）；绿丰按固定规则把 PIN 表放右上、性能参数紧接其下 |
| `SU_sunup` | SUN UP（Sanap）图框：整页外框，右上 REV/ECN/DATE，底部整条标题栏（2006/2008/2540/2501 系列） |
| `KH_keheng` | 惠州科横/科衡图框：8 格数字/字母框，右上修订栏，顶部 RoHS 小框，右下标题栏 |
| `RQ_runqing` | 润擎/鼎端 B01M 系列图框：黄色外框+黑色内框，框外上方有超大型号字，需 `frame_search` 0.3；右上修订栏，右下 logo+标题栏 |
| `N_nd_cad2pdf` | 诺德 cad2pdf 图框（字母列头 F…A，右上 RoHS+修订栏，底部整条公司名/标题栏）；一页多图时用 job 的 clip 指定单张 |
| `KS_kangsheng_old` | 康生旧款图框（深圳市康生电子科技有限公司，Foxit 编辑过）：左上「客户确认签章处」，右上修订栏，右下公司名+TITLE/PART No+GENERAL TOLERANCE 标题栏；文字为细小三角形轮廓，模板开关 `round_joins`（圆角连接，避免字形出毛刺）、`views_top`（视图组靠上排，只让底部避开康生标题栏） |
| `TC_grid_bomleft` | 字母/数字格外框（F…A / 9…1），右上 REV/ECN/APPD 小修订栏，左上 RoHS，右下 TOLERANCE+APPROVED 标题栏；左下零件/材料表保留（Type-C/USB）；点胶网点是极小描边三角形，需 `round_joins` |
| `LH_dc_vertical` | 乐鸿/竖排 DC·耳机插座框（原图竖放，rotate 270）：底部整条标题栏，右上「一般尺寸公差」表，左上小空框；右下零件表保留 |
| `DC1_bom_br` / `DC2_leftcol` / `DC3_jack_en` / `DC4_std_br` | 四种 DC 插座框（0185 / 0189 / 0279 / 0241 型）：右下或底部标题栏+公差栏去掉，零件表保留；前三种原图竖放 rotate 270；DC2/DC3/DC4 用 `layout: "sheet"`（右栏逻辑会把小符号放得过大） |
| `LF_lvfeng_inner` | 绿丰图框变体：零件表+标题栏在内框下沿之内，模板自带 `frame_bottom: "title_top"`、`layout: "sheet"`、`width_cap` 1.2（不加 width_cap 会有线变粗） |
| `CQ_chart_dwg` | 创勤/鑫风雷 USB 图框（原图竖放，rotate 270）：右下「产品图 PRODUCT CHART DWG+公差一览表+公司名」标题栏，右上 MAPX/MODIFICATION 修订栏，左上 RoHS+客户图；检测到的内框含格号带，用上下窄带去掉；`layout: "sheet"` |
| `WJ_black_dwg` | 黑底 DWG 导出图框（白框线/绿尺寸/洋红端子，页面 7257×4535）：顶部 RoHS Compliant 小框、右上修订栏、底部标题栏+COPYRIGHT；标题栏顶线分两段，不能用 title_top |
| `MU_black_cadgen` | 黑底白线 CAD 图框（页面仅 193×138，粗白线）：左上 CAD GENERATED 备注、右上 REV./ECN NO. 修订栏、右下标题栏；零件表保留 |
| `LT_lituo` | 利托(LITUO)微动开关图框：左上「CAD FILE:」小框、右下标题栏（TOLERANCE/修订/公司 logo/签名）；左下零件表、右上 SCHEMATIC 框保留；字是粗描边三角形，需 `round_joins` |
| `HL_holy` | 宏利(Holy)图框：字母/数字格号外框，右下标题栏（公司名/一般公差/检验标示）+底部修订栏；右侧 P数/A/B 尺寸表保留（标题栏上沿要放在表底与标题栏顶线之间，取 0.82） |
| `PS_pinshang` | 品尚图框（凯拓林「结构图面」同款，标题框更高）：右下 QUALITY/TOLERANCE 标题栏，右上「结构图面」+修订栏，左上 RoHS |
| `LH_dc_upright` | 乐鸿 DC 插座框的「不转、整页布局」版：原图是内外双框，需 job `clip` 圈住外框（如 `[5,223,591,620]`），右栏重排会拆散说明文字所以用 `layout: "sheet"` |
| `LP_lianpan` | 联攀/联邦(LianPan·LiaoFeng)图框：左上 RoHS Compliant、右上 REV./LOCAS. 修订栏、底部整条标题栏；NOTES、Part No./Dim 表保留；需 `round_joins` |
| `ZY_bc_landscape` | 质源 BC 系列图框（同一模板横放不转、竖放 rotate 270 都能用）：右下标题栏（未注公差/公司名/品名/图号），底边格号带用窄带去掉；Pin数/PART NO./DIM 表保留 |
| `WJ2_yellow_a4p` | W.J.H 黄网格框的 A4 竖页版（横框画在竖页中下部，文字浅绿细线） |
| `TF_tufu` | 途富电子(东莞)图框：RoHS 左上、修订栏右上、底部整条标题栏+COPYRIGHT；零件表保留，`bottom_slot` 把它挪到康生标题栏左边（页面左边被裁的量不同时 `ND-…2Pin` 一类图的零件表下两行会丢，见 HANDOFF） |
| `DD2_dingduan_a4p` | 鼎端 A4 竖页版（框外上方有大号「端子: 型号」，需 `frame_search` 0.3，模板自带；登记指纹时也要加 `--search 0.3`） |
| `WD_weiding` | 伟定(WD)图框：右上 REV/ECN/DRAWER 修订栏，右下 logo+电话+标题栏；Notes、订购编号表保留 |
| `CEN_cenlink` | 欣訊(CEN LINK)图框：右上 REV/DESCRIPTION/ECN/DATE/DRAWER 修订栏（可达 3 行，取到 0.092），右下 CEN logo+标题栏；Notes（含订购编号图）保留 |
| `DT_dingte` | 鼎特(Dingte)图框：整页外框，右上小修订栏，右下标题栏；四边 Autodesk 水印用边缘窄带去掉（水印是文字轮廓，没有专门的水印逻辑） |

**模板开关（写在模板里，只对该图框生效）：** `bottom_slot`、`table_band_top`、`frame_pad`、`rail_cap`（右栏字不超过图的放大倍数×该值）、`join_line_pieces`、`split_paths`、`rail_stack`、`line_extent_fix`、`width_cap`（输出线宽上限）、`layout: "sheet"`（保持整页布局，不拉右栏）、`fit_search`（在标题栏外找最大可放缩放；不写时若普通缩放放不下会自动启用）、`frame_bottom`（模板也可写，job 里的优先；自动判图框时只能靠模板），`rail_pull`（[[x0,y0,x1,y1],…]：这些区域的东西拉到右栏最上面，例如 PIN 表）、`rail_margin`、`frame_search`（内框搜索带，默认 0.12；大字写在框外的图用 0.3）。

**表格贴着供应商图框边**：零件表/Pin 表画到图框左右边时，原图常拿图框线当表格外边框，去框后会缺边。程序会在「三条以上同起点的表格横线都止于图框左右边」时补回这段边线（report 里 `table_side_restored`，2026-10-03 起，两个品牌都生效）。看对照图时仍要核对表格四边完整。

**算范围要用线段端点**：0 宽发丝线的 `rect` 高度为 0，`Rect |= rect` 会把它当空矩形忽略，会低估内容范围（clip 取小了会让长线整条被排除、`FRAME_NOT_FOUND`）。

**新建模板：** 用 `tools/grid_preview.py` 看网格，把供应商标题栏、修订栏、RoHS、水印等区域按内框的比例写成 `furniture_frac: [[x0,y0,x1,y1], ...]`（0–1）。同一供应商同一图框只写一次。

### 处理原则

- 颜色：黑/灰/绿和主标注色 → 康生蓝；其他彩色（端子、焊盘）→ 康生金。
- 排版：视图保持原图相对位置，整体等比放大；右侧说明/尺寸表/材料表放右栏（最宽到 x=524）。**固定规则：右上角放 Pin 数尺寸表，紧接着下面放性能参数/说明**（模板开关 `rail_stack`；原图里表和说明左右并排时必须打开）。
- 原图写错的（尺寸表删除线、数量和 Pin 数不符等）照原样保留，报给人核对，不自行改。
- DWG 转换后某张图的文字/表格缺失（和同系列其他张对比能看出），这张不出图，备注「原图损坏，请供应商重发」。**不从别的型号抄。**
- 图框判别库 `families/cad_signatures.json`：每个已验证图框存一个 3200 位的指纹。只登记看过对照图确认合格的图；不合格的图框保持「对不上」，不要登记。
- 改了 `cad_family.py` 或模板：把以前做过的图全部重跑一遍，逐张比对输出，任何一张变了都要看过确认。新规则尽量做成模板开关，只对新图框生效。
