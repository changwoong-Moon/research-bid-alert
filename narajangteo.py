#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
나라장터 연구용역 공고 대시보드 생성기 (연구용역 알리미)

조달청 나라장터 입찰공고정보서비스(공공데이터포털, 데이터셋 15129394)에서
용역 입찰공고를 수집해 키워드로 선별한 뒤, 단일 HTML 대시보드
(research_bids.html)를 생성합니다.

동작 방식 (2026-08-16 개편):
- 선별된 공고를 data/bids.json 에 누적 저장합니다.
- 매 실행은 최근 FETCH_DAYS일치만 새로 받아 저장분에 합칩니다.
  (마지막 성공 이후 공백이 있으면 그만큼 자동으로 더 거슬러 받습니다.)
- 나라장터 API가 응답하지 않아도 저장분으로 대시보드를 만들고 상단에
  "갱신 실패" 배너를 띄웁니다. 대시보드가 통째로 멈추는 일이 없습니다.
- 표시·보관 기간은 DAYS_BACK일. 그보다 오래된 공고는 저장분에서 지웁니다.

- 실행: python narajangteo.py            (환경변수 SERVICE_KEY 필요)
- 전량: python narajangteo.py --full     (DAYS_BACK일치 전량 재수집. 키워드를 바꾼 뒤 한 번)
- 데모: python narajangteo.py --demo     (API 호출 없이 샘플 데이터로 레이아웃 확인)

표준 라이브러리만 사용합니다 (별도 pip install 불필요).
"""

import datetime
import hashlib
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

# 제외 키워드의 예외: 아래 낱말은 제외 판정에서 빼고 봅니다.
# 예) "청소" 때문에 "청소년 정책 연구"가 걸러지는 일을 막음 (2026-08-16 발견)
EXCLUDE_EXCEPTIONS = [
    "청소년",
]

# 표시·보관 기간: 오늘부터 거슬러 올라갈 일수 (한국시간 기준)
DAYS_BACK = 20

# 매 실행에서 새로 받아오는 기간(일). 나머지는 data/bids.json 저장분을 씁니다.
# 공고 게시일 기준이라 2~3일이면 새 공고·변경·취소를 모두 잡습니다.
FETCH_DAYS = 3

# 생성할 결과 파일 이름 (GitHub Actions 워크플로가 이 이름을 사용하므로
# 바꾸면 update.yml의 cp 명령도 같이 바꿔야 합니다)
OUTPUT_FILE = "research_bids.html"

# 선별된 공고의 누적 저장 파일 (저장소에 커밋됨)
CACHE_FILE = os.path.join("data", "bids.json")

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
FETCH_DEADLINE = 12 * 60       # 초. 평소(증분) 수집이 이 시간을 넘기면 포기하고 저장분으로 대시보드를 만듦
FETCH_DEADLINE_FULL = 25 * 60  # 초. 전량(--full) 또는 저장분이 없는 첫 수집의 상한
FAILED_MARKER = "fetch_failed"  # 수집 실패 시 만드는 표시 파일 (워크플로가 마지막에 확인)

# 저장분에 남기는 항목 (용량 절약: 필요한 필드만)
KEEP_FIELDS = (
    "bidNtceNo", "bidNtceOrd", "bidNtceNm", "ntceKindNm", "ntceInsttNm", "dminsttNm",
    "bidNtceDt", "rgstDt", "bidClseDt", "bidNtceDtlUrl", "bidNtceUrl",
    "asignBdgtAmt", "presmptPrce", "bdgtAmt",
)

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


_fetch_deadline = None  # fetch_bids가 설정하는 수집 마감 시각 (time.monotonic 기준)


def _past_deadline():
    return _fetch_deadline is not None and time.monotonic() > _fetch_deadline


def request_api(url):
    """단일 URL을 재시도 포함 호출하고 파싱된 dict를 반환."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        if _past_deadline():
            raise ApiError("수집 시간 상한을 넘겨 중단했습니다. 나라장터 API가 매우 느린 상태입니다.")
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


def fetch_bids(service_key, now, begin=None, deadline=None):
    """조회기간(begin~now) 내 용역 공고 전량 수집 (totalCount 기반 페이지네이션).

    begin을 주지 않으면 DAYS_BACK일 전 0시부터 받습니다.
    deadline(초)을 넘기면 ApiError로 중단합니다 (기본 FETCH_DEADLINE).
    """
    global _fetch_deadline
    key = normalize_service_key(service_key)
    if begin is None:
        begin = (now - datetime.timedelta(days=DAYS_BACK)).replace(
            hour=0, minute=0, second=0, microsecond=0)
    print("조회 기간(KST): %s ~ %s" % (begin.strftime("%Y-%m-%d %H:%M"),
                                       now.strftime("%Y-%m-%d %H:%M")))
    _fetch_deadline = time.monotonic() + (deadline or FETCH_DEADLINE)
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
    _fetch_deadline = None
    print("수집 완료: 원본 공고 %d건" % len(all_items))
    return all_items


