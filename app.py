#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, random, requests, subprocess, ctypes
from seleniumbase import SB

EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://bot-hosting.net"

if not (EMAIL and PASSWORD):
    print("EMAIL und PASSWORD müssen gesetzt sein.")
    sys.exit(1)


def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("Telegram nicht konfiguriert, überspringe")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("Telegram-Nachricht gesendet")
    except Exception as e:
        print(f"Telegram-Fehler: {e}")


def format_notification(status: str, extra: str = "", error: str = "",
                        expiry_date: str = "") -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if "@" in EMAIL:
        name, domain = EMAIL.split("@", 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + "****"

    lines = [
        "Bot-hosting Renewal",
        "",
        f"{status}",
        f"Konto: {masked_email}",
    ]
    if expiry_date:
        lines.append(f"Ablauf: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"Fehler: {error}")
    lines.append(f"Zeit: {now}")
    return "\n".join(lines)


def js(sb, script, default=None, errors=None):
    try:
        result = sb.execute_script(script)
        return default if result is None else result
    except Exception as e:
        if errors is not None:
            errors.append(f"execute_script: {type(e).__name__}: {e}")
        try:
            result = sb.cdp.evaluate("(function(){ " + script + " })()")
            return default if result is None else result
        except Exception as e2:
            if errors is not None:
                errors.append(f"cdp.evaluate: {type(e2).__name__}: {e2}")
            return default


def diag_turnstile_dom(sb, errors=None):
    snap = js(sb, """
        var out = [];
        var ifr = document.querySelectorAll('iframe');
        out.push('top-doc iframes: ' + ifr.length);
        for (var i = 0; i < ifr.length; i++) {
            var r = ifr[i].getBoundingClientRect();
            out.push('iframe w=' + Math.round(r.width) + ' h=' + Math.round(r.height) + ' src=' + (ifr[i].src || '').slice(0, 90));
        }
        var ts = document.querySelectorAll('[class*="turnstile"], [id*="turnstile"], input[name="cf-turnstile-response"]');
        out.push('turnstile-ish elements: ' + ts.length);
        for (var j = 0; j < ts.length; j++) {
            var rr = ts[j].getBoundingClientRect();
            var cls = (typeof ts[j].className === 'string') ? ts[j].className : (ts[j].getAttribute('class') || '');
            out.push(' - ' + ts[j].tagName + ' class=' + cls + ' w=' + Math.round(rr.width) + ' h=' + Math.round(rr.height));
        }
        out.push('window.turnstile=' + (typeof window.turnstile) + ' readyState=' + document.readyState);
        return out.join('\\n');
    """, "")
    for line in str(snap).splitlines():
        print("   [diag] " + line)
    if errors:
        for err in errors:
            print("   [diag][exec-error] " + str(err)[:200])


TURNSTILE_TOKEN_JS = """
try { if (window.turnstile && window.turnstile.getResponse) {
        var t = window.turnstile.getResponse(); if (t) return t; } } catch (e) {}
var el = document.querySelector('input[name="cf-turnstile-response"]');
return el ? (el.value || '') : '';
"""

TURNSTILE_EXISTS_JS = """
return document.querySelector(
    'input[name="cf-turnstile-response"], iframe[src*="challenges.cloudflare.com"], .cf-turnstile'
) !== null;
"""

TS_COORDS_JS = """
function findWidget(root) {
    var els = root.querySelectorAll(
        'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, [id*="cf-chl"]');
    for (var i = 0; i < els.length; i++) {
        var r = els[i].getBoundingClientRect();
        if (r.width > 50 && r.height > 20) return els[i];
    }
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
        if (all[j].shadowRoot) {
            var f = findWidget(all[j].shadowRoot);
            if (f) return f;
        }
    }
    return null;
}
function boxFromTokenInput() {
    var el = document.querySelector('input[name="cf-turnstile-response"]');
    var cur = el ? el.parentElement : null;
    for (var k = 0; k < 6 && cur; k++) {
        var r = cur.getBoundingClientRect();
        if (r.width > 100 && r.width < 600 && r.height > 30 && r.height < 140) return cur;
        cur = cur.parentElement;
    }
    return null;
}
var hit = findWidget(document) || boxFromTokenInput();
if (!hit) return null;
var hr = hit.getBoundingClientRect();
return {cx: Math.round(hr.x + 30), cy: Math.round(hr.y + hr.height / 2)};
"""


def wait_turnstile_present(sb, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if js(sb, TURNSTILE_EXISTS_JS, False):
            return True
        time.sleep(1)
    return False


def _activate_window():
    if os.name == "nt":
        return
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls],
                               capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]],
                               timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"],
                       timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _windows_mouse_click(x: int, y: int):
    try:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.08)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
    except Exception:
        pass


