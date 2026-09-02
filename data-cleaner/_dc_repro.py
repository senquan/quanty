import asyncio
import json
import sys
import traceback
import warnings

warnings.filterwarnings('ignore')
sys.path.insert(0, '.')

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.strategy import engine  # noqa: E402

e = create_engine(settings.DATABASE_URL.replace('+asyncpg', '+psycopg2'))
with e.connect() as c:
    cfg = c.execute(text(
        'SELECT config FROM factor.factor_strategies WHERE id=14')).scalar()
if isinstance(cfg, str):
    cfg = json.loads(cfg)


def _work():
    return engine.compute_target(cfg, '2026-09-01')


async def main():
    loop = asyncio.get_running_loop()
    for attempt in (1, 2):
        print(f'--- 第 {attempt} 次(同一 loop, 复用线程池) ---', flush=True)
        try:
            r = await loop.run_in_executor(None, _work)
            if isinstance(r, dict) and 'error' in r:
                print('  返回 error:', r['error'])
            else:
                print('  OK date=', r.get('date'),
                      ' holdings=', len(r.get('holdings') or []),
                      ' scores=', len(r.get('scores') or {}))
        except Exception:
            traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())
