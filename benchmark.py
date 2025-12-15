# -*- coding: utf-8 -*-
"""
트레이딩 전략 벤치마크 시스템
- 매수/매도 조건별 성능 추적
- MFE/MAE 분석
- 파라미터 비교 분석
"""

import os
import csv
import json
import time
import statistics
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
import threading

KST = timezone(timedelta(hours=9))

# =========================
# 📊 트레이드 기록 데이터 구조
# =========================
@dataclass
class TradeRecord:
    """개별 거래 기록"""
    trade_id: str
    market: str

    # 진입 정보
    entry_time: float  # timestamp
    entry_price: float
    entry_reason: str  # ign, early, mega, probe, confirm, grind, etc.
    entry_mode: str    # probe / confirm
    entry_conditions: Dict = field(default_factory=dict)  # 진입 당시 조건들

    # 청산 정보 (완료 후 업데이트)
    exit_time: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""  # HARD_STOP, TRAIL_EXIT, TP1, STALL_CUT, etc.

    # 성과 지표
    pnl_pct: float = 0.0       # 순수익률 (수수료 포함)
    pnl_krw: float = 0.0       # 원화 손익
    mfe_pct: float = 0.0       # Maximum Favorable Excursion (최대 상승률)
    mae_pct: float = 0.0       # Maximum Adverse Excursion (최대 하락률)
    hold_seconds: int = 0      # 보유 시간

    # 메타 정보
    hour_of_day: int = 0
    day_of_week: int = 0
    volume_surge: float = 0.0
    spread_pct: float = 0.0

    def is_win(self) -> bool:
        return self.pnl_pct > 0

    def to_dict(self) -> dict:
        return asdict(self)


