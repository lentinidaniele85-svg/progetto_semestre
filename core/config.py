from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Literal["ollama", "openrouter"] = Field(
        default="ollama", alias="LLM_PROVIDER"
    )
    ollama_model: str = Field(default="llama3", alias="OLLAMA_MODEL")

    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(
        default="openai/gpt-3.5-turbo", alias="OPENROUTER_MODEL"
    )

    lca_data_source: Literal["csv", "ecoinvent_api"] = Field(
        default="csv", alias="LCA_DATA_SOURCE"
    )

    environmental_impact_unit: str = Field(
        default="kg CO₂ eq", alias="ENVIRONMENTAL_IMPACT_UNIT"
    )

    weight_co2: float = Field(default=0.40, alias="WEIGHT_CO2")
    weight_cost: float = Field(default=0.30, alias="WEIGHT_COST")
    weight_energy: float = Field(default=0.15, alias="WEIGHT_ENERGY")
    weight_water: float = Field(default=0.15, alias="WEIGHT_WATER")


settings = Settings()
