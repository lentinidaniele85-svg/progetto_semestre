# Due Diligence Report — Sustainable Product Optimization Agent
**Team**: Senior Product Designer · UX/UI Expert · Software Architect · Senior FE/BE · QA · Performance · Product Strategist · Power User

---

## 1. Executive Summary

L'applicazione è un **agente AI per l'ottimizzazione sostenibile dei materiali**, costruito su LangGraph + Streamlit. Il concept è solido e ben documentato internamente (`ai_cognitive_architecture.md`). L'approccio neuro-simbolico (LLM per semantica + Python deterministico per calcoli LCA) è architetturalmente corretto e intelligente.

**Stato reale del prodotto**: MVP accademico funzionante, con bug strutturali non critici ma potenzialmente bloccanti in produzione. Alcune feature sono incomplete o incoerenti. L'UX è confusa per un utente nuovo. Il codice è leggibile ma con debito tecnico significativo. Non è pronto per produzione senza interventi mirati.

**Voto complessivo**: 6.5/10 — Idea forte, esecuzione parziale.

---

## 2. Come Funziona Realmente l'App

### Pipeline effettiva (modalità Interactive)

```
User Input
  └─> constraint_extractor (sync LLM)
        └─> [INTERRUPT] human_feedback_processor_constraints
              └─> workflow_bom_ideator (async, LLM + DataSet.xlsx fuzzy match)
                    ├─> [INTERRUPT] human_feedback_processor_interview (se dati mancanti)
                    │     └─> workflow_bom_ideator (retry)
                    └─> [INTERRUPT] human_feedback_processor_workflow
                          └─> material_ideator (async LLM)
                                └─> lca_validator (deterministico)
                                      └─> mcda_scorer (deterministico)
                                            └─> END → Report HTML/PDF
```

### Flusso reale vs. dichiarato

Il documento `ai_cognitive_architecture.md` descrive "7 passi" ma il grafo reale ha 4 nodi funzionali + 3 nodi feedback. Il tracker "7 Steps" in UI è **decorativo**: il campo `current_lca_step` viene impostato a `7` direttamente da `workflow_bom_ideator` e `lca_validator` senza mai passare per i valori 1-6. L'utente vede sempre il passo 1 o 7, mai gli intermedi.

### Modalità Auto vs. Interactive

In modalità `auto` il grafo si compila senza interrupt. La UI non espone un toggle per cambiare modalità — `st.session_state.mode` è inizializzato a `"interactive"` ma non esiste alcun controllo UI per cambiarlo. La modalità `auto` è sostanzialmente inaccessibile all'utente finale.

---

## 3. Problemi UX/UI

### 3.1 Prima Impressione (Gravità: Alta)

- Il titolo "🌿 Sustainable Product Optimization Agent" è generico. Non spiega il valore in 3 secondi.
- La caption "Glass Box Mode — watch the agent think in real time" presuppone che l'utente sappia cosa sia un agente AI.
- Il layout 1/3 sinistra (chat) + 2/3 destra (dashboard) è non ovvio: l'utente potrebbe non capire perché ci sono due colonne o dove guardare.
- Il dashboard destro è **completamente vuoto** all'avvio: 7 sezioni con caption "will appear here…" comunicano un senso di incompletezza.

### 3.2 Empty States (Gravità: Media)

Ogni sezione del dashboard mostra caption testuali ("The BOM will appear after…") invece di placeholder visivi. Non c'è gerarchia: tutte le sezioni vuote appaiono simultaneamente e con lo stesso peso visivo.

**Fix consigliato**: Mostrare solo le sezioni rilevanti allo step corrente, nascondendo le future con `st.empty()` progressivo.

### 3.3 Progress Tracker "7 Steps" (Gravità: Alta)

Il tracker mostra 7 passi con icone ✅/🔄/⏳, ma:
- `current_lca_step` parte a 1 e passa direttamente a 7 (nessun passo 2-6 viene mai impostato).
- Il tracker è puramente decorativo e **induce in errore** l'utente pensando che ci sia una progressione granulare.
- I nomi dei passi sono in italiano ma la UI è mista italiano/inglese.

### 3.4 Inconsistenza Linguistica (Gravità: Media)

