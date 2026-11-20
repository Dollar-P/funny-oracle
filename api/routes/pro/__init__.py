"""Pro ("The Desk") API under /api/pro (PRO.md §8). Paper trading only."""
from fastapi import APIRouter

from api.routes.pro import backtests, evaluation, events, ledger, risk, scheduler, settings, signals

router = APIRouter(prefix="/pro", tags=["pro"])
for _m in (settings, signals, ledger, risk, scheduler, evaluation, backtests, events):
    router.include_router(_m.router)
