"""Email 通知（使用 SMTP，預設 Gmail）。

需要的環境變數（在 GitHub 的 Settings → Secrets 設定）：
  SMTP_USER      寄件 Gmail 帳號
  SMTP_PASSWORD  Gmail「應用程式密碼」（16 碼，不是登入密碼）
  MAIL_TO        收件人，多個用逗號分隔（沒填就寄給自己）
選填：SMTP_HOST（預設 smtp.gmail.com）、SMTP_PORT（預設 465）

若沒有設定 Gmail，改用「GitHub Issue」通知：每天在儲存庫開一則 Issue，
GitHub 會自動寄通知信到你 GitHub 帳號的信箱（不需要任何密碼）。
"""
from __future__ import annotations

import html
import json
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


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def build_issue_md(title, date, report_url, strat_info, rows_by_code) -> str:
    lines = [f"## {title} {date}", ""]
    if report_url:
        lines += [f"👉 [開啟完整網頁報表（可排序、看走勢圖）]({report_url})", ""]
    for s in strat_info:
        codes = s["codes"]
        lines += [f"### {s['name']}（{len(codes)} 檔）", f"<sub>{s['desc']}</sub>", ""]
        if not codes:
            lines += ["今日無符合", ""]
            continue
        lines += ["| 股票 | 收盤 | 漲跌% | 成交量(張) | 量比前日 | 量比5日均 |", "|---|--:|--:|--:|--:|--:|"]
        for c in sorted(codes, key=lambda c: -(rows_by_code[c].get("change_pct") or 0))[:50]:
            r = rows_by_code[c]
            lines.append(
                f"| [{c} {r['name']}]({r['url']}) "
                f"| {_fmt(r.get('close'), lambda v: f'{v:.2f}')} "
                f"| {_fmt(r.get('change_pct'), lambda v: f'{v:+.2f}')} "
                f"| {_fmt(r.get('volume_lots'), lambda v: f'{v:,.0f}')} "
                f"| {_fmt(r.get('vol_x_prev'), lambda v: f'{v:.1f}×')} "
                f"| {_fmt(r.get('vol_x_avg5'), lambda v: f'{v:.1f}×')} |"
            )
        if len(codes) > 50:
            lines.append(f"\n…另有 {len(codes) - 50} 檔，請看網頁報表")
        lines.append("")
    lines.append("<sub>此訊息由 GitHub Actions 自動產生，僅供參考，不構成投資建議。</sub>")
    return "\n".join(lines)


def create_issue(title: str, body_md: str) -> bool:
    """在自己的儲存庫開 Issue；GitHub 會寄通知信給儲存庫擁有者。"""
    import requests

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        log.warning("沒有 GITHUB_TOKEN，略過 Issue 通知")
        return False
    r = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        data=json.dumps({"title": title, "body": body_md[:65000]}),
        timeout=30,
    )
    if r.status_code >= 300:
        log.error("開 Issue 失敗：HTTP %s %s", r.status_code, r.text[:200])
        return False
    log.info("已建立通知 Issue：%s", r.json().get("html_url"))
    return True
