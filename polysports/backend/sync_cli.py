from __future__ import annotations
import argparse
from sqlalchemy.orm import Session
from .config import get_settings
from .database import make_engine
from .models import SyncRun
from .sports_sync import run_sync


def main():
    parser = argparse.ArgumentParser(description='手动同步未来 36 小时赛程')
    parser.add_argument('sport',choices=('football','nba'))
    args = parser.parse_args()
    settings = get_settings()
    provider, key = ('polymarket',None) if args.sport == 'football' else ('balldontlie',settings.balldontlie_key)
    engine = make_engine(settings)
    try:
        run_id = run_sync(engine, provider, key.get_secret_value() if key else '')
        with Session(engine) as db:
            run = db.get(SyncRun,run_id)
            print(f'同步运行 {run_id}: {run.status}; 记录数 {run.items_upserted}; 错误 {run.error_code or "无"}')
            return 0 if run.status == 'succeeded' else 1
    finally:
        engine.dispose()

if __name__ == '__main__':
    raise SystemExit(main())
