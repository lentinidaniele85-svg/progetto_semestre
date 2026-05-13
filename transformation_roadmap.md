# TRANSFORMATION ROADMAP — Sustainable Product Optimization Agent

## Strategia di Trasformazione e Task

L'obiettivo è stabilizzare e rendere scalabile la piattaforma risolvendo i bug correnti e introducendo miglioramenti UX/Architetturali progressivi. *(Per i dettagli sull'architettura logica e sulla struttura del codice, consulta rispettivamente i file `ai_logic.md` e `code_structure.md`).*

### FASE 1 — Stabilità (Fix Critici)
- **T01**: Fix report generator field names (`co2_eq_kg` -> `environmental_impact`).
- **T02**: Fix `is_market` in `find_closest_match` per evitare il doppio conteggio del trasporto.
- **T03**: Gestione sicura API Key OpenRouter (via `.env` o `st.secrets`).
- **T04**: Rendere visibile in UI ogni volta che si usa il fallback LCA (3.5 kg CO2) aggiornando `assumptions_list`.

### FASE 2 — Correttezza
- **T05**: Progress tracker reale (Step 1-7 basato sui nodi LangGraph, sostituendo il salto 1->7).
- **T06**: Rimozione codice morto (`bom_decomposer`, `semantic_ideator` in `nodes.py`).
- **T07**: Aggiunta di `current_phase` allo stato LangGraph per un routing privo di bug.
- **T08**: Estrazione della distanza logistica tramite LLM; se fallisce, avviso visibile in UI per l'uso del default (500 km).
- **T19**: Ricerca LCA Gerarchica Filtrata (`csv_lca_client.py`) con campi bifase separati per `target_product` e `target_geography`.

### FASE 3 — UX (User Experience)
- **T09**: Unificazione linguistica dell'interfaccia (tutto in inglese).
- **T10**: Inserimento Confirm Dialog prima di eliminare i dati correnti con "Restart Session".
- **T11**: Progressive disclosure per il dashboard (mostrare i moduli in sequenza all'avanzare delle fasi).
- **T12**: Toast e Warning visivamente chiari: Giallo per assunzioni normali, Rosso per fallback di emergenza.

### FASE 4 — Architettura
- **T13**: Implementazione di un Thread-Safe Lock per il Singleton LCA DataProvider, supportando il multi-user in Streamlit.
- **T14**: Caching delle istanze LLM all'interno della `ModelFactory` per miglior performance.
- **T15**: Unificazione del nodo `human_feedback_processor` guidato internamente dalla `current_phase`.
- **T16**: Utilizzo di MCDA Reale popolando Energia (MJ) e Costo (€/kg) dal DataSet.xlsx.
- **T17**: Modifica di `constraint_extractor` in funzione asincrona.
- **T18**: Ottimizzazione del caching PDF utilizzando un hash del contenuto invece di `id()`.

### FASE 5 — Sviluppi Futuri (Scale & AI)
- **RAG / Embeddings Vectoriali**: Sostituzione di `difflib` (fuzzy matching) con ChromaDB per l'ancoraggio del materiale.
- **Sistema Logistico API**: Integrazione con Google Maps API per il calcolo real-time del $tkm$.
- **MCDA Dinamico con "Personalità" (AHP)**: Slider in UI per far scegliere all'utente l'importanza del costo vs ecologia.
- **LLM Vision (Multimodalità)**: Caricamento immagini del prodotto in UI e analisi dimensionale tramite GPT-4o / LLaVA.
- **Database LCA Relazionale**: Esportazione DataSet su PostgreSQL.
