"""Email 通知（使用 SMTP，預設 Gmail）。

需要的環境變數（在 GitHub 的 Settings → Secrets 設定）：
  SMTP_USER      寄件 Gmail 帳號
  SMTP_PASSWORD  Gmail「應用程式密碼」（16 碼，不是登入密碼）
  MAIL_TO        收件人，多個用逗號分隔（沒填就寄給自己）
選填：SMTP_HOST（預設 smtp.gmail.com）、SMTP_PORT（預設 465）
"""
from __future__ import annotations

import html
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def _fmt(v, f):
    return "—" if v is None else f(v)


def build_email_html(title, date, report_url, strat_info, rows_by_code) -> str:
    parts = [
        f"<div style='font-family:-apple-system,\"Microsoft JhengHei\",sans-serif;color:#1d1d1b;max-width:720px'>",
        f"<h2 style='margin:0 0 4px'>{html.escape(title)} {date}</h2>",
    ]
    if report_url:
        parts.append(f"<p style='margin:0 0 16px'><a href='{report_url}'>開啟完整網頁報表 →</a></p>")
    for s in strat_info:
        codes = s["codes"]
        parts.append(
            f"<h3 style='margin:20px 0 2px'>{html.escape(s['name'])}（{len(codes)} 檔）</h3>"
            f"<div style='color:#8a8a84;font-size:12px;margin-bottom:6px'>{html.escape(s['desc'])}</div>"
        )
        if not codes:
            parts.append("<div style='color:#8a8a84'>今日無符合</div>")
            continue
        th = "style='text-align:right;padding:4px 8px;border-bottom:1px solid #ddd;font-size:12px;color:#5c5c57'"
        td = "style='text-align:right;padding:4px 8px;border-bottom:1px solid #eee'"
        parts.append(
            "<table style='border-collapse:collapse;font-size:14px'><tr>"
            f"<th {th.replace('right','left')}>股票</th><th {th}>收盤</th><th {th}>漲跌%</th>"
            f"<th {th}>成交量(張)</th><th {th}>量比前日</th><th {th}>量比5日均</th></tr>"
        )
        sorted_codes = sorted(codes, key=lambda c: -(rows_by_code[c].get("change_pct") or 0))
        for c in sorted_codes[:50]:
            r = rows_by_code[c]
            chg = r.get("change_pct")
            color = "#d0312d" if (chg or 0) > 0 else "#18864b" if (chg or 0) < 0 else "#1d1d1b"
            parts.append(
                "<tr>"
                f"<td style='padding:4px 8px;border-bottom:1px solid #eee'><a href='{r['url']}'>{c}</a> {html.escape(r['name'])}</td>"
                f"<td {td}>{_fmt(r.get('close'), lambda v: f'{v:.2f}')}</td>"
                f"<td style='text-align:right;padding:4px 8px;border-bottom:1px solid #eee;color:{color}'>"
                f"{_fmt(chg, lambda v: f'{v:+.2f}')}</td>"
                f"<td {td}>{_fmt(r.get('volume_lots'), lambda v: f'{v:,.0f}')}</td>"
                f"<td {td}>{_fmt(r.get('vol_x_prev'), lambda v: f'{v:.1f}×')}</td>"
                f"<td {td}>{_fmt(r.get('vol_x_avg5'), lambda v: f'{v:.1f}×')}</td></tr>"
            )
        parts.append("</table>")
        if len(codes) > 50:
            parts.append(f"<div style='color:#8a8a84;font-size:12px'>…另有 {len(codes) - 50} 檔，請看網頁報表</div>")
    parts.append(
        "<p style='color:#8a8a84;font-size:12px;margin-top:24px'>此信由 GitHub Actions 自動寄出，僅供參考，不構成投資建議。</p></div>"
    )
    return "".join(parts)


def send_email(subject: str, body_html: str, attachment: tuple[str, bytes] | None = None) -> bool:
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASSWORD")
    if not user or not pwd:
        log.warning("沒有設定 SMTP_USER / SMTP_PASSWORD，略過寄信")
        return False
    to = [a.strip() for a in (os.environ.get("MAIL_TO") or user).split(",") if a.strip()]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg.set_content("請用支援 HTML 的郵件程式開啟。")
    msg.add_alternative(body_html, subtype="html")
    if attachment:
        msg.add_attachment(attachment[1], maintype="text", subtype="html", filename=attachment[0])
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=60) as s:
            s.login(user, pwd)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
    log.info("已寄信給 %s", ", ".join(to))
    return True
