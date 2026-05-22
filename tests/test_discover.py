import os
import rimeup

HDR = '---\nname: d\nversion: "{v}"\nsort: by_weight\n...\n'


def make(root, rel, version, body):
    from tests.conftest import write
    return write(os.path.join(root, rel), HDR.format(v=version) + body)


def test_find_dict_files(tmp_path, writer):
    writer(str(tmp_path / "rime_ice.dict.yaml"), "x")
    writer(str(tmp_path / "cn_dicts" / "base.dict.yaml"), "x")
    writer(str(tmp_path / "en_dicts" / "en.dict.yaml"), "x")
    writer(str(tmp_path / "notes.txt"), "x")
    found = rimeup.find_dict_files(str(tmp_path))
    assert found == ["cn_dicts/base.dict.yaml", "en_dicts/en.dict.yaml", "rime_ice.dict.yaml"]


def test_discover_statuses(tmp_path, writer):
    local = tmp_path / "local"
    up = tmp_path / "up"
    # same
    writer(str(local / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\n")
    writer(str(up / "cn_dicts/a.dict.yaml"), HDR.format(v="1") + "a\ta\t1\n")
    # updated
    writer(str(local / "cn_dicts/b.dict.yaml"), HDR.format(v="1") + "b\tb\t1\n")
    writer(str(up / "cn_dicts/b.dict.yaml"), HDR.format(v="2") + "b\tb\t1\nx\tx\t1\n")
    # upstream_new
    writer(str(up / "cn_dicts/c.dict.yaml"), HDR.format(v="1") + "c\tc\t1\n")
    # local_only
    writer(str(local / "liangfen.dict.yaml"), HDR.format(v="1") + "l\tl\t1\n")

    got = {d["name"]: d["status"] for d in rimeup.discover_dicts(str(local), str(up))}
    assert got["cn_dicts/a.dict.yaml"] == "same"
    assert got["cn_dicts/b.dict.yaml"] == "updated"
    assert got["cn_dicts/c.dict.yaml"] == "upstream_new"
    assert got["liangfen.dict.yaml"] == "local_only"


def test_dict_stats(tmp_path, writer):
    lp = writer(str(tmp_path / "l.dict.yaml"), HDR.format(v="2026-01") + "a\ta\t1\nb\tb\t1\n")
    upp = writer(str(tmp_path / "u.dict.yaml"), HDR.format(v="2026-05") + "a\ta\t1\nc\tc\t1\n")
    local_ver, up_ver, n_new, n_local = rimeup.dict_stats(lp, upp)
    assert (local_ver, up_ver) == ("2026-01", "2026-05")
    assert n_new == 1 and n_local == 1


# C1: discover_dicts treats CRLF-vs-LF identical content as "same"
def test_discover_crlf_same_as_lf(tmp_path):
    """A local file with CRLF line endings but identical content to LF upstream is 'same'."""
    content_lf = HDR.format(v="1") + "a\ta\t1\n"
    content_crlf = content_lf.replace("\n", "\r\n")
    local = tmp_path / "local"
    up = tmp_path / "up"
    os.makedirs(str(local), exist_ok=True)
    os.makedirs(str(up), exist_ok=True)
    # write local with CRLF (binary mode to preserve CRLF)
    local_file = str(local / "a.dict.yaml")
    with open(local_file, "wb") as f:
        f.write(content_crlf.encode("utf-8"))
    # write upstream with LF
    up_file = str(up / "a.dict.yaml")
    with open(up_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(content_lf)
    got = {d["name"]: d["status"] for d in rimeup.discover_dicts(str(local), str(up))}
    assert got["a.dict.yaml"] == "same"
