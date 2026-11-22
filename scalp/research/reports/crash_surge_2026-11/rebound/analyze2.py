# -*- coding: utf-8 -*-
"""급락 후 반등 검토 — 검토 의견(2026-11-20) 반영판. 관찰만(매도 규칙 없음).
판단 기준은 결과를 보기 전에 머리말에 적는다. 결정은 고정 시점(5·15·30·60분) 비용 뒤 수익으로만, 최고 상승폭은 참고."""
import datetime as dt
import importlib.util
import math
import pickle
import statistics as st
import sys
from pathlib import Path

DATA, OUT = Path(sys.argv[1]), Path(sys.argv[2])
S = pickle.loads((DATA / "signals.pkl").read_bytes())
TH, VOL_X, CUT = -0.01821, 3.0, "2025-01-01"
BTC, ETH = "KRW-BTC", "KRW-ETH"
COST = {BTC: 0.0007, ETH: 0.0007}
EXTRA = (0.003, 0.008)                     # 알트의 추가 체결 손실 가정 (11/15 실측 거래당 0.3~0.8%p)
RECENT = "2026-11-13"                      # 업비트가 체결 기록을 주는 최근 7일
T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
        2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)


def pct(v, d=2):
    return "-" if v is None else ("%+." + str(d) + "f%%") % (100 * v)


def mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def ci95(vals):
    """평균과 양측 95% 구간 (t). 값이 둘 미만이면 (평균, None, None)."""
    vals = [x for x in vals if x is not None]
    if not vals:
        return None, None, None
    m = mean(vals)
    if len(vals) < 2:
        return m, None, None
    h = (T975[len(vals) - 2] if len(vals) - 1 <= 30 else 1.96) * st.stdev(vals) / math.sqrt(len(vals))
    return m, m - h, m + h


def net(m, r):
    c = COST.get(m, 0.001)
    return (1 + r) * (1 - c) / (1 + c) - 1


def groups():
    alt = S["alt"]
    bt = lambda s: s["btc1h"] is not None and s["btc1h"] <= TH
    nb = lambda s: s["btc1h"] is not None and s["btc1h"] > TH
    return [("1. BTC 직접", "btc", S["btc"]),
            ("2. 동반 하락 (거래대금 조건 없음)", "alt", [s for s in alt if bt(s)]),
            ("2. 동반 하락 (거래대금 3배 이상)", "alt", [s for s in alt if bt(s) and s["mult"] >= VOL_X]),
            ("3. BTC 신호 뒤 15~60분 알트", "lead", S["lead"]),
            ("4. BTC 조건 없는 알트 (조건 없음) ※낙관적", "alt", [s for s in alt if nb(s)]),
            ("4. BTC 조건 없는 알트 (3배 이상) ※낙관적", "alt", [s for s in alt if nb(s) and s["mult"] >= VOL_X])]


def ekey(kind, s):
    return s["btc_q"] if kind == "lead" else s["q"]


def periods(sigs):
    return [("2023–24", [s for s in sigs if s["day"] < CUT]), ("2025–26", [s for s in sigs if s["day"] >= CUT]), ("전체", sigs)]


def by_day_events(kind, sigs, val):
    """사건 평균 → 그날 평균 → [일별 값]. val(신호) 가 None 이면 뺀다."""
    ev = {}
    for s in sigs:
        v = val(s)
        if v is not None:
            ev.setdefault((s["day"], ekey(kind, s)), []).append(v)
    days = {}
    for (d, _k), v in ev.items():
        days.setdefault(d, []).append(mean(v))
    return [mean(v) for v in days.values()], len(ev)


