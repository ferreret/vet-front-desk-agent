"""Reception's notices, sent to a Telegram chat.

A bot, made once with Telegram's own @BotFather, posts each notice to the chat the clinic
reads: a message taken, an emergency, an appointment booked, moved or cancelled. They are
sent off the call's path, on a worker thread: a slow or failing Telegram makes no caller
wait, and is said in the server's log.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger("vetdesk.telegram")

TOKEN, CHAT = "VETDESK_TELEGRAM_TOKEN", "VETDESK_TELEGRAM_CHAT"
API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str, chat: str,
                 post: Callable[..., Any] = httpx.post) -> None:
        self._url = API.format(token=token, method="sendMessage")
        self._chat, self._post = chat, post
        self._outbox: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._work, daemon=True, name="telegram").start()

    def send(self, text: str) -> None:
        self._outbox.put(text)

    def _work(self) -> None:
        while True:
            text = self._outbox.get()
            try:
                answer = self._post(self._url, json={"chat_id": self._chat, "text": text},
                                    timeout=15)
                if answer.status_code >= 400:
                    # Never the address: the bot's key is part of it.
                    log.warning("a notice was not delivered: Telegram answered %s",
                                answer.status_code)
            except Exception as error:  # reception's phone line must not depend on this
                log.warning("a notice was not delivered: %s", type(error).__name__)
            finally:
                self._outbox.task_done()

    def wait(self) -> None:
        """Until everything queued has been sent: for tests."""
        self._outbox.join()
