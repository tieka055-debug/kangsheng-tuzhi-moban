# 给代理的说明

- 唯一入口：`pipeline/run_batch.py`（draft → 读公差 → finish）。按 `SKILL.md` 执行，不要翻历史聊天。
- 不要逐张手写坐标，也不要为单张图改族配置。例外只做局部处理（见 SKILL.md 第 6 节）。
- 读公差时只读本图的小图，逐行照原图写，不跨型号抄值。
- 修改代码后先运行 `python -m unittest discover -s tests`，再在本机回归清单上重跑，确认没有退步。
- 不上传原图、成品、业务数据。
