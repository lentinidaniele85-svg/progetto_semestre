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

Puoi copiare `.env.example` in `.env` per modificare le impostazioni del modello o dei pesi dell'MCDA:

| Variabile | Descrizione | Opzioni |
| --- | --- | --- |
| `LLM_PROVIDER` | Quale backend LLM usare | `ollama`, `openrouter` |
| `OLLAMA_MODEL` | Nome del modello locale | es. `llama3`, `mistral` |
| `OPENROUTER_API_KEY` | Chiave API di OpenRouter | — |
| `OPENROUTER_MODEL` | Modello su OpenRouter | es. `openai/gpt-3.5-turbo` |
| `LCA_DATA_SOURCE` | Sorgente dati LCA | `csv`, `ecoinvent_api` |

---

## 📁 Struttura del Progetto

- `agents/`: Contiene i nodi di LangGraph e la definizione del grafo (`graph.py`).
- `core/`: Configurazione e inizializzazione del modello LLM (`llm_factory.py`).
- `data/`: Gestione dei dati LCA. Di default usa un file locale (`export.csv`).
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
