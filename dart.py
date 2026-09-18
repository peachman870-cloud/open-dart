"""OpenDART API 호출 모듈 (공식 안내: https://opendart.fss.or.kr/guide/main.do)
재무는 '단일회사 전체 재무제표(fnlttSinglAcntAll)' 기준."""
import io
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd
import requests

BASE = "https://opendart.fss.or.kr/api"
VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
REPORT_CODES = {"사업보고서": "11011", "반기보고서": "11012", "1분기보고서": "11013", "3분기보고서": "11014"}


class DartError(Exception):
    pass


import threading
import time

SESSION = requests.Session()  # 연결 재사용 (속도 향상)
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16))
_api_cache, _api_lock = {}, threading.Lock()
_down_until = [0.0]
API_TTL = 6 * 3600  # 같은 조회는 6시간 동안 다시 부르지 않음


def _get(key, path, **params):
    ck = (path, tuple(sorted(params.items())))
    hit = _api_cache.get(ck)
    ttl = 600 if path == "list.json" else API_TTL  # 공시 목록은 10분 (미리 받은 자료는 load_cache 참고)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    if time.time() < _down_until[0]:  # 최근에 서버 연결이 안 됐으면 기다리지 않고 바로 포기
        raise DartError("DART 서버에 연결할 수 없어요 (잠시 후 다시 시도)")
    params["crtfc_key"] = key
    try:
        r = SESSION.get(f"{BASE}/{path}", params=params, timeout=(5, 20))
        r.raise_for_status()
    except requests.RequestException as e:  # 오류 문구에 인증키가 담긴 주소가 보이지 않게 함
        if isinstance(e, (requests.ConnectionError, requests.Timeout)):
            _down_until[0] = time.time() + 300
        raise DartError("DART 서버에 연결할 수 없어요 (시간 초과)") from None
    d = r.json()
    status = d.get("status")
    if status == "013":  # 조회된 데이터 없음
        d = None
    elif status != "000":
        raise DartError(f"[{status}] {d.get('message')}")
    with _api_lock:
        _api_cache[ck] = (time.time(), d)
    return d


# ---------------------------------------------------------------- 미리 받아둔 자료 (prefetch.py)
_FS_COLS = ("sj_div", "account_id", "account_nm", "thstrm_amount", "thstrm_add_amount",
            "frmtrm_amount", "frmtrm_add_amount")


def _slim(path, d):
    """저장 용량 줄이기: 재무제표는 계산에 쓰는 표·칸만 남김 (자본변동표 등 제외)"""
    if path != "fnlttSinglAcntAll.json" or not d:
        return d
    rows = [{c: r.get(c) for c in _FS_COLS if c in r} for r in d.get("list", [])
            if r.get("sj_div") in ("BS", "IS", "CIS", "CF")]
    return {"status": d.get("status"), "list": rows}


