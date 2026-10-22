"""Regression tests for data loss, format compatibility, and interrupted updates.

All dictionaries, caches, baselines, backups, and journals live under tmp_path.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
import yaml

import rimeup as r


def dictionary(body="a\ta\t100\n", version="1", **metadata):
    meta = {"name": "d", "version": version, "sort": "by_weight"}
    meta.update(metadata)
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "...\n" + body


def put(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def trees(tmp_path):
    local, up, base, backup = [tmp_path / s for s in ("local", "up", "base", "backup")]
    for root in (local, up, base):
        root.mkdir()
    for name in ("a.dict.yaml", "b.dict.yaml"):
        put(local, name, dictionary())
        put(base, name, dictionary())
        put(up, name, dictionary("a\ta\t200\n", version="2"))
    return local, up, base, backup


def plan(trees, mode="incremental"):
    local, up, base, _ = trees
    return r.prepare_update(["a.dict.yaml", "b.dict.yaml"], str(local), str(up), str(base), mode)


def apply(trees):
    local, _, base, backup = trees
    writes, expected = plan(trees)
    r.apply_transaction(writes, expected, str(local), str(base), str(backup))


def cli_args(trees, *flags):
    local, up, base, backup = trees
    return ["--rime-dir", str(local), "--repo", str(up),
            "--cache-dir", str(local.parent / "cache"),
            "--baseline-dir", str(base), "--backup-dir", str(backup), *flags]


def run_cli(trees, *flags):
    with patch.object(r, "fetch_upstream", return_value=(str(trees[1]), None)):
        return r.main(cli_args(trees, *flags))


@pytest.mark.parametrize("columns,row,expected", [
    (["text", "weight"], "测试词\t500", r.Entry("测试词", None, "500")),
    (["weight", "text", "code"], "500\t测试词\tce shi ci", r.Entry("测试词", "ce shi ci", "500")),
    (["text", "code", "weight"], "测试词\t\t500", r.Entry("测试词", None, "500")),
    (["text"], "测试词", r.Entry("测试词", None, None)),
])
def test_column_mapping_round_trip(columns, row, expected):
    parsed = r.parse_dict(dictionary(row + "\n", columns=columns))
    assert parsed.entries == [expected]
    assert r.render_entry(expected, columns) == row


def test_tencent_reweight_remains_one_entry():
    def parsed(weight):
        return r.parse_dict(dictionary(f"测试词\t{weight}\n", columns=["text", "weight"]))
    up, local, base = parsed(200), parsed(500), parsed(100)
    merged = r.parse_dict(r.merge_incremental(up.header, up.entries, local.entries, base.entries))
    assert merged.entries == [r.Entry("测试词", None, "500")]
    assert r.compare(local.entries, up.entries) == (0, 0)


def test_tencent_untouched_weight_follows_upstream():
    old = r.parse_dict(dictionary("测试词\t100\n", columns=["text", "weight"]))
    up = r.parse_dict(dictionary("测试词\t200\n", columns=["text", "weight"]))
    merged = r.parse_dict(r.merge_incremental(up.header, up.entries, old.entries, old.entries))
    assert merged.entries == [r.Entry("测试词", None, "200")]


def test_column_removal_cannot_drop_a_code():
    header = r.parse_dict(dictionary("", columns=["text", "weight"])).header
    entry = r.Entry("测试词", "ce shi ci", "100")
    with pytest.raises(r.ParseError):
        r.merge_incremental(header, [entry], [entry], [entry])


@pytest.mark.parametrize("bad", [
    "---\nname: d\n...\n",                     # missing version
    "---\nname: d\nversion: 1\nname: other\n...\n",  # duplicate key
    "---\nname: [unterminated\nversion: 1\n...\n",
    dictionary("a\ta\t100\textra\n"),
    dictionary("a\ta\tnot-a-weight\n"),
    dictionary("a\ta\t1\n", columns=["text", "stem"]),
    dictionary("a\ta\t1\n", columns=["text", "text"]),
    dictionary("a\ta\t1\n", import_tables="not-a-list"),
])
def test_invalid_dictionary_rejected(bad):
    with pytest.raises(r.ParseError):
        r.parse_dict(bad)


def test_local_deletion_and_code_edit_survive_two_updates():
    base = r.parse_dict(dictionary("a\ta\t100\nb\told\t100\n"))
    local = r.parse_dict(dictionary("b\tnew\t500\n"))
    up = r.parse_dict(dictionary("a\ta\t200\nb\told\t200\nc\tc\t100\n", version="2"))
    first = r.parse_dict(r.merge_incremental(up.header, up.entries, local.entries, base.entries))
    assert first.entries == [r.Entry("c", "c", "100"), r.Entry("b", "new", "500")]
    later = r.parse_dict(dictionary("a\ta\t300\nb\told\t300\nc\tc\t200\n", version="3"))
    second = r.parse_dict(r.merge_incremental(later.header, later.entries, first.entries, up.entries))
    assert second.entries == [r.Entry("c", "c", "200"), r.Entry("b", "new", "500")]


def test_header_preserves_local_imports_and_unknown_settings():
    base = r.parse_dict(dictionary(import_tables=["base"], encoder={"max_phrase_length": 4}))
    local = r.parse_dict(dictionary(import_tables=["base", "mydict"], encoder={"max_phrase_length": 6}))
    up = r.parse_dict(dictionary(version="2", import_tables=["base"], encoder={"max_phrase_length": 4}))
    result = r.parse_dict(r.merge_header(up, local, base)).meta
    assert result["import_tables"] == ["base", "mydict"]
    assert result["encoder"] == {"max_phrase_length": 6}
    assert result["version"] == "2"


def test_header_conflict_stops_instead_of_losing_local_imports():
    base = r.parse_dict(dictionary(import_tables=["base"]))
    local = r.parse_dict(dictionary(import_tables=["base", "mine"]))
    up = r.parse_dict(dictionary(import_tables=["base", "new"]))
    with pytest.raises(r.UpdateError, match="import_tables"):
        r.merge_header(up, local, base)


@pytest.mark.parametrize("source", ["local", "up", "base"])
def test_bad_input_never_falls_back_to_overwrite(trees, source):
    local, up, base, backup = trees
    root = {"local": local, "up": up, "base": base}[source]
    put(root, "b.dict.yaml", "---\nname: incomplete\n")
    before = {p: p.read_bytes() for root in (local, base) for p in root.glob("*.yaml")}
    assert run_cli(trees, "--incremental") != 0
    assert {p: p.read_bytes() for p in before} == before
    assert not backup.exists()


def test_missing_baseline_does_not_freeze_realistic_old_weights(trees):
    local, up, base, backup = trees
    put(local, "b.dict.yaml", dictionary("的\tbai'shao\n"))
    put(up, "b.dict.yaml", dictionary("的\tbai'shao\t40529\n", version="2"))
    (base / "b.dict.yaml").unlink()
    assert run_cli(trees, "--incremental") != 0
    assert r.parse_dict((local / "b.dict.yaml").read_text()).entries[0].weight is None
    assert r.parse_dict((local / "a.dict.yaml").read_text()).entries[0].weight == "100"
    assert not backup.exists()


def test_same_files_are_seeded_and_next_update_gets_new_weights(trees):
    local, up, base, backup = trees
    for path in base.iterdir():
        path.unlink()
    for name in ("a.dict.yaml", "b.dict.yaml"):
        put(up, name, (local / name).read_text())
    assert run_cli(trees, "--incremental") == 0
    assert (base / "b.dict.yaml").read_bytes() == (up / "b.dict.yaml").read_bytes()
    put(up, "b.dict.yaml", dictionary("a\ta\t200\n", version="2"))
    trees = (local, up, base, backup.parent / "backup2")
    assert run_cli(trees, "--incremental") == 0
    assert r.parse_dict((local / "b.dict.yaml").read_text()).entries[0].weight == "200"


def test_new_required_table_is_included_but_unrelated_table_is_not(trees):
    local, up, base, _ = trees
    put(up, "a.dict.yaml", dictionary(version="2", import_tables=["cn_dicts/new"]))
    put(up, "cn_dicts/new.dict.yaml", dictionary(name="new"))
    put(up, "cn_dicts/unrelated.dict.yaml", dictionary(name="unrelated"))
    apply(trees)
    assert (local / "cn_dicts/new.dict.yaml").read_bytes() == (up / "cn_dicts/new.dict.yaml").read_bytes()
    assert (base / "cn_dicts/new.dict.yaml").exists()
    assert not (local / "cn_dicts/unrelated.dict.yaml").exists()


@pytest.mark.parametrize("imports", [["missing"], ["../outside"], ["b"]])
def test_missing_unsafe_or_cyclic_import_aborts(trees, imports):
    local, up, _, backup = trees
    put(up, "a.dict.yaml", dictionary(version="2", import_tables=imports))
    if imports == ["b"]:
        put(up, "b.dict.yaml", dictionary(version="2", import_tables=["a"]))
    assert run_cli(trees, "--incremental") != 0
    assert (local / "a.dict.yaml").read_text() == dictionary()
    assert not backup.exists()


@pytest.mark.parametrize("scope", ["rime", "baseline"])
def test_mid_batch_failure_restores_both_dictionaries_and_baselines(trees, scope):
    local, _, base, backup = trees
    # Byte-exact restoration must preserve CRLF and file permissions.
    original = (local / "a.dict.yaml").read_text().replace("\n", "\r\n").encode()
    (local / "a.dict.yaml").write_bytes(original)
    (local / "a.dict.yaml").chmod(0o640)
    snapshot = {p: p.read_bytes() for root in (local, base) for p in root.glob("*.yaml")}
    fail_path = (local if scope == "rime" else base) / "b.dict.yaml"
    real_write = r.atomic_write

    def fail(path, text):
        if Path(path) == fail_path:
            raise OSError("injected write failure")
        real_write(path, text)

    with patch.object(r, "atomic_write", side_effect=fail):
        assert run_cli(trees, "--incremental") != 0
    assert {p: p.read_bytes() for p in snapshot} == snapshot
    assert (local / "a.dict.yaml").stat().st_mode & 0o777 == 0o640
    assert (backup / "a.dict.yaml").read_bytes() == original
    assert not (local / r.JOURNAL).exists()
    assert not (base / ".rimeup-source.json").exists()


def test_failure_removes_new_dependency(trees):
    local, up, base, _ = trees
    put(up, "a.dict.yaml", dictionary(version="2", import_tables=["cn_dicts/new"]))
    put(up, "cn_dicts/new.dict.yaml", dictionary(name="new"))
    real_write = r.atomic_write

    def fail(path, text):
        if Path(path) == base / "b.dict.yaml":
            raise OSError("fail after the new table was installed")
        real_write(path, text)

    with patch.object(r, "atomic_write", side_effect=fail):
        assert run_cli(trees, "--incremental") != 0
    assert not (local / "cn_dicts/new.dict.yaml").exists()
    assert (local / "a.dict.yaml").read_text() == dictionary()


def test_hard_process_exit_can_be_recovered_without_network(trees):
    local, up, base, backup = trees
    script = """
