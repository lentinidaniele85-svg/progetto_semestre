# TRANSFORMATION ROADMAP — Sustainable Product Optimization Agent
## Principal Architect Edition | Part 2 of 3

---

# 5. NUOVA ARCHITETTURA PROPOSTA

## 5.1 Principi Architetturali

1. **Separazione di responsabilità totale** — ogni file ha una sola ragione per cambiare
2. **Stato esplicito** — `current_phase` come campo di stato, non logica implicita
3. **Nodo feedback unificato** — un solo `human_feedback_processor`, routing su `current_phase`
4. **Fallback sempre visibili** — ogni fallback aggiorna `assumptions_list`, notificato in UI
5. **LCA deterministico completo** — `is_market`, `energy_mj`, `cost_per_kg` reali dal dataset

## 5.2 AgentState ideale

```python
class AgentState(TypedDict, total=False):
    # INPUT
    user_input: str
    mode: Literal["auto", "interactive"]

    # PHASE TRACKING (nuovo — elimina routing implicito)
    current_phase: Literal[
        "init", "interview", "constraints", "workflow",
        "material", "lca", "mcda", "complete", "error"
    ]
    current_lca_step: int  # 1-7, aggiornato ad ogni nodo

    # CORE DATA
    constraints: dict
    bom: list[dict]
    workflow_steps: list[dict]
    semantic_alternatives: list[dict]
    lca_results: list[dict]
    mcda_scores: list[dict]

    # UX / FEEDBACK
    chat_history: list[dict]
    thought_log: list[str]
    pending_feedback: str | None
    assumptions_list: list[str]
    error_message: str | None  # nuovo — errori visibili invece di silenti

    # DATI DERIVATI
    detected_geometry: str
    logistics_data: dict
```

## 5.3 Grafo ideale (topologia pulita)

```python
# graph.py — ideale
graph.add_node("constraint_extractor", constraint_extractor)
graph.add_node("workflow_bom_ideator", workflow_bom_ideator)
graph.add_node("material_ideator", material_ideator)
graph.add_node("lca_validator", lca_validator)
graph.add_node("mcda_scorer", mcda_scorer)
graph.add_node("human_feedback_processor", human_feedback_processor)  # uno solo!

graph.add_edge(START, "constraint_extractor")
graph.add_edge("constraint_extractor", "human_feedback_processor")
graph.add_edge("human_feedback_processor", "workflow_bom_ideator")
graph.add_conditional_edges("workflow_bom_ideator", route_after_workflow)
graph.add_edge("human_feedback_processor", "material_ideator")  # se phase=="workflow"
graph.add_edge("material_ideator", "lca_validator")
graph.add_edge("lca_validator", "mcda_scorer")
graph.add_edge("mcda_scorer", END)

# route_after_workflow basato su current_phase — NON su (pending_feedback AND bom)
def route_after_workflow(state):
    if state.get("current_phase") == "interview":
        return "human_feedback_processor"   # torna per intervista
    return "human_feedback_processor"       # torna per approvazione workflow
```

## 5.4 csv_lca_client.py ideale (bug fissi)

```python
def find_closest_match(self, label: str, threshold: float = 0.5) -> dict | None:
    # ...
    return {
        "id": row["id"],
        "providerName": row["processname"],
        "flowName": row["outputname"],
        "location": row["location"],
        "environmental_impact": float(row["climatechangeimpact"]),
        "is_market": "market" in str(row["processname"]).lower(),  # FIX C2
    }

async def get_impact_scores(self, material_id: str) -> dict | None:
    # ...
    return {
        "environmental_impact": float(r["climatechangeimpact"]),
        "is_market": "market" in str(r["processname"]).lower(),
        "energy_mj": float(r.get("energyimpact", 0.0)),   # se colonna presente
        "water_l": float(r.get("waterimpact", 0.0)),       # se colonna presente
        "cost_tier": _estimate_cost_tier(r),
        "cost_per_kg": _estimate_cost_per_kg(r),
        "lifespan_years": 10.0,
    }
```

## 5.5 generator.py ideale (bug fisso)

```python
# FIX C1: era "co2_eq_kg", deve essere "environmental_impact"
orig_co2: dict[str, float] = {
    r["component_name"]: r["original_scores"]["environmental_impact"]
    for r in lca_results
}
# FIX: era "co2_reduction_pct", deve essere "impact_reduction_pct"
total_opt += orig * (1 - best["impact_reduction_pct"] / 100) if best else orig
```

