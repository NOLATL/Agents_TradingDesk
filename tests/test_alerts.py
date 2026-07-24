"""Regression tests for ops.alerts.notify.

2026-07-23: the ReviewRejected alert title contained an em dash; httpx
headers must be ASCII, so notify() raised UnicodeEncodeError and killed the
whole nightly run. notify() must (a) deliver non-ASCII titles and (b) never
raise, whatever the transport does.
"""

import httpx
import pytest

from ops import alerts

EM_DASH_TITLE = "Scout proposal failed review — no candidate today"


@pytest.fixture
def topic(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")


def test_non_ascii_title_is_sent_via_query_params(monkeypatch, topic):
    sent = {}

    def fake_post(url, **kwargs):
        sent["url"] = url
        sent.update(kwargs)
        # Build the real request so any header-encoding error still surfaces.
        httpx.Request("POST", url, **{k: v for k, v in kwargs.items()
                                      if k in ("content", "params", "headers")})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    assert alerts.notify(EM_DASH_TITLE, "détail — ünïcode", priority="high") is True
    assert sent["params"]["title"] == EM_DASH_TITLE
    assert "headers" not in sent


def test_empty_tags_omitted(monkeypatch, topic):
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    alerts.notify("t", "m")
    assert "tags" not in sent["params"]


def test_transport_failure_never_raises(monkeypatch, topic, capsys):
    def boom(*a, **k):
        raise UnicodeEncodeError("ascii", "—", 0, 1, "ordinal not in range(128)")

    monkeypatch.setattr(alerts.httpx, "post", boom)
    assert alerts.notify(EM_DASH_TITLE, "msg") is False
    assert "alert:send-failed" in capsys.readouterr().err


def test_http_error_never_raises(monkeypatch, topic, capsys):
    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(alerts.httpx, "post", boom)
    assert alerts.notify("title", "msg") is False
    assert "alert:send-failed" in capsys.readouterr().err


def test_no_topic_returns_false(monkeypatch, capsys):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert alerts.notify("title", "msg") is False
    assert "alert:no-topic" in capsys.readouterr().err