import os, pathlib, sys
import rimeup as r
local, up, base, backup = sys.argv[1:]
writes, expected = r.prepare_update(['a.dict.yaml', 'b.dict.yaml'], local, up, base, 'incremental')
real_write = r.atomic_write
def crash(path, text):
    if pathlib.Path(path) == pathlib.Path(local) / 'b.dict.yaml':
        os._exit(71)
    real_write(path, text)
r.atomic_write = crash
r.apply_transaction(writes, expected, local, base, backup)
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, *map(str, trees)],
                            cwd=str(Path(r.__file__).parent), capture_output=True, text=True)
    assert result.returncode == 71, result.stderr
    assert r.parse_dict((local / "a.dict.yaml").read_text()).entries[0].weight == "200"
    assert (local / r.JOURNAL / "manifest.json").exists()
    assert run_cli(trees, "--incremental") != 0
    with patch.object(r, "fetch_upstream", side_effect=AssertionError("must not fetch")):
        assert r.main(cli_args(trees, "--recover")) == 0
    for root in (local, base):
        for name in ("a.dict.yaml", "b.dict.yaml"):
            assert (root / name).read_text() == dictionary()
    assert not (local / r.JOURNAL).exists()


def test_stale_cache_cannot_update(trees):
    cache = trees[0].parent / "cache"
    (cache / ".git").mkdir(parents=True)
    put(cache, "a.dict.yaml", dictionary("a\ta\t50\n", version="old"))
    with patch.object(r, "fetch_upstream", side_effect=subprocess.CalledProcessError(1, ["git", "fetch"])):
        assert r.main(cli_args(trees, "--incremental")) != 0
        assert r.main(cli_args(trees, "--check")) != 0
    assert (trees[0] / "a.dict.yaml").read_text() == dictionary()
    assert not trees[3].exists()


