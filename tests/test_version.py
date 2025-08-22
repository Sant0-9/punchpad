def test_version_import():
    from punchpad_app.__version__ import __version__
    assert isinstance(__version__, str)
    assert __version__ == "0.1.0"