# =========================
# 📈 벤치마크 통계 클래스
# =========================
class BenchmarkStats:
    """전략 성능 통계 집계"""

    def __init__(self):
        self.trades: List[TradeRecord] = []
        self._lock = threading.Lock()

        # 조건별 성능 집계
        self.stats_by_entry_reason: Dict[str, List[TradeRecord]] = defaultdict(list)
        self.stats_by_exit_reason: Dict[str, List[TradeRecord]] = defaultdict(list)
        self.stats_by_entry_mode: Dict[str, List[TradeRecord]] = defaultdict(list)
        self.stats_by_hour: Dict[int, List[TradeRecord]] = defaultdict(list)

    def add_trade(self, trade: TradeRecord):
        """거래 기록 추가"""
        with self._lock:
            self.trades.append(trade)
            self.stats_by_entry_reason[trade.entry_reason].append(trade)
            self.stats_by_exit_reason[trade.exit_reason].append(trade)
            self.stats_by_entry_mode[trade.entry_mode].append(trade)
            self.stats_by_hour[trade.hour_of_day].append(trade)

    def calc_stats(self, trades: List[TradeRecord]) -> dict:
        """거래 리스트에서 통계 계산"""
        if not trades:
            return {"count": 0}

        wins = [t for t in trades if t.is_win()]
        losses = [t for t in trades if not t.is_win()]

        pnls = [t.pnl_pct for t in trades]
        mfes = [t.mfe_pct for t in trades]
        maes = [t.mae_pct for t in trades]  # 음수값들

        win_rate = len(wins) / len(trades) * 100 if trades else 0

        avg_win = statistics.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = statistics.mean([t.pnl_pct for t in losses]) if losses else 0

        # Profit Factor
        gross_profit = sum(t.pnl_pct for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl_pct for t in losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # 기대값 (Expectancy)
        expectancy = (win_rate/100 * avg_win) + ((100-win_rate)/100 * avg_loss)

        # MFE/MAE 분석
        avg_mfe = statistics.mean(mfes) if mfes else 0
        avg_mae = statistics.mean(maes) if maes else 0

        # 이익 실현 효율 (실현 수익 / 최대 가능 수익)
        capture_ratios = []
        for t in wins:
            if t.mfe_pct > 0:
                capture_ratios.append(t.pnl_pct / t.mfe_pct)
        avg_capture = statistics.mean(capture_ratios) * 100 if capture_ratios else 0

        # 손실 통제 효율 (실제 손실 / 최대 노출 손실)
        loss_control_ratios = []
        for t in losses:
            if t.mae_pct < 0:
                # mae가 음수이므로 절대값 비교
                loss_control_ratios.append(t.pnl_pct / t.mae_pct)
        avg_loss_control = statistics.mean(loss_control_ratios) * 100 if loss_control_ratios else 0

        return {
            "count": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(statistics.mean(pnls), 3) if pnls else 0,
            "total_pnl": round(sum(pnls), 2),
            "avg_win": round(avg_win, 3),
            "avg_loss": round(avg_loss, 3),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 3),
            "avg_mfe": round(avg_mfe, 3),
            "avg_mae": round(avg_mae, 3),
            "capture_efficiency": round(avg_capture, 1),
            "loss_control_efficiency": round(avg_loss_control, 1),
            "avg_hold_sec": round(statistics.mean([t.hold_seconds for t in trades]), 0) if trades else 0,
        }

    def get_summary(self) -> dict:
        """전체 요약 통계"""
        with self._lock:
            return {
                "overall": self.calc_stats(self.trades),
                "by_entry_reason": {
                    k: self.calc_stats(v)
                    for k, v in self.stats_by_entry_reason.items()
                },
                "by_exit_reason": {
                    k: self.calc_stats(v)
                    for k, v in self.stats_by_exit_reason.items()
                },
                "by_entry_mode": {
                    k: self.calc_stats(v)
                    for k, v in self.stats_by_entry_mode.items()
                },
                "by_hour": {
                    k: self.calc_stats(v)
                    for k, v in sorted(self.stats_by_hour.items())
                },
            }

    def print_report(self):
        """콘솔에 리포트 출력"""
        summary = self.get_summary()

        print("\n" + "="*60)
        print("📊 트레이딩 전략 벤치마크 리포트")
        print("="*60)

        # 전체 성과
        overall = summary["overall"]
        print(f"\n🎯 전체 성과 ({overall['count']}건)")
        print(f"   승률: {overall['win_rate']}% ({overall['wins']}W / {overall['losses']}L)")
        print(f"   총 손익: {overall['total_pnl']:+.2f}%")
        print(f"   평균 손익: {overall['avg_pnl']:+.3f}%")
        print(f"   Profit Factor: {overall['profit_factor']}")
        print(f"   기대값(Expectancy): {overall['expectancy']:+.3f}%")
        print(f"   평균 MFE: {overall['avg_mfe']:+.3f}% | 평균 MAE: {overall['avg_mae']:+.3f}%")
        print(f"   이익실현효율: {overall['capture_efficiency']}% | 손실통제효율: {overall['loss_control_efficiency']}%")

        # 진입 사유별 성과
        print(f"\n📥 진입 사유별 성과")
        print("-"*60)
        for reason, stats in sorted(summary["by_entry_reason"].items(),
                                    key=lambda x: x[1].get('win_rate', 0), reverse=True):
            if stats['count'] > 0:
                print(f"   {reason:15} | {stats['count']:3}건 | 승률 {stats['win_rate']:5.1f}% | "
                      f"PnL {stats['avg_pnl']:+.3f}% | PF {stats['profit_factor']:.2f}")

        # 청산 사유별 성과
        print(f"\n📤 청산 사유별 성과")
        print("-"*60)
        for reason, stats in sorted(summary["by_exit_reason"].items(),
                                    key=lambda x: x[1].get('count', 0), reverse=True):
            if stats['count'] > 0:
                avg_pnl_str = f"{stats['avg_pnl']:+.3f}%" if stats['avg_pnl'] else "N/A"
                print(f"   {reason:25} | {stats['count']:3}건 | 승률 {stats['win_rate']:5.1f}% | "
                      f"평균 {avg_pnl_str}")

        # 진입 모드별 성과 (probe vs confirm)
        print(f"\n🎮 진입 모드별 성과")
        print("-"*60)
        for mode, stats in summary["by_entry_mode"].items():
            if stats['count'] > 0:
                print(f"   {mode:15} | {stats['count']:3}건 | 승률 {stats['win_rate']:5.1f}% | "
                      f"PnL {stats['avg_pnl']:+.3f}% | MFE {stats['avg_mfe']:+.3f}%")

        # 시간대별 성과
        print(f"\n🕐 시간대별 성과")
        print("-"*60)
        for hour, stats in summary["by_hour"].items():
            if stats['count'] >= 2:
                bar = "█" * int(stats['win_rate'] / 10)
                print(f"   {hour:02d}시 | {stats['count']:3}건 | 승률 {stats['win_rate']:5.1f}% {bar}")

        print("\n" + "="*60)