# ============================================================================
# 필터링 · 정규화
# ============================================================================

def name_matches(name):
    """공고명이 INCLUDE 키워드 중 하나를 포함하고 EXCLUDE 키워드는 포함하지 않는가.

    EXCLUDE 판정 때는 EXCLUDE_EXCEPTIONS 낱말을 지운 문자열로 봅니다.
    """
    if not name:
        return False
    if not any(k in name for k in INCLUDE_KEYWORDS):
        return False
    masked = name
    for phrase in EXCLUDE_EXCEPTIONS:
        masked = masked.replace(phrase, " ")
    return not any(k in masked for k in EXCLUDE_KEYWORDS)


def item_key(item):
    """공고를 식별하는 키. 공고번호가 없으면 공고명+게시일시로 만든 대체 키."""
    no = str(item.get("bidNtceNo") or "").strip()
    if no:
        return no
    basis = "%s|%s" % (str(item.get("bidNtceNm") or "").strip(),
                       str(item.get("bidNtceDt") or item.get("rgstDt") or "").strip())
    return "noid:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def dedupe_latest(items):
    """공고번호 기준으로 차수(bidNtceOrd)가 가장 큰 최신 건만 남김. 입력 순서 유지."""
    best = {}
    for item in items:
        key = item_key(item)
        ord_new = to_int(item.get("bidNtceOrd"), 0)
        if key not in best or ord_new >= to_int(best[key].get("bidNtceOrd"), 0):
            best[key] = item  # 재공고/변경공고/취소공고는 차수가 큰 최신 건만 유지
    return list(best.values())


def filter_bids(items):
    """표시용 선별: 공고번호 중복 제거(차수 큰 것 유지) → 취소공고 제거 → 공고명 키워드 필터.

    중복 제거를 먼저 해야, 원공고 뒤에 올라온 취소공고가 원공고를 덮어 화면에서 사라집니다.
    """
    latest = dedupe_latest(items)
    result = []
    for item in latest:
        if "취소" in str(item.get("ntceKindNm") or ""):  # 최신 차수가 취소공고면 제외
            continue
        if not name_matches(str(item.get("bidNtceNm") or "").strip()):
            continue
        result.append(item)
    print("키워드 선별 결과: %d건 (중복 제거 전 %d건)" % (len(result), len(items)))
    return result


# ============================================================================
# 누적 저장 (data/bids.json)
# ============================================================================

def _empty_cache():
    return {"meta": {}, "items": []}


def load_cache(path=CACHE_FILE):
    """저장분을 읽어 {"meta": {...}, "items": [...]} 로 반환. 없거나 깨졌으면 빈 구조."""
    if not os.path.exists(path):
        return _empty_cache()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print("[주의] 저장분(%s)을 읽지 못해 빈 상태로 시작합니다: %s" % (path, e))
        return _empty_cache()
    if not isinstance(data, dict):
        return _empty_cache()
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    items = [x for x in (data.get("items") or []) if isinstance(x, dict)]
    return {"meta": meta, "items": items}


def save_cache(cache, path=CACHE_FILE):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)


def _trim(item):
    out = {k: str(item.get(k) or "") for k in KEEP_FIELDS if item.get(k) not in (None, "")}
    if "_first_seen" in item:
        out["_first_seen"] = item["_first_seen"]
    return out


def posted_date(item):
    """게시일(bidNtceDt→rgstDt→_first_seen) 을 date로. 전혀 없으면 None."""
    dt = parse_dt(item.get("bidNtceDt")) or parse_dt(item.get("rgstDt"))
    if dt:
        return dt.date()
    fs = str(item.get("_first_seen") or "")
    try:
        return datetime.date.fromisoformat(fs[:10]) if fs else None
    except ValueError:
        return None


def merge_items(cached, fetched, today_str):
    """새로 받은 공고를 저장분에 합침.

    - 키워드에 맞는 공고, 그리고 이미 저장된 공고의 후속 차수(변경·취소 등)만 받아들임
    - 같은 공고번호는 차수가 큰 것이 이김 (동일 차수면 새로 받은 것으로 갱신)
    - 처음 본 날짜(_first_seen)는 보존
    """
    by_key = {item_key(it): it for it in cached}
    added = updated = 0
    for raw in fetched:
        key = item_key(raw)
        name = str(raw.get("bidNtceNm") or "").strip()
        if key not in by_key and not name_matches(name):
            continue
        item = _trim(raw)
        old = by_key.get(key)
        if old is None:
            item["_first_seen"] = today_str
            by_key[key] = item
            added += 1
        else:
            if to_int(item.get("bidNtceOrd"), 0) < to_int(old.get("bidNtceOrd"), 0):
                continue  # 이미 더 최신 차수를 갖고 있음
            item["_first_seen"] = old.get("_first_seen") or today_str
            by_key[key] = item
            updated += 1
    print("저장분 병합: 신규 %d건, 갱신 %d건 (저장분 총 %d건)" % (added, updated, len(by_key)))
    return list(by_key.values())


