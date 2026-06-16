"""CLI wrapper for Naver Finance market collection."""

from __future__ import annotations

import argparse
import json
import sys

from naver_market_data import collect_naver_market_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Naver Finance world and domestic leader data")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    result = collect_naver_market_data(args.date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