lines = ["# 급락 후 반등 검토 — 결과 (관찰만, 매도 규칙 없음)", "",
         "## 미리 적은 판단 기준 (결과를 보기 전에 적음)", "",
         "1. 이 분석으로 돈을 넣을 규칙을 정하지 않는다. 앞으로 추적할 규칙을 한두 개 고르는 데까지만 쓴다(이 기간 자료는 이미 여러 번 봤다).",
         "2. 같은 시각 비교에서 알트가 비용 뒤 BTC·ETH 보다 **일 단위로 뚜렷이 낫지 않으면**(알트 − BTC, 알트 − ETH 의 일별 평균 95% 구간 하한이 0 이하) "
         "큰 코인(BTC·ETH)으로 간다. 알트의 추가 체결 손실(0.3·0.8%p)을 넣은 값으로도 본다.",
         "3. 2023–24 에서 본 방향이 2025–26 에서도 같은지 확인한다. 다르면 어느 쪽으로도 결론 내지 않는다.",
         "4. 결정은 고정 시점(5·15·30·60분)의 비용 뒤 수익으로만 한다. 최고 상승폭·최대 추가 하락은 참고로만 본다.", "",
         "## 자료와 정의", "",
         "고정 20종목, 2023-01-01 ~ 2026-11-19, 업비트 공개 시세. 신호는 15분봉 마감 기준. BTC 1시간 −1.821% 는 처음 충족한 봉만 신호로 잡고 그 뒤 60분은 같은 사건. "
         "알트 15분 −3% 도 같은 종목은 60분 안에 다시 세지 않는다. 관찰은 신호 다음 봉 시가부터 5분봉(체결이 없는 봉은 직전 종가). "
         "비용 뒤 = 봉 기준 비용(편도 BTC·ETH 0.07%, 그 밖 0.10%). 사건 = 같은 15분봉에서 난 신호 묶음(3번은 앞선 BTC 신호 하나). "
         "※낙관적: 업비트는 지금 상장된 종목 시세만 주므로 혼자 폭락해 상장폐지된 종목이 빠져 있다(4번에 특히 크다).", ""]

# ---------------------------------------------------------------- 핵심: 같은 시각 짝 비교
lines += ["## 1. 핵심 — 같은 시각에 BTC·ETH 를 샀다면 (60분, 비용 뒤, 일 단위로 묶음)", "",
          "알트 − BTC, 알트 − ETH 는 같은 신호 시각의 차이를 사건 → 날 순으로 평균낸 것. [ ] 는 일별 평균의 95% 구간. "
          "'알트 추가 손실' 열은 알트에 체결 손실을 더 뺀 값.", "",
          "| 시나리오 | 기간 | 급락 일수 (사건, 신호) | 신호 종목 60분 | 같은 시각 BTC | 같은 시각 ETH | 알트 − BTC [95%] | 알트 − ETH [95%] | 알트 − max(BTC,ETH), 추가 손실 0.3%p / 0.8%p |",
          "|---|---|---|---|---|---|---|---|---|"]
for name, kind, sigs in groups()[1:]:
    for period, part in periods(sigs):
        ok = [s for s in part if s.get("obs")]
        a_days, n_ev = by_day_events(kind, ok, lambda s: s["obs"]["net60"])
        b_days, _ = by_day_events(kind, ok, lambda s: s["btc"]["net60"] if s.get("btc") else None)
        e_days, _ = by_day_events(kind, ok, lambda s: s["eth"]["net60"] if s.get("eth") else None)
        db, _ = by_day_events(kind, ok, lambda s: s["obs"]["net60"] - s["btc"]["net60"] if s.get("btc") else None)
        de, _ = by_day_events(kind, ok, lambda s: s["obs"]["net60"] - s["eth"]["net60"] if s.get("eth") else None)
        extra = []
        for x in EXTRA:
            dd, _ = by_day_events(kind, ok, lambda s, x=x: s["obs"]["net60"] - x - max(s["btc"]["net60"], s["eth"]["net60"]) if s.get("btc") and s.get("eth") else None)
            m, lo, hi = ci95(dd)
            extra.append("%s [%s, %s]" % (pct(m), pct(lo), pct(hi)))
        mb, lb, hb = ci95(db)
        me, le, he = ci95(de)
        lines.append("| %s | %s | %d (%d, %d) | %s | %s | %s | %s [%s, %s] | %s [%s, %s] | %s |" % (
            name, period, len(a_days), n_ev, len(ok), pct(mean(a_days)), pct(mean(b_days)), pct(mean(e_days)),
            pct(mb), pct(lb), pct(hb), pct(me), pct(le), pct(he), " / ".join(extra)))
lines.append("")

# 같은 시각 BTC·ETH 자체 수익의 구간 — 알트 신호가 난 순간 큰 코인을 샀다면 (사건마다 한 번만 센다: 같은 사건의 신호들은 같은 BTC 값)
lines += ["### 1-2. 알트 신호 시각에 BTC·ETH 를 샀다면 — 그 자체 60분 비용 뒤 [95%]", "",
          "같은 사건 안의 신호들은 BTC·ETH 값이 같으므로 사건마다 한 번 → 날 순으로 묶었다. 1번(BTC 직접, 처음 넘은 봉)과 비교용.", "",
          "| 시나리오 | 기간 | 급락 일수 (사건) | BTC 60분 [95%] | ETH 60분 [95%] | BTC 60분 > 0 인 사건 비율 |", "|---|---|---|---|---|---|"]
