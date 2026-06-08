import re
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# DIRETTIVA 2: TransportValidatorMixin
#
# Mixin riusabile che fornisce validator deterministici per i campi logistici.
# Ereditato da ConstraintsExtract e WorkflowAndBOMResponse per evitare
# duplicazione del codice.
#
# Problema risolto: l'LLM può inserire '500 km' in transport_mode (errore)
# o una stringa non numerica in distance_km (errore silenzioso in Pydantic v2).
# I validator con mode='before' intercettano PRIMA della coercizione di tipo.
# ---------------------------------------------------------------------------
class TransportValidatorMixin(BaseModel):
    """Validator deterministici per distance_km e transport_mode.

    Regola distance_km (mode='before'):
      - Se stringa tipo '500 km' o '1.500': estrae la parte numerica con regex.
      - Se non numerica e non None: solleva ValueError.
      - Se None: passa (campo opzionale).

    Regola transport_mode (mode='before'):
      - Normalizza varianti ('truck' → 'lorry', 'nave' → 'ship').
      - Se contiene un digit (\\d): solleva ValueError con messaggio esplicito
        che guida l'LLM a rieseguire l'estrazione correttamente.
      - Se non mappabile: solleva ValueError.
      - Se None: passa (campo opzionale).
    """

    @field_validator("distance_km", mode="before", check_fields=False)
    @classmethod
    def coerce_distance_km(cls, v):
        """Estrae la parte numerica da stringhe tipo '500 km' invece di fallire."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            val = float(v)
            if val <= 0:
                raise ValueError(
                    f"distance_km deve essere un numero positivo, ricevuto: {val}. "
                    "Inserire la distanza in km (es. 500)."
                )
            return val
        if isinstance(v, str):
            # Estrai la parte numerica: supporta '500 km', '1.500', '1,500.5'
            cleaned = v.strip().replace(",", ".")
            match = re.search(r"\d+(?:\.\d+)?", cleaned)
            if match:
                val = float(match.group())
                if val <= 0:
                    raise ValueError(
                        f"distance_km estratto da '{v}' è {val} (non positivo). "
                        "Inserire una distanza maggiore di zero."
                    )
                return val
            raise ValueError(
                f"distance_km non contiene un valore numerico valido: '{v}'. "
                "Inserire solo il numero in km (es. 500). "
                "NON includere unità di misura nel campo."
            )
        raise ValueError(f"distance_km: tipo non supportato {type(v).__name__}")

    @field_validator("transport_mode", mode="before", check_fields=False)
    @classmethod
    def coerce_transport_mode(cls, v):
        """Normalizza sinonimi e rifiuta valori numerici in transport_mode."""
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError(
                f"transport_mode deve essere una stringa, ricevuto {type(v).__name__}. "
                "Valori ammessi: 'lorry', 'ship', 'aircraft'."
            )
        v_stripped = v.strip()
        # GUARD: rifiuta stringhe con numeri (es. '500 km', '2 giorni')
        # Questi valori appartengono a distance_km, non a transport_mode.
        if re.search(r"\d", v_stripped):
            raise ValueError(
                f"transport_mode contiene un numero ('{v_stripped}'). "
                "Questo campo accetta SOLO la modalità di trasporto: 'lorry', 'ship', 'aircraft'. "
                "Il valore numerico appartiene al campo distance_km."
            )
        v_lower = v_stripped.lower()
        _TRANSPORT_NORMALIZER: dict = {
            # Lorry/truck
            "lorry": "lorry", "truck": "lorry", "camion": "lorry",
            "road": "lorry", "strada": "lorry", "tir": "lorry",
            # Ship
            "ship": "ship", "nave": "ship", "sea": "ship",
            "maritime": "ship", "ferry": "ship", "traghetto": "ship",
            "container ship": "ship", "cargo": "ship",
            # Aircraft
            "aircraft": "aircraft", "airplane": "aircraft",
            "air": "aircraft", "aereo": "aircraft",
            "air freight": "aircraft", "plane": "aircraft",
        }
        normalized = _TRANSPORT_NORMALIZER.get(v_lower)
        if normalized is None:
            raise ValueError(
                f"transport_mode '{v_stripped}' non riconosciuto. "
                "Valori ammessi (e sinonimi accettati): "
                "'lorry' (truck, camion), 'ship' (nave, ferry), 'aircraft' (aereo, airplane)."
            )
        return normalized



class ConstraintsExtract(TransportValidatorMixin):
    budget: Optional[str] = None
    aesthetics: Optional[str] = None
    structural_requirements: Optional[str] = None
    weight_limit_kg: Optional[float] = Field(default=None, gt=0.0)
    mass: Optional[float] = Field(
        default=None, 
        gt=0.0, 
        description="Massa o peso esplicito in kg. Restituisci null SE E SOLO SE l'utente non specifica alcuna quantità."
    )
    geography: Optional[str] = Field(default=None, description="Area geografica o nazione (es. 'Italia', 'Francia', 'Europa'). Da estrarre se esplicitata nell'input. CRITICAL DIRECTIVE: DO NOT GUESS OR INFER THIS VALUE BASED ON LANGUAGE OR CONTEXT. IF THE USER DOES NOT EXPLICITLY WRITE A GEOGRAPHY OR COUNTRY, YOU ABSOLUTELY MUST RETURN NULL.")
    recyclability_required: Optional[bool] = None
    dimensions: Optional[str] = Field(default=None, description="Dimensioni del prodotto (es. 50x50x90cm). 1 dei 4 Pilastri.")
    mechanical_load: Optional[str] = Field(default=None, description="Carico meccanico o peso da sostenere. 1 dei 4 Pilastri.")
    usage_environment: Optional[str] = Field(default=None, description="Ambiente d'uso (es. indoor, outdoor, umido). 1 dei 4 Pilastri.")
    target_lifespan: Optional[str] = Field(default=None, description="Durata o vita utile attesa. 1 dei 4 Pilastri.")
    task_type: Literal["modeling", "optimization"] = Field(default="optimization", description="Tipo di task: 'modeling' (solo calcolo, niente MCDA) oppure 'optimization' (ricerca materiali migliori)")
    supplier_country: Optional[str] = Field(
        default=None,
        description=(
            "Nazione di origine del materiale/fornitore (es. 'Italia', 'Cina', 'Germania'). "
            "Diverso da geography (nazione di produzione). Usato per il calcolo del trasporto. "
            "CRITICAL DIRECTIVE: DO NOT GUESS OR INFER THIS VALUE BASED ON LANGUAGE OR CONTEXT. IF THE USER DOES NOT EXPLICITLY WRITE A GEOGRAPHY OR COUNTRY, YOU ABSOLUTELY MUST RETURN NULL."
        )
    )
    destination_country: Optional[str] = Field(
        default=None,
        description=(
            "Nazione di destinazione/assemblaggio (es. 'Spagna', 'Stati Uniti'). "
            "Usato per calcolare la distanza fornitore\u2192sito."
        )
    )
    distance_km: Optional[float] = Field(
        default=None,
        description=(
            "Distanza di trasporto in km. SOLO valore numerico positivo (es. 500). "
            "NON inserire unità di misura. NON inserire la modalità di trasporto qui."
        )
    )
    transport_mode: Optional[Literal["lorry", "ship", "aircraft"]] = Field(
        default=None,
        description=(
            "Modalità di trasporto principale. Mappa 'Nave' o 'Ship' -> 'ship', "
            "'Aereo' -> 'aircraft', 'Camion'/'Lorry' -> 'lorry'. "
            "Se non specificato dall'utente, lasciare null — il sistema applicherà un fallback "
            "condizionale (lorry se è presente distance_km, nessun trasporto extra se mancano entrambi)."
        )
    )


class BOMComponent(BaseModel):
    name: str
    material: str = Field(description="Strictly the English name of the raw material (e.g. 'polypropylene', 'steel'). Do NOT include geography, weight, or context.")
    weight_kg: float = Field(default=1.0, gt=0.0)
    functional_role: Optional[str] = Field(
        default=None,
        description="The structural or functional purpose of this component (e.g. 'load-bearing frame', 'aesthetic casing', 'cushioning layer').",
    )
    baseline_environmental_impact: Optional[float] = Field(
        default=None,
        description="Baseline environmental impact from LCA database.",
    )
    baseline_cost: Optional[float] = Field(
        default=None,
        description="Baseline cost tier (1=cheap, 2=medium, 3=expensive, 4=very expensive).",
    )
    lifespan_years: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Expected useful lifespan of this component in years.",
    )
    material_source: str = Field(description="Il nome esatto del materiale derivato dal dataset.")
    geometry: Optional[str] = Field(default=None, description="Geometry of the component (e.g. Corpi Cavi, Pezzi Pieni Complessi, Film, Profili/Tubi). MUST be None when is_material_only=True — do NOT invent a geometry for raw materials.")
    manufacturing_process: Optional[str] = Field(default=None, description="Manufacturing process derived from geometry mapping (e.g. 'injection moulding', 'blow moulding'). MUST be None when is_material_only=True — do NOT hallucinate a process for raw materials.")
    unit_impact_value: float = Field(default=0.0, description="Il valore di impatto unitario (es: kg CO2/kg).")
    estimated_cost_per_kg: Optional[float] = Field(default=None, description="The estimated market price per kg found online.")
    estimated_energy_mj: Optional[float] = Field(default=None, description="The estimated energy in MJ required found online.")
    supplier_country: Optional[str] = Field(default=None, description="Origine specifica di questo materiale (se diversa dal prodotto).")
    distance_km: Optional[float] = Field(default=None, ge=0.0, description="Distanza specifica di trasporto per questo componente.")
    transport_mode: Optional[Literal["lorry", "ship", "aircraft"]] = Field(default=None)
    is_recycled: bool = Field(
        default=False,
        description=(
            "True if the user explicitly requested recycled/secondary material for this component. "
            "CRITICAL: If the user writes 'recycled wood', 'riciclato', 'secondario', 'scrap', etc., "
            "keep the material name CLEAN (e.g. 'wood') but MUST set is_recycled=True. "
            "NEVER add 'recycled' to the material field — this boolean is the ONLY source of truth."
        ),
    )
    is_material_only: bool = Field(
        default=False,
        description="True if this component is just a raw material without manufacturing process."
    )


class BOMExtract(BaseModel):
    components: List[BOMComponent]


class MaterialAlternative(BaseModel):
    name: str
    base_material: str = Field(description="Chemical base name ONLY for DB lookup. e.g. 'carbon fiber', 'steel'")
    modifier: Optional[str] = Field(default=None, description="Sustainability attribute, e.g. 'recycled', 'bio-based'. Separated to prevent semantic anchor loss")
    justification: str
    is_recycled: bool = Field(
        default=False,
        description=(
            "True if this alternative is a recycled/secondary material variant. "
            "When True, the DB search engine will disable virgin-material filters "
            "and apply recycled-content bonuses."
        ),
    )
    aesthetic_match: float = Field(ge=0.0, le=1.0)
    structural_match: float = Field(ge=0.0, le=1.0)
    estimated_cost_change: Optional[str] = Field(
        default=None,
        description="Expected cost impact vs. original: 'Cheaper', 'Same', or 'More Expensive'.",
    )
    estimated_cost_per_kg: Optional[float] = Field(default=None, description="The estimated market price per kg found online.")
    estimated_energy_mj: Optional[float] = Field(default=None, description="The estimated energy in MJ required found online.")


class ComponentAlternatives(BaseModel):
    component_name: str
    alternatives: List[MaterialAlternative]

class ProcessSearch(BaseModel):
    query: str = Field(description="The general process name (e.g., 'molding', 'cutting', 'extrusion', 'welding')")
    expected_output: str = Field(description="The expected output of this process (e.g., 'steel part', 'plastic component')")

class WorkflowStep(BaseModel):
    process_name: str = Field(description="Name of the manufacturing process (providerName)")
    process_output: str = Field(description="Adequate output of the process (flowName)")

class WorkflowPlannerOutput(BaseModel):
    searches: List[ProcessSearch] = Field(description="Searches to query dataset_ecoinvent_perfetto.xlsx for the manufacturing steps")

class WorkflowAndBOMResponse(TransportValidatorMixin):
    is_material_only: bool = Field(description="Vero se l'input è solo un materiale grezzo, Falso se è un prodotto.")
    is_interview_complete: bool = Field(description="True se l'utente ha fornito Massa, Materiale, Geografia e i 4 Pilastri.")
    interview_questions: List[str] = Field(default_factory=list, description="Domande se i dati obbligatori mancano.")
    geography: Optional[str] = Field(default=None, description="Luogo o distanza per la logistica. CRITICAL DIRECTIVE: DO NOT GUESS OR INFER THIS VALUE BASED ON LANGUAGE OR CONTEXT. IF THE USER DOES NOT EXPLICITLY WRITE A GEOGRAPHY OR COUNTRY, YOU ABSOLUTELY MUST RETURN NULL.")
    distance_km: Optional[float] = Field(
        default=None,
        description="Distanza stimata in km tra fornitore e sito, se esplicitata. SOLO valore numerico positivo."
    )
    total_mass_kg: Optional[float] = Field(default=None, gt=0.0, description="Massa totale in kg per la logistica.")
    assumptions_made: List[str] = Field(default_factory=list, description="Assunzioni fatte dall'IA (es. materiale inferito).")
    workflow_steps: List[WorkflowStep] = Field(default_factory=list, description="List of generic sequential manufacturing processes.")
    components: List[BOMComponent] = Field(default_factory=list, description="Componenti del prodotto con massa, geometria e materiale.")
    transport_mode: Optional[Literal["lorry", "ship", "aircraft"]] = Field(
        default=None,
        description="Modalità di trasporto. SOLO: 'lorry', 'ship', 'aircraft'. NON inserire distanze o numeri."
    )
    supplier_country: Optional[str] = Field(
        default=None,
        description=(
            "Nazione di origine del fornitore del materiale principale. "
            "Se esplicitata dall'utente, usata per cercare il dataset di trasporto corretto. "
            "NON inferire se non dichiarata. "
            "CRITICAL DIRECTIVE: DO NOT GUESS OR INFER THIS VALUE BASED ON LANGUAGE OR CONTEXT. IF THE USER DOES NOT EXPLICITLY WRITE A GEOGRAPHY OR COUNTRY, YOU ABSOLUTELY MUST RETURN NULL."
        )
    )
    destination_country: Optional[str] = Field(
        default=None,
        description=(
            "Nazione di destinazione/assemblaggio. "
            "NON inferire se non dichiarata. "
            "Se nota, usata con supplier_country per stimare distance_km."
        )
    )

class MaterialIdeationResponse(BaseModel):
    components: List[ComponentAlternatives] = Field(default_factory=list, description="List of sustainable material alternatives for each component (FASE 3 and 4).")

