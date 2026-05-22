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

Entry = namedtuple("Entry", ["text", "code", "weight"])
ParsedDict = namedtuple("ParsedDict", ["header", "meta", "entries"])

# M9: module constant for the local-only section marker used in merge output
LOCAL_ENTRIES_MARKER = "# === rimeup 保留的本地词条 ==="


class ParseError(Exception):
    pass


def entry_key(e):
    return (e.text, e.code)


def render_entry(e):
    if e.weight is not None:
        return f"{e.text}\t{e.code}\t{e.weight}"
    if e.code != e.text:
        return f"{e.text}\t{e.code}"
    return e.text


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

    meta = {}
    for line in lines[: term + 1]:
        s = line.strip()
        for field in ("version", "sort", "name"):
            prefix = field + ":"
            if s.startswith(prefix):
                raw = s[len(prefix):].strip()
                # M6: strip trailing inline comment before stripping quotes
                comment_idx = raw.find(" #")
                if comment_idx != -1:
                    raw = raw[:comment_idx].strip()
                val = raw.strip('"')
                meta[field] = val

    entries = []
    for line in lines[term + 1:]:
        stripped = line.strip()
        # Rime comments start with '#' — this matches Rime's own comment semantics, so it is correct.
        if not stripped or stripped.startswith("#"):
            continue
        # I3: skip document boundary markers that may appear in the entry body
        if stripped in ("...", "---"):
            continue
        parts = line.rstrip("\n").split("\t")
        text_ = parts[0]
        code = parts[1] if len(parts) > 1 and parts[1] != "" else text_
        weight = parts[2] if len(parts) > 2 and parts[2] != "" else None
        entries.append(Entry(text_, code, weight))
    return ParsedDict(header=header, meta=meta, entries=entries)


def compare(local_entries, upstream_entries):
    lkeys = {entry_key(e) for e in local_entries}
    ukeys = {entry_key(e) for e in upstream_entries}
    n_new = len(ukeys - lkeys)
    n_local_only = len(lkeys - ukeys)
    return n_new, n_local_only


def _to_map(entries):
    return {entry_key(e): e for e in entries}


def merge_incremental(upstream_header, upstream_entries, local_entries, baseline_entries):
    first_run = baseline_entries is None
    L = _to_map(local_entries)
    U = _to_map(upstream_entries)
    B = _to_map(baseline_entries) if not first_run else {}

    out_lines = []
    for e in upstream_entries:
        k = entry_key(e)
        le = L.get(k)
        if le is not None and le.weight != e.weight:
            if first_run:
                use_local = True
            else:
                be = B.get(k)
                use_local = (be is None) or (be.weight != le.weight)
            if use_local:
                out_lines.append(render_entry(le))
                continue
        out_lines.append(render_entry(e))

    appended = []
    for le in local_entries:
        k = entry_key(le)
        if k in U:
            continue
        if first_run:
            keep = True
        else:
            be = B.get(k)
            keep = (be is None) or (be != le)
        if keep:
            appended.append(render_entry(le))

    text = upstream_header if upstream_header.endswith("\n") else upstream_header + "\n"
    if out_lines:
        text += "\n".join(out_lines) + "\n"
    if appended:
        # M9: use the module constant for the marker string
        text += f"\n{LOCAL_ENTRIES_MARKER}\n" + "\n".join(appended) + "\n"
    return text


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
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
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


def backup(rels, rime_dir, backup_dir):
    _copy_rel(rels, rime_dir, backup_dir)
    return backup_dir


def update_baseline(rels, upstream_dir, baseline_dir):
    _copy_rel(rels, upstream_dir, baseline_dir)
    return baseline_dir


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def fetch_upstream(repo, cache_dir):
    if os.path.isdir(os.path.join(cache_dir, ".git")):
        _run(["git", "-C", cache_dir, "fetch", "--depth", "1", "origin", "HEAD"])
        _run(["git", "-C", cache_dir, "reset", "--hard", "FETCH_HEAD"])
    else:
        parent = os.path.dirname(cache_dir)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _run(["git", "clone", "--depth", "1", repo, cache_dir])
    out = _run(["git", "-C", cache_dir, "log", "-1", "--format=%h|%ci|%s"]).stdout.strip()
    sha, date, subject = out.split("|", 2)
    return cache_dir, {"sha": sha, "date": date.split()[0], "subject": subject}


def apply_overwrite(rels, rime_dir, upstream_dir):
    for rel in rels:
        atomic_write(os.path.join(rime_dir, rel), _read(os.path.join(upstream_dir, rel)))


