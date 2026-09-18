"""OpenDartReader(https://github.com/financedata/opendartreader)가 제공하는 기능을 앱용으로 옮긴 모듈
- 사업보고서 주요 항목 28종, 주요사항보고 36종, 증권신고서 6종
- 지분공시(대량보유·임원/주요주주 소유), 전체 기업 공시 검색(유형·기간·제출인)
- 공시 원문 텍스트, 하위 문서 목록, 첨부 문서, 첨부 파일, 재무제표 XBRL 원본
모든 조회는 dart._get 을 거쳐 인증키가 화면에 보이지 않고, 결과는 캐시됨."""
import io
import re
import zipfile

import pandas as pd
import requests

import dart

# ---------------------------------------------------------------- 사업보고서 주요 항목
REPORT_MAP = {
    "배당": "alotMatter", "최대주주": "hyslrSttus", "최대주주변동": "hyslrChgSttus", "소액주주": "mrhlSttus",
    "주식총수": "stockTotqySttus", "자기주식": "tesstkAcqsDspsSttus", "증자(감자)": "irdsSttus",
    "임원": "exctvSttus", "직원": "empSttus", "사외이사": "outcmpnyDrctrNdChangeSttus",
    "미등기임원보수": "unrstExctvMendngSttus", "임원전체보수": "hmvAuditAllSttus",
    "임원개인보수": "hmvAuditIndvdlBySttus", "개인별보수(5억이상 상위5인)": "indvdlByPay",
    "임원전체보수승인": "drctrAdtAllMendngSttusGmtsckConfmAmount",
    "임원전체보수유형": "drctrAdtAllMendngSttusMendngPymntamtTyCl",
    "타법인출자": "otrCprInvstmntSttus", "회계감사(감사의견)": "accnutAdtorNmNdAdtOpinion",
    "감사용역": "adtServcCnclsSttus", "회계감사인 비감사용역": "accnutAdtorNonAdtServcCnclsSttus",
    "채무증권발행": "detScritsIsuAcmslt", "회사채미상환": "cprndNrdmpBlce", "단기사채미상환": "srtpdPsndbtNrdmpBlce",
    "기업어음미상환": "entrprsBilScritsNrdmpBlce", "신종자본증권미상환": "newCaplScritsNrdmpBlce",
    "조건부자본증권미상환": "cndlCaplScritsNrdmpBlce", "공모자금사용": "pssrpCptalUseDtls",
    "사모자금사용": "prvsrpCptalUseDtls",
}

# ---------------------------------------------------------------- 주요사항보고
EVENT_MAP = {
    "유상증자": "piicDecsn", "무상증자": "fricDecsn", "유무상증자": "pifricDecsn", "감자": "crDecsn",
    "전환사채발행": "cvbdIsDecsn", "신주인수권부사채발행": "bdwtIsDecsn", "교환사채발행": "exbdIsDecsn",
    "조건부자본증권발행": "wdCocobdIsDecsn", "자기주식취득": "tsstkAqDecsn", "자기주식처분": "tsstkDpDecsn",
    "자기주식취득신탁계약체결": "tsstkAqTrctrCnsDecsn", "자기주식취득신탁계약해지": "tsstkAqTrctrCcDecsn",
    "회사합병": "cmpMgDecsn", "회사분할": "cmpDvDecsn", "회사분할합병": "cmpDvmgDecsn", "주식교환·이전": "stkExtrDecsn",
    "영업양수": "bsnInhDecsn", "영업양도": "bsnTrfDecsn", "유형자산양수": "tgastInhDecsn", "유형자산양도": "tgastTrfDecsn",
    "타법인증권양수": "otcprStkInvscrInhDecsn", "타법인증권양도": "otcprStkInvscrTrfDecsn",
    "사채권양수": "stkrtbdInhDecsn", "사채권양도": "stkrtbdTrfDecsn", "자산양수도(풋백옵션)": "astInhtrfEtcPtbkOpt",
    "소송": "lwstLg", "부도발생": "dfOcr", "영업정지": "bsnSp", "회생절차": "ctrcvsBgrq", "해산사유": "dsRsOcr",
    "관리절차개시": "bnkMngtPcbg", "관리절차중단": "bnkMngtPcsp", "해외상장결정": "ovLstDecsn",
    "해외상장폐지결정": "ovDlstDecsn", "해외상장": "ovLst", "해외상장폐지": "ovDlst",
}

# ---------------------------------------------------------------- 증권신고서
REGSTATE_MAP = {"지분증권": "estkRs", "채무증권": "bdRs", "증권예탁증권": "stkdpRs",
                "합병": "mgRs", "분할": "dvRs", "주식의포괄적교환이전": "extrRs"}

