# Manifest v2

新任务使用 `schema_version: 2`。v1 仅供旧清单兼容，不具有 v2 的客观整页覆盖、可读性和双向整页 QA。坐标单位为 PDF point，原点在左上；源坐标基于 `source.rotation` 烘焙后的可见页。

## 基本结构

```json
{
  "schema_version": 2,
  "source": {"path":"SOURCE.pdf","sha256":"SHA256","page":1,"rotation":270,"expected_pages":1},
  "renderer": "auto",
  "identity": {
    "record_id":"STABLE_RECORD_ID","expected_model":"MODEL","observed_model":"MODEL","model_evidence":"完整原页证据",
    "observed_parts":["PART-NO"],"part_pattern":"REGEX"
  },
  "fields": {
    "model":"MODEL","title":"TITLE","scale_text":"1:1","unit":"mm","sheet":"1/1",
    "tolerances":[],"no_tolerance_block_reason":"源图无公差块"
  },
  "assets": {"background":"BACKGROUND.png","brand_strip":"BRAND.png","font":"FONT"},
  "groups": [{
    "id":"front_view","kind":"view","clips":[[100,100,240,200]],
    "reviewed_source_extent":[100,100,240,200],"dst":[40,80],"scale":1.0
  }],
  "coverage": {
    "mode":"full-page-minus-exclusions",
    "exclude":[{"box":[0,0,842,29],"kind":"outer_frame","reason":"源页外框"}]
  },
  "review": {
    "source_sha256":"SHA256","source_inventory_sha256":"SOURCE_INVENTORY_SHA256",
    "reviewer":"REVIEWER","reviewer_run_id":"SOURCE-REVIEW-RUN",
    "reviewer_role":"source_inventory_reviewer","coverage_basis":"uncropped-full-sheet",
    "full_page_reviewed":true,"verdict":"PASS"
  }
}
```

- `observed_parts` 为原表全部型号；原图无表时改填 `identity.no_part_table_reason`。
- `identity.record_id` 必须是核验过的稳定产品记录 ID（字母、数字、点、下划线或连字符），批次 `jobs[].id` 必须与之完全一致。
- `fields.tolerances` 在 v2 必须为空。默认将公差值作为 `tolerance` 原矢量组保留；已批准英文重排模式则由 `approved_tolerance_reflow` 指向逐型号原图字段映射、认可版布局和认可 PDF，三份证据均绑定 SHA256。原图确无公差块时填 `no_tolerance_block_reason`。
- `renderer` 可为 `auto` / `native` / `svg`。通常品牌图有效分辨率至少 150 DPI，标题字体必须覆盖所有字符。
- `stroke_profile` 缺省为 `source`，保留原源线宽；`legacy-thin-stroke-boost-v1` 只用于与逐源用户认可成品核对过的旧打印兼容个案，且必须随配方/缓存绑定。不得作为新图纸通用修饰。
- 唯一兼容例外是用户已认可第 14–18 行使用的原版 `assets/brand-strip.png`：597×92 像素，SHA256 `436ab38839ec933f3c295c9b17005cb8942b16245715073c7e69da1bd5d16078`。标准位置有效分辨率为约 127.1 DPI；审核报告如实标记 `approved-native-original` 和未达到通常 DPI 阈值。这是恢复已认可原资产，不是分辨率提升；其他低分辨率文件仍被拒绝。

## 内容组

`kind` 只能为 `view`、`isometric`、`pcb`、`table`、`performance`、`note`、`projection`、`tolerance`。

