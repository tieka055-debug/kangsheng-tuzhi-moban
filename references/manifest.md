# Manifest v2

新任务使用 `schema_version: 2`。v1 仅供旧清单兼容，不具有 v2 的客观整页覆盖、可读性和双向整页 QA。坐标单位为 PDF point，原点在左上；源坐标基于 `source.rotation` 烘焙后的可见页。

## 基本结构

```json
{
  "schema_version": 2,
  "source": {"path":"SOURCE.pdf","sha256":"SHA256","page":1,"rotation":270,"expected_pages":1},
  "renderer": "auto",
  "identity": {
    "expected_model":"MODEL","observed_model":"MODEL","model_evidence":"完整原页证据",
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
- `fields.tolerances` 在 v2 必须为空；公差值作为 `tolerance` 原矢量组保留。原图确无公差块时填 `no_tolerance_block_reason`。
- `renderer` 可为 `auto` / `native` / `svg`。通常品牌图有效分辨率至少 150 DPI，标题字体必须覆盖所有字符。
- 唯一兼容例外是用户已认可第 14–18 行使用的原版 `assets/brand-strip.png`：597×92 像素，SHA256 `436ab38839ec933f3c295c9b17005cb8942b16245715073c7e69da1bd5d16078`。标准位置有效分辨率为约 127.1 DPI；审核报告如实标记 `approved-native-original` 和未达到通常 DPI 阈值。这是恢复已认可原资产，不是分辨率提升；其他低分辨率文件仍被拒绝。

## 内容组

`kind` 只能为 `view`、`isometric`、`pcb`、`table`、`performance`、`note`、`projection`、`tolerance`。

- `clips` 是同组原区域；`dst` 对应联合外框左上角，同组多片共用一个等比变换。
- `reviewed_source_extent` 是审核者在完整原页上标定的最小完整技术外框。裁切携带该范围外墨迹或切断文字会停止。
- `view` / `pcb` 必须 `scale: 1.0`。其他组缩放后的可提取技术文字不得小于 4.75 pt。
- 组不得进入排除区，不得与其他组真实墨迹重叠。
- 表外框与源页框共线时，`table` / `tolerance` 可用最多一条水平和一条垂直 `restored_source_rules`；只能恢复无文本闭合线。
- v2 的 `tolerance` 最多一组（可含多片），完整目标框及恢复线笔画必须在底部专用槽 `[400,493,488,564]` 内；模板不再绘制第二份空白公差框。原值与原表直接搬运，勿用通用默认值替代。
- 用户要求去掉空白公差格时，先查未裁切原件及放大格内墨迹，确认只有空网格而无字/数/符号。用多片 `clips` 保留完整原表头及有值部分，把纯空格框登记为有理由的 `nontechnical_annotation` 排除区并重做源清单审核。不能只靠文字提取为空认定为空，也不能套用其他型号的空列判断。
- `projection` 的完整目标框必须在右下专用格 `[775,545.5,820,564]` 内。其他技术组仍不得占用标题或公差保留区。型号表固定右上，性能块位于其下。

## 客观整页覆盖

v2 的兴趣范围固定为旋转后整页减 `coverage.exclude`，不允许操作者把 `include` 缩成已选 clips。如仍写 `include`，它必须精确等于整页。

`exclude.kind` 只允许 `outer_frame`、`supplier_title_block`、`watermark`、`replaced_title_field`、`nontechnical_annotation`。每个排除区必须有具体 `reason`；`audit.json` 会记录其墨迹量和可提取文字。

## 两级哈希

```sh
python scripts/kangsheng.py hashes JOB.json
```

- `source_inventory_sha256`：绑定源哈希/页/旋转、身份、源字段、组类型/裁切/审核外框/恢复线、整页排除和表头；不绑定本地源路径、`dst`、`scale`、品牌资产。
- `inventory_sha256`：绑定除顶层 `review` 外的完整输出配方。

只移动布局或改缩放时不重做源清点；修改源 clips / extent / 身份 / 排除区则必须重审。

## 最终审核

`build` 生成 `final-review-template.json`。终审者查看与模板哈希一致的 `review-board.png` 和 `review-details.png`，然后填写与源审不同的 `reviewer` / `reviewer_run_id`、`full_page_compared: true`、`verdict: "PASS"`，并把 `required_checks` 全部显式写入 `checks`。

`verify --review` 校验源、输出、两级清单、两张审核图和审核运行身份。只有生成 `release.json` 才可发布。

自动比较能检测输出偏离既定配方，却不能证明配方本身排版正确。终审额外放大检查标题/型号字形、Logo 原版外观、公差槽、表格最右列及最后一行的完整边框，并与用户认可的品牌版式比较。