KIND = {"전체": "", "정기공시": "A", "주요사항보고": "B", "발행공시": "C", "지분공시": "D", "기타공시": "E",
        "외부감사관련": "F", "펀드공시": "G", "자산유동화": "H", "거래소공시": "I", "공정위공시": "J"}

REPORT_CODES = {"사업보고서": "11011", "반기보고서": "11012", "1분기보고서": "11013", "3분기보고서": "11014"}

# 자주 나오는 영어 칸 이름 → 한글
COLS = {
    "rcept_no": "접수번호", "corp_cls": "시장", "corp_code": "고유번호", "corp_name": "회사명", "stock_code": "종목코드",
    "report_nm": "보고서명", "flr_nm": "제출인", "rcept_dt": "접수일", "rm": "비고", "se": "구분", "nm": "성명",
    "relate": "관계", "stock_knd": "주식종류", "bsis_posesn_stock_co": "기초 주식수",
    "bsis_posesn_stock_qota_rt": "기초 지분율", "trmend_posesn_stock_co": "기말 주식수",
    "trmend_posesn_stock_qota_rt": "기말 지분율", "thstrm": "당기", "frmtrm": "전기", "lwfr": "전전기",
    "sexdstn": "성별", "birth_ym": "출생년월", "ofcps": "직위", "rgist_exctv_at": "등기임원여부",
    "fte_at": "상근여부", "chrg_job": "담당업무", "main_career": "주요경력", "hffc_pd": "재직기간",
    "tm_expr_day": "임기만료일", "fo_bbm": "사업부문", "sm": "합계", "avrg_cnwk_sdytrn": "평균근속연수",
    "fyer_salary_totamt": "연간급여총액", "jan_salary_am": "1인평균급여", "report_tp": "보고구분",
    "repror": "대표보고자", "stkqy": "보유주식수", "stkqy_irds": "증감주식수", "stkrt": "보유비율",
    "stkrt_irds": "증감비율", "ctr_stkqy": "주요계약주식수", "ctr_stkrt": "주요계약비율",
    "report_resn": "보고사유", "isu_exctv_rgist_at": "등기임원여부", "isu_exctv_ofcps": "직위",
    "isu_main_shrholdr": "주요주주", "sp_stock_lmp_cnt": "특정증권 소유수", "sp_stock_lmp_irds_cnt": "특정증권 증감수",
    "sp_stock_lmp_rate": "특정증권 소유비율", "sp_stock_lmp_irds_rate": "특정증권 증감비율", "stlm_dt": "결산기준일",
}


def _df(d):
    if not d or not d.get("list"):
        return pd.DataFrame()
    df = pd.DataFrame(d["list"])
    return df.rename(columns={c: COLS.get(c, c) for c in df.columns})


def report(key, corp_code, item, year, reprt_code="11011"):
    return _df(dart._get(key, f"{REPORT_MAP[item]}.json", corp_code=corp_code,
                         bsns_year=str(year), reprt_code=reprt_code))


def event(key, corp_code, item, bgn, end):
    return _df(dart._get(key, f"{EVENT_MAP[item]}.json", corp_code=corp_code, bgn_de=bgn, end_de=end))


def regstate(key, corp_code, item, bgn, end):
    d = dart._get(key, f"{REGSTATE_MAP[item]}.json", corp_code=corp_code, bgn_de=bgn, end_de=end)
    if not d:
        return pd.DataFrame()
    if d.get("list"):
        return _df(d)
    frames = []
    for g in d.get("group", []) or []:
        f = _df({"list": g.get("list", [])})
        if not f.empty:
            f.insert(0, "항목", g.get("title", ""))
            frames.append(f)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def major_shareholders(key, corp_code):
    """대량보유 상황보고 (5% 이상)"""
    return _df(dart._get(key, "majorstock.json", corp_code=corp_code))


def exec_shareholders(key, corp_code):
    """임원·주요주주 소유보고"""
    return _df(dart._get(key, "elestock.json", corp_code=corp_code))


