# Manifest v1

所有坐标单位为 PDF point。源坐标基于 `source.rotation` **设为该绝对角度并烘焙之后**的可见页面，不是原未旋转 MediaBox。先查看 `init` 的源页预览，再定坐标。

## 必填结构

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定 `1` |
| `source` | `{path, sha256, page:1, rotation:0或90或180或270, expected_pages:1}` |
| `identity.expected_model` | 当前待制作产品的完整型号 |
| `identity.observed_model` | 从同一源 PDF 核实的型号；和输出必须一致 |
| `identity.model_evidence` | 原件标题区域/记录映射的核对说明，不能用候选稿反证原件 |
| `identity.observed_parts` | 原表各型号；仅用于核对，绝不用于重绘表数据 |
| `identity.part_pattern` | 预期该原件型号族的正则；例如严格约束独有型号后缀 |
| `identity.no_part_table_reason` | 原件确实无型号表时必填原因；不能当作忽略表格的开关 |
| `fields` | `{model,title,scale_text,unit,sheet,tolerances:[最多4行],revision可选}`。原图空白保持空白，数值不设默认值 |
| `assets` | `{background,brand_strip,font}`，相对 manifest 或绝对本地路径 |
| `renderer` | `auto` 默认优先 native；特殊色空间才 SVG 后备。可显式 `native` 或 `svg` |
| `groups` | 见下文；不得漏表格/脚注/共享尺寸/投影符号 |
| `coverage.include` | 对整张未裁源图独立确定的技术范围矩形数组；禁止由已选择的 crops 反推范围 |
| `coverage.exclude` | `{box:[x0,y0,x1,y1],reason}` 数组，仅旧品牌/签署栏、已显式重排的标题字段等非保留技术区域 |
| `review` | 源核对证据：`{source_sha256,inventory_sha256,reviewer,verdict:"PASS"}` |

`inventory_sha256` 是除 `review` 外整个 manifest 的排序 JSON SHA256，用 `inventory-hash` 获得。审核人/审核代理在真实看过原图后填写；单纯计算哈希不是审核。任何源路径、身份、字段或几何变动都会使旧审核失效。

## 内容组

```json
{
  "id": "side_view",
  "kind": "view",
  "clips": [[100,100,240,200],[100,200,200,220]],
  "dst": [240,210],
  "scale": 1.0
}
```

- `kind`：`view`、`isometric`、`pcb`、`table`、`performance`、`note`、`projection` 或 `tolerance`。
- `dst` 对应所有 `clips` 联合外框的左上角；每片相对偏移自动保留。无需分别重算 L 形各片坐标。
- `scale` 是统一等比缩放。共享尺寸线的组不能独立拉伸/打散。尺寸视图 `view` 和 PCB 的 scale 必须为 1.0，保证源物理比例；等轴示意、表格、性能块可等比缩放。型号保留原有合法单空格，禁止首尾或连续无效空白。
- `projection` 只能使用已核对的原投影符号，放在右下小单元格。它是唯一允许进入标题保留区的技术组。
- 真实墨迹重叠失败；矩形外框只在空白处重叠可通过。标题保留区覆盖任何源墨迹会失败。
- 型号表顶端在页面右上 62% 之后，性能位于页面右侧 30% 高度之后且低于表格。布局检查不会自动装箱或悄悄缩小产品视图。

## 可选表头

原表已有正确表头时直接搬全表，不使用此项。原件将表头写在底部时，可在独立检查并保留全部数据行后，用 `table_headers` 只绘制表头和网格：

```json
{"table_headers":[{"labels":["PART NO.","DIM A","DIM B","DIM C"],
 "xs":[575,685,724,763,802],"y":[50,70]}]}
```

只接受固定表头标签；**没有 values/cells 数据重绘字段**。`coverage.exclude` 需说明被移走的旧表头边界，不能包含任何数据行。

## 输出人工验收证据

```json
{
  "source_sha256": "当前原件SHA256",
  "output_sha256": "当前输出SHA256",
  "inventory_sha256": "当前清单SHA256",
  "reviewer": "真实核图人员或独立核图代理标识",
  "verdict": "PASS",
  "checks": ["同型号原件对照", "全部产品视图和边缘尺寸", "PCB和共享说明", "全部型号表行", "性能材质镀层", "标题系列单位公差投影", "整页可读性"]
}
```

`verify --review` 同时检查自动门禁及上述哈希。无人工证据时只能返回自动核查通过，`release_ready` 为 false。此文件不要由生成脚本盲填 PASS。原件字迹不清、供应商型号冲突或缺参数时报告具体位置，不跨型号猜补。
