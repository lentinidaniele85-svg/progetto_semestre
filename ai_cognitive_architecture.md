# Architettura Cognitiva — Sustainable Product Optimization Agent

Documento di riferimento per comprendere come "pensa" l'agente, la struttura dei dati che attraversa i nodi e le regole di routing del grafo LangGraph.

---

## 1. Paradigma Ibrido (Neuro-Simbolico)

```
Utente
  │
  ▼
[LLM — Neurale]           Comprensione, BOM, inferenza materiali, intervista
  │
  ▼
[Python — Simbolico]      Fuzzy match DB, calcoli LCA, regole deterministiche
  │
  ▼
Risposta + Report
```

**L'LLM non tocca mai i numeri LCA.** I valori CO₂ vengono sempre letti da `DataSet.xlsx` tramite `csv_lca_client.find_closest_match()` con soglia di similarità ≥ 0.85 (Pass 1) o ≥ 0.70 (Pass 2).

---

## 2. Grafo LangGraph e Routing

Il grafo è definito in `agents/graph.py` e compilato con `interrupt_before=["human_feedback_processor"]` in modalità interattiva.

```
START
  │
  ▼
constraint_extractor          [nodo sincrono — no LLM async]
  │
  ▼
human_feedback_processor  ◄──────────────────────────────────────┐
  │                                                              │
  ├─ phase='error'     ─────────────────────────────────► END   │
  │                                                              │
  ├─ phase='workflow' AND task='modeling' ──► lca_validator ─────┘
  │
  ├─ phase='workflow' AND task='optimization' ──► material_ideator
  │                                                    │
  │                                             lca_validator
  │                                                    │
  │                                             mcda_scorer ──► END
  │
  └─ qualsiasi altro (constraints/interview) ──► workflow_bom_ideator
                                                       │
                                                       └──────────┘ (torna a human_feedback_processor)
```

### Valori di `current_phase`

| Valore | Significato | Prossimo nodo |
|--------|-------------|---------------|
| `"constraints"` | Vincoli estratti, in attesa approvazione | `workflow_bom_ideator` |
| `"interview"` | Dati mancanti, in attesa risposta utente | `workflow_bom_ideator` |
| `"workflow"` | BOM e logistica completate | `material_ideator` o `lca_validator` |
| `"lca"` | Calcolo LCA completato | `mcda_scorer` |
| `"complete"` | Processo terminato | `END` |
| `"error"` | Errore critico (materiale non trovato, ecc.) | `END` |

---

## 3. Lo Stato Globale (`AgentState`)

Definito in `agents/state.py` come `TypedDict`. È la "memoria" che scorre tra i nodi:

| Campo | Tipo | Descrizione |
|-------|------|-------------|
| `user_input` | `str` | Input originale + risposte interview accodate |
| `constraints` | `dict` | Vincoli estratti: massa, geografia, task_type, ecc. |
| `bom` | `list[dict]` | Distinta base con materiali, pesi, geometrie |
| `workflow_steps` | `list[dict]` | Passi del processo manifatturiero |
| `logistics_data` | `dict` | `distance_km`, `tkm`, `supplier_country`, ecc. |
| `lca_results` | `list[dict]` | Impatti CO₂ per componente (materiale + processo + trasporto) |
| `semantic_alternatives` | `list[dict]` | Alternative materiali proposte da `material_ideator` |
| `mcda_scores` | `list[dict]` | Classifica MCDA con score e best_alternative |
| `assumptions_list` | `list[str]` | Tutte le assunzioni dichiarate (visibili in UI) |
| `thought_log` | `list[str]` | Log ragionamento step-by-step (Glass Box UI) |
| `pending_feedback` | `str \| None` | Messaggio in attesa di risposta utente |
| `current_phase` | `str` | Fase corrente per routing |
| `current_lca_step` | `int` | Step 1-7 (tracker progressione UI) |
| `interview_attempt_count` | `int` | 0=primo tentativo, 1=secondo tentativo |
| `error_message` | `str \| None` | Messaggio di errore critico |

---

## 4. Nodi e Responsabilità

### `constraint_extractor` (sincrono, `nodes.py`)
- Chiama LLM con `ConstraintsExtract` schema Pydantic
- Estrae: massa, geografia, supplier_country, destination_country, task_type
- Regola: estrae massa **solo se esplicitamente dichiarata** (`mass_kg`, `5 kg`, ecc.)
- Output: `current_phase = "constraints"`

### `workflow_bom_ideator` (asincrono, `workflow_node.py`)
Implementa i 7 passi del System Prompt. Logica deterministica post-LLM:

**Gap Analysis (Passo 7) — 2 tentativi:**
```
attempt_count == 0:
  missing = []
  if mass is None and not is_material_only: missing.append("massa")
  if geography not specified:               missing.append("luogo (geografia)")
  if dist_km is None and not is_material_only: missing.append("distanza di trasporto (km)")

  if missing → pending_feedback = "Mancano: {missing}" → phase="interview" → STOP

attempt_count == 1 (secondo tentativo):
  if mass is None    → assume 1.0 kg
  if geo not given   → assume RER
  if dist is None    → has_transport=False (usa market for, nessun default km)
  → phase="workflow" → PROCEDE
```

