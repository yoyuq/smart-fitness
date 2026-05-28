"""
routes_v2_backtest.py — V2 Backtest API Routes

Routes:
- GET /api/v2/backtest/status  — 回测状态
- GET /api/v2/backtest/results  — 回测结果
- POST /api/v2/backtest/start   — 启动回测
- POST /api/v2/backtest/stop    — 停止回测
- GET /api/v2/backtest/trades   — 交易日志
- GET /api/v2/backtest/news     — 新闻情绪数据
"""

import sys, os, json
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# ── 导入回测引擎 ──
QUANT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "quant")
if QUANT_DIR not in sys.path:
    sys.path.insert(0, QUANT_DIR)

router = APIRouter(prefix="/api/v2/backtest", tags=["backtest"])


# ===================== Pydantic Models =====================

class BacktestStartRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    use_llm_news: bool = True
    use_llm_review: bool = True


class BacktestStatus(BaseModel):
    running: bool
    progress: float
    current_date: str
    total_days: int
    current_day: int
    result_available: bool
    error: Optional[str] = None


class BacktestSummary(BaseModel):
    start_date: str
    end_date: str
    initial_capital: float
    final_value: float
    total_return: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    buys: int
    sells: int


class TradeRecord(BaseModel):
    time: str
    type: str
    symbol: str
    price: float
    qty: Optional[int] = 0
    cost: Optional[float] = None


class NewsMood(BaseModel):
    date: str
    mood: str
    score: float
    impact_sectors: List[str]
    key_events: List[str]
    reason: str


class EquityPoint(BaseModel):
    date: str
    value: float
    dd: float
    dd_pct: float


class HoldingsSummary(BaseModel):
    code: str
    name: str
    qty: int
    weight: float
    pnl_pct: float
    market_value: float


class LlmReviewData(BaseModel):
    timestamp: str
    body: str
    tips: List[str]
    date: str
    mood: str
    score: float
    impact_sectors: List[str]
    key_events: List[str]
    reason: str


# ===================== Lazy Imports =====================

_engine_module = None


def _get_engine():
    """延迟加载回测引擎模块"""
    global _engine_module
    if _engine_module is None:
        try:
            import _v6_llm as eng
            _engine_module = eng
        except ImportError:
            from quant import _v6_llm as eng
            _engine_module = eng
    return _engine_module


# ===================== Routes =====================

@router.get("/status", response_model=BacktestStatus)
async def get_status():
    """获取回测运行状态"""
    eng = _get_engine()
    status = eng.get_backtest_status()
    return BacktestStatus(
        running=status.get("running", False),
        progress=status.get("progress", 0.0),
        current_date=status.get("current_date", ""),
        total_days=status.get("total_days", 0),
        current_day=status.get("current_day", 0),
        result_available=status.get("result_available", False),
        error=status.get("error"),
    )


@router.get("/results")
async def get_results():
    """获取回测结果"""
    eng = _get_engine()
    result = eng.get_backtest_results()
    if result is None:
        raise HTTPException(status_code=404, detail="没有回测结果，请先运行回测")
    # 截断大字段
    truncated = dict(result)
    if len(truncated.get("snapshots", [])) > 1000:
        truncated["snapshots"] = truncated["snapshots"][:: max(1, len(truncated["snapshots"]) // 1000)]
    if len(truncated.get("trades", [])) > 200:
        truncated["trades"] = truncated["trades"][-200:]
    return truncated


@router.post("/start")
async def start_backtest(req: BacktestStartRequest):
    """启动回测（短跑模式：默认近90天）"""
    eng = _get_engine()
    status = eng.get_backtest_status()
    if status.get("running"):
        raise HTTPException(status_code=400, detail="回测正在运行中")
    # 短跑默认值
    start = req.start_date or "2026-01-01"
    end = req.end_date or "2026-05-13"
    result = eng.start_backtest_async(
        start_date=start,
        end_date=end,
        use_llm_news=req.use_llm_news,
        use_llm_review=req.use_llm_review,
    )
    return result


@router.post("/stop")
async def stop_backtest():
    eng = _get_engine()
    return eng.stop_backtest()


@router.get("/trades", response_model=List[TradeRecord])
async def get_trades(limit: int = Query(default=100, ge=1, le=1000)):
    """获取交易日志"""
    eng = _get_engine()
    trades = eng.get_backtest_trades(limit=limit)
    result = []
    for t in trades:
        result.append(TradeRecord(
            time=t.get("time", ""),
            type=t.get("type", ""),
            symbol=t.get("symbol", ""),
            price=t.get("price", 0),
            qty=t.get("qty", 0),
            cost=t.get("cost"),
        ))
    return result


@router.get("/news")
async def get_news():
    """获取新闻情绪数据"""
    eng = _get_engine()
    return eng.get_backtest_news()


@router.get("/equity", response_model=List[EquityPoint])
async def get_equity():
    """获取资产曲线数据"""
    eng = _get_engine()
    data = eng.get_equity_curve()
    result = []
    for item in data:
        result.append(EquityPoint(
            date=item.get("date", ""),
            value=item.get("value", 0.0),
            dd=item.get("dd", 0.0),
            dd_pct=item.get("dd_pct", 0.0),
        ))
    return result


@router.get("/holdings", response_model=List[HoldingsSummary])
async def get_holdings():
    """获取当前持仓"""
    eng = _get_engine()
    holdings = eng.get_current_holdings()
    result = []
    for item in holdings:
        result.append(HoldingsSummary(
            code=item.get("code", ""),
            name=item.get("name", ""),
            qty=item.get("qty", 0),
            weight=item.get("weight", 0.0),
            pnl_pct=item.get("pnl_pct", 0.0),
            market_value=item.get("market_value", 0.0),
        ))
    return result


@router.get("/llm-review", response_model=LlmReviewData)
async def get_llm_review():
    """获取最新 LLM 复盘"""
    eng = _get_engine()
    review = eng.get_latest_llm_review()
    return LlmReviewData(
        timestamp=review.get("timestamp", ""),
        body=review.get("body", ""),
        tips=review.get("tips", []),
    )


@router.get("/news-summary")
async def get_news_summary():
    """获取新闻情绪统计"""
    eng = _get_engine()
    return eng.get_news_statistics()


@router.get("/logs")
async def get_logs(limit: int = Query(default=50, ge=1, le=500)):
    """获取最近日志"""
    eng = _get_engine()
    logs = eng.get_log_buffer()
    return {"logs": logs[-limit:]}


@router.get("/config")
async def get_config():
    """获取当前配置参数"""
    eng = _get_engine()
    return {
        "start_date": eng.START_DATE,
        "end_date": eng.END_DATE,
        "buy_t": eng.BUY_T,
        "sell_t": eng.SELL_T,
        "stop_loss": eng.STOP_LOSS,
        "take_profit": eng.TAKE_PROFIT,
        "max_positions": eng.MAX_POSITIONS,
        "stock_pool_count": len(eng.STOCK_POOL),
        "initial_capital": float(eng.INITIAL_CAPITAL),
        "llm_review_interval": eng.LLM_REVIEW_INTERVAL,
        "use_trailing": eng.USE_TRAILING,
        "trailing_stop": eng.TRAILING_STOP,
    }
