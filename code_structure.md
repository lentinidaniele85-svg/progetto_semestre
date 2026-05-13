# Struttura del Codice e Repository

Il progetto è strutturato seguendo principi di modularità e separazione delle responsabilità (Separation of Concerns). Questa organizzazione facilita la manutenzione e permette di scalare singole componenti indipendentemente.

## Root Directory

- `README.md`: Documentazione di base del progetto.
- `SETUP.bat` e `START.bat`: Script per gli utenti Windows per automatizzare la creazione del virtual environment, l'installazione delle dipendenze e l'avvio della Streamlit app senza passare dal terminale.
- `requirements.txt`: Elenco delle librerie Python necessarie.
- `.env`: (e `.env.example`) File per le variabili d'ambiente (chiavi API, configurazioni LLM e Data Source). **Cruciale per la sicurezza**.

## Moduli Principali

### 1. `agents/` (Logica LangGraph)
Contiene il "cervello" dell'agente, definito come un grafo a stati.
- `graph.py`: Definisce la topologia del grafo (i nodi e i collegamenti tra di essi). Stabilisce quali funzioni chiamare in base all'output del nodo precedente e gestisce gli "interrupt" per il feedback umano.
- `state.py`: Definisce `AgentState` tramite Pydantic/TypedDict. È la memoria a breve termine dell'agente che passa di nodo in nodo durante un'esecuzione.
- `schemas.py`: I modelli Pydantic. Forzano l'LLM a rispondere con strutture JSON precise e prevedibili (es. la definizione di un `BOMComponent`).
- `nodes.py`: Le funzioni dei nodi generici (es. `constraint_extractor`, `lca_validator`, `mcda_scorer`). Ciascun nodo riceve lo stato, lo manipola e restituisce l'aggiornamento.
- `workflow_node.py`: Il nodo incaricato di ideare il flusso produttivo e la Distinta Base (BOM). Implementa le fasi da 1 a 6 della logica.
- `material_node.py`: Il nodo dedicato specificamente a proporre alternative di materiali sostenibili basate sui vincoli individuati.

### 2. `ui/` (Interfaccia Utente)
- `app.py`: L'applicazione Streamlit. Implementa un'interfaccia "Glass Box" (a scatola trasparente) divisa in due colonne: a sinistra la chat interattiva (dove l'utente dialoga con l'IA), a destra la dashboard progressiva che mostra i "pensieri" dell'agente (Thought Log), la distinta base generata e i grafici LCA. Non contiene logica di business, solo rendering.

### 3. `core/` (Configurazioni e Foundation)
- `config.py`: Centralizza la gestione delle impostazioni ambientali usando `pydantic-settings`. Valida che le chiavi API e i setup siano corretti all'avvio.
- `llm_factory.py`: Implementa il pattern Factory per istanziare l'LLM corretto (Ollama locale o OpenRouter per API cloud) in base alle impostazioni del `.env`. Fornisce un punto di accesso unico ai modelli AI.

### 4. `data/` (Accesso Dati e Data Source)
- `csv_lca_client.py`: Client per la lettura del file Excel (`DataSet.xlsx`). Implementa logiche offline come la **Ricerca Gerarchica Filtrata** bifase: esegue prima un "Fuzzy Matching" unicamente sul prodotto (`target_product`), e poi filtra geograficamente in ordine di priorità esatta o di fallback RER/GLO (`target_geography`).
- `provider_factory.py`: Un Singleton Factory che gestisce le istanze dei client LCA, assicurando che non vengano caricati dataset multipli pesanti in memoria.

### 5. `prompts/`
Contiene le istruzioni base (System Prompts) fornite all'LLM.
- `semantic_ideation_*.yaml`: Definisce le "regole d'ingaggio" dell'IA, forzando toni professionali, procedure di analisi e divieti (come il non inventare dati LCA). Mantenerli separati dal codice Python semplifica il fine-tuning dei prompt.

### 6. `reports/` (Output e Rendering Documentale)
- `generator.py`: Motore di rendering. Prende l'elaborazione finita (BOM, LCA results, MCDA scores) e usa librerie come WeasyPrint o framework HTML per generare i report professionali scaricabili in PDF o HTML.

### 7. `tests/` (Assicurazione Qualità)
Contiene i test automatici (`pytest`) per validare il comportamento del data layer (es. testare il Fuzzy Matching) e simulare esecuzioni del grafo senza bisogno di interagire manualmente tramite UI.
