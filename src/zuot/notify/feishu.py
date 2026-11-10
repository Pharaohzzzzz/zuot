"""Feishu delivery for actionable T signals.

The default transport is a group custom-bot webhook. It is intentionally
one-way: the system never accepts orders or confirmations through Feishu.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from ..config import NotifyConfig


@dataclass(frozen=True)
class NotifyResult:
    ok: bool
    delivered: bool
    detail: str


class FeishuNotifier:
    def __init__(
        self,
        cfg: NotifyConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.cfg = cfg
        self._client = client or httpx.Client(trust_env=False, timeout=8.0)

    def webhook_url(self) -> str | None:
        return os.environ.get(self.cfg.webhook_env) or None

    def send(self, text: str) -> NotifyResult:
        if self.cfg.dry_run:
            return NotifyResult(True, False, "dry_run: " + text.replace("\n", " | "))
        url = self.webhook_url()
        if not url:
            return NotifyResult(False, False, f"missing env {self.cfg.webhook_env}")
        try:
            response = self._client.post(
                url,
                json={"msg_type": "text", "content": {"text": text}},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return NotifyResult(False, False, f"feishu error: {exc}")
        code = payload.get("code", payload.get("StatusCode", 0))
        if code not in (0, "0", None):
            return NotifyResult(False, True, f"feishu rejected: {payload}")
        return NotifyResult(True, True, "sent")


def sell_text(
    *,
    symbol: str,
    name: str,
    price: float,
    qty: int,
    target: float,
    stop: float,
    deadline: str,
    reason: str,
) -> str:
    return (
        f"【ZUOT 卖出】{name} {symbol}\n"
        f"现价 {price:.2f}，卖出 {qty} 股（先卖后买）\n"
        f"目标买回 {target:.2f}（{(target / price - 1):+.2%}）\n"
        f"止损买回 {stop:.2f}（{(stop / price - 1):+.2%}）\n"
        f"最迟 {deadline} 必须买回\n"
        f"依据：{reason}\n"
        f"成交后执行：zuot fill --side sell --price <成交价>"
    )


def buyback_text(
    *,
    symbol: str,
    name: str,
    price: float,
    qty: int,
    target: float,
    stop: float,
    why: str,
) -> str:
    return (
        f"【ZUOT 买回】{name} {symbol}\n"
        f"现价 {price:.2f}，买回 {qty} 股\n"
        f"目标 {target:.2f} / 止损 {stop:.2f} / 触发：{why}\n"
        f"成交后执行：zuot fill --side buy --price <成交价>"
    )
