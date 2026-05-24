"""一次性初始化脚本：建库 + 注入池子 + 拉一次数据。
执行方式：cd system && python data/seed_pool.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils_fundamentals import get_db, MOAT_POOL
from scrapers.fetch_financials import sync_all_pool

if __name__ == "__main__":
    conn = get_db()
    conn.close()
    print(f"DB 初始化完成，池子 {len(MOAT_POOL)} 只")
    print("开始首次拉取...")
    results = sync_all_pool()
    for t, ok, msg in results:
        print(f"{'✅' if ok else '❌'} {msg}")