Il codice mescola italiano e inglese senza logica:
- Messaggi assistant: mix ("Ho estratto questi vincoli" + "I need a few more details")
- Nomi passi dashboard: italiano
- Nomi colonne BOM: inglese
- Etichette button: inglese (✅ Approve, ❌ Restart Session)
- Messaggi di errore: inglese
- Commenti codice: italiano + inglese

### 3.5 Bottoni e Interazione (Gravità: Media)

- Il bottone "✅ Approve Constraints" appare **prima** del messaggio che spiega cosa si sta approvando, creando confusione su cosa l'utente stia confermando.
- "❌ Restart Session" è un'azione distruttiva (cancella tutto) senza conferma dialog. Un click accidentale perde l'intera sessione.
- Il bottone Approve e il campo testo fanno la stessa cosa: entrambi accettano "Approve" come input. Duplicazione di pathway non comunicata all'utente.

### 3.6 Chat History (Gravità: Media)

- I messaggi "🤖 Agent steps:\n• Step 1\n• Step 2" duplicano informazioni già visibili nel Thought Log del dashboard. L'utente vede gli stessi pensieri dell'agente in due posti diversi.
- Non c'è scroll automatico verso il basso nella chat.
- Non c'è differenziazione visiva tra i tipi di messaggio assistant (risposta narrativa vs. log tecnico).

### 3.7 CSS Premium Parziale (Gravità: Bassa)

Il CSS `st.html()` è inserito dentro `with left_col:` — ciò significa che gli stili dichiarati (`h3`, `.stChatMessage`, ecc.) si applicano globalmente ma sono dichiarati localmente. Scelta fragile, non mantenibile. Lo stile `h3 { color: #059669 }` va in conflitto con i subheader del pannello destro che usano `st.subheader()` (renderizzati come h3 da Streamlit).

---

## 4. Problemi Architetturali

### 4.1 Nodo `human_feedback_processor` Triplicato (Gravità: Alta)

```python
graph.add_node("human_feedback_processor_constraints", human_feedback_processor)
graph.add_node("human_feedback_processor_interview", human_feedback_processor)
graph.add_node("human_feedback_processor_workflow", human_feedback_processor)
```

La **stessa funzione** è registrata 3 volte come nodi distinti per permettere interrupt granulari. Questo è un workaround di LangGraph, non una vera separazione di responsabilità. La funzione `human_feedback_processor` deve dedurre internamente il contesto (BOM vuota? → fase interview; BOM piena? → fase review) con logica implicita e fragile.

**Problema reale**: Se lo stato BOM è parzialmente popolato (edge case), la distinzione interview/workflow può rompersi silenziosamente.

**Soluzione**: Utilizzare un singolo nodo con un campo stato esplicito `current_phase: Literal["interview", "constraints", "workflow"]` e routing condizionale basato su quel campo.

### 4.2 Routing Condizionale Fragile (Gravità: Alta)

```python
def check_interview_complete(state: AgentState):
    if state.get("pending_feedback") is not None and not state.get("bom"):
        return "human_feedback_processor_interview"
    return "human_feedback_processor_workflow"
```

Questa funzione decide se andare all'interview o al workflow basandosi su due condizioni simultanee (`pending_feedback != None` AND `bom == []`). Ma `pending_feedback` viene impostato anche nei casi di errore (`workflow_node.py` lo usa per comunicare errori: `"pending_feedback": "Errore durante l'analisi. Riprovare."`).

**Bug potenziale**: Se `workflow_bom_ideator` fallisce (exception path) e `bom` è ancora vuota, il grafo va al nodo `human_feedback_processor_interview` invece di gestire l'errore. L'utente vede una richiesta di intervista quando in realtà c'è stato un crash.

### 4.3 Nodo `bom_decomposer` Mai Usato (Gravità: Alta)

In `agents/nodes.py` esiste la funzione `bom_decomposer` (con `semantic_ideator` e relativa logica). In `agents/graph.py` questi nodi **non vengono mai aggiunti al grafo**. Il grafo reale usa `workflow_bom_ideator` (che fa tutto insieme). Il codice in `nodes.py` è **codice morto** che occupa 150+ righe e inganna chiunque legga il file.

La stessa `semantic_ideator` in `nodes.py` non è nel grafo. La logica equivalente è in `material_node.py`.

### 4.4 Async/Sync Mixing Pericoloso (Gravità: Alta)

