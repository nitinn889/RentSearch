"""Alert channels that reach you when the page is in the background or you are away.

The page itself adds the in-app banner, a chime and a browser notification.
"""
from __future__ import annotations

import base64
import html
import smtplib
import subprocess
from dataclasses import dataclass, field
from email.message import EmailMessage
from xml.sax.saxutils import escape as xml_escape

import requests

from . import config
from .models import Listing

POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


@dataclass
class Alert:
    title: str
    groups: list[tuple[str, list[Listing]]] = field(default_factory=list)  # (heading, listings)

    @property
    def listings(self) -> list[Listing]:
        return [l for _, group in self.groups for l in group]


def inr(amount: float) -> str:
    """Indian digit grouping: 1234567 -> 12,34,567."""
    s = str(int(round(amount)))
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(([head] if head else []) + groups + [tail])


def price_text(l: Listing) -> str:
    if not l.price:
        return "Price on request"
    if l.deal == "rent":
        return f"₹{inr(l.price)}/mo"
    if l.price >= 1e7:
        return f"₹{l.price / 1e7:.2f}".rstrip("0").rstrip(".") + " Cr"
    if l.price >= 1e5:
        return f"₹{l.price / 1e5:.1f}".rstrip("0").rstrip(".") + " L"
    return f"₹{inr(l.price)}"


def line(l: Listing) -> str:
    where = f"{l.distance_km:.1f} km away" if l.distance_km is not None else l.locality
    return " · ".join(p for p in (price_text(l), l.title, where) if p)


# ---------------------------------------------------------------- channels

def _windows_toast(cfg: dict, alert: Alert) -> None:
    if not config.WINDOWS_TOAST:
        raise RuntimeError("Windows notifications only work when the app runs on your Windows PC")
    listings = alert.listings
    target = listings[0].url if len(listings) == 1 else config.PUBLIC_URL
    lines = "".join(f"<text>{xml_escape(line(l))}</text>" for l in listings[:2])
    xml = (f'<toast activationType="protocol" launch="{xml_escape(target, {chr(34): "&quot;"})}">'
           f'<visual><binding template="ToastGeneric"><text>{xml_escape(alert.title)}</text>{lines}</binding></visual>'
           '<audio src="ms-winsoundevent:Notification.Reminder" /></toast>')
    xml_b64 = base64.b64encode(xml.encode("utf-8")).decode()
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{xml_b64}')));"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{POWERSHELL_APP_ID}')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
        capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode:
        detail = (done.stderr or done.stdout or "").strip()
        raise RuntimeError(detail.splitlines()[0] if detail else f"PowerShell exited with {done.returncode}")


def _ntfy(cfg: dict, alert: Alert) -> None:
    parts = []
    for heading, group in alert.groups:
        parts.append(heading)
        parts += [f"• {line(l)}" for l in group[:8]]
        if len(group) > 8:
            parts.append(f"…and {len(group) - 8} more")
    listings = alert.listings
    payload = {
        "topic": cfg["ntfy_topic"],
        "title": alert.title,
        "message": "\n".join(parts),
        "tags": ["house"],
        "priority": 5,
        "click": listings[0].url if len(listings) == 1 else config.PUBLIC_URL,
        "actions": [{"action": "view", "label": f"Open #{i + 1}", "url": l.url} for i, l in enumerate(listings[:3])],
    }
    try:
        r = requests.post(cfg["ntfy_server"].rstrip("/") or "https://ntfy.sh", json=payload, timeout=20)
    except requests.RequestException as e:
        raise RuntimeError(f"could not reach the ntfy server ({e.__class__.__name__})") from None
    if not r.ok:
        raise RuntimeError(f"ntfy answered HTTP {r.status_code}: {r.text[:200]}")


