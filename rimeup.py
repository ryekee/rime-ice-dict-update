#!/usr/bin/env python3
#
# rimeup — update Rime dictionaries from the iDvel/rime-ice upstream.
# Copyright (C) 2026 Ryekee
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""rimeup — update Rime dictionaries from the iDvel/rime-ice upstream."""
from collections import namedtuple
import unicodedata
import os
import hashlib
import tempfile
import shutil
import subprocess
import argparse
import sys
import datetime
import json
import re
import stat
from contextlib import contextmanager
from pathlib import Path

import yaml

Entry = namedtuple("Entry", ["text", "code", "weight"])
ParsedDict = namedtuple("ParsedDict", ["header", "meta", "entries"])

# M9: module constant for the local-only section marker used in merge output
LOCAL_ENTRIES_MARKER = "# === rimeup 保留的本地词条 ==="


class UpdateError(Exception):
    pass


class ParseError(UpdateError):
    pass


class MissingBaselineError(UpdateError):
    pass


DEFAULT_COLUMNS = ("text", "code", "weight")


class _HeaderLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently discarding settings."""


def _unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ParseError("词典头部存在重复或无效的键")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_HeaderLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _columns(meta):
    columns = meta.get("columns", list(DEFAULT_COLUMNS))
    if (not isinstance(columns, list) or not columns
            or any(c not in DEFAULT_COLUMNS for c in columns)
            or len(set(columns)) != len(columns) or "text" not in columns):
        raise ParseError("不支持的 columns：仅支持 text、code、weight，且必须包含 text")
    return tuple(columns)


def entry_key(e):
    return (e.text, e.code)


def render_entry(e, columns=DEFAULT_COLUMNS):
    values = e._asdict()
    for field in ("code", "weight"):
        if field not in columns and values[field] is not None:
            raise ParseError(f"输出 columns 缺少 {field}，不能丢弃已有数据")
    return "\t".join(values[field] or "" for field in columns).rstrip("\t")


def parse_dict(text):
    # C1: normalize CRLF and bare CR to LF before any processing
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = text.split("\n")
    term = None
    for i, line in enumerate(lines):
        if line.strip() == "...":
            term = i
            break
    if term is None:
        raise ParseError("missing metadata terminator '...'")

    header = "\n".join(lines[: term + 1]) + "\n"

    try:
        meta = yaml.load(header, Loader=_HeaderLoader)
    except yaml.YAMLError as exc:
        raise ParseError(f"无效的 YAML 头部：{exc}") from exc
    if not isinstance(meta, dict):
        raise ParseError("词典头部必须是 YAML 映射")
    for field in ("name", "version"):
        if field not in meta or not isinstance(meta[field], (str, int, float, datetime.date)):
            raise ParseError(f"词典头部缺少有效的 {field}")
        meta[field] = str(meta[field])
        if not meta[field]:
            raise ParseError(f"词典头部的 {field} 不能为空")
    columns = _columns(meta)
    imports = meta.get("import_tables", [])
    if not isinstance(imports, list) or any(not isinstance(s, str) or not s for s in imports):
        raise ParseError("import_tables 必须是词典名称列表")

    entries = []
    for line in lines[term + 1:]:
        stripped = line.strip()
        if stripped == "# no comment":
            raise ParseError("暂不支持 # no comment 指令，已停止以避免丢失以 # 开头的词条")
        if not stripped or line.startswith("#"):
            continue
        if stripped in ("...", "---"):
            raise ParseError("词条区存在多余的 YAML 文档标记")
        parts = line.rstrip("\n").split("\t")
        if len(parts) > len(columns):
            raise ParseError("词条列数超过 columns 定义")
        values = dict(zip(columns, parts))
        text_ = values.get("text", "")
        code = values.get("code") or None
        weight = values.get("weight") or None
        if not text_.strip() or (weight is not None and not re.fullmatch(r"\d+(?:\.\d+)?%?", weight)):
            raise ParseError("词条文本为空或词频格式无效")
        entries.append(Entry(text_, code, weight))
    return ParsedDict(header=header, meta=meta, entries=entries)


