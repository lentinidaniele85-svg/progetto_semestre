from pathlib import Path

import yaml  # pyrefly: ignore [missing-import]
from langchain_core.language_models import BaseChatModel  # pyrefly: ignore [missing-import]
from langchain_openai import ChatOpenAI  # pyrefly: ignore [missing-import]

from core.config import settings

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Cache dell'istanza LLM — evita di creare un nuovo client ad ogni nodo
_model_cache: dict[str, BaseChatModel] = {}


class ModelFactory:
    """Crea e cacha il client LLM OpenRouter."""

    @staticmethod
    def get_model(max_tokens: int | None = None) -> BaseChatModel:
        cache_key = f"{settings.llm_provider}:{settings.openrouter_model}:{max_tokens}"
        if cache_key not in _model_cache:
            kwargs = {
                "model": settings.openrouter_model,
                "openai_api_key": settings.openrouter_api_key,
                "base_url": OPENROUTER_BASE_URL,
            }
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            _model_cache[cache_key] = ChatOpenAI(**kwargs)
        return _model_cache[cache_key]

    @staticmethod
    def get_system_prompt(prompt_name: str) -> str:
        """Carica il system prompt da /prompts/{prompt_name}.yaml."""
        prompt_path = PROMPTS_DIR / f"{prompt_name}.yaml"
        if not prompt_path.exists():
            raise FileNotFoundError(f"File prompt non trovato: {prompt_path}")

        with prompt_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if "system_prompt" not in data:
            raise KeyError(f"Chiave 'system_prompt' mancante in {prompt_path}")

        return data["system_prompt"]
