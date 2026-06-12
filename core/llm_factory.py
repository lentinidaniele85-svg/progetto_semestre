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
        cache_key = f"{settings.llm_provider}:{settings.openrouter_model}"
        if cache_key not in _model_cache:
            _model_cache[cache_key] = ChatOpenAI(
                model=settings.openrouter_model,
                openai_api_key=settings.openrouter_api_key,
                base_url=OPENROUTER_BASE_URL,
                # ── Cap esplicito sui token di output (fix errore 402 OpenRouter) ──
                # PROBLEMA: langchain-openai >= 0.2 rinomina SEMPRE il parametro
                # costruttore 'max_tokens' in 'max_completion_tokens' (terminologia
                # OpenAI post-set/2024) prima di inviarlo. Il gateway di OpenRouter,
                # che instrada verso decine di provider eterogenei, riconosce per il
                # proprio pre-flight credit-check SOLO il parametro standard
                # 'max_tokens' — ignora 'max_completion_tokens'. Risultato: nessun
                # cap arriva a OpenRouter, che stima il costo sul MASSIMO output del
                # modello (16384 token per gpt-4o-mini — esattamente il valore
                # riportato nell'errore "You requested up to 16384 tokens, but can
                # only afford 16078"), facendo fallire la richiesta con HTTP 402
                # ancora prima che il modello generi una sola risposta.
                # SOLUZIONE: 'extra_body' inietta i campi indicati direttamente nel
                # JSON della richiesta HTTP (bypassando la rinomina di langchain —
                # è il meccanismo raccomandato dalla stessa documentazione
                # langchain-openai per i parametri specifici di provider compatibili
                # come OpenRouter/vLLM/LM Studio). In questo modo OpenRouter riceve
                # un 'max_tokens' esplicito e contenuto, il pre-flight credit-check
                # passa, e l'output resta comunque ampiamente sufficiente per gli
                # schemi Pydantic strutturati (ConstraintsExtract, BOM, ecc.).
                extra_body={"max_tokens": 2500},
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