def compare(local_entries, upstream_entries):
    lkeys = {entry_key(e) for e in local_entries}
    ukeys = {entry_key(e) for e in upstream_entries}
    n_new = len(ukeys - lkeys)
    n_local_only = len(lkeys - ukeys)
    return n_new, n_local_only


def _entry_groups(entries):
    """Keep duplicate rows and their order; Rime dictionaries can contain them."""
    result = {}
    for e in entries:
        k = entry_key(e)
        if k not in result:
            result[k] = e
        elif isinstance(result[k], Entry):
            result[k] = (result[k], e)
        else:
            result[k] += (e,)
    return result


def merge_incremental(upstream_header, upstream_entries, local_entries, baseline_entries):
    if baseline_entries is None:
        raise MissingBaselineError("缺少基线，不能区分旧上游数据与本地改动；请提供历史基线，或明确选择 --overwrite")
    L = _entry_groups(local_entries)
    U = _entry_groups(upstream_entries)
    B = _entry_groups(baseline_entries)
    columns = _columns(parse_dict(upstream_header).meta)

    out_lines = []
    emitted_local = set()
    for e in upstream_entries:
        k = entry_key(e)
        le = L.get(k)
        if k in B and le is None:
            continue  # A local deletion is a local change, even if upstream reweighted it.
        if le is not None and le != U[k] and le != B.get(k):
            if k not in emitted_local:
                group = (le,) if isinstance(le, Entry) else le
                out_lines.extend(render_entry(row, columns) for row in group)
                emitted_local.add(k)
            continue
        out_lines.append(render_entry(e, columns))

    appended = []
    for le in local_entries:
        k = entry_key(le)
        if k in U:
            continue
        keep = L[k] != B.get(k)
        if keep:
            appended.append(render_entry(le, columns))

    text = upstream_header if upstream_header.endswith("\n") else upstream_header + "\n"
    if out_lines:
        text += "\n".join(out_lines) + "\n"
    if appended:
        # M9: use the module constant for the marker string
        text += f"\n{LOCAL_ENTRIES_MARKER}\n" + "\n".join(appended) + "\n"
    return text


def merge_header(up, local, baseline):
    """Merge complete metadata. Stop on conflicting structural changes."""
    if not (up.meta["name"] == local.meta["name"] == baseline.meta["name"]):
        raise UpdateError("词典 name 不一致，不能合并")
    missing = object()
    merged = dict(up.meta)
    for key in set(local.meta) | set(baseline.meta):
        if key == "version":
            continue
        lv = local.meta.get(key, missing)
        bv = baseline.meta.get(key, missing)
        uv = up.meta.get(key, missing)
        if lv == bv:
            continue
        if uv != bv and uv != lv:
            raise UpdateError(f"词典头部 {key} 在本地和上游同时修改，请先解决冲突")
        if lv is missing:
            merged.pop(key, None)
        else:
            merged[key] = lv
    if merged == up.meta:
        return up.header
    return "---\n" + yaml.safe_dump(merged, allow_unicode=True, sort_keys=False) + "...\n"


def display_width(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s, width):
    return s + " " * max(0, width - display_width(s))