def test_check_never_writes_dictionary_baseline_backup_or_lock(trees):
    local, _, base, backup = trees
    before = {p: p.read_bytes() for root in (local, base) for p in root.rglob("*") if p.is_file()}
    assert run_cli(trees, "--incremental", "--check") == 0
    after = {p: p.read_bytes() for root in (local, base) for p in root.rglob("*") if p.is_file()}
    assert after == before
    assert not backup.exists()


def test_preparation_detects_concurrent_edit(trees):
    local, _, base, backup = trees
    writes, expected = plan(trees)
    put(local, "a.dict.yaml", dictionary("mine\tmine\t999\n"))
    with pytest.raises(r.UpdateError, match="发生变化"):
        r.apply_transaction(writes, expected, str(local), str(base), str(backup))
    assert "mine" in (local / "a.dict.yaml").read_text()
    assert not backup.exists()


def test_nonempty_backup_is_not_overwritten(trees):
    put(trees[3], "a.dict.yaml", "valuable old backup")
    assert run_cli(trees, "--incremental") != 0
    assert (trees[3] / "a.dict.yaml").read_text() == "valuable old backup"
    assert (trees[0] / "a.dict.yaml").read_text() == dictionary()


def test_different_targets_have_different_default_baselines(tmp_path):
    assert r._baseline_default(str(tmp_path / "a"), "repo") != r._baseline_default(str(tmp_path / "b"), "repo")
    assert r._baseline_default(str(tmp_path), "repo-a") != r._baseline_default(str(tmp_path), "repo-b")


