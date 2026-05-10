from pathlib import Path

import yaml
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from core.config import settings

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Cache dell'istanza LLM — evita di creare un nuovo client ad ogni nodo
_model_cache: dict[str, BaseChatModel] = {}


class ModelFactory:
    """Crea e cacha il client LLM OpenRouter."""

    @staticmethod
    def get_model() -> BaseChatModel:
        cache_key = f"openrouter:{settings.openrouter_model}"
        if cache_key not in _model_cache:
            _model_cache[cache_key] = ChatOpenAI(
                model=settings.openrouter_model,
                openai_api_key=settings.openrouter_api_key,
                base_url=OPENROUTER_BASE_URL,
            )
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
