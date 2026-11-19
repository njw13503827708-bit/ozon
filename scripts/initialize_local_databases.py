"""Explicit local initialization/migration; never invoked by HTTP requests."""
import argparse
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SOURCE_ROOT), str(SOURCE_ROOT / "scripts"),
                str(SOURCE_ROOT / "market-intelligence"), str(SOURCE_ROOT / "finance-center")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--database", choices=("erp", "task", "market", "finance"), required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.database == "erp":
        from backend.db.erp import initialize
        print(initialize(root))
    elif args.database == "task":
        from task_database import initialize
        print(initialize(root))
    elif args.database == "market":
        from market_intelligence import MarketStore
        path = root / "market-intelligence/market.sqlite"
        MarketStore(path).initialize()
        print(path)
    else:
        from finance_center import FinanceCenter
        center = FinanceCenter(root)
        center.initialize()
        print(center.db_path)


if __name__ == "__main__":
    main()
