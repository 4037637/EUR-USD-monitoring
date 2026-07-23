"""Fetch EUR/USD rate, compare against the last alerted value, log it,
and send a Telegram alert when the move is >= 0.001.

Data source: Yahoo Finance's public chart JSON endpoint
(https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X). Chosen over
HTML scraping because it returns stable, structured JSON with no markup to
break, and requires no authentication for this use case.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# Ensure Russian text prints correctly regardless of the host console's
# default encoding (notably Windows cp1252 terminals).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
LOG_PATH = Path(__file__).resolve().parent / "rates_log.json"
MADRID_TZ = ZoneInfo("Europe/Madrid")
ALERT_THRESHOLD = 0.001
LOG_TIME_FORMAT = "%d.%m.%Y %H:%M:%S"

# Yahoo rejects requests without a browser-like User-Agent.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def fetch_rate() -> float:
    response = requests.get(YAHOO_CHART_URL, headers=REQUEST_HEADERS, timeout=15)
    response.raise_for_status()
    data = response.json()

    try:
        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Не удалось получить курс: неожиданный формат ответа Yahoo Finance"
        ) from exc

    return round(float(price), 4)


def now_madrid() -> datetime:
    return datetime.now(MADRID_TZ)


def load_log() -> dict:
    with LOG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_log(data: dict) -> None:
    with LOG_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def parse_chat_ids(raw: str) -> list[str]:
    return [chat_id.strip() for chat_id in raw.split(",") if chat_id.strip()]


def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    url = TELEGRAM_API_URL.format(token=bot_token)
    response = requests.post(
        url, json={"chat_id": chat_id, "text": text}, timeout=15
    )
    print(f"[debug] Telegram chat_id={chat_id}: status_code={response.status_code}")
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API вернул ошибку: {payload}")


def send_telegram_to_all(bot_token: str, chat_ids: list[str], text: str) -> bool:
    """Send to every recipient, continuing past individual failures.

    Returns True if at least one message was delivered successfully.
    """
    any_success = False
    for chat_id in chat_ids:
        try:
            send_telegram(bot_token, chat_id, text)
            any_success = True
        except Exception as exc:  # noqa: BLE001 - report and continue to next recipient
            print(
                f"Ошибка отправки сообщения в Telegram для chat_id={chat_id}: {exc}",
                file=sys.stderr,
            )
    return any_success


def build_message(direction: str, diff: float, old_rate: float, old_time: str,
                   new_rate: float, new_time: str) -> str:
    return (
        f"EUR/USD {direction}: изменение {diff:.4f}\n"
        f"Было: {old_rate:.4f} ({old_time})\n"
        f"Стало: {new_rate:.4f} ({new_time})\n"
        f"Источник: Yahoo Finance"
    )


def main() -> int:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id_raw:
        print(
            "Ошибка: не заданы переменные окружения TELEGRAM_BOT_TOKEN и/или "
            "TELEGRAM_CHAT_ID",
            file=sys.stderr,
        )
        return 1

    chat_ids = parse_chat_ids(chat_id_raw)
    print(f"[debug] Распознано chat_id из TELEGRAM_CHAT_ID: {len(chat_ids)}")
    if not chat_ids:
        print(
            "Ошибка: TELEGRAM_CHAT_ID задан, но не содержит ни одного корректного chat_id",
            file=sys.stderr,
        )
        return 1

    try:
        rate = fetch_rate()
    except Exception as exc:  # noqa: BLE001 - report any fetch failure clearly
        print(f"Ошибка получения курса EUR/USD: {exc}", file=sys.stderr)
        return 1

    now = now_madrid()
    log_timestamp = now.strftime(LOG_TIME_FORMAT)

    log = load_log()
    last_alert = log.get("last_alert")

    if last_alert is None:
        log["last_alert"] = {"rate": rate, "timestamp": log_timestamp}
        log.setdefault("history", []).append(
            {"rate": rate, "timestamp": log_timestamp, "alert_sent": False}
        )
        save_log(log)
        print(f"Первый запуск: базовый курс {rate:.4f} сохранён, оповещение не отправлено")
        return 0

    diff = round(abs(rate - last_alert["rate"]), 4)
    exit_code = 0

    if diff >= ALERT_THRESHOLD:
        direction = "UP" if rate > last_alert["rate"] else "DOWN"
        message = build_message(
            direction=direction,
            diff=diff,
            old_rate=last_alert["rate"],
            old_time=last_alert["timestamp"],
            new_rate=rate,
            new_time=log_timestamp,
        )
        if not send_telegram_to_all(bot_token, chat_ids, message):
            log.setdefault("history", []).append(
                {"rate": rate, "timestamp": log_timestamp, "alert_sent": False}
            )
            save_log(log)
            return 1

        log["last_alert"] = {"rate": rate, "timestamp": log_timestamp}
        log.setdefault("history", []).append(
            {"rate": rate, "timestamp": log_timestamp, "alert_sent": True}
        )
        print(f"Оповещение отправлено: {rate:.4f} ({direction}, изменение {diff:.4f})")
    else:
        log.setdefault("history", []).append(
            {"rate": rate, "timestamp": log_timestamp, "alert_sent": False}
        )
        print(f"Курс {rate:.4f}, изменение {diff:.4f} меньше порога, оповещение не отправлено")

    save_log(log)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
