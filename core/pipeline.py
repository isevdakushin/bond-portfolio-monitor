"""Полный расчёт по снимку (и, если есть, предыдущему снимку) — один вход для интерфейса, скрипта и отчёта."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from .analytics import Analysis, analyze
from .estimate import Estimate, estimate
from .stress import run_all as run_stress
from .gspread import GSpreadResult, recompute
from .loader import Snapshot
from .market import Market, curves_between, load_market
from .quality import run_checks
from .rules import evaluate as evaluate_rules
from .signals import relative_value, spread_changes
from .snapshots import Comparison, compare, inherit_targets, order_snapshots


@dataclass
class Results:
    snap: Snapshot
    cfg: dict
    quality: pd.DataFrame
    analysis: Analysis
    market: Market | None = None
    gspread: GSpreadResult | None = None
    signals: pd.DataFrame | None = None
    rules: dict | None = None
    prev: Snapshot | None = None
    market_prev: Market | None = None
    comparison: Comparison | None = None
    spread_changes: pd.DataFrame | None = None
    corrections: pd.DataFrame | None = None    # журнал поправок аналитика (если были)
    estimate: Estimate | None = None
    stress: dict | None = None
    notes: list = field(default_factory=list)
    today: dt.date = field(default_factory=dt.date.today)


def run(snap: Snapshot, cfg: dict, prev: Snapshot | None = None, fred_key: str | None = None,
        market: Market | None = None, market_prev: Market | None = None, use_market: bool = True,
        curves_hist: pd.DataFrame | None = None, today: dt.date | None = None) -> Results:
    """market / market_prev / curves_hist можно передать готовыми (интерфейс кэширует их)."""
    notes = []
    if prev is not None:
        snap, prev, note = order_snapshots(snap, prev)
        notes += [note] if note else []
        snap, inh = inherit_targets(snap, prev)
        notes += inh
    r = Results(snap=snap, cfg=cfg, quality=run_checks(snap, cfg), analysis=analyze(snap, cfg), prev=prev,
                notes=notes, today=today or dt.date.today())
    if use_market:
        r.market = market or load_market(snap.as_of, fred_key, today=today)
        if prev is not None:
            r.market_prev = market_prev or load_market(prev.as_of, fred_key, today=today)
    if r.market is not None and r.market.curve_asof is not None:
        if curves_hist is None and snap.as_of:
            win = cfg["gspread"]["implied_date_window_days"]
            try:
                curves_hist = curves_between(snap.as_of - dt.timedelta(days=win), snap.as_of)
            except Exception:  # noqa: BLE001 — диагностика необязательна
                curves_hist = None
        r.gspread = recompute(snap, r.market.curve_asof, cfg, curves_hist)
    spread = None
    if cfg["signals"]["spread_source"] == "recomputed" and r.gspread is not None:
        spread = pd.Series(r.gspread.table["G (срок)" if r.gspread.primary == "maturity" else "G (дюрация)"].values,
                           index=snap.positions.index)
    r.signals = relative_value(snap, cfg, spread)
    if r.market is not None:
        r.rules = evaluate_rules(snap, r.market, cfg, today=r.today)
    r.stress = run_stress(snap, cfg)
    if r.market is not None and r.market.curve_asof is not None and r.market.curve_latest is not None:
        try:
            r.estimate = estimate(snap, cfg, fred_key, r.market.curve_asof, r.market.curve_latest, today=r.today)
        except Exception as e:  # noqa: BLE001 — оценка необязательна; причина показывается
            r.notes.append(f"Оценка на текущую дату недоступна: {e}")
    if prev is not None:
        r.spread_changes = spread_changes(snap, prev, cfg)
        r.comparison = compare(snap, prev, cfg, r.market.curve_asof if r.market else None,
                               r.market_prev.curve_asof if r.market_prev else None)
    return r
