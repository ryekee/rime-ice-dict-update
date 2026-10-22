# rimeup

从 [iDvel/rime-ice](https://github.com/iDvel/rime-ice) 更新 Rime 的 `.dict.yaml` 词库。项目仓库是 [ryekee/rime-ice-dict-update](https://github.com/ryekee/rime-ice-dict-update)。

支持覆盖更新和三方增量合并。每次更新先检查全部文件，再备份、写入并校验。失败时回退词库和基线。工具不会重新部署输入法。

需要 Python 3.8+、Git 和 PyYAML 6.x。当前支持 macOS 和 Linux；并发锁使用 POSIX `flock`。

## 安装

推荐用 pipx 隔离依赖：

```bash
pipx install git+https://github.com/ryekee/rime-ice-dict-update.git
```

从本地源码安装：

```bash
pipx install .
# 更新已有安装：
pipx install --force .
```

也可以使用独立的 Python 虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/rimeup --help
```

1.1 起使用 PyYAML 解析词典头部。旧版的单文件 `curl` 安装方式不再适用。不要把脚本复制到缺少 PyYAML 的 Python 环境中。

## 检查与更新

```bash
rimeup --check                # 拉取上游并列出差异，不写词库、基线或备份
rimeup --incremental --check  # 额外预检增量合并、基线和词典引用，仍不写入
rimeup                       # 显示差异，再选择覆盖、增量合并或退出
rimeup --incremental         # 基于历史上游基线合并
rimeup --overwrite           # 明确用上游内容替换对应词库，本地改动会被覆盖
```

检查命令会更新 Git 下载缓存。它不会修改 Rime 目录。拉取失败时，普通 `--check` 可以显示旧缓存的比较结果，但会标明缓存过期并返回非零退出码。更新命令在拉取失败时停止，不使用旧缓存写入。

差异表比较本地和上游内容。本地定制也会使文件显示「有更新」，不代表上游一定有新提交。实际写入计划会排除合并后没有变化的文件。

成功更新词库后，请在输入法菜单中手动选择「重新部署」。词库写入成功不代表 Rime 已完成编译。

## 增量合并规则

基线是上次成功同步的**原始上游词库**，不是合并后的本地文件。

| 情况 | 处理 |
| --- | --- |
| 本地词条与基线相同 | 采用上游修改，包括调频和删除 |
| 本地新增词条 | 保留 |
| 本地修改词频 | 保留本地词频 |
| 本地删除词条或修改编码 | 保留删除；修改编码视为删除旧词条、添加新词条 |
| 上游新增词条 | 加入 |
| 本地修改词典头部 | 按字段合并，包括 `import_tables`；同一字段双方改动且结果不同时停止 |
| 缺少基线，且本地与上游不同 | **整次停止，不猜测哪些差异是本地定制** |
| 缺少基线，但本地与上游完全一致 | 可以直接建立基线 |

解析器按 `columns` 定义处理字段，支持 `text`、`code`、`weight` 及其排列。腾讯词库的 `text, weight` 两列格式可正常合并。自动注音词条的空编码与显式编码分别处理。

同一词条的重复记录按有序的一组数据合并。未修改的重复记录保留上游顺序，不用最后一条覆盖前面的记录。

不支持的列、`# no comment` 指令、无效 YAML 或无效词频会使更新停止。增量模式不会因解析失败而自动改成覆盖模式。显式 `--overwrite` 可以替换损坏的本地文件，但上游文件仍必须通过验证。

头部无需修改时保留上游原文。需要合并头部字段时重新生成 YAML，语义设置保留，原头部注释和排版可能改变。词条区的说明注释不参与三方合并。

### 从 1.0 迁移

默认基线从可清理的缓存目录移至：

```text
~/Library/Application Support/rimeup/baselines/<Rime目录与上游仓库的标识>/
```

不同 Rime 目录或不同上游使用独立基线。首次成功写入会记录基线所属目录和仓库，避免混用。

旧基线**不会自动迁移或假定可信**。确认旧基线来自当前目录和上游后，可以显式使用它：

```bash
rimeup --baseline-dir ~/.cache/rimeup/baseline --incremental --check
```

旧版仅给实际修改过的文件保存基线，因此旧基线可能不完整。缺少基线时，应提供对应的历史上游文件；如果明确要舍弃本地差异，也可以选择 `--overwrite` 建立新基线。工具不会把当前本地文件直接当成历史上游。

## 更新范围

扫描仓库根目录、`cn_dicts/` 和 `en_dicts/` 内的 `.dict.yaml` 文件。

- 更新本地与上游都有的词库。本地独有文件不删除。
- 上游新词库通常只报告。当它被合并结果的 `import_tables` 引用时，连同该依赖一起安装、备份记录和回退。
- 缺少引用词库、引用越界或循环引用时，整次停止。
- 不更新 schema、Lua、OpenCC、自定义短语 `.txt` 或用户学习库 `.userdb`。
- 这不是完整的雾凇配置更新器；中英混输 `.txt` 文件也不在更新范围内。

## 失败恢复

更新流程：

1. 在内存中准备全部结果，验证词典格式与引用关系。
2. 检查文件是否在准备期间被其他程序改动。备份即将替换的本地词库。
3. 保存词库、基线的原始字节和权限，以及待写入文件和恢复记录。
4. 逐文件原子替换，并在写后校验 SHA-256。词库与基线属于同一批更新。
5. 全部写入成功后标记完成，再清理恢复记录。

捕获到写入错误或键盘中断时，自动恢复整批词库和基线，并删除本次新增的词库文件。自动回退失败时保留恢复副本并报错。

**整批更新不是一个文件系统原子操作。** 强制结束进程、断电等情况可能暂时留下部分更新。下次运行会检测到恢复记录并停止，避免继续更新。此时使用原来的目录参数恢复：

```bash
rimeup --recover
# 使用过自定义目录时：
rimeup --rime-dir /path/to/rime --baseline-dir /path/to/baseline --recover
```

恢复操作不联网。如果中断发生在全部写入完成并已记录成功之后，恢复命令只清理记录。恢复完成前不要手动重新部署。恢复依赖磁盘上的副本，不能替代独立备份，也不保证损坏磁盘上的数据可恢复。

备份默认位于 `~/Library/Application Support/rimeup/backups/<时间戳>/`。不允许覆盖非空备份目录。正常完成后的该备份包含被替换的本地词库；恢复中断更新所需的基线副本保存在 Rime 目录的 `.rimeup-transaction/`，成功或回退完成后清理。

## 选项

| 选项 | 说明 |
| --- | --- |
| `--check`, `-n` | 只检查；可与一种更新模式组合进行预检 |
| `--incremental` | 三方增量合并 |
| `--overwrite` | 整体覆盖，与增量模式互斥 |
| `--recover` | 恢复中断更新，与检查、更新模式互斥 |
| `--rime-dir DIR` | 默认为 `~/Library/Rime`，或环境变量 `RIME_DIR` |
| `--repo URL` | 默认为 `https://github.com/iDvel/rime-ice.git` |
| `--cache-dir DIR` | 默认为 `~/.cache/rimeup/rime-ice` |
| `--baseline-dir DIR` | 默认按本地目录和上游仓库独立保存 |
| `--backup-dir DIR` | 使用新的备份目录；已有目录必须为空 |

Rime、缓存、基线、备份目录不能相同或互相包含。工具拒绝修改符号链接词库，拒绝重置有本地改动的 Git 缓存，并校验缓存的 `origin`。

退出码：`0` 表示操作成功，`1` 表示检查或更新失败，`2` 表示参数错误或 Rime 目录不存在，`130` 表示用户取消。POSIX 文件锁阻止同一目录或缓存的并发更新。

## 开发与测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

测试使用临时目录和本地 Git 仓库，不访问用户的 Rime 数据。覆盖两列及重排列词库、基线缺失、本地删除和头部修改、引用依赖、写入失败、进程强制退出后恢复、缓存失败及 CLI 退出码。

## 许可

[GPL-3.0-only](LICENSE) © Ryekee。词库来自 [iDvel/rime-ice](https://github.com/iDvel/rime-ice)，请遵循上游许可。
