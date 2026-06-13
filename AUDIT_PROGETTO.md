# 🔍 Audit del Progetto — LCA Sustainability Co-Pilot

**Data:** 2026-06-13
**Tipo di analisi:** revisione manuale di tutti i moduli Python del progetto (`agents/`, `core/`, `data/`, `reports/`, `ui/`, ~6700 righe totali) + verifica con grep dei findings su codice morto.

> ℹ️ Nel repo esiste già [`analisi_test_e_migliorie.md`](analisi_test_e_migliorie.md), focalizzato sulla test suite (coverage, asyncio, API key in `.env`, duplicazioni in `Prompt.txt`). Questo documento è **complementare**: copre problemi di codice/architettura emersi dalla lettura end-to-end della pipeline che non sono trattati nell'altro file (in particolare un possibile **XSS nel report HTML** e diverse aree di **codice morto**).

---

## 🔴 Bug Urgenti

### 1. Possibile HTML/XSS injection nel report generato (`reports/generator.py`)

**Gravità: Alta — riguarda contenuto mostrato in browser.**

In `generate_html_report()`, alcuni campi vengono interpolati **senza escaping** direttamente nell'HTML del report:

- `user_input` (testo libero scritto dall'utente in chat) viene inserito così com'è:
  ```python
  <strong>Descrizione Prodotto:</strong> {user_input}
  ```
- I valori di `constraints.items()` (`k`, `v`) e le stringhe di `assumptions_list` vengono inseriti come `<li>{v}</li>` / `<li>{a}</li>` — questi valori provengono in parte dall'LLM e in parte dal testo di feedback dell'utente.

**Perché è un problema:** il report HTML generato viene scaricato dall'utente ma è anche **mostrato nella colonna destra di `ui/app.py`** ("report canvas"). Se `user_input` (o un valore di `constraints`/`assumptions_list` derivato da esso) contiene caratteri come `<`, `>`, `&` o un tag `<script>`/`<img onerror=...>`, questi finiscono nell'HTML non sanificati: nel caso migliore rompono il layout, nel caso peggiore costituiscono un **vettore di HTML/script injection** quando il report viene aperto in un browser.

**Fix proposto:** usare `html.escape()` su tutti i campi testuali provenienti da `user_input`, `constraints` e `assumptions_list` prima dell'interpolazione in `generate_html_report()` (in particolare nella sezione `.meta` e in `_assumptions_list()`).

---

### 2. Fallback CO₂ errato per i processi "Extrusion (film)" / "Extrusion" in `lca_validator` (`agents/nodes.py`)

**Gravità: Media-Alta — produce un numero silenziosamente sbagliato senza errori visibili.**

In `lca_validator` (agents/nodes.py:273-296), quando la ricerca dinamica del processo nel dataset fallisce, si usa un fallback hardcoded:

```python
GEOMETRY_TO_PROCESS = {
    "Corpi Cavi": "Blow moulding",
    "Pezzi Pieni Complessi": "Injection moulding",
    "Film": "Film extrusion",
    "Profili/Tubi": "Tube extrusion"
}
process_name = GEOMETRY_TO_PROCESS.get(orig_comp.get("geometry"), "Injection moulding").lower()
...
process_impact = PROCESS_IMPACTS.get(process_name, PROCESS_IMPACTS.get(process_name.capitalize(), 1.0))
```

`PROCESS_IMPACTS` (in `core/config.py`) contiene le chiavi `"Extrusion (film)"` (0.5) e `"Extrusion"` (0.6) — **non** `"Film extrusion"` né `"Tube extrusion"`. Quindi:

- per `geometry = "Film"` → `process_name = "film extrusion"` → `.capitalize()` = `"Film extrusion"` → **non matcha nessuna chiave** → fallback finale `1.0`
- per `geometry = "Profili/Tubi"` → `process_name = "tube extrusion"` → `.capitalize()` = `"Tube extrusion"` → **non matcha nessuna chiave** → fallback finale `1.0`

Per `"Corpi Cavi"` e `"Pezzi Pieni Complessi"` il `.capitalize()` invece combacia correttamente con `"Blow moulding"`/`"Injection moulding"`.

**Effetto:** se per un componente con geometria "Film" o "Profili/Tubi" la ricerca dinamica nel dataset non trova un match per il processo, l'impatto di processo usato è `1.0 kgCO₂/kg` invece dei valori calibrati `0.5`/`0.6` — un errore silenzioso (quasi doppio per "Extrusion") che si propaga nel calcolo finale e nel confronto MCDA.

**Fix proposto:** allineare i nomi in `GEOMETRY_TO_PROCESS` a quelli usati come chiavi in `PROCESS_IMPACTS` (cioè `"Extrusion (film)"` e `"Extrusion"`), oppure aggiungere le varianti mancanti a `PROCESS_IMPACTS`. Verificare anche la consistenza con `GEOMETRY_MAPPING` in `agents/workflow_node.py` (vedi punto 1 della sezione Migliorie), che usa già i nomi corretti `"Extrusion (film)"`/`"Extrusion"`.

---

### 3. Riga "Manufacturing" della BOM in UI mostra sempre il default `1.0` (`ui/app.py`)

**Gravità: Bassa/Media — solo display, non incide sul calcolo LCA reale.**

In `ui/app.py` (righe ~858-879), la riga "(Manufacturing)" della tabella BOM, quando non trovata in `lca_lookup`, calcola:

```python
proc = item.get("manufacturing_process")
...
proc_unit = PROCESS_IMPACTS.get(proc, 1.0)
```

Ma `comp["manufacturing_process"]` viene impostato in `agents/workflow_node.py` (righe ~804-848, tassonomia inline) con stringhe **minuscole** come `"injection moulding"`, `"metal working"`, `"woodworking"`, `"glass production"`, `"textile production, weaving"`, `"electronic component production, wafer fabrication"`. `PROCESS_IMPACTS` ha invece chiavi capitalizzate (`"Injection moulding"`, `"Metal working"`, `"Woodworking"`, `"Glass production"`, `"Textile weaving"`, ecc.) — **case-sensitive**, quindi il lookup fallisce sempre e mostra `1.0` per qualunque processo.

**Fix proposto:** normalizzare il case (es. `.title()` o capitalizzazione coerente) prima del lookup, oppure usare le stesse stringhe esatte di `PROCESS_IMPACTS` quando si assegna `comp["manufacturing_process"]` nella tassonomia inline di `workflow_node.py`.

---

## 🟡 Migliorie / Step Successivi

### 1. Codice morto: ~370 righe in `agents/workflow_node.py` (Process Resolver v1, mai chiamato)

**Verificato con grep** — i seguenti simboli sono definiti ma **non vengono mai chiamati** da nessun punto della pipeline (solo riferimenti reciproci al loro interno):

- `GEOMETRY_MAPPING` (riga 82)
- `COMPONENT_PROCESS_MAPPER` + `get_process_by_component_name()` (righe 100-172)
- `_MATERIAL_CLASS_MAP`, `_GEOMETRY_CLASS_SIGNALS`, `_LLM_GEOMETRY_LABEL_MAP`, `_PROCESS_RESOLUTION_TABLE`, `_classify_material()`, `_classify_geometry()`, `resolve_process()` (righe 189-443)
- `determine_manufacturing_process()` — già marcata "Deprecato" nel codice (riga 447-449), a sua volta chiama `resolve_process()` che non è chiamato da nessun altro punto

Il vero assegnatore di `comp["manufacturing_process"]` è la **tassonomia inline** alle righe ~804-848 dello stesso file (blocco "DIRETTIVA 3: Process Mapper — Material-First con Fallback sul Nome"), che usa una logica completamente diversa (keyword sul nome del materiale → categoria → processo).

**Step successivo:** rimuovere il blocco di ~370 righe (righe 82-450 circa) se confermato che non serve nemmeno per usi futuri, oppure — se si vuole mantenere la logica più sofisticata basata su classi materiale/geometria — **sostituire** la tassonomia inline con una chiamata a `resolve_process()`, evitando due sistemi paralleli che possono divergere.

---

### 2. `CO2_FALLBACK_VALUE` importato ma non utilizzato (`agents/nodes.py`)

**Verificato con grep** — `CO2_FALLBACK_VALUE` (definito in `core/config.py:81`, valore `3.5`) viene importato in `agents/nodes.py:10` ma compare solo in un **commento** (riga 155), non in codice eseguibile. La costante era probabilmente usata da una vecchia logica di fallback, ora sostituita dalla "STRICT MODE" di `lca_validator` (blocco con richiesta di feedback se la confidenza di match è < 0.85/0.75/0.70).

**Step successivo:** rimuovere l'import inutilizzato da `agents/nodes.py` (e valutare se rimuovere anche la costante da `core/config.py`, oppure lasciarla documentata come "storica" se altri moduli potrebbero usarla in futuro).

---

### 3. Logging hygiene: ~30 `print()` di debug in `data/csv_lca_client.py`

Il modulo `csv_lca_client.py` (il motore di ricerca/matching LCA, eseguito ad ogni componente della BOM) contiene decine di `print(f"[DEBUG] ...")`, `[SPELLING]`, `[NORMALIZER]`, `[RECYCLED]`, `[GEOMETRY]`, `[INTENT]`, `[MISS FAIL]`, ecc. — mentre altri moduli usano correttamente `logger = logging.getLogger(__name__)`.

**Effetti:** rumore nei log di produzione, impossibilità di controllare il livello di verbosità, possibile overhead su dataset grandi (ogni `find_closest_match` può generare molte righe di output).

**Step successivo:** convertire i `print()` in chiamate `logger.debug(...)`/`logger.info(...)` con i prefissi esistenti come parte del messaggio, così il livello di log diventa configurabile centralmente.

---

### 4. Cache `_llm_expansion_cache` (globale, mai svuotata) in `data/csv_lca_client.py`

`generate_search_queries()` cachea le espansioni semantiche LLM in un dizionario globale a livello di modulo (`_llm_expansion_cache`), mai svuotato da `flush_provider_cache()` (che pulisce solo `_match_cache`/`_search_cache` dell'istanza `CSVLcaClient`).

**Rischio attuale:** basso, perché la cardinalità è limitata ai materiali/processi distinti incontrati. **Step successivo (se il servizio gira a lungo / multi-tenant):** spostare la cache dentro l'istanza del client (così viene svuotata da `flush_provider_cache()`) o aggiungere un limite/TTL.

---

### 5. MCDA limitato a CO₂ + costo (pesi energia/acqua a zero)

In `core/config.py`, `weight_energy=0.0` e `weight_water=0.0` (solo `weight_co2=0.70` e `weight_cost=0.30` sono attivi), per via di un limite del dataset (`dataset_ecoinvent_perfetto.xlsx` non fornisce questi dati in modo affidabile per tutti i record).

**Step successivo (futuro):** se in futuro si integra una fonte dati con energia/consumo idrico affidabili per riga, riattivare questi pesi nello scoring MCDA per un confronto più completo tra alternative.

---

### 6. Hardcoding del caso "Carbon Fiber" nel prompt di `agents/material_node.py`

Il prompt utente passato all'LLM in `material_ideator` contiene un blocco molto specifico e dettagliato che forza, per i componenti in "Carbon Fiber", la scelta di alternative fisse (`glass fibre`, `nylon`, `polycarbonate`) perché il dataset non ha record di carbon fiber riciclata/bio-based sopra la soglia di confidenza 0.85.

**Rischio:** è un workaround specifico del dataset attuale, fragile se `dataset_ecoinvent_perfetto.xlsx` viene aggiornato (es. se in futuro vengono aggiunti record di carbon fiber riciclata, questo blocco diventerebbe obsoleto e potenzialmente controproducente, ma nessuno se ne accorgerebbe).

**Step successivo:** documentare esplicitamente questa dipendenza dal dataset (es. commento con data/versione del dataset) e/o spostare la lista di "materiali con alternative forzate" in una struttura dati centralizzata/config, così da poterla aggiornare senza modificare il prompt.

---

### 7. Performance: molte chiamate LLM sequenziali per ogni analisi

Una singola esecuzione della pipeline può includere: estrazione constraints, workflow/BOM ideation, eventuale stima massa LLM (`_estimate_mass_with_llm`), material ideation, più una chiamata `generate_search_queries` per ciascun materiale/processo/trasporto distinto incontrato nella ricerca LCA. La cache mitiga le ripetizioni nella stessa sessione, ma il "cold start" di un'analisi nuova resta potenzialmente lento (più chiamate sequenziali a OpenRouter).

**Step successivo (se servono tempi di risposta migliori):** valutare parallelizzazione delle chiamate `generate_search_queries` indipendenti (es. con `asyncio.gather`), o pre-calcolo/caching persistente (su disco) delle espansioni semantiche più comuni tra sessioni.

---

### 8. `EcoinventAPIClient` non implementato

`data/ecoinvent_api_client.py` è uno stub che alza `NotImplementedError("Ecoinvent API integration pending license.")` per entrambi i metodi richiesti dall'interfaccia `LCADataProvider`. Coerente con `lca_data_source="csv"` di default — non è un bug, ma va tenuto a mente come **limite noto** se in futuro si vuole passare a una fonte dati live (richiede licenza Ecoinvent).

---

### 9. Vedi anche `analisi_test_e_migliorie.md`

Per la parte di **test coverage** (es. `reports/generator.py` e `agents/workflow_node.py` senza test unitari, routing del grafo non testato, gestione `.env`/API key, duplicazioni in `Prompt.txt`), fare riferimento al documento già presente [`analisi_test_e_migliorie.md`](analisi_test_e_migliorie.md), che tratta questi aspetti in modo più approfondito e con step prioritizzati. In particolare, lo **STEP 4** di quel documento (test di smoke per `generate_html_report()`) sarebbe un buon punto per verificare anche il fix del punto 1 di questo documento (escaping HTML).

---

## 📊 Riepilogo

| # | Tipo | Voce | Priorità | Stato |
|---|------|------|----------|-------|
| Bug 1 | Sicurezza | XSS/HTML injection in report HTML (`user_input` non escaped) | 🔴 Alta | ✅ Risolto (`reports/generator.py`: escaping con `html.escape`) |
| Bug 2 | Calcolo | Fallback CO₂ errato per Extrusion/Extrusion (film) | 🟠 Media-Alta | ✅ Risolto (`agents/nodes.py`: `GEOMETRY_TO_PROCESS` corretto) |
| Bug 3 | UI | Riga "Manufacturing" BOM mostra sempre impatto 1.0 (case mismatch) | 🟡 Bassa | ✅ Risolto (`ui/app.py`: lookup case-insensitive su `PROCESS_IMPACTS`) |
| Miglioria 1 | Cleanup | ~370 righe codice morto in `workflow_node.py` | 🟡 Media | ✅ Risolto (rimosse) |
| Miglioria 2 | Cleanup | Import inutilizzato `CO2_FALLBACK_VALUE` | 🟢 Bassa | ✅ Risolto (rimosso da `core/config.py`, `agents/nodes.py`, `code_structure.md`) |
| Miglioria 3 | Logging | `print()` → `logger` in `csv_lca_client.py` | 🟡 Media | ✅ Risolto (tutti i `print()` convertiti a `logger.debug()`) |
| Miglioria 4 | Cache | `_llm_expansion_cache` mai svuotata | 🟢 Bassa | ✅ Risolto (`flush_expansion_cache()` chiamata da `flush_match_cache()`) |
| Miglioria 5 | Feature | MCDA: pesi energia/acqua a 0 | 🟢 Futuro | ⏳ Limite noto del dataset, nessuna azione immediata |
| Miglioria 6 | Robustezza | Hardcoding "Carbon Fiber" nel prompt | 🟡 Media | ✅ Documentato (commento di dipendenza dal dataset in `agents/material_node.py`) |
| Miglioria 7 | Performance | Chiamate LLM sequenziali multiple | 🟢 Futuro | ⏳ Nessuna azione immediata, da valutare se necessario |
| Miglioria 8 | Documentazione | `EcoinventAPIClient` non implementato | 🟢 Nota | ⏳ Limite noto, coerente con configurazione attuale |
