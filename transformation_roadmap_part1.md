# TRANSFORMATION ROADMAP — Sustainable Product Optimization Agent
## Principal Architect Edition | Part 1 of 3

---

# 1. COMPRENSIONE DEL SISTEMA

## 1.1 Cosa è il sistema oggi

Il sistema è un **agente AI neuro-simbolico** per l'ottimizzazione sostenibile dei materiali di prodotto. È costruito su:

- **LangGraph** — orchestrazione del grafo agente con checkpoint, interrupt e human-in-the-loop
- **Streamlit** — interfaccia utente "Glass Box" dual-pane (chat + dashboard)
- **LLM (Ollama/OpenRouter)** — ragionamento semantico, estrazione vincoli, ideazione materiali
- **Python deterministico** — calcoli LCA, MCDA, fuzzy matching su DataSet.xlsx

### Pipeline reale del grafo

```
User Input
  └─> constraint_extractor [sync LLM]
        └─> [INTERRUPT] human_feedback_processor_constraints
              └─> workflow_bom_ideator [async, LLM + DataSet fuzzy match]
                    ├─> [INTERRUPT] human_feedback_processor_interview (se dati mancanti)
                    │     └─> workflow_bom_ideator (retry)
                    └─> [INTERRUPT] human_feedback_processor_workflow
                          └─> material_ideator [async LLM]
                                └─> lca_validator [deterministico]
                                      └─> mcda_scorer [deterministico]
                                            └─> END → Report HTML/PDF
```

### Moduli principali

| File | Responsabilità | Stato |
|------|---------------|-------|
| `agents/graph.py` | Definizione grafo LangGraph | Funzionante, fragile |
| `agents/nodes.py` | constraint_extractor, lca_validator, mcda_scorer, human_feedback_processor + DEAD CODE | Misto |
| `agents/workflow_node.py` | workflow_bom_ideator (FASE 0-2 del SOP) | Funzionante |
| `agents/material_node.py` | material_ideator (FASE 3-4 del SOP) | Funzionante |
| `agents/state.py` | AgentState TypedDict | Incompleto |
| `agents/schemas.py` | Pydantic schemas LLM output | Buono |
| `data/csv_lca_client.py` | LCA data provider (Excel) | Bug critici |
| `data/provider_factory.py` | Singleton factory LCA | Non thread-safe |
| `reports/generator.py` | HTML/PDF report | Rotto (field name errato) |
| `ui/app.py` | Streamlit dual-pane UI | Funzionante, problemi UX |
| `core/config.py` | Settings Pydantic | OK |
| `core/llm_factory.py` | LLM factory (Ollama/OpenRouter) | OK ma non cached |
| `prompts/semantic_ideation_*.yaml` | System prompt principale | OK |

---

# 2. COMPRENSIONE DEL DOCX

## 2.1 Titolo e scopo dichiarato

**"AI LCA modelling — System Prompt 1"**  
Prompt per un agente LCA specializzato nella selezione di processi ecoinvent e costruzione di modelli di ciclo di vita.

## 2.2 Logica a 7 passi dichiarata nel DOCX

Il documento descrive un **modello di ragionamento sequenziale** in 7 passi:

| Passo | Logica |
|-------|--------|
| **Passo 1** | Materiale o oggetto? L'agente classifica l'input: è una sostanza grezza o un prodotto fisico? |
| **Passo 2** | Lookup aggregato: esiste già un dataset ecoinvent per questo prodotto completo? |
| **Passo 3** | Inferenza materiale: se non specificato, ragiona per esclusione tecnica e dichiara l'assunzione |
| **Passo 4** | Mapping processo: la geometria dell'oggetto vincola il processo manifatturiero |
| **Passo 5** | Multi-componente: scomponi in parti se ci sono materiali/geometrie diverse |
| **Passo 6** | Logistica: se c'è un tratto di trasporto esplicito, calcola i tkm. Verifica doppio conteggio con dataset "market" |
| **Passo 7** | Gap analysis: massa? materiale? geografia? Se manca qualcosa → chiedi o dichiara assunzione |

## 2.3 Principi fondamentali del DOCX

1. **Dichiarazione esplicita delle assunzioni** — ogni inferenza va dichiarata come tale
2. **Esclusione del doppio conteggio logistico** — dataset "market" include già trasporto; non aggiungerlo due volte
3. **Geometria → Processo** — la forma fisica dell'oggetto determina il processo, non viceversa
4. **Dati mancanti → richiesta esplicita** — non procedere mai con valori inventati tacitamente

## 2.4 Esempi chiave nel DOCX

- **Esempio 1** (PP grezzo): materiale puro → nessun processo di trasformazione da aggiungere
- **Esempio 2** (sedia PP Svezia + trasporto 800 km): PP injection moulding + trasporto aggiuntivo (3.6 tkm calcolati esplicitamente), con dichiarazione che il dataset market copre già il trasporto base
- **Esempio 3** (sedia generica): massa mancante → gap analysis → richiesta all'utente

## 2.5 Mismatch critico DOCX vs. Implementazione

| Concetto DOCX | Implementazione reale | Delta |
|--------------|----------------------|-------|
| Dichiarazione assunzioni in output | `assumptions_list` in stato, mostrato in UI | ✅ Parzialmente corretto |
| Doppio conteggio logistico (is_market) | `is_market` non restituito da `find_closest_match` | ❌ Bug: sempre False |
| Calcolo tkm esplicito | `dist_km = 500.0` hardcoded | ❌ Ignorato se geography vaga |
| 7 passi visibili all'utente | `current_lca_step` salta da 1 a 7 | ❌ Tracker fittizio |
| Gap analysis → domande utente | Implementata, ma routing fragile | ⚠️ Funziona ma rischioso |
| Geometria → processo | GEOMETRY_MAPPING corretto | ✅ Corretto |
| Dataset "market" check | `is_market` calcolato ma mai usato | ❌ Logica presente ma rotta |

