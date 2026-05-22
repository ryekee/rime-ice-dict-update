import rimeup
from rimeup import Entry


def test_compare_counts():
    local = [Entry("a", "a", "1"), Entry("b", "b", "1"), Entry("c", "c", "1")]
    upstream = [Entry("a", "a", "1"), Entry("b", "b", "9"), Entry("d", "d", "1")]
    n_new, n_local_only = rimeup.compare(local, upstream)
    assert n_new == 1          # d
    assert n_local_only == 1   # c
    # b is shared (same key) -> counts in neither