---

# 6. STRATEGIA DI TRASFORMAZIONE

## 6.1 Principio guida

**Non riscrivere, correggere progressivamente.**  
Il progetto ha basi architetturali solide. La strategia è chirurgica: fix bug critici prima, poi refactoring UX, poi architettura avanzata.

## 6.2 Fasi di trasformazione

```
FASE 1 — Stabilità (1-2 giorni)
  Fix bug critici che producono output errati
  Nessuna modifica architetturale
  
FASE 2 — Correttezza (3-5 giorni)
  Fix logica di prodotto (is_market, distanza, tracker)
  Pulizia codice morto
  
FASE 3 — UX (5-7 giorni)
  Uniformità linguistica
  Progressive disclosure dashboard
  Confirm dialog, toast notifications
  
FASE 4 — Architettura (7-14 giorni)
  current_phase nello stato
  Unificazione human_feedback_processor
  Thread safety, LLM caching
  
FASE 5 — Avanzato (futuro)
  RAG/embeddings per matching semantico
  MCDA dinamico con slider AHP
  API Maps per logistica reale
```

---

# 7. ROADMAP COMPLETA CON TASK

## Task Overview (ordinati per esecuzione)

| # | Task | Fase | Priorità | Complessità | Impatto |
|---|------|------|----------|-------------|---------|
| T01 | Fix report generator field names | 1 | 🔴 Critica | Bassa | Alto |
| T02 | Fix is_market in find_closest_match | 1 | 🔴 Critica | Bassa | Alto |
| T03 | Ruotare API key + usare st.secrets | 1 | 🔴 Critica | Bassa | Sicurezza |
| T04 | Fix fallback silenzioso 3.5 CO₂ | 1 | 🔴 Critica | Bassa | Alto |
| T05 | Progress tracker reale (step 1-7) | 2 | 🟠 Alta | Media | Medio |
| T06 | Rimuovere codice morto nodes.py | 2 | 🟠 Alta | Bassa | Medio |
| T07 | Fix routing su errore (current_phase) | 2 | 🟠 Alta | Media | Alto |
| T08 | Distanza logistica da LLM o warning | 2 | 🟠 Alta | Media | Medio |
| T09 | Unifica lingua (tutto inglese) | 3 | 🟡 Media | Media | Basso |
| T10 | Confirm dialog Restart Session | 3 | 🟡 Media | Bassa | Basso |
| T11 | Progressive disclosure dashboard | 3 | 🟡 Media | Media | Medio |
| T12 | Toast/warning per assunzioni | 3 | 🟡 Media | Bassa | Medio |
| T13 | Thread-safe LCA singleton | 4 | 🟡 Media | Media | Medio |
| T14 | LLM caching in ModelFactory | 4 | 🟡 Media | Bassa | Basso |
| T15 | Refactor human_feedback_processor | 4 | 🟡 Media | Alta | Alto |
| T16 | MCDA reale (energy + cost dal dataset) | 4 | 🟡 Media | Alta | Alto |
| T17 | constraint_extractor → async | 4 | 🟢 Bassa | Bassa | Basso |
| T18 | Cache PDF con hash content | 4 | 🟢 Bassa | Bassa | Basso |

---

# 8. BREAKDOWN TASK-BY-TASK

---

## T01 — Fix Report Generator Field Names

**Obiettivo:** Correggere i nomi dei campi errati in `generator.py` che causano crash/valori zero nel report.

**Perché esiste:** Il report HTML/PDF è la feature di output principale del sistema. Con i campi sbagliati, genera sempre valori a zero o lancia KeyError.

**Problema che risolve:** C1 — `co2_eq_kg` non esiste nello stato; il campo corretto è `environmental_impact`. Il campo `co2_reduction_pct` non esiste; il corretto è `impact_reduction_pct`.

**File coinvolti:** `reports/generator.py`

**Dipendenze:** Nessuna

**Rischi:** Nessuno (fix meccanico di nomi)

**Cosa NON rompere:** La struttura HTML del report, i calcoli totali, il CSS

**Come verificare:** Eseguire il pipeline completo, scaricare HTML, verificare che i totali CO₂ siano > 0 e coerenti con i valori nel dashboard