**Fuzzy Match nel DB:**
```python
has_transport = dist_km is not None and dist_km > 0
best_match = provider.find_closest_match(
    material, location=geography, has_transport=has_transport
)
```

**Strict Mode:** se il match non raggiunge soglia 0.85 → `phase="error"`, workflow bloccato.

### `human_feedback_processor` (asincrono, `nodes.py`)
- **Fase `interview`:** qualsiasi risposta viene accodata a `user_input` (non distingue approvazione)
- **Fase `constraints`/`workflow`:** controlla token di approvazione (`ok`, `sì`, `continua`...)
  - Approvazione → `pending_feedback = None`, fase invariata
  - Modifica → chiama LLM con patch chirurgica sulla BOM (solo i campi citati)

### `lca_validator` (asincrono, `nodes.py`)
- Calcolo deterministico: `impatto = impatto_materiale × massa + impatto_processo × massa`
- Trasporto: `tkm = Σ(massa_componente/1000 × dist_km)` — solo per componenti non-market
- Anti double-counting: se tutti i materiali sono `market for` → warning se dist > 200 km
- Strict Mode: materiale originale non trovato → `phase="error"` (non usa fallback inventati)

### `material_ideator` (`material_node.py`)
- Solo per `task_type="optimization"`
- Chiama LLM per proporre alternative di materiali sostenibili per ogni componente BOM
- Output: `semantic_alternatives` con giustificazione, aesthetic_match, structural_match

### `mcda_scorer` (sincrono, `nodes.py`)
```
score = Δ_CO2 × w_co2 + Δ_costo × w_cost + Δ_energia × w_energy
```
- Pesi da `core/config.py`
- Alternativa con score massimo = `best_alternative`

---

## 5. Schemi Pydantic (`agents/schemas.py`)

### `WorkflowAndBOMResponse`
Output strutturato del `workflow_bom_ideator`:

| Campo | Tipo | Note |
|-------|------|------|
| `is_material_only` | `bool` | Passo 1 del system prompt |
| `is_interview_complete` | `bool` | `False` → attiva gap analysis |
| `total_mass_kg` | `float \| None` | `None` se non dichiarata → intervista |
| `geography` | `str \| None` | In inglese, normalizzata da `normalize_text()` |
| `distance_km` | `float \| None` | Solo se esplicitamente dichiarato dall'utente |
| `supplier_country` | `str \| None` | Origine del materiale grezzo |
| `destination_country` | `str \| None` | Destinazione finale |
| `components` | `list[BOMComponent]` | Componenti della BOM |
| `workflow_steps` | `list[WorkflowStep]` | Passi manifatturieri |
| `assumptions_made` | `list[str]` | Assunzioni dichiarate dall'LLM |
| `interview_questions` | `list[str]` | Domande per gap analysis |

### `BOMComponent`
| Campo | Tipo | Note |
|-------|------|------|
| `name` | `str` | Nome componente |
| `material` | `str` | Nome materiale in inglese (no "waste", "recycled") |
| `geometry` | `str` | Da: Corpi Cavi, Pezzi Pieni Complessi, Film, Profili/Tubi |
| `weight_kg` | `float` | Peso componente |
| `functional_role` | `str` | Ruolo funzionale |

---

## 6. Database LCA — `csv_lca_client.py`

### Caricamento
- `DataSet.xlsx` → Pandas DataFrame in memoria all'avvio
- Colonne richieste: `id`, `processname`, `outputname`, `location`, `climatechangeimpact`
- Pre-calcolo colonne lowercase (`_flowname_lower`, `_processname_lower`) per velocità

### `find_closest_match()` — 3 stadi

**Stadio 1: Espansione Semantica**
```python
search_terms = _expand_semantic_terms(label)
# "acciaio" → ["acciaio", "steel", "cast iron", "ferro"]
```

**Stadio 2: Fuzzy Match con filtri**
- Filtro Waste Assoluto: `re.search(r"\bwaste\b|\bscrap\b", ...)` → skip
- Filtro Metallo: `impact < 1.0` → skip (evita processi di trasformazione)
- Filtro Plastica: `impact <= 0.8` → skip
- Penalità prodotti finiti: `pipe`, `tube`, `forging`, `vessel`... → `−0.5`
- Bonus/penalità `market for` basato su `has_transport`
- Penalità `_get_geometry()`: geometrie incompatibili (slab vs block) → skip

**Stadio 3: Fallback Geografico**
```
[location_richiesta] → [RER/Europe without Switzerland] → [Global] → [Rest-of-World]
```

**Pass 1** (soglia 0.85): solo materiali vergini  
**Pass 2** (soglia 0.70): fallback standard

### `_parse_ecoinvent_name()`
Parser per stringhe ecoinvent nel formato:
```
"activity name, attribute | product name | location"
→ (activity_core, product_name, location)
```
Usato internamente per calcolare lo score di similarità solo sul nome core (non sugli attributi tecnici dopo la virgola).

---

## 7. Strict Mode — Nessun Dato Inventato

In entrambi `workflow_bom_ideator` e `lca_validator`:
- Materiale non trovato con confidenza ≥ 0.85 → `phase="error"`, messaggio utente con suggerimenti alternativi
- Alternativa non trovata → saltata con warning in `assumptions_list` (non blocca il workflow)
- Fallback geografico usato → nota in `assumptions_list` (non blocca)
