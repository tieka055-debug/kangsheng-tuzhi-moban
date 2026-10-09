# kangsheng-tuzhi-moban

把供应商的单页矢量 PDF 工程图批量转成**康生品牌图纸**。操作手册见 [SKILL.md](SKILL.md)，任何模型（Claude、Codex、Zcode 或低成本模型）都只需读这一份。

## 流程

```
原图.pdf ─▶ pipeline/auto_manifest.py   自动算出分块、裁切、排除区和排版（族配置里只有相对规则）
         ─▶ engine/kangsheng.py draft   原矢量搬运，整页零遗漏门禁
         ─▶ 分诊 AUTO_OK / REVIEW / EXCEPTION，并导出公差小图
         ─▶ 读公差（几秒）─▶ pipeline/english_tolerance.py   英文公差栏，引擎重新跑全部门禁
         ─▶ 康生草稿.pdf + 原图对照.png
```

## 目录

| 目录 | 内容 |
|---|---|
| `engine/` | 出图引擎：矢量搬运、改色、门禁、标题栏、英文公差栏 |
| `pipeline/` | 自动清单、批量入口、字段小图、英文公差，以及 3 个独立检测器 |
| `families/` | 供应商族配置（目前只有质源 `zhiyuan.json`） |
| `assets/` | 康生背景和品牌条 |
| `tests/` | 单元测试 |

## 实测（2026-09-25，质源图纸）

| 验证 | 结果 |
|---|---|
| 最后一轮留出验证 | 21 张未见过的图：19 张通过门禁，其中 14 张直接可看、5 张需看、2 张例外；未发现"门禁通过但内容有错"的图 |
| 飞书表第 36–41 行实测 | 5/5 通过；英文公差栏 5/5 通过 |

**速度**：每张机器时间约 10–30 秒。再加上读公差（几秒）和看对照图（约 1 分钟）。

## 数据边界

本仓库只放程序、品牌资产和规范。以下内容**不上传**：供应商原图、成品、型号清单、业务记录 ID、私有路径、令牌。

## 安装（Mac / Windows 都可以）

需要：Python 3.9 以上、Git、Ghostscript（带文字层的 PDF 要先把字转成线条）、一个中文字体文件。回填飞书另需 `lark-cli`（只出图不回填可不装）。

### Mac

```sh
git clone https://github.com/tieka055-debug/kangsheng-tuzhi-moban.git
cd kangsheng-tuzhi-moban
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
brew install ghostscript          # 没有 Homebrew 也可以用 conda/micromamba 装到用户目录
```
字体：`--font "/System/Library/Fonts/STHeiti Medium.ttc" --font-index 0`

### Windows（PowerShell）

1. 安装 Python（python.org，安装时勾选 **Add python.exe to PATH**）、Git（git-scm.com）、Ghostscript 64 位（ghostscript.com 下载 `gs…w64.exe`）。
2. 把 Ghostscript 的 `bin` 目录加入 PATH（例如 `C:\Program Files\gs\gs10.04.0\bin`），新开 PowerShell 运行 `gswin64c -v` 能看到版本即可。程序会自动找 `gs` / `gswin64c` / `gswin32c`。
3. 安装本仓库：
   ```powershell
   git clone https://github.com/tieka055-debug/kangsheng-tuzhi-moban.git
   cd kangsheng-tuzhi-moban
   py -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
4. 出图（字体用系统自带的黑体或微软雅黑）：
   ```powershell
   .venv\Scripts\python pipeline\cad_family.py job.json --out 输出目录 --font C:\Windows\Fonts\simhei.ttf
   ```
   用 `msyh.ttc`（微软雅黑）时加 `--font-index 0`。

### 给其他智能体用

能读写本地文件、能运行命令的智能体（Claude Code、Codex 等）：让它先读 `SKILL.md`、`docs/HANDOFF.md`；批量回填飞书照 `docs/GPT_BATCH_PROMPT.md`。只能聊天、不能运行命令的网页版智能体跑不了。
