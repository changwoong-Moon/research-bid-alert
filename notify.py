#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
연구용역 알리미 — Gmail 메일 발송기

update.yml 워크플로가 narajangteo.py(수집·대시보드 생성) 바로 다음에 실행합니다.
API를 다시 부르지 않고 data/bids.json 저장분만 읽습니다.

발송 규칙 (하루 1통 원칙):
- 그날 처음 성공한 실행에서 '안내 메일' 1통을 보냅니다.
    · 새 공고가 있으면  → 새 공고 목록 (+ 마감 임박 공고)
    · 새 공고가 없으면  → "오늘 새 공고 없음" 짧은 메일 (+ 마감 임박 공고)
  같은 날 나중 실행에서 새 공고가 더 생기면 다음 날 아침 메일에 포함합니다.
- 수집이 계속 실패해 정오(FAILURE_MAIL_HOUR)까지 안내 메일을 못 보냈으면
  '수집 실패' 알림을 하루 1통 보냅니다.
- 수동 실행에서 force를 켜면 위 규칙과 무관하게 테스트 메일을 보냅니다.

필요한 저장소 Secrets:
  GMAIL_ADDRESS      보내고 받을 Gmail 주소
  GMAIL_APP_PASSWORD Google 앱 비밀번호 16자리 (2단계 인증 필요)
                     발급: https://myaccount.google.com/apppasswords

기록 파일:
  data/seen.json        이미 안내한 공고번호 → 처음 안내한 날짜
  data/mail_state.json  마지막 안내 메일 날짜, 마지막 실패 알림 날짜

