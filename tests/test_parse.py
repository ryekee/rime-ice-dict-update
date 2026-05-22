import rimeup
from rimeup import Entry


def test_render_entry_with_weight():
    assert rimeup.render_entry(Entry("苹果", "ping guo", "100")) == "苹果\tping guo\t100"


def test_render_entry_no_weight_code_differs():
    assert rimeup.render_entry(Entry("苹果", "ping guo", None)) == "苹果\tping guo"


def test_render_entry_single_column_when_code_equals_text():
    assert rimeup.render_entry(Entry("README.md", "README.md", None)) == "README.md"


SAMPLE = """# comment header
# more comment
---
name: demo
version: "2026-01-01"
sort: by_weight
...
# section comment
苹果\tping guo\t100
香蕉\txiang jiao
README.md

iPhone\tiPhone\t999
"""


def test_parse_meta():
    pd = rimeup.parse_dict(SAMPLE)
    assert pd.meta["version"] == "2026-01-01"
    assert pd.meta["sort"] == "by_weight"


def test_parse_skips_comments_and_blanks():
    pd = rimeup.parse_dict(SAMPLE)
    assert [e.text for e in pd.entries] == ["苹果", "香蕉", "README.md", "iPhone"]


def test_parse_fields():
    pd = rimeup.parse_dict(SAMPLE)
    assert pd.entries[0] == rimeup.Entry("苹果", "ping guo", "100")
    assert pd.entries[1] == rimeup.Entry("香蕉", "xiang jiao", None)
    assert pd.entries[2] == rimeup.Entry("README.md", "README.md", None)


def test_parse_header_preserved_through_terminator():
    pd = rimeup.parse_dict(SAMPLE)
    assert pd.header.endswith("...\n")
    assert pd.header.startswith("# comment header")
    assert 'version: "2026-01-01"' in pd.header


def test_parse_raises_without_metadata_terminator():
    import pytest
    with pytest.raises(rimeup.ParseError):
        rimeup.parse_dict("苹果\tping guo\t100\n")


# C1: CRLF handling
SAMPLE_CRLF = SAMPLE.replace("\n", "\r\n")


def test_parse_crlf_yields_clean_weights():
    """CRLF input must not leave \\r on weight or any field."""
    pd = rimeup.parse_dict(SAMPLE_CRLF)
    for e in pd.entries:
        assert "\r" not in (e.text or "")
        assert "\r" not in (e.code or "")
        assert "\r" not in (e.weight or "")
    assert pd.meta["version"] == "2026-01-01"


# I3: '...' appearing again inside entry body becomes junk
SAMPLE_WITH_BODY_ELLIPSIS = """---
name: demo
version: "1"
sort: by_weight
...
苹果\tping guo\t100
...
香蕉\txiang jiao
"""


def test_parse_ellipsis_in_body_skipped():
    """A '...' line inside the entry body must be silently skipped."""
    pd = rimeup.parse_dict(SAMPLE_WITH_BODY_ELLIPSIS)
    texts = [e.text for e in pd.entries]
    assert "..." not in texts
    assert "苹果" in texts
    assert "香蕉" in texts


# M6: inline comment leaks into metadata value
SAMPLE_WITH_COMMENT = """---
name: demo
version: "2026-01-01"  # inline note
sort: by_weight
...
苹果\tping guo\t100
"""


def test_parse_meta_inline_comment_stripped():
    """Inline # comments after a meta value must be stripped."""
    pd = rimeup.parse_dict(SAMPLE_WITH_COMMENT)
    assert pd.meta["version"] == "2026-01-01"
    assert "#" not in pd.meta["version"]
