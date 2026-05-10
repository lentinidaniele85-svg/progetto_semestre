# Architettura Cognitiva: Sustainable Product Optimization Agent

Questo documento esplica dettagliatamente i processi decisionali ("come pensa"), le strutture dei parametri, il funzionamento specifico e le prospettive di miglioramento dell'Intelligenza Artificiale implementata in questo progetto. È stato concepito appositamente per permettere a una nuova IA di eseguire un "onboarding" istantaneo e comprendere la logica profonda del sistema.

---

## 1. Il Paradigma Ibrido (Neuro-Simbolico)
Il sistema **non** è un semplice chatbot LLM. Adotta un approccio **Neuro-Simbolico** (o Ibrido):
- **La Rete Neurale (LLM)**: Viene usata esclusivamente per l'estrazione di metadati semantici, scomposizione semantica degli oggetti (creare una BOM da un testo) e formulazione di ipotesi sui materiali.
- **Il Motore Simbolico (Python/Pandas)**: Esegue i calcoli LCA (Life Cycle Assessment), il mapping geometrico-manifatturiero e l'algoritmo MCDA (Multi-Criteria Decision Analysis) in modo matematicamente rigido e **totalmente offline**.

*Regola d'Oro del progetto*: **L'LLM non ha l'autorità di inventare numeri di impatto ambientale.**

---

## 2. Come "Pensa" (Il Flusso Logico a 7 Passi)
L'agente elabora l'input dell'utente attraverso una pipeline sequenziale (`agents/workflow_node.py` e `agents/nodes.py`) che segue questi 7 step precisi:

1. **Analisi Entità**: Il sistema classifica l'input in due categorie. È un *Materiale Grezzo* o un *Prodotto Complesso*?
2. **Lookup Aggregato**: (Se applicabile) Controlla se il prodotto intero ha già un'impronta calcolata nel database.
3. **Selezione Materiale (Inferenza)**: Se l'utente chiede una "Sedia" senza specificare i materiali, l'LLM fa una deduzione (es. *Plastica* e *Acciaio*). Ogni inferenza viene salvata in una `assumptions_list` e notificata in UI tramite avvisi arancioni.
4. **Vincolo Geometrico (Mapping)**: L'LLM assegna una Geometria astratta (es. *Corpi Cavi*, *Film*, *Pezzi Pieni*). Il codice Python mappa poi inflessibilmente quella geometria a un processo manifatturiero noto (es. *Pezzi Pieni* -> *Injection moulding*).
5. **Scomposizione BOM**: Generazione strutturata della Bill of Materials.
6. **Calcolo Logistica**: L'IA estrae geografia e massa. Il codice Python calcola l'impatto di trasporto in tonnellate-chilometro ($tkm = (massa\_kg / 1000) \times distanza\_km$). Se il materiale nel dataset contiene la keyword "market", si assume che il trasporto sia già incluso.
7. **Validazione (Gap Analysis)**: Se l'LLM si accorge che mancano i *4 Pilastri* (Dimensioni, Carico Meccanico, Ambiente, Durata), la Massa o la Geografia, **interrompe l'esecuzione** e formula le domande necessarie per procedere (Human-in-the-loop).

---

## 3. Parametri e Restrizioni (Pydantic & State)
Il flusso di pensiero dell'IA è ingabbiato in schemi rigidi usando la libreria `pydantic`. L'LLM comunica *solamente* emettendo JSON validati.

### Lo Stato Globale (`AgentState`)
In `agents/state.py` viene mantenuto un `TypedDict` persistente (LangGraph Memory):
- `current_lca_step`: Tiene traccia dell'avanzamento visivo nella UI.
- `assumptions_list`: Traccia le allucinazioni controllate (le ipotesi fatte).
- `logistics_data`: Mantiene i dati crudi su $tkm$ e distanza.
- `pending_feedback`: Gestisce l'interruzione del grafo in attesa della risposta umana.

