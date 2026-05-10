from typing import List, Optional
from pydantic import BaseModel, Field


class ConstraintsExtract(BaseModel):
    budget: Optional[str] = None
    aesthetics: Optional[str] = None
    structural_requirements: Optional[str] = None
    weight_limit_kg: Optional[float] = None
    recyclability_required: Optional[bool] = None
    dimensions: Optional[str] = Field(default=None, description="Dimensioni del prodotto (es. 50x50x90cm). 1 dei 4 Pilastri.")
    mechanical_load: Optional[str] = Field(default=None, description="Carico meccanico o peso da sostenere. 1 dei 4 Pilastri.")
    usage_environment: Optional[str] = Field(default=None, description="Ambiente d'uso (es. indoor, outdoor, umido). 1 dei 4 Pilastri.")
    target_lifespan: Optional[str] = Field(default=None, description="Durata o vita utile attesa. 1 dei 4 Pilastri.")


class BOMComponent(BaseModel):
    name: str
    material: str
    weight_kg: float = Field(default=0.0)
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
        description="Expected useful lifespan of this component in years.",
    )
    material_source: str = Field(description="Il nome esatto del materiale derivato dal dataset.")
    geometry: str = Field(description="La geometria del componente (es. Corpi Cavi, Pezzi Pieni Complessi, Film, Profili/Tubi).")
    manufacturing_process: str = Field(description="Il processo di produzione forzato tramite tabella geometrie.")
    unit_impact_value: float = Field(default=0.0, description="Il valore di impatto unitario (es: kg CO2/kg).")


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
    total_mass_kg: Optional[float] = Field(default=None, description="Massa totale in kg per la logistica.")
    assumptions_made: List[str] = Field(default_factory=list, description="Assunzioni fatte dall'IA (es. materiale inferito).")
    workflow_steps: List[WorkflowStep] = Field(default_factory=list, description="List of generic sequential manufacturing processes.")
    components: List[BOMComponent] = Field(default_factory=list, description="Componenti del prodotto con massa, geometria e materiale.")

class MaterialIdeationResponse(BaseModel):
    components: List[ComponentAlternatives] = Field(default_factory=list, description="List of sustainable material alternatives for each component (FASE 3 and 4).")

