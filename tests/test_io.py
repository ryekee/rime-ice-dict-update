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


def _apply(rels, rime, up, base, mode="incremental"):
    writes, expected = rimeup.prepare_update(rels, str(rime), str(up), str(base), mode)
    rimeup.apply_transaction(writes, expected, str(rime), str(base), str(rime.parent / "backup"))


def test_apply_overwrite(tmp_path, writer):
    rime = tmp_path / "rime"
    up = tmp_path / "up"
    writer(str(rime / "a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\n")
    writer(str(up / "a.dict.yaml"), HDR.format(v="2") + "a\ta\t2\n")
    _apply(["a.dict.yaml"], rime, up, tmp_path / "base", "overwrite")
    assert (rime / "a.dict.yaml").read_bytes() == (up / "a.dict.yaml").read_bytes()


def test_apply_incremental_preserves_local_only(tmp_path, writer):
    rime, up, base = (tmp_path / s for s in ("rime", "up", "base"))
    writer(str(rime / "a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\nmine\tmine\t5\n")
    writer(str(up / "a.dict.yaml"), HDR.format(v="2") + "a\ta\t1\nnew\tnew\t2\n")
    writer(str(base / "a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\n")
    _apply(["a.dict.yaml"], rime, up, base)
    out = (rime / "a.dict.yaml").read_text()
    assert "new\tnew\t2" in out and "mine\tmine\t5" in out
    assert 'version: "2"' in out
    assert (base / "a.dict.yaml").read_bytes() == (up / "a.dict.yaml").read_bytes()


def test_missing_baseline_changes_nothing(tmp_path, writer):
    import pytest
    rime, up, base = (tmp_path / s for s in ("rime", "up", "base"))
    old = HDR.format(v="1") + "a\ta\t1\n"
    writer(str(rime / "a.dict.yaml"), old)
    writer(str(up / "a.dict.yaml"), HDR.format(v="2") + "a\ta\t2\n")
    with pytest.raises(rimeup.MissingBaselineError):
        _apply(["a.dict.yaml"], rime, up, base)
    assert (rime / "a.dict.yaml").read_text() == old
    assert not base.exists()
    assert not (tmp_path / "backup").exists()


def test_invalid_file_aborts_whole_batch(tmp_path, writer):
    import pytest
    rime, up, base = (tmp_path / s for s in ("rime", "up", "base"))
    originals = {}
    for name in ("a.dict.yaml", "b.dict.yaml"):
        originals[name] = HDR.format(v="1") + "a\ta\t1\n"
        writer(str(rime / name), originals[name])
        writer(str(base / name), originals[name])
        writer(str(up / name), HDR.format(v="2") + "a\ta\t2\n")
    # The last upstream file is invalid. Earlier files must remain untouched.
    writer(str(up / "b.dict.yaml"), "---\nname: broken\n")
    with pytest.raises(rimeup.ParseError):
        _apply(list(originals), rime, up, base)
    for name, old in originals.items():
        assert (rime / name).read_text() == old
        assert (base / name).read_text() == old
    assert not (tmp_path / "backup").exists()
