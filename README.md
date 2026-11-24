# 절벽과 바닥

미국 증시(S&P 500)의 변곡점 신호를 두 묶음으로 따로 세는 주간 계기판입니다.

**페이지: https://woohyeonjo.github.io/cliff-bottom/**

- **절벽 지표 10개**: 고점에서 꺾이는 징후 (시장 폭, 경기 민감도, 신용, 금리곡선, 고용, 통화정책, 금융여건, 투자심리, 주택)
- **바닥 지표 10개**: 하락이 멈추는 징후 (낙폭, 변동성 정점 통과, 신용 개선, 완화, 실업 정점 통과, 반등 폭, 투자심리 극단)

지표마다 켜짐/꺼짐만 있고, 켜진 개수를 셉니다. 지금 어느 쪽 근거가 얼마나 쌓였는지 보여 주는 계기판이며, 예측 도구나 투자 조언이 아닙니다.

## 어떻게 동작하나

1. `fetch_data.py` — FRED(키 없는 CSV), Yahoo Finance, FINRA에서 16개 시계열을 받아 `data/raw.json` 으로 저장합니다.
2. `signals.py` — 금요일마다 그 주까지 **실제로 발표된 값만** 써서 20개 지표를 판정합니다.
   조건을 2주 연속 충족해야 켜지고, 2주 연속 충족하지 못해야 꺼집니다. 결과는 `data/signals.json` 입니다.
3. `index.html` — `data/signals.json` 을 읽어 요약, 2007년부터의 주별 차트, 지표 카드를 보여 줍니다.

GitHub Actions(`.github/workflows/update.yml`)가 월~토 매일 22:47 UTC(한국 시간 다음 날 07:47)에 1~2를 돌리고 GitHub Pages 로 배포합니다.
이번 주는 금요일 종가 전까지 잠정값이고, 매일 다시 판정합니다.

규칙, 임계값, 발표 지연 가정, 쓰지 않기로 한 지표와 이유는 [DESIGN.md](DESIGN.md)에 있습니다.
임계값은 실제 결과를 보기 전에 정했고, 결과를 보고 바꾸지 않습니다.

## 로컬에서 실행

파이썬 3.10 이상, 외부 의존성 없음.

```bash
python fetch_data.py          # data/raw.json
python signals.py --report    # data/signals.json, 지표별 켜진 기간 표 출력
python -m http.server 8020    # http://localhost:8020
```

`index.html` 을 파일로 바로 열면 브라우저가 `data/signals.json` 읽기를 막으므로 웹 서버로 열어야 합니다.

## 자료 출처

- [FRED](https://fred.stlouisfed.org/) (세인트루이스 연은): BAA10Y, T10Y3M, DFF, NFCI, ICSA, SAHMREALTIME, PERMIT, UMCSENT
- Yahoo Finance: ^GSPC, SPY, RSP, XLY, XLP, ^VIX, ^VIX3M
- [FINRA](https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics): 마진 통계 (고객 신용융자 잔고)