def search(key, bgn, end, kind="", corp_code=None, final=True, max_pages=10):
    """공시 검색. corp_code 없으면 전체 기업(기간 최대 3개월)"""
    rows = []
    for page in range(1, max_pages + 1):
        p = dict(bgn_de=bgn, end_de=end, page_no=page, page_count=100, last_reprt_at="Y" if final else "N")
        if kind:
            p["pblntf_ty"] = kind
        if corp_code:
            p["corp_code"] = corp_code
        d = dart._get(key, "list.json", **p)
        if not d:
            break
        rows += d["list"]
        if page >= int(d.get("total_page", 1)):
            break
    df = _df({"list": rows})
    if not df.empty:
        df["링크"] = dart.VIEWER + df["접수번호"]
        df["시장"] = df["시장"].map({"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "기타"}).fillna(df["시장"])
    return df


def by_presenter(df, name):
    """제출인 이름으로 걸러내기 (예: 국민연금공단)"""
    return df[df["제출인"].str.contains(name, na=False)] if not df.empty and name else df


# ---------------------------------------------------------------- 원문·첨부 (파일)
def _bytes(key, path, **params):
    """zip 등 파일을 받는 API. 인증키가 오류 문구에 나오지 않게 처리"""
    params["crtfc_key"] = key
    try:
        r = dart.SESSION.get(f"{dart.BASE}/{path}", params=params, timeout=(5, 60))
        r.raise_for_status()
    except requests.RequestException:
        raise dart.DartError("DART 서버에 연결할 수 없어요") from None
    if not r.content.startswith(b"PK"):
        raise dart.DartError(r.text[:200].replace(params["crtfc_key"], "***"))
    return r.content


def document_zip(key, rcept_no):
    """공시 원문(XML) zip 파일"""
    return _bytes(key, "document.xml", rcept_no=rcept_no)


def document_text(key, rcept_no, limit=200_000):
    """공시 원문을 읽기 쉬운 글자로 (표·태그 제거)"""
    z = zipfile.ZipFile(io.BytesIO(document_zip(key, rcept_no)))
    parts = []
    for n in z.namelist():
        raw = z.read(n)
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                t = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        t = re.sub(r"<[^>]+>", " ", t)
        parts.append(re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip())
    return "\n\n".join(parts)[:limit]


def xbrl_zip(key, rcept_no, reprt_code="11011"):
    """재무제표 XBRL 원본 zip"""
    return _bytes(key, "fnlttXbrl.xml", rcept_no=rcept_no, reprt_code=reprt_code)


UA = {"User-Agent": "Mozilla/5.0"}
WEB = "https://dart.fss.or.kr"


def sub_docs(rcept_no):
    """보고서의 하위 문서(목차) 목록: 제목, 링크 — DART 웹페이지를 읽어서 만듦(비공식)"""
    r = requests.get(f"{WEB}/dsaf001/main.do", params={"rcpNo": rcept_no}, headers=UA, timeout=20)
    pat = (r"node[12]\['text'\][ =]+\"(.*?)\";\s+node[12]\['id'\][ =]+\"(\d+)\";\s+node[12]\['rcpNo'\][ =]+\"(\d+)\";"
           r"\s+node[12]\['dcmNo'\][ =]+\"(\d+)\";\s+node[12]\['eleId'\][ =]+\"(\d+)\";\s+node[12]\['offset'\][ =]+\"(\d+)\";"
           r"\s+node[12]\['length'\][ =]+\"(\d+)\";\s+node[12]\['dtd'\][ =]+\"(.*?)\";")
    rows = [[m[0], f"{WEB}/report/viewer.do?rcpNo={m[2]}&dcmNo={m[3]}&eleId={m[4]}&offset={m[5]}&length={m[6]}&dtd={m[7]}"]
            for m in re.findall(pat, r.text)]
    return pd.DataFrame(rows, columns=["문서", "링크"])


def attach_docs(rcept_no):
    """첨부 문서 목록 (감사보고서 등)"""
    r = requests.get(f"{WEB}/dsaf001/main.do", params={"rcpNo": rcept_no}, headers=UA, timeout=20)
    m = re.search(r'<select[^>]*id="att"[^>]*>(.*?)</select>', r.text, re.S)
    rows = []
    for val, title in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', m.group(1) if m else "", re.S):
        if val and val != "null":
            rows.append([" ".join(re.sub(r"<[^>]+>", "", title).split()), f"{WEB}/dsaf001/main.do?{val}"])
    return pd.DataFrame(rows, columns=["첨부 문서", "링크"])


def attach_files(rcept_no):
    """첨부 파일(PDF·엑셀 등) 목록: 파일명, 받기 링크"""
    r = requests.get(f"{WEB}/dsaf001/main.do", params={"rcpNo": rcept_no}, headers=UA, timeout=20)
    m = re.search(r"node[12]\['rcpNo'\][ =]+\"(\d+)\";\s+node[12]\['dcmNo'\][ =]+\"(\d+)\";", r.text)
    if not m:
        m2 = re.search(r"viewDoc\('(\d+)', '(\d+)'", r.text)
        if not m2:
            return pd.DataFrame(columns=["파일", "링크"])
        m = m2
    r = requests.get(f"{WEB}/pdf/download/main.do", params={"rcp_no": m.group(1), "dcm_no": m.group(2)},
                     headers=UA, timeout=20)
    rows = [[re.sub(r"<[^>]+>", "", t).strip(), WEB + h]
            for t, h in re.findall(r"<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*<a[^>]*href=\"([^\"]+)\"", r.text, re.S)]
    return pd.DataFrame(rows, columns=["파일", "링크"])
