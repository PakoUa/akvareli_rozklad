"""Тижнева діаграма «Світло за тиждень» у Telegram-каналі.

Раз на тиждень (перший запуск після півночі понеділка) публікує нове повідомлення з картинкою поточного тижня,
а решту запусків (щогодини) редагує це ж повідомлення, щоб картинка була актуальною. Коли починається новий
тиждень, попереднє повідомлення востаннє оновлюється готовою картинкою завершеного тижня.

Картинка — скріншот сторінки /week воркера (headless Chromium). Стан (тиждень і message_id) лежить у
state/week_chart.json: його комітить workflow.

Змінні середовища:
  BOT_TOKEN, CHANNEL_ID   токен бота і канал (секрети)
  WORKER_URL              адреса воркера (за замовчуванням akvasvitlo-warn.pakoua.workers.dev)
  TELEGRAM_API            лише для тестів (за замовчуванням https://api.telegram.org)
  TODAY                   лише для тестів: дата YYYY-MM-DD замість поточної
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

DEFAULT_URL = "https://akvasvitlo-warn.pakoua.workers.dev"
MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
          "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
STATE = Path("state/week_chart.json")

BASE = (os.environ.get("WORKER_URL") or DEFAULT_URL).rstrip("/")
API = (os.environ.get("TELEGRAM_API") or "https://api.telegram.org").rstrip("/")
TOKEN = os.environ["BOT_TOKEN"]
CHAT = os.environ["CHANNEL_ID"]


def monday_of(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def span(start: dt.date) -> str:
    end = start + dt.timedelta(days=6)
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS[end.month - 1]}"
    return f"{start.day} {MONTHS[start.month - 1]} – {end.day} {MONTHS[end.month - 1]}"


def caption(start: dt.date) -> str:
    return f"📊 Світло за тиждень · {span(start)}"


def render(browser, week: dt.date, path: str) -> bool:
    """Зберігає скріншот сторінки /week за тиждень week. False — даних немає."""
    page = browser.new_page(viewport={"width": 900, "height": 800}, device_scale_factor=2,
                            locale="uk-UA", timezone_id="Europe/Kyiv")
    try:
        page.goto(f"{BASE}/week?week={week.isoformat()}", wait_until="networkidle")
        page.wait_for_selector(".cards", timeout=30000)
        if "Даних за цей тиждень немає" in page.locator(".foot").inner_text():
            return False
        page.add_style_tag(content=".nav{display:none}")  # кнопки навігації на картинці зайві
        page.locator("#app").screenshot(path=path)
        return True
    finally:
        page.close()


def tg(method: str, data: dict, png: str) -> dict:
    with open(png, "rb") as f:
        r = requests.post(f"{API}/bot{TOKEN}/{method}", data=data, files={"photo": f}, timeout=60)
    try:
        return r.json()
    except ValueError:
        return {"ok": False, "description": f"HTTP {r.status_code}"}


def send_new(png: str, week: dt.date) -> int:
    res = tg("sendPhoto", {"chat_id": CHAT, "caption": caption(week), "disable_notification": "true"}, png)
    if not res.get("ok"):
        raise RuntimeError(f"sendPhoto: {res.get('description')}")
    return res["result"]["message_id"]


def edit(message_id: int, png: str, week: dt.date) -> str:
    """'ok' | 'gone' (повідомлення немає) ; інші помилки — виняток."""
    media = {"type": "photo", "media": "attach://photo", "caption": caption(week)}
    res = tg("editMessageMedia", {"chat_id": CHAT, "message_id": message_id, "media": json.dumps(media)}, png)
    if res.get("ok"):
        return "ok"
    desc = res.get("description") or ""
    if "message is not modified" in desc:
        return "ok"
    if "message to edit not found" in desc or "MESSAGE_ID_INVALID" in desc:
        return "gone"
    raise RuntimeError(f"editMessageMedia: {desc}")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    today = dt.date.fromisoformat(os.environ["TODAY"]) if os.environ.get("TODAY") \
        else dt.datetime.now(ZoneInfo("Europe/Kyiv")).date()
    cur = monday_of(today)
    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            # новий тиждень: востаннє оновлюємо повідомлення минулого тижня готовою картинкою
            if state.get("message_id") and state.get("week") and state["week"] != cur.isoformat():
                old = dt.date.fromisoformat(state["week"])
                if render(browser, old, "old.png"):
                    print("минулий тиждень:", edit(state["message_id"], "old.png", old))
                state = {}

            if not render(browser, cur, "week.png"):
                print("Даних за поточний тиждень поки немає")
                save_state(state)
                return 0

            if state.get("message_id") and state.get("week") == cur.isoformat():
                result = edit(state["message_id"], "week.png", cur)
                print("оновлено:", result)
                if result == "gone":  # повідомлення видалили вручну: публікуємо заново
                    state = {"week": cur.isoformat(), "message_id": send_new("week.png", cur)}
                    print("опубліковано заново, id", state["message_id"])
            else:
                state = {"week": cur.isoformat(), "message_id": send_new("week.png", cur)}
                print("опубліковано, id", state["message_id"])
        finally:
            browser.close()

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
