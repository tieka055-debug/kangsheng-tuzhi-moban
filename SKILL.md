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
3. **选模板。** 按下表的图框描述选；拿不准就先用 `tools/grid_preview.py 原图.pdf 预览.png` 看网格。都不像 → 新建模板（见下）。
4. **读公差。** 放大原图的公差格（例如 `page.get_pixmap(dpi=600, clip=...)`），逐行照抄到 `tolerance`。字体不支持的符号（如 `≤`、`∠`）改写成 `0~5`、`ANG`，数值不变。
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
   缺图/原图损坏的记录：`python tools/feishu_note.py --base … --table … --notes notes.json`（在「备注」后面追加说明，不改原内容）。
   base/table/字段 ID 属于业务数据，运行时传参，不写进仓库。

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
| `N_nd_cad2pdf` | 诺德 cad2pdf 图框（字母列头 F…A，右上 RoHS+修订栏，底部整条公司名/标题栏）；一页多图时用 job 的 clip 指定单张 |

**新建模板：** 用 `tools/grid_preview.py` 看网格，把供应商标题栏、修订栏、RoHS、水印等区域按内框的比例写成 `furniture_frac: [[x0,y0,x1,y1], ...]`（0–1）。同一供应商同一图框只写一次。

### 处理原则

- 颜色：黑/灰/绿和主标注色 → 康生蓝；其他彩色（端子、焊盘）→ 康生金。
- 排版：视图保持原图相对位置，整体等比放大；右侧说明/尺寸表/材料表放右栏（最宽到 x=524）。**固定规则：右上角放 Pin 数尺寸表，紧接着下面放性能参数/说明**（模板开关 `rail_stack`；原图里表和说明左右并排时必须打开）。
- 原图写错的（尺寸表删除线、数量和 Pin 数不符等）照原样保留，报给人核对，不自行改。
- DWG 转换后某张图的文字/表格缺失（和同系列其他张对比能看出），这张不出图，备注「原图损坏，请供应商重发」。**不从别的型号抄。**
- 改了 `cad_family.py` 或模板：把以前做过的图全部重跑一遍，逐张比对输出，任何一张变了都要看过确认。新规则尽量做成模板开关，只对新图框生效。
