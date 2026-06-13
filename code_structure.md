# Struttura del Codice — Sustainable Product Optimization Agent

---

## Root Directory

```
progetto_semestre/
├── README.md                                  # Documentazione avvio
├── SETUP.bat / START.bat                      # Script Windows (venv + avvio Streamlit)
├── requirements.txt                           # Dipendenze Python
├── .env / .env.exemple                        # API key e configurazione (non committare .env)
│
├── ai_logic.md                                # Logica dell'agente (questo file e correlati)
├── ai_cognitive_architecture.md               # Architettura cognitiva dettagliata
├── code_structure.md                          # Struttura del codice (questo file)
│
├── test_prompts_reali.py                      # Test E2E manuale: esegue prompt reali contro il grafo (richiede LLM attivo)
│
├── agents/                                    # Grafo LangGraph e nodi
├── core/                                      # Configurazione e LLM factory
├── data/                                      # Dataset LCA e client
├── prompts/                                   # System prompt YAML
├── ui/                                        # Interfaccia Streamlit
├── reports/                                   # Generatore report PDF/HTML
└── tests/                                     # Test pytest (data layer e grafo)
```

---

## `agents/` — Grafo LangGraph

| File | Responsabilità |
|------|---------------|
| `graph.py` | Topologia del grafo: nodi, archi, routing condizionale, interrupt HITL |
| `state.py` | `AgentState` (TypedDict): memoria condivisa tra i nodi |
| `schemas.py` | Schemi Pydantic per output strutturati LLM (`WorkflowAndBOMResponse`, `BOMComponent`, ecc.) |
| `nodes.py` | Nodi generici: `constraint_extractor`, `lca_validator`, `mcda_scorer`, `human_feedback_processor` |
| `workflow_node.py` | Nodo principale: 7 passi del System Prompt, gap analysis a 2 tentativi, fuzzy match DB |
| `material_node.py` | Nodo alternativa materiali (solo `task_type="optimization"`) |

### Routing del grafo (`graph.py`)

```
START → constraint_extractor → human_feedback_processor
                                        │
                        ┌───────────────┼───────────────────────┐
                        │               │                       │
                   phase='error'  phase='workflow'    phase altro
                        │               │              (interview/constraints)
                       END    ┌─────────┴──────────┐          │
                              │                    │           │
                         task=modeling      task=optimization  │
                              │                    │           │
                         lca_validator      material_ideator   │
                              │                    │           │
                             END            lca_validator      │
                                                   │           │
                                            mcda_scorer        │
                                                   │           ▼
                                                  END   workflow_bom_ideator
                                                              │
                                                   (torna a human_feedback_processor)
```

---

## `core/` — Configurazione e LLM

| File | Responsabilità |
|------|---------------|
| `config.py` | `Settings` (pydantic-settings): API key, pesi MCDA, impatti processo, `TRANSPORT_IMPACT_PER_TKM` |
| `llm_factory.py` | Pattern Factory: istanzia LLM (OpenRouter cloud) e carica system prompt da `prompts/` |

**Costanti chiave in `config.py`:**
- `PROCESS_IMPACTS`: impatto CO₂ per tipo processo manifatturiero (Injection moulding, Extrusion, ecc.)
- `TRANSPORT_IMPACT_PER_TKM`: valore fallback trasporto su camion (0.05 kgCO₂/tkm)
- `weight_co2`, `weight_cost`, `weight_energy`: pesi MCDA

---

## `data/` — Accesso Dati LCA

| File | Responsabilità |
|------|---------------|
| `dataset_ecoinvent_perfetto.xlsx` | Dataset ecoinvent con colonne: `id`, `processname`, `outputname`, `location`, `climatechangeimpact`, `unitofmeasure` |
| `csv_lca_client.py` | Client principale: caricamento Pandas, fuzzy match 3-stadio, filtri waste/metallo/plastica, regola market for |
| `lca_interface.py` | Interfaccia astratta `LCADataProvider` |
| `provider_factory.py` | Singleton factory: garantisce una sola istanza del client in memoria |
| `ecoinvent_api_client.py` | Placeholder per futura integrazione API ecoinvent (non usato) |

