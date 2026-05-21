from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class ConstraintsExtract(BaseModel):
    budget: Optional[str] = None
    aesthetics: Optional[str] = None
    structural_requirements: Optional[str] = None
    weight_limit_kg: Optional[float] = Field(default=None, gt=0.0)
    mass: Optional[float] = Field(default=None, gt=0.0, description="Massa o peso esplicito in kg (es. '1 kg', '5kg'). Da usare assolutamente se l'input specifica una quantità.")
    geography: Optional[str] = Field(default=None, description="Area geografica o nazione (es. 'Region_X', 'Country_Y'). Da estrarre se esplicitata nell'input.")
    recyclability_required: Optional[bool] = None
    dimensions: Optional[str] = Field(default=None, description="Dimensioni del prodotto (es. 50x50x90cm). 1 dei 4 Pilastri.")
    mechanical_load: Optional[str] = Field(default=None, description="Carico meccanico o peso da sostenere. 1 dei 4 Pilastri.")
    usage_environment: Optional[str] = Field(default=None, description="Ambiente d'uso (es. indoor, outdoor, umido). 1 dei 4 Pilastri.")
    target_lifespan: Optional[str] = Field(default=None, description="Durata o vita utile attesa. 1 dei 4 Pilastri.")
    task_type: Literal["modeling", "optimization"] = Field(default="optimization", description="Tipo di task: 'modeling' (solo calcolo, niente MCDA) oppure 'optimization' (ricerca materiali migliori)")
    supplier_country: Optional[str] = Field(
        default=None,
        description=(
            "Nazione di origine del materiale/fornitore (es. 'Country_A', 'Country_B'). "
            "Diverso da geography (nazione di produzione). Usato per il calcolo del trasporto."
        )
    )
    destination_country: Optional[str] = Field(
        default=None,
        description=(
            "Nazione di destinazione/assemblaggio (es. 'Country_C'). "
            "Usato per calcolare la distanza fornitore\u2192sito."
        )
    )
    transport_mode: Optional[Literal["lorry", "ship", "aircraft"]] = Field(
        default=None,
        description="Modalità di trasporto. Inferire solo se esplicitato dall'utente (es. nave, aereo, camion)."
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


class BOMExtract(BaseModel):
    components: List[BOMComponent]


class MaterialAlternative(BaseModel):
    name: str
    justification: str
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
    searches: List[ProcessSearch] = Field(description="Searches to query DataSet.xlsx for the manufacturing steps")

class WorkflowAndBOMResponse(BaseModel):
    is_material_only: bool = Field(description="Vero se l'input è solo un materiale grezzo, Falso se è un prodotto.")
    is_interview_complete: bool = Field(description="True se l'utente ha fornito Massa, Materiale, Geografia e i 4 Pilastri.")
    interview_questions: List[str] = Field(default_factory=list, description="Domande se i dati obbligatori mancano.")
    geography: Optional[str] = Field(default=None, description="Luogo o distanza per la logistica.")
    distance_km: Optional[float] = Field(default=None, ge=0.0, description="Distanza stimata in km tra fornitore e sito, se esplicitata")
    total_mass_kg: Optional[float] = Field(default=None, gt=0.0, description="Massa totale in kg per la logistica.")
    assumptions_made: List[str] = Field(default_factory=list, description="Assunzioni fatte dall'IA (es. materiale inferito).")
    workflow_steps: List[WorkflowStep] = Field(default_factory=list, description="List of generic sequential manufacturing processes.")
    components: List[BOMComponent] = Field(default_factory=list, description="Componenti del prodotto con massa, geometria e materiale.")
    transport_mode: Optional[Literal["lorry", "ship", "aircraft"]] = Field(
        default=None,
        description="Modalità di trasporto. Inferire solo se esplicitato dall'utente (es. lorry, ship, aircraft)."
    )
    supplier_country: Optional[str] = Field(
        default=None,
        description=(
            "Nazione di origine del fornitore del materiale principale. "
            "Se esplicitata dall'utente, usata per cercare il dataset di trasporto corretto. "
            "NON inferire se non dichiarata."
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