`workflow_bom_ideator` e `material_ideator` sono funzioni `async` che chiamano `_invoke_structured` (sync) tramite `asyncio.to_thread()`. Questo è corretto in isolamento. Ma `_stream()` in `app.py` usa `graph.astream()` dentro un `asyncio.get_running_loop().run_until_complete()` patchato da `nest_asyncio`.

Questo schema (nest_asyncio + run_until_complete + asyncio.to_thread) è:
- Fragile su Windows (event loop diversi tra thread)
- Non testato sotto carico
- Documentato come workaround nel codice stesso

### 4.5 API Key Esposta nel File .env (Gravità: Critica — Sicurezza)

```

```

Il file `.env` contiene una API key reale committata nel repository. Il `.gitignore` include `.env`, ma il file è comunque presente nella working directory e potrebbe essere stato committato accidentalmente in passato. Questa chiave deve essere **ruotata immediatamente**.

### 4.6 Singleton LCA Provider Non Thread-Safe (Gravità: Media)

```python
_provider_cache: dict[str, LCADataProvider] = {}

def get_lca_provider() -> LCADataProvider:
    if source in _provider_cache:
        return _provider_cache[source]
    ...
    _provider_cache[source] = CSVLcaClient()
```

Il singleton module-level non è protetto da lock. In un contesto multi-thread (Streamlit può usare thread multipli), due richieste simultanee potrebbero creare due istanze, entrambe caricando il DataFrame da disco.

---

## 5. Problemi di Logica di Prodotto

### 5.1 MCDA Score Non Differenzia Mai (Gravità: Alta)

Il calcolo MCDA è:
```python
mcda_score = delta_co2 * 0.40 + delta_cost * 0.30 + delta_energy * 0.15 + delta_water * 0.15
```

Ma `energy_mj`, `water_l`, `cost_per_kg` sono **sempre 0.0** in `get_impact_scores()`:
```python
return {"environmental_impact": float(...), "energy_mj": 0.0, "water_l": 0.0, "cost_tier": 0, "cost_per_kg": 0.0}
```

Quindi `delta_cost = 0`, `delta_energy = 0`, `delta_water = 0`. Il 60% del peso MCDA è **sempre nullo**. L'MCDA è di fatto un ranking per sola CO₂, non un'analisi multi-criterio. Il nome "MCDA" è fuorviante.

### 5.2 Distanza Logistica Hardcoded (Gravità: Alta)

```python
dist_km = 500.0  # Default distance if geography is vague
```

Nonostante il sistema chieda esplicitamente la geografia ("Luogo o distanza per la logistica"), il calcolo usa sempre 500 km fissi. Questo rende il campo geografia decorativo. La documentazione lo menziona come "simplificazione" ma non lo comunica all'utente.

### 5.3 Fallback LCA Silenzioso (Gravità: Media)

Se `find_closest_match()` non trova il materiale nel DataSet.xlsx:
```python
comp["unit_impact_value"] = 3.5  # fallback
```

