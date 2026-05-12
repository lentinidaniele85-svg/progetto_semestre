## T09 — Unifica Lingua (tutto inglese)

**Obiettivo:** Scegliere l'inglese come lingua unica per tutti i messaggi UI, nomi passo dashboard, bottoni e messaggi assistant.

**File coinvolti:** `ui/app.py`, `agents/nodes.py`, `agents/workflow_node.py`, `agents/material_node.py`

**Dipendenze:** Nessuna

**Checklist:**
- [ ] `app.py` L221-226: tradurre messaggi assistant italiani in inglese
- [ ] `app.py` L461-468: tradurre nomi steps ("Analisi Entità" → "Entity Analysis", ecc.)
- [ ] `app.py` L502: "La creazione dell'oggetto..." → inglese
- [ ] `agents/nodes.py` thought_log: tradurre tutti i messaggi italiani
- [ ] `agents/workflow_node.py` thought_log e error messages: tradurre
- [ ] Prompt YAML: già in inglese/italiano misto — decidere e uniformare

**Priorità:** 🟡 Media | **Complessità:** 2/5

---

## T10 — Confirm Dialog per Restart Session

**Obiettivo:** Aggiungere un dialogo di conferma prima di eseguire `handle_reject()` per evitare perdita accidentale della sessione.

**File coinvolti:** `ui/app.py` (L415-417 e L420-422)

**Dipendenze:** Nessuna

**Come verificare:** Cliccare "Restart Session", verificare che appaia un dialog modale con "Are you sure?" prima di resettare.

**Implementazione:**
```python
@st.dialog("Restart Session")
def _confirm_restart():
    st.warning("This will delete the current analysis. Are you sure?")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Yes, restart", type="primary"):
            handle_reject()
            st.rerun()
    with col2:
        if st.button("Cancel"):
            st.rerun()
```

**Priorità:** 🟡 Media | **Complessità:** 1/5

---

## T11 — Progressive Disclosure Dashboard

**Obiettivo:** Mostrare le sezioni del dashboard solo quando i dati sono disponibili, usando `current_phase` per decidere cosa visualizzare.

**File coinvolti:** `ui/app.py` (colonna destra, righe 452-663)

**Dipendenze:** T07 (current_phase nello stato)

**Logica:**
```
phase == "init"        → mostra solo onboarding message
phase == "constraints" → mostra Thought Log
phase == "interview"   → mostra Thought Log + domande
phase == "workflow"    → mostra Thought Log + Workflow + BOM
phase == "material"    → mostra + LCA Alternatives
phase == "lca"/"mcda"  → mostra + chart + MCDA table
phase == "complete"    → mostra tutto + download buttons
```

**Priorità:** 🟡 Media | **Complessità:** 3/5

---

## T12 — Toast/Warning per Assunzioni e Fallback

**Obiettivo:** Ogni entry in `assumptions_list` deve essere mostrata come `st.warning` con icona chiara, non nascosta nell'expander del thought log.

