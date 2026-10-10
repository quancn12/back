"""Check that every pinned requirement is installed at the pinned version.

`pip check` only compares packages that are already installed against one
another, so it reports success while a requirement is missing entirely. That
lets a setup script declare victory and leaves the application to fail later on
an import. This closes that gap.

Exits 0 when everything matches, 1 otherwise, listing what is wrong.
Standard library only, so it runs before anything else is guaranteed present.
"""
from __future__ import annotations

import importlib.metadata as metadata
import pathlib
import re
import sys

PINNED = re.compile(r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?==(.+)$")


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    path = root / "requirements.txt"
    if not path.exists():
        print(f"Not found: {path}")
        return 1

    problems: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = PINNED.match(line)
        if not match:
            # Unpinned or exotic specifiers are left to pip.
            continue
        name, _extras, wanted = match.groups()
        try:
            found = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f"  {name}: not installed")
            continue
        if found != wanted:
            problems.append(f"  {name}: {found} installed, {wanted} required")

    if problems:
        print("requirements.txt is not satisfied:")
        print("\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