---

# 3. VISIONE IDEALE DEL PRODOTTO

## 3.1 Cosa il prodotto DOVREBBE essere

Un **Co-Pilot di ingegneria della sostenibilità** che:

1. Guida un product engineer/designer attraverso la decomposizione tecnica di un prodotto
2. Raccoglie i 4 Pilastri (Dimensioni, Carico, Ambiente, Durata) in modo dialogico e preciso
3. Decompone il prodotto in BOM con processi manifatturieri basati su geometria reale
4. Calcola l'impronta LCA originale con dati reali dal database ecoinvent (DataSet.xlsx)
5. Propone 3 alternative per componente (Eco-Max / Balanced / Drop-in) con giustificazione tecnica
6. Calcola MCDA reale multi-criterio (CO₂ + costo + energia + acqua)
7. Mostra chiaramente ogni assunzione fatta, ogni fallback usato, ogni limitazione dei dati
8. Produce un report professionale scaricabile con tutti i numeri verificabili

## 3.2 Flusso UX ideale

```
[1] Onboarding chiaro → utente capisce cosa fa il sistema in 10 secondi
[2] Utente descrive prodotto → agente analizza
[3] Gap analysis → domande precise solo se necessarie (non fastidiose)
[4] Conferma BOM + workflow → utente può modificare
[5] Visualizzazione progressiva → ogni fase aggiorna la dashboard
[6] Risultati MCDA → chiari, leggibili, con ranking visivo
[7] Download report → PDF professionale con tutti i dati
```

## 3.3 Architettura ideale

```
ui/app.py                    ← Streamlit UI (puro rendering, nessuna logica business)
agents/
  graph.py                   ← LangGraph (solo topologia, interrupt dichiarativi)
  state.py                   ← AgentState arricchito con current_phase
  nodes/
    constraint_extractor.py  ← Nodo 1 (ora file separato)
    workflow_bom_node.py      ← Nodo 2 (FASE 0-2 SOP)
    material_ideator_node.py  ← Nodo 3 (FASE 3-4 SOP)
    lca_validator_node.py     ← Nodo 4 (calcolo deterministico)
    mcda_scorer_node.py       ← Nodo 5 (scoring multi-criterio)
    human_feedback_node.py    ← Nodo 6 (unico, routing su current_phase)
  schemas.py                 ← Pydantic (invariato, ottimo)
data/
  lca_interface.py           ← ABC (invariato)
  csv_lca_client.py          ← Bug fixati, is_market corretto
  provider_factory.py        ← Thread-safe con lock
core/
  config.py                  ← (invariato)
  llm_factory.py             ← + caching istanza LLM
prompts/
  semantic_ideation_api.yaml ← (invariato, ottimo)
reports/
  generator.py               ← Fix field name co2_eq_kg → environmental_impact
```

---

# 4. PROBLEMI SISTEMICI

## 4.1 Classificazione per gravità

### 🔴 CRITICI (bloccano correttezza dei dati)

| ID | Problema | Impatto |
|----|----------|---------|
| C1 | `generator.py` usa `co2_eq_kg` (inesistente) invece di `environmental_impact` | Report HTML/PDF sempre vuoto/crashato |
| C2 | `find_closest_match` non restituisce `is_market` | Trasporto sempre doppio-contato |
| C3 | API key OpenRouter esposta in `.env` | Security breach |
| C4 | MCDA usa `energy_mj=0`, `water_l=0`, `cost_per_kg=0` sempre | MCDA = ranking CO₂ mascherato |

### 🟠 ALTI (degradano funzionalità)

| ID | Problema | Impatto |
|----|----------|---------|
| A1 | `current_lca_step` salta 1→7 | Tracker fittizio, inganna l'utente |
| A2 | `dist_km = 500.0` hardcoded | Campo geografia decorativo |
| A3 | Routing fragile: errore workflow → interview | Bug silenzioso su exception path |
| A4 | `human_feedback_processor` triplicato | Fragile, logica implicita |
| A5 | Fallback LCA 3.5 kg CO₂ silenzioso | Viola "Regola d'Oro" del progetto |
| A6 | `constraint_extractor` sincrono nell'event loop async | Blocco event loop |

### 🟡 MEDI (degradano qualità e UX)

| ID | Problema | Impatto |
|----|----------|---------|
| M1 | Codice morto: `bom_decomposer`, `semantic_ideator` in nodes.py | Confusione manutenzione |
| M2 | Inconsistenza linguistica (IT/EN misto) | UX confusa |
| M3 | "Restart Session" senza confirm dialog | Perdita dati accidentale |
| M4 | Cache PDF con `id()` come key | Non affidabile tra rerun |
| M5 | CSS in `st.html()` dentro colonna | Scope non garantito |
| M6 | Singleton LCA non thread-safe | Race condition multi-utente |
| M7 | LLM non cached (nuova istanza ogni nodo) | Performance degraded |

### 🟢 BASSI (debt tecnico)

| ID | Problema |
|----|----------|
| B1 | Empty states non gestiti visivamente |
| B2 | Nessun scroll automatico chat |
| B3 | `auto` mode inaccessibile da UI |
| B4 | `nest_asyncio` workaround fragile su Windows |
| B5 | Schema validation DataSet.xlsx senza recovery |
