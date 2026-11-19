import json

import httpx
import pytest

from mqc.config import LlmSettings
from mqc.llm import BudgetExhausted, LlmError, LlmUnavailable, OpenAiCompatibleClient, json_list, parse_json_content
from mqc.net import Http

SETTINGS = LlmSettings(base_url="https://llm.example/v1", models=["big", "lite"], api_key="k")


def _client(tmp_path, handler, max_calls=None, settings=SETTINGS):
    http = Http(client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0, sleep=lambda s: None)
    return OpenAiCompatibleClient(settings, http, tmp_path, max_calls)


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_overloaded_model_falls_back_and_is_skipped_afterwards(tmp_path):
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        return httpx.Response(503) if model == "big" else _reply('{"ok": 1}')

    client = _client(tmp_path, handler)
    assert client.complete_json("s", "u1") == {"ok": 1}
    assert client.complete_json("s", "u2") == {"ok": 1}
    assert seen == ["big", "lite", "lite"]
    assert client.last_model == "lite"


def test_cached_answer_is_reused_without_a_call(tmp_path):
    calls = []
    client = _client(tmp_path, lambda r: calls.append(1) or _reply('{"a": 1}'))
    client.complete_json("s", "u")
    assert client.complete_json("s", "u") == {"a": 1}
    assert len(calls) == 1 and client.cache_hits == 1


def test_all_models_overloaded_is_unavailable_not_error(tmp_path):
    with pytest.raises(LlmUnavailable, match="no model available"):
        _client(tmp_path, lambda r: httpx.Response(503)).complete_json("s", "u")


def test_bad_request_is_a_plain_error(tmp_path):
    with pytest.raises(LlmError) as error:
        _client(tmp_path, lambda r: httpx.Response(400, text="bad")).complete_json("s", "u")
    assert not isinstance(error.value, LlmUnavailable)


def test_budget_stops_calls(tmp_path):
    client = _client(tmp_path, lambda r: _reply("{}"), max_calls=1)
    client.complete_json("s", "u1")
    with pytest.raises(BudgetExhausted):
        client.complete_json("s", "u2")


def test_missing_key_is_unavailable(tmp_path):
    settings = SETTINGS.model_copy(update={"api_key": ""})
    with pytest.raises(LlmUnavailable, match="MQC_LLM_API_KEY"):
        _client(tmp_path, lambda r: _reply("{}"), settings=settings).complete_json("s", "u")


def test_fenced_json_is_parsed():
    assert parse_json_content('```json\n{"a": 1}\n```') == {"a": 1}


def test_timeout_fails_this_request_but_keeps_the_model(tmp_path):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("slow")
        return _reply('{"ok": 1}')

    client = _client(tmp_path, handler)
    with pytest.raises(LlmError) as error:
        client.complete_json("s", "long article")
    assert not isinstance(error.value, LlmUnavailable)
    assert client.complete_json("s", "next article") == {"ok": 1}
    assert client.unavailable_models == {}


def test_bare_list_is_accepted_where_an_object_was_asked():
    assert json_list(parse_json_content('[{"start": "b0.s0"}]'), "quotes") == [{"start": "b0.s0"}]
    assert json_list({"quotes": [1]}, "quotes") == [1]
    assert json_list({"quotes": "oops"}, "quotes") == []


def test_scalar_json_is_rejected():
    with pytest.raises(LlmError, match="neither an object nor a list"):
        parse_json_content("42")


def test_model_that_keeps_timing_out_is_dropped_for_the_run(tmp_path):
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "big":
            raise httpx.ReadTimeout("hung")
        return _reply('{"ok": 1}')

    client = _client(tmp_path, handler)
    with pytest.raises(LlmError):
        client.complete_json("s", "article 1")
    assert client.complete_json("s", "article 2") == {"ok": 1}
    assert client.complete_json("s", "article 3") == {"ok": 1}
    assert seen == ["big", "big", "lite", "lite"]
    assert "consecutive timeouts" in client.unavailable_models["big"]


def test_success_resets_the_timeout_count(tmp_path):
    answers = iter([httpx.ReadTimeout("slow"), _reply('{"a": 1}'), httpx.ReadTimeout("slow"), _reply('{"b": 1}')])

    def handler(request):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    client = _client(tmp_path, handler)
    with pytest.raises(LlmError):
        client.complete_json("s", "1")
    client.complete_json("s", "2")
    with pytest.raises(LlmError):
        client.complete_json("s", "3")
    assert client.complete_json("s", "4") == {"b": 1}
    assert client.unavailable_models == {}
