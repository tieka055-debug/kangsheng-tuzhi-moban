# kangsheng-tuzhi-moban

把各家供应商的连接器工程图（矢量 PDF、cad2pdf、DWG）批量转成**康生公司统一的图纸模板**：康生图框和标题栏、康生配色（轮廓蓝、尺寸/端子金）、康生版式（尽量 Pin 表右上、说明接下面，视图放大），原图的技术内容按原矢量搬运、不重打。操作手册见 [SKILL.md](SKILL.md)，任何能读文件、跑命令的智能体（Claude Code、Codex 等）都只需读这一份；Mac 和 Windows 都能用。

## 流程

```
原图 ─▶ pipeline/frame_match.py   认供应商图框（已积累 46 个图框模板、362 个看过对照图的样本）
     ─▶ 读公差（只读本图）写进 job.json
     ─▶ pipeline/cad_family.py      去掉供应商图框/标题栏/RoHS/修订栏，原矢量搬进康生图框，重新排版、改色
     ─▶ 康生图纸.pdf + 原图对照.png ─▶ 看对照图 ─▶ tools/feishu_backfill.py 回填飞书
```
质源单页矢量 PDF 另有一条全自动门禁流程 `pipeline/run_batch.py`（draft → 读公差 → finish），见 SKILL.md 前半部分。
认不出的图框不硬做：记进待处理清单，由维护人新建模板、登记样本后再做。

## 目录

| 目录 | 内容 |
|---|---|
| `engine/` | 出图引擎：矢量搬运、改色、门禁、标题栏、英文公差栏 |
| `pipeline/` | 自动清单、批量入口、字段小图、英文公差，以及 3 个独立检测器 |
| `families/` | 图框模板 `cad_templates.json`、图框指纹样本 `cad_signatures.json`、质源族配置 `zhiyuan.json` |
| `brands/` | 品牌配置（康生 / 润擎） |
| `tools/` | 飞书回填与备注、回归测试、网格预览 |
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
字体不用传：程序自动用 `/System/Library/Fonts/STHeiti Medium.ttc`（见 `engine/fonts.py`）。

### Windows（PowerShell）

1. 安装 Python（python.org，安装时勾选 **Add python.exe to PATH**）、Git（git-scm.com）、Ghostscript 64 位（ghostscript.com 下载 `gs…w64.exe`）。
2. 设置 `PYTHONUTF8=1`（系统环境变量，或每次在 PowerShell 里 `$env:PYTHONUTF8=1`），避免中文路径和输出乱码。
3. 把 Ghostscript 的 `bin` 目录加入 PATH（例如 `C:\Program Files\gs\gs10.04.0\bin`），新开 PowerShell 运行 `gswin64c -v` 能看到版本即可。程序会自动找 `gs` / `gswin64c` / `gswin32c`。
4. 安装本仓库：
   ```powershell
   git clone https://github.com/tieka055-debug/kangsheng-tuzhi-moban.git
   cd kangsheng-tuzhi-moban
   py -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
5. 出图（字体不用传，自动用系统黑体 `C:\Windows\Fonts\simhei.ttf`）：
   ```powershell
   .venv\Scripts\python pipeline\cad_family.py job.json --out 输出目录
   ```
   回填飞书需要 `lark-cli`（npm 安装的 `lark-cli.cmd` 也能自动找到）。

### 给其他智能体用

能读写本地文件、能运行命令的智能体（Claude Code、Codex 等）：仓库根目录的 `AGENTS.md`（Codex 等读）和 `CLAUDE.md`（Claude Code 读）会自动把它带到 `SKILL.md`；批量回填飞书照 `docs/GPT_BATCH_PROMPT.md`。只能聊天、不能运行命令的网页版智能体跑不了。
