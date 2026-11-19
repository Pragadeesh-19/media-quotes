import json
import os
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DEFAULT_MODELS = "gemini-3.8-flash,gemini-3.5-flash-lite"


class Channel(BaseModel):
    type: str
    url: str | None = None
    query: str | None = None


class SourceConfig(BaseModel):
    source_id: str
    name: str
    publication_name: str
    domains: list[str]
    channels: list[Channel]
    fetch_methods: list[str]
    body_selector: str | None = None
    url_path_strip: str | None = None
    exclude_url_patterns: list[str] = []
    syndication_partners: dict[str, str | None] = {}
    history_channels: list[Channel] = []


class LlmSettings(BaseModel):
    base_url: str
    models: list[str]
    api_key: str


def load_sources(directory: Path = CONFIG_DIR / "sources") -> dict[str, SourceConfig]:
    configs = (SourceConfig.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json")))
    return {c.source_id: c for c in configs}


def load_lexicon(path: Path = CONFIG_DIR / "lexicon.json") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def llm_settings() -> LlmSettings:
    return LlmSettings(
        base_url=os.environ.get("MQC_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
        models=[m.strip() for m in os.environ.get("MQC_LLM_MODELS", DEFAULT_MODELS).split(",") if m.strip()],
        api_key=os.environ.get("MQC_LLM_API_KEY", ""),
    )