로컬 확인: python notify.py --dry-run   (메일을 보내지 않고 내용만 출력, 기록도 안 바꿈)
"""

import datetime
import html
import json
import os
import smtplib
import ssl
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import narajangteo as nj

STATE_FILE = os.path.join("data", "seen.json")
MAIL_STATE_FILE = os.path.join("data", "mail_state.json")
STATE_KEEP_DAYS = 30          # 이 일수보다 오래된 안내 기록은 정리 (보관기간 20일보다 넉넉하면 됨)
MAIL_MAX_ITEMS = 50           # 한 통에 싣는 최대 공고 수 (Gmail 102KB 본문 잘림 방지)
CLOSING_SOON_DAYS = 3         # '마감 임박' 으로 함께 알려줄 기준(일)
CLOSING_SOON_MAX = 5          # 마감 임박 공고 최대 표시 수
DIGEST_HOUR = 7               # 이 시각(KST) 이후의 첫 성공 실행에서 하루 1통 발송
FAILURE_MAIL_HOUR = 12        # 이 시각(KST)까지 수집이 계속 실패하면 실패 알림 1통
DASHBOARD_URL = "https://changwoong-moon.github.io/research-bid-alert/"
MAIL_TITLE = "연구용역 알리미"


# ============================================================================
# 기록 파일
# ============================================================================

def load_state():
    """{공고번호: 처음 안내한 날짜} dict를 반환. 파일이 없으면 None (첫 실행)."""
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):  # 옛 형식(리스트) 호환
        return {no: "" for no in data}
    return data


def save_state(state, today_str):
    """오래된 항목을 정리하고 저장."""
    cutoff = (datetime.datetime.strptime(today_str, "%Y-%m-%d")
              - datetime.timedelta(days=STATE_KEEP_DAYS)).strftime("%Y-%m-%d")
    pruned = {no: d for no, d in state.items() if not d or d >= cutoff}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=1)


def load_mail_state():
    if not os.path.exists(MAIL_STATE_FILE):
        return {}
    try:
        with open(MAIL_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_mail_state(mail_state):
    os.makedirs(os.path.dirname(MAIL_STATE_FILE), exist_ok=True)
    with open(MAIL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(mail_state, f, ensure_ascii=False, indent=1)


# ============================================================================
# 메일 본문
# ============================================================================

def _dday_html(b):
    if b["closed"]:
        return '<span style="color:#8a8fa3">마감</span>'
    if b["dday"] is None:
        return '<span style="color:#8a8fa3">일정 미정</span>'
    if b["dday"] <= 3:
        return '<span style="color:#d3273e;font-weight:700">D-%s</span>' % (
            "DAY" if b["dday"] <= 0 else b["dday"])
    return '<span style="color:#2456c9;font-weight:700">D-%d</span>' % b["dday"]


def _bid_card_html(b):
    esc = html.escape
    org = esc(b["ntce_org"])
    if b["dmnd_org"] and b["dmnd_org"] != b["ntce_org"]:
        org += " · 수요 " + esc(b["dmnd_org"])
    return (
        '<div style="border:1px solid #e2e6ee;border-radius:10px;'
        'padding:14px 16px;margin:0 0 10px">'
        '<div style="font-size:13px;margin-bottom:6px">%s'
        '<span style="color:#8a8fa3"> · %s 게시</span></div>'
        '<div style="font-size:15px;font-weight:700;line-height:1.45;margin-bottom:6px">'
        '<a href="%s" style="color:#1c2333;text-decoration:none">%s</a></div>'
        '<div style="font-size:13px;color:#68718a;margin-bottom:4px">%s</div>'
        '<div style="font-size:13px;color:#454c63">입찰마감 <b>%s</b>'
        ' &nbsp;·&nbsp; 예산 <b>%s</b></div>'
        '</div>'
        % (_dday_html(b), esc(b["posted"] or "날짜 미상"),
           esc(b["url"], quote=True), esc(b["name"]),
           org, esc(b["close_str"]), esc(b["amount"])))


def _closing_soon_html(closing):
    if not closing:
        return ""
    return ('<h3 style="font-size:15px;margin:22px 0 8px">⏰ 마감 임박 (%d일 이내) %d건</h3>%s'
            % (CLOSING_SOON_DAYS, len(closing), "".join(_bid_card_html(b) for b in closing)))


def _wrap_html(title, sub, body):
    return (
        '<div style="max-width:640px;margin:0 auto;'
        'font-family:\'Apple SD Gothic Neo\',\'Malgun Gothic\',sans-serif;color:#1c2333">'
        '<h2 style="font-size:18px;margin:0 0 4px">%s</h2>'
        '<p style="font-size:13px;color:#68718a;margin:0 0 16px">%s</p>'
        '%s'
        '<p style="font-size:12px;color:#8a8fa3;margin:18px 0 0;line-height:1.7">'
        '전체 공고 보기: <a href="%s" style="color:#2456c9">%s</a><br>'
        '매일 아침 1통씩 발송됩니다(새 공고가 없는 날은 "없음"으로 알려드립니다). '
        '자료 출처: 조달청 나라장터 입찰공고정보서비스</p>'
        '</div>' % (title, sub, body, DASHBOARD_URL, DASHBOARD_URL))


def build_new_bids_html(bids, now, omitted, closing):
    body = "".join(_bid_card_html(b) for b in bids)
    if omitted:
        body += ('<p style="font-size:13px;color:#454c63">외 %d건은 대시보드에서 확인하세요.</p>'
                 % omitted)
    body += _closing_soon_html(closing)
    return _wrap_html(MAIL_TITLE,
                      "%s 기준 · 새로 올라온 공고 <b>%d건</b>"
                      % (now.strftime("%Y-%m-%d %H:%M"), len(bids) + omitted),
                      body)


def build_no_news_html(now, total_open, closing):
    body = ('<div style="border:1px solid #e2e6ee;border-radius:10px;padding:16px;'
            'font-size:14px;color:#454c63;line-height:1.6">'
            '오늘 새로 올라온 연구용역 공고가 없습니다.<br>'
            '현재 대시보드에는 마감 전 공고 <b>%d건</b>이 있습니다.</div>' % total_open)
    body += _closing_soon_html(closing)
    return _wrap_html(MAIL_TITLE, "%s 기준" % now.strftime("%Y-%m-%d %H:%M"), body)


def build_failure_html(now, meta):
    last_ok = nj.parse_iso(meta.get("last_success"))
    body = ('<div style="border:1px solid #f5c98a;background:#fff4e5;border-radius:10px;'
            'padding:16px;font-size:14px;color:#5c3800;line-height:1.7">'
            '오늘은 나라장터 공고 수집이 계속 실패하고 있습니다.<br>'
            '마지막 시도 %s — %s<br>'
            '대시보드는 <b>%s</b>에 수집한 저장분을 표시하고 있습니다.<br>'
            '다음 자동 갱신 때 다시 시도하며, 복구되면 그동안의 새 공고를 정리해 보내드립니다.</div>'
            % (html.escape(now.strftime("%H:%M")),
               html.escape(nj._short_error(meta.get("last_error"), 120) or "원인 미상"),
               html.escape(nj._fmt_dt(last_ok))))
    return _wrap_html(MAIL_TITLE + " — 수집 실패 알림", "%s 기준" % now.strftime("%Y-%m-%d %H:%M"), body)


# ============================================================================
# 발송
# ============================================================================

def send_gmail(subject, html_body, dry_run=False):
    if dry_run:
        print("\n----- [DRY-RUN] 발송 예정 메일 -----")
        print("제목:", subject)
        print("본문(HTML 길이 %d자):" % len(html_body))
        print(_html_to_text(html_body)[:2500])
        print("----- [DRY-RUN] 끝 -----\n")
        return

    addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    # 앱 비밀번호는 'abcd efgh ...' 처럼 공백 포함으로 복사되는 경우가 많아 제거
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not addr or not password:
        print("오류: GMAIL_ADDRESS / GMAIL_APP_PASSWORD 시크릿이 등록되지 않았습니다.")
        print("저장소 Settings → Secrets and variables → Actions 에서 등록하세요.")
        sys.exit(1)

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = formataddr((str(Header(MAIL_TITLE, "utf-8")), addr))
    msg["To"] = addr
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=60) as server:
        server.login(addr, password)
        server.sendmail(addr, [addr], msg.as_string())
    print("메일 발송 완료: %s → %s" % (subject, addr))


def _html_to_text(s):
    import re
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</(div|p|h2|h3)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


# ============================================================================
# 메인
# ============================================================================

def main(argv):
    dry_run = "--dry-run" in argv
    force = os.environ.get("NOTIFY_FORCE", "") == "1"
    now = datetime.datetime.now(nj.KST)
    today = now.strftime("%Y-%m-%d")

    cache = nj.load_cache()
    meta = cache["meta"]
    items = cache["items"]
    last_attempt = nj.parse_iso(meta.get("last_attempt"))
    fetch_ok = bool(meta.get("last_attempt_ok")) and bool(last_attempt) \
        and last_attempt.strftime("%Y-%m-%d") == today
    print("저장분 %d건 · 이번 수집 %s · 마지막 수집 성공 %s"
          % (len(items), "성공" if fetch_ok else "실패/미실행",
             nj._fmt_dt(nj.parse_iso(meta.get("last_success")))))

    if not items:
        print("저장분이 비어 있어 안내할 공고가 없습니다. (첫 수집이 성공한 뒤부터 발송)")
        return 0

    selected = nj.filter_bids(items)
    all_bids = nj.prepare_bids(selected, now)
    open_bids = [b for b in all_bids if not b["closed"]]
    closing = [b for b in open_bids
               if b["dday"] is not None and 0 <= b["dday"] <= CLOSING_SOON_DAYS][:CLOSING_SOON_MAX]

    state = load_state()
    mail_state = load_mail_state()
    if state is None:
        # 첫 실행: 현재 공고 전체를 본 것으로 기록만 하고 발송하지 않음 (첫날 폭주 방지)
        state = {nj.item_key(it): today for it in selected}
        if not dry_run:
            save_state(state, today)
        print("첫 실행: 공고 %d건을 기준선으로 저장했습니다. 다음 실행부터 새 공고만 안내합니다."
              % len(state))
        if not force:
            return 0

    new_raw = [it for it in selected if nj.item_key(it) not in state]
    new_bids = nj.prepare_bids(new_raw, now) if new_raw else []
    digest_sent_today = mail_state.get("last_digest_date") == today

    def send_new_bids():
        bids = new_bids
        omitted = 0
        if len(bids) > MAIL_MAX_ITEMS:
            omitted = len(bids) - MAIL_MAX_ITEMS
            bids = bids[:MAIL_MAX_ITEMS]
        subject = "[%s] 새 공고 %d건 (%s)" % (MAIL_TITLE, len(new_bids), now.strftime("%m/%d"))
        send_gmail(subject, build_new_bids_html(bids, now, omitted, closing), dry_run)
        for it in new_raw:
            state[nj.item_key(it)] = today
        mail_state["last_digest_date"] = today
        if not dry_run:
            save_state(state, today)
            save_mail_state(mail_state)
        print("상태 갱신: 새 공고 %d건 안내 기록 (총 %d건 추적 중)" % (len(new_raw), len(state)))

    # ── 수동 테스트 (force) ──
    if force:
        if new_bids:
            print("테스트 모드: 새 공고 %d건이 있어 실제 안내 메일을 보냅니다." % len(new_bids))
            send_new_bids()
        else:
            sample = open_bids[:5] or all_bids[:5]
            subject = "[%s] 테스트 메일 — 발송 설정 정상 (%s)" % (MAIL_TITLE, now.strftime("%m/%d"))
            print("테스트 모드: 새 공고가 없어 최신 %d건으로 테스트 메일을 보냅니다." % len(sample))
            send_gmail(subject, build_new_bids_html(sample, now, 0, closing), dry_run)
        return 0

    # ── 하루 1통 안내 메일 ──
    if new_bids and not digest_sent_today:
        send_new_bids()
        return 0

    if new_bids and digest_sent_today:
        print("새 공고 %d건이 있지만 오늘 안내 메일은 이미 발송했습니다 → 내일 아침 메일에 포함합니다."
              % len(new_bids))
        return 0

    if not new_bids and not digest_sent_today and fetch_ok and now.hour >= DIGEST_HOUR:
        subject = "[%s] 오늘 새 공고 없음 (%s)" % (MAIL_TITLE, now.strftime("%m/%d"))
        send_gmail(subject, build_no_news_html(now, len(open_bids), closing), dry_run)
        mail_state["last_digest_date"] = today
        if not dry_run:
            save_mail_state(mail_state)
        return 0

    # ── 수집 실패가 정오까지 이어지면 실패 알림 1통 ──
    if (not fetch_ok and not digest_sent_today and now.hour >= FAILURE_MAIL_HOUR
            and mail_state.get("last_failure_mail_date") != today):
        subject = "[%s] ⚠ 오늘 수집 실패 (%s)" % (MAIL_TITLE, now.strftime("%m/%d"))
        send_gmail(subject, build_failure_html(now, meta), dry_run)
        mail_state["last_failure_mail_date"] = today
        if not dry_run:
            save_mail_state(mail_state)
        return 0

    if digest_sent_today:
        print("오늘 안내 메일은 이미 발송했습니다. 발송 없음.")
    elif not fetch_ok:
        print("이번 수집이 실패해 안내 메일을 보류합니다 (다음 실행에서 재시도).")
    else:
        print("발송 조건에 해당하지 않아 메일을 보내지 않습니다. (현재 시각 %s)" % now.strftime("%H:%M"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except nj.ApiError as e:
        print("실패: %s" % e)
        sys.exit(1)