**Output atteso:** Report HTML con valori CO₂ reali, non zero

**Checklist:**
- [ ] Sostituire `r["original_scores"]["co2_eq_kg"]` con `r["original_scores"]["environmental_impact"]` (L19)
- [ ] Sostituire `best["co2_reduction_pct"]` con `best["impact_reduction_pct"]` (L28, L51, L63)
- [ ] Verificare tutti gli altri accessi a campi `mcda_scores` (L52: `best['mcda_score']` — OK)

**Priorità:** 🔴 Immediata  
**Complessità:** 1/5  
**Impatto architetturale:** Nullo (fix puntuale)

---

## T02 — Fix is_market in find_closest_match

**Obiettivo:** Aggiungere il campo `is_market` al dizionario restituito da `find_closest_match` in `csv_lca_client.py`.

**Perché esiste:** Il calcolo del trasporto LCA dipende da `is_market`: se il dataset del materiale include già il trasporto di mercato, non va aggiunto di nuovo. Senza questo fix, il trasporto viene sempre contato doppio (violando la logica del DOCX al Passo 6).

**Problema che risolve:** C2 + logica esplicita del DOCX "verifica doppio conteggio"

**File coinvolti:** `data/csv_lca_client.py` (metodo `find_closest_match`, riga 79-85)

**Dipendenze:** Nessuna

**Rischi:** Nessuno

**Cosa NON rompere:** Il formato del dizionario restituito (aggiunta additive, non sostituzione)

**Come verificare:** Test unitario: cercare un materiale con "market" nel nome processo → `is_market` deve essere True. Cercare un materiale senza "market" → `is_market` deve essere False.

**Output atteso:**
```python
{
    "id": "...",
    "providerName": "market for polypropylene...",
    "flowName": "polypropylene...",
    "location": "RER",
    "environmental_impact": 2.1,
    "is_market": True   # ← nuovo campo
}
```

**Checklist:**
- [ ] Aggiungere `"is_market": "market" in str(row["processname"]).lower()` al dict di ritorno (dopo L84)
- [ ] Verificare che `lca_validator` in `nodes.py` legga già `is_market` dal dict (L276: `orig_match.get("is_market", False)`) — sì, già corretto
- [ ] Aggiungere test in `tests/test_data_layer.py`

**Priorità:** 🔴 Immediata  
**Complessità:** 1/5  
**Impatto architetturale:** Nullo

---

## T03 — Rotazione API Key + Secrets Management

**Obiettivo:** Rimuovere la API key hardcoded dal `.env`, ruotarla sul provider, configurare il sistema per usare variabili d'ambiente o `st.secrets`.

**Perché esiste:** Una API key esposta in un file git-tracked è una vulnerabilità critica. Anche se `.gitignore` include `.env`, il file potrebbe essere stato committato in passato.

**Problema che risolve:** C3 — Security

**File coinvolti:** `.env`, `core/config.py`, `README.md`

**Dipendenze:** Nessuna

**Rischi:** Se la chiave viene ruotata senza aggiornare la config locale, l'app non parte. Comunicare il processo chiaramente.

**Cosa NON rompere:** Il meccanismo di lettura dalla config (pydantic-settings funziona con env vars di sistema oltre che con `.env`)

**Come verificare:** Avviare l'app con `OPENROUTER_API_KEY` come variabile d'ambiente (non in `.env`); verificare che si connetta correttamente.

**Checklist:**
- [ ] Ruotare la chiave sul pannello OpenRouter
- [ ] Rimuovere la chiave reale da `.env` (lasciare solo `OPENROUTER_API_KEY=your_key_here`)
- [ ] Aggiungere `.env` a `.gitignore` (verificare che ci sia già)
- [ ] Aggiornare `README.md` con istruzioni per impostare la chiave
- [ ] Opzionale: aggiungere controllo in `config.py` che validi la chiave non sia vuota in produzione

**Priorità:** 🔴 Immediata  
**Complessità:** 1/5  
**Impatto architetturale:** Nullo

---

## T04 — Fallback LCA Visibile (3.5 kg CO₂ silenzioso)

**Obiettivo:** Ogni volta che `find_closest_match` fallisce e viene usato il valore di fallback 3.5 kg CO₂/kg, aggiungere una entry in `assumptions_list` e mostrare un warning in UI.

