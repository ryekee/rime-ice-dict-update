import os
import subprocess
import rimeup
from unittest.mock import patch

HDR = '---\nname: d\nversion: "{v}"\nsort: by_weight\n...\n'


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _fake_repo(tmp_path, writer):
    repo = tmp_path / "upstream"; repo.mkdir()
    _git(["init", "-q"], str(repo)); _git(["config", "user.email", "t@t"], str(repo))
    _git(["config", "user.name", "t"], str(repo))
    _git(["config", "commit.gpgsign", "false"], str(repo))
    writer(str(repo / "cn_dicts/a.dict.yaml"), HDR.format(v="2") + "a\ta\t1\nnew\tnew\t2\n")
    _git(["add", "-A"], str(repo)); _git(["commit", "-qm", "up"], str(repo))
    return str(repo)


def _common(tmp_path):
    return [
        "--cache-dir", str(tmp_path / "cache" / "rime-ice"),
        "--baseline-dir", str(tmp_path / "baseline"),
        "--backup-dir", str(tmp_path / "backup"),
    ]


def test_cli_check_changes_nothing(tmp_path, writer, capsys):
    repo = _fake_repo(tmp_path, writer)
    rime = tmp_path / "rime"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    rc = rimeup.main(["--rime-dir", str(rime), "--repo", repo, "--check"] + _common(tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "有更新" in out or "updated" in out
    assert "mine\tmine\t5" in rimeup._read(str(rime / "cn_dicts/a.dict.yaml"))  # untouched


def test_cli_incremental_merges_and_writes_baseline(tmp_path, writer):
    repo = _fake_repo(tmp_path, writer)
    rime = tmp_path / "rime"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    rc = rimeup.main(["--rime-dir", str(rime), "--repo", repo, "--incremental"] + _common(tmp_path))
    assert rc == 0
    out = rimeup._read(str(rime / "cn_dicts/a.dict.yaml"))
    assert "new\tnew\t2" in out and "mine\tmine\t5" in out
    # baseline now seeded for next run
    assert os.path.exists(str(tmp_path / "baseline" / "cn_dicts/a.dict.yaml"))
    # backup captured the pre-change file
    assert "mine\tmine\t5" in rimeup._read(str(tmp_path / "backup" / "cn_dicts/a.dict.yaml"))


def test_cli_overwrite(tmp_path, writer):
    repo = _fake_repo(tmp_path, writer)
    rime = tmp_path / "rime"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    rc = rimeup.main(["--rime-dir", str(rime), "--repo", repo, "--overwrite"] + _common(tmp_path))
    assert rc == 0
    out = rimeup._read(str(rime / "cn_dicts/a.dict.yaml"))
    assert "mine\tmine\t5" not in out      # overwritten
    assert "new\tnew\t2" in out


def test_cli_incremental_first_run_prints_reminder(tmp_path, writer, capsys):
    """Incremental with no baseline should print the first-run reminder."""
    repo = _fake_repo(tmp_path, writer)
    rime = tmp_path / "rime"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    # baseline dir exists but has no file for this rel -> first run
    (tmp_path / "baseline").mkdir(parents=True, exist_ok=True)
    rc = rimeup.main(["--rime-dir", str(rime), "--repo", repo, "--incremental"] + _common(tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "首次增量无基线" in out


def test_cli_incremental_second_run_no_reminder(tmp_path, writer, capsys):
    """After baseline is seeded, a second incremental run should NOT print the reminder."""
    repo = _fake_repo(tmp_path, writer)
    rime = tmp_path / "rime"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    # First run seeds the baseline
    rimeup.main(["--rime-dir", str(rime), "--repo", repo, "--incremental"] + _common(tmp_path))
    capsys.readouterr()  # discard first-run output
    # Simulate a local change to trigger updated status on second run
    # We need to make local differ from upstream again; just re-write local with different content
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t99\n")
    rc = rimeup.main(["--rime-dir", str(rime), "--repo", repo, "--incremental"] + _common(tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "首次增量无基线" not in out


def test_missing_git_returns_nonzero_with_hint(tmp_path, capsys):
    """When git is not found, main() should return non-zero and print an install hint."""
    with patch("rimeup.shutil.which", return_value=None):
        rc = rimeup.main(["--rime-dir", str(tmp_path)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "git" in err
    assert "brew install git" in err


def test_cli_short_n_is_dry_run(tmp_path, writer, capsys):
    """-n should be equivalent to --check: show table but not modify files."""
    repo = _fake_repo(tmp_path, writer)
    rime = tmp_path / "rime"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    rc = rimeup.main(["--rime-dir", str(rime), "--repo", repo, "-n"] + _common(tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "有更新" in out or "updated" in out
    # file must be untouched
    assert "mine\tmine\t5" in rimeup._read(str(rime / "cn_dicts/a.dict.yaml"))


# C2: malformed upstream_new / same file must not crash the whole table
MALFORMED_HDR = '---\nname: d\nversion: "1"\nsort: by_weight\n'  # no '...' terminator


def test_main_malformed_upstream_new_does_not_raise(tmp_path, writer, capsys):
    """A malformed upstream_new file alongside valid updated files must not abort main()."""
    repo = _fake_repo(tmp_path, writer)
    rime = tmp_path / "rime"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    # Inject a malformed upstream_new file directly into the cloned cache directory
    # We need to place it in the cache dir that _fake_repo's fetch_upstream will clone to
    # Instead, write it to the repo before cloning by monkeypatching discover_dicts
    # Simpler: write it to local rime dir AND upstream cache after main clones
    # Easiest: use discover_dicts directly by writing the file into the upstream repo dir
    # We can't easily do this with _fake_repo because it's already committed.
    # Use patch to inject a malformed entry into discover_dicts output.
    original_discover = rimeup.discover_dicts

    def patched_discover(local_dir, upstream_dir):
        results = original_discover(local_dir, upstream_dir)
        # inject a fake upstream_new entry pointing to a malformed file
        malformed_path = str(tmp_path / "malformed.dict.yaml")
        writer(malformed_path, MALFORMED_HDR + "x\tx\t1\n")
        results.append({
            "name": "cn_dicts/malformed.dict.yaml",
            "status": "upstream_new",
            "local_path": malformed_path,
            "up_path": malformed_path,
        })
        return results

    with patch("rimeup.discover_dicts", patched_discover):
        rc = rimeup.main(["--rime-dir", str(rime), "--repo", repo, "--check"] + _common(tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    # The table must still contain the valid entry
    assert "有更新" in out


# M7: --overwrite and --incremental together must be mutually exclusive
def test_overwrite_and_incremental_together_exit_nonzero(tmp_path):
    """Passing both --overwrite and --incremental must cause argparse to exit non-zero."""
    import pytest
    with pytest.raises(SystemExit) as exc_info:
        rimeup.main(["--rime-dir", str(tmp_path), "--overwrite", "--incremental"])
    assert exc_info.value.code != 0