def test_baseline_from_other_target_is_rejected(trees):
    put(trees[2], ".rimeup-source.json", json.dumps({"rime_dir": "other", "repo": "other"}))
    assert run_cli(trees, "--incremental") != 0
    assert not trees[3].exists()


def test_overlapping_directories_rejected(trees):
    args = cli_args(trees, "--overwrite")
    args[args.index("--cache-dir") + 1] = str(trees[0] / "cache")
    with patch.object(r, "fetch_upstream", side_effect=AssertionError("must not fetch")):
        assert r.main(args) != 0


def test_symlink_target_rejected(trees, tmp_path):
    outside = put(tmp_path, "outside.dict.yaml", dictionary())
    (trees[0] / "a.dict.yaml").unlink()
    (trees[0] / "a.dict.yaml").symlink_to(outside)
    assert run_cli(trees, "--incremental") != 0
    assert outside.read_text() == dictionary()


def test_lock_rejects_second_update(tmp_path):
    with r._lock(str(tmp_path / "lock")):
        with pytest.raises(r.UpdateError, match="正在运行"):
            with r._lock(str(tmp_path / "lock")):
                pytest.fail("acquired a held lock")


def test_script_propagates_failure_exit_code(tmp_path):
    result = subprocess.run([sys.executable, "-B", r.__file__, "--rime-dir", str(tmp_path / "missing"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "目录不存在" in result.stderr


def test_duplicate_english_rows_keep_first_weight_and_original_order():
    body = "iPhone\tiPhone\t999\nMac\tMac\niPhone\tiPhone\n"
    base = r.parse_dict(dictionary(body))
    up = r.parse_dict(dictionary(body.replace("999", "1000"), version="2"))
    merged = r.parse_dict(r.merge_incremental(up.header, up.entries, base.entries, base.entries))
    assert merged.entries == up.entries
    assert merged.entries[0].weight == "1000"
    assert merged.entries[2].weight is None


def test_local_duplicate_group_change_is_preserved():
    base = r.parse_dict(dictionary("iPhone\tiPhone\t999\niPhone\tiPhone\n"))
    local = r.parse_dict(dictionary("iPhone\tiPhone\t500\niPhone\tiPhone\n"))
    up = r.parse_dict(dictionary("iPhone\tiPhone\t1000\niPhone\tiPhone\n", version="2"))
    merged = r.parse_dict(r.merge_incremental(up.header, up.entries, local.entries, base.entries))
    assert merged.entries == local.entries


def test_comment_directive_is_not_silently_discarded():
    with pytest.raises(r.ParseError, match="no comment"):
        r.parse_dict(dictionary("# no comment\n#hashtag\thashtag\t1\n"))


def test_repository_mismatch_cannot_reset_cache(tmp_path):
    cache = tmp_path / "cache"
    (cache / ".git").mkdir(parents=True)
    result = subprocess.CompletedProcess([], 0, stdout="https://github.com/example/other.git\n")
    with patch.object(r, "_run", return_value=result) as run:
        with pytest.raises(r.UpdateError, match="来源"):
            r.fetch_upstream(r.UPSTREAM_REPO, str(cache))
    assert run.call_count == 1


def test_dirty_cache_is_not_reset(tmp_path):
    cache = tmp_path / "cache"
    (cache / ".git").mkdir(parents=True)
    results = [subprocess.CompletedProcess([], 0, stdout=r.UPSTREAM_REPO + "\n"),
               subprocess.CompletedProcess([], 0, stdout=" M a.dict.yaml\n")]
    with patch.object(r, "_run", side_effect=results) as run:
        with pytest.raises(r.UpdateError, match="本地改动"):
            r.fetch_upstream(r.UPSTREAM_REPO, str(cache))
    assert run.call_count == 2


def test_recovery_failure_keeps_journal_for_retry(trees):
    local, _, base, _ = trees
    original_write = r.atomic_write

    def fail_write(path, text):
        if Path(path) == local / "b.dict.yaml":
            raise OSError("injected failure")
        original_write(path, text)

    original_restore = r._restore_file

    def fail_restore(source, target, mode):
        if target == local / "a.dict.yaml":
            raise OSError("injected restore failure")
        original_restore(source, target, mode)

    with patch.object(r, "atomic_write", side_effect=fail_write), patch.object(r, "_restore_file", side_effect=fail_restore):
        assert run_cli(trees, "--incremental") != 0
    assert (local / r.JOURNAL / "manifest.json").exists()
    with patch.object(r.shutil, "which", return_value=None):
        assert r.main(cli_args(trees, "--recover")) == 0
    assert (local / "a.dict.yaml").read_text() == dictionary()
    assert (base / "a.dict.yaml").read_text() == dictionary()


def test_committed_journal_cleanup_does_not_revert_success(trees):
    local, _, base, _ = trees
    real_remove = r.shutil.rmtree

    def interrupted_cleanup(path, *args, **kwargs):
        if Path(path) == local / r.JOURNAL:
            raise OSError("cleanup interrupted")
        real_remove(path, *args, **kwargs)

    with patch.object(r.shutil, "rmtree", side_effect=interrupted_cleanup):
        assert run_cli(trees, "--incremental") != 0
    assert json.loads((local / r.JOURNAL / "manifest.json").read_text())["status"] == "committed"
    assert r.main(cli_args(trees, "--recover")) == 0
    for root in (local, base):
        assert r.parse_dict((root / "a.dict.yaml").read_text()).entries[0].weight == "200"


def test_successful_incremental_is_idempotent(trees):
    local, _, base, backup = trees
    put(local, "a.dict.yaml", dictionary("a\ta\t500\nmine\tmine\t20\n"))
    assert run_cli(trees, "--incremental") == 0
    before = {p: p.read_bytes() for root in (local, base) for p in root.rglob("*") if p.is_file()}
    second_backup = backup.parent / "backup2"
    assert run_cli((*trees[:3], second_backup), "--incremental") == 0
    assert {p: p.read_bytes() for p in before} == before
    assert not second_backup.exists()


def test_check_works_without_git_for_help():
    with patch.object(r.shutil, "which", return_value=None):
        with pytest.raises(SystemExit) as result:
            r.main(["--help"])
    assert result.value.code == 0


def test_overwrite_preserves_upstream_crlf_bytes(trees):
    local, up, base, _ = trees
    raw = dictionary("a\ta\t200\n", version="2").replace("\n", "\r\n").encode()
    (up / "a.dict.yaml").write_bytes(raw)
    assert run_cli(trees, "--overwrite") == 0
    assert (local / "a.dict.yaml").read_bytes() == raw
    assert (base / "a.dict.yaml").read_bytes() == raw


def test_explicit_overwrite_can_preflight_a_broken_local_file(trees):
    put(trees[0], "a.dict.yaml", "---\nname: broken\n")
    assert run_cli(trees, "--overwrite", "--check") == 0
    assert (trees[0] / "a.dict.yaml").read_text() == "---\nname: broken\n"
    assert not trees[3].exists()


def test_update_does_not_touch_custom_config_or_userdb(trees):
    local = trees[0]
    paths = [put(local, "rime_ice.custom.yaml", "patch: {mine: true}\n"),
             put(local, "lua/custom.lua", "return 'mine'\n"),
             put(local, "custom_phrase.txt", "my phrase\n"),
             put(local, "rime_ice.userdb/data", "my learned words\n")]
    before = {p: p.read_bytes() for p in paths}
    assert run_cli(trees, "--incremental") == 0
    assert {p: p.read_bytes() for p in paths} == before