**File coinvolti:** `ui/app.py` (già c'è `st.warning` per assumptions ma con formattazione migliorabile)

**Dipendenze:** T04 (fallback visibile)

**Miglioramento:** Distinguere visivamente tra assunzioni normali (arancione) e fallback critici (rosso):
```python
for assumption in assumptions:
    if "fallback" in assumption.lower() or "3.5" in assumption:
        st.error(f"⚠️ Data fallback: {assumption}", icon="🔴")
    else:
        st.warning(f"ℹ️ Assumption: {assumption}", icon="🟡")
```

**Priorità:** 🟡 Media | **Complessità:** 1/5

---

## T13 — Thread-Safe LCA Singleton

**Obiettivo:** Proteggere `_provider_cache` con un `threading.Lock` per evitare race condition in Streamlit multi-utente.

**File coinvolti:** `data/provider_factory.py`

**Implementazione:**
```python
import threading
_provider_cache: dict[str, LCADataProvider] = {}
_cache_lock = threading.Lock()

def get_lca_provider() -> LCADataProvider:
    source = settings.lca_data_source
    with _cache_lock:
        if source not in _provider_cache:
            _provider_cache[source] = CSVLcaClient()
        return _provider_cache[source]
```

**Priorità:** 🟡 Media | **Complessità:** 1/5

---

## T14 — LLM Caching in ModelFactory

**Obiettivo:** Cachare l'istanza LLM invece di crearne una nuova ad ogni chiamata di nodo.

**File coinvolti:** `core/llm_factory.py`

**Implementazione:**
```python
_model_cache: dict[str, BaseChatModel] = {}

@staticmethod
def get_model() -> BaseChatModel:
    key = f"{settings.llm_provider}:{settings.ollama_model or settings.openrouter_model}"
    if key not in _model_cache:
        # ... build model ...
        _model_cache[key] = model
    return _model_cache[key]
```

**Priorità:** 🟡 Media | **Complessità:** 1/5

---

## T15 — Refactor human_feedback_processor Unificato

**Obiettivo:** Unificare i 3 nodi `human_feedback_processor_*` in un singolo nodo con routing basato su `current_phase`.

**File coinvolti:** `agents/graph.py`, `agents/nodes.py`

**Dipendenze:** T07 (current_phase deve esistere nello stato)

**Grafo risultante:**
```python
graph.add_node("human_feedback_processor", human_feedback_processor)
# Un solo nodo, tre interrupt points → gestiti da current_phase
```

**Routing:**
```python
async def human_feedback_processor(state: AgentState) -> dict:
    phase = state.get("current_phase", "constraints")
    if phase == "interview":
        return await _handle_interview(state)
    elif phase == "constraints":
        return await _handle_constraints(state)
    elif phase == "workflow":
        return await _handle_workflow(state)
```

**Priorità:** 🟡 Media | **Complessità:** 4/5

---

## T16 — MCDA Reale (Energy + Cost dal Dataset)

**Obiettivo:** Popolare `energy_mj` e `cost_per_kg` con valori reali dal DataSet.xlsx, o con stime basate sulla categoria materiale, invece di restituire sempre 0.0.

**File coinvolti:** `data/csv_lca_client.py`, `agents/nodes.py` (lca_validator)

**Dipendenze:** Analisi preliminare delle colonne del DataSet.xlsx

**Approccio A (se colonne presenti nel dataset):**
```python
"energy_mj": float(r.get("energyimpact", 0.0))
"cost_per_kg": float(r.get("cost", 0.0))
```

**Approccio B (se colonne assenti — stima per categoria):**
```python
MATERIAL_COST_ESTIMATES = {
    "polypropylene": 1.2, "steel": 0.8, "aluminum": 2.1,
    "wood": 0.4, "glass": 0.9, "cotton": 1.8,
}
def _estimate_cost_per_kg(row) -> float:
    name = str(row.get("outputname", "")).lower()
    for mat, cost in MATERIAL_COST_ESTIMATES.items():
        if mat in name:
            return cost
    return 1.0  # default generico, non zero
```

**Priorità:** 🟡 Media | **Complessità:** 3/5

---

## T17 — constraint_extractor Asincrono

**Obiettivo:** Convertire `constraint_extractor` da sincrono a asincrono per consistenza con il resto del grafo.

**File coinvolti:** `agents/nodes.py` (righe 73-103)

**Implementazione:** Cambiare `def constraint_extractor` in `async def constraint_extractor` e sostituire `_invoke_structured` con `_ainvoke_structured`.

**Priorità:** 🟢 Bassa | **Complessità:** 1/5

---

## T18 — Cache PDF con Hash Content

**Obiettivo:** Sostituire `id(state.get("mcda_scores"))` come cache key con un hash del contenuto, affidabile tra rerun.

**File coinvolti:** `ui/app.py` (righe 617-622)

**Implementazione:**
```python
import hashlib, json
_pdf_key_content = hashlib.md5(
    json.dumps(state.get("mcda_scores", []), sort_keys=True).encode()
).hexdigest()
```

**Priorità:** 🟢 Bassa | **Complessità:** 1/5

---

# 9. PROMPT AUTONOMI PER OGNI TASK

---

## PROMPT T01 — Fix Report Generator

```
Sei un Senior Python Developer che lavora su un agente AI per l'ottimizzazione 
sostenibile dei materiali (LangGraph + Streamlit).

TASK: Correggi i nomi dei campi errati in reports/generator.py

CONTESTO:
- Il sistema produce uno stato AgentState con questi campi:
  - lca_results: list[dict] dove ogni dict ha "original_scores" con chiave 
    "environmental_impact" (NON "co2_eq_kg")
  - mcda_scores: list[dict] dove best_alternative ha chiave 
    "impact_reduction_pct" (NON "co2_reduction_pct")

PROBLEMI DA RISOLVERE:
1. Riga 19: r["original_scores"]["co2_eq_kg"] → r["original_scores"]["environmental_impact"]
2. Riga 28: best["co2_reduction_pct"] → best["impact_reduction_pct"]  
3. Riga 51: best['co2_reduction_pct'] → best['impact_reduction_pct']
4. Riga 63: best["co2_reduction_pct"] → best["impact_reduction_pct"]

VINCOLI:
- NON modificare la struttura HTML del report
- NON modificare il CSS
- NON modificare altri file oltre a reports/generator.py
- Preservare tutti i commenti esistenti

VERIFICA: Dopo la modifica, il report HTML deve mostrare valori CO₂ > 0 
quando lca_results e mcda_scores sono popolati.

OUTPUT ATTESO: reports/generator.py con i 4 field names corretti.
```

---

## PROMPT T02 — Fix is_market

```
Sei un Senior Python Developer su un progetto di Life Cycle Assessment (LCA).

TASK: Aggiungi il campo is_market al dizionario restituito da find_closest_match
in data/csv_lca_client.py

CONTESTO:
- Il file DataSet.xlsx contiene una colonna "processname"
- Se "market" è nel processname, significa che il trasporto è già incluso 
  nell'impatto del materiale (dataset di mercato ecoinvent)
- Il metodo find_closest_match (righe 66-86) restituisce un dict ma non 
  include "is_market"
- Il metodo chiamante (agents/nodes.py, lca_validator) già legge 
  orig_match.get("is_market", False) — il campo mancante fa sì che sia 
  sempre False → il trasporto viene sempre contato doppio

MODIFICA RICHIESTA:
Nel return del metodo find_closest_match, aggiungere:
  "is_market": "market" in str(row["processname"]).lower()

VINCOLI:
- NON modificare la firma del metodo
- NON modificare altri metodi della classe
- Il campo va aggiunto in modo additivo (non rimuovere campi esistenti)

VERIFICA: Scrivere un test che cerca "market for polypropylene" e verifica 
is_market == True. Cercare "polypropylene production" e verificare 
is_market == False.
```

---

## PROMPT T03 — Rotazione API Key + Secrets Management

```text
Sei un esperto DevOps e Python Developer che lavora su un agente AI per l'ottimizzazione sostenibile dei materiali.

TASK: Metti in sicurezza la gestione della API Key rimuovendola dal file hardcoded e configurando il sistema in modo sicuro.

CONTESTO:
- Attualmente la API key (OpenRouter/Ollama) potrebbe essere esposta nel file .env.
- È una pratica di sicurezza critica non tracciare mai chiavi API in repository Git.
- Il sistema usa `pydantic-settings` in `core/config.py` per leggere le configurazioni.

MODIFICHE RICHIESTE:
1. Rimuovi la chiave reale dal file `.env` (lascia solo un placeholder es. `OPENROUTER_API_KEY=your_key_here`).
2. Verifica che il file `.env` sia incluso correttamente nel `.gitignore`.
3. Aggiorna il `README.md` aggiungendo un paragrafo chiaro con le istruzioni per impostare la chiave (es. tramite export nel terminale o creando un `.env` locale).
4. (Opzionale ma consigliato) In `core/config.py`, aggiungi una validazione che sollevi un errore esplicito all'avvio se la chiave non è configurata e si sta tentando di usare OpenRouter.

VINCOLI:
- NON rompere il meccanismo di lettura della configurazione. L'app deve continuare a funzionare se la variabile d'ambiente è impostata nel sistema o nel .env locale.

OUTPUT ATTESO: File `.env` ripulito, `.gitignore` verificato, `README.md` e `core/config.py` aggiornati.
```

---

## PROMPT T04 — Fallback LCA Visibile

```text
Sei un Senior Python Developer su un progetto LangGraph.

TASK: Rendi visibile all'utente ogni volta che il sistema usa il valore LCA di fallback (3.5 kg CO₂).

CONTESTO:
- Quando `find_closest_match` fallisce, il sistema assegna un valore silente di 3.5 kg CO₂/kg.
- Questo viola la "Regola d'Oro" di trasparenza del progetto: ogni assunzione deve essere dichiarata.
- L'AgentState ha un campo `assumptions_list`.

MODIFICHE RICHIESTE:
1. In `agents/workflow_node.py` (circa L76-78), quando si usa il fallback di 3.5, aggiungi una stringa al dizionario restituito per aggiornare `assumptions_list` (es. "Dati LCA non trovati per [materiale], usato valore di fallback 3.5 kg CO₂/kg").
2. In `agents/nodes.py` in `lca_validator` (circa L277-279) implementa lo stesso pattern per il fallback.
3. In `agents/nodes.py` nel path delle alternative fallback (circa L296-299) implementa lo stesso pattern.

VINCOLI:
- NON modificare o rimuovere il fallback stesso, in quanto serve per la robustezza del sistema. Devi solo aggiungere la notifica.
- L'aggiunta a `assumptions_list` in un nodo LangGraph che ritorna un dict si sommerà ai valori esistenti se lo stato usa l'operatore "add".

VERIFICA: Analizzando un materiale sconosciuto (es. "vibranium"), deve apparire un warning esplicito nella UI per il fallback.
```

---

## PROMPT T06 — Rimozione Codice Morto da nodes.py

```text
Sei un Software Engineer focalizzato sul refactoring e pulizia del codice.

TASK: Elimina il codice obsoleto e non utilizzato dal file `agents/nodes.py`.

CONTESTO:
- Le funzioni `bom_decomposer` e `semantic_ideator` presenti in `nodes.py` (circa righe 111-235) sono obsolete.
- Le loro logiche sono state sostituite rispettivamente dai file `workflow_node.py` e `material_node.py`.
- Il codice morto crea confusione.

MODIFICHE RICHIESTE:
1. Elimina completamente la funzione `bom_decomposer`.
2. Elimina completamente la funzione `semantic_ideator`.
3. Controlla se le costanti `PROCESS_IMPACTS` e `TRANSPORT_IMPACT_PER_TKM` (circa L239-245) sono ancora usate da `lca_validator` all'interno dello stesso file. Se sì, MANTIENILE.

VINCOLI:
- NON eliminare le costanti globali se sono usate da altre funzioni attive nel file.
- Esegui una rapida ricerca globale prima di cancellare per assicurarti che `bom_decomposer` e `semantic_ideator` non siano importate da qualche parte (es. test vecchi).

OUTPUT ATTESO: Il file `agents/nodes.py` ripulito dalle due funzioni morte.
```

---

## PROMPT T08 — Distanza Logistica da LLM o Warning Visibile

```text
Sei un AI Engineer che sviluppa workflow LangGraph.

TASK: Rendi trasparente l'assunzione sulla distanza logistica di default (500 km).

CONTESTO:
- Attualmente in `workflow_node.py`, il campo `geography` viene estratto dall'LLM, ma il sistema usa sempre un valore hardcoded di `500.0` km per i calcoli logistici, ignorando il testo.
- Questo causa un problema di trasparenza per l'utente.

MODIFICHE RICHIESTE:
1. Aggiorna lo schema Pydantic `WorkflowAndBOMResponse` aggiungendo un campo opzionale `distance_km: Optional[float] = Field(description="Distanza stimata in km tra fornitore e sito, se esplicitata")`.
2. Aggiorna il prompt del nodo `workflow_bom_ideator` per istruire l'LLM ad estrarre la distanza se viene menzionata nel testo.
3. In `workflow_node.py` (circa L97-103), verifica se `distance_km` è presente. Se manca, imposta `dist_km = 500.0` e AGGIUNGI un messaggio ad `assumptions_list` (es. "Distanza logistica non specificata per [componente], usato valore di default 500 km").

VINCOLI:
- Non far bloccare l'esecuzione se la distanza non viene trovata, il fallback a 500 è il comportamento corretto, ma deve essere loggato in `assumptions_list`.

OUTPUT ATTESO: Schemi aggiornati, prompt migliorato e calcolo della distanza con alert sulle assunzioni.
```

---

## PROMPT T05 — Progress Tracker Reale

```
Sei un Senior Python Developer su un agente LangGraph con UI Streamlit.

TASK: Impostare current_lca_step progressivamente in ogni nodo del grafo
invece di saltare sempre da 1 a 7.

CONTESTO SISTEMA:
- Il grafo ha questi nodi nell'ordine: constraint_extractor → 
  workflow_bom_ideator → material_ideator → lca_validator → mcda_scorer
- L'AgentState ha un campo "current_lca_step: int"
- La UI (ui/app.py) visualizza 7 step: 1=Analisi Entità, 2=Lookup Aggregato,
  3=Selezione Materiale, 4=Vincolo Geometrico, 5=Scomposizione BOM, 
  6=Calcolo Logistica, 7=Validazione
- Attualmente tutti i nodi impostano current_lca_step=7 o non lo impostano

MAPPING RICHIESTO:
- constraint_extractor: step 1 (all'inizio della funzione)
- workflow_bom_ideator: step 2 all'avvio, step 3 dopo inferenza materiale,
  step 4 dopo GEOMETRY_MAPPING, step 5 dopo costruzione BOM,
  step 6 dopo calcolo logistics
- lca_validator: step 7 (già presente, ma rimuovere il 7 dal path 
  interview-incomplete — riga 61 di workflow_node.py)
- material_ideator: nessuno step separato (fa parte del flusso 3-4)

FILE DA MODIFICARE:
- agents/nodes.py (constraint_extractor, lca_validator)
- agents/workflow_node.py (workflow_bom_ideator)

VINCOLI:
- NON modificare la logica di business dei nodi
- NON modificare ui/app.py
- NON rimuovere step 7 da lca_validator (solo dal path interview)

VERIFICA: Eseguire l'app e osservare il tracker avanzare visivamente 
durante l'esecuzione.
```

---

## PROMPT T07 — Fix Routing con current_phase

```
Sei un Principal Engineer che lavora su un agente LangGraph.

TASK: Aggiungere current_phase allo stato e usarlo nel routing condizionale
per eliminare il bug che porta al nodo interview su errori workflow.

CONTESTO BUG:
In agents/graph.py, check_interview_complete decide il routing così:
  if state.get("pending_feedback") is not None and not state.get("bom"):
      return "human_feedback_processor_interview"
  return "human_feedback_processor_workflow"

PROBLEMA: Se workflow_bom_ideator fallisce (eccezione), imposta:
  {"pending_feedback": "Errore...", "thought_log": [...]}
Con bom ancora vuota → il routing va a "interview" invece che gestire l'errore.

SOLUZIONE:
1. In agents/state.py aggiungere: current_phase: str (con default "init")
2. In ogni nodo che ritorna un dict, aggiungere "current_phase": "<fase>"
   - constraint_extractor → "constraints"  
   - workflow_bom_ideator path interview-incomplete → "interview"
   - workflow_bom_ideator path success → "workflow"
   - workflow_bom_ideator path exception → "error"
3. In agents/graph.py check_interview_complete:
   phase = state.get("current_phase", "")
   if phase == "interview":
       return "human_feedback_processor_interview"
   return "human_feedback_processor_workflow"

FILE DA MODIFICARE: agents/state.py, agents/graph.py, agents/workflow_node.py,
agents/nodes.py (constraint_extractor)

VINCOLI:
- NON modificare la topologia del grafo (nodi e edge rimangono)
- NON modificare ui/app.py
- Il fallback "error" deve essere gestito in app.py (solo mostrare 
  st.error se current_phase == "error")

VERIFICA: Forzare un timeout nel workflow (impostare timeout=0 in llm_factory)
e verificare che l'app non mostri domande di intervista.
```

---

## PROMPT T15 — Refactor human_feedback_processor

```
Sei un Senior Software Architect che refactorizza un agente LangGraph.

TASK: Unificare i 3 nodi human_feedback_processor_* in un singolo nodo
usando current_phase per il routing interno.

PREREQUISITI: Il campo current_phase deve essere già nello stato AgentState
(completare T07 prima di questo task).

CONTESTO ATTUALE:
In agents/graph.py:
  graph.add_node("human_feedback_processor_constraints", human_feedback_processor)
  graph.add_node("human_feedback_processor_interview", human_feedback_processor)
  graph.add_node("human_feedback_processor_workflow", human_feedback_processor)
La STESSA funzione è registrata 3 volte come workaround LangGraph.

REFACTORING RICHIESTO:
1. In agents/nodes.py, modificare human_feedback_processor per usare 
   current_phase internamente (invece di dedurre la fase da bom vuota/piena)
2. In agents/graph.py:
   - Sostituire i 3 nodi con uno solo: 
     graph.add_node("human_feedback_processor", human_feedback_processor)
   - Aggiornare tutti gli edge che puntavano ai vecchi nodi
   - Aggiornare l'interrupt list: ["human_feedback_processor"]
   - La funzione di routing check_interview_complete deve ancora esistere
     ma puntare sempre allo stesso nodo

NOTA: Con un solo nodo e un solo interrupt, il grafo è più pulito ma 
LangGraph non sa "quale" interrupt è stato — questo è OK perché usiamo 
current_phase per determinarlo.

VINCOLI:
- NON modificare la logica interna di _handle_interview/_handle_constraints/
  _handle_workflow
- NON modificare ui/app.py (si basa su next_node name → aggiornare 
  i check da "human_feedback_processor_constraints" a 
  "human_feedback_processor")
- Aggiornare ui/app.py solo per i check del nome nodo

VERIFICA: Eseguire l'intero flusso interactive e verificare che tutti 
e 3 i checkpoint (constraints, interview, workflow) funzionino correttamente.
```

---

## PROMPT T09 — Unifica Lingua (tutto inglese)

```text
Sei uno UX Developer e Python Engineer per una Web App Streamlit.

TASK: Unifica la lingua del sistema esclusivamente all'inglese per evitare un misto italo/inglese incoerente.

CONTESTO:
- Il sistema attualmente mischia messaggi UI, Thought Log e prompt in italiano e inglese.
- Per scalabilità e target professionale, tutto deve essere rigorosamente in inglese.

MODIFICHE RICHIESTE:
1. In `ui/app.py`:
   - Traduci i messaggi in L221-226 (messaggi di benvenuto/assistente).
   - Traduci la mappatura dei nomi degli step in L461-468 (es. "Analisi Entità" -> "Entity Analysis").
   - Traduci le stringhe statiche (es. L502 "La creazione dell'oggetto...").
2. In `agents/nodes.py` e `agents/workflow_node.py`:
   - Traduci tutte le stringhe iniettate nel `thought_log`.
   - Traduci i messaggi di errore restituiti nei return dictionary.
3. Nei Prompt YAML (`prompts/` o nel codice se inline):
   - Assicurati che le istruzioni di sistema chiedano all'LLM di rispondere esclusivamente in lingua inglese.

VINCOLI:
- Fai attenzione a non alterare le chiavi dei dizionari che la logica interna utilizza (es. i nomi delle variabili o delle fasi). Traduci SOLO le stringhe di output visibili all'utente.
```

---

## PROMPT T10 — Confirm Dialog per Restart Session

```text
Sei uno sviluppatore UI Streamlit avanzato.

TASK: Implementa una modale di conferma per evitare la perdita accidentale dei dati della sessione in corso.

CONTESTO:
- Cliccare sul pulsante "Restart Session" o su "Reject" azzera lo stato (tramite `handle_reject()`).
- Serve un dialog (Modale) di conferma (`st.dialog`).

MODIFICHE RICHIESTE:
1. In `ui/app.py`, implementa la decorazione `@st.dialog("Restart Session")` su una nuova funzione `_confirm_restart()`.
2. La funzione deve contenere un testo di avviso: "This will delete the current analysis. Are you sure?"
3. Aggiungi due colonne con i bottoni "Yes, restart" (type="primary") e "Cancel".
4. Se l'utente clicca Yes, esegui `handle_reject()` e `st.rerun()`. Se Cancel, solo `st.rerun()`.
5. Modifica il comportamento del bottone "Restart Session" originale in modo che invochi questa funzione modale al click.

VINCOLI:
- Utilizza la feature nativa `st.dialog` introdotta recentemente in Streamlit.
- Assicurati che lo stato venga cancellato SOLO se l'utente conferma.
```

---

## PROMPT T11 — Progressive Disclosure Dashboard

```text
Sei un Frontend Developer specializzato in Streamlit UI/UX.

TASK: Nascondere le sezioni del dashboard di destra e mostrarle progressivamente in base all'avanzamento.

CONTESTO:
- Il layout di destra in `ui/app.py` mostra spazi vuoti o espander non necessari prima che i dati vengano generati.
- Si deve usare `state.get("current_phase")` per determinare cosa renderizzare.
- Le fasi sono: "init", "constraints", "interview", "workflow", "material", "lca", "mcda", "complete".

MODIFICHE RICHIESTE IN ui/app.py (Colonna destra):
1. Leggi la fase attuale: `phase = st.session_state.agent_state.get("current_phase", "init")`
2. Applica la seguente logica condizionale per i blocchi UI:
   - Se phase == "init": mostra solo un messaggio di onboarding iniziale.
   - Se phase in ["constraints", "interview"]: mostra il Thought Log (e le eventuali domande).
   - Se phase == "workflow": mostra Thought Log + la sezione Workflow + la BOM.
   - Se phase == "material": mostra quanto sopra + le Alternative Materiali LCA.
   - Se phase in ["lca", "mcda"]: mostra quanto sopra + Grafici LCA + Tabella MCDA.
   - Se phase == "complete": mostra tutto + i bottoni di Download (PDF/HTML).

VINCOLI:
- Assicurati che se `current_phase` non è definito, il comportamento di default non causi crash, ma si comporti in modo aggraziato mostrando il minimo indispensabile (init).
```

---

## PROMPT T12 — Toast/Warning per Assunzioni e Fallback

```text
Sei un UX Engineer per Streamlit.

TASK: Rendi le assunzioni del sistema più visibili, distinguendo tra normali logiche e fallback critici.

CONTESTO:
- Le stringhe in `assumptions_list` sono l'unico modo per far capire all'utente che il sistema ha inferito un dato o usato un fallback.
- Attualmente vengono mostrate tutte insieme, senza distinzione di severità.

MODIFICHE RICHIESTE IN ui/app.py:
1. Trova il loop che renderizza le `assumptions_list` nel dashboard.
2. Modifica la logica di rendering per ispezionare il testo della stringa:
   - Se il testo contiene parole chiave come "fallback" o "3.5", mostralo usando `st.error(f"⚠️ Data fallback: {assumption}", icon="🔴")`.
   - Altrimenti (assunzioni normali, es. logistica), mostralo usando `st.warning(f"ℹ️ Assumption: {assumption}", icon="🟡")`.

VINCOLI:
- Questa modifica deve essere fatta esclusivamente a livello di componente UI nel file `app.py`.
- NON modificare il modo in cui i nodi LangGraph aggiungono gli elementi alla lista.
```

---

## PROMPT T13 — Thread-Safe LCA Singleton

```text
Sei un Senior Backend Engineer.

TASK: Prevenire race conditions nella creazione del provider LCA in un ambiente Streamlit multi-utente.

CONTESTO:
- `data/provider_factory.py` memorizza le istanze in un dizionario globale `_provider_cache`.
- Quando più utenti si connettono contemporaneamente all'app Streamlit, l'accesso a questo dizionario senza lock può generare race conditions e istanziazioni multiple scorrette.

MODIFICHE RICHIESTE IN data/provider_factory.py:
1. Importa il modulo standard `threading`.
2. Istanzia un lock globale: `_cache_lock = threading.Lock()`.
3. Nel metodo `get_lca_provider()`, avvolgi la logica di istanziazione e caching (`if source not in _provider_cache:...`) all'interno di un blocco `with _cache_lock:`.

VINCOLI:
- Mantenere la logica Singleton. L'obiettivo è solo rendere l'accesso thread-safe.

OUTPUT ATTESO: Il file `provider_factory.py` aggiornato con il `threading.Lock`.
```

---

## PROMPT T14 — LLM Caching in ModelFactory

```text
Sei un Software Architect esperto di LangChain.

TASK: Ottimizzare l'istanziazione degli LLM aggiungendo un caching a livello di Factory.

CONTESTO:
- Ad ogni esecuzione di nodo, `core/llm_factory.py` nel metodo `get_model()` crea una nuova istanza di `BaseChatModel`.
- Questo comporta overhead e spreco di memoria (soprattutto in loop lunghi o con molti nodi).
- Vogliamo cachare le istanze dei modelli.

MODIFICHE RICHIESTE IN core/llm_factory.py:
1. Definisci un dizionario privato a livello di modulo o classe: `_model_cache: dict[str, BaseChatModel] = {}`.
2. Modifica la funzione `get_model()`:
   - Genera una chiave univoca basata sul provider e modello scelto (es. `key = f"{settings.llm_provider}:{settings.ollama_model or settings.openrouter_model}"`).
   - Verifica se la `key` è già nella cache. Se sì, restituisci l'istanza.
   - Altrimenti, prosegui con la normale logica di istanziazione, salvala in `_model_cache[key]`, e restituiscila.

VINCOLI:
- Rispetta le logiche attuali che switchano tra `ChatOllama` e `ChatOpenAI` basate sui settings. Aggiungi solo il livello di caching a monte.
```

---

## PROMPT T16 — MCDA Reale (Energy + Cost dal Dataset)

```text
Sei un Data Engineer e Backend Developer.

TASK: Implementare il recupero di dati reali di energia e costo dal dataset (o usare stime strutturate) invece di mockare tutto a 0.0.

CONTESTO:
- Attualmente `csv_lca_client.py` popola `energy_mj` e `cost_per_kg` a `0.0`.
- Questo invalida il 60% dei pesi MCDA che si basano su queste metriche, rendendo inutile il ranking.

MODIFICHE RICHIESTE:
1. Analizza le colonne esposte in `DataSet.xlsx`.
2. Se esistono colonne rilevanti per l'energia e il costo, modificale in `get_impact_scores()` nel file `data/csv_lca_client.py`. (es. `float(r.get("energyimpact", 0.0))`).
3. Se non esistono, implementa una funzione `_estimate_cost_per_kg(row)` e `_estimate_energy_mj(row)` all'interno di `csv_lca_client.py` che assegni valori realistici basati su categorizzazione testuale (es. se "polypropylene" è nel nome, costo = 1.2; se "steel", costo = 0.8).
4. Sostituisci i mock `0.0` con la chiamata a queste logiche.
5. In `agents/nodes.py` (`lca_validator`), assicurati che questi valori vegano letti correttamente nel dizionario dei risultati.

VINCOLI:
- Assicurarsi che i valori non siano mai esattamente `0.0` per evitare problemi nelle funzioni di normalizzazione (divisioni per zero o min/max identici). Imposta sempre un valore di default base (es. 1.0).

OUTPUT ATTESO: L'engine MCDA diventerà funzionale e varierà dinamicamente sulla base dei materiali trovati.
```

---

# 10. DOMANDE NECESSARIE PER COMPLETARE IL SISTEMA

In ordine di importanza strutturale:

### D1 — Lingua dell'interfaccia (CRITICA)
**Il prodotto deve essere in italiano o in inglese?**  
Attualmente è un misto incoerente. La scelta impatta tutti i messaggi, i nomi passo, i prompt YAML e i messaggi di errore. Ogni risposta diversa implica un task di portata diversa.

### D2 — Copertura reale del DataSet.xlsx (CRITICA per MCDA)
**Il DataSet.xlsx contiene colonne per energia (MJ) e costo (€/kg)?**  
Se non ci sono, il 60% del peso MCDA è sempre zero e l'analisi multi-criterio è falsa. Se sì, quali sono i nomi esatti delle colonne? Questo determina se T16 è un fix semplice o richiede una fonte dati esterna.

### D3 — Target utente e contesto d'uso (STRATEGICA)
**Chi usa il sistema? Un product designer? Un ingegnere LCA? Un buyer?**  
Il livello tecnico dell'utente cambia radicalmente la UX: quanto spiegare il concetto di "market dataset"? Quanto dettagliare le assunzioni? Quanto semplificare il report?

### D4 — Modalità auto (ARCHITETTURALE)
**La modalità "auto" deve essere accessibile all'utente finale?**  
Attualmente è inaccessibile dalla UI. Se sì, va aggiunto un toggle. Se no, va rimossa la logica `mode` dallo stato per ridurre la complessità.

### D5 — Fonti dati future (ROADMAP)
**Il DataSet.xlsx verrà sostituito dall'API ecoinvent reale?**  
L'interfaccia `LCADataProvider` è già pronta per supportarlo. Sapere se questo è un obiettivo a breve/medio termine determina quanto investire nell'architettura dati.

### D6 — Report: PDF o HTML? (UX)
**Il PDF tramite WeasyPrint è un requisito reale o opzionale?**  
WeasyPrint richiede dipendenze di sistema (Cairo/GTK3) difficili su Windows. Se il PDF non è critico, l'HTML-only semplifica deployment e mantainability.

### D7 — MCDA pesi: fissi o configurabili? (PRODOTTO)
**I pesi MCDA (CO₂ 40%, costo 30%, energia 15%, acqua 15%) devono essere fissi o modificabili dall'utente?**  
Il DOCX menziona slider AHP come roadmap futura. Se è un requisito a medio termine, l'architettura va predisposta ora.

### D8 — Gestione errori di connessione (UX)
**Cosa deve succedere se Ollama va in timeout durante material_ideator?**  
Attualmente `material_ideator` imposta `pending_feedback` con il messaggio di errore e si aspetta che l'utente risponda "Riprovare?". Ma il grafo è già avanzato oltre il nodo di workflow — non può ripartire. La sessione è di fatto persa. Questo è accettabile?

### D9 — Multi-componente avanzato (LOGICA)
**Se un prodotto ha 8+ componenti, il sistema deve generare 24+ alternative LLM in serie?**  
Attualmente `material_ideator` genera alternative per tutti i componenti in una sola chiamata LLM. Con BOM grandi, questo causa timeout. Serve un limite o una strategia di batching?

### D10 — Onboarding utente (UX)
**Deve esistere una schermata/sezione di onboarding che spiega cosa fa il sistema prima del primo utilizzo?**  
Attualmente l'utente vede direttamente il chat input senza capire cosa aspettarsi. Un tooltip, un modal o una sezione "Come funziona" ridurrebbe il tasso di abbandono.

---

# 11. ANALISI RISCHI

| Rischio | Prob | Impatto | Mitigazione |
|---------|------|---------|-------------|
| Dataset cambia struttura → crash avvio | Media | Critico | Schema validation + recovery graceful (T16 dipendente) |
| nest_asyncio smette di funzionare su update Streamlit | Alta | Critico | Migrare a asyncio nativo (lungo termine) |
| API key esposta → costi non autorizzati | Alta | Alto | T03 — rotazione immediata |
| MCDA identici per tutti → ranking insensato | Certa | Alto | T16 — dati reali energy/cost |
| Report HTML vuoto → feature inutile | Certa | Medio | T01 — fix immediato |
| Ollama timeout → sessione persa | Alta | Medio | Gestione errori esplicita + retry UI |
| Multi-user Streamlit → singleton race condition | Media | Medio | T13 — threading.Lock |

---

# 12. STRATEGIA ANTI-REGRESSIONE

1. **Test prima di ogni task** — eseguire `pytest tests/` prima di ogni modifica
2. **Test unitari per ogni bug fix** — aggiungere un test che riproduce il bug prima di fixarlo
3. **Snapshot dello stato** — salvare un `AgentState` esempio (JSON) come fixture di test
4. **Verifica manuale del report** — dopo T01, sempre scaricare l'HTML e verificare valori non-zero
5. **Feature flag per refactoring grandi** — T15 (human_feedback unificato) deve avere un flag per rollback rapido

---

# 13. STRATEGIA SCALABILITÀ FUTURA

| Upgrade | Quando | Perché |
|---------|--------|--------|
| ChromaDB per fuzzy match semantico | Dopo T06 | Elimina falsi match difflib (es. "wood" vs "ywood") |
| SQLite per DataSet.xlsx | Dopo T16 | Query efficienti, no reload in memoria |
| API Google Maps per logistica | Dopo T08 | Distanza reale invece di 500 km default |
| Slider AHP per MCDA dinamico | Dopo T16 | Pesi personalizzabili per utente |
| LLM Vision per geometrie | Futuro | Upload immagine prodotto invece di descrizione testuale |
| Ecoinvent API | Futuro | Dataset reale invece di Excel locale |

---

# 14. STRATEGIA MAINTAINABILITY

1. **Un file = una responsabilità** — separare i nodi in file distinti (da `nodes.py` monolitico a `nodes/` directory)
2. **Costanti in config** — `TRANSPORT_IMPACT_PER_TKM`, `PROCESS_IMPACTS` devono essere in `core/config.py` o `data/constants.py`
3. **Logging strutturato** — ogni nodo già usa `logger`, ma i livelli sono inconsistenti
4. **Docstring sulle funzioni pubbliche** — aggiungere docstring con parametri e return type a tutti i metodi `public`
5. **CHANGELOG.md** — tenere traccia di ogni fix/feature per facilitate la revisione accademica

---

# 15. STRATEGIA UX CONSISTENCY

1. **Design system token** — definire colori, spacing e font una volta in CSS (non inline)
2. **Componenti riusabili** — estrarre pattern ripetuti (es. "componente con expander + dataframe") in funzioni helper
3. **Empty state standard** — ogni sezione senza dati mostra lo stesso pattern visivo (non testo libero)
4. **Messaggi assistant** — tutti con lo stesso tono, formato e lingua
5. **Loading states** — `st.status` già usato, ma espandere per mostrare quale step è in corso

---

# 16. CONCLUSIONE DA PRINCIPAL ARCHITECT

## Valutazione finale del progetto

Questo è un progetto con **un'anima architetturale genuinamente corretta** e un'esecuzione che riflette la velocità tipica di un prototipo accademico. Le scelte fondamentali — neuro-simbolico, LangGraph, Pydantic, LCA deterministico — sono quelle giuste. Non è un progetto da riscrivere: è un progetto da **completare correttamente**.

## I 5 interventi che cambiano tutto

Se dovessi scegliere solo 5 azioni immediate ad alto impatto:

1. **T01** — Fix report generator (10 minuti, output principale rotto)
2. **T02** — Fix is_market (5 minuti, logica LCA fondamentale)
3. **T04** — Fallback visibile (30 minuti, integrità dei dati e trasparenza)
4. **T05** — Progress tracker reale (1 ora, UX percepita)
5. **T07** — Routing esplicito con current_phase (2 ore, stabilità architetturale)

Con questi 5 task, il sistema passa da un **MVP con output parzialmente errato** a un **MVP con output corretto e UX onesta**. È la differenza tra un progetto che inganna l'utente e uno che lo serve.

## Giudizio sul debito tecnico

Il debito tecnico è **gestibile**: non è un sistema legacy con anni di accumulazione. È un prototipo con 3-4 mesi di velocità. Con 2 sprint metodici, può diventare un prodotto dimostrabile senza vergogna.

## Raccomandazione finale

**Eseguire i task nell'ordine della roadmap, senza saltare fasi.**  
La tentazione di fare prima le cose "visibili" (UX, CSS, progressive disclosure) è alta, ma insensata: non ha senso migliorare la presentazione di dati errati. Correttezza prima, bellezza dopo.

---
*Documento generato il 2026-05-10 — Principal Architect Edition*
