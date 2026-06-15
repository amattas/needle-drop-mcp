def test_package_exposes_version():
    import needledrop

    assert needledrop.__version__ == "0.1.0"
