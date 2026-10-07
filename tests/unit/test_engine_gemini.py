"""Gemini REST client with httpx.MockTransport: success, fallback, thought parts, key hygiene."""
import json
import logging
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from intent_engine.config import Settings, parse_models  # noqa: E402
from intent_engine.gemini import GeminiClient, GeminiError, extract_text  # noqa: E402
from intent_engine.metrics import LLM_ERRORS  # noqa: E402

KEY = "test-key-SECRET-9f3a"
MODELS = ("m-one", "m-two", "m-three")


def ok_body(text, extra_parts=()):
    return {"candidates": [{"content": {"parts": [*extra_parts, {"text": text}]}}]}


def client(handler, models=MODELS, key=KEY):
    sleeps = []
    c = GeminiClient(key, models, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    return c, sleeps


def model_of(req):
    return req.url.path.rsplit("/", 1)[1].split(":")[0]


def test_success_request_shape():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=ok_body('{"a":1}'))

    c, _ = client(handler)
    res = c.generate("SYSTEM", "USER")
    assert res.text == '{"a":1}' and res.model == "m-one"
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/m-one:generateContent"
    assert seen["key"] == KEY and KEY not in seen["url"] and KEY not in json.dumps(seen["body"])
    b = seen["body"]
    assert b["systemInstruction"]["parts"][0]["text"] == "SYSTEM"
    assert b["contents"][0]["parts"][0]["text"] == "USER"
    gc = b["generationConfig"]
    assert gc["temperature"] == 0.1 and gc["responseMimeType"] == "application/json" and gc["maxOutputTokens"] == 4096


def test_thought_parts_ignored():
    def handler(req):
        return httpx.Response(200, json=ok_body("REAL", [{"text": "secret reasoning", "thought": True}]))

    c, _ = client(handler)
    assert c.generate("s", "u").text == "REAL"
    assert extract_text({"candidates": [{"content": {"parts": [{"thought": True, "text": "x"}, {"text": "a"}, {"text": "b"}]}}]}) == "ab"


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_failed_model_falls_through_to_next(status):
    before = LLM_ERRORS.labels(model="m-one")._value.get()

    def handler(req):
        if model_of(req) == "m-one":
            return httpx.Response(status, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}})
        return httpx.Response(200, json=ok_body("{}"))

    c, sleeps = client(handler)
    res = c.generate("s", "u")
    assert res.model == "m-two"
    assert LLM_ERRORS.labels(model="m-one")._value.get() == before + 1
    if status in (429, 500, 503):
        assert sleeps  # short backoff before the next model


def test_timeout_moves_on():
    def handler(req):
        if model_of(req) != "m-three":
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=ok_body("{}"))

    c, sleeps = client(handler)
    assert c.generate("s", "u").model == "m-three"
    assert len(sleeps) == 2


def test_all_fail_raises_without_key():
    def handler(req):
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": f"bad key {KEY}"}})

    c, _ = client(handler)
    with pytest.raises(GeminiError) as ei:
        c.generate("s", "u")
    text = str(ei.value) + repr(ei.value) + " ".join(ei.value.attempts)
    assert KEY not in text
    assert "m-one" in text and "m-two" in text and "m-three" in text and "429" in text


def test_transport_error_text_never_contains_key():
    def handler(req):
        raise httpx.ConnectError(f"cannot connect with {KEY}")

    c, _ = client(handler)
    with pytest.raises(GeminiError) as ei:
        c.generate("s", "u")
    assert KEY not in str(ei.value) and KEY not in repr(ei.value)


def test_key_not_in_repr_logs_or_settings(caplog):
    def handler(req):
        return httpx.Response(500)

    c, _ = client(handler)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(GeminiError):
            c.generate("s", "u")
    assert KEY not in caplog.text and KEY not in repr(c)
    s = Settings(gemini_api_key=KEY, grafana_password="pw-xyz", grafana_token="tok-abc")
    assert KEY not in repr(s) and "pw-xyz" not in repr(s) and "tok-abc" not in repr(s)


def test_malformed_and_empty_responses_fall_through():
    def handler(req):
        m = model_of(req)
        if m == "m-one":
            return httpx.Response(200, json={"candidates": []})
        if m == "m-two":
            return httpx.Response(200, json=ok_body("   "))
        return httpx.Response(200, json=ok_body("{}"))

    c, _ = client(handler)
    assert c.generate("s", "u").model == "m-three"


def test_not_configured():
    c = GeminiClient(None, MODELS, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert not c.configured
    with pytest.raises(GeminiError):
        c.generate("s", "u")


def test_model_names_are_sanitised():
    assert parse_models("a, b ,../evil, c/d,a") == ("a", "b")
    c = GeminiClient(KEY, ("ok-1", "../x", "a b"), transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert c.models == ("ok-1",)


def test_settings_from_env_defaults_and_values():
    s = Settings.from_env({})
    assert s.gemini_api_key is None and s.grafana_ds_uid == "ibg-prometheus"
    assert s.gemini_models[0] == "gemini-3.5-flash-lite" and s.grafana_url == "http://grafana:3000"
    s = Settings.from_env({"GEMINI_API_KEY": " k ", "GRAFANA_URL": "http://g:3000/", "GEMINI_MODELS": "x,y"})
    assert s.gemini_api_key == "k" and s.grafana_url == "http://g:3000" and s.gemini_models == ("x", "y")
