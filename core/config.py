from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
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

    @model_validator(mode="after")
    def validate_api_key(self) -> "Settings":
        if self.llm_provider == "openrouter":
            if not self.openrouter_api_key or self.openrouter_api_key == "your_key_here":
                raise ValueError("ERRORE: OPENROUTER_API_KEY non è configurata correttamente. Impostala nel file .env o come variabile di ambiente.")
        return self

    @model_validator(mode="after")
    def validate_mcda_weights(self) -> "Settings":
        total = self.weight_co2 + self.weight_cost + self.weight_energy + self.weight_water
        if abs(total - 1.0) > 1e-5:
            raise ValueError(f"ERRORE LCA: La somma dei pesi MCDA deve essere esattamente 1.0. Somma attuale: {total}")
        return self

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


# ---------------------------------------------------------------------------
# Costanti fisiche LCA — centralizzate qui per facilità di taratura
# ---------------------------------------------------------------------------

# Impatto processo manifatturiero [kg CO₂ eq / kg prodotto]
PROCESS_IMPACTS: dict[str, float] = {
    "Blow moulding": 0.8,
    "Injection moulding": 1.2,
    "Extrusion (film)": 0.5,
    "Extrusion": 0.6,
    "Metal working": 1.5,
    "Section bar rolling": 1.1,
    "Metal sheet rolling": 1.0,
    "Woodworking": 0.3,
    "Textile weaving": 0.8,
    "Glass production": 1.4,
    "Ceramic firing": 1.6,
}

# Impatto trasporto [kg CO₂ eq / (tonnellata · km)]
TRANSPORT_IMPACT_PER_TKM: float = 0.05
SHIP_IMPACT_PER_TKM: float = 0.012
AIRCRAFT_IMPACT_PER_TKM: float = 0.800

# Valore di fallback cautelativo per materiali non trovati nel database [kg CO₂ eq / kg]
CO2_FALLBACK_VALUE: float = 3.5