def render_table(rows, head_info):
    headers = ["词库", "本地版本", "上游版本", "状态", "上游新增", "本地独有"]

    def cell(r):
        n_new = f"+{r['n_new']}" if r["n_new"] not in (None, 0) else "-"
        n_local = str(r["n_local"]) if r["n_local"] not in (None, 0) else "-"
        return [r["name"], r["local_ver"], r["up_ver"], r["status"], n_new, n_local]

    table = [headers] + [cell(r) for r in rows]
    widths = [max(display_width(row[i]) for row in table) for i in range(len(headers))]
    lines = []
    if head_info:
        lines.append(f"上游 HEAD: {head_info['sha']}  {head_info['date']}  {head_info['subject']}")
        lines.append("")
    for row in table:
        lines.append("  ".join(_pad(row[i], widths[i]) for i in range(len(headers))))

    # Summary line (spec §9)
    n_total = len(rows)
    n_updated = sum(1 for r in rows if r["status"].startswith("有更新"))
    n_same = sum(1 for r in rows if r["status"] == "相同")
    n_local_only = sum(1 for r in rows if r["status"] == "本地独有")
    n_upstream_new = sum(1 for r in rows if r["status"] == "上游新文件")
    lines.append(
        f"共 {n_total} 个词库：{n_updated} 个有更新，{n_same} 个相同，"
        f"{n_local_only} 个本地独有，{n_upstream_new} 个上游新文件。"
    )
    return "\n".join(lines) + "\n"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _normalize_newlines(text):
    """Return text with CRLF and bare CR normalized to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def find_dict_files(root):
    rels = []
    for sub in ("", "cn_dicts", "en_dicts"):
        d = os.path.join(root, sub) if sub else root
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name.endswith(".dict.yaml") and os.path.isfile(os.path.join(d, name)):
                rels.append(os.path.join(sub, name) if sub else name)
    return sorted(rels)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_equal(path_a, path_b):
    """Return True if both files have identical content after newline normalisation.

    C1: compares newline-normalised content so CRLF-vs-LF identical files are 'same'.
    """
    with open(path_a, encoding="utf-8") as f:
        a = _normalize_newlines(f.read())
    with open(path_b, encoding="utf-8") as f:
        b = _normalize_newlines(f.read())
    return a == b


def dict_stats(local_path, up_path):
    lp = parse_dict(_read(local_path))
    up = parse_dict(_read(up_path))
    n_new, n_local = compare(lp.entries, up.entries)
    return lp.meta.get("version", "?"), up.meta.get("version", "?"), n_new, n_local


def _safe_version(path):
    """Return the version metadata string from a dict file, or '?' on ParseError.

    C2: used in all non-updated branches so a malformed file never aborts the table.
    """
    try:
        return parse_dict(_read(path)).meta.get("version", "?")
    except ParseError:
        return "?"


def discover_dicts(local_dir, upstream_dir):
    local_rels = set(find_dict_files(local_dir))
    up_rels = set(find_dict_files(upstream_dir))
    out = []
    for rel in sorted(up_rels | local_rels):
        lp = os.path.join(local_dir, rel)
        upp = os.path.join(upstream_dir, rel)
        if rel in up_rels and rel in local_rels:
            # C1: compare newline-normalised content so CRLF-vs-LF is treated as "same"
            status = "same" if _content_equal(lp, upp) else "updated"
        elif rel in up_rels:
            status = "upstream_new"
        else:
            status = "local_only"
        out.append({"name": rel, "status": status, "local_path": lp, "up_path": upp})
    return out


def atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d)
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else 0o600
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _sync_dir(d)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _copy_rel(rels, src_dir, dst_dir):
    for rel in rels:
        src = os.path.join(src_dir, rel)
        dst = os.path.join(dst_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        with open(dst, "rb") as copied:
            os.fsync(copied.fileno())
        _sync_dir(os.path.dirname(dst))


def backup(rels, rime_dir, backup_dir):
    _copy_rel(rels, rime_dir, backup_dir)
    return backup_dir


def update_baseline(rels, upstream_dir, baseline_dir):
    _copy_rel(rels, upstream_dir, baseline_dir)
    return baseline_dir


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)


def _repo_identity(repo):
    if repo.startswith("git@github.com:"):
        repo = "https://github.com/" + repo.split(":", 1)[1]
    if "://" not in repo:
        return os.path.realpath(repo)
    return re.sub(r"\.git$", "", repo.rstrip("/"))


def fetch_upstream(repo, cache_dir):
    if os.path.isdir(os.path.join(cache_dir, ".git")):
        origin = _run(["git", "-C", cache_dir, "remote", "get-url", "origin"]).stdout.strip()
        if _repo_identity(origin) != _repo_identity(repo):
            raise UpdateError("缓存仓库来源与 --repo 不一致，请使用独立的 --cache-dir")
        if _run(["git", "-C", cache_dir, "status", "--porcelain"]).stdout.strip():
            raise UpdateError("上游缓存存在本地改动，请使用新的 --cache-dir")
        _run(["git", "-C", cache_dir, "fetch", "--depth", "1", "origin", "HEAD"])
        _run(["git", "-C", cache_dir, "reset", "--hard", "FETCH_HEAD"])
    else:
        parent = os.path.dirname(cache_dir)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _run(["git", "clone", "--depth", "1", repo, cache_dir])
    out = _run(["git", "-C", cache_dir, "log", "-1", "--format=%H|%ci|%s"]).stdout.strip()
    sha, date, subject = out.split("|", 2)
    return cache_dir, {"sha": sha, "date": date.split()[0], "subject": subject}


def _safe_path(root, rel):
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise UpdateError(f"无效的相对路径：{rel}")
    target = Path(root)
    for part in path.parts:
        target = target / part
        if target.is_symlink():
            raise UpdateError(f"不处理符号链接：{target}")
    return target


def prepare_update(rels, rime_dir, upstream_dir, baseline_dir, mode):
    """Build the complete plan in memory. This function never writes files."""
    writes = {"rime": {}, "baseline": {}}
    expected = {}

    def read(root, rel, track=False):
        path = _safe_path(root, rel)
        data = path.read_bytes() if path.exists() else None
        if track:
            digest = hashlib.sha256(data).hexdigest() if data is not None else None
            if str(path) in expected and expected[str(path)] != digest:
                raise UpdateError(f"文件在检查期间发生变化，已停止：{path}")
            expected[str(path)] = digest
        return data.decode("utf-8") if data is not None else None

    for rel in rels:
        try:
            up_text = read(upstream_dir, rel)
            local_text = read(rime_dir, rel, True)
            base_text = read(baseline_dir, rel, True)
            up = parse_dict(up_text)
            if mode == "overwrite" or local_text is None:
                merged = up_text
            else:
                local = parse_dict(local_text)
                if _normalize_newlines(local_text) == _normalize_newlines(up_text):
                    merged = local_text
                else:
                    if base_text is None:
                        raise MissingBaselineError("缺少基线。请提供历史基线，或明确选择 --overwrite；未修改任何词库")
                    base = parse_dict(base_text)
                    header = merge_header(up, local, base)
                    merged = merge_incremental(header, up.entries, local.entries, base.entries)
            parse_dict(merged)
            if merged != local_text:
                writes["rime"][rel] = merged
            if up_text != base_text:
                writes["baseline"][rel] = up_text
        except UpdateError as exc:
            raise type(exc)(f"{rel}：{exc}") from exc

    # Validate the resulting import graph. Include newly required upstream tables,
    # but do not install unrelated new dictionaries.
    visited = set()
    visiting = set()
    available = set(find_dict_files(upstream_dir))

    def visit(rel):
        if rel in visiting:
            raise UpdateError(f"词典引用存在循环：{rel}")
        if rel in visited:
            return
        visiting.add(rel)
        content = writes["rime"].get(rel)
        if content is None:
            content = read(rime_dir, rel, True)
        if content is None:
            if rel not in available:
                raise UpdateError(f"缺少引用词典：{rel}")
            content = read(upstream_dir, rel)
            parsed = parse_dict(content)
            read(baseline_dir, rel, True)
            writes["rime"][rel] = content
            writes["baseline"][rel] = content
        else:
            parsed = parse_dict(content)
        for table in parsed.meta.get("import_tables", []):
            dependency = table + ".dict.yaml"
            _safe_path(rime_dir, dependency)
            if dependency != rel:
                visit(dependency)
        visiting.remove(rel)
        visited.add(rel)

    for rel in rels:
        visit(rel)
    return writes, expected


JOURNAL = ".rimeup-transaction"


def _sync_dir(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _lock(path):
    import fcntl
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("另一个 rimeup 正在运行，请稍后再试") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _restore_file(source, target, mode):
    """Restore exact bytes, including CRLF, without using the update write hook."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as output, open(source, "rb") as original:
            shutil.copyfileobj(original, output)
            os.fchmod(output.fileno(), mode)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, target)
        _sync_dir(str(target.parent))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def recover_transaction(rime_dir, baseline_dir, check_current=True):
    journal = _safe_path(rime_dir, JOURNAL)
    if not journal.exists():
        return False
    manifest_path = journal / "manifest.json"
    if not manifest_path.exists():
        # Preparation never writes destinations until the manifest is durable.
        shutil.rmtree(journal)
        return True
    manifest = json.loads(manifest_path.read_text())
    roots = {"rime": str(Path(rime_dir).resolve()), "baseline": str(Path(baseline_dir).resolve())}
    if manifest.get("roots") != roots:
        raise UpdateError("恢复记录的目录不匹配，请使用原来的 --baseline-dir")
    if manifest.get("status") not in ("prepared", "committed"):
        raise UpdateError("恢复记录状态无效，已停止")
    if manifest.get("status") != "committed":
        items = manifest["items"]
        # Validate every recovery source before changing any destination.
        for item in items:
            target = _safe_path(roots[item["scope"]], item["rel"])
            if item["existed"]:
                source = _safe_path(journal, item["old"])
                if _sha256(source) != item["sha256"]:
                    raise UpdateError("恢复副本校验失败；保留恢复记录，请从备份手动恢复")
            if check_current:
                current = _sha256(target) if target.exists() else None
                allowed = (item.get("sha256"), item["new_sha256"])
                if current not in allowed:
                    raise UpdateError(f"中断后文件又发生变化，已停止恢复以保留这些改动：{target}")
        for item in reversed(items):
            target = _safe_path(roots[item["scope"]], item["rel"])
            if item["existed"]:
                _restore_file(journal / item["old"], target, item["mode"])
            elif target.exists():
                target.unlink()
                _sync_dir(str(target.parent))
    shutil.rmtree(journal)
    _sync_dir(rime_dir)
    return True