for name, kind, sigs in groups()[1:4]:
    for period, part in periods(sigs):
        ok = [s for s in part if s.get("obs") and s.get("btc") and s.get("eth")]
        b_days, n_ev = by_day_events(kind, ok, lambda s: s["btc"]["net60"])
        e_days, _ = by_day_events(kind, ok, lambda s: s["eth"]["net60"])
        ev = {}
        for s in ok:
            ev[(s["day"], ekey(kind, s))] = s["btc"]["net60"]
        mb, lb, hb = ci95(b_days)
        me, le, he = ci95(e_days)
        win = mean([1.0 if v > 0 else 0.0 for v in ev.values()])
        lines.append("| %s | %s | %d (%d) | %s [%s, %s] | %s [%s, %s] | %s |" % (
            name, period, len(b_days), n_ev, pct(mb), pct(lb), pct(hb), pct(me), pct(le), pct(he), "-" if win is None else "%.0f%%" % (100 * win)))
lines.append("")

# 같은 시각 짝 비교를 5·15·30·60분 모두 (검토 의견 2026-11-20 ②) — 확인 시점일 뿐 매도 규칙이 아니다
lines += ["### 1-3. 같은 시각 짝 비교 — 5·15·30·60분 (비용 뒤, 사건 → 날 순으로 묶음)", "",
          "같은 급락에 알트 여러 개가 걸리면 그 알트들의 평균을 그 순간 BTC·ETH 와 한 번 비교한다. 시점은 가격을 보는 때일 뿐 매도 규칙이 아니다.", "",
          "| 시나리오 | 시점 | 급락 일수 | 알트 | BTC | ETH | 알트 − BTC [95%] | 알트 − ETH [95%] | 알트 − BTC: 2023–24 / 2025–26 |", "|---|---|---|---|---|---|---|---|---|"]
HZ = (("5분", "r5"), ("15분", "r15"), ("30분", "r30"), ("60분", "r60"))
for name, kind, sigs in groups()[1:]:
    ok = [s for s in sigs if s.get("obs") and s.get("btc") and s.get("eth")]
    for label, h in HZ:
        a = lambda s, h=h: net(s["m"], s["obs"][h])
        b = lambda s, h=h: net(BTC, s["btc"][h])
        e = lambda s, h=h: net(ETH, s["eth"][h])
        a_d, _ = by_day_events(kind, ok, a)
        b_d, _ = by_day_events(kind, ok, b)
        e_d, _ = by_day_events(kind, ok, e)
        db, _ = by_day_events(kind, ok, lambda s: a(s) - b(s))
        de, _ = by_day_events(kind, ok, lambda s: a(s) - e(s))
        per = []
        for _p, part in periods(ok)[:2]:
            d, _ = by_day_events(kind, part, lambda s: a(s) - b(s))
            per.append(pct(mean(d)))
        mb, lb, hb = ci95(db)
        me, le, he = ci95(de)
        lines.append("| %s | %s | %d | %s | %s | %s | %s [%s, %s] | %s [%s, %s] | %s |" % (
            name, label, len(a_d), pct(mean(a_d)), pct(mean(b_d)), pct(mean(e_d)), pct(mb), pct(lb), pct(hb), pct(me), pct(le), pct(he), " / ".join(per)))
lines.append("")

# ---------------------------------------------------------------- 고정 시점 수익 (비용 뒤)
lines += ["## 2. 시나리오별 고정 시점 수익 (비용 뒤, 일 단위로 묶은 평균과 95% 구간)", "",
          "| 시나리오 | 기간 | 급락 일수 (사건, 신호) | 5분 | 15분 | 30분 | 60분 [95%] | 60분 > 0 인 신호 비율 |", "|---|---|---|---|---|---|---|---|"]
