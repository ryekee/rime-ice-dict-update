import rimeup
from rimeup import Entry

HEADER = '# h\n---\nname: d\nversion: "U"\nsort: by_weight\n...\n'


def parse_entries(text):
    return rimeup.parse_dict(HEADER + text).entries


def test_3way_local_addition_kept():
    up = [Entry("a", "a", "1")]
    local = [Entry("a", "a", "1"), Entry("mine", "mine", "5")]
    base = [Entry("a", "a", "1")]
    out = rimeup.merge_incremental(HEADER, up, local, base)
    assert "# === rimeup 保留的本地词条 ===" in out
    assert out.strip().endswith("mine\tmine\t5")
    assert out.startswith(HEADER)


def test_3way_untouched_follows_upstream_weight():
    up = [Entry("a", "a", "9")]      # upstream re-weighted
    local = [Entry("a", "a", "1")]   # equals baseline -> user untouched
    base = [Entry("a", "a", "1")]
    out = rimeup.merge_incremental(HEADER, up, local, base)
    assert "a\ta\t9" in out          # upstream weight wins
    assert "a\ta\t1" not in out


def test_3way_user_reweight_preserved():
    up = [Entry("a", "a", "9")]      # upstream weight 9
    local = [Entry("a", "a", "5")]   # user set 5
    base = [Entry("a", "a", "1")]    # baseline 1 -> user changed it
    out = rimeup.merge_incremental(HEADER, up, local, base)
    assert "a\ta\t5" in out          # user's weight preserved
    assert "a\ta\t9" not in out


def test_3way_upstream_removed_untouched_word_is_dropped():
    up = []                          # upstream removed it
    local = [Entry("a", "a", "1")]
    base = [Entry("a", "a", "1")]    # user never touched -> respect removal
    out = rimeup.merge_incremental(HEADER, up, local, base)
    assert "a\ta" not in out


def test_3way_upstream_removed_modified_word_is_kept():
    up = []
    local = [Entry("a", "a", "5")]   # user changed it
    base = [Entry("a", "a", "1")]
    out = rimeup.merge_incremental(HEADER, up, local, base)
    assert "a\ta\t5" in out          # kept (appended)


def test_3way_upstream_addition_included():
    up = [Entry("a", "a", "1"), Entry("new", "new", "2")]
    local = [Entry("a", "a", "1")]
    base = [Entry("a", "a", "1")]
    out = rimeup.merge_incremental(HEADER, up, local, base)
    assert "new\tnew\t2" in out


def test_first_run_no_baseline_preserves_all_local_diffs():
    up = [Entry("a", "a", "9")]
    local = [Entry("a", "a", "5"), Entry("mine", "mine", "1")]
    out = rimeup.merge_incremental(HEADER, up, local, None)
    assert "a\ta\t5" in out          # local weight preserved (conservative)
    assert "mine\tmine\t1" in out    # local-only kept


# C1: incremental merge with CRLF local content must not spuriously duplicate entries
def test_incremental_crlf_local_no_spurious_duplicate():
    """CRLF input parsed into entries must yield clean keys; no duplicate appends."""
    crlf_header = HEADER.replace("\n", "\r\n")
    crlf_body = "a\ta\t1\nmine\tmine\t5\n"
    local_text = crlf_header + crlf_body.replace("\n", "\r\n")
    local = rimeup.parse_dict(local_text).entries
    up = [Entry("a", "a", "1"), Entry("new", "new", "2")]
    base = [Entry("a", "a", "1")]
    out = rimeup.merge_incremental(HEADER, up, local, base)
    # mine should appear exactly once (not duplicated as a fake "modified" entry)
    assert out.count("mine\tmine\t5") == 1
    # weights must not have \r
    assert "\r" not in out
