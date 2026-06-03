#!/usr/bin/env python3
"""安定化チェック — 5項目を N 回連続実行

使い方:
  python stability_check.py
  python stability_check.py --cycles 3
"""

from __future__ import annotations

import argparse
import sys

from stability import format_report, run_stability_suite


def main() -> int:
    parser = argparse.ArgumentParser(description="競輪AI 安定化チェック")
    parser.add_argument("--cycles", type=int, default=3, help="連続成功の試行回数")
    args = parser.parse_args()

    ok, results = run_stability_suite(cycles=max(1, args.cycles))
    print(format_report(results))
    if ok:
        print(f"RESULT: PASS ({args.cycles} cycles)")
        return 0
    print(f"RESULT: FAIL (see above)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