### I Filtri di Estrazione Pydantic
In `agents/schemas.py`, gli oggetti come `BOMComponent` e `WorkflowAndBOMResponse` forzano l'LLM a non saltare i passaggi:
- Campi come `material_source`, `geometry` e `manufacturing_process` sono obbligatori (`str = Field(...)`). Se l'LLM sbaglia, la validazione fallisce, il sistema logga l'errore ed esegue un fallback al prompt testuale grezzo per auto-correggersi (`_invoke_structured`).

---

## 4. Ancoraggio alla Realtà: Dataset e Fuzzy Matching
Poiché le idee dell'LLM (es. "Plastica riciclata") non coincidono mai esattamente con le diciture ingegneristiche dei dataset, il progetto si poggia sul file `data/csv_lca_client.py`:
- Alla partenza carica `DataSet.xlsx` in memoria via `Pandas`.
- Usa il metodo `find_closest_match` (tramite la libreria built-in `difflib`) per eseguire un Fuzzy Matching. Se l'IA propone "wood", il sistema cerca la riga più simile nel dataset.
- Se l'IA inventa un materiale inesistente, il client si appoggia al `BASELINE_LCA_PROFILES`, iniettando un "rumore deterministico" matematico basato sull'MD5 hash del nome, così che i test restino riproducibili ma i numeri appaiano organici.

## 5. Il Calcolo Deterministico dell'Impatto (LCA Validator)
Trovato in `agents/nodes.py`. È pura logica matematica:
`Impatto Totale = (Impatto_Materiale_Unitario + Impatto_Processo + Impatto_Trasporto) * Massa_kg`

L'LLM **NON** vi ha accesso. Riceve solo i risultati finiti per le fasi di scoring (MCDA), in cui valuta costi, durate, compatibilità strutturale e impatto ambientale usando un'unica metrica dinamica (`environmental_impact_unit` importata da `core/config.py`).

---

## 6. Sviluppi Futuri e Aggiornamenti Possibili (Roadmap per IA Successive)
Se devi migliorare questo software, ecco le aree a più alto potenziale di ottimizzazione (Low hanging fruits):

1. **RAG / Embeddings Vectoriali (Sostituto del Fuzzy Matching)**
   *Attualmente:* `difflib` controlla somiglianze stringa. "Legno" e "Wood" non matchano.
   *Upgrade:* Caricare il `DataSet.xlsx` in un database vettoriale (es. ChromaDB) all'avvio. Convertire le risposte dell'LLM in embedding e cercare i Materiali/Processi tramite *Cosine Similarity*.
2. **Sistema Logistico Avanzato via API**
   *Attualmente:* Si usa un dummy value o l'LLM estrae il luogo.
   *Upgrade:* Integrare le API di Google Maps o OpenStreetMap nel nodo di Logistica per calcolare la distanza reale tra l'utente e il presunto fornitore per il $tkm$.
3. **MCDA Dinamico con "Personalità" (AHP)**
   *Attualmente:* L'MCDA (Multi-Criteria Decision Analysis) dà pesi standardizzati per Sostenibilità, Costo ed Estetica.
   *Upgrade:* Permettere all'utente nella Glass Box UI di muovere degli slider (es. "Importanza del Costo" vs "Importanza Ambiente") per generare i pesi dinamicamente tramite la gerarchia AHP (Analytic Hierarchy Process).
4. **LLM Vision per Analisi Geometrie (Multimodalità)**
   *Attualmente:* L'LLM deve leggere una descrizione per mappare la "Geometria".
   *Upgrade:* Far caricare all'utente il render 3D o lo schizzo (immagine) nella UI Streamlit e passarlo a `gpt-4o` o `llava` affinché il nodo estrattore misuri visivamente i 4 pilastri e le geometrie necessarie.
5. **Database LCA basato su SQL/Cloud**
   *Attualmente:* Caricamento Pandas monolitico.
   *Upgrade:* Esportare `DataSet.xlsx` su un database SQLite o PostgreSQL per query LCA ad alta efficienza.
