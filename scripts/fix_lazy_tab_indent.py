"""Fix app.py tabs where body renders outside should_render_tab()."""
from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"
WITH_INDENT = 4
IF_INDENT = 8
BODY_INDENT = 12


def main() -> None:
    lines = APP.read_text(encoding="utf-8").splitlines(keepends=True)
    i = 0
    fixes = 0
    while i < len(lines):
        line = lines[i]
        if "if should_render_tab(" not in line or not line.startswith(" " * IF_INDENT):
            i += 1
            continue
        start = i
        j = i + 1
        while j < len(lines) and not lines[j].startswith("elif active_main"):
            j += 1
        block = lines[start:j]
        orphan_start = None
        for k, bl in enumerate(block[1:], start=1):
            if not bl.strip():
                continue
            indent = len(bl) - len(bl.lstrip(" "))
            if indent == IF_INDENT and "if should_render_tab(" not in bl:
                orphan_start = k
                break
        if orphan_start is None:
            i = j
            continue
        for k in range(orphan_start, len(block)):
            block[k] = "    " + block[k]
        lines[start:j] = block
        fixes += 1
        i = j
    APP.write_text("".join(lines), encoding="utf-8")
    print(f"fixed {fixes} tab block(s)")


if __name__ == "__main__":
    main()
