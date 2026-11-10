import json

import httpx

from zuot.config import NotifyConfig
from zuot.notify.feishu import FeishuNotifier


def test_dry_run_does_not_call_network():
    cfg = NotifyConfig(dry_run=True)
    transport = httpx.MockTransport(lambda request: 1 / 0)
    notifier = FeishuNotifier(cfg, client=httpx.Client(transport=transport))
    result = notifier.send("hello")
    assert result.ok
    assert not result.delivered
    assert "dry_run" in result.detail


def test_missing_webhook_env(monkeypatch):
    monkeypatch.delenv("ZUOT_TEST_WEBHOOK", raising=False)
    cfg = NotifyConfig(webhook_env="ZUOT_TEST_WEBHOOK", dry_run=False)
    result = FeishuNotifier(cfg).send("hello")
    assert not result.ok
    assert "missing env" in result.detail


def test_webhook_success(monkeypatch):
    monkeypatch.setenv("ZUOT_TEST_WEBHOOK", "https://example.invalid/hook")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "msg": "ok"})

    cfg = NotifyConfig(webhook_env="ZUOT_TEST_WEBHOOK", dry_run=False)
    notifier = FeishuNotifier(cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = notifier.send("signal")
    assert result.ok and result.delivered
    assert seen[0]["msg_type"] == "text"
    assert seen[0]["content"]["text"] == "signal"