### Logica `find_closest_match()` in sintesi

```
Input: label, location, has_transport

Stadio 1: Espansione semantica (sinonimi industriali)
Stadio 2: Filtro candidati per location (exact → partial → fallback geo)
         + Filtro waste/recycled (Boolean Gating su is_recycled)
         + Filtro geometria (_get_geometry, slab vs block ecc.)
         + Score difflib con bonus/penalità market for / production
         + Penalità prodotti finiti (-0.5) / Geometry Hint Bonus (+0.15)
         + Score Modifier Contestuale (plastic_material -0.6, extraction_activity +0.25)
Stadio 3: Se non trovato → prossima geografia nella gerarchia

Pass 1 soglia 0.85 → materiali vergini
Pass 2 soglia 0.70 → fallback standard
```

**Regola `market for`:**
- `has_transport=False` → bonus `+0.3` a `market for`, penalità `−0.3` a `production`
- `has_transport=True` → penalità `−0.3` a `market for`, bonus `+0.4` a `production`

---

## `prompts/` — System Prompt

| File | Contenuto |
|------|-----------|
| `semantic_ideation_api.yaml` | System prompt principale: 7 passi logica, regole material specificity, HARD LOCK GEOGRAPHY, assunzioni, formato JSON output |

Il prompt viene caricato e formattato da `llm_factory.py → get_system_prompt("semantic_ideation_api")` con i placeholder `{user_input}`, `{constraints}`, `{geography}`.

---

## `ui/` — Interfaccia Streamlit

| File | Contenuto |
|------|-----------|
| `app.py` | Glass Box UI: colonna sinistra (chat), colonna destra (Thought Log, BOM, grafici LCA, MCDA). Gestione HITL interrupt, invio feedback utente, visualizzazione assunzioni |

La UI non contiene logica di business. Comunica con il grafo LangGraph tramite `graph.invoke()` e `graph.update_state()` per iniettare il feedback utente.

---

## `reports/` — Generatore Report

| File | Contenuto |
|------|-----------|
| `generator.py` | Rendering HTML/PDF del report finale: BOM, LCA results, MCDA scores, assunzioni dichiarate, tabella processi Ecoinvent matchati (ultima sezione prima del footer) |

---

## `tests/` — Test Pytest

| File | Contenuto |
|------|-----------|
| `test_data_layer.py` | Test del `CSVLcaClient`: ricerca materiali, fuzzy match, fallback geografico |
| `test_graph.py` | Test del grafo LangGraph: simulazione flussi, interrupt, routing |

---

## Test nella Root (verifica E2E manuale)

| File | Contenuto | Copertura |
|------|-----------|-----------|
| `test_prompts_reali.py` | 11 prompt realistici (modeling/optimization) eseguiti contro il grafo reale, con LLM attivo (richiede `OPENROUTER_API_KEY` valida e credito disponibile) | Smoke test end-to-end con LLM reale |

Per eseguire:
```bash
python -X utf8 test_prompts_reali.py
```

Per i test automatici (mock LLM, nessun credito richiesto), usare la suite pytest in `tests/` (vedi sopra).

---

## Dipendenze Principali (`requirements.txt`)

| Libreria | Uso |
|----------|-----|
| `langchain-core` | Messaggi LLM, structured output |
| `langgraph` | Grafo a stati con interrupt HITL |
| `langchain-openai` | Client OpenRouter (compatibile OpenAI API) |
| `pydantic` / `pydantic-settings` | Validazione schemi JSON e configurazione |
| `pandas` / `openpyxl` | Lettura `dataset_ecoinvent_perfetto.xlsx` |
| `streamlit` | UI Glass Box |
| `python-dotenv` | Caricamento `.env` |