def _xdotool_click(x: int, y: int):
    if os.name == "nt":
        _windows_mouse_click(x, y)
        return
    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)],
                       timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")


def turnstile_token(sb) -> str:
    token = js(sb, TURNSTILE_TOKEN_JS, "")
    return token if isinstance(token, str) else ""


def _click_turnstile(sb, locate_timeout=10) -> bool:
    coords = None
    errors = []
    deadline = time.time() + locate_timeout
    while time.time() < deadline:
        coords = js(sb, TS_COORDS_JS, None, errors)
        if coords:
            break
        time.sleep(1)
    if not coords:
        print("Turnstile nicht gefunden, Diagnose:")
        diag_turnstile_dom(sb, errors)
        return False

    try:
        wi = js(sb, "return '' + (window.screenX || 0) + '|' + (window.screenY || 0) + '|' +"
                    " window.outerHeight + '|' + window.innerHeight;", "")
        parts = str(wi).split("|")
        sx, sy = int(parts[0] or 0), int(parts[1] or 0)
        bar = int(parts[2] or 800) - int(parts[3] or 768)
        ax = int(coords["cx"]) + sx
        ay = int(coords["cy"]) + sy + bar
        print(f"Mausklick Turnstile ({ax}, {ay})")
        _xdotool_click(ax, ay)
        time.sleep(3)
        return True
    except Exception as e:
        print(f"Klick fehlgeschlagen: {e}")
        return False


def click_turnstile(sb) -> bool:
    if _click_turnstile(sb):
        return True
    coords = js(sb, TS_COORDS_JS)
    if coords:
        try:
            if hasattr(sb, "cdp") and hasattr(sb.cdp, "gui_click_x_y"):
                print(f"Koord-Klick Turnstile ({coords['cx']},{coords['cy']})")
                sb.cdp.gui_click_x_y(int(coords["cx"]), int(coords["cy"]))
                return True
        except Exception as e:
            print(f"Koord-Klick fehlgeschlagen: {e}")
    try:
        if hasattr(sb, "cdp") and hasattr(sb.cdp, "click_captcha"):
            print("Fallback: cdp.click_captcha")
            sb.cdp.click_captcha()
            return True
        print("Hinweis: SeleniumBase-Version ohne click_captcha")
    except Exception as e:
        print(f"click_captcha Fehler: {e}")
    return False


def solve_turnstile(sb, timeout=90, tag="Turnstile", first_wait=8) -> bool:
    if turnstile_token(sb):
        return True
    deadline = time.time() + timeout
    next_click = time.time() + first_wait
    clicks = 0
    while time.time() < deadline:
        if turnstile_token(sb):
            print(f"{tag} bestanden (Token erhalten)")
            return True
        if time.time() >= next_click:
            clicks += 1
            print(f"Klick {clicks} auf Turnstile...")
            click_turnstile(sb)
            next_click = time.time() + 10 + random.uniform(0, 3)
        time.sleep(2)
    ok = bool(turnstile_token(sb))
    print(f"{tag}: {'bestanden' if ok else 'fehlgeschlagen'}")
    return ok


def handle_turnstile(sb) -> bool:
    print("Prüfe Turnstile...")
    return solve_turnstile(sb, timeout=90, tag="Turnstile", first_wait=8)


def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


def format_countdown(countdown_str: str) -> str:
    try:
        h, m, _ = countdown_str.split(":")
        h = int(h)
        m = int(m)
        if h > 0:
            return f"{h}h{m}min"
        else:
            return f"{m}min"
    except Exception:
        return countdown_str


