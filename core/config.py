from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Unico provider supportato: OpenRouter
    llm_provider: Literal["openrouter"] = Field(
        default="openrouter", alias="LLM_PROVIDER"
    )
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(
        default="openai/gpt-4o-mini", alias="OPENROUTER_MODEL"
    )

    lca_data_source: Literal["csv", "ecoinvent_api"] = Field(
        default="csv", alias="LCA_DATA_SOURCE"
    )

    environmental_impact_unit: str = Field(
        default="kg CO₂ eq", alias="ENVIRONMENTAL_IMPACT_UNIT"
    )

    # Pesi MCDA — dataset contiene solo climateChangeImpact.
    # CO₂ = 0.70 (dato reale), costo = 0.30 (stima per categoria).
    # energy e water rimangono a 0 perché il dataset non li fornisce.
    weight_co2: float = Field(default=0.70, alias="WEIGHT_CO2")
    weight_cost: float = Field(default=0.30, alias="WEIGHT_COST")
    weight_energy: float = Field(default=0.0, alias="WEIGHT_ENERGY")
    weight_water: float = Field(default=0.0, alias="WEIGHT_WATER")


settings = Settings()
