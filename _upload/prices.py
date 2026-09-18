"""주가 데이터
0순위: KRX OpenAPI '유가증권/코스닥 일별매매정보' (공식, 인증키 필요) https://openapi.krx.co.kr
1순위: 공공데이터포털 '금융위원회_주식시세정보' (공식, 인증키 필요, 전 영업일 기준)
       https://www.data.go.kr/data/15094808/openapi.do
2순위: 네이버 금융 차트 (비공식, 키 불필요, 예고 없이 바뀔 수 있음)"""
import datetime as dt
import re
from urllib.parse import unquote

import pandas as pd
import requests

GOV_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
NAVER_URL = "https://fchart.stock.naver.com/sise.nhn"
KRX_URL = "https://data-dbg.krx.co.kr/svc/apis/sto/"
_krx_cache = {}


def _krx_day(day, krx_key):
    """해당일 전 종목 시세 (유가증권 + 코스닥). 휴장일이면 빈 표"""
    ck = day.strftime("%Y%m%d")
    if ck not in _krx_cache:
        rows = []
        for svc in ("stk_bydd_trd", "ksq_bydd_trd"):  # 유가증권, 코스닥
            try:
                r = requests.get(KRX_URL + svc, params={"basDd": ck}, headers={"AUTH_KEY": krx_key}, timeout=30)
                if r.status_code == 200:
                    rows += r.json().get("OutBlock_1", [])
            except Exception:
                pass
        _krx_cache[ck] = pd.DataFrame(rows)
    return _krx_cache[ck]


def krx_price_on(stock_code, date, krx_key):
    """KRX 공식 종가. date 또는 직전 거래일(최대 10일 전까지). (종가, 시가총액, 상장주식수) 또는 None"""
    for i in range(11):
        day = date - dt.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        df = _krx_day(day, krx_key)
        if df.empty:
            continue
        m = df[df["ISU_CD"] == stock_code]
        if not m.empty:
            x = m.iloc[0]
            return float(x["TDD_CLSPRC"]), float(x["MKTCAP"]), float(x["LIST_SHRS"]), x["BAS_DD"]
        return None  # 거래일인데 종목이 없음 (권한 없는 시장이거나 상장 전)
    return None


def _gov(stock_code, days, key):
    bgn = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y%m%d")
    r = requests.get(GOV_URL, params={"serviceKey": unquote(key), "resultType": "json", "numOfRows": 2000, "pageNo": 1,
                                      "likeSrtnCd": stock_code, "beginBasDt": bgn}, timeout=30)
    r.raise_for_status()
    items = r.json()["response"]["body"]["items"]
    items = items.get("item", []) if isinstance(items, dict) else []
    df = pd.DataFrame(items)
    if df.empty:
        return df
    df = df[df["srtnCd"] == stock_code]
    out = pd.DataFrame({
        "날짜": pd.to_datetime(df["basDt"]),
        "종가": pd.to_numeric(df["clpr"]),
        "거래량": pd.to_numeric(df["trqu"]),
        "시가총액": pd.to_numeric(df["mrktTotAmt"]),
        "상장주식수": pd.to_numeric(df["lstgStCnt"]),
    })
    return out.sort_values("날짜").reset_index(drop=True)


def _naver(stock_code, days):
    r = requests.get(NAVER_URL, params={"symbol": stock_code, "timeframe": "day",
                                        "count": int(days * 0.7) + 10, "requestType": 0},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    rows = re.findall(r'data="([^"]+)"', r.text)
    recs = []
    for s in rows:
        p = s.split("|")
        if len(p) >= 6:
            recs.append({"날짜": pd.to_datetime(p[0]), "종가": float(p[4]), "거래량": float(p[5])})
    return pd.DataFrame(recs)


def history(stock_code, days=365, gov_key=""):
    """(주가 표, 출처)"""
    if gov_key:
        try:
            df = _gov(stock_code, days, gov_key)
            if not df.empty:
                return df, "공공데이터포털(금융위원회)"
        except Exception:
            pass
    return _naver(stock_code, days), "네이버 금융(비공식)"


def price_on(stock_code, date, gov_key="", krx_key=""):
    """date(해당일 또는 직전 거래일) 종가. (종가, 시가총액, 상장주식수, 출처) — 못 구하면 (None, None, None, None)"""
    if krx_key:
        k = krx_price_on(stock_code, date, krx_key)
        if k:
            return k[0], k[1], k[2], "KRX"
    if gov_key:
        try:
            r = requests.get(GOV_URL, params={
                "serviceKey": unquote(gov_key), "resultType": "json", "numOfRows": 50, "pageNo": 1,
                "likeSrtnCd": stock_code,
                "beginBasDt": (date - dt.timedelta(days=14)).strftime("%Y%m%d"),
                "endBasDt": (date + dt.timedelta(days=1)).strftime("%Y%m%d")}, timeout=30)
            r.raise_for_status()
            items = r.json()["response"]["body"]["items"]
            items = items.get("item", []) if isinstance(items, dict) else []
            df = pd.DataFrame(items)
            if not df.empty:
                df = df[(df["srtnCd"] == stock_code) & (df["basDt"] <= date.strftime("%Y%m%d"))].sort_values("basDt")
            if not df.empty:
                x = df.iloc[-1]
                return float(x["clpr"]), float(x["mrktTotAmt"]), float(x["lstgStCnt"]), "공공데이터포털"
        except Exception:
            pass
    try:
        days = (dt.date.today() - date).days + 20
        h = _naver(stock_code, int(days / 0.7) + 5)
        h = h[h["날짜"] <= pd.Timestamp(date)]
        if not h.empty:
            return float(h["종가"].iloc[-1]), None, None, "네이버 금융"
    except Exception:
        pass
    return None, None, None, None


def valuation(price, shares, fin_row, dps=None, mcap=None, eps_disclosed=None):
    """가치평가 지표. price: 종가, shares: 보통주 발행주식수, fin_row: 최근 사업연도 재무 행
    eps_disclosed: 사업보고서에 공시된 기본주당이익(있으면 PER·EPS에 이 값을 씀 — KRX 방식)"""
    if price is None or not shares:
        return {}
    mcap = mcap or price * shares
    g = lambda k: None if fin_row is None or pd.isna(fin_row.get(k)) else fin_row.get(k)
    ni = g("지배주주순이익") if g("지배주주순이익") is not None else g("당기순이익")
    eq = g("지배주주자본") if g("지배주주자본") is not None else g("자본총계")
    sales, ebitda, netdebt = g("매출액"), g("EBITDA"), g("순차입금")
    eps = ni / shares if ni is not None else None
    if eps_disclosed is not None:
        eps = eps_disclosed
    bps = eq / shares if eq is not None else None
    ev = mcap + netdebt if netdebt is not None else None
    r2 = lambda v: None if v is None else round(v, 2)
    return {
        "주가(원)": price,
        "시가총액(억원)": round(mcap / 1e8),
        "EPS(원)": None if eps is None else round(eps),
        "BPS(원)": None if bps is None else round(bps),
        "PER(배)": r2(price / eps) if eps and eps > 0 else None,
        "PBR(배)": r2(price / bps) if bps and bps > 0 else None,
        "PSR(배)": r2(mcap / sales) if sales else None,
        "EV/EBITDA(배)": r2(ev / ebitda) if ev is not None and ebitda and ebitda > 0 else None,
        "주당배당금(원)": dps,
        "배당수익률(%)": r2(dps / price * 100) if dps else None,
    }
