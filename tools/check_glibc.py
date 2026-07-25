from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


GLIBC_VERSION = re.compile(r"\bGLIBC_(\d+)\.(\d+)\b")


def required_versions(output: str) -> set[tuple[int, int]]:
    return {(int(major), int(minor)) for major, minor in GLIBC_VERSION.findall(output)}


def elf_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and path.read_bytes()[:4] == b"\x7fELF":
            yield path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reject a Linux bundle that requires a newer glibc than supported."
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--max", required=True, dest="maximum")
    args = parser.parse_args()

    maximum = tuple(int(part) for part in args.maximum.split(".", 1))
    offenders: list[tuple[Path, tuple[int, int]]] = []
    inspected = 0
    for path in elf_files(args.bundle):
        inspected += 1
        result = subprocess.run(
            ["readelf", "--version-info", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        versions = required_versions(result.stdout)
        if versions and max(versions) > maximum:
            offenders.append((path, max(versions)))

    if inspected == 0:
        raise SystemExit(f"No ELF files found under {args.bundle}")
    if offenders:
        details = "\n".join(
            f"- {path}: requires GLIBC_{version[0]}.{version[1]}"
            for path, version in offenders
        )
        raise SystemExit(
            f"Bundle exceeds the GLIBC_{args.maximum} compatibility baseline:\n{details}"
        )
    print(f"Checked {inspected} ELF files; maximum supported baseline is GLIBC_{args.maximum}.")


if __name__ == "__main__":
    main()
