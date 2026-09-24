# 工程图纸代码入口

读取根目录 `SKILL.md`。唯一生产 CLI 为 `scripts/kangsheng.py`，不要引入旧动态装箱、任意表格数值转录或旧上传入口。已审核公差的source_fields语义替换是明确支持的例外，遵循references/supplier-fields.md。原件是数据真源，清单中的坐标不是跨型号通用模板。公开目录不保存供应链原件、成品或凭证。更改程序后运行 `python -m unittest discover -s tests -v`，真实源回归在仓库外完成。
