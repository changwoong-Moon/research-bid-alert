#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
나라장터 연구용역 공고 대시보드 생성기 (연구용역 알리미)

조달청 나라장터 입찰공고정보서비스(공공데이터포털, 데이터셋 15129394)에서
용역 입찰공고를 수집해 키워드로 선별한 뒤, 단일 HTML 대시보드
(research_bids.html)를 생성합니다.

- 실행: python narajangteo.py            (환경변수 SERVICE_KEY 필요)
- 데모: python narajangteo.py --demo     (API 호출 없이 샘플 데이터로 레이아웃 확인)

표준 라이브러리만 사용합니다 (별도 pip install 불필요).
"""

import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# ============================================================================
# 사용자 설정 — 이 블록만 고치면 됩니다
# ============================================================================

# 공고명에 아래 키워드 중 하나라도 포함되면 수집 후보가 됩니다.
INCLUDE_KEYWORDS = [
    "연구", "연구용역", "정책", "실태조사", "타당성", "학술", "조사연구",
    "발전방안", "기본계획", "종합계획", "중장기", "로드맵", "성과평가", "컨설팅",
    # 행정학 분야 보강 (2026-08): "조직"·"진단" 단독은 생체조직·안전진단 등
    # 무관한 공고가 섞여 복합어로만 추가
    "거버넌스", "조직진단", "조직문화", "경영진단", "행정진단",
    "만족도", "개선방안", "공론화", "성과분석",
]

# 후보 중 공고명에 아래 키워드가 포함되면 제외합니다 (기술·시설·용역성 공고 걸러내기).
EXCLUDE_KEYWORDS = [
    "감리", "설계", "시공", "청소", "경비", "소독", "방역", "급식",
    "유지보수", "유지관리", "시스템", "소프트웨어", "홈페이지", "전산",
    "임상", "폐기물", "인쇄", "촬영",
]

# 수집 기간: 오늘부터 거슬러 올라갈 일수 (한국시간 기준)
DAYS_BACK = 20

# 생성할 결과 파일 이름 (GitHub Actions 워크플로가 이 이름을 사용하므로
# 바꾸면 update.yml의 cp 명령도 같이 바꿔야 합니다)
OUTPUT_FILE = "research_bids.html"

# ============================================================================
# 내부 상수 (보통 고칠 필요 없음)
# ============================================================================

# 차세대 나라장터(2025-01 개통) 이후의 현행 엔드포인트. 경로의 /ad/ 가 필수입니다.
BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
# 용역 공고를 나라장터 검색조건 방식으로 조회하는 오퍼레이션
OPERATION = "getBidPblancListInfoServcPPSSrch"

NUM_OF_ROWS = 999      # 한 페이지 최대 건수 (공식 가이드 샘플에서 999 사용 확인)
MAX_PAGES = 30         # 페이지네이션 안전 상한 (999 x 30 = 약 3만 건)
CHUNK_DAYS = 30        # 조회기간은 1회 최대 1개월 제한(공식 가이드) → 30일 단위 분할
HTTP_TIMEOUT = 180     # 초 (999건 응답은 4~5MB라 회선이 혼잡하면 수 분 걸리는 경우가 있음)
MAX_RETRIES = 3        # 네트워크 오류 시 재시도 횟수 (지수 백오프)

KST = datetime.timezone(datetime.timedelta(hours=9))  # Actions 러너는 UTC이므로 명시적 KST 사용

# 공공데이터포털 게이트웨이 오류코드 → 한국어 안내
GATEWAY_CODE_HELP = {
    "12": "해당 오픈API 서비스가 없거나 폐기되었습니다. 엔드포인트 URL을 확인하세요.",
    "20": "서비스 접근이 거부되었습니다. 활용신청이 아직 승인되지 않았을 수 있습니다.",
    "22": "일일 서비스 요청 제한 횟수를 초과했습니다 (개발계정 1,000건/일). 내일 다시 시도하세요.",
    "30": ("등록되지 않은 서비스키 입니다. 키를 발급받은 직후라면 반영까지 최대 1시간 걸릴 수 있으니 "
           "잠시 후 다시 실행하세요. 'Encoding' 키를 넣었다면 'Decoding' 키로 바꿔 등록하세요."),
    "31": "기한이 만료된 서비스키입니다. 공공데이터포털에서 연장 신청 후 사용하세요.",
    "32": "활용신청 시 등록한 도메인/IP와 다른 곳에서 호출했습니다.",
}


class ApiError(Exception):
    """API 호출 관련 오류 (한국어 메시지 포함)."""


# ============================================================================
# 유틸리티
# ============================================================================

def to_int(value, default=0):
    """문자열/숫자를 안전하게 int로 변환."""
    try:
        return int(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return default


def parse_dt(value):
    """'YYYY-MM-DD HH:MM(:SS)' / 'YYYYMMDDHHMM' 등 다양한 표기를 KST datetime으로."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    try:
        if len(digits) >= 12:
            return datetime.datetime(
                int(digits[0:4]), int(digits[4:6]), int(digits[6:8]),
                int(digits[8:10]), int(digits[10:12]), tzinfo=KST)
        if len(digits) == 8:
            return datetime.datetime(
                int(digits[0:4]), int(digits[4:6]), int(digits[6:8]), tzinfo=KST)
    except ValueError:
        return None
    return None