for name, kind, sigs in groups():
    for period, part in periods(sigs):
        ok = [s for s in part if s.get("obs")]
        cells = []
        for h in ("r5", "r15", "r30"):
            d, _ = by_day_events(kind, ok, lambda s, h=h: net(s["m"], s["obs"][h]))
            cells.append(pct(mean(d)))
        d60, n_ev = by_day_events(kind, ok, lambda s: s["obs"]["net60"])
        m, lo, hi = ci95(d60)
        win = mean([1.0 if s["obs"]["net60"] > 0 else 0.0 for s in ok])
        lines.append("| %s | %s | %d (%d, %d) | %s | %s [%s, %s] | %s |" % (name, period, len(d60), n_ev, len(ok), " | ".join(cells), pct(m), pct(lo), pct(hi),
                                                                     "-" if win is None else "%.0f%%" % (100 * win)))
lines.append("")

# ---------------------------------------------------------------- 참고: 최고 상승·최대 추가 하락
lines += ["## 3. 참고 — 1시간 안의 최고 상승·최대 추가 하락 (결정에 쓰지 않음)", "",
          "최고점은 아무 의미 없는 흔들림에서도 늘 플러스로 나온다. 이 표로 매도 기준을 고르지 않는다.", "",
          "| 시나리오 | 신호 | 최고 상승 중앙값 (걸린 분) | 최대 추가 하락 중앙값 (걸린 분) | 고점 뒤 되밀림 중앙값 |", "|---|---|---|---|---|"]
for name, kind, sigs in groups():
    o = [s["obs"] for s in sigs if s.get("obs")]
    if o:
        lines.append("| %s | %d | %s (%d) | %s (%d) | %s |" % (name, len(o), pct(st.median([x["mfe"] for x in o])), st.median([x["t_mfe"] for x in o]),
                                                        pct(st.median([x["mae"] for x in o])), st.median([x["t_mae"] for x in o]), pct(st.median([x["pullback"] for x in o]))))
lines.append("")

# ---------------------------------------------------------------- 5번: 반등 확인 뒤 진입 (진입 없음 포함)
lines += ["## 4. 5번 — 반등 봉 확인 뒤 진입 (최대 60분 기다림, 확인 없으면 '진입 없음')", "",
          "공정 비교: 모든 신호를 분모로 두고, 진입 없음은 수익 0 으로 센다. 진입 없음이던 신호를 기본 진입했다면 어땠는지도 적는다. "
          "확인 진입의 60분은 확인 진입 시각부터 센다(그래서 끝나는 시각이 기본 진입보다 늦다). 평균은 다른 표처럼 사건 → 날 순으로 묶었다.", "",
          "| 시나리오 | 기간 | 급락 일수 (신호) | 진입 없음 (그때 기본 진입 60분) | 확인 진입: 기다린 분 중앙값, 신호 시가 대비 중앙값 | 60분 비용 뒤: 기본 진입 / 확인 진입 (진입 없음 = 0) | 확인 − 기본 [95%] |",
          "|---|---|---|---|---|---|---|"]
for name, kind, sigs in groups():
    for period, part in periods(sigs):
        ok = [s for s in part if s.get("obs") and s.get("conf")]
        if not ok:
            continue
        conf = [s for s in ok if s["conf"].get("wait") is not None]
        none = [s for s in ok if s["conf"].get("wait") is None]
        conf_ids = {id(s) for s in conf}
        cval = lambda s: s["conf"]["obs"]["net60"] if id(s) in conf_ids else 0.0
        base, _ = by_day_events(kind, ok, lambda s: s["obs"]["net60"])
        alt_, _ = by_day_events(kind, ok, cval)
        dd, _ = by_day_events(kind, ok, lambda s: cval(s) - s["obs"]["net60"])
        m, lo, hi = ci95(dd)
        lines.append("| %s | %s | %d (%d) | %d (%s) | %s분, %s | %s / %s | %s [%s, %s] |" % (
            name, period, len(base), len(ok), len(none), pct(mean([s["obs"]["net60"] for s in none])),
            "-" if not conf else "%d" % st.median([s["conf"]["wait"] for s in conf]),
            "-" if not conf else pct(st.median([s["conf"]["late"] for s in conf])), pct(mean(base)), pct(mean(alt_)), pct(m), pct(lo), pct(hi)))
lines.append("")

# ---------------------------------------------------------------- 6·7·8번
alt_all = [s for s in S["alt"] if s.get("obs") and s["btc15"] is not None and s["btc1h"] is not None]
lines += ["## 5. 6·7·8번 — 반등 차이를 설명하는 참고 (60분 비용 뒤, 일 단위)", "", "| 분석 | 시나리오 | 구간 | 급락 일수 (신호) | 60분 비용 뒤 [95%] |", "|---|---|---|---|---|"]


