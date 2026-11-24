"""절벽과 바닥 지표에 쓸 원자료를 받아 data/raw.json 으로 저장한다.

- FRED: 키 없이 CSV 다운로드 주소(fredgraph.csv)를 쓴다.
- Yahoo Finance: 일봉 차트 API. ETF 는 배당을 반영한 수정 종가, 지수는 종가.
- FINRA: 월간 마진 부채 엑셀 파일.

일간 시계열은 주 단위(금요일에 끝나는 주의 마지막 관측치)로 줄여 저장한다.
이번 주처럼 아직 금요일이 오지 않은 주도 그 주의 최신 관측치가 들어간다 (잠정 판정용).
주간·월간 시계열은 발표된 그대로(관측 기준일) 저장한다. 발표 지연은 신호 계산 단계에서 반영한다.

받기에 실패한 시계열은 이전 raw.json 의 값을 그대로 쓰고 errors 에 적는다.
하나라도 실패하면 종료 코드 2 로 끝난다 (Actions 에서 실패로 보이게).

표준 라이브러리만 사용한다.
    python fetch_data.py
    python fetch_data.py --out data/raw.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (cliff-bottom)"}
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1d"
FINRA_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"

# 52주·12개월 되돌아보기가 2007년 첫 주부터 가능하도록 2006년부터 받는다.
DAILY_FROM = dt.date(2006, 1, 1)
# 전년 대비, 12개월 안의 급증 여부 등을 보려면 월간은 2년 더 앞에서부터 필요하다.
SLOW_FROM = dt.date(2004, 1, 1)

EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)

Series = list[tuple[dt.date, float]]

# id, 이름, 출처 종류, 출처 기호, 주기, 단위
#   주기: daily(→주 단위로 줄여 저장) / weekly / monthly
SOURCES: list[dict] = [
    # 가격 (Yahoo)
    {"id": "SPX", "name": "S&P 500 지수 종가", "kind": "yahoo", "symbol": "^GSPC", "field": "close", "freq": "daily", "unit": "pt"},
    {"id": "SPY", "name": "SPY 수정 종가 (시가총액가중)", "kind": "yahoo", "symbol": "SPY", "field": "adjclose", "freq": "daily", "unit": "$"},
    {"id": "RSP", "name": "RSP 수정 종가 (동일가중)", "kind": "yahoo", "symbol": "RSP", "field": "adjclose", "freq": "daily", "unit": "$"},
    {"id": "XLY", "name": "XLY 수정 종가 (경기소비재)", "kind": "yahoo", "symbol": "XLY", "field": "adjclose", "freq": "daily", "unit": "$"},
    {"id": "XLP", "name": "XLP 수정 종가 (필수소비재)", "kind": "yahoo", "symbol": "XLP", "field": "adjclose", "freq": "daily", "unit": "$"},
    {"id": "VIX", "name": "VIX (1개월 변동성 예상치)", "kind": "yahoo", "symbol": "^VIX", "field": "close", "freq": "daily", "unit": "pt"},
    {"id": "VIX3M", "name": "VIX3M (3개월 변동성 예상치)", "kind": "yahoo", "symbol": "^VIX3M", "field": "close", "freq": "daily", "unit": "pt"},
    # 금리·신용·금융여건 (FRED)
    {"id": "BAA10Y", "name": "Baa 회사채 - 10년 국채 금리차", "kind": "fred", "symbol": "BAA10Y", "freq": "daily", "unit": "%p"},
    {"id": "T10Y3M", "name": "10년 - 3개월 국채 금리차", "kind": "fred", "symbol": "T10Y3M", "freq": "daily", "unit": "%p"},
    {"id": "DFF", "name": "연방기금 실효금리", "kind": "fred", "symbol": "DFF", "freq": "daily", "unit": "%"},
    {"id": "NFCI", "name": "시카고 연은 금융여건지수", "kind": "fred", "symbol": "NFCI", "freq": "weekly", "unit": "지수"},
    # 경기·고용 (FRED)
    {"id": "ICSA", "name": "신규 실업수당 청구 (주간, 계절조정)", "kind": "fred", "symbol": "ICSA", "freq": "weekly", "unit": "건"},
    {"id": "SAHM", "name": "삼 룰 실시간 지표", "kind": "fred", "symbol": "SAHMREALTIME", "freq": "monthly", "unit": "%p"},
    {"id": "PERMIT", "name": "신규 주택 건축 허가 (연율, 천 호)", "kind": "fred", "symbol": "PERMIT", "freq": "monthly", "unit": "천 호"},
    # 투자심리
    {"id": "UMCSENT", "name": "미시간대 소비자심리지수", "kind": "fred", "symbol": "UMCSENT", "freq": "monthly", "unit": "지수"},
    {"id": "MARGIN", "name": "FINRA 마진 부채 (고객 신용융자 잔고)", "kind": "finra", "symbol": "margin-statistics.xlsx", "freq": "monthly", "unit": "백만 $"},
]

# 마지막 관측치가 이보다 오래되면 경고 (일). 월간은 발표가 한 달 넘게 늦는다.
STALE_DAYS = {"daily": 10, "weekly": 21, "monthly": 80}


# ---------- 받기 ----------

def http_get(url: str, tries: int = 3) -> bytes:
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001 — 네트워크 오류는 잠깐 쉬고 다시
            last = e
            if i < tries - 1:
                time.sleep(2 * (i + 1))
    assert last is not None
    raise last


def fred(symbol: str) -> Series:
    rows = csv.reader(io.StringIO(http_get(FRED_CSV.format(symbol)).decode("utf-8")))
    next(rows)  # 머리글
    out: Series = []
    for row in rows:
        if len(row) < 2 or row[1] in ("", "."):
            continue
        out.append((dt.date.fromisoformat(row[0]), float(row[1])))
    return out


def yahoo(symbol: str, field: str) -> Series:
    p1 = int(dt.datetime.combine(DAILY_FROM, dt.time(), dt.timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    res = json.loads(http_get(YAHOO_CHART.format(urllib.parse.quote(symbol), p1, p2)))["chart"]["result"][0]
    if field == "adjclose":
        vals = res["indicators"]["adjclose"][0]["adjclose"]
    else:
        vals = res["indicators"]["quote"][0]["close"]
    out: Series = []
    for ts, v in zip(res["timestamp"], vals):
        if v is None:  # 장중이거나 빈 날
            continue
        out.append(((EPOCH + dt.timedelta(seconds=ts)).date(), float(v)))
    return out


def finra_margin() -> Series:
    """FINRA 엑셀의 'Debit Balances in Customers' Securities Margin Accounts' (월, 백만 달러)."""
    z = zipfile.ZipFile(io.BytesIO(http_get(FINRA_XLSX)))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
    out: Series = []
    for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).findall(".//m:sheetData/m:row", ns):
        vals = []
        for c in row.findall("m:c", ns):
            v = c.find("m:v", ns)
            vals.append(None if v is None else shared[int(v.text)] if c.get("t") == "s" else v.text)
        ym = vals[0] if vals else None
        if ym and len(ym) == 7 and ym[4] == "-" and len(vals) > 1 and vals[1]:
            out.append((dt.date(int(ym[:4]), int(ym[5:]), 1), float(vals[1])))
    out.sort()
    return out