Il valore 3.5 kg CO₂/kg è arbitrario (circa l'impatto del PP vergine). Non c'è notifica all'utente che il dato è un fallback inventato, non un dato reale dal database. Viola la "Regola d'Oro" dichiarata nel documento architetturale ("L'LLM non ha l'autorità di inventare numeri di impatto ambientale") — ma il sistema Python lo fa comunque silenziosamente.

### 5.4 `is_market` Flag Non Utilizzato Correttamente (Gravità: Media)

`is_market` (se il processo include già il trasporto) viene calcolato in `bom_decomposer` (nodo non nel grafo) e in `lca_validator`. Ma `lca_validator` chiama `provider.find_closest_match()` che **non restituisce** il campo `is_market` (il metodo `find_closest_match` non include quel campo nel dict restituito). Il valore `is_market` è sempre `False` in `lca_validator`, rendendo il calcolo del trasporto sempre attivo.

### 5.5 Report HTML con Campo Sbagliato (Gravità: Media)

In `reports/generator.py`:
```python
orig_co2: dict[str, float] = {
    r["component_name"]: r["original_scores"]["co2_eq_kg"]
    for r in lca_results
}
```

Ma il campo reale nello stato è `"environmental_impact"`, non `"co2_eq_kg"`. Il report HTML genererà sempre `KeyError` → totali a 0. Il report esportato è **rotto per design**, non ha mai funzionato con i dati reali del pipeline corrente.

---

## 6. Problemi Tecnici

### 6.1 Frontend (Streamlit)

- **Stato duplicato**: `st.session_state.graph_state` e lo stato interno di LangGraph (nel checkpointer `MemorySaver`) sono due copie dello stato. Possono divergere se `_stream()` aggiorna solo parzialmente.
- **Rerun pattern**: Ogni azione chiama `st.rerun()` che ricrea l'intera pagina da zero. Con un thought log lungo o una BOM grande, il render diventa lento.
- **CSS via `st.html()`**: Non è il modo raccomandato per iniettare stili globali in Streamlit. Con gli aggiornamenti di Streamlit, `st.html()` potrebbe isolare lo scope del CSS.
- **Cache PDF fragile**: `id(state.get("mcda_scores"))` come cache key è non affidabile — l'id Python di un oggetto in memoria può cambiare tra rerun.

### 6.2 Backend/Agenti

- `constraint_extractor` è **sincrono** mentre tutti gli altri nodi sono asincroni. In modalità async streaming, un nodo sincrono blocca l'event loop.
- `mcda_scorer` è sincrono senza buona ragione — potrebbe semplicemente essere `def`, ma allinearlo agli altri sarebbe più consistente.
- La gestione errori è eccessivamente permissiva: quasi ogni eccezione viene swallowed e sostituita con un fallback silenzioso. In produzione è impossibile sapere quante analisi stiano producendo risultati di fallback.

### 6.3 Data Layer

- `CSVLcaClient` carica un file Excel da 2.4 MB in memoria a ogni avvio del processo. Per un singolo utente è ok. Con Streamlit multi-utente (cloud), ogni sessione potrebbe ricaricare il file.
- `difflib.get_close_matches` con cutoff 0.5 è molto permissivo. Materiali con nomi simili ma profili LCA molto diversi (es. "wood" vs "ywood") possono matchare erroneamente.
- Le colonne richieste dal `_validate_schema()` (`id`, `processname`, `outputname`, `location`, `climatechangeimpact`) sono hardcoded. Se il DataSet.xlsx cambia struttura, il sistema crasha all'avvio senza recovery.

### 6.4 Performance

- Il LLM viene istanziato a ogni chiamata di nodo: `ModelFactory.get_model()` crea una nuova istanza `ChatOllama` o `ChatOpenAI` ogni volta. Nessun caching del client LLM.
- `semantic_ideator` (in `nodes.py`, mai usato nel grafo) itera su ogni componente BOM in loop sequenziale sincrono. Per una BOM con 10 componenti sarebbero 10 chiamate LLM in serie.

---

## 7. Cose Fatte Bene — Da NON Toccare

### 7.1 Architettura Neuro-Simbolica ✅
La separazione netta tra LLM (semantica) e Python deterministico (calcoli LCA/MCDA) è la scelta progettuale più corretta del sistema. Va preservata e rafforzata.

### 7.2 Schema Pydantic Rigoroso ✅
L'uso di Pydantic per forzare l'output strutturato dell'LLM, con fallback a raw text parsing, è un pattern robusto. Il meccanismo `_invoke_structured` con retry è ben pensato.

### 7.3 Provider Factory + Interfaccia Astratta ✅
`LCADataProvider` come ABC con `CSVLcaClient` come implementazione e `provider_factory.py` per il routing è pattern corretto. Permette di aggiungere `ecoinvent_api` senza toccare il resto del codice.

### 7.4 Test Suite ✅
La test suite con mock LLM è concettualmente corretta. I test coprono l'intero pipeline end-to-end senza dipendenze esterne.

### 7.5 Caching LCA Provider ✅
Il singleton `_provider_cache` evita di ricaricare il DataSet.xlsx a ogni richiesta. Corretta ottimizzazione di performance.

### 7.6 Gestione Errori Connessione UI ✅
La gestione differenziata degli errori di connessione (`_CONNECTION_ERRORS`) con messaggio persistente (`_last_error`) che sopravvive ai `st.rerun()` è una soluzione elegante al problema del ciclo di vita Streamlit.

---

## 8. Cose Da Rifare — Con Priorità

### Priorità Immediata (blocca correttezza dei dati)

| # | Problema | File | Fix |
|---|----------|------|-----|
| 1 | Report HTML usa `co2_eq_kg` invece di `environmental_impact` | `reports/generator.py` L19, L28, L51, L63 | Rename field key |
| 2 | `is_market` non restituito da `find_closest_match` | `data/csv_lca_client.py` L66-86 | Aggiungere `"is_market"` al dict restituito |
| 3 | API key esposta in `.env` | `.env` | Ruotare chiave, usare secrets manager |
| 4 | MCDA score usa campi sempre zero | `data/csv_lca_client.py` L88-103 | Popolare `energy_mj`, `cost_per_kg` dal DataSet |

### Priorità Breve Termine (correttezza funzionale)

| # | Problema | Fix |
|---|----------|-----|
| 5 | Progress tracker decorativo | Impostare `current_lca_step` progressivamente in ogni nodo |
| 6 | Codice morto (`bom_decomposer`, `semantic_ideator` in nodes.py) | Rimuovere o documentare come deprecated |
| 7 | Distanza logistica hardcoded a 500km | Estrarre dalla risposta LLM o notificare in UI |
| 8 | Fallback LCA silenzioso (3.5 kg CO₂) | Aggiungere a `assumptions_list`, mostrare warning in UI |
| 9 | Routing fragile su errore workflow | Aggiungere campo `current_phase` allo stato |

### Priorità Medio Termine (qualità e UX)

| # | Problema | Fix |
|---|----------|-----|
| 10 | Inconsistenza linguistica | Scegliere italiano O inglese, uniformare tutto |
| 11 | Nodo `human_feedback_processor` triplicato | Refactoring con campo stato esplicito |
| 12 | Empty states non gestiti visivamente | Placeholder card, progressive disclosure |
| 13 | Confirm dialog per "Restart Session" | `st.dialog()` di conferma |
| 14 | CSS iniettato in colonna | Spostare in `st.html()` a livello pagina o `config.toml` |
| 15 | Cache PDF con `id()` | Usare hash del contenuto come cache key |

---

## 9. Refactoring Consigliato

### 9.1 Stato Esplicito della Fase

```python
# Aggiungere ad AgentState
current_phase: Literal["interview", "constraints", "workflow", "material", "lca", "mcda", "complete"]
```

Ogni nodo imposta `current_phase` al valore corretto. La UI usa questo campo per mostrare solo le sezioni rilevanti e per aggiornare il progress tracker in modo accurato. Elimina la logica implicita del routing.

### 9.2 Unificare human_feedback_processor

```python
async def human_feedback_processor(state: AgentState) -> dict:
    phase = state.get("current_phase", "constraints")
    if phase == "interview":
        return _handle_interview(state)
    elif phase == "constraints":
        return _handle_constraints(state)
    elif phase == "workflow":
        return _handle_workflow(state)
```

Un solo nodo, un solo interrupt, routing basato su stato esplicito.

### 9.3 Popolare Campi Mancanti nel DataSet

```python
# csv_lca_client.py - get_impact_scores
return {
    "environmental_impact": float(r["climatechangeimpact"]),
    "is_market": "market" in str(r["processname"]).lower(),
    "energy_mj": float(r.get("energyimpact", 0.0)),  # se presente nel dataset
    "water_l": float(r.get("waterimpact", 0.0)),
    "cost_tier": _estimate_cost_tier(r),
    "cost_per_kg": _estimate_cost_per_kg(r),
    "lifespan_years": 10.0,
}
```

### 9.4 Rimuovere Codice Morto

`nodes.py` va ridotto a: `constraint_extractor`, `lca_validator`, `mcda_scorer`, `human_feedback_processor` + helpers. `bom_decomposer` e `semantic_ideator` vanno eliminati (superseded da `workflow_node.py` e `material_node.py`).

---

## 10. Roadmap Consigliata

### Sprint 1 — Bug Critici (1-2 giorni)
1. Fix `co2_eq_kg` → `environmental_impact` nel report generator
2. Fix `is_market` in `find_closest_match`
3. Ruotare API key e usare `st.secrets`
4. Aggiungere `assumptions_list` warning per fallback 3.5

### Sprint 2 — Correttezza Funzionale (3-5 giorni)
5. Progress tracker reale (impostare step 1-7 in ogni nodo)
6. Rimuovere codice morto da `nodes.py`
7. Fix routing su errore (campo `current_phase`)
8. Distanza logistica: usare valore LLM o mostrare assunzione

### Sprint 3 — Qualità UX (5-7 giorni)
9. Unificare lingua (tutto italiano o tutto inglese)
10. Confirm dialog per Restart Session
11. Progressive disclosure nel dashboard (mostrare sezioni per step)
12. Aggiungere toast/notification per assunzioni e fallback

### Sprint 4 — Architettura (7-14 giorni)
13. Refactoring `human_feedback_processor` unificato con `current_phase`
14. MCDA reale: popolare energy/cost dal dataset o da LLM strutturato
15. RAG/embeddings per fuzzy match semantico (ChromaDB)

### Sprint 5 — Scalabilità (futuro)
16. SQLite per DataSet.xlsx (query efficienti)
17. API Maps per logistica reale
18. MCDA dinamico con slider AHP nell'UI

---

## 11. Analisi Rischio Futuro

| Rischio | Probabilità | Impatto | Note |
|---------|-------------|---------|------|
| Dataset cambia struttura → crash avvio | Media | Critico | Schema validation rigida senza recovery |
| nest_asyncio smette di funzionare su update Streamlit | Alta | Critico | Workaround noto, non soluzione |
| API key OpenRouter esposta → costi non autorizzati | Alta | Alto | Chiave hardcoded in `.env` |
| MCDA scoreboard identici per tutti → nessuna differenziazione | Certa | Alto | Cost/Energy sempre 0 |
| Report HTML sempre vuoto → feature inutile | Certa | Medio | Field name sbagliato |
| Ollama timeout → UI congela | Alta | Medio | 120s timeout ma nessun feedback visivo |
| Multi-user Streamlit → singleton non thread-safe | Media | Medio | Reload multipli DataFrame |

---

## 12. Analisi Qualità Generale

| Area | Voto | Note |
|------|------|------|
| Concept di prodotto | 9/10 | Idea forte, differenziante, ben documentata |
| Architettura generale | 7/10 | Pattern corretti, ma workaround problematici |
| Qualità codice | 6/10 | Leggibile, ma con codice morto e inconsistenze |
| UX/UI | 5/10 | Confusa per nuovi utenti, linguaggio misto |
| Correttezza dati | 4/10 | MCDA parzialmente finto, report rotto, fallback silenzioso |
| Test coverage | 6/10 | Pipeline coperta ma edge cases non testati |
| Sicurezza | 3/10 | API key esposta, nessun auth, nessuna sanitizzazione input |
| Performance | 6/10 | Ok per singolo utente, fragile in scalabilità |
| Documentazione | 8/10 | `ai_cognitive_architecture.md` eccellente per un progetto accademico |

---

## 13. Conclusione da Principal Architect

**Se fossi il responsabile tecnico di questo progetto**, la valutazione è questa:

**Il progetto ha un'anima buona e un'esecuzione incompleta.**

La scelta architetturale neuro-simbolica è genuinamente corretta e non comune. L'uso di LangGraph con checkpoint e interrupt per un flusso human-in-the-loop è la strada giusta. La separazione tra LCA deterministico e LLM semantico è un principio da preservare assolutamente.

**Cosa farei subito (in ordine):**

1. **Ruotare la chiave API** — non è negoziabile.
2. **Fixare il report generator** — è la feature di output principale ed è rotta.
3. **Eliminare il codice morto** — `nodes.py` con `bom_decomposer` e `semantic_ideator` crea confusione a chiunque manutenga il codice.
4. **Rendere il progress tracker reale** — è l'unico indicatore visivo di avanzamento e attualmente mente all'utente.
5. **Documentare esplicitamente i fallback** — ogni volta che il sistema usa un valore inventato (3.5 kg CO₂, 500 km), deve dirlo chiaramente in UI.

**Cosa NON farei ora:** non refactorizerei l'architettura del grafo o il sistema di feedback finché i bug di correttezza dati non sono risolti. Non ha senso migliorare l'UX di un sistema che produce output parzialmente errati.

**Giudizio finale**: Questo è un ottimo progetto accademico con basi architetturali solide. Con 2 sprint di lavoro sui bug critici e la UX, potrebbe essere una demo convincente. Per diventare un prodotto reale, richiede un refactoring mirato (non un rewrite) e l'implementazione reale dei criteri MCDA con dati energetici e di costo dal dataset.

---
*Report generato il 2026-05-10 — Sustainable Product Optimization Agent Due Diligence*