- `clips` 是同组原区域；`dst` 对应联合外框左上角，同组多片共用一个等比变换。
- `reviewed_source_extent` 是审核者在完整原页上标定的最小完整技术外框。裁切携带该范围外墨迹或切断文字会停止。
- `view` / `pcb` 默认 `scale: 1.0`。例外必须用 `approved_uniform_view_scales` 绑定同源认可 `layout.json` 与 PDF 哈希，并把 `fields.scale_text` 标为 `NTS`；各组仍只能单一等比缩放，禁止 `scale_x`/`scale_y`/`stretch`/隐藏变换。其他组缩放后的可提取技术文字不得小于 4.75 pt。
- 组不得进入排除区，不得与其他组真实墨迹重叠。
- 表外框与源页框共线时，`table` / `tolerance` 可用最多一条水平和一条垂直 `restored_source_rules`；只能恢复无文本闭合线。
- 恢复线可标 `line_cap: butt|round|square`，默认 `butt`。仅当原 PDF 矢量笔画可核验为圆端帽或方端帽时才设置对应值；源覆盖区与输出矢量均按所填端帽和原线宽计算，不能用端帽扩大覆盖去掩盖其他遗漏。
- 表格恢复线裁区须完全避开原笔画；输出矢量审计要求该目标线恰好出现一次，端点、线宽、端帽和颜色一致，原线与恢复线重画叠加会失败。
- v2 的 `tolerance` 最多一组（可含多片），完整目标框及恢复线笔画必须在底部专用槽 `[400,493,488,564]` 内。默认模式原值与原表直接搬运。`approved_tolerance_reflow` 模式保留该源组作为覆盖与原始像素差证据，但在成品中只画认可英文外框、标题及四档字段；源映射必须含原图页/裁区指纹、逐档值与单位、独立字段复核哈希。原表内额外档位或条件非空时拒绝重排；其他技术组内的附加条件须单独绑定独立证据，并由该组源矢量 QA 覆盖。英文栏实际文字与认可 PDF 局部像素双重核对；源组原始像素差仍记录，其他组及整页检查不降阈值。源审核、终审继续独立执行。
- 用户要求去掉空白公差格时，先查未裁切原件及放大格内墨迹，确认只有空网格而无字/数/符号。用多片 `clips` 保留完整原表头及有值部分，把纯空格框登记为有理由的 `nontechnical_annotation` 排除区并重做源清单审核。不能只靠文字提取为空认定为空，也不能套用其他型号的空列判断。
- `projection` 的完整目标框必须在右下专用格 `[775,545.5,820,564]` 内。其他技术组仍不得占用标题或公差保留区。型号表固定右上，性能块位于其下。

## 客观整页覆盖

v2 的兴趣范围固定为旋转后整页减 `coverage.exclude`，不允许操作者把 `include` 缩成已选 clips。如仍写 `include`，它必须精确等于整页。

`exclude.kind` 只允许 `outer_frame`、`supplier_title_block`、`watermark`、`replaced_title_field`、`nontechnical_annotation`。每个排除区必须有具体 `reason`；`audit.json` 会记录其墨迹量和可提取文字。

## 两级哈希

```sh
python scripts/kangsheng.py hashes JOB.json
```

- `source_inventory_sha256`：绑定源哈希/页/旋转、身份、源字段、组类型/裁切/审核外框/恢复线、整页排除和表头；英文重排另绑定逐型号源字段映射哈希。不绑定本地源路径、`dst`、`scale`、品牌资产。
- `inventory_sha256`：绑定除顶层 `review` 外的完整输出配方。

只移动布局或改缩放时不重做源清点；修改源 clips / extent / 身份 / 排除区则必须重审。

## 最终审核

`build` 生成 `final-review-template.json`。终审者查看与模板哈希一致的 `review-board.png` 和 `review-details.png`，然后填写与源审不同的 `reviewer` / `reviewer_run_id`、`full_page_compared: true`、`verdict: "PASS"`，并把 `required_checks` 全部显式写入 `checks`。

`verify --review` 校验源、输出、两级清单、两张审核图和审核运行身份。只有生成 `release.json` 才可发布。

自动比较能检测输出偏离既定配方，却不能证明配方本身排版正确。终审额外放大检查标题/型号字形、Logo 原版外观、公差槽、表格最右列及最后一行的完整边框，并与用户认可的品牌版式比较。
