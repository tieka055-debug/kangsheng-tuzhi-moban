# 当前供应商：完整字段与跨模型接力

本轮生产版本沿用现有矢量搬运引擎，不再运行私人目录中的
`render_dynamic_batch.py` / `run_geometry_batch.py`。

## 路由和复用边界

- 同原件：已有审核 manifest + `source_fields` 账本，运行统一 CLI，不再读聊天、不重新识别。
- 同供应商新原件：复用语义角色、槽位和颜色规则，**重新定位当前原件技术范围**。
  只有已验证的定位器匹配、完整源清单和字段证据通过才可无模型生成。
- 新供应商或不匹配布局：局部/首次适配后保存自己的配方；不是“第二张无条件自动”。
- “进化”指经验证的配方/证据库增长，不是模型训练，不自动把候选写成黄金基准。

## 两个负样本与修复

1. CAD 公差中部分字体是曲线，`get_text()` 或正则只取得一档；它不代表完整公差。
   替换公差前必须读取 `source_fields`：原件SHA、方向、整格图像指纹、各档字段、独立逐字段审核。
   比较实际输出与该完整账本，而非比较“提取子集”和“输出子集”。PCB局部公差继续原矢量搬运。
2. 搜索得到标题不等于查看器可见。标题字体必须是独立 TTF/OTF、覆盖全部字形并嵌入。
   禁止把整个 TTC 集合当作单个 PDF 字体。终审同时看 MuPDF 和 Poppler（或用户实际查看器）。

`source_fields` 与旧的 `approved_tolerance_reflow` 互斥；后者保留用于旧冻结配方。
不要改写历史认可 PDF。新源使用当前英文疏排公差，不恢复双语网格。

## 唯一入口

在技能根目录运行（运行环境以接力包 `HANDOFF.json` 为准）：

```sh
python scripts/kangsheng.py handoff-check .
python scripts/kangsheng.py build jobs/JOB-manifest.json --output runs/JOB \
  --cache cache --control-root control
python scripts/kangsheng.py recheck-source-fields jobs/JOB-manifest.json \
  runs/JOB/drawing.pdf --report runs/JOB/source-fields-qa.json
```

`build` 保留原有源审核、全页覆盖、逐片几何及版面 QA。自动通过不等于工程发布。
失败只检查异常报告；保持最多两次尝试，不重置账本反复试，不重新微调整页。

新源字段需局部审阅时：

```sh
python scripts/kangsheng.py inspect-source-fields SOURCE.pdf --rotation 270 \
  --box X0,Y0,X1,Y1 --output review/fields
```

该命令只输出原格图像、指纹和辅助文字，不填公差值，不签 PASS。
经独立核对后保存 `kangsheng-source-fields-v1` 账本。不能从相似型号复制数值。

## 接力包

```sh
python scripts/kangsheng.py pack-local jobs/JOB-manifest.json --output PRIVATE_BUNDLE
```

包包含技能代码、品牌资产、当前产品原件、相对路径配方、完整字段账本、审核证据和校验清单。
复制到不同目录后先运行 `handoff-check`。未知原件不能借用已知 job；依赖缺失或版本不同先解决环境。
包是**私有业务资料**，不得上传公共 Git 仓库；公开技能只含通用代码与规则。

安装技能的模型只需读 `SKILL.md`；不支持 Skill 自动发现的模型，明确指定该文件即可。
新窗口不要依赖绝对旧工作目录，也不要靠标题猜最新版：以本包 `HANDOFF.json` 和校验清单为准。
实际 Python/PyMuPDF 版本锁定在包中；跨版本须重新运行测试和视觉回归，不承诺任意环境必然一致。

新型号有现成标题子集不包含的汉字时，程序会阻断而非丢字。用有权使用的字体自动生成该型号独立子集：

```sh
python scripts/kangsheng.py prepare-title-font jobs/JOB-fields.json \
  --font FONT.ttf --font-index 0 --output private/JOB-title.ttf
```

也可输入本机TTC及明确的字体索引，输出始终是独立字体；更新 manifest 的 assets.font 后再生成。
