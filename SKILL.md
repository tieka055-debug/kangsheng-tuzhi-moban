---
name: kangsheng-tuzhi-moban
description: 把质源供应商的单页矢量 PDF 工程图批量转成康生品牌图纸。程序自动算出全部裁切和排版，引擎门禁保证内容零遗漏；每张只需读一次公差格小图、看一眼对照图。
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