def row(label, scen, part_label, kind, part):
    d, _ = by_day_events(kind, part, lambda s: s["obs"]["net60"])
    m, lo, hi = ci95(d)
    lines.append("| %s | %s | %s | %d (%d) | %s [%s, %s] |" % (label, scen, part_label, len(d), len(part), pct(m), pct(lo), pct(hi)))


for scen, sel in (("2. 동반 하락", lambda s: s["btc1h"] <= TH), ("4. BTC 조건 없음 ※낙관적", lambda s: s["btc1h"] > TH)):
    part = sorted([s for s in alt_all if sel(s)], key=lambda s: s["r15"] - s["btc15"])
    k = len(part) // 3
    for lab, chunk in (("BTC 보다 가장 더 빠짐", part[:k]), ("중간", part[k:2 * k]), ("덜 빠짐", part[2 * k:])):
        if chunk:
            row("6. 상대 낙폭", scen, "%s (%s ~ %s)" % (lab, pct(chunk[0]["r15"] - chunk[0]["btc15"], 1), pct(chunk[-1]["r15"] - chunk[-1]["btc15"], 1)), "alt", chunk)
    for lo_, hi_ in ((0, 1.5), (1.5, 3), (3, 6), (6, 1e9)):
        row("7. 거래대금 배수", scen, "%g배 이상" % lo_ if hi_ > 1e8 else "%g ~ %g배" % (lo_, hi_), "alt", [s for s in part if lo_ <= s["mult"] < hi_])
for name, kind, sigs in groups():
    ok = [s for s in sigs if s.get("obs")]
    row("8. BTC 24시간", name, "양수", kind, [s for s in ok if (s.get("btc24") or 0) > 0])
    row("8. BTC 24시간", name, "0 이하", kind, [s for s in ok if s.get("btc24") is not None and s["btc24"] <= 0])
lines.append("")

# 3번: 걸린 시간
lines += ["## 6. 3번 — BTC 신호부터 알트 신호까지 걸린 시간", "", "| 걸린 시간 | 급락 일수 (신호) | 60분 비용 뒤 | 같은 시각 BTC |", "|---|---|---|---|"]
for lag in (15, 30, 45, 60):
    part = [s for s in S["lead"] if s["lag"] == lag and s.get("obs")]
    d, _ = by_day_events("lead", part, lambda s: s["obs"]["net60"])
    b, _ = by_day_events("lead", part, lambda s: s["btc"]["net60"] if s.get("btc") else None)
    lines.append("| %d분 | %d (%d) | %s | %s |" % (lag, len(d), len(part), pct(mean(d)), pct(mean(b))))
lines.append("")

# ---------------------------------------------------------------- 최근 7일: 실제 체결 기록으로
recent = [(name, s) for name, kind, sigs in groups() for s in sigs if s["day"] >= RECENT and s.get("obs")]
lines += ["## 7. 최근 7일 신호 — 실제 체결 기록으로 다시 계산", ""]
if not recent:
    lines += ["최근 7일(%s 이후)에 신호가 없었습니다." % RECENT, ""]
else:
    spec = importlib.util.spec_from_file_location("fw", str(Path(__file__).resolve().parents[3] / "forward_watch.py"))
    FW = importlib.util.module_from_spec(spec)
    sys.argv = [sys.argv[0]]
    spec.loader.exec_module(FW)
    lines += ["| 시나리오 | 종목 | 신호 시각 (UTC) | 봉 기준 60분 비용 뒤 | 60초 추정 | 주문 크기(100만 원) 추정 |", "|---|---|---|---|---|---|"]
    seen = set()
    for name, s in recent:
        t = (s["q"] + 1) * 15
        key = (name, s["m"], t)
        if key in seen:
            continue
        seen.add(key)
        try:
            f = FW.fill_check(s["m"], t * 60000, (t + 60) * 60000, None, None)
            a = pct(f["net"]) if f.get("status") == "ok" else f.get("status")
            b = pct(f["order_net"]) if f.get("order_status") == "ok" else f.get("order_status")
        except Exception as e:
            a = b = "조회 실패 (%s)" % type(e).__name__
        lines.append("| %s | %s | %s | %s | %s | %s |" % (name, s["m"][4:], dt.datetime.fromtimestamp(t * 60, tz=dt.timezone.utc).strftime("%m-%d %H:%M"),
                                                       pct(s["obs"]["net60"]), a, b))
    lines.append("")

OUT.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
