<div align="center">

# rimeup

**一行命令，把 [雾凇拼音 / rime-ice](https://github.com/iDvel/rime-ice) 上游词库安全地更新到你本地。**

保留你的手动改动 —— 不只是新增的词，连你调过的词频也一并保住。

[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Dependencies: none](https://img.shields.io/badge/runtime%20deps-0-brightgreen.svg)](#)

</div>

---

## 痛点

[rime-ice](https://github.com/iDvel/rime-ice) 的词库一直在更新，但它**不是**通过包管理器分发的——你得手动去仓库里下载词库文件覆盖到本地。于是每次更新都要：翻仓库、比版本、决定覆盖还是合并、手动备份、再重新部署。更麻烦的是，一旦你在词库里加过自定义词、调过某些词的词频，**直接覆盖就全没了**。

`rimeup` 把这一整套变成一条命令。

## ✨ 特性

- 🔍 **一眼看清差异** —— 表格列出每个词库的本地/上游版本、状态，以及「上游新增 / 本地独有」的词条数。
- 🔀 **三方增量合并** —— 拉取上游更新的同时，**保留你的本地新增词条和词频调整**（靠基线区分「你改的」与「上游改的」，详见下文）。
- 💥 **也支持整体覆盖** —— 想要干净的上游版本？一个选项搞定。
- 🛡️ **安全第一** —— 改动前自动备份；原子写入，中途崩溃也不会写坏词库；只动 `*.dict.yaml`，绝不碰你的 schema / lua / opencc；本地独有的词库文件只跳过、不删除。
- 🪶 **零运行时依赖** —— 单个 Python 文件，只用标准库（另需系统自带的 `git`）。

## 📸 长这样

```text
$ rimeup

上游 HEAD: a5d123b  2026-05-18  dict: 增改词汇 (#1545)

词库                        本地版本    上游版本    状态      上游新增  本地独有
cn_dicts/base.dict.yaml     2025-11-01  2026-05-16  有更新     +7177      12
cn_dicts/ext.dict.yaml      2025-11-01  2026-05-16  有更新     +1562       0
en_dicts/en_ext.dict.yaml   2025-10-31  2026-03-08  有更新     +286        4
liangfen.dict.yaml          5.0         —           本地独有   -          -
melt_eng.dict.yaml          2023-05-09  2023-05-09  相同       -          -
共 5 个词库：3 个有更新，1 个相同，1 个本地独有，0 个上游新文件。

[1] 覆盖更新   [2] 增量合并   [3] 不更新
请选择 [1/2/3]:
```

## 📦 安装

> 适用于 macOS（默认 Rime 目录 `~/Library/Rime`，对应 Squirrel／鼠须管）。其他平台见[配置](#️-选项与配置)。

**方式一：pipx（推荐）**

```bash
pipx install git+https://github.com/ryekee/rime-ice-dict-update.git
```

**方式二：pip**

```bash
pip install git+https://github.com/ryekee/rime-ice-dict-update.git
```

**方式三：单文件，零安装**

工具就是一个自包含的脚本，下载下来丢进 `PATH` 即可：

```bash
curl -fsSL https://raw.githubusercontent.com/ryekee/rime-ice-dict-update/main/rimeup.py \
  -o ~/.local/bin/rimeup && chmod +x ~/.local/bin/rimeup
```

**方式四：从源码**

```bash
git clone https://github.com/ryekee/rime-ice-dict-update.git
cd rime-ice-dict-update
make install        # = pipx install .
```

装好后确认一下：

```bash
rimeup --check
```

## 🚀 使用

```bash
rimeup                # 比对差异，交互选择：覆盖 / 增量合并 / 不更新
rimeup --check        # 只看差异表格，不做任何改动（-n 等价）
rimeup --incremental  # 直接增量合并（保留本地新增 + 词频调整）
rimeup --overwrite    # 直接整体覆盖为上游版本
```

更新完成后，**记得在输入法菜单点「重新部署」**让改动生效（大词库首次编译会花一点时间，属正常）。

## 🔀 增量合并是怎么工作的

「直接覆盖」会丢掉你的手动改动；而「只要本地和上游不一样就保留本地」又会让你**永远拿不到上游对那些词的更新**——因为分不清到底是「你改的」还是「上游改的」。

`rimeup` 用一份**基线**（你上次同步时的上游版本，存于 `~/.cache/rimeup/baseline/`）做三方合并，对每个 `(词, 编码)`：

| 情况 | 处理 |
| --- | --- |
| 本地新增的词（基线、上游都没有） | ✅ **保留你的** |
| 本地 == 基线（你没动过） | ⬆️ **跟随上游**（拿到上游的新词 / 调频；上游若删除则随之删除） |
| 本地 != 基线（你改过词频 / 内容） | ✅ **保留你的** |
| 上游新增的词 | ⬆️ **并入** |

> **首次增量**：还没有基线时，会保守地保留所有「本地与上游不同」的词条，并给出提示；从第二次起就能精确区分了。
>
> 本地独有的新增词条会被追加到文件末尾、带 `# === rimeup 保留的本地词条 ===` 标记；rime-ice 词库多为 `sort: by_weight`，Rime 部署时会自动重排。

## 🛡️ 安全保障

- **自动备份**：任何改动前，会把将被修改的文件备份到 `~/Library/Application Support/rimeup/backups/<时间戳>/`。
- **原子写入**：先写临时文件再替换，进程中途中断也不会留下半截损坏的词库。
- **范围受限**：只处理 `*.dict.yaml`（仓库根目录 + `cn_dicts/` + `en_dicts/`），不动 schema、lua、opencc 等其它文件。
- **不误删**：上游已移除、但你本地还留着的词库（如 `liangfen.dict.yaml`）会被标为「本地独有」并跳过，绝不删除。
- **解析容错**：个别词库若解析失败，会自动降级为「整体覆盖」并提示，绝不让一个坏文件中断整次更新。

## ⚙️ 选项与配置

| 选项 | 说明 | 默认 |
| --- | --- | --- |
| `--check`, `-n` | 只展示差异表格，不改动 | |
| `--overwrite` | 整体覆盖更新（与 `--incremental` 互斥） | |
| `--incremental` | 词条级增量合并（与 `--overwrite` 互斥） | |
| `--rime-dir DIR` | 本地 Rime 配置目录 | `~/Library/Rime`（或环境变量 `RIME_DIR`） |
| `--repo URL` | 上游仓库 | `https://github.com/iDvel/rime-ice.git` |
| `--cache-dir DIR` | 上游缓存克隆目录 | `~/.cache/rimeup/rime-ice` |
| `--baseline-dir DIR` | 三方合并的基线目录 | `~/.cache/rimeup/baseline` |
| `--backup-dir DIR` | 备份目录 | `~/Library/Application Support/rimeup/backups/<时间戳>` |

非 macOS 用户：用 `--rime-dir` 或环境变量 `RIME_DIR` 指向你的 Rime 目录（例如 fcitx5 的 `~/.local/share/fcitx5/rime`）即可。

## 🧑‍💻 开发

```bash
git clone https://github.com/ryekee/rime-ice-dict-update.git
cd rime-ice-dict-update
make dev     # 建 venv 并安装 pytest
make test    # 运行测试
```

工具本身仅依赖 Python 标准库；测试用 `pytest`，且全部离线运行（用本地临时 git 仓库充当「上游」，不联网）。

## ❓ 常见问题

**改完没生效？** 需要在输入法菜单（菜单栏图标）点「重新部署」重新编译词库。

**所有词库都显示「相同」？** 说明本地已经是最新；下次上游更新后再跑就会看到 `有更新`。

**上游新增了一个全新词库文件？** 当前版本只在表格里报告（`上游新文件`），不会自动加入——因为新词库需要你在 schema 里引用后才有意义。

## 🙏 致谢

词库来自 [**iDvel/rime-ice（雾凇拼音）**](https://github.com/iDvel/rime-ice)。`rimeup` 只是一个更新工具，所有词库数据归原项目所有，请遵循其许可协议。

## 📄 许可

[GPL-3.0-only](LICENSE) © Ryekee —— 与上游 [rime-ice](https://github.com/iDvel/rime-ice) 保持一致。
