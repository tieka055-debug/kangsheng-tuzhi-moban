# kangsheng-tuzhi-moban

康生连接器工程图纸模板：**原件身份绑定 → 原矢量内容组重排 → 完整性核查 → 原图目检 → 指定附件发布**。

本版固化已验收的蓝色康生样式：型号表右上，性能参数位于其下的右侧中部，产品视图及 PCB 利用左、中、下部空间。它是可复用的制作工具，不是自动理解任意图纸的黑箱；首次内容分组和坐标仍需对照原件核定。

## 安装与使用

Python 3.10+：

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/kangsheng.py init INPUT.pdf --manifest JOB.json --model '原图完整型号' --rotation 270
```

1. 对照输出的未裁切源页，填写 `JOB.json` 中的身份、内容组、位置、标题字段及审核记录。规范见 [references/manifest.md](references/manifest.md)。
2. 为输出标题配置有完整中文字符的本地授权字体；本仓库不分发系统字体。
3. 审核清单后构建：

```sh
python scripts/kangsheng.py inventory-hash JOB.json
python scripts/kangsheng.py build JOB.json --output OUTPUT --cache CACHE
python scripts/kangsheng.py verify JOB.json OUTPUT/drawing.pdf --review REVIEW.json
python -m unittest discover -s tests -v
```

输出为单页 A4 横向 `drawing.pdf`、`preview.png`、`layout.json`、`audit.json`。失败时仅保留诊断候选，不输出新的合格图。每个任务用独立 OUTPUT 目录，避免误用此前结果。

## 技术与成本

- Python + PyMuPDF 排版/渲染；pikepdf 解析颜色操作符，保留源 PDF 文字字体与矢量。原件为特殊色空间时自动使用矢量 SVG 路径后备，不重新生成图形和参数。
- 原表格数据作为原矢量搬运，只允许可选的 `PART NO.` / `DIM` 表头规范化，不提供数字表格重绘算法。
- 缓存只存在用户工作目录，按原件哈希与引擎版本去重。源技术像素覆盖检查、输出掩码检查由本地程序执行，不调用模型或网络。
- 一张图实际耗时与矢量复杂度、字体、机器、首次核图有关。固定清单后的本地生成可很快；首次识别、人工验收及上传应另计，不能以渲染时间冒充总交付时间。

## 质量边界

自动门禁包含源技术区域覆盖、逐内容块输出缺失/新增墨迹检查、型号冲突、标题字形、画布边界、真实墨迹重叠、固定区域布局、文件和清单哈希。输出墨迹允许 4 倍渲染下 2 像素抗锯齿容差，单块缺失比例须 **小于 0.2%**；源技术范围内未放置墨迹须为 **0**。输出墨迹按同背景的蓝/金混合透明度匹配源阈值，不用更严格的原始蓝色通道阈值误杀细线。新增墨迹参照原图较浅笔划（<245）及全部已放置内容组，防止把邻近组误报。阈值不是工程误差许可。

“技术范围”与“非技术排除范围”必须由独立核图确认。掩码检查无法证明原件本身正确、无法取代数值和图意核对，不会自动颁发工程认证。脚本不会伪造人工通过记录，也不内置飞书令牌或网络发布。

## 内容与隐私

仓库仅包含通用程序、两张已批准品牌资产、规范和程序生成的合成测试。客户/供应链图纸、原始型号表、个人目录、附件令牌、日志及逐行清单不入库。品牌图片仍归原权利人所有；请仅在有权使用康生品牌的工作中使用。

本仓库不包含旧装箱/旧裁图/旧上传入口。项目外历史文件不由安装脚本删除。