**Perché esiste:** Il DOCX dice esplicitamente "dichiara ogni assunzione". Il fallback silenzioso viola la "Regola d'Oro" del progetto (L'LLM non inventa numeri, ma il sistema Python lo fa tacitamente).

**Problema che risolve:** A5 — trasparenza sui dati inventati

**File coinvolti:** `agents/workflow_node.py` (L76-78), `agents/nodes.py` (L278-280, L297-299)

**Dipendenze:** Nessuna (T02 può essere fatto prima/dopo in parallelo)

**Rischi:** Aumento del volume di warnings in UI se il fuzzy match ha bassa copertura. Considerare un cutoff migliore.

**Cosa NON rompere:** Il meccanismo di fallback stesso (deve restare per robustezza); solo aggiungere notifica

**Come verificare:** Inserire un prodotto con materiale sconosciuto (es. "vibranium"); verificare che il warning "Dati LCA non trovati per 'vibranium', usato valore di fallback 3.5 kg CO₂/kg" appaia nella sezione Assunzioni del dashboard.

**Checklist:**
- [ ] In `workflow_node.py` L77-78: quando si usa il fallback 3.5, aggiungere a `assumptions_list` nel return dict
- [ ] In `nodes.py` L277-279: stesso pattern per lca_validator
- [ ] In `nodes.py` L296-299: stesso pattern per alternative fallback
- [ ] Verificare che `app.py` mostri `assumptions_list` con warning (già fa `st.warning` — OK)

**Priorità:** 🔴 Immediata  
**Complessità:** 2/5  
**Impatto architetturale:** Basso

---

## T05 — Progress Tracker Reale (Step 1-7)

**Obiettivo:** Impostare `current_lca_step` progressivamente in ogni nodo del grafo, invece di saltare sempre a 7.

**Perché esiste:** Il tracker a 7 step è l'unico indicatore visivo di avanzamento. Attualmente inganna l'utente mostrando sempre step 1 o 7.

**Problema che risolve:** A1

**File coinvolti:** `agents/nodes.py`, `agents/workflow_node.py`, `agents/material_node.py`

**Dipendenze:** Nessuna

**Rischi:** Basso. Il campo `current_lca_step` è già nello stato.

**Cosa NON rompere:** La visualizzazione nel dashboard (usa `< current_step` per le checkmark — OK)

**Mapping step → nodo:**
```
Step 1 → constraint_extractor (Analisi Entità)
Step 2 → workflow_bom_ideator avvio (Lookup Aggregato)
Step 3 → workflow_bom_ideator inferenza materiale (Selezione Materiale)
Step 4 → workflow_bom_ideator geometria (Vincolo Geometrico)
Step 5 → workflow_bom_ideator BOM completa (Scomposizione BOM)
Step 6 → workflow_bom_ideator logistics (Calcolo Logistica)
Step 7 → lca_validator (Validazione)
```

**Come verificare:** Avviare l'app, osservare che il tracker avanza durante l'esecuzione

**Checklist:**
- [ ] `constraint_extractor`: aggiungere `"current_lca_step": 1` nel return
- [ ] `workflow_bom_ideator` avvio: aggiungere `"current_lca_step": 2` all'inizio
- [ ] `workflow_bom_ideator` dopo fuzzy match: step 3
- [ ] `workflow_bom_ideator` dopo GEOMETRY_MAPPING: step 4
- [ ] `workflow_bom_ideator` BOM completa: step 5
- [ ] `workflow_bom_ideator` logistics: step 6
- [ ] `lca_validator`: step 7 (già c'è, OK)
- [ ] Rimuovere `"current_lca_step": 7` dal path di interview-incomplete in workflow_node.py L61

**Priorità:** 🟠 Alta  
**Complessità:** 2/5  
**Impatto architetturale:** Basso

---

## T06 — Rimozione Codice Morto da nodes.py

**Obiettivo:** Eliminare `bom_decomposer` e `semantic_ideator` da `nodes.py`, che sono stati superseded da `workflow_node.py` e `material_node.py` ma non sono stati rimossi.

**Perché esiste:** Il codice morto occupa ~200 righe, crea confusione a chi legge il file, e contiene una logica diversa (e più vecchia) rispetto all'implementazione reale.

**Problema che risolve:** M1 — debito tecnico, leggibilità

**File coinvolti:** `agents/nodes.py` (righe 111-235)

**Dipendenze:** Nessuna (verificare che nessun import usa queste funzioni)

**Rischi:** Basso, ma verificare con grep prima di eliminare.

**Come verificare:**
```bash
grep -r "bom_decomposer\|semantic_ideator" --include="*.py" .
# Deve restituire solo nodes.py stesso, non import esterni
```

**Checklist:**
- [ ] Grep per confermare zero import di `bom_decomposer` e `semantic_ideator` fuori da nodes.py
- [ ] Eliminare `bom_decomposer` (righe 111-170)
- [ ] Eliminare `semantic_ideator` (righe 177-235)
- [ ] Eliminare le costanti `PROCESS_IMPACTS` e `TRANSPORT_IMPACT_PER_TKM` se non usate (sono in nodes.py L239-245, usate da `lca_validator` — TENERLE)
- [ ] Aggiornare eventuali docstring o commenti che le citano

**Priorità:** 🟠 Alta  
**Complessità:** 1/5  
**Impatto architetturale:** Basso (cleanup)

---

## T07 — Fix Routing su Errore (current_phase nello stato)

**Obiettivo:** Aggiungere `current_phase` allo stato e usarlo nel routing condizionale, eliminando la logica fragile `pending_feedback != None AND bom == []`.

**Perché esiste:** `check_interview_complete` in `graph.py` distingue interview da workflow basandosi su due condizioni implicite. Se `workflow_bom_ideator` fallisce con un'eccezione, imposta `pending_feedback = "Errore..."` e `bom = []` → il grafo va all'interview invece di gestire l'errore.

**Problema che risolve:** A3 + preparazione per T15

**File coinvolti:** `agents/state.py`, `agents/graph.py`, `agents/workflow_node.py`, `agents/nodes.py`

**Dipendenze:** Nessuna (ma T15 estende questo)

**Rischi:** Medio. Cambia la topologia del grafo.

**Cosa NON rompere:** Il flusso happy path (interview → workflow → material)

**Come verificare:** Simulare un errore nel workflow (timeout forzato) e verificare che non venga mostrata una domanda di intervista.

**Checklist:**
- [ ] Aggiungere `current_phase: str` a `AgentState` in `state.py`
- [ ] In `constraint_extractor`: `"current_phase": "constraints"` nel return
- [ ] In `workflow_bom_ideator` path interview: `"current_phase": "interview"`
- [ ] In `workflow_bom_ideator` path success: `"current_phase": "workflow"`
- [ ] In `workflow_bom_ideator` path error: `"current_phase": "error"`, `"error_message": str(exc)`
- [ ] In `graph.py` `check_interview_complete`: cambiare condizione a `state.get("current_phase") == "interview"`
- [ ] Aggiungere gestione error phase in `app.py`

**Priorità:** 🟠 Alta  
**Complessità:** 3/5  
**Impatto architetturale:** Medio

---

## T08 — Distanza Logistica da LLM o Warning Visibile

**Obiettivo:** Se il LLM non riesce a estrarre una distanza reale dal testo, mostrare esplicitamente in UI che viene usata la distanza di default (500 km), aggiungendola a `assumptions_list`.

**Perché esiste:** Il campo `geography` viene estratto dall'LLM ma la distanza effettiva è sempre 500 km hardcoded. L'utente non sa che il dato geografico che ha fornito viene ignorato.

**Problema che risolve:** A2

**File coinvolti:** `agents/workflow_node.py` (L97-103)

**Dipendenze:** Nessuna

**Come verificare:** Fornire una geografia specifica ("Milano, 200 km dal fornitore") e verificare che il warning appaia se non viene estratta la distanza.

**Checklist:**
- [ ] Tentare di estrarre `distance_km` da `result.geography` con regex o campo schema dedicato
- [ ] Se estrazione fallisce: `dist_km = 500.0` ma aggiungere a `assumptions_list`: "Distanza logistica non specificata, usato valore di default 500 km"
- [ ] Aggiungere campo `distance_km: Optional[float]` a `WorkflowAndBOMResponse` schema
- [ ] Istruire il prompt a estrarre la distanza se menzionata

**Priorità:** 🟠 Alta  
**Complessità:** 2/5  
**Impatto architetturale:** Basso
