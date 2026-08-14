#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
연구용역 알리미 — Gmail 메일 발송기

매일 10시(KST)에 GitHub Actions(notify.yml)가 실행합니다.
narajangteo.py와 같은 조건으로 공고를 수집한 뒤, 이전에 본 적 없는
'새 공고'만 골라 Gmail로 발송합니다. 새 공고가 없으면 메일을 보내지 않습니다.

필요한 저장소 Secrets:
  SERVICE_KEY        공공데이터포털 인증키 (대시보드와 공용)
  GMAIL_ADDRESS      보내고 받을 Gmail 주소
  GMAIL_APP_PASSWORD Google 앱 비밀번호 16자리 (2단계 인증 필요)
                     발급: https://myaccount.google.com/apppasswords

동작 방식:
- data/seen.json 에 이미 안내한 공고번호를 기록해 두고, 여기 없는 공고만 발송
- 첫 실행은 현재 공고 전체를 '본 것'으로 초기화만 하고 메일을 보내지 않음
  (첫날부터 수백 건이 쏟아지는 것을 막기 위함)
- 수동 실행에서 force를 켜면 새 공고가 없어도 최신 5건으로 테스트 메일 발송
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
STATE_KEEP_DAYS = 30          # 이 일수보다 오래된 기록은 정리 (수집기간 5일보다 넉넉하면 됨)
MAIL_MAX_ITEMS = 50           # 한 통에 싣는 최대 공고 수 (Gmail 102KB 본문 잘림 방지)
DASHBOARD_URL = "https://changwoong-moon.github.io/research-bid-alert/"
MAIL_TITLE = "연구용역 알리미"


def load_state():
    """{공고번호: 처음 본 날짜} dict를 반환. 파일이 없으면 None (첫 실행)."""
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


def build_email_html(bids, now, omitted=0):
    """새 공고 목록을 Gmail에서 잘 보이는 인라인 스타일 HTML로."""
    esc = html.escape
    rows = []
    for b in bids:
        if b["closed"]:
            dday = '<span style="color:#8a8fa3">마감</span>'
        elif b["dday"] is None:
            dday = '<span style="color:#8a8fa3">일정 미정</span>'
        elif b["dday"] <= 3:
            dday = '<span style="color:#d3273e;font-weight:700">D-%s</span>' % (
                "DAY" if b["dday"] <= 0 else b["dday"])
        else:
            dday = '<span style="color:#2456c9;font-weight:700">D-%d</span>' % b["dday"]
        org = esc(b["ntce_org"])
        if b["dmnd_org"] and b["dmnd_org"] != b["ntce_org"]:
            org += " · 수요 " + esc(b["dmnd_org"])
        rows.append(
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
            % (dday, esc(b["posted"] or "날짜 미상"),
               esc(b["url"], quote=True), esc(b["name"]),
               org, esc(b["close_str"]), esc(b["amount"])))

    return (
        '<div style="max-width:640px;margin:0 auto;'
        'font-family:\'Apple SD Gothic Neo\',\'Malgun Gothic\',sans-serif;color:#1c2333">'
        '<h2 style="font-size:18px;margin:0 0 4px">%s</h2>'
        '<p style="font-size:13px;color:#68718a;margin:0 0 16px">'
        '%s 기준 · 새로 올라온 공고 <b>%d건</b></p>'
        '%s%s'
        '<p style="font-size:12px;color:#8a8fa3;margin:18px 0 0;line-height:1.7">'
        '전체 공고 보기: <a href="%s" style="color:#2456c9">%s</a><br>'
        '이 메일은 새 공고가 있는 날만 발송됩니다. '
        '자료 출처: 조달청 나라장터 입찰공고정보서비스</p>'
        '</div>'
        % (MAIL_TITLE, now.strftime("%Y-%m-%d %H:%M"), len(bids) + omitted,
           "".join(rows),
           ('<p style="font-size:13px;color:#454c63">외 %d건은 대시보드에서 확인하세요.</p>'
            % omitted) if omitted else "",
           DASHBOARD_URL, DASHBOARD_URL))


def send_gmail(subject, html_body):
    addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    # 앱 비밀번호는 'abcd efgh ...' 처럼 공백 포함으로 복사되는 경우가 많아 제거
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not addr or not password:
        print("오류: GMAIL_ADDRESS / GMAIL_APP_PASSWORD 시크릿이 등록되지 않았습니다.")
        print("저장소 Settings → Secrets and variables → Actions 에서 등록하세요.")
        print("(등록 전까지 상태를 갱신하지 않으므로, 등록 후 실행되면 그동안 쌓인")
        print(" 새 공고가 한꺼번에 발송됩니다.)")
        sys.exit(1)

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = formataddr((str(Header(MAIL_TITLE, "utf-8")), addr))
    msg["To"] = addr
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
        server.login(addr, password)
        server.sendmail(addr, [addr], msg.as_string())
    print("메일 발송 완료: %s → %s" % (subject, addr))


def main():
    force = os.environ.get("NOTIFY_FORCE", "") == "1"
    now = datetime.datetime.now(nj.KST)
    today = now.strftime("%Y-%m-%d")

    service_key = os.environ.get("SERVICE_KEY", "").strip()
    if not service_key:
        print("오류: SERVICE_KEY 시크릿이 없습니다.")
        return 1

    raw = nj.fetch_bids(service_key, now)
    selected = nj.filter_bids(raw)

    state = load_state()
    if state is None:
        # 첫 실행: 현재 공고 전체를 본 것으로 기록만 하고 발송하지 않음
        state = {str(it.get("bidNtceNo") or ""): today for it in selected}
        state.pop("", None)
        save_state(state, today)
        print("첫 실행: 공고 %d건을 기준선으로 저장했습니다. 다음 실행부터 새 공고만 메일로 발송합니다."
              % len(state))
        if not force:
            return 0

    # 공고번호가 없는 비정상 항목은 재발송이 반복될 수 있어 알림 대상에서 제외
    new_items = [it for it in selected
                 if str(it.get("bidNtceNo") or "").strip()
                 and str(it.get("bidNtceNo") or "").strip() not in state]

    if not new_items and not force:
        print("새 공고 없음 — 메일을 보내지 않습니다. (현재 %d건 모두 기존 안내분)"
              % len(selected))
        return 0

    test_mode = False
    omitted = 0
    if new_items:
        bids = nj.prepare_bids(new_items, now)
        subject = "[%s] 새 공고 %d건 (%s)" % (MAIL_TITLE, len(bids), now.strftime("%m/%d"))
        if len(bids) > MAIL_MAX_ITEMS:
            omitted = len(bids) - MAIL_MAX_ITEMS
            bids = bids[:MAIL_MAX_ITEMS]
    else:  # force 테스트: 최신 5건 샘플
        test_mode = True
        bids = nj.prepare_bids(selected, now)[:5]
        subject = "[%s] 테스트 메일 — 발송 설정 정상 (%s)" % (MAIL_TITLE, now.strftime("%m/%d"))
        print("테스트 모드: 새 공고가 없어 최신 %d건으로 테스트 메일을 보냅니다." % len(bids))

    send_gmail(subject, build_email_html(bids, now, omitted))

    if not test_mode:
        for it in new_items:
            no = str(it.get("bidNtceNo") or "")
            if no:
                state[no] = today
        save_state(state, today)
        print("상태 갱신: 새 공고 %d건 기록 (총 %d건 추적 중)" % (len(new_items), len(state)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except nj.ApiError as e:
        print("실패: %s" % e)
        sys.exit(1)
