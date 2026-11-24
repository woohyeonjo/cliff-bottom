"""data/raw.json 으로 절벽·바닥 지표 20개를 주마다 판정해 data/signals.json 으로 저장한다.

규칙과 임계값은 DESIGN.md 3장, 발표 지연은 4장을 따른다. 여기서 숫자를 바꾸면 DESIGN.md 도 함께 바꾼다.

- 판정 주: 금요일에 끝나는 주. 그 주 금요일까지 발표된 값만 쓴다 (발표 지연 반영).
- 마지막 주는 금요일 종가가 아직 없으면 잠정(provisional)이다.
- 조건 충족(raw)과 켜짐(on)은 다르다. 2주 연속 충족해야 켜지고, 2주 연속 미충족이어야 꺼진다.
- 히스토리는 2007년 첫 주부터 내보낸다. 2006년은 되돌아보기용으로만 쓴다.

    python signals.py                 # data/raw.json → data/signals.json
    python signals.py --report        # 지표별 켜진 기간을 함께 출력 (구현 확인용)
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
GRID_FROM = dt.date(2006, 1, 6)     # 되돌아보기용 시작 (금요일)
OUT_FROM = dt.date(2007, 1, 5)      # 내보내는 첫 주

# 관측 기준일에 더할 발표 지연 (일). DESIGN.md 4장.
LAG_DAYS = {"ICSA": 5, "NFCI": 5, "SAHM": 38, "UMCSENT": 30, "PERMIT": 48, "MARGIN": 52}

Series = list[tuple[dt.date, float]]
Opt = float | None

# 지표 정의 (화면 표시용 정보 포함). 판정 함수는 아래 judge() 에 있다.
INDICATORS: list[dict] = [
    {"id": "C1", "set": "cliff", "name": "소수 종목 장세", "cat": "시장 폭", "nature": "선행",
     "rule": "S&P 500이 52주 고점 대비 -5% 이내인데, 동일가중(RSP)/시가총액가중(SPY) 비율이 26주 전보다 낮음"},
    {"id": "C2", "set": "cliff", "name": "방어주로 이동", "cat": "경기 민감도", "nature": "선행",
     "rule": "경기소비재/필수소비재(XLY/XLP) 비율이 26주 전보다 낮음"},
    {"id": "C3", "set": "cliff", "name": "신용 스프레드 확대", "cat": "신용", "nature": "선행",
     "rule": "Baa 회사채와 10년 국채 금리차가 52주 최저치보다 0.5%p 이상 높음"},
    {"id": "C4", "set": "cliff", "name": "금리곡선 정상화", "cat": "금리곡선", "nature": "선행",
     "rule": "10년-3개월 금리차가 0 이상이고, 최근 52주 안에 0 미만이었던 적 있음"},
    {"id": "C5", "set": "cliff", "name": "삼 룰", "cat": "고용", "nature": "동행",
     "rule": "실업률 3개월 평균이 지난 12개월 최저치보다 0.5%p 이상 높음"},
    {"id": "C6", "set": "cliff", "name": "실업수당 청구 증가", "cat": "고용", "nature": "동행",
     "rule": "신규 실업수당 청구 4주 평균이 52주 최저치의 1.2배 이상"},
    {"id": "C7", "set": "cliff", "name": "긴축 진행", "cat": "통화정책", "nature": "선행",
     "rule": "연방기금금리가 26주 전보다 0.25%p 이상 높음"},
    {"id": "C8", "set": "cliff", "name": "금융여건 긴축", "cat": "금융여건", "nature": "동행",
     "rule": "시카고 연은 금융여건지수(NFCI)가 0 초과"},
    {"id": "C9", "set": "cliff", "name": "마진 부채 꺾임", "cat": "투자심리", "nature": "선행",
     "rule": "최근 12개월 안에 마진 부채가 전년 대비 +30% 이상 늘어난 적이 있고, 현재 6개월 최고치보다 10% 이상 줄어듦"},
    {"id": "C10", "set": "cliff", "name": "건축 허가 감소", "cat": "경기 선행", "nature": "선행",
     "rule": "신규 주택 건축 허가가 1년 전보다 10% 이상 감소"},
    {"id": "B1", "set": "bottom", "name": "약세장 진입", "cat": "낙폭", "nature": "상태",
     "rule": "S&P 500이 역대 고점 대비 -20% 이하"},
    {"id": "B2", "set": "bottom", "name": "VIX 정점 통과", "cat": "변동성", "nature": "전환",
     "rule": "최근 13주 VIX 최고치가 30 이상이고, 현재 25 미만"},
    {"id": "B3", "set": "bottom", "name": "기간구조 복귀", "cat": "변동성", "nature": "전환",
     "rule": "최근 13주 안에 VIX/VIX3M이 1을 넘었고, 현재 1 미만"},
    {"id": "B4", "set": "bottom", "name": "신용 스프레드 축소", "cat": "신용", "nature": "전환",
     "rule": "Baa 회사채와 10년 국채 금리차가 26주 최고치보다 0.5%p 이상 낮음"},
    {"id": "B5", "set": "bottom", "name": "완화 시작", "cat": "통화정책", "nature": "전환",
     "rule": "연방기금금리가 26주 전보다 0.25%p 이상 낮음"},
    {"id": "B6", "set": "bottom", "name": "실업수당 정점 통과", "cat": "고용", "nature": "전환",
     "rule": "최근 26주 안에 청구 4주 평균이 52주 최저치의 1.2배를 넘었고, 현재 26주 최고치보다 10% 이상 낮음"},
    {"id": "B7", "set": "bottom", "name": "광범위한 반등", "cat": "시장 폭", "nature": "전환",
     "rule": "동일가중 지수(RSP)가 13주 최저치보다 15% 이상 높음"},
    {"id": "B8", "set": "bottom", "name": "금융여건 개선", "cat": "금융여건", "nature": "전환",
     "rule": "NFCI가 26주 최고치보다 0.3 이상 낮음"},
    {"id": "B9", "set": "bottom", "name": "마진 부채 디레버리징", "cat": "투자심리", "nature": "상태",
     "rule": "마진 부채가 1년 전보다 20% 이상 감소"},
    {"id": "B10", "set": "bottom", "name": "소비자심리 저점 통과", "cat": "투자심리", "nature": "전환",
     "rule": "미시간대 소비자심리지수가 60 미만이고, 3개월 전보다 높음"},
]


# ---------- 읽기와 정렬 ----------

def load_raw(path: Path) -> tuple[dict[str, Series], dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    series = {k: [(dt.date.fromisoformat(d), v) for d, v in zip(e["d"], e["v"])] for k, e in raw["series"].items()}
    return series, raw


def fridays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def asof(series: Series, cutoffs: list[dt.date], lag: int = 0) -> list[Opt]:
    """각 기준일에 알 수 있었던 마지막 값 (관측일 + lag ≤ 기준일)."""
    avail = [d + dt.timedelta(days=lag) for d, _ in series]
    out: list[Opt] = []
    for c in cutoffs:
        i = bisect.bisect_right(avail, c) - 1
        out.append(series[i][1] if i >= 0 else None)
    return out


def asof_with_date(series: Series, cutoffs: list[dt.date], lag: int = 0) -> list[dt.date | None]:
    avail = [d + dt.timedelta(days=lag) for d, _ in series]
    out: list[dt.date | None] = []
    for c in cutoffs:
        i = bisect.bisect_right(avail, c) - 1
        out.append(series[i][0] if i >= 0 else None)
    return out


def rolling4(series: Series) -> Series:
    """주간 시계열의 4주 이동평균 (관측일 그대로)."""
    return [(series[i][0], sum(v for _, v in series[i - 3 : i + 1]) / 4) for i in range(3, len(series))]


# ---------- 창 함수 (None 이 섞이면 None) ----------

def window(xs: list[Opt], t: int, n: int) -> list[float] | None:
    if t - n + 1 < 0:
        return None
    w = xs[t - n + 1 : t + 1]
    return None if any(v is None for v in w) else w  # type: ignore[return-value]


def wmin(xs: list[Opt], t: int, n: int) -> Opt:
    w = window(xs, t, n)
    return None if w is None else min(w)


def wmax(xs: list[Opt], t: int, n: int) -> Opt:
    w = window(xs, t, n)
    return None if w is None else max(w)


def back(xs: list[Opt], t: int, n: int) -> Opt:
    return xs[t - n] if t - n >= 0 else None


def ratio(a: list[Opt], b: list[Opt]) -> list[Opt]:
    return [None if x is None or y is None or y == 0 else x / y for x, y in zip(a, b)]


def all_known(*vals: Opt) -> bool:
    return all(v is not None for v in vals)


# ---------- 월간 지표 (관측월 단위로 먼저 계산) ----------

def monthly_flags(series: Series, fn) -> Series:
    """월간 시계열의 각 달에 fn(values, i) 를 적용한 결과 (1.0/0.0, 판정 불가면 제외)."""
    vals = [v for _, v in series]
    out: Series = []
    for i, (d, _) in enumerate(series):
        r = fn(vals, i)
        if r is not None:
            out.append((d, 1.0 if r else 0.0))
    return out


def margin_turn(v: list[float], i: int) -> bool | None:
    """C9: 최근 12개월 안에 전년 대비 +30% 이상, 현재 6개월 최고치보다 10% 이상 감소."""
    if i < 23:
        return None
    yoys = [v[j] / v[j - 12] - 1 for j in range(i - 11, i + 1)]
    return max(yoys) >= 0.30 and v[i] <= 0.9 * max(v[i - 5 : i + 1])


def margin_delever(v: list[float], i: int) -> bool | None:
    """B9: 1년 전보다 20% 이상 감소."""
    return None if i < 12 else v[i] / v[i - 12] - 1 <= -0.20


def permit_drop(v: list[float], i: int) -> bool | None:
    """C10: 1년 전보다 10% 이상 감소."""
    return None if i < 12 else v[i] / v[i - 12] - 1 <= -0.10


def sentiment_turn(v: list[float], i: int) -> bool | None:
    """B10: 60 미만이고 3개월 전보다 높음."""
    return None if i < 3 else v[i] < 60 and v[i] > v[i - 3]


# ---------- 판정 ----------

def judge(s: dict[str, Series], weeks: list[dt.date]) -> tuple[dict[str, list[bool | None]], dict]:
    """주마다 지표별 조건 충족 여부(raw). None 은 판정에 필요한 값이 없음."""
    A = lambda k, lag=0: asof(s[k], weeks, lag)  # noqa: E731
    spx, spy, rsp, xly, xlp = A("SPX"), A("SPY"), A("RSP"), A("XLY"), A("XLP")
    vix, vix3m = A("VIX"), A("VIX3M")
    baa, curve, dff = A("BAA10Y"), A("T10Y3M"), A("DFF")
    nfci = A("NFCI", LAG_DAYS["NFCI"])
    claims4 = asof(rolling4(s["ICSA"]), weeks, LAG_DAYS["ICSA"])
    sahm = A("SAHM", LAG_DAYS["SAHM"])
    umc = A("UMCSENT", LAG_DAYS["UMCSENT"])
    c9 = asof(monthly_flags(s["MARGIN"], margin_turn), weeks, LAG_DAYS["MARGIN"])
    b9 = asof(monthly_flags(s["MARGIN"], margin_delever), weeks, LAG_DAYS["MARGIN"])
    c10 = asof(monthly_flags(s["PERMIT"], permit_drop), weeks, LAG_DAYS["PERMIT"])
    b10 = asof(monthly_flags(s["UMCSENT"], sentiment_turn), weeks, LAG_DAYS["UMCSENT"])

    breadth, cyc, vterm = ratio(rsp, spy), ratio(xly, xlp), ratio(vix, vix3m)
    ath: list[Opt] = []
    hi = None
    for v in spx:
        hi = v if hi is None or (v is not None and v > hi) else hi
        ath.append(hi)

    n = len(weeks)
    raw: dict[str, list[bool | None]] = {ind["id"]: [None] * n for ind in INDICATORS}
    c6_raw: list[bool | None] = [None] * n
    for t in range(n):
        def put(key: str, cond, *need: Opt) -> None:
            raw[key][t] = bool(cond()) if all_known(*need) else None

        hi52, r26 = wmax(spx, t, 52), back(breadth, t, 26)
        put("C1", lambda: spx[t] / hi52 - 1 >= -0.05 and breadth[t] < r26, spx[t], hi52, breadth[t], r26)
        c26 = back(cyc, t, 26)
        put("C2", lambda: cyc[t] < c26, cyc[t], c26)
        bmin52 = wmin(baa, t, 52)
        put("C3", lambda: baa[t] >= bmin52 + 0.5, baa[t], bmin52)
        cmin52 = wmin(curve, t, 52)
        put("C4", lambda: curve[t] >= 0 and cmin52 < 0, curve[t], cmin52)
        put("C5", lambda: sahm[t] >= 0.5, sahm[t])
        lmin52 = wmin(claims4, t, 52)
        put("C6", lambda: claims4[t] >= 1.2 * lmin52, claims4[t], lmin52)
        c6_raw[t] = raw["C6"][t]
        f26 = back(dff, t, 26)
        put("C7", lambda: dff[t] - f26 >= 0.25 - 1e-9, dff[t], f26)
        put("C8", lambda: nfci[t] > 0, nfci[t])
        put("C9", lambda: c9[t] == 1.0, c9[t])
        put("C10", lambda: c10[t] == 1.0, c10[t])

        put("B1", lambda: spx[t] / ath[t] - 1 <= -0.20, spx[t], ath[t])
        vmax13 = wmax(vix, t, 13)
        put("B2", lambda: vmax13 >= 30 and vix[t] < 25, vix[t], vmax13)
        tmax13 = wmax(vterm, t, 13)
        put("B3", lambda: tmax13 > 1 and vterm[t] < 1, vterm[t], tmax13)
        bmax26 = wmax(baa, t, 26)
        put("B4", lambda: baa[t] <= bmax26 - 0.5, baa[t], bmax26)
        put("B5", lambda: dff[t] - f26 <= -0.25 + 1e-9, dff[t], f26)
        lmax26 = wmax(claims4, t, 26)
        spike = None if t < 25 or any(v is None for v in c6_raw[t - 25 : t + 1]) else any(c6_raw[t - 25 : t + 1])
        put("B6", lambda: spike and claims4[t] <= 0.9 * lmax26, claims4[t], lmax26, spike)
        rmin13 = wmin(rsp, t, 13)
        put("B7", lambda: rsp[t] >= 1.15 * rmin13, rsp[t], rmin13)
        nmax26 = wmax(nfci, t, 26)
        put("B8", lambda: nfci[t] <= nmax26 - 0.3, nfci[t], nmax26)
        put("B9", lambda: b9[t] == 1.0, b9[t])
        put("B10", lambda: b10[t] == 1.0, b10[t])

    # 마지막 주 카드에 보여 줄 값
    t = n - 1
    def r(x: Opt, nd: int = 4) -> Opt:
        return None if x is None else round(x, nd)
    def chg(a: Opt, b: Opt) -> Opt:
        return None if a is None or b is None else a / b - 1
    def month_of(key: str) -> str | None:
        d = asof_with_date(s[key], [weeks[t]], LAG_DAYS[key])[0]
        return None if d is None else d.strftime("%Y-%m")
    mv = [v for _, v in s["MARGIN"]]
    pv = [v for _, v in s["PERMIT"]]
    uv = [v for _, v in s["UMCSENT"]]
    mi = len([d for d, _ in s["MARGIN"] if d + dt.timedelta(days=LAG_DAYS["MARGIN"]) <= weeks[t]]) - 1
    pi = len([d for d, _ in s["PERMIT"] if d + dt.timedelta(days=LAG_DAYS["PERMIT"]) <= weeks[t]]) - 1
    ui = len([d for d, _ in s["UMCSENT"] if d + dt.timedelta(days=LAG_DAYS["UMCSENT"]) <= weeks[t]]) - 1
    now = {
        "C1": {"dd52": r(chg(spx[t], wmax(spx, t, 52))), "breadth_chg26": r(chg(breadth[t], back(breadth, t, 26)))},
        "C2": {"chg26": r(chg(cyc[t], back(cyc, t, 26)))},
        "C3": {"baa": r(baa[t], 2), "min52": r(wmin(baa, t, 52), 2)},
        "C4": {"curve": r(curve[t], 2), "min52": r(wmin(curve, t, 52), 2)},
        "C5": {"sahm": r(sahm[t], 2), "month": month_of("SAHM")},
        "C6": {"claims4": r(claims4[t], 0), "ratio_to_min52": r(None if claims4[t] is None or not wmin(claims4, t, 52) else claims4[t] / wmin(claims4, t, 52), 3)},
        "C7": {"dff": r(dff[t], 2), "dff_26w_ago": r(back(dff, t, 26), 2)},
        "C8": {"nfci": r(nfci[t], 3)},
        "C9": {"chg_from_6m_high": r(mv[mi] / max(mv[mi - 5 : mi + 1]) - 1) if mi >= 5 else None,
               "max_yoy_12m": r(max(mv[j] / mv[j - 12] - 1 for j in range(mi - 11, mi + 1))) if mi >= 23 else None,
               "month": month_of("MARGIN")},
        "C10": {"yoy": r(pv[pi] / pv[pi - 12] - 1) if pi >= 12 else None, "month": month_of("PERMIT")},
        "B1": {"dd_ath": r(chg(spx[t], ath[t]))},
        "B2": {"vix": r(vix[t], 2), "max13": r(wmax(vix, t, 13), 2)},
        "B3": {"ratio": r(vterm[t], 3), "max13": r(wmax(vterm, t, 13), 3)},
        "B4": {"baa": r(baa[t], 2), "max26": r(wmax(baa, t, 26), 2)},
        "B5": {"dff": r(dff[t], 2), "dff_26w_ago": r(back(dff, t, 26), 2)},
        "B6": {"claims4": r(claims4[t], 0), "max26": r(wmax(claims4, t, 26), 0)},
        "B7": {"from_13w_low": r(chg(rsp[t], wmin(rsp, t, 13)))},
        "B8": {"nfci": r(nfci[t], 3), "max26": r(wmax(nfci, t, 26), 3)},
        "B9": {"yoy": r(mv[mi] / mv[mi - 12] - 1) if mi >= 12 else None, "month": month_of("MARGIN")},
        "B10": {"umcsent": r(uv[ui], 1), "three_months_ago": r(uv[ui - 3], 1) if ui >= 3 else None, "month": month_of("UMCSENT")},
    }
    extra = {"spx": spx, "ath": ath, "now": now}
    return raw, extra


def hysteresis(raw: list[bool | None]) -> list[bool]:
    """2주 연속 충족해야 켜지고, 2주 연속 미충족이어야 꺼진다. 판정 불가(None)는 미충족으로 본다."""
    out, state = [], False
    for t, v in enumerate(raw):
        cur, prev = bool(v), bool(raw[t - 1]) if t > 0 else False
        if not state and cur and prev:
            state = True
        elif state and not cur and not prev:
            state = False
        out.append(state)
    return out


# ---------- 내보내기 ----------

def bits(xs: list) -> str:
    return "".join("-" if v is None else "1" if v else "0" for v in xs)


def runs(on: list[bool], weeks: list[dt.date]) -> list[tuple[str, str]]:
    out, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        if start is not None and (not v or i == len(on) - 1):
            end = i if v else i - 1
            out.append((weeks[start].isoformat(), weeks[end].isoformat()))
            start = None
    return out


def build(series: dict[str, Series], raw_meta: dict) -> dict:
    last_obs = series["SPX"][-1][0]
    last_friday = last_obs + dt.timedelta(days=(4 - last_obs.weekday()) % 7)
    weeks = fridays(GRID_FROM, last_friday)
    provisional = last_obs < last_friday
    raw, extra = judge(series, weeks)
    k0 = weeks.index(OUT_FROM)
    out_weeks = weeks[k0:]
    on = {k: hysteresis(v) for k, v in raw.items()}
    cliff = [i["id"] for i in INDICATORS if i["set"] == "cliff"]
    bottom = [i["id"] for i in INDICATORS if i["set"] == "bottom"]
    spx, ath = extra["spx"], extra["ath"]
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "raw_generated_at": raw_meta.get("generated_at"),
        "data_as_of": last_obs.isoformat(),
        "provisional": provisional,
        "fetch_errors": raw_meta.get("errors", {}),
        "note": "weeks 는 금요일 날짜. on/raw 는 주마다 한 글자 (1=켜짐·충족, 0=꺼짐·미충족, -=판정 불가). "
                "on 은 raw 에 2주 연속 규칙을 적용한 결과. 마지막 주가 provisional 이면 금요일 종가 전의 잠정값.",
        "indicators": INDICATORS,
        "weeks": [w.isoformat() for w in out_weeks],
        "spx": [None if v is None else round(v, 2) for v in spx[k0:]],
        "dd": [None if v is None or a is None else round(v / a - 1, 4) for v, a in zip(spx[k0:], ath[k0:])],
        "cliff": [sum(on[k][t] for k in cliff) for t in range(k0, len(weeks))],
        "bottom": [sum(on[k][t] for k in bottom) for t in range(k0, len(weeks))],
        "on": {k: bits(v[k0:]) for k, v in on.items()},
        "raw": {k: bits(v[k0:]) for k, v in raw.items()},
        "now": extra["now"],
    }


def dump(data: dict) -> str:
    """긴 배열은 한 줄로, 나머지는 읽기 쉽게."""
    lines = ["{"]
    items = list(data.items())
    for i, (k, v) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        if isinstance(v, dict) and k in ("on", "raw", "now"):
            inner = [f"  {json.dumps(ik)}: {json.dumps(iv, ensure_ascii=False, separators=(',', ':'))}" for ik, iv in v.items()]
            lines.append(f" {json.dumps(k)}: {{\n" + ",\n".join(inner) + f"\n }}{comma}")
        elif k == "indicators":
            inner = [f"  {json.dumps(x, ensure_ascii=False, separators=(',', ':'))}" for x in v]
            lines.append(f" {json.dumps(k)}: [\n" + ",\n".join(inner) + f"\n ]{comma}")
        else:
            lines.append(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def report(data: dict) -> str:
    weeks = [dt.date.fromisoformat(w) for w in data["weeks"]]
    rows = [f"기준 {data['data_as_of']} ({'잠정' if data['provisional'] else '확정'}), "
            f"절벽 {data['cliff'][-1]}/10, 바닥 {data['bottom'][-1]}/10", ""]
    rows.append("| id | 이름 | 지금 | 켜진 주 | 판정 불가 주 | 켜진 기간 |")
    rows.append("|---|---|---|---|---|---|")
    for ind in data["indicators"]:
        on = [c == "1" for c in data["on"][ind["id"]]]
        unknown = data["raw"][ind["id"]].count("-")
        spans = runs(on, weeks)
        txt = ", ".join(f"{a[:7]}~{b[:7]}" if a[:7] != b[:7] else a[:7] for a, b in spans)
        rows.append(f"| {ind['id']} | {ind['name']} | {'켜짐' if on[-1] else '꺼짐'} | {sum(on)} | {unknown} | {txt} |")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(HERE / "data" / "raw.json"))
    ap.add_argument("--out", default=str(HERE / "data" / "signals.json"))
    ap.add_argument("--report", action="store_true", help="지표별 켜진 기간 표 출력")
    args = ap.parse_args()

    series, meta = load_raw(Path(args.raw))
    data = build(series, meta)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump(data), encoding="utf-8")
    print(f"저장: {out}  (기준 {data['data_as_of']}, 절벽 {data['cliff'][-1]}, 바닥 {data['bottom'][-1]})", file=sys.stderr)
    if args.report:
        print(report(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