# =========================
# 🔬 파라미터 A/B 테스트
# =========================
class ParameterComparison:
    """
    두 가지 설정의 성능 비교
    - 이전 설정 vs 새 설정
    """

    # 비교할 핵심 파라미터들
    PARAM_SETS = {
        "old_good": {
            "TOP_N": 60,
            "TRAIL_ARM_GAIN": 0.012,
            "TRAIL_DISTANCE_MIN": 0.010,
            "POSTCHECK_ENABLED": False,
            "ADD1_MIN_GAIN": 0.003,  # 더 완화
            "ADD1_MIN_BUY": 0.56,    # 더 완화
            "PROBE_TIMEOUT_SEC": 120,
        },
        "new_strict": {
            "TOP_N": 120,
            "TRAIL_ARM_GAIN": 0.003,   # 더 빠른 트레일 무장
            "TRAIL_DISTANCE_MIN": 0.005, # 더 타이트한 트레일
            "POSTCHECK_ENABLED": True,
            "ADD1_MIN_GAIN": 0.004,   # 더 엄격
            "ADD1_MIN_BUY": 0.60,     # 더 엄격
            "PROBE_TIMEOUT_SEC": 180,
        }
    }

    @classmethod
    def analyze_diff(cls):
        """두 설정의 차이 분석"""
        old = cls.PARAM_SETS["old_good"]
        new = cls.PARAM_SETS["new_strict"]

        print("\n" + "="*60)
        print("🔍 파라미터 변경 분석 (이전 vs 현재)")
        print("="*60)

        issues = []

        for key in old:
            if key in new:
                old_val = old[key]
                new_val = new[key]

                if old_val != new_val:
                    print(f"\n   {key}:")
                    print(f"      이전: {old_val}")
                    print(f"      현재: {new_val}")

                    # 영향 분석
                    if key == "TRAIL_ARM_GAIN":
                        if new_val < old_val:
                            issues.append(f"⚠️ TRAIL_ARM_GAIN 감소 ({old_val} → {new_val}): "
                                        f"너무 빨리 트레일 무장 → 조기 청산 증가 가능")

                    elif key == "TRAIL_DISTANCE_MIN":
                        if new_val < old_val:
                            issues.append(f"⚠️ TRAIL_DISTANCE_MIN 감소 ({old_val} → {new_val}): "
                                        f"더 타이트한 트레일 → 작은 눌림에도 청산")

                    elif key == "POSTCHECK_ENABLED":
                        if new_val and not old_val:
                            issues.append(f"⚠️ POSTCHECK_ENABLED 활성화: "
                                        f"추가 필터링 → 좋은 신호도 놓칠 수 있음")

                    elif key == "ADD1_MIN_GAIN":
                        if new_val > old_val:
                            issues.append(f"⚠️ ADD1_MIN_GAIN 증가 ({old_val} → {new_val}): "
                                        f"추매 조건 엄격화 → probe→confirm 전환 감소")

                    elif key == "ADD1_MIN_BUY":
                        if new_val > old_val:
                            issues.append(f"⚠️ ADD1_MIN_BUY 증가 ({old_val} → {new_val}): "
                                        f"매수비 요구치 상승 → 진입 기회 감소")

        if issues:
            print("\n" + "-"*60)
            print("🚨 잠재적 문제점:")
            for issue in issues:
                print(f"   {issue}")

        print("\n" + "="*60)

        return issues


