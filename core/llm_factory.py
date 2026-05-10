from pathlib import Path
from typing import Union

import yaml
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from core.config import settings

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ModelFactory:
    """Builds the appropriate LLM client based on the configured provider."""

    @staticmethod
    def get_model() -> BaseChatModel:
        if settings.llm_provider == "ollama":
            # 120 s timeout prevents the UI from hanging indefinitely when
            # Ollama is slow or the model is still loading.
            return ChatOllama(model=settings.ollama_model, timeout=120)

        if settings.llm_provider == "openrouter":
            return ChatOpenAI(
                model=settings.openrouter_model,
                openai_api_key=settings.openrouter_api_key,
                openai_api_base=OPENROUTER_BASE_URL,
            )

        raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r}")

    @staticmethod
    def get_system_prompt(prompt_name: str) -> str:
        """Load a system prompt from /prompts/{prompt_name}.yaml."""
        prompt_path = PROMPTS_DIR / f"{prompt_name}.yaml"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

        with prompt_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if "system_prompt" not in data:
            raise KeyError(f"'system_prompt' key missing in {prompt_path}")

        return data["system_prompt"]