def prune_items(items, now):
    """게시일이 DAYS_BACK일보다 오래된 공고를 저장분에서 제거하고 게시일 내림차순으로 정렬."""
    floor = (now - datetime.timedelta(days=DAYS_BACK)).date()
    kept = []
    for it in items:
        d = posted_date(it)
        if d is None or d >= floor:
            kept.append(it)
    kept.sort(key=lambda it: (posted_date(it) or datetime.date.min, item_key(it)), reverse=True)
    if len(kept) != len(items):
        print("보관 기간 지난 공고 %d건 정리 (저장분 %d건 유지)" % (len(items) - len(kept), len(kept)))
    return kept


def compute_fetch_begin(cache, now, full=False):
    """이번 실행에서 받아올 시작 시각을 정함.

    - 전량(--full) 또는 저장분이 비어 있으면 DAYS_BACK일 전부터
    - 평소에는 FETCH_DAYS일 전부터. 다만 마지막 성공 이후 공백이 그보다 길면
      (마지막 성공 전날)부터 받아 빠진 기간을 메움 (DAYS_BACK 이내에서)
    """
    floor = (now - datetime.timedelta(days=DAYS_BACK)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    if full or not cache.get("items"):
        return floor
    begin = (now - datetime.timedelta(days=FETCH_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    last_ok = parse_iso(cache.get("meta", {}).get("last_success"))
    if last_ok:
        catch_up = (last_ok - datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        begin = min(begin, catch_up)
    return max(begin, floor)


def parse_iso(value):
    """ISO 8601 문자열을 aware datetime(KST)으로. 실패하면 None."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


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
.alert{
  background:#fff4e5; border:1px solid #f5c98a; color:#7a4b00; border-radius:12px;
  padding:12px 14px; margin:0 0 14px; font-size:.86rem; line-height:1.55;
  word-break:keep-all; overflow-wrap:anywhere;
}
.alert b{color:#5c3800}
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
    <p class="sub">__UPDATED__ 수집(한국시간) · 최근 __DAYS__일 공고 중 <b>__COUNT__건</b> 표시</p>
__STATUS__
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
    __FOOTER_STATUS__<br>
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

  // 대시보드는 하루 몇 번만 갱신되므로 D-day 배지를 열람 시점 기준으로 KST 재계산
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


def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "기록 없음"


def _short_error(text, limit=90):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def build_status_html(status):
    """상단 배너(갱신 실패 시)와 하단 상태 한 줄을 만듦. status 키:
    collected_at(마지막 수집 성공 시각), degraded(bool), error(str), error_at(datetime)"""
    esc = html.escape
    status = status or {}
    collected_at = status.get("collected_at")
    banner = ""
    if status.get("degraded"):
        banner = (
            '    <div class="alert" role="status">⚠ <b>%s 갱신 실패</b> — %s<br>'
            '아래 목록은 <b>%s</b>에 수집한 저장분입니다. 다음 자동 갱신 때 다시 시도합니다.</div>\n'
            % (esc(_fmt_dt(status.get("error_at"))),
               esc(_short_error(status.get("error"))),
               esc(_fmt_dt(collected_at))))
    footer = "마지막 수집 성공: %s" % esc(_fmt_dt(collected_at))
    if status.get("degraded"):
        footer += " · 마지막 시도: %s (실패)" % esc(_fmt_dt(status.get("error_at")))
    return banner, footer


def build_html(bids, now, status=None):
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

    status = dict(status or {})
    status.setdefault("collected_at", now)
    banner, footer_status = build_status_html(status)
    updated = status.get("collected_at") or now

    return (HTML_TEMPLATE
            .replace("__UPDATED__", esc(updated.strftime("%Y-%m-%d %H:%M")))
            .replace("__DAYS__", str(DAYS_BACK))
            .replace("__COUNT__", str(len(bids)))
            .replace("__STATUS__", banner)
            .replace("__FOOTER_STATUS__", footer_status)
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

def write_output(document):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(document)


def _mark_failed(failed):
    """수집 실패 표시 파일을 만들거나 지움 (워크플로 마지막 단계가 확인)."""
    try:
        if failed:
            with open(FAILED_MARKER, "w", encoding="utf-8") as f:
                f.write("fetch failed\n")
        elif os.path.exists(FAILED_MARKER):
            os.remove(FAILED_MARKER)
    except OSError:
        pass


def main(argv):
    demo_mode = "--demo" in argv
    full_mode = "--full" in argv or os.environ.get("FULL_REFRESH", "").strip() == "1"
    now = datetime.datetime.now(KST)  # Actions 러너는 UTC → 반드시 KST로 계산
    today_str = now.strftime("%Y-%m-%d")

    try:
        if demo_mode:
            print("[데모 모드] API 호출 없이 샘플 데이터로 HTML을 생성합니다. (저장분은 건드리지 않음)")
            selected = filter_bids(demo_items(now))
            bids = prepare_bids(selected, now)
            demo_status = {"collected_at": now}
            if "--demo-fail" in argv:  # 실패 배너 모양 확인용
                demo_status = {"collected_at": now - datetime.timedelta(hours=19),
                               "degraded": True, "error_at": now,
                               "error": "네트워크 오류로 API 호출에 3회 모두 실패했습니다: timed out"}
            write_output(build_html(bids, now, demo_status))
            print("완료: %s 생성 (표시 공고 %d건, 기준 시각 %s KST)"
                  % (OUTPUT_FILE, len(bids), now.strftime("%Y-%m-%d %H:%M")))
            return 0

        service_key = os.environ.get("SERVICE_KEY", "").strip()
        if not service_key:
            print("오류: 환경변수 SERVICE_KEY가 설정되어 있지 않습니다.")
            print("GitHub 저장소의 Settings → Secrets and variables → Actions 에서")
            print("이름 'SERVICE_KEY'로 공공데이터포털 일반 인증키(Decoding)를 등록하세요.")
            return 1

        cache = load_cache()
        meta = cache["meta"]
        items = cache["items"]
        print("저장분: %d건 (마지막 수집 성공: %s)"
              % (len(items), meta.get("last_success") or "없음"))

        begin = compute_fetch_begin(cache, now, full=full_mode)
        bootstrap = full_mode or not items
        if full_mode:
            print("[전량 재수집] 최근 %d일치를 모두 다시 받습니다." % DAYS_BACK)
        elif not items:
            print("[첫 수집] 저장분이 없어 최근 %d일치를 받습니다. 다음부터는 최근 %d일치만 받습니다."
                  % (DAYS_BACK, FETCH_DAYS))
        degraded = False
        error_text = ""
        meta["last_attempt"] = now.isoformat(timespec="seconds")
        try:
            raw_items = fetch_bids(service_key, now, begin,
                                   deadline=FETCH_DEADLINE_FULL if bootstrap else FETCH_DEADLINE)
            items = merge_items(items, raw_items, today_str)
            meta["last_success"] = now.isoformat(timespec="seconds")
            meta["last_attempt_ok"] = True
            meta["last_error"] = ""
            meta["fetch_window"] = "%s ~ %s" % (begin.strftime("%Y-%m-%d %H:%M"),
                                              now.strftime("%Y-%m-%d %H:%M"))
        except ApiError as e:
            degraded = True
            error_text = str(e)
            meta["last_attempt_ok"] = False
            meta["last_error"] = error_text
            meta["last_error_at"] = now.isoformat(timespec="seconds")
            print("수집 실패: %s" % error_text)
            if not items:
                print("저장분도 없어 대시보드를 만들 수 없습니다. 다음 실행에서 다시 시도합니다.")
                save_cache({"meta": meta, "items": []})
                _mark_failed(True)
                return 1
            print("::warning::나라장터 수집 실패 — 저장분 %d건으로 대시보드를 만듭니다." % len(items))

        items = prune_items(items, now)
        save_cache({"meta": meta, "items": items})

        selected = filter_bids(items)  # 저장분에 현재 키워드·취소 여부를 다시 적용
        bids = prepare_bids(selected, now)
        status = {
            "collected_at": parse_iso(meta.get("last_success")),
            "degraded": degraded,
            "error": error_text,
            "error_at": now if degraded else None,
        }
        write_output(build_html(bids, now, status))
        _mark_failed(degraded)
        print("완료: %s 생성 (표시 공고 %d건, 수집 기준 %s, 페이지 생성 %s KST%s)"
              % (OUTPUT_FILE, len(bids), _fmt_dt(status["collected_at"]),
                 now.strftime("%Y-%m-%d %H:%M"), " — 갱신 실패 배너 포함" if degraded else ""))
        return 0

    except Exception:
        import traceback
        print("실패: 예상하지 못한 오류가 발생했습니다. 아래 상세 내용을 확인하세요.")
        traceback.print_exc()
        _mark_failed(True)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