def fetch(src: dict) -> Series:
    if src["kind"] == "fred":
        return fred(src["symbol"])
    if src["kind"] == "yahoo":
        return yahoo(src["symbol"], src["field"])
    if src["kind"] == "finra":
        return finra_margin()
    raise ValueError(src["kind"])


# ---------- 다듬기 ----------

def week_friday(d: dt.date) -> dt.date:
    """d 가 속한 주(토~금)의 금요일."""
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)


def to_weekly(series: Series) -> Series:
    """주마다 마지막 관측치 하나만 남긴다 (날짜는 실제 관측일)."""
    last: dict[dt.date, tuple[dt.date, float]] = {}
    for d, v in series:
        last[week_friday(d)] = (d, v)
    return [last[k] for k in sorted(last)]


def shape(src: dict, series: Series) -> Series:
    start = DAILY_FROM if src["freq"] == "daily" else SLOW_FROM
    series = sorted(p for p in series if p[0] >= start)
    return to_weekly(series) if src["freq"] == "daily" else series


def encode(src: dict, series: Series) -> dict:
    nd = 6 if src["unit"] in ("%", "%p", "지수") else 4
    return {
        "name": src["name"],
        "source": f"{src['kind'].upper()} {src['symbol']}",
        "freq": src["freq"],
        "stored": "weekly_last" if src["freq"] == "daily" else src["freq"],
        "unit": src["unit"],
        "d": [d.isoformat() for d, _ in series],
        "v": [round(v, nd) for _, v in series],
    }