def format_amount(*values):
    """금액 후보들 중 처음으로 유효한 값을 '1,234,567원'으로. 없으면 '미공개'."""
    for v in values:
        if v is None:
            continue
        s = str(v).strip().replace(",", "")
        if not s:
            continue
        try:
            n = int(float(s))
        except ValueError:
            continue
        if n > 0:
            return "{:,}원".format(n)
    return "미공개"


def normalize_service_key(key):
    """이중 인코딩 방지: Encoding 키(%2B 등 포함)가 들어오면 원문(Decoding)으로 되돌림.

    urllib의 urlencode가 정확히 1회 인코딩하므로, 여기서는 항상
    '디코딩된 원문 키'를 만들어 두는 것이 안전합니다.
    """
    if re.search(r"%[0-9A-Fa-f]{2}", key):
        return urllib.parse.unquote(key)
    return key


# ============================================================================
# API 호출
# ============================================================================

def _extract_gateway_error(obj):
    """파싱된 dict에서 게이트웨이 오류(OpenAPI_ServiceResponse)를 찾음."""
    if isinstance(obj, dict) and "OpenAPI_ServiceResponse" in obj:
        header = (obj.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader") or {}
        code = str(header.get("returnReasonCode", "")).strip()
        msg = str(header.get("returnAuthMsg") or header.get("errMsg") or "").strip()
        return code, msg
    return None


def _xml_to_dict(text):
    """XML 응답을 JSON 응답과 같은 dict 구조로 변환 (오류/정상 모두 대응)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ApiError("XML 응답 해석 실패(%s). 응답 앞부분: %s" % (e, text[:200]))

    def txt(el):
        return (el.text or "").strip() if el is not None else ""

    if root.tag == "OpenAPI_ServiceResponse":
        header = root.find("cmmMsgHeader")
        return {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {
            "errMsg": txt(header.find("errMsg")) if header is not None else "",
            "returnAuthMsg": txt(header.find("returnAuthMsg")) if header is not None else "",
            "returnReasonCode": txt(header.find("returnReasonCode")) if header is not None else "",
        }}}

    if root.tag == "response":
        header = root.find("header")
        body = root.find("body")
        result = {"header": {
            "resultCode": txt(header.find("resultCode")) if header is not None else "",
            "resultMsg": txt(header.find("resultMsg")) if header is not None else "",
        }, "body": {}}
        if body is not None:
            items = []
            items_el = body.find("items")
            if items_el is not None:
                for item_el in items_el.findall("item"):
                    items.append({child.tag: (child.text or "") for child in item_el})
            result["body"] = {
                "items": {"item": items},
                "numOfRows": txt(body.find("numOfRows")),
                "pageNo": txt(body.find("pageNo")),
                "totalCount": txt(body.find("totalCount")),
            }
        return {"response": result}

    raise ApiError("알 수 없는 XML 응답 형식입니다: 루트 태그 <%s>" % root.tag)


def _parse_body(text):
    """응답 본문(JSON 또는 XML)을 dict로 파싱."""
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return _xml_to_dict(stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        raise ApiError("응답을 JSON/XML로 해석할 수 없습니다. 응답 앞부분: %s" % stripped[:200])


def _raise_gateway_error(code, msg):
    help_text = GATEWAY_CODE_HELP.get(code, "공공데이터포털 오류코드표를 확인하세요.")
    raise ApiError("[게이트웨이 오류 %s] %s — %s" % (code or "?", msg or "원인 미상", help_text))


def request_api(url):
    """단일 URL을 재시도 포함 호출하고 파싱된 dict를 반환."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "research-bid-alert/1.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                text = resp.read().decode("utf-8-sig", "replace")
            try:
                parsed = _parse_body(text)
            except ApiError as e:
                last_error = str(e)  # 200 응답이지만 본문 손상 — 일시적일 수 있어 재시도
            else:
                gateway = _extract_gateway_error(parsed)
                if gateway:
                    _raise_gateway_error(*gateway)  # 인증/트래픽 오류는 재시도 무의미
                return parsed
        except urllib.error.HTTPError as e:
            # 신형 게이트웨이는 키 오류를 HTTP 403/400 + 오류 본문으로 반환함
            try:
                body_text = e.read().decode("utf-8-sig", "replace")
            except Exception:
                body_text = ""
            gateway = None
            if body_text.strip():
                try:
                    gateway = _extract_gateway_error(_parse_body(body_text))
                except ApiError:
                    gateway = None
            if gateway:
                _raise_gateway_error(*gateway)
            last_error = "HTTP %s %s" % (e.code, e.reason)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = str(e)
        if attempt < MAX_RETRIES:
            wait = 2 ** attempt  # 2초, 4초
            print("  [재시도 %d/%d] 호출 실패(%s), %d초 후 재시도합니다."
                  % (attempt, MAX_RETRIES, last_error, wait))
            time.sleep(wait)
    raise ApiError("네트워크 오류로 API 호출에 %d회 모두 실패했습니다: %s" % (MAX_RETRIES, last_error))


def _extract_items(body):
    """response.body 안의 items를 형태와 무관하게 item dict 리스트로 정규화.

    JSON에서 items가 배열일 수도, {"item": [...]} 중첩일 수도, 0건일 때
    빈 문자열("")/누락일 수도 있어 모두 방어합니다.
    """
    items = body.get("items")
    if items in (None, "", []):
        return []
    result = []
    if isinstance(items, list):
        for entry in items:
            if isinstance(entry, dict) and set(entry.keys()) == {"item"}:
                inner = entry["item"]
                if isinstance(inner, list):
                    result.extend(x for x in inner if isinstance(x, dict))
                elif isinstance(inner, dict):
                    result.append(inner)
            elif isinstance(entry, dict):
                result.append(entry)
    elif isinstance(items, dict):
        inner = items.get("item")
        if isinstance(inner, list):
            result.extend(x for x in inner if isinstance(x, dict))
        elif isinstance(inner, dict):
            result.append(inner)
    return result


def _iter_chunks(begin, end, days=CHUNK_DAYS):
    """조회기간 1개월 제한에 대비해 (begin, end)를 최대 days일 단위로 분할."""
    cursor = begin
    while cursor < end:
        nxt = min(cursor + datetime.timedelta(days=days), end)
        yield cursor, nxt
        cursor = nxt


def fetch_bids(service_key, now):
    """조회기간 내 용역 공고 전량 수집 (totalCount 기반 페이지네이션)."""
    key = normalize_service_key(service_key)
    begin = (now - datetime.timedelta(days=DAYS_BACK)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    print("조회 기간(KST): %s ~ %s" % (begin.strftime("%Y-%m-%d %H:%M"),
                                       now.strftime("%Y-%m-%d %H:%M")))
    all_items = []
    for c_begin, c_end in _iter_chunks(begin, now):
        print("구간 %s ~ %s 수집 중..." % (c_begin.strftime("%Y%m%d%H%M"),
                                          c_end.strftime("%Y%m%d%H%M")))
        page = 1
        collected = 0  # 서버가 페이지 크기를 임의 축소해도 안전하도록 수신 건수로 종료 판정
        while True:
            params = {
                "serviceKey": key,
                "numOfRows": NUM_OF_ROWS,
                "pageNo": page,
                "inqryDiv": 1,  # 1 = 공고게시일시 기준
                "inqryBgnDt": c_begin.strftime("%Y%m%d%H%M"),
                "inqryEndDt": c_end.strftime("%Y%m%d%H%M"),
                "type": "json",
            }
            url = "%s/%s?%s" % (BASE_URL, OPERATION, urllib.parse.urlencode(params))
            parsed = request_api(url)

            response = parsed.get("response") or {}
            header = response.get("header") or {}
            result_code = str(header.get("resultCode", "")).strip()
            if result_code not in ("00", "0"):
                if result_code in ("03", "3"):  # No Data — 0건은 정상
                    print("  결과 없음 (resultCode 03).")
                    break
                raise ApiError("[API 오류 %s] %s" % (
                    result_code or "?", str(header.get("resultMsg", "")).strip() or "원인 미상"))

            body = response.get("body") or {}
            page_items = _extract_items(body)
            total = to_int(body.get("totalCount"), 0)
            all_items.extend(page_items)
            collected += len(page_items)
            print("  %d쪽: %d건 수신 (구간 전체 %d건)" % (page, len(page_items), total))

            if not page_items or collected >= total:
                break
            if page >= MAX_PAGES:
                print("  [주의] 페이지 상한(%d)에 도달해 수집을 중단합니다." % MAX_PAGES)
                break
            page += 1
    print("수집 완료: 원본 공고 %d건" % len(all_items))
    return all_items


# ============================================================================
# 필터링 · 정규화
# ============================================================================

def filter_bids(items):
    """공고명 키워드 필터 + 취소공고 제거 + 공고번호 기준 중복 제거(차수 큰 것 유지)."""
    candidates = []
    for item in items:
        name = str(item.get("bidNtceNm") or "").strip()
        if not name:
            continue
        if "취소" in str(item.get("ntceKindNm") or ""):  # 취소공고는 제외
            continue
        if not any(k in name for k in INCLUDE_KEYWORDS):
            continue
        if any(k in name for k in EXCLUDE_KEYWORDS):
            continue
        candidates.append(item)

    best = {}
    for item in candidates:
        no = str(item.get("bidNtceNo") or "").strip()
        key = no if no else "no-id-%d" % id(item)
        ord_new = to_int(item.get("bidNtceOrd"), 0)
        if key not in best or ord_new >= to_int(best[key].get("bidNtceOrd"), 0):
            best[key] = item  # 재공고/변경공고는 차수가 큰 최신 건만 유지

    result = list(best.values())
    print("키워드 선별 결과: %d건 (중복 제거 전 %d건)" % (len(result), len(candidates)))
    return result


def prepare_bids(items, now):
    """표시용 필드 정리 + 마감 임박 순 정렬 (마감 지난 공고는 뒤로)."""
    bids = []
    for item in items:
        close_dt = parse_dt(item.get("bidClseDt"))
        posted_dt = parse_dt(item.get("bidNtceDt")) or parse_dt(item.get("rgstDt"))
        closed = bool(close_dt and close_dt < now)
        dday = (close_dt.date() - now.date()).days if close_dt else None
        url = str(item.get("bidNtceDtlUrl") or item.get("bidNtceUrl") or "").strip()
        if not url.startswith(("http://", "https://")):
            url = "https://www.g2b.go.kr"
        bids.append({
            "name": str(item.get("bidNtceNm") or "").strip(),
            "url": url,
            "ntce_org": str(item.get("ntceInsttNm") or "").strip(),
            "dmnd_org": str(item.get("dminsttNm") or "").strip(),
            "posted": posted_dt.strftime("%Y-%m-%d") if posted_dt else "",
            "posted_iso": posted_dt.isoformat() if posted_dt else "",
            "close_dt": close_dt,
            "close_str": close_dt.strftime("%Y-%m-%d %H:%M") if close_dt else "미정",
            "closed": closed,
            "dday": dday,
            "amount": format_amount(item.get("asignBdgtAmt"),
                                    item.get("presmptPrce"),
                                    item.get("bdgtAmt")),
        })
    far_future = datetime.datetime(9999, 12, 31, tzinfo=KST)
    bids.sort(key=lambda b: (1 if b["closed"] else 0, b["close_dt"] or far_future))
    return bids


# ============================================================================
# HTML 대시보드 생성
# ============================================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>연구용역 알리미</title>
<style>
:root{
  --bg:#f3f5f8; --card:#ffffff; --text:#1c2333; --muted:#68718a;
  --accent:#2456c9; --accent-soft:#e3ebfb; --danger:#d3273e; --border:#e2e6ee;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--text); line-height:1.5;
  font-family:"Apple SD Gothic Neo","Malgun Gothic","맑은 고딕",
    -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans KR",sans-serif;
  -webkit-text-size-adjust:100%;
}
.wrap{max-width:760px; margin:0 auto; padding:20px 14px 48px}
header h1{font-size:1.4rem; margin:0 0 6px}
.sub{color:var(--muted); font-size:.85rem; margin:0 0 14px}
.sub b{color:var(--text)}
.search input{
  width:100%; padding:12px 14px; font-size:1rem; color:var(--text);
  border:1px solid var(--border); border-radius:10px; background:var(--card);
}
.search input:focus{outline:2px solid var(--accent); border-color:var(--accent)}
.sort{display:flex; gap:6px; margin-top:10px}
.sort button{
  flex:1; padding:8px 4px; font-size:.82rem; color:var(--muted); cursor:pointer;
  background:var(--card); border:1px solid var(--border); border-radius:999px;
}
.sort button.on{background:var(--accent); border-color:var(--accent); color:#fff; font-weight:700}
.card{
  background:var(--card); border:1px solid var(--border); border-radius:12px;
  padding:14px 16px; margin-top:12px;
}
.card.closed{opacity:.55}
.card-top{display:flex; justify-content:space-between; align-items:center; gap:8px}
.badge{
  display:inline-block; font-size:.74rem; font-weight:700; padding:3px 10px;
  border-radius:999px; background:var(--accent-soft); color:var(--accent); white-space:nowrap;
}
.badge.urgent{background:var(--danger); color:#fff}
.badge.closed{background:#e6e8ee; color:var(--muted)}
.badge.none{background:#f0f1f5; color:var(--muted)}
.posted{font-size:.76rem; color:var(--muted); white-space:nowrap}
.card h2{font-size:1rem; margin:9px 0 6px; line-height:1.45; font-weight:600;
  word-break:keep-all; overflow-wrap:anywhere}
.card h2 a{color:inherit; text-decoration:none}
.card h2 a:hover,.card h2 a:focus{color:var(--accent); text-decoration:underline}
.org{font-size:.82rem; color:var(--muted); margin-bottom:9px;
  word-break:keep-all; overflow-wrap:anywhere}
.rows{display:flex; flex-wrap:wrap; gap:4px 20px; font-size:.85rem}
.rows span{color:var(--muted); margin-right:6px}
.notice{
  background:var(--card); border:1px dashed var(--border); border-radius:12px;
  padding:30px 16px; text-align:center; color:var(--muted); margin-top:14px;
  font-size:.9rem;
}
footer{margin-top:32px; font-size:.75rem; color:var(--muted); line-height:1.7}
@media (min-width:600px){
  header h1{font-size:1.6rem}
  .card{padding:16px 20px}
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>연구용역 알리미</h1>
    <p class="sub">__UPDATED__ 갱신(한국시간) · 최근 __DAYS__일 공고 중 <b>__COUNT__건</b> 표시</p>
    <div class="search">
      <input id="q" type="search" placeholder="공고명·기관명으로 바로 검색" autocomplete="off" aria-label="공고 검색">
    </div>
    <div class="sort" role="group" aria-label="정렬 방식">
      <button type="button" data-sort="close" class="on">마감 임박순</button>
      <button type="button" data-sort="new">최신 게시순</button>
      <button type="button" data-sort="old">오래된 순</button>
    </div>
  </header>
  <main id="list">
__CONTENT__
    <div id="no-result" class="notice" style="display:none">검색어에 맞는 공고가 없습니다.</div>
  </main>
  <footer>
    자료 출처: 조달청 나라장터 입찰공고정보서비스(공공데이터포털)<br>
    공고명 키워드 기준으로 자동 선별한 결과이므로 실제 공고가 누락될 수 있습니다.
    중요한 공고는 나라장터에서 직접 확인하세요.
  </footer>
</div>
<script>
(function () {
  var input = document.getElementById('q');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var noResult = document.getElementById('no-result');

  // 대시보드는 하루 2회만 갱신되므로 D-day 배지를 열람 시점 기준으로 KST 재계산
  var nowMs = Date.now();
  var nowK = new Date(nowMs + 32400000);  // UTC+9
  var todayK = Date.UTC(nowK.getUTCFullYear(), nowK.getUTCMonth(), nowK.getUTCDate());
  cards.forEach(function (card) {
    var iso = card.getAttribute('data-close');
    var badge = card.querySelector('.badge');
    if (!iso || !badge) return;
    var close = new Date(iso);
    if (isNaN(close.getTime())) return;
    if (close.getTime() < nowMs) {
      badge.textContent = '마감';
      badge.className = 'badge closed';
      card.classList.add('closed');
      return;
    }
    var closeK = new Date(close.getTime() + 32400000);
    var closeDay = Date.UTC(closeK.getUTCFullYear(), closeK.getUTCMonth(), closeK.getUTCDate());
    var dday = Math.round((closeDay - todayK) / 86400000);
    badge.textContent = dday <= 0 ? 'D-DAY' : 'D-' + dday;
    badge.className = dday <= 3 ? 'badge urgent' : 'badge';
  });

  // 정렬: 서버가 렌더링한 순서(마감 임박순)를 기준으로 게시일 오름/내림 재배열
  var list = document.getElementById('list');
  var initialOrder = cards.slice();
  function applySort(mode) {
    var arr = initialOrder.slice();
    if (mode === 'new' || mode === 'old') {
      arr.sort(function (a, b) {
        var pa = a.getAttribute('data-posted') || '';
        var pb = b.getAttribute('data-posted') || '';
        if (pa === pb) return 0;
        if (!pa) return 1;   // 게시일 정보가 없는 공고는 뒤로
        if (!pb) return -1;
        if (mode === 'new') return pa < pb ? 1 : -1;
        return pa < pb ? -1 : 1;
      });
    }
    arr.forEach(function (card) { list.insertBefore(card, noResult); });
  }
  var sortButtons = Array.prototype.slice.call(document.querySelectorAll('.sort button'));
  sortButtons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      sortButtons.forEach(function (b) { b.className = ''; });
      btn.className = 'on';
      applySort(btn.getAttribute('data-sort'));
    });
  });

  if (!input) return;
  input.addEventListener('input', function () {
    var query = input.value.trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (card) {
      var haystack = card.getAttribute('data-search') || '';
      var hit = !query || haystack.indexOf(query) !== -1;
      card.style.display = hit ? '' : 'none';
      if (hit) shown++;
    });
    if (noResult) {
      noResult.style.display = (shown === 0 && cards.length > 0) ? '' : 'none';
    }
  });
})();
</script>
</body>
</html>
"""


def _badge_html(bid):
    if bid["closed"]:
        return '<span class="badge closed">마감</span>'
    if bid["dday"] is None:
        return '<span class="badge none">일정 미정</span>'
    if bid["dday"] <= 0:
        return '<span class="badge urgent">D-DAY</span>'
    if bid["dday"] <= 3:
        return '<span class="badge urgent">D-%d</span>' % bid["dday"]
    return '<span class="badge">D-%d</span>' % bid["dday"]


def build_html(bids, now):
    esc = html.escape
    cards = []
    for bid in bids:
        if bid["dmnd_org"] and bid["dmnd_org"] != bid["ntce_org"]:
            org_line = "공고 %s · 수요 %s" % (esc(bid["ntce_org"]), esc(bid["dmnd_org"]))
        else:
            org_line = esc(bid["ntce_org"]) or "기관 정보 없음"
        search_blob = esc(" ".join(
            [bid["name"], bid["ntce_org"], bid["dmnd_org"]]).lower())
        posted = ('<span class="posted">%s 게시</span>' % esc(bid["posted"])) if bid["posted"] else ""
        card_class = "card closed" if bid["closed"] else "card"
        close_iso = bid["close_dt"].isoformat() if bid["close_dt"] else ""
        cards.append(
            '    <article class="%s" data-search="%s" data-close="%s" data-posted="%s">\n'
            '      <div class="card-top">%s%s</div>\n'
            '      <h2><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></h2>\n'
            '      <div class="org">%s</div>\n'
            '      <div class="rows">\n'
            '        <div><span>입찰마감</span><b>%s</b></div>\n'
            '        <div><span>예산</span><b>%s</b></div>\n'
            '      </div>\n'
            '    </article>' % (
                card_class, search_blob, close_iso, bid["posted_iso"],
                _badge_html(bid), posted,
                esc(bid["url"], quote=True), esc(bid["name"]),
                org_line, esc(bid["close_str"]), esc(bid["amount"])))

    if cards:
        content = "\n".join(cards)
    else:
        content = ('    <div class="notice">최근 %d일간 조건에 맞는 공고가 없습니다.<br>'
                   '키워드 설정(INCLUDE_KEYWORDS)을 조정해 보세요.</div>' % DAYS_BACK)

    return (HTML_TEMPLATE
            .replace("__UPDATED__", esc(now.strftime("%Y-%m-%d %H:%M")))
            .replace("__DAYS__", str(DAYS_BACK))
            .replace("__COUNT__", str(len(bids)))
            .replace("__CONTENT__", content))


# ============================================================================
# 데모 데이터 (--demo)
# ============================================================================

def demo_items(now):
    """API 호출 없이 레이아웃 확인용 샘플 6건 (D-day 케이스 다양화)."""
    def close_at(days, hour=17):
        return (now + datetime.timedelta(days=days)).replace(
            hour=hour, minute=0).strftime("%Y-%m-%d %H:%M")

    def posted_at(days_ago):
        return (now - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%d 10:00")

    today_close = now.replace(hour=23, minute=59).strftime("%Y-%m-%d %H:%M")
    return [
        {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000",
         "bidNtceNm": "지역 관광 활성화 실태조사 및 발전방안 연구",
         "ntceInsttNm": "강원특별자치도", "dminsttNm": "강원특별자치도",
         "bidNtceDt": posted_at(1), "bidClseDt": today_close,
         "asignBdgtAmt": "95000000", "bidNtceDtlUrl": "https://www.g2b.go.kr"},
        {"bidNtceNo": "R26BK00000002", "bidNtceOrd": "000",
         "bidNtceNm": "2026년 청년정책 기본계획 수립 연구용역",
         "ntceInsttNm": "세종특별자치시", "dminsttNm": "세종특별자치시",
         "bidNtceDt": posted_at(2), "bidClseDt": close_at(1),
         "asignBdgtAmt": "180000000", "bidNtceDtlUrl": "https://www.g2b.go.kr"},
        {"bidNtceNo": "R26BK00000003", "bidNtceOrd": "001",
         "bidNtceNm": "공공데이터 활용 촉진을 위한 정책 연구용역 (재공고)",
         "ntceInsttNm": "조달청", "dminsttNm": "행정안전부",
         "bidNtceDt": posted_at(3), "bidClseDt": close_at(3),
         "presmptPrce": "68181818", "bidNtceDtlUrl": "https://www.g2b.go.kr"},
        {"bidNtceNo": "R26BK00000004", "bidNtceOrd": "000",
         "bidNtceNm": "중장기 재정운용계획 수립 타당성 조사연구",
         "ntceInsttNm": "한국지방행정연구원", "dminsttNm": "전남광주통합특별시",
         "bidNtceDt": posted_at(4), "bidClseDt": close_at(12),
         "asignBdgtAmt": "250000000", "presmptPrce": "227272727",
         "bidNtceDtlUrl": "https://www.g2b.go.kr"},
        {"bidNtceNo": "R26BK00000005", "bidNtceOrd": "000",
         "bidNtceNm": "탄소중립 이행 로드맵 수립 컨설팅",
         "ntceInsttNm": "환경부", "dminsttNm": "",
         "bidNtceDt": posted_at(1), "bidClseDt": "",
         "bidNtceDtlUrl": "https://www.g2b.go.kr"},
        {"bidNtceNo": "R26BK00000006", "bidNtceOrd": "000",
         "bidNtceNm": "노인복지시설 운영 실태조사 학술연구용역",
         "ntceInsttNm": "보건복지부", "dminsttNm": "한국보건사회연구원",
         "bidNtceDt": posted_at(5), "bidClseDt": close_at(-2),
         "asignBdgtAmt": "120000000", "bidNtceDtlUrl": "https://www.g2b.go.kr"},
    ]


# ============================================================================
# 메인
# ============================================================================

def main(argv):
    demo_mode = "--demo" in argv
    now = datetime.datetime.now(KST)  # Actions 러너는 UTC → 반드시 KST로 계산

    try:
        if demo_mode:
            print("[데모 모드] API 호출 없이 샘플 데이터로 HTML을 생성합니다.")
            raw_items = demo_items(now)
        else:
            service_key = os.environ.get("SERVICE_KEY", "").strip()
            if not service_key:
                print("오류: 환경변수 SERVICE_KEY가 설정되어 있지 않습니다.")
                print("GitHub 저장소의 Settings → Secrets and variables → Actions 에서")
                print("이름 'SERVICE_KEY'로 공공데이터포털 일반 인증키(Decoding)를 등록하세요.")
                return 1
            raw_items = fetch_bids(service_key, now)

        selected = filter_bids(raw_items)
        bids = prepare_bids(selected, now)
        document = build_html(bids, now)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(document)
        print("완료: %s 생성 (표시 공고 %d건, 기준 시각 %s KST)"
              % (OUTPUT_FILE, len(bids), now.strftime("%Y-%m-%d %H:%M")))
        return 0

    except ApiError as e:
        print("실패: %s" % e)
        return 1
    except Exception:
        import traceback
        print("실패: 예상하지 못한 오류가 발생했습니다. 아래 상세 내용을 확인하세요.")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