def apply_transaction(writes, expected, rime_dir, baseline_dir, backup_dir):
    """Back up, stage, and commit dictionaries and baselines as one recoverable batch."""
    roots = {"rime": str(Path(rime_dir).resolve()), "baseline": str(Path(baseline_dir).resolve())}
    journal = _safe_path(roots["rime"], JOURNAL)
    if journal.exists():
        raise UpdateError("发现未完成的更新，请先运行 rimeup --recover")
    for path, digest in expected.items():
        current = _sha256(path) if os.path.exists(path) else None
        if current != digest:
            raise UpdateError(f"文件在检查后发生变化，已停止：{path}")
    backup_path = Path(backup_dir)
    if backup_path.exists() and any(backup_path.iterdir()):
        raise UpdateError("备份目录非空，请使用新的 --backup-dir")
    backup_path.mkdir(parents=True, exist_ok=True)
    backup([rel for rel in writes["rime"] if _safe_path(rime_dir, rel).exists()], rime_dir, backup_dir)
    journal.mkdir()
    manifest = {"roots": roots, "status": "prepared", "items": []}
    try:
        for scope, files in writes.items():
            for rel, content in files.items():
                target = _safe_path(roots[scope], rel)
                index = len(manifest["items"])
                item = {"scope": scope, "rel": rel, "old": f"old-{index}",
                        "new": f"new-{index}", "existed": target.exists()}
                if target.exists():
                    item["mode"] = stat.S_IMODE(target.stat().st_mode)
                    _restore_file(target, journal / item["old"], item["mode"])
                    item["sha256"] = _sha256(journal / item["old"])
                atomic_write(str(journal / item["new"]), content)
                item["new_sha256"] = _sha256(journal / item["new"])
                manifest["items"].append(item)
        # Check again after staging to avoid overwriting edits made during preparation.
        for path, digest in expected.items():
            if (_sha256(path) if os.path.exists(path) else None) != digest:
                raise UpdateError(f"文件在准备期间发生变化，已停止：{path}")
        atomic_write(str(journal / "manifest.json"), json.dumps(manifest, ensure_ascii=False))
        _sync_dir(rime_dir)
        for item in manifest["items"]:
            target = _safe_path(roots[item["scope"]], item["rel"])
            atomic_write(str(target), (journal / item["new"]).read_bytes().decode("utf-8"))
            if _sha256(target) != item["new_sha256"]:
                raise UpdateError(f"写入后校验失败：{target}")
        manifest["status"] = "committed"
        atomic_write(str(journal / "manifest.json"), json.dumps(manifest, ensure_ascii=False))
    except BaseException:
        try:
            recover_transaction(rime_dir, baseline_dir, check_current=False)
        except Exception as recovery_error:
            raise UpdateError(f"更新失败，自动回退也失败；已保留 {journal}，请运行 --recover。{recovery_error}") from recovery_error
        raise
    # A crash during cleanup leaves a committed journal; recovery only removes it.
    shutil.rmtree(journal)
    _sync_dir(rime_dir)


