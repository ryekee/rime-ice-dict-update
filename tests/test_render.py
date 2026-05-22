import rimeup


def test_display_width_counts_cjk_as_two():
    assert rimeup.display_width("ab") == 2
    assert rimeup.display_width("中文") == 4
    assert rimeup.display_width("a中") == 3


def test_render_table_contains_rows_and_head():
    rows = [
        {"name": "cn_dicts/base.dict.yaml", "local_ver": "2026-05-16",
         "up_ver": "2026-05-16", "status": "相同", "n_new": None, "n_local": None},
        {"name": "en_dicts/en_ext.dict.yaml", "local_ver": "2026-03-08",
         "up_ver": "2026-03-15", "status": "有更新", "n_new": 42, "n_local": 3},
    ]
    out = rimeup.render_table(rows, {"sha": "abc123", "date": "2026-05-18", "subject": "x"})
    assert "abc123" in out
    assert "cn_dicts/base.dict.yaml" in out
    assert "+42" in out
    assert "有更新" in out
    # the '相同' row shows '-' for counts
    assert "-" in out


def test_render_table_summary_line():
    """The last line of the table should be a summary with correct counts."""
    rows = [
        {"name": "cn_dicts/base.dict.yaml", "local_ver": "2026-05-16",
         "up_ver": "2026-05-16", "status": "相同", "n_new": None, "n_local": None},
        {"name": "en_dicts/en_ext.dict.yaml", "local_ver": "2026-03-08",
         "up_ver": "2026-03-15", "status": "有更新", "n_new": 42, "n_local": 3},
        {"name": "radical.dict.yaml", "local_ver": "2026-03-11",
         "up_ver": "2026-04-01", "status": "有更新(解析失败)", "n_new": None, "n_local": None},
        {"name": "liangfen.dict.yaml", "local_ver": "5.0",
         "up_ver": "—", "status": "本地独有", "n_new": None, "n_local": None},
        {"name": "new_up.dict.yaml", "local_ver": "—",
         "up_ver": "2026-05-01", "status": "上游新文件", "n_new": None, "n_local": None},
    ]
    out = rimeup.render_table(rows, None)
    # Summary counts: 5 total, 2 updated (有更新 + 有更新(解析失败)), 1 same, 1 local_only, 1 upstream_new
    assert "共 5 个词库" in out
    assert "2 个有更新" in out
    assert "1 个相同" in out
    assert "1 个本地独有" in out
    assert "1 个上游新文件" in out
