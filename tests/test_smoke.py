import rimeup


def test_module_imports():
    assert rimeup.Entry("a", "a", None).text == "a"
