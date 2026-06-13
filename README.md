# 🌿 Sustainable Product Optimization

Questo progetto utilizza un agente LangGraph e un'interfaccia Streamlit per ottimizzare la sostenibilità di un prodotto industriale. Analizza il prodotto, estrae i vincoli, genera un Bill of Materials (BOM) e propone materiali alternativi più sostenibili validati tramite dati LCA (Life Cycle Assessment) e una logica MCDA (Multi-Criteria Decision Analysis).

## 🚀 Come iniziare (Metodo Facile)

Abbiamo semplificato l'installazione e l'avvio per Windows.

### 1. Installazione (Prima volta)
Fai **doppio click** sul file `SETUP.bat`.
Questo script automatico si occuperà di:
- Creare un ambiente virtuale pulito
- Installare tutte le dipendenze necessarie

### 2. Avvio dell'Applicazione
Fai **doppio click** sul file `START.bat`.
Questo script attiverà l'ambiente e aprirà l'interfaccia utente (Streamlit) direttamente nel tuo browser.

---

## ⚙️ Configurazione (.env)

Il progetto utilizza variabili d'ambiente per la configurazione e per la gestione sicura dei segreti (come le chiavi API). **Attenzione: non committare mai la tua chiave API!**

Per configurare l'applicazione, crea un file `.env` nella root del progetto (puoi usare `.env.example` come base se presente) oppure esporta le variabili nel terminale prima dell'avvio (es: `set OPENROUTER_API_KEY=sk-or-...` su Windows).

Esempio di configurazione nel file `.env`:
```env
OPENROUTER_API_KEY=sk-or-your-actual-api-key
```

| Variabile | Descrizione | Opzioni |
| --- | --- | --- |
| `LLM_PROVIDER` | Backend LLM (unico supportato) | `openrouter` |
| `OPENROUTER_API_KEY` | Chiave API di OpenRouter | **Obbligatoria** |
| `OPENROUTER_MODEL` | Modello su OpenRouter | es. `openai/gpt-4o-mini` |
| `LCA_DATA_SOURCE` | Sorgente dati LCA | `csv` (default), `ecoinvent_api` (non implementato) |
| `WEIGHT_CO2` / `WEIGHT_COST` / `WEIGHT_ENERGY` / `WEIGHT_WATER` | Pesi MCDA (la somma deve essere 1.0) | default `0.70` / `0.30` / `0.0` / `0.0` |

---

## 📁 Struttura del Progetto

- `agents/`: Contiene i nodi di LangGraph e la definizione del grafo (`graph.py`).
- `core/`: Configurazione e inizializzazione del modello LLM (`llm_factory.py`).
- `data/`: Gestione dei dati LCA. Di default usa il dataset locale `dataset_ecoinvent_perfetto.xlsx`.
- `prompts/`: Istruzioni di sistema YAML per guidare l'LLM nelle varie fasi.
- `reports/`: Generazione dei report in PDF/HTML con WeasyPrint.
- `ui/`: Applicazione Streamlit "Glass Box" (`app.py`).
- `tests/`: Test automatizzati per validare il grafo e il data layer.

## 🧪 Eseguire i test

Se vuoi verificare che tutto funzioni a livello di logica, apri il terminale, attiva l'ambiente e lancia i test:

```cmd
call venv\Scripts\activate.bat
pytest tests/
```
