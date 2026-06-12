# 📋 Analisi Test & Migliorie — LCA Sustainability Co-Pilot

**Data esecuzione test:** 2026-06-13  
**Ambiente:** Python 3.14.0, pytest-9.0.3, Windows  
**Dataset LCA:** `dataset_ecoinvent_perfetto.xlsx` (~2.5 MB)

---

## ✅ Risultato Test Suite

```
21 passed in 17.75s — TUTTI I TEST SUPERATI
```

| Suite | # Test | Risultato |
|---|---|---|
| `tests/test_data_layer.py` | 12 | ✅ 12/12 PASSED |
| `tests/test_graph.py` | 9 | ✅ 9/9 PASSED |

Nessun test fallito. Nessun warning critico da pytest.

---

## ⚠️ PROBLEMATICHE IDENTIFICATE DALL'ANALISI DEL CODICE

### 🔴 PROBLEMA 1 — Copertura test insufficiente (Test Coverage ~30%)

**File coinvolti:** `tests/`, `agents/nodes.py`, `agents/workflow_node.py`, `reports/generator.py`

**Descrizione:**  
I test esistenti coprono solo:
- `test_data_layer.py`: layer CSV (ricerca, match, score)
- `test_graph.py`: pipeline completa con **LLM finto** (mock)

**Cosa NON è testato:**
- `reports/generator.py` → `generate_html_report()` e `generate_pdf_report()` non hanno nemmeno un test di smoke
- `agents/workflow_node.py` (47 KB!) → nessun test unitario
- `agents/material_node.py` (6 KB) → nessun test unitario
- Il routing del grafo (`route_after_feedback`, `route_after_lca`, `route_after_material`) non ha test dedicati
- `human_feedback_processor` (nodo critico) non ha test con feedback reale (es. approvazione, rifiuto)
- Il calcolo del trasporto misto ("Mixed Logistics") non è testato in isolamento

---

### 🔴 PROBLEMA 2 — Nessun test per i prompt reali del Prompt.txt

**File coinvolti:** `Prompt.txt`, `agents/graph.py`

**Descrizione:**  
Il file `Prompt.txt` contiene 10 prompt reali in italiano che rappresentano i casi d'uso principali dell'applicazione:
1. Sedia in plastica (8 kg, Germania, 600 km camion)
2. 200 kg EPS polistirene, Europa
3. 15 kg LDPE sacchetti, Francia, 450 km
4. 25 kg alluminio estruso, Germania, 150 km
5. 50 kg tubo PVC, Germania, 500 km
6. Film in polivinilfluoruro, USA, 300 km
7. 500 kg profilati alluminio + 1500 km nave
8. 200 kg carta grafica riciclata, Polonia
9. 10 kg PP stampato a iniezione, Europa (no trasporto)
10. Racchetta padel fibra di carbonio, Italia, 600 km (stima massa)
11. Microchip silicio, aereo Shenzhen→Milano 9500 km (stima massa)

**Nessuno di questi prompt è eseguito nei test automatici.** Non esiste un test end-to-end con LLM reale che verifichi che la pipeline produca output sensati per questi casi.

---

### 🟡 PROBLEMA 3 — API Key esposta nel file `.env`

**File coinvolto:** `.env` (riga 6)

**Descrizione:**  
La chiave `OPENROUTER_API_KEY=sk-or-v1-...` è in chiaro nel file `.env`. Anche se `.env` è tipicamente nel `.gitignore`, il `.gitignore` attuale potrebbe non escluderlo correttamente o potrebbe essere stato committato in passato.

```
OPENROUTER_API_KEY=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
```

---

### 🟡 PROBLEMA 4 — `asyncio_mode = auto` in `pytest.ini` con `asyncio.run()` nei test

**File coinvolti:** `pytest.ini`, `tests/test_data_layer.py`

**Descrizione:**  
Il `pytest.ini` configura `asyncio_mode = auto`, che permette di dichiarare test come `async def`. Tuttavia, `test_data_layer.py` usa `asyncio.run()` dentro funzioni di test **sincrone**, il che è ridondante e può causare comportamenti inattesi in Python 3.14 dove il loop di default è cambiato. I test passano ora, ma potrebbero rompersi con versioni future di `pytest-asyncio`.

**Esempio problematico:**
```python
def test_search_materials_returns_results(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("electricity"))  # ← dovrebbe essere async def + await
```