UPSTREAM_REPO = "https://github.com/iDvel/rime-ice.git"
STATUS_LABEL = {"same": "相同", "updated": "有更新",
                "upstream_new": "上游新文件", "local_only": "本地独有"}


def _default(path):
    return os.path.expanduser(path)


def _baseline_default(rime_dir, repo):
    identity = os.path.realpath(os.path.expanduser(rime_dir)) + "\n" + _repo_identity(repo)
    key = hashlib.sha256(identity.encode()).hexdigest()[:20]
    return _default(f"~/Library/Application Support/rimeup/baselines/{key}")


def _validate_directories(args):
    names = ("rime_dir", "cache_dir", "baseline_dir", "backup_dir")
    for name in names:
        setattr(args, name, os.path.realpath(os.path.expanduser(getattr(args, name))))
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pa, pb = getattr(args, a), getattr(args, b)
            if os.path.commonpath([pa, pb]) in (pa, pb):
                raise UpdateError(f"{a} 与 {b} 不能相同或互相包含")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    p = argparse.ArgumentParser(prog="rimeup", description="从 rime-ice 上游更新本地 Rime 词库")
    p.add_argument("--rime-dir", default=os.environ.get("RIME_DIR", _default("~/Library/Rime")))
    p.add_argument("--repo", default=UPSTREAM_REPO)
    p.add_argument("--cache-dir", default=_default("~/.cache/rimeup/rime-ice"))
    p.add_argument("--baseline-dir", help="历史上游基线目录；默认按 Rime 目录和上游仓库独立保存")
    p.add_argument("--backup-dir",
                   default=_default(f"~/Library/Application Support/rimeup/backups/{ts}"))
    p.add_argument("--check", "-n", action="store_true", help="拉取并检查，不修改词库、基线或备份")
    p.add_argument("--recover", action="store_true", help="恢复中断的更新，不拉取或更新词库")
    # M7: --overwrite and --incremental are mutually exclusive
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument("--overwrite", action="store_true", help="整体覆盖更新")
    mode_group.add_argument("--incremental", action="store_true", help="词条级增量合并")
    args = p.parse_args(argv)
    args.baseline_dir = args.baseline_dir or _baseline_default(args.rime_dir, args.repo)
    if args.recover and (args.check or args.overwrite or args.incremental):
        p.error("--recover 不能与检查或更新选项同时使用")
    if not args.recover and shutil.which("git") is None:
        print("错误：未找到 git，请先安装：brew install git", file=sys.stderr)
        return 1

    if not os.path.isdir(args.rime_dir):
        print(f"错误：Rime 目录不存在：{args.rime_dir}", file=sys.stderr)
        return 2

    try:
        _validate_directories(args)
        if args.recover:
            with _lock(os.path.join(args.rime_dir, ".rimeup.lock")):
                restored = recover_transaction(args.rime_dir, args.baseline_dir)
            print("已恢复中断的更新。" if restored else "没有需要恢复的更新。")
            return 0
        if os.path.lexists(os.path.join(args.rime_dir, JOURNAL)):
            raise UpdateError("发现未完成的更新；请先使用相同目录参数运行 rimeup --recover")
        with _lock(args.cache_dir + ".lock"):
            if args.check:
                return _execute(args)
            with _lock(os.path.join(args.rime_dir, ".rimeup.lock")):
                return _execute(args)
    except (UpdateError, OSError, UnicodeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("已取消。", file=sys.stderr)
        return 130


def _execute(args):
    identity = {"rime_dir": args.rime_dir, "repo": _repo_identity(args.repo)}
    identity_path = _safe_path(args.baseline_dir, ".rimeup-source.json")
    identity_text = identity_path.read_text() if identity_path.exists() else None
    if identity_text is not None and json.loads(identity_text) != identity:
        raise UpdateError("基线属于其他 Rime 目录或上游仓库，请使用独立的 --baseline-dir")
    stale = False
    try:
        cache, head = fetch_upstream(args.repo, args.cache_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        if not args.check or not os.path.isdir(os.path.join(args.cache_dir, ".git")):
            raise UpdateError(f"无法获取上游，已停止：{exc}") from exc
        print(f"警告：拉取失败，以下仅为旧缓存比较，不能证明已是最新：{exc}", file=sys.stderr)
        cache, head = args.cache_dir, {"sha": "?", "date": "?", "subject": "旧缓存，仅供检查"}
        stale = True

    discovered = discover_dicts(args.rime_dir, cache)
    rows = []
    invalid = False
    for d in discovered:
        if d["status"] == "updated":
            try:
                lv, uv, n_new, n_local = dict_stats(d["local_path"], d["up_path"])
                status_label = STATUS_LABEL[d["status"]]
            except (ParseError, UnicodeError):
                lv = uv = "?"
                n_new = n_local = None
                status_label = "有更新(解析失败)"
                invalid = True
        elif d["status"] == "upstream_new":
            # C2: use _safe_version so a malformed file returns '?' instead of crashing
            lv, uv, n_new, n_local = "—", _safe_version(d["up_path"]), None, None
            status_label = STATUS_LABEL[d["status"]]
        elif d["status"] == "local_only":
            # C2: use _safe_version so a malformed file returns '?' instead of crashing
            lv, uv, n_new, n_local = _safe_version(d["local_path"]), "—", None, None
            status_label = STATUS_LABEL[d["status"]]
        else:  # same
            # C2: use _safe_version so a malformed file returns '?' instead of crashing
            v = _safe_version(d["local_path"])
            lv, uv, n_new, n_local = v, v, None, None
            status_label = STATUS_LABEL[d["status"]]
        rows.append({"name": d["name"], "local_ver": lv, "up_ver": uv,
                     "status": status_label, "n_new": n_new, "n_local": n_local})
    print(render_table(rows, head))

    updated = [d["name"] for d in discovered if d["status"] == "updated"]
    if args.check:
        if not stale and (args.incremental or args.overwrite):
            common = [d["name"] for d in discovered if d["status"] in ("same", "updated")]
            plan, _ = prepare_update(common, args.rime_dir, cache, args.baseline_dir,
                                     "overwrite" if args.overwrite else "incremental")
            print(f"预检通过：计划修改 {len(plan['rime'])} 个词库；未写入。")
            return 0
        return 1 if stale or invalid else 0

    mode = "overwrite" if args.overwrite else "incremental" if args.incremental else None
    if mode is None and not updated:
        mode = "incremental"  # Identical files can safely seed their upstream baseline.
    if mode is None:
        print("\n[1] 覆盖更新   [2] 增量合并   [3] 不更新")
        choice = input("请选择 [1/2/3]: ").strip()
        mode = {"1": "overwrite", "2": "incremental", "3": "skip"}.get(choice, "skip")
    if mode == "skip":
        print("未做任何更改。")
        return 0

    common = [d["name"] for d in discovered if d["status"] in ("same", "updated")]
    if not common:
        print("没有与上游对应的本地词库，未更新。")
        return 0
    writes, expected = prepare_update(common, args.rime_dir, cache, args.baseline_dir, mode)
    if identity_text is None:
        writes["baseline"][".rimeup-source.json"] = json.dumps(identity, ensure_ascii=False)
        expected[str(identity_path)] = None
    if not any(writes.values()):
        print("没有需要写入的词库或基线。")
        return 0
    apply_transaction(writes, expected, args.rime_dir, args.baseline_dir, args.backup_dir)

    print(f"\n已{'覆盖' if mode == 'overwrite' else '增量合并'}更新 {len(writes['rime'])} 个词库；基线已保存。")
    print(f"备份：{args.backup_dir}")
    if writes["rime"]:
        print("请在输入法菜单点「重新部署」让改动生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
