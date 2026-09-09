"""``python -m e2e_harness …`` → the e2e CLI (see ``e2e_harness.cli``)."""

from __future__ import annotations

import sys

from e2e_harness.cli import main

if __name__ == "__main__":
    sys.exit(main())
