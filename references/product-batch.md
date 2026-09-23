# 已认可原件的确定性批量重放（隔离候选）

本入口仍是 `scripts/kangsheng.py batch`，但输入必须是 `kangsheng-products-v1`。它与旧 `jobs` 工程构建模式互斥：这里只处理**逐张源 SHA 已登记、配方已冻结、认可 PDF 与独立 anchor 相符**的精确原件。匹配同名、相近型号或相似版式均不授予自动处理资格；未知源进入异常队列。产物是视觉重放，`engineering_release=false`，不产生工程发布凭证。

```json
{
  "schema_version": "kangsheng-products-v1",
  "products": [
    {
      "id": "RECORD_ID",
      "model": "MODEL_FROM_SOURCE",
      "source": "SOURCE.pdf",
      "source_sha256": "SOURCE_SHA256_64_LOWERCASE_HEX"
    }
  ]
}
```

```sh
python scripts/kangsheng.py batch PRODUCTS.json \
  --output-root WORK/batch --control-root WORK/control \
  --golden-ledger PRIVATE/golden-ledger.json \
  --catalog-dir supported-layouts --workers 2
```

若账本同时含由 PyMuPDF 1.26.5 创建的已认可旧视觉 PDF，显式增加 `--runtime-126 PATH_TO_PINNED_PYTHON`；该 Python 必须实际报告 1.26.5。每张配方绑定自己的 PyMuPDF 版本，程序按绑定版本选进程重放。缺失或版本不符时停在 `NEEDS_AI_REVIEW`，不换渲染器凑近似图。

预检核对原图 SHA、身份、单页/方向/尺寸与结构特征、catalog 状态、独立认可 anchor、配方内容/引擎/资产及认可 PDF。结构特征只给出候选族，绝不允许跨源复制尺寸、公差、Pin、Part No.、PCB 或性能参数。新结构、额外技术区域或不确定品牌墨迹一律送 `NEEDS_AI_REVIEW`；智能体只接收该件的源图、局部证据和失败理由，在隔离目录研究。另一个同结构源未经独立提取器和回归验证前，不将家族晋升为泛化 `SUPPORTED`。

输出为 `batch-result.json`、`needs-ai-review.json` 和每件的 `drawing.pdf`、`preview.png`、`replay-check.json`。终端只打印产品 ID、型号、layout、状态、生成/核验/总耗时。成功状态 `PASS_VISUAL_REPLAY` 表示对该件已认可整页 PDF 的 4×RGB 零差重放；PDF 字节 SHA 可能因 trailer `/ID` 不同而变动，不能以视觉 SHA 取代配方与源 SHA 绑定。相同输入及代码/规则/账本/anchor/运行时不变时，批量缓存可直接复用；任一绑定变动则进入新输出命名空间并重新核验。

并行使用独立进程，父进程写批量汇总，各进程写各自输出目录。`--workers` 仅允许 1、2、4；按机器内存实测选择，不因并发自动放宽 QA。老 `jobs` 模式仍使用工程审查先导门禁和项目控制账本，见 [批量执行与接力](batch-handoff.md)。

修改代码或布局后先在隔离目录跑合成测试，再用**仓库外**私有账本及 anchor 跑 `scripts/golden_regression.py`。缺少获认可的现行 PDF、配方或工程语义冲突会明确列为阻断，不改黄金图纸或 anchor 来制造通过。
