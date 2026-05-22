import os
import subprocess
import rimeup

HDR = '---\nname: d\nversion: "{v}"\nsort: by_weight\n...\n'


def test_atomic_write_creates_and_replaces(tmp_path):
    p = str(tmp_path / "f.txt")
    rimeup.atomic_write(p, "hello")
    assert rimeup._read(p) == "hello"
    rimeup.atomic_write(p, "world")
    assert rimeup._read(p) == "world"


def test_backup_copies_relative_paths(tmp_path, writer):
    rime = tmp_path / "rime"
    bk = tmp_path / "bk"
    writer(str(rime / "cn_dicts/a.dict.yaml"), "A")
    rimeup.backup(["cn_dicts/a.dict.yaml"], str(rime), str(bk))
    assert rimeup._read(str(bk / "cn_dicts/a.dict.yaml")) == "A"


def test_update_baseline_copies_from_upstream(tmp_path, writer):
    up = tmp_path / "up"
    base = tmp_path / "base"
    writer(str(up / "cn_dicts/a.dict.yaml"), "U")
    rimeup.update_baseline(["cn_dicts/a.dict.yaml"], str(up), str(base))
    assert rimeup._read(str(base / "cn_dicts/a.dict.yaml")) == "U"


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True,
                   capture_output=True, text=True)


def test_fetch_upstream_clones_then_updates(tmp_path, writer):
    repo = tmp_path / "fake_upstream"
    repo.mkdir()
    _git(["init", "-q"], str(repo))
    _git(["config", "user.email", "t@t"], str(repo))
    _git(["config", "user.name", "t"], str(repo))
    _git(["config", "commit.gpgsign", "false"], str(repo))
    writer(str(repo / "cn_dicts/a.dict.yaml"), "v1")
    _git(["add", "-A"], str(repo))
    _git(["commit", "-q", "-m", "one"], str(repo))

    cache = tmp_path / "cache" / "rime-ice"
    path, head = rimeup.fetch_upstream(str(repo), str(cache))
    assert rimeup._read(os.path.join(path, "cn_dicts/a.dict.yaml")) == "v1"
    assert head["subject"] == "one"

    # second run updates existing cache
    writer(str(repo / "cn_dicts/a.dict.yaml"), "v2")
    _git(["commit", "-aqm", "two"], str(repo))
    path2, head2 = rimeup.fetch_upstream(str(repo), str(cache))
    assert rimeup._read(os.path.join(path2, "cn_dicts/a.dict.yaml")) == "v2"
    assert head2["subject"] == "two"


def test_apply_overwrite(tmp_path, writer):
    rime = tmp_path / "rime"; up = tmp_path / "up"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\n")
    writer(str(up / "cn_dicts/a.dict.yaml"), HDR.format(v="2") + "a\ta\t1\nx\tx\t1\n")
    rimeup.apply_overwrite(["cn_dicts/a.dict.yaml"], str(rime), str(up))
    assert rimeup._read(str(rime / "cn_dicts/a.dict.yaml")) == rimeup._read(str(up / "cn_dicts/a.dict.yaml"))


def test_apply_incremental_preserves_local_only(tmp_path, writer):
    rime = tmp_path / "rime"; up = tmp_path / "up"; base = tmp_path / "base"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    writer(str(up / "cn_dicts/a.dict.yaml"), HDR.format(v="2") + "a\ta\t1\nnew\tnew\t2\n")
    writer(str(base / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\n")
    rimeup.apply_incremental(["cn_dicts/a.dict.yaml"], str(rime), str(up), str(base))
    out = rimeup._read(str(rime / "cn_dicts/a.dict.yaml"))
    assert "new\tnew\t2" in out      # upstream addition pulled in
    assert "mine\tmine\t5" in out    # local-only preserved
    assert 'version: "2"' in out     # upstream header/version


def test_apply_incremental_first_run_without_baseline(tmp_path, writer):
    rime = tmp_path / "rime"; up = tmp_path / "up"; base = tmp_path / "base"
    writer(str(rime / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    writer(str(up / "cn_dicts/a.dict.yaml"), HDR.format(v="2") + "a\ta\t1\n")
    # base dir has no file for this rel -> first run
    rimeup.apply_incremental(["cn_dicts/a.dict.yaml"], str(rime), str(up), str(base))
    assert "mine\tmine\t5" in rimeup._read(str(rime / "cn_dicts/a.dict.yaml"))


MALFORMED = '---\nname: d\nversion: "1"\nsort: by_weight\n'  # missing '...' terminator


def test_apply_incremental_parseerror_falls_back_to_overwrite(tmp_path, writer):
    """A malformed local file should be overwritten with upstream (no raise)."""
    rime = tmp_path / "rime"; up = tmp_path / "up"; base = tmp_path / "base"
    writer(str(rime / "cn_dicts/a.dict.yaml"), MALFORMED + "a\ta\t1\n")
    writer(str(up / "cn_dicts/a.dict.yaml"), HDR.format(v="2") + "a\ta\t1\nx\tx\t2\n")
    writer(str(base / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\n")
    _first_run, parse_warnings = rimeup.apply_incremental(
        ["cn_dicts/a.dict.yaml"], str(rime), str(up), str(base)
    )
    # file is now the upstream version
    result = rimeup._read(str(rime / "cn_dicts/a.dict.yaml"))
    assert "x\tx\t2" in result
    # a warning was returned
    assert any("cn_dicts/a.dict.yaml" in w for w in parse_warnings)


def test_apply_incremental_parseerror_does_not_affect_other_files(tmp_path, writer):
    """Other valid files still merge correctly when one file has a ParseError."""
    rime = tmp_path / "rime"; up = tmp_path / "up"; base = tmp_path / "base"
    # malformed local for a.dict.yaml
    writer(str(rime / "cn_dicts/a.dict.yaml"), MALFORMED + "a\ta\t1\n")
    writer(str(up / "cn_dicts/a.dict.yaml"), HDR.format(v="2") + "a\ta\t1\n")
    writer(str(base / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\n")
    # valid local for b.dict.yaml
    writer(str(rime / "cn_dicts/b.dict.yaml"), HDR.format(v="1") + "b\tb\t1\nmine\tmine\t5\n")
    writer(str(up / "cn_dicts/b.dict.yaml"), HDR.format(v="2") + "b\tb\t1\nnew\tnew\t3\n")
    writer(str(base / "cn_dicts/b.dict.yaml"), HDR.format(v="1") + "b\tb\t1\n")
    _first_run, _warnings = rimeup.apply_incremental(
        ["cn_dicts/a.dict.yaml", "cn_dicts/b.dict.yaml"], str(rime), str(up), str(base)
    )
    b_out = rimeup._read(str(rime / "cn_dicts/b.dict.yaml"))
    assert "new\tnew\t3" in b_out    # upstream addition pulled in
    assert "mine\tmine\t5" in b_out  # local-only preserved