def _telegram(cfg: dict, alert: Alert) -> None:
    out = [f"<b>{html.escape(alert.title)}</b>"]
    for heading, group in alert.groups:
        out.append(f"\n<b>{html.escape(heading)}</b>")
        for l in group[:12]:
            out.append(f'• <a href="{html.escape(l.url)}">{html.escape(l.title)}</a> — {html.escape(price_text(l))}'
                       + (f", {l.distance_km:.1f} km" if l.distance_km is not None else ""))
        if len(group) > 12:
            out.append(f"…and {len(group) - 12} more")
    out.append(f'\n<a href="{html.escape(config.PUBLIC_URL)}">Open Rent Radar</a>')
    try:  # never let the token-bearing URL reach an error message
        r = requests.post(f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage", timeout=20, json={
            "chat_id": cfg["telegram_chat_id"], "text": "\n".join(out), "parse_mode": "HTML",
            "disable_web_page_preview": True})
    except requests.RequestException as e:
        raise RuntimeError(f"could not reach Telegram ({e.__class__.__name__})") from None
    if not r.ok:
        try:
            reason = r.json().get("description")
        except ValueError:
            reason = None
        raise RuntimeError(f"Telegram refused the message: {reason or f'HTTP {r.status_code}'}")


def _email(cfg: dict, alert: Alert) -> None:
    msg = EmailMessage()
    msg["Subject"] = alert.title
    msg["From"] = cfg["smtp_user"]
    msg["To"] = cfg["email_to"]
    text, rows = [], []
    for heading, group in alert.groups:
        text.append(f"{heading}\n" + "\n\n".join(f"{line(l)}\n{l.url}" for l in group))
        rows.append(f'<h3 style="margin:18px 0 6px">{html.escape(heading)}</h3><table>' + "".join(
            f'<tr><td style="padding:6px 12px 6px 0;vertical-align:top"><b>{html.escape(price_text(l))}</b></td>'
            f'<td style="padding:6px 0"><a href="{html.escape(l.url)}">{html.escape(l.title)}</a><br>'
            f'<span style="color:#666">{html.escape(" · ".join(p for p in (l.building, l.locality) if p))}'
            f'{f" · {l.distance_km:.1f} km away" if l.distance_km is not None else ""}</span></td></tr>'
            for l in group) + "</table>")
    msg.set_content("\n\n".join(text) + f"\n\nOpen Rent Radar: {config.PUBLIC_URL}")
    msg.add_alternative(f'<div style="font-family:sans-serif;font-size:14px"><h2>{html.escape(alert.title)}</h2>'
                        f'{"".join(rows)}<p><a href="{html.escape(config.PUBLIC_URL)}">Open Rent Radar</a></p></div>',
                        subtype="html")
    port = int(cfg["smtp_port"])
    try:
        if port == 465:
            with smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=30) as s:
                s.login(cfg["smtp_user"], cfg["smtp_password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["smtp_host"], port, timeout=30) as s:
                s.starttls()
                s.login(cfg["smtp_user"], cfg["smtp_password"])
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("the mail server rejected the login (for Gmail, use an App Password)") from None
    except (smtplib.SMTPException, OSError) as e:
        raise RuntimeError(f"email failed: {e}") from None


CHANNELS = {
    "windows": (_windows_toast, lambda c: config.WINDOWS_TOAST and c["windows_toast"]),
    "phone": (_ntfy, lambda c: bool(c["ntfy_topic"])),
    "telegram": (_telegram, lambda c: bool(c["telegram_token"] and c["telegram_chat_id"])),
    "email": (_email, lambda c: bool(c["email_to"] and c["smtp_user"] and c["smtp_password"])),
}


def dispatch(alert: Alert, settings: dict) -> dict[str, str]:
    """Send to every configured channel; returns channel -> "sent" or the error."""
    cfg = settings["alerts"]
    results = {}
    for name, (send, configured) in CHANNELS.items():
        if configured(cfg):
            try:
                send(cfg, alert)
                results[name] = "sent"
            except Exception as e:
                results[name] = f"failed: {e}"
    return results


def test(channel: str, settings: dict) -> None:
    send, configured = CHANNELS[channel]
    if not configured(settings["alerts"]):
        raise RuntimeError("fill in this channel's settings first" if channel != "windows"
                           else "Windows notifications are off or not available here")
    sample = Listing(source="rent-radar", source_id="test", deal="rent", title="Test alert: 2 BHK Flat for Rent",
                     url=config.PUBLIC_URL, price=25000, locality="Test locality", distance_km=1.2)
    send(settings["alerts"], Alert("Rent Radar test alert: alerts are working", [("Example listing", [sample])]))
