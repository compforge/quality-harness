"""Increment the repository VERSION independently of SDK package versions."""

import argparse
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", choices=("patch", "minor", "major"), default="patch")
    args = parser.parse_args()
    version_file = Path(__file__).resolve().parents[1] / "VERSION"
    try:
        current = version_file.read_text().strip()
    except OSError as error:
        parser.error(f"cannot read {version_file}: {error}")
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", current):
        parser.error(f"{version_file}: expected MAJOR.MINOR.PATCH, got {current!r}")
    parts = [int(part) for part in current.split(".")]
    index = {"major": 0, "minor": 1, "patch": 2}[args.part]
    parts[index] += 1
    parts[index + 1 :] = [0] * (2 - index)
    updated = ".".join(map(str, parts))
    version_file.write_text(updated + "\n")
    print(f"VERSION: {current} → {updated}")


if __name__ == "__main__":
    main()