# =========================
# 📁 CSV 로깅
# =========================
class BenchmarkLogger:
    """벤치마크 데이터 CSV 저장"""

    CSV_FIELDS = [
        "trade_id", "market", "entry_time", "exit_time",
        "entry_price", "exit_price", "entry_reason", "exit_reason",
        "entry_mode", "pnl_pct", "pnl_krw", "mfe_pct", "mae_pct",
        "hold_seconds", "hour_of_day", "day_of_week",
        "volume_surge", "spread_pct", "win"
    ]

    def __init__(self, filepath: str = "benchmark_trades.csv"):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._ensure_header()

    def _ensure_header(self):
        """CSV 헤더 생성 (파일 없으면)"""
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
                writer.writeheader()

    def log_trade(self, trade: TradeRecord):
        """거래 기록 저장"""
        with self._lock:
            with open(self.filepath, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
                row = {
                    "trade_id": trade.trade_id,
                    "market": trade.market,
                    "entry_time": datetime.fromtimestamp(trade.entry_time, KST).strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_time": datetime.fromtimestamp(trade.exit_time, KST).strftime("%Y-%m-%d %H:%M:%S") if trade.exit_time else "",
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "entry_reason": trade.entry_reason,
                    "exit_reason": trade.exit_reason,
                    "entry_mode": trade.entry_mode,
                    "pnl_pct": round(trade.pnl_pct, 4),
                    "pnl_krw": round(trade.pnl_krw, 0),
                    "mfe_pct": round(trade.mfe_pct, 4),
                    "mae_pct": round(trade.mae_pct, 4),
                    "hold_seconds": trade.hold_seconds,
                    "hour_of_day": trade.hour_of_day,
                    "day_of_week": trade.day_of_week,
                    "volume_surge": round(trade.volume_surge, 2),
                    "spread_pct": round(trade.spread_pct, 3),
                    "win": 1 if trade.is_win() else 0,
                }
                writer.writerow(row)

    def load_trades(self) -> List[TradeRecord]:
        """저장된 거래 기록 로드"""
        trades = []
        if not os.path.exists(self.filepath):
            return trades

        with open(self.filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trade = TradeRecord(
                    trade_id=row["trade_id"],
                    market=row["market"],
                    entry_time=datetime.strptime(row["entry_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp() if row["entry_time"] else 0,
                    entry_price=float(row["entry_price"] or 0),
                    entry_reason=row["entry_reason"],
                    entry_mode=row["entry_mode"],
                    exit_time=datetime.strptime(row["exit_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp() if row["exit_time"] else 0,
                    exit_price=float(row["exit_price"] or 0),
                    exit_reason=row["exit_reason"],
                    pnl_pct=float(row["pnl_pct"] or 0),
                    pnl_krw=float(row["pnl_krw"] or 0),
                    mfe_pct=float(row["mfe_pct"] or 0),
                    mae_pct=float(row["mae_pct"] or 0),
                    hold_seconds=int(row["hold_seconds"] or 0),
                    hour_of_day=int(row["hour_of_day"] or 0),
                    day_of_week=int(row["day_of_week"] or 0),
                    volume_surge=float(row["volume_surge"] or 0),
                    spread_pct=float(row["spread_pct"] or 0),
                )
                trades.append(trade)

        return trades


# =========================
# 🎮 글로벌 벤치마크 인스턴스
# =========================
_benchmark_stats = BenchmarkStats()
_benchmark_logger = BenchmarkLogger()


def get_benchmark_stats() -> BenchmarkStats:
    """글로벌 벤치마크 통계 인스턴스"""
    return _benchmark_stats


def get_benchmark_logger() -> BenchmarkLogger:
    """글로벌 벤치마크 로거 인스턴스"""
    return _benchmark_logger


# =========================
# 🔗 메인 봇 통합 함수들
# =========================
_active_trades: Dict[str, TradeRecord] = {}
_trades_lock = threading.Lock()


def on_entry(market: str, entry_price: float, entry_reason: str,
             entry_mode: str, conditions: dict = None) -> str:
    """
    진입 시 호출 - TradeRecord 생성

    Args:
        market: 마켓 코드 (예: KRW-BTC)
        entry_price: 진입가
        entry_reason: 진입 사유 (ign, early, mega, probe, grind, etc.)
        entry_mode: 진입 모드 (probe / confirm)
        conditions: 진입 당시 조건들 (선택)

    Returns:
        trade_id: 거래 ID
    """
    now = time.time()
    now_dt = datetime.fromtimestamp(now, KST)

    trade_id = f"{market}_{int(now*1000)}"

    trade = TradeRecord(
        trade_id=trade_id,
        market=market,
        entry_time=now,
        entry_price=entry_price,
        entry_reason=entry_reason,
        entry_mode=entry_mode,
        entry_conditions=conditions or {},
        hour_of_day=now_dt.hour,
        day_of_week=now_dt.weekday(),
        volume_surge=conditions.get("volume_surge", 0) if conditions else 0,
        spread_pct=conditions.get("spread", 0) if conditions else 0,
    )

    with _trades_lock:
        _active_trades[market] = trade

    return trade_id


def on_exit(market: str, exit_price: float, exit_reason: str,
            mfe_pct: float = 0.0, mae_pct: float = 0.0,
            pnl_krw: float = 0.0):
    """
    청산 시 호출 - TradeRecord 완성 및 저장

    Args:
        market: 마켓 코드
        exit_price: 청산가
        exit_reason: 청산 사유 (HARD_STOP, TRAIL_EXIT, TP1, etc.)
        mfe_pct: 최대 수익률 %
        mae_pct: 최대 손실률 % (음수)
        pnl_krw: 원화 손익
    """
    with _trades_lock:
        trade = _active_trades.pop(market, None)

    if not trade:
        print(f"[BENCHMARK] {market} 활성 거래 없음 - 기록 생략")
        return

    now = time.time()

    # 거래 완성
    trade.exit_time = now
    trade.exit_price = exit_price
    trade.exit_reason = exit_reason
    trade.mfe_pct = mfe_pct
    trade.mae_pct = mae_pct
    trade.hold_seconds = int(now - trade.entry_time)

    # PnL 계산 (수수료 0.1% 반영)
    FEE_RATE = 0.001
    if trade.entry_price > 0:
        raw_pnl = (exit_price / trade.entry_price - 1.0) * 100.0
        trade.pnl_pct = raw_pnl - (FEE_RATE * 100.0)  # 왕복 수수료 차감

    trade.pnl_krw = pnl_krw

    # 통계에 추가
    _benchmark_stats.add_trade(trade)

    # CSV 로깅
    _benchmark_logger.log_trade(trade)

    print(f"[BENCHMARK] {market} 기록 완료: {trade.entry_reason} → {exit_reason} | "
          f"PnL {trade.pnl_pct:+.2f}% | MFE {mfe_pct:+.2f}% | MAE {mae_pct:+.2f}%")


def update_mfe_mae(market: str, current_price: float):
    """
    모니터링 중 MFE/MAE 업데이트

    Args:
        market: 마켓 코드
        current_price: 현재가
    """
    with _trades_lock:
        trade = _active_trades.get(market)
        if not trade:
            return

        if trade.entry_price > 0:
            current_pnl = (current_price / trade.entry_price - 1.0) * 100.0
            trade.mfe_pct = max(trade.mfe_pct, current_pnl)
            trade.mae_pct = min(trade.mae_pct, current_pnl)


def print_benchmark_report():
    """현재까지 벤치마크 리포트 출력"""
    _benchmark_stats.print_report()


def analyze_parameters():
    """파라미터 변경 분석"""
    ParameterComparison.analyze_diff()


# =========================
# 📊 MFE/MAE 진단 함수
# =========================
def diagnose_mfe_mae():
    """
    MFE/MAE 진단: 이익 반납 / 손절 타이밍 분석

    핵심 진단 포인트:
    1. 손절 거래의 MAE가 SL보다 작음 → 손절가 도달 전 손절 (너무 빠름)
    2. 익절 거래의 MFE >> 실현 수익 → 이익 반납 (너무 늦음)
    3. 손절 거래의 MFE > 0 → 수익 구간 진입 후 손절 (트레일링 문제)
    """
    trades = _benchmark_logger.load_trades()
    if not trades:
        print("[DIAGNOSE] 거래 기록 없음")
        return

    print("\n" + "="*60)
    print("🔬 MFE/MAE 진단 리포트")
    print("="*60)

    wins = [t for t in trades if t.is_win()]
    losses = [t for t in trades if not t.is_win()]

    # 1. 익절 거래 - 이익 반납 분석
    if wins:
        givebacks = []
        for t in wins:
            if t.mfe_pct > 0:
                giveback = t.mfe_pct - t.pnl_pct
                givebacks.append((t, giveback))

        givebacks.sort(key=lambda x: x[1], reverse=True)

        print(f"\n📈 익절 거래 이익 반납 분석 ({len(wins)}건)")
        print("-"*60)

        avg_giveback = statistics.mean([g[1] for g in givebacks]) if givebacks else 0
        print(f"   평균 이익 반납: {avg_giveback:.3f}%")

        if givebacks:
            worst_givebacks = givebacks[:5]
            print(f"   최악 반납 Top 5:")
            for t, gb in worst_givebacks:
                print(f"      {t.market} | MFE {t.mfe_pct:+.2f}% → 실현 {t.pnl_pct:+.2f}% | "
                      f"반납 {gb:.2f}% | 사유: {t.exit_reason}")

    # 2. 손절 거래 - 수익 구간 진입 후 손절 분석
    if losses:
        profit_then_loss = [t for t in losses if t.mfe_pct > 0.3]  # 0.3% 이상 수익 봤다가 손절

        print(f"\n📉 손절 거래 분석 ({len(losses)}건)")
        print("-"*60)
        print(f"   수익 구간 진입 후 손절: {len(profit_then_loss)}건 ({len(profit_then_loss)/len(losses)*100:.1f}%)")

        if profit_then_loss:
            print(f"   상세:")
            for t in profit_then_loss[:5]:
                print(f"      {t.market} | MFE {t.mfe_pct:+.2f}% → 손절 {t.pnl_pct:+.2f}% | "
                      f"사유: {t.exit_reason}")

    # 3. 조기 청산 의심 (MAE가 작은데 손절)
    if losses:
        early_stops = [t for t in losses if t.mae_pct > -1.0 and t.pnl_pct < -0.3]
        print(f"\n⚡ 조기 청산 의심 ({len(early_stops)}건)")
        print("-"*60)
        if early_stops:
            for t in early_stops[:5]:
                print(f"      {t.market} | MAE {t.mae_pct:+.2f}% | 실현 {t.pnl_pct:+.2f}% | "
                      f"사유: {t.exit_reason}")

    # 4. 청산 사유별 MFE/MAE 평균
    print(f"\n📊 청산 사유별 MFE/MAE")
    print("-"*60)
    exit_reasons = defaultdict(list)
    for t in trades:
        exit_reasons[t.exit_reason].append(t)

    for reason, ts in sorted(exit_reasons.items(), key=lambda x: len(x[1]), reverse=True):
        if len(ts) >= 2:
            avg_mfe = statistics.mean([t.mfe_pct for t in ts])
            avg_mae = statistics.mean([t.mae_pct for t in ts])
            avg_pnl = statistics.mean([t.pnl_pct for t in ts])
            print(f"   {reason:25} | {len(ts):3}건 | MFE {avg_mfe:+.2f}% | MAE {avg_mae:+.2f}% | PnL {avg_pnl:+.2f}%")

    print("\n" + "="*60)


# =========================
# CLI 명령어
# =========================
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("사용법:")
        print("  python benchmark.py report     - 벤치마크 리포트 출력")
        print("  python benchmark.py diagnose   - MFE/MAE 진단")
        print("  python benchmark.py params     - 파라미터 변경 분석")
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "report":
        # CSV에서 로드 후 리포트
        logger = BenchmarkLogger()
        trades = logger.load_trades()

        if trades:
            stats = BenchmarkStats()
            for t in trades:
                stats.add_trade(t)
            stats.print_report()
        else:
            print("거래 기록 없음")

    elif cmd == "diagnose":
        diagnose_mfe_mae()

    elif cmd == "params":
        analyze_parameters()

    else:
        print(f"알 수 없는 명령어: {cmd}")
