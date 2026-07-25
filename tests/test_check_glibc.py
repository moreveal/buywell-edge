from tools.check_glibc import required_versions


def test_required_versions_extracts_unique_numeric_versions() -> None:
    output = """
      0x0010: Name: GLIBC_2.17  Flags: none  Version: 5
      0x0020: Name: GLIBC_2.28  Flags: none  Version: 4
      0x0030: Name: GLIBC_2.17  Flags: none  Version: 3
    """

    assert required_versions(output) == {(2, 17), (2, 28)}