def decode(entry: dict) -> Series:
    return [(dt.date.fromisoformat(d), v) for d, v in zip(entry["d"], entry["v"])]


# ---------- 쓰기 ----------

def dump(data: dict) -> str:
    """시계열 하나를 한 줄로 써서 변경 내역을 읽기 쉽게 한다."""
    head = {k: v for k, v in data.items() if k != "series"}
    lines = ["{"]
    for k, v in head.items():
        lines.append(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},")
    lines.append(' "series": {')
    items = list(data["series"].items())
    for i, (k, v) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}{comma}")
    lines.append(" }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def summary_table(data: dict, today: dt.date) -> str:
    rows = ["| id | 출처 | 주기 | 개수 | 처음 | 마지막 | 최신값 | 상태 |", "|---|---|---|---|---|---|---|---|"]
    for sid, e in data["series"].items():
        state = "실패 (이전 값 사용)" if sid in data["errors"] else "오래됨" if sid in data["warnings"] else "정상"
        first, last = (e["d"][0], e["d"][-1]) if e["d"] else ("-", "-")
        val = e["v"][-1] if e["v"] else "-"
        rows.append(f"| {sid} | {e['source']} | {e['freq']} | {len(e['d'])} | {first} | {last} | {val} | {state} |")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "data" / "raw.json"))
    args = ap.parse_args()
    out = Path(args.out)

    old: dict = {}
    if out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8")).get("series", {})
        except (json.JSONDecodeError, OSError):
            old = {}

    today = dt.datetime.now(dt.timezone.utc).date()
    data: dict = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "note": "일간 시계열은 주마다 마지막 관측치만 저장. 날짜는 실제 관측일(월간은 해당 월 1일).",
        "errors": {},
        "warnings": {},
        "series": {},
    }

    for src in SOURCES:
        sid = src["id"]
        print(f"{sid:8s} {src['kind']} {src['symbol']} ...", file=sys.stderr)
        try:
            series = shape(src, fetch(src))
            if not series:
                raise ValueError("빈 시계열")
        except Exception as e:  # noqa: BLE001 — 하나가 실패해도 나머지는 계속
            data["errors"][sid] = f"{type(e).__name__}: {e}"
            print(f"  실패: {e}", file=sys.stderr)
            if sid in old:
                series = decode(old[sid])
            else:
                series = []
        data["series"][sid] = encode(src, series)
        if series and (today - series[-1][0]).days > STALE_DAYS[src["freq"]]:
            data["warnings"][sid] = f"마지막 관측 {series[-1][0]} ({(today - series[-1][0]).days}일 전)"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump(data), encoding="utf-8")

    table = summary_table(data, today)
    print(f"\n저장: {out}\n\n{table}", file=sys.stderr)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"## cliff_bottom 원자료 ({data['generated_at']})\n\n{table}\n")
            for kind in ("errors", "warnings"):
                for sid, msg in data[kind].items():
                    f.write(f"\n- {kind} `{sid}`: {msg}")
            f.write("\n")
    return 2 if data["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