---

### 🟡 PROBLEMA 5 — Duplicazione del codice di display geography (dict inline ripetuto)

**File coinvolto:** `agents/nodes.py` (righe 314, 360, 474)

**Descrizione:**  
Lo stesso dizionario di mapping `{it: Italy, fr: France, ...}` appare **3 volte** identico nel file `nodes.py`. Questo viola il principio DRY (Don't Repeat Yourself) e rende manutenzione difficile.

---

### 🟡 PROBLEMA 6 — Nessuna validazione dell'output del report HTML

**File coinvolto:** `reports/generator.py`

**Descrizione:**  
`generate_html_report()` non ha nessun test. Se viene passato uno stato malformato (es. `lca_results` con struttura diversa dal previsto), la funzione potrebbe silenziosamente produrre HTML vuoto o parziale senza alcun errore visibile all'utente.

---

### 🟡 PROBLEMA 7 — Il prompt duplicato in `Prompt.txt`

**File coinvolto:** `Prompt.txt` (righe 53–58)

**Descrizione:**  
Le ultime due righe del file `Prompt.txt` sono identiche al prompt precedente (microchip silicio, aereo Shenzhen→Milano). Si tratta di una duplicazione accidentale che confonde chi legge la lista dei casi di test.

---

### 🟠 PROBLEMA 8 — Nessun test di regressione sul calcolo CO₂

**File coinvolto:** `agents/nodes.py` (funzione `lca_validator`)

**Descrizione:**  
La formula LCA è:
```
CO₂_totale = (impatto_materiale × scala) + (impatto_processo × scala) + impatto_trasporto
```
Non esiste nessun test che verifichi che il valore numerico sia corretto per un input noto. Se la formula venisse modificata (es. errore nel `_resolve_scale_factor`), i test attuali **non se ne accorgerebbero** perché verificano solo `>= 0`, non un valore atteso preciso.

---

## 🔧 MIGLIORIE STEP-BY-STEP DA IMPLEMENTARE

### STEP 1 — Rimuovere il prompt duplicato da `Prompt.txt`
**Priorità:** Bassa | **Difficoltà:** Minima | **Tempo:** 2 minuti

Eliminare le righe 53–58 di `Prompt.txt` (prompt del microchip duplicato).

---

### STEP 2 — Estrarre il dizionario geography in una costante condivisa
**Priorità:** Media | **Difficoltà:** Bassa | **Tempo:** 15 minuti

In `agents/nodes.py` o `core/config.py`, definire una sola volta:
```python
GEO_DISPLAY_MAP = {
    "it": "Italy", "fr": "France", "de": "Germany",
    "es": "Spain", "uk": "United Kingdom", "us": "United States",
    "rer": "Europe (RER)", "glo": "Global", "row": "Rest of World"
}
```
E usarlo ovunque al posto dei dict inline ripetuti.

---

### STEP 3 — Convertire i test `asyncio.run()` in test `async def`
**Priorità:** Media | **Difficoltà:** Bassa | **Tempo:** 20 minuti

Cambiare tutti i test in `tests/test_data_layer.py` che usano `asyncio.run()` in funzioni `async def` con `await`, sfruttando la modalità `asyncio_mode = auto` già configurata in `pytest.ini`.

**Prima:**
```python
def test_search_materials_returns_results(client):
    results = asyncio.run(client.search_materials("electricity"))
```
**Dopo:**
```python
async def test_search_materials_returns_results(client):
    results = await client.search_materials("electricity")
```

---

### STEP 4 — Aggiungere test di smoke per `generate_html_report()`
**Priorità:** Alta | **Difficoltà:** Bassa | **Tempo:** 30 minuti

Creare `tests/test_report.py` con almeno:
- Test che verifica che l'HTML generato contenga i tag principali (`<html>`, `<body>`, `<table>`)
- Test che verifica che con stato vuoto non lanci eccezioni
- Test che con `task_type="modeling"` vs `"optimization"` produca HTML diverso

---

### STEP 5 — Aggiungere test di regressione numerica sull'LCA
**Priorità:** Alta | **Difficoltà:** Media | **Tempo:** 45 minuti

Usando ID noti del dataset (`_REAL_ID_A`, `_REAL_ID_B` già definiti in `test_data_layer.py`), aggiungere un test che:
1. Carica un componente con materiale noto (es. polypropylene)
2. Esegue `lca_validator` con mock LLM
3. Verifica che il CO₂ totale sia circa il valore atteso (entro ±5%)

Questo protegge la formula LCA da regressioni silenziosee.

---

### STEP 6 — Aggiungere test per i casi del `Prompt.txt`
**Priorità:** Alta | **Difficoltà:** Media | **Tempo:** 1–2 ore

Creare `tests/test_prompts.py` con test parametrizzati per i prompt reali. Usare LLM mockato (come in `test_graph.py`) ma con stati iniziali realistici corrispondenti ai casi del `Prompt.txt`.

Esempio struttura:
```python
@pytest.mark.parametrize("prompt,expected_task_type", [
    ("Voglio modellare una sedia in plastica...", "modeling"),
    ("Voglio ottimizzare un lotto di sacchetti LDPE...", "optimization"),
    ...
])
def test_prompt_detected_task_type(prompt, expected_task_type):
    # verifica che _detect_task_type rilevi il tipo corretto
    ...
```

---

### STEP 7 — Aggiungere test per il routing del grafo
**Priorità:** Alta | **Difficoltà:** Media | **Tempo:** 30 minuti

Aggiungere in `tests/test_graph.py` test dedicati per le funzioni di routing:
- `route_after_feedback` con `phase="error"` → deve restituire `END`
- `route_after_feedback` con `phase="workflow"` e keyword "ottimizz" → deve restituire `"material_ideator"`
- `route_after_lca` con `task_type="modeling"` → deve restituire `END`
- `route_after_material` con `phase="error"` → deve restituire `END`

---

### STEP 8 — Ruotare / invalidare la API Key OpenRouter esposta
**Priorità:** Critica (sicurezza) | **Difficoltà:** Bassa | **Tempo:** 5 minuti

La chiave `sk-or-v1-3be9a...` presente nel `.env` dovrebbe essere **ruotata immediatamente** su https://openrouter.ai/settings/keys se è mai stata committata in git.

Verificare con:
```bash
git log --all -p -- .env | grep OPENROUTER_API_KEY
```
Se compare nella history git, la chiave è compromessa e va rigenerata.

Aggiungere al `.gitignore` (se non già presente):
```
.env
*.env
```

---

### STEP 9 — Aggiungere test per `human_feedback_processor` con feedback reale
**Priorità:** Media | **Difficoltà:** Media | **Tempo:** 45 minuti

Il nodo `human_feedback_processor` gestisce l'approvazione/rifiuto dell'utente, ma non è mai testato con feedback simulato. Aggiungere test che:
- Passano un feedback di approvazione (es. "ok", "sì", "procedi") → verifica `pending_feedback=None`
- Passano un feedback di modifica → verifica che `bom` o `constraints` vengano aggiornati

---

### STEP 10 — Aggiungere test per il calcolo del trasporto misto
**Priorità:** Media | **Difficoltà:** Media | **Tempo:** 45 minuti

La logica "Mixed Logistics" in `lca_validator` non è coperta da nessun test. Aggiungere:
- Test con `distance_km > 0` e `transport_mode="lorry"` → verifica `transport_impact > 0`
- Test con nave transoceanica (`ship`) → verifica uso di `SHIP_IMPACT_PER_TKM`
- Test con aereo (`aircraft`) → verifica uso di `AIRCRAFT_IMPACT_PER_TKM`
- Test con `distance_km = 0` → verifica `transport_impact = 0`

---

## 📊 Riepilogo Priorità

| Step | Tipo | Priorità | Impatto |
|------|------|----------|---------|
| STEP 8 | Sicurezza | 🔴 CRITICA | Protezione API key |
| STEP 4 | Testing | 🔴 ALTA | Copertura report |
| STEP 5 | Testing | 🔴 ALTA | Regressione numerica LCA |
| STEP 6 | Testing | 🔴 ALTA | Casi reali Prompt.txt |
| STEP 7 | Testing | 🔴 ALTA | Routing grafo |
| STEP 3 | Refactoring | 🟡 MEDIA | Compatibilità asyncio futuro |
| STEP 9 | Testing | 🟡 MEDIA | Human feedback |
| STEP 10 | Testing | 🟡 MEDIA | Calcolo trasporto |
| STEP 2 | Refactoring | 🟡 MEDIA | Manutenibilità codice |
| STEP 1 | Cleanup | 🟢 BASSA | Pulizia file |
