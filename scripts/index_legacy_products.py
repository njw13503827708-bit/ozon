"""Index explicitly selected legacy product artifacts into an ERP SQLite root."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.legacy_product_index_service import index_legacy_products


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--products-root", type=Path, required=True)
    parser.add_argument("--erp-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(index_legacy_products(args.products_root, args.erp_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