def dump_cache(file):
    """지금까지 받은 DART 조회 결과를 파일로 저장 (공시 목록은 제외)"""
    import gzip, json
    items = sorted([[p, [list(kv) for kv in prm], _slim(p, v[1])]
                    for (p, prm), v in _api_cache.items() if p != "list.json"], key=lambda x: json.dumps(x[:2]))
    raw = json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with open(file, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as g:  # 내용이 같으면 파일도 같게
        g.write(raw)
    return len(items)


def load_cache(file, valid_hours=36):
    """저장해 둔 조회 결과를 불러옴. valid_hours 동안은 다시 조회하지 않음"""
    import gzip, json
    try:
        with gzip.open(file, "rb") as g:
            items = json.loads(g.read().decode("utf-8"))
    except Exception:
        return 0
    ts = time.time() - API_TTL + valid_hours * 3600
    with _api_lock:
        for p, prm, d in items:
            _api_cache[(p, tuple(tuple(kv) for kv in prm))] = (ts, d)
    return len(items)


def load_corp_codes(key):
    """상장회사 목록 (회사명, 고유번호, 종목코드)"""
    r = SESSION.get(f"{BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=60)
    r.raise_for_status()
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        raise DartError(r.text[:300])
    root = ET.fromstring(z.read(z.namelist()[0]))
    rows = [{c.tag: (c.text or "").strip() for c in item} for item in root.iter("list")]
    df = pd.DataFrame(rows)
    df = df[df["stock_code"] != ""]
    return df[["corp_code", "corp_name", "stock_code"]].reset_index(drop=True)


def company(key, corp_code):
    return _get(key, "company.json", corp_code=corp_code) or {}


def _num(s):
    s = str(s if s is not None else "").replace(",", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- 계정 찾기 규칙
# (재무제표 구분, 표준계정ID 목록, 계정명 앞부분 목록)
IS = ("IS", "CIS")
BS = ("BS",)
CF = ("CF",)
ITEMS = {
    "매출액": (IS, ["ifrs-full_Revenue"], ["매출액", "수익(매출액)", "영업수익", "매출"]),
    "매출원가": (IS, ["ifrs-full_CostOfSales"], ["매출원가"]),
    "매출총이익": (IS, ["ifrs-full_GrossProfit"], ["매출총이익"]),
    "영업이익": (IS, ["dart_OperatingIncomeLoss"], ["영업이익"]),
    "금융비용": (IS, ["ifrs-full_FinanceCosts"], ["금융비용", "금융원가", "이자비용"]),
    "당기순이익": (IS, ["ifrs-full_ProfitLoss"], ["당기순이익", "분기순이익", "반기순이익"]),
    "지배주주순이익": (IS, ["ifrs-full_ProfitLossAttributableToOwnersOfParent"],
                 ["지배기업의소유주에게귀속되는당기순이익", "지배기업소유주지분", "지배기업의소유주지분", "지배기업소유주"]),
    "자산총계": (BS, ["ifrs-full_Assets"], ["자산총계"]),
    "유동자산": (BS, ["ifrs-full_CurrentAssets"], ["유동자산"]),
    "현금성자산": (BS, ["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산"]),
    "재고자산": (BS, ["ifrs-full_Inventories"], ["재고자산"]),
    "매출채권": (BS, ["dart_ShortTermTradeReceivable", "ifrs-full_CurrentTradeReceivables",
                  "ifrs-full_TradeAndOtherCurrentReceivables"], ["매출채권", "단기매출채권"]),
    "부채총계": (BS, ["ifrs-full_Liabilities"], ["부채총계"]),
    "유동부채": (BS, ["ifrs-full_CurrentLiabilities"], ["유동부채"]),
    "자본총계": (BS, ["ifrs-full_Equity"], ["자본총계"]),
    "지배주주자본": (BS, ["ifrs-full_EquityAttributableToOwnersOfParent"],
                ["지배기업의소유주에게귀속되는자본", "지배기업소유주지분", "지배기업의소유주지분"]),
    "영업활동현금흐름": (CF, ["ifrs-full_CashFlowsFromUsedInOperatingActivities"], ["영업활동현금흐름", "영업활동으로인한현금흐름"]),
    "유형자산취득": (CF, ["ifrs-full_PurchaseOfPropertyPlantAndEquipment"], ["유형자산의취득", "유형자산취득"]),
    "감가상각비": (CF, ["ifrs-full_AdjustmentsForDepreciationExpense"], ["감가상각비"]),
    "무형자산상각비": (CF, ["ifrs-full_AdjustmentsForAmortisationExpense"], ["무형자산상각비"]),
}
BS_KEYS = [k for k, v in ITEMS.items() if v[0] == BS]


def _pick(df, spec, col):
    sjs, ids, names = spec
    d = df[df["sj_div"].isin(sjs)]
    for i in ids:
        m = d[d["account_id"] == i]
        if not m.empty:
            v = _num(m.iloc[0].get(col))
            if v is not None:
                return v
    nm = d["account_nm"].str.replace(r"\s", "", regex=True)
    for n in names:
        m = d[nm.str.startswith(n)]
        if not m.empty:
            v = _num(m.iloc[0].get(col))
            if v is not None:
                return v
    return None


def _borrowings(df, col):
    """차입금 + 사채 합계 (근사치, 리스부채 제외)"""
    d = df[df["sj_div"] == "BS"]
    nm = d["account_nm"].str.replace(r"\s", "", regex=True)
    mask = (nm.str.contains("차입금") | nm.str.endswith("사채")) & ~nm.str.contains("할인|할증|상환|이자|부채총계")
    vals = [_num(v) for v in d.loc[mask, col]]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def full_statements(key, corp_code, year, reprt_code="11011", fs_pref="연결"):
    """전체 재무제표 원자료. fs_pref='연결'이면 연결 우선(없으면 별도), '별도'면 별도만. (df, '연결'|'별도')"""
    order = (("CFS", "연결"), ("OFS", "별도")) if fs_pref == "연결" else (("OFS", "별도"),)
    for fs, label in order:
        d = _get(key, "fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=str(year),
                 reprt_code=reprt_code, fs_div=fs)
        if d and d.get("list"):
            df = pd.DataFrame(d["list"])
            for c in ("account_id", "account_nm", "sj_div"):
                df[c] = df.get(c, "").fillna("").astype(str)
            # 신설·분할 회사 등은 전기(전년) 금액 칸이 아예 없을 수 있음 → 빈 칸으로 채움
            for c in ("thstrm_amount", "frmtrm_amount", "thstrm_add_amount", "frmtrm_add_amount"):
                if c not in df:
                    df[c] = None
            return df, label
    return None, None


Q_REPORT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}  # 1분기, 반기, 3분기, 사업보고서
FLOW_KEYS = [k for k, v in ITEMS.items() if v[0] != BS]  # 손익·현금흐름 (기간 누적 값)
_cum_cache = {}


def cumulative(df, annual):
    """보고서 원자료 → 당기 값 (손익·현금흐름은 연초부터 누적, 재무상태는 기말 잔액)"""
    has_add = df["thstrm_add_amount"].notna().any()
    out = {}
    for k, spec in ITEMS.items():
        col = "thstrm_add_amount" if (spec[0] == IS and not annual and has_add) else "thstrm_amount"
        out[k] = _pick(df, spec, col)
    out["차입금"] = _borrowings(df, "thstrm_amount")
    if out.get("지배주주순이익") is None:  # 계속영업·중단영업으로 나눠 공시한 회사 → 합산
        cont = _pick(df, (IS, ["ifrs-full_IncomeFromContinuingOperationsAttributableToOwnersOfParent"], []),
                     "thstrm_add_amount" if (not annual and has_add) else "thstrm_amount")
        disc_ = _pick(df, (IS, ["ifrs-full_IncomeFromDiscontinuedOperationsAttributableToOwnersOfParent"], []),
                      "thstrm_add_amount" if (not annual and has_add) else "thstrm_amount")
        if cont is not None or disc_ is not None:
            out["지배주주순이익"] = (cont or 0) + (disc_ or 0)
    if out.get("매출총이익") is None and out.get("매출액") is not None and out.get("매출원가") is not None:
        out["매출총이익"] = out["매출액"] - out["매출원가"]
    return out


def period_cum(key, corp_code, year, q, fs="연결"):
    """q: 1~4 (4 = 사업보고서). (누적 값, '연결'|'별도') 또는 None"""
    ck = (corp_code, int(year), q, fs)
    if ck not in _cum_cache:
        raw, label = full_statements(key, corp_code, year, Q_REPORT[q], fs)
        _cum_cache[ck] = None if raw is None else (cumulative(raw, q == 4), label)
    return _cum_cache[ck]


def period_values(key, corp_code, year, q=0, fs="연결"):
    """q=0 연간, 1~4 단일 분기. 분기 손익·현금흐름 = 이번 누적 − 직전 분기 누적 (4Q = 연간 − 3분기 누적)"""
    c = period_cum(key, corp_code, year, 4 if q == 0 else q, fs)
    if not c:
        return None, None
    cur = dict(c[0])
    if q > 1:
        p = period_cum(key, corp_code, year, q - 1, fs)
        if not p:
            return None, None
        for k in FLOW_KEYS:
            a, b = cur.get(k), p[0].get(k)
            cur[k] = None if a is None or b is None else a - b
    return cur, c[1]


def begin_balance(key, corp_code, year, q=0, fs="연결"):
    """기간 시작 시점(직전 기말)의 재무상태"""
    c = period_cum(key, corp_code, year - 1, 4, fs) if q in (0, 1) else period_cum(key, corp_code, year, q - 1, fs)
    return c[0] if c else {}


# ---------------------------------------------------------------- 지표 계산
def _div(a, b, pct=True):
    if a is None or b is None or b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return round(a / b * (100 if pct else 1), 2)


def _avg(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2


def _growth(c, p):
    if c is None or p is None or p <= 0:
        return None  # 전기가 0 이하(적자)면 증가율 의미 없음
    return round((c / p - 1) * 100, 2)


def ratios(cur, begin, yoy, f=1):
    """begin: 기초 재무상태(평균 계산용), yoy: 전년 동기 값(증가율용), f: 연환산 배수(분기=4)"""
    ann = lambda v: None if v is None else v * f
    prev = begin
    sales, op, ni = cur.get("매출액"), cur.get("영업이익"), cur.get("당기순이익")
    ni_owner = cur.get("지배주주순이익") if cur.get("지배주주순이익") is not None else ni
    eq_owner = cur.get("지배주주자본") if cur.get("지배주주자본") is not None else cur.get("자본총계")
    eq_owner_p = prev.get("지배주주자본") if prev.get("지배주주자본") is not None else prev.get("자본총계")
    da = sum(v for v in (cur.get("감가상각비"), cur.get("무형자산상각비")) if v is not None) or None
    ebitda = op + da if (op is not None and da is not None) else None
    capex = abs(cur["유형자산취득"]) if cur.get("유형자산취득") is not None else None
    ocf = cur.get("영업활동현금흐름")
    inv_turn = _div(ann(cur.get("매출원가")), _avg(cur.get("재고자산"), prev.get("재고자산")), pct=False)
    ar_turn = _div(ann(sales), _avg(cur.get("매출채권"), prev.get("매출채권")), pct=False)
    r = {
        # 수익성
        "매출총이익률(%)": _div(cur.get("매출총이익"), sales),
        "영업이익률(%)": _div(op, sales),
        "순이익률(%)": _div(ni, sales),
        "EBITDA마진(%)": _div(ebitda, sales),
        "ROE(%)": _div(ann(ni_owner), _avg(eq_owner, eq_owner_p)),
        "ROA(%)": _div(ann(ni), _avg(cur.get("자산총계"), prev.get("자산총계"))),
        # 안정성
        "부채비율(%)": _div(cur.get("부채총계"), cur.get("자본총계")),
        "유동비율(%)": _div(cur.get("유동자산"), cur.get("유동부채")),
        "당좌비율(%)": _div(None if cur.get("유동자산") is None else cur["유동자산"] - (cur.get("재고자산") or 0),
                        cur.get("유동부채")),
        "자기자본비율(%)": _div(cur.get("자본총계"), cur.get("자산총계")),
        "차입금의존도(%)": _div(cur.get("차입금"), cur.get("자산총계")),
        "이자보상배율(배)": _div(op, cur.get("금융비용"), pct=False),
        # 활동성
        "총자산회전율(회)": _div(ann(sales), _avg(cur.get("자산총계"), prev.get("자산총계")), pct=False),
        "재고자산회전율(회)": inv_turn,
        "재고자산회전일수(일)": round(365 / inv_turn, 1) if inv_turn else None,
        "매출채권회전율(회)": ar_turn,
        "매출채권회전일수(일)": round(365 / ar_turn, 1) if ar_turn else None,
        # 성장성 (전년 동기 대비)
        "매출증가율(%)": _growth(sales, yoy.get("매출액")),
        "영업이익증가율(%)": _growth(op, yoy.get("영업이익")),
        "순이익증가율(%)": _growth(ni, yoy.get("당기순이익")),
        "총자산증가율(%)": _growth(cur.get("자산총계"), yoy.get("자산총계")),
        # 현금흐름
        "EBITDA": ebitda,
        "순차입금": None if cur.get("차입금") is None else cur["차입금"] - (cur.get("현금성자산") or 0),
        "설비투자(CAPEX)": capex,
        "잉여현금흐름(FCF)": None if (ocf is None or capex is None) else ocf - capex,
    }
    return r


GROUPS = {
    "수익성": ["매출총이익률(%)", "영업이익률(%)", "순이익률(%)", "EBITDA마진(%)", "ROE(%)", "ROA(%)"],
    "안정성": ["부채비율(%)", "유동비율(%)", "당좌비율(%)", "자기자본비율(%)", "차입금의존도(%)", "이자보상배율(배)"],
    "활동성": ["총자산회전율(회)", "재고자산회전율(회)", "재고자산회전일수(일)", "매출채권회전율(회)", "매출채권회전일수(일)"],
    "성장성": ["매출증가율(%)", "영업이익증가율(%)", "순이익증가율(%)", "총자산증가율(%)"],
    "현금흐름": ["영업활동현금흐름", "잉여현금흐름(FCF)", "설비투자(CAPEX)", "EBITDA", "순차입금"],
    "가치평가": ["PER(배)", "PBR(배)", "PSR(배)", "EV/EBITDA(배)", "배당수익률(%)", "주가(원)", "시가총액(억원)",
             "EPS(원)", "BPS(원)"],
}
AMOUNT_METRICS = set(GROUPS["현금흐름"])  # 억원 단위 표시
AMOUNTS = ["매출액", "매출총이익", "영업이익", "당기순이익", "지배주주순이익", "자산총계", "부채총계", "자본총계",
           "지배주주자본", "현금성자산", "재고자산", "매출채권", "차입금", "영업활동현금흐름", "EBITDA", "순차입금",
           "설비투자(CAPEX)", "잉여현금흐름(FCF)"]


def inv_turn_ttm(key, corp_code, year, q, fs="연결"):
    """분기 재고자산회전율 (최근 4개 분기 합산)
    = 최근 4개 분기 매출원가 합계 ÷ (1년 전 같은 분기말 재고 + 이번 분기말 재고) / 2
    최근 4개 분기 매출원가 = 올해 누적 + 전년 연간 − 전년 같은 분기 누적 (4Q는 올해 연간)"""
    cur = period_cum(key, corp_code, year, q, fs)
    prev_same = period_cum(key, corp_code, year - 1, q, fs)
    if not cur or not prev_same:
        return None
    if q == 4:
        cogs = cur[0].get("매출원가")
    else:
        prev_ann = period_cum(key, corp_code, year - 1, 4, fs)
        a, b, c = cur[0].get("매출원가"), (prev_ann or [{}])[0].get("매출원가"), prev_same[0].get("매출원가")
        cogs = None if None in (a, b, c) else a + b - c
    return _div(cogs, _avg(cur[0].get("재고자산"), prev_same[0].get("재고자산")), pct=False)


def financials(key, corp_code, years, q=0, fs="연결"):
    """연도별 금액 + 지표 표. q=0 연간, 1~4 분기(단일 분기, 비율은 연환산)"""
    rows = []
    for y in years:
        cur, label = period_values(key, corp_code, y, q, fs)
        if cur is None:
            continue
        yoy, _ = period_values(key, corp_code, y - 1, q, fs)
        begin = begin_balance(key, corp_code, y, q, fs)
        r = ratios(cur, begin, yoy or {}, 1 if q == 0 else 4)
        if q:  # 분기: 재고자산회전율은 최근 4개 분기 합산(TTM) 방식
            it = inv_turn_ttm(key, corp_code, y, q, fs)
            r["재고자산회전율(회)"] = it
            r["재고자산회전일수(일)"] = round(365 / it, 1) if it else None
        rows.append({"연도": int(y), "기준": label, **cur, **r})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in df.columns:  # 빈 값(None)이 섞여도 모두 숫자형으로
        if c != "기준":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("연도").reset_index(drop=True)


# ---------------------------------------------------------------- 공시 기본주당이익(EPS)
_EPS_SPEC = (IS, ["ifrs-full_BasicEarningsLossPerShare"],
             ["기본주당이익", "기본주당순이익", "기본및희석주당이익", "기본및희석주당순이익", "보통주기본주당이익"])


def basic_eps(key, corp_code, year):
    """사업보고서(연간) 연결 기준 기본주당이익(원). 연결이 없으면 별도. 없으면 None"""
    raw, _ = full_statements(key, corp_code, year, "11011", "연결")
    return None if raw is None else _pick(raw, _EPS_SPEC, "thstrm_amount")


def eps_year_for(date):
    """date 시점에 시장이 쓰는 '직전 사업연도' (사업보고서는 보통 3월 말까지 나옴)"""
    return date.year - 1 if date.month >= 4 else date.year - 2


# ---------------------------------------------------------------- 주식수·배당
def shares(key, corp_code, year, reprt_code="11011"):
    """보통주 발행주식총수, 자기주식수"""
    d = _get(key, "stockTotqySttus.json", corp_code=corp_code, bsns_year=str(year), reprt_code=reprt_code)
    if not d:
        return None
    for r in d["list"]:
        if "보통" in (r.get("se") or ""):
            return {"발행주식수": _num(r.get("istc_totqy")), "자기주식수": _num(r.get("tesstk_co")),
                    "유통주식수": _num(r.get("distb_stock_co"))}
    return None


def dividend(key, corp_code, year):
    """보통주 주당 현금배당금(원)"""
    d = _get(key, "alotMatter.json", corp_code=corp_code, bsns_year=str(year), reprt_code="11011")
    if not d:
        return None
    for r in d["list"]:
        se = (r.get("se") or "").replace(" ", "")
        if "주당현금배당금" in se and "보통" in (r.get("stock_knd") or "보통"):
            return _num(r.get("thstrm"))
    return None


# ---------------------------------------------------------------- 공시
def disclosures(key, corp_code, bgn_de, end_de, max_pages=3):
    """공시 목록. 날짜는 YYYYMMDD."""
    rows = []
    for page in range(1, max_pages + 1):
        d = _get(key, "list.json", corp_code=corp_code, bgn_de=bgn_de, end_de=end_de,
                 page_no=page, page_count=100)
        if not d:
            break
        rows += d["list"]
        if page >= int(d.get("total_page", 1)):
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["링크"] = VIEWER + df["rcept_no"]
    df = df.rename(columns={"rcept_dt": "접수일", "corp_name": "회사", "report_nm": "공시명", "flr_nm": "제출인"})
    return df[["접수일", "회사", "공시명", "제출인", "링크"]]


IMPORTANT = ["유상증자", "무상증자", "전환사채", "신주인수권", "최대주주", "합병", "분할", "소송",
             "감사보고서", "감사의견", "단일판매", "공급계약", "자기주식", "영업정지", "횡령", "배임",
             "상장폐지", "관리종목", "불성실공시"]


def is_important(title):
    return any(k in title for k in IMPORTANT)


# ---------------------------------------------------------------- 지표 설명 (마우스 올리면 표시)
DESC = {
    "매출액": "손익계산서의 매출액(영업수익) · 분기는 해당 분기 3개월 값",
    "영업이익": "손익계산서의 영업이익 · 분기는 해당 분기 3개월 값",
    "매출총이익률(%)": "매출총이익 ÷ 매출액 × 100",
    "영업이익률(%)": "영업이익 ÷ 매출액 × 100",
    "순이익률(%)": "당기순이익 ÷ 매출액 × 100",
    "EBITDA마진(%)": "(영업이익 + 감가상각비 + 무형자산상각비) ÷ 매출액 × 100",
    "ROE(%)": "지배주주순이익 ÷ 평균 지배주주자본 × 100\n(평균 = 기초·기말 평균, 분기·반기는 연환산)",
    "ROA(%)": "당기순이익 ÷ 평균 자산총계 × 100\n(분기·반기는 연환산)",
    "부채비율(%)": "부채총계 ÷ 자본총계 × 100",
    "유동비율(%)": "유동자산 ÷ 유동부채 × 100",
    "당좌비율(%)": "(유동자산 − 재고자산) ÷ 유동부채 × 100",
    "자기자본비율(%)": "자본총계 ÷ 자산총계 × 100",
    "차입금의존도(%)": "차입금 ÷ 자산총계 × 100\n(차입금 = '차입금'·'사채' 계정 합계, 리스부채 제외 · 근사치)",
    "이자보상배율(배)": "영업이익 ÷ 금융비용\n(금융비용에 이자 외 항목이 섞일 수 있어 근사치)",
    "총자산회전율(회)": "매출액 ÷ 평균 자산총계",
    "재고자산회전율(회)": "매출원가 ÷ 평균 재고자산\n연간: 기초·기말 평균\n분기: 최근 4개 분기 매출원가 합계 ÷ (1년 전 같은 분기말·이번 분기말 재고 평균)\n매출원가가 없는 회사는 표시 안 함",
    "재고자산회전일수(일)": "365 ÷ 재고자산회전율",
    "매출채권회전율(회)": "매출액 ÷ 평균 매출채권",
    "매출채권회전일수(일)": "365 ÷ 매출채권회전율",
    "매출증가율(%)": "(당기 매출액 ÷ 전년 동기 매출액 − 1) × 100\n(전년 값이 0 이하면 표시 안 함)",
    "영업이익증가율(%)": "(당기 영업이익 ÷ 전년 동기 영업이익 − 1) × 100\n(전년이 적자면 표시 안 함)",
    "순이익증가율(%)": "(당기 순이익 ÷ 전년 동기 순이익 − 1) × 100\n(전년이 적자면 표시 안 함)",
    "총자산증가율(%)": "(당기말 자산총계 ÷ 전기말 자산총계 − 1) × 100",
    "영업활동현금흐름": "현금흐름표의 '영업활동으로 인한 현금흐름'",
    "잉여현금흐름(FCF)": "영업활동현금흐름 − 유형자산 취득액",
    "설비투자(CAPEX)": "현금흐름표의 '유형자산의 취득' 금액",
    "EBITDA": "영업이익 + 감가상각비 + 무형자산상각비",
    "순차입금": "차입금 − 현금및현금성자산 (마이너스면 순현금)",
    "PER(배)": "KRX 종가 ÷ 공시 기본EPS (가장 최근 확정 사업보고서)\n연간은 그 해 EPS, 1~3분기는 전년도 EPS · 주가는 기준 기간 말 KRX 종가 · 적자면 표시 안 함",
    "PBR(배)": "주가 ÷ BPS (BPS = 지배주주자본 ÷ 보통주 주식수)\n주가는 기준 기간 말 종가",
    "PSR(배)": "시가총액 ÷ 매출액 (분기는 매출 연환산 ×4)",
    "EV/EBITDA(배)": "(시가총액 + 순차입금) ÷ EBITDA (분기는 EBITDA 연환산 ×4)",
    "배당수익률(%)": "보통주 주당 현금배당금 ÷ 주가 × 100\n(연간·4Q는 그해 배당, 1~3Q는 직전 연도 배당 기준)",
    "주가(원)": "기준 기간 말 종가 (1Q 3/31, 2Q 6/30, 3Q 9/30, 연간·4Q 12/31 · 휴장일이면 직전 거래일)",
    "시가총액(억원)": "기준 기간 말 보통주 시가총액",
    "EPS(원)": "사업보고서에 공시된 기본주당이익 (연간은 그 해, 1~3분기는 전년도)",
    "BPS(원)": "지배주주자본 ÷ 보통주 주식수",
}
