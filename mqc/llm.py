import hashlib
import json
import re
from pathlib import Path
from typing import Protocol

from mqc.config import LlmSettings
from mqc.net import Http, HttpFailure

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_CAPACITY_ERRORS = ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504")


class LlmError(Exception):
    pass


class LlmUnavailable(LlmError):
    pass


class BudgetExhausted(LlmUnavailable):
    pass


class LlmClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict | list: ...


class OpenAiCompatibleClient:
    """Calls models in the configured order; a model that fails on capacity is skipped for the rest of the run."""

    def __init__(self, settings: LlmSettings, http: Http, cache_dir: Path, max_calls: int | None = None):
        self.settings = settings
        self.http = http
        self.cache_dir = cache_dir
        self.max_calls = max_calls
        self.calls = 0
        self.cache_hits = 0
        self.unavailable_models: dict[str, str] = {}
        self.last_model: str | None = None

    def complete_json(self, system: str, user: str) -> dict | list:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for model in self.settings.models:
            cached = self._cache_path(model, messages)
            if cached.exists():
                self.cache_hits += 1
                self.last_model = model
                return json.loads(cached.read_text(encoding="utf-8"))["parsed"]
        if not self.settings.api_key:
            raise LlmUnavailable("MQC_LLM_API_KEY is not set")
        for model in self.settings.models:
            if model in self.unavailable_models:
                continue
            try:
                return self._call(model, messages)
            except _CapacityError as error:
                self.unavailable_models[model] = str(error)
        raise LlmUnavailable(f"no model available: {self.unavailable_models}")

    def _call(self, model: str, messages: list[dict]) -> dict | list:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExhausted(f"LLM call budget of {self.max_calls} reached")
        self.calls += 1
        request = _request(model, messages)
        try:
            response = self.http.request("POST", f"{self.settings.base_url.rstrip('/')}/chat/completions",
                                         json=request, timeout=120,
                                         headers={"Authorization": f"Bearer {self.settings.api_key}"})
        except HttpFailure as error:
            if any(marker in str(error) for marker in _CAPACITY_ERRORS):
                raise _CapacityError(str(error)) from error
            raise LlmError(str(error)) from error
        if response.status_code != 200:
            raise LlmError(f"{model} HTTP {response.status_code}: {response.text[:300]}")
        content = response.json()["choices"][0]["message"]["content"]
        parsed = parse_json_content(content)
        cached = self._cache_path(model, messages)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps({"model": model, "request": request, "raw": content, "parsed": parsed},
                                     ensure_ascii=False), encoding="utf-8")
        self.last_model = model
        return parsed

    def _cache_path(self, model: str, messages: list[dict]) -> Path:
        key = hashlib.sha256(json.dumps(_request(model, messages), sort_keys=True).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"


class _CapacityError(Exception):
    pass


def _request(model: str, messages: list[dict]) -> dict:
    return {"model": model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": messages}


def parse_json_content(content: str) -> dict | list:
    try:
        parsed = json.loads(_FENCE.sub("", content.strip()))
    except json.JSONDecodeError as error:
        raise LlmError(f"model returned invalid JSON: {content[:200]!r}") from error
    if not isinstance(parsed, (dict, list)):
        raise LlmError("model returned JSON that is neither an object nor a list")
    return parsed


def json_list(response: dict | list, name: str) -> list:
    """The list under `name`, accepting a bare list when the model omits the wrapping object."""
    if isinstance(response, list):
        return response
    value = response.get(name, [])
    return value if isinstance(value, list) else []