def apply_incremental(rels, rime_dir, upstream_dir, baseline_dir):
    """Apply incremental merge for each rel.

    Returns (first_run_rels, warnings):
      - first_run_rels: list of rels that had no baseline (first run)
      - warnings: list of warning strings for files that hit ParseError and were overwritten
    """
    first_run_rels = []
    warnings = []
    for rel in rels:
        try:
            up = parse_dict(_read(os.path.join(upstream_dir, rel)))
            loc = parse_dict(_read(os.path.join(rime_dir, rel)))
            base_path = os.path.join(baseline_dir, rel)
            base_entries = parse_dict(_read(base_path)).entries if os.path.exists(base_path) else None
            if base_entries is None:
                first_run_rels.append(rel)
            merged = merge_incremental(up.header, up.entries, loc.entries, base_entries)
            atomic_write(os.path.join(rime_dir, rel), merged)
        except ParseError:
            atomic_write(os.path.join(rime_dir, rel), _read(os.path.join(upstream_dir, rel)))
            warnings.append(f"⚠️ {rel}：解析失败，已改为整体覆盖")
    return first_run_rels, warnings


UPSTREAM_REPO = "https://github.com/iDvel/rime-ice.git"
STATUS_LABEL = {"same": "相同", "updated": "有更新",
                "upstream_new": "上游新文件", "local_only": "本地独有"}


def _default(path):
    return os.path.expanduser(path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if shutil.which("git") is None:
        print("错误：未找到 git，请先安装：brew install git", file=sys.stderr)
        return 1
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    p = argparse.ArgumentParser(prog="rimeup", description="从 rime-ice 上游更新本地 Rime 词库")
    p.add_argument("--rime-dir", default=os.environ.get("RIME_DIR", _default("~/Library/Rime")))
    p.add_argument("--repo", default=UPSTREAM_REPO)
    p.add_argument("--cache-dir", default=_default("~/.cache/rimeup/rime-ice"))
    p.add_argument("--baseline-dir", default=_default("~/.cache/rimeup/baseline"))
    p.add_argument("--backup-dir",
                   default=_default(f"~/Library/Application Support/rimeup/backups/{ts}"))
    p.add_argument("--check", "-n", action="store_true", help="只展示表格，不改动")
    # M7: --overwrite and --incremental are mutually exclusive
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument("--overwrite", action="store_true", help="整体覆盖更新")
    mode_group.add_argument("--incremental", action="store_true", help="词条级增量合并")
    args = p.parse_args(argv)

    if not os.path.isdir(args.rime_dir):
        print(f"错误：Rime 目录不存在：{args.rime_dir}", file=sys.stderr)
        return 2

    try:
        cache, head = fetch_upstream(args.repo, args.cache_dir)
    except Exception as e:  # noqa: BLE001
        if os.path.isdir(os.path.join(args.cache_dir, ".git")):
            print(f"警告：拉取上游失败，改用旧缓存：{e}", file=sys.stderr)
            cache = args.cache_dir
            head = {"sha": "?", "date": "?", "subject": "(stale cache)"}
        else:
            print(f"错误：无法获取上游：{e}", file=sys.stderr)
            return 1

    discovered = discover_dicts(args.rime_dir, cache)
    rows = []
    for d in discovered:
        if d["status"] == "updated":
            try:
                lv, uv, n_new, n_local = dict_stats(d["local_path"], d["up_path"])
                status_label = STATUS_LABEL[d["status"]]
            except ParseError:
                lv = uv = "?"
                n_new = n_local = None
                status_label = "有更新(解析失败)"
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
    if not updated:
        print("没有需要更新的词库。")
        return 0

    if args.check:
        return 0

    mode = "overwrite" if args.overwrite else "incremental" if args.incremental else None
    if mode is None:
        print("\n[1] 覆盖更新   [2] 增量合并   [3] 不更新")
        choice = input("请选择 [1/2/3]: ").strip()
        mode = {"1": "overwrite", "2": "incremental", "3": "skip"}.get(choice, "skip")
    if mode == "skip":
        print("未做任何更改。")
        return 0

    backup(updated, args.rime_dir, args.backup_dir)
    if mode == "overwrite":
        apply_overwrite(updated, args.rime_dir, cache)
    else:
        first_run_rels, parse_warnings = apply_incremental(updated, args.rime_dir, cache, args.baseline_dir)
        for w in parse_warnings:
            print(w)
        if first_run_rels:
            print("⚠️ 首次增量无基线，已保留全部本地差异；下次起将精确区分。")
    update_baseline(updated, cache, args.baseline_dir)

    print(f"\n已{'覆盖' if mode == 'overwrite' else '增量合并'}更新 {len(updated)} 个词库。")
    print(f"备份：{args.backup_dir}")
    print("⚠️ 请在输入法菜单点「重新部署」让改动生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