def normalize_date(date_str: str) -> str:
    parts = date_str.split("/")
    if len(parts) == 3:
        if len(parts[2]) == 4:
            return f"{parts[2]}/{int(parts[0]):02d}/{int(parts[1]):02d}"
        if len(parts[0]) == 4:
            return f"{parts[0]}/{int(parts[1]):02d}/{int(parts[2]):02d}"
    return date_str


DATE_RE = r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})"


def extract_expiry_date(page_source: str) -> str:
    patterns = [
        r"[Ee]xpires\s*[:\-]?\s*" + DATE_RE,
        DATE_RE,
    ]
    for pattern in patterns:
        match = re.search(pattern, page_source)
        if match:
            return normalize_date(match.group(1))
    return None


def get_expiry_from_dom(sb) -> str:
    for selector in ["span.st-meta", ".st-meta", '[class*="st-meta"]']:
        try:
            if sb.is_element_present(selector):
                match = re.search(DATE_RE, sb.get_text(selector))
                if match:
                    return normalize_date(match.group(1))
        except Exception:
            pass
    return None


def main():
    print("#" * 25)
    print("   Bot-hosting Auto-Renew")
    print("#" * 25)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"

    sb_kwargs = {"uc": True, "headless": HEADLESS, "locale_code": "en-US"}
    if IS_PROXY:
        print(f"Proxy aktiv: {PROXY_SERVER}")
        sb_kwargs["proxy"] = PROXY_SERVER
    else:
        print("Direktverbindung")

    with SB(**sb_kwargs) as sb:
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"Öffentliche IP: {ip}")
        except Exception as e:
            print(f"IP-Ermittlung fehlgeschlagen: {e}")

        login_ok = False

        if EMAIL and PASSWORD:
            print("Browser startet...")
            try:
                print(f"Öffne {BASE_URL}/login")
                sb.open(f"{BASE_URL}/login")
                sb.wait_for_ready_state_complete()
                sb.sleep(3)
                sb.click('button[type="submit"]')
                sb.sleep(12)
                print("Fülle E-Mail...")
                sb.input('input[type="email"]', EMAIL)
                sb.sleep(1)
                print("Fülle Passwort...")
                sb.input('input[type="password"]', PASSWORD)
                sb.sleep(3)

                if wait_turnstile_present(sb):
                    if not handle_turnstile(sb):
                        print("Turnstile im Login fehlgeschlagen, Fallback...")
                        try:
                            sb.cdp.click_captcha()
                            sb.sleep(4)
                        except Exception as e:
                            print(f"Fallback fehlgeschlagen: {e}")
                else:
                    print("Kein Turnstile im Login")

                sb.sleep(1)
                sb.click('button[type="submit"]')
                print("Login abschicken...")
                sb.sleep(6)
                sb.wait_for_ready_state_complete()
                sb.open(f"{BASE_URL}/dash/billings")
                sb.wait_for_ready_state_complete()
                sb.sleep(3)
                current_url = sb.get_current_url()
                current_title = sb.get_title()
                print(f"URL: {current_url} | Title: {current_title}")

                if ("/dash/billings" in current_url
                        and "/login" not in current_url
                        and "error=" not in current_url):
                    login_ok = True
                    print("Login erfolgreich — Billing-Seite erreicht")
                else:
                    print(f"Login fehlgeschlagen, URL: {current_url}")
            except Exception as e:
                print(f"Login-Ausnahme: {type(e).__name__}: {str(e)[:150]}")

        if not login_ok:
            send_telegram_message(format_notification("❌ Login fehlgeschlagen",
                                                      error="Password Login fehlgeschlagen"))
            return

        sb.sleep(3)
        try:
            page_source = sb.get_page_text() + "\n" + sb.get_page_source()
        except Exception:
            page_source = sb.get_page_source()
        current_expiry = get_expiry_from_dom(sb) or extract_expiry_date(page_source)
        if current_expiry:
            print(f"Aktueller Ablauf: {current_expiry}")
        else:
            print("Ablaufdatum nicht gefunden")

        outer_renew_selector = None
        countdown_text = None
        possible_selectors = [
            'type:button("Renew free plan")',
            'button:contains("Renew free plan")',
            '[class*="btn btn-primary btn-sm"]',
        ]

        for selector in possible_selectors:
            try:
                if sb.is_element_visible(selector):
                    button_text = sb.get_text(selector)
                    match = re.search(
                        r"(?:Renew in|Renewal cooldown)\s+(\d{1,2}:\d{2}:\d{2})",
                        button_text, re.I,
                    )
                    if match:
                        countdown_text = match.group(1)
                        print(f"Cooldown aktiv: '{button_text}'")
                        break
                    elif "Renew" in button_text:
                        outer_renew_selector = selector
                        print(f"Renew-Button verfügbar: '{button_text}'")
                        break
            except Exception:
                pass

        if outer_renew_selector:
            print("Klicke Renew-Button, warte auf Modal...")
            try:
                sb.sleep(2)
                sb.click(outer_renew_selector)
                sb.sleep(15)
            except Exception as e:
                print(f"Klick fehlgeschlagen: {e}")
                send_telegram_message(format_notification("❌ Renew-Klick fehlgeschlagen",
                                                          error=str(e)[:150]))
                return

            turnstile_passed = True
            if wait_turnstile_present(sb):
                turnstile_passed = handle_turnstile(sb)
                if not turnstile_passed:
                    print("Turnstile im Modal fehlgeschlagen, Fallback...")
                    try:
                        sb.cdp.click_captcha()
                        sb.sleep(4)
                        turnstile_passed = bool(turnstile_token(sb))
                    except Exception as e:
                        print(f"Fallback fehlgeschlagen: {e}")
            else:
                print("Kein Turnstile im Modal")

            if not turnstile_passed:
                print("Turnstile fehlgeschlagen, Abbruch")
                send_telegram_message(format_notification("❌ Turnstile fehlgeschlagen",
                                                          error="Kein Token"))
                return

            print("Warte auf Confirm und klicke...")
            time.sleep(5)

            modal_button_clicked = False
            try:
                selector = 'div[role="dialog"] button.btn-primary'
                sb.click(selector, timeout=8)
                modal_button_clicked = True
                print("Confirm geklickt")
            except Exception as e:
                print(f"Confirm-Klick fehlgeschlagen: {e}")

            sb.sleep(5)
            sb.refresh()
            print("Warte auf neue Ablaufdaten...")
            sb.wait_for_ready_state_complete()
            sb.sleep(3)

            try:
                new_page_text = sb.get_page_text() + "\n" + sb.get_page_source()
            except Exception:
                new_page_text = sb.get_page_source()
            new_expiry = get_expiry_from_dom(sb) or extract_expiry_date(new_page_text)
            new_match = re.search(
                r"(?:Renewal cooldown|Renew in)\s+(\d{1,2}:\d{2}:\d{2})",
                new_page_text,
            )
            if new_match:
                new_countdown = new_match.group(1)
                print(f"Renew erfolgreich, neuer Countdown: {new_countdown}")
                if new_expiry:
                    print(f"Neuer Ablauf: {new_expiry}")
                send_telegram_message(format_notification(
                    "✅ Renew erfolgreich",
                    extra=f"Nächster Renew in {format_countdown(new_countdown)}",
                    expiry_date=new_expiry or "unbekannt",
                ))
            else:
                if new_expiry and new_expiry != current_expiry:
                    print(f"Renew erfolgreich, neuer Ablauf: {new_expiry}")
                    send_telegram_message(format_notification(
                        "✅ Renew erfolgreich",
                        extra="Ablaufdatum aktualisiert",
                        expiry_date=new_expiry,
                    ))
                else:
                    print("Ergebnis unklar — Ablaufdatum unverändert")
                    send_telegram_message(format_notification(
                        "⚠️ Renew unklar",
                        error="Ablaufdatum unverändert",
                    ))
        else:
            if countdown_text:
                print(f"Cooldown aktiv, Renew in {format_countdown(countdown_text)}")
                send_telegram_message(format_notification(
                    "⏳ Coold
