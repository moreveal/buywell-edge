import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools" / "check_glibc.py"
SPEC = importlib.util.spec_from_file_location("check_glibc", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
required_versions = MODULE.required_versions


def test_required_versions_extracts_unique_numeric_versions() -> None:
    output = """
      0x0010: Name: GLIBC_2.17  Flags: none  Version: 5
      0x0020: Name: GLIBC_2.28  Flags: none  Version: 4
      0x0030: Name: GLIBC_2.17  Flags: none  Version: 3
    """

    assert required_versions(output) == {(2, 17), (2, 28)}
