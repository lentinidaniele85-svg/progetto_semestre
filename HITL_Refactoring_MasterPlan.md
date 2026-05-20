# HITL Refactoring Master Plan — SPOA
**Human-in-the-Loop Refactoring: Piano di Esecuzione Sequenziale**
*Documento di riferimento pre-implementazione · 2026-05-20*

---

> [!IMPORTANT]
> Eseguire **un Task alla volta**. Non procedere al Task successivo senza esplicita autorizzazione.

---

## 📖 Come Leggere Questo Documento

Ogni Task è strutturato in **due livelli distinti**:

| Sezione | Cosa fare |
|---------|----------|
| **Contesto Architetturale** / **Problema attuale** / **Flusso attuale** / **Perché è un problema** | 📖 **SOLO LETTURA** — Spiega il perché. Non contiene codice da copiare. |
| **Obiettivo del Task** | 📖 **SOLO LETTURA** — Riassume cosa si vuole ottenere. |
| **File Coinvolti** | 📖 **SOLO LETTURA** — Mappa dei file da toccare. |
| **Modifiche Dettagliate** (1.A, 1.B, 1.C …) | 📋 **DA IMPLEMENTARE** — Contiene il codice/prompt esatto da copiare nel file indicato. |
| **Criterio di Completamento** | ✅ **CHECKLIST** — Da spuntare quando il task è fatto. |

> [!TIP]
> Per ogni task, **leggi prima il Contesto** per capire il problema, poi **vai diretto alle Modifiche Dettagliate** per il codice da copiare.

---

## Indice
1. [Task 1 — Lo Scudo dei Dati](#task-1--lo-scudo-dei-dati)
2. [Task 2 — Parsing Ecoinvent e Fallback Logistici](#task-2--parsing-ecoinvent-e-fallback-logistici)
3. [Task 3 — Trasporto Dinamico e Logica Market-For](#task-3--trasporto-dinamico-e-logica-market-for)
4. [Task 4 — UI Narrativa e Correzione Granulare](#task-4--ui-narrativa-e-correzione-granulare)

---

---

# TASK 1 — Lo Scudo dei Dati

> **📖 Sezioni 1.1–1.3 = SOLO LETTURA (contesto e motivazione)**
> **📋 Sezioni 1.A–1.F = CODICE DA IMPLEMENTARE (copia nei file indicati)**

## Contesto Architetturale

### Il problema attuale (codice vigente)

Il sistema attuale **inietta silenziosamente valori di default** ogni volta che l'utente non specifica dati fondamentali. Questo avviene in due punti distinti:

**1. `agents/workflow_node.py` — righe 283-295:**
```python
mass = result.total_mass_kg or 1.0          # ← 1.0 kg se non specificata

if result.distance_km is not None:
    dist_km = result.distance_km
else:
    dist_km = 500.0                          # ← 500 km se non specificata
    assumptions.append(
        "Logistics distance not specified: using default value 500 km."
    )
```

**2. `prompts/semantic_ideation_api.yaml` — Step 7 (righe 165-205):**
```yaml
Step 7 — Completamento Proattivo (NO blocking)
  ✔ Mass known?
    NO → apply standard assumption by product category:
          • Sedia/Chair → 4.5 kg
          ...
          RECORD the assumption. Set is_interview_complete=True.
  ✔ Geography known?
    NO → default to RER (European average)
```
Questo prompt **vieta esplicitamente** al modello di bloccarsi per chiedere massa o geografia.

**3. `agents/nodes.py` — riga 401 (lca_validator):**
```python
transport_match = provider.find_closest_match(
    target_product="transport, freight, lorry, unspecified",
    target_geography="RER"   # ← FISSO, sempre Europe senza Swiss
)
```

**4. `agents/schemas.py` — `WorkflowAndBOMResponse`:**
```python
geography: Optional[str]      # = nazione di PRODUZIONE (non del fornitore)
distance_km: Optional[float]  # = distanza km, solo se esplicitata
```
Lo schema non distingue tra **nazione di produzione** e **nazione del fornitore** (origine del trasporto). Sono la stessa variabile `geography`, usata ambiguamente.

**5. `agents/schemas.py` — `ConstraintsExtract`:**
```python
geography: Optional[str]  # = "area geografica o nazione di produzione"
```
Anche qui nessun campo separato per fornitore vs destinazione.

### Flusso attuale (cosa succede quando l'utente non specifica nulla)

```
User: "una sedia"
  │
  ▼
constraint_extractor → constraints = {task_type: "optimization"}  [niente mass, niente geography]
  │
  ▼
workflow_bom_ideator → LLM con prompt "Assumption-First" →
  result.total_mass_kg = 4.5  (assunto dal modello)
  result.geography = "RER"    (assunto dal modello)
  result.distance_km = None
  result.is_interview_complete = True  ← ALWAYS TRUE per il prompt
  │
  ▼
workflow_node.py riga 283: mass = 4.5
workflow_node.py riga 289: dist_km = 500.0  (default Python)
  │
  ▼
→ Procede al CSV lookup con dati inventati. Zero domande all'utente.
```

### Perché questo è un problema

1. **Accuratezza scientifica**: L'impatto LCA è proporzionale alla massa (`mat_total_impact = mat_impact × mass_kg`). Una massa sbagliata di 10× produce risultati sbagliati di 10×.
2. **Distanza di trasporto**: `transport_impact_total = total_tkm × transport_impact_per_tkm`. Con 500 km di default per una sedia prodotta in Cina (che potrebbe essere 10.000 km), l'impatto trasporto è sottostimato di 20×.
3. **Nazione del fornitore**: Manca completamente. Se l'acciaio viene dalla Cina ma il prodotto è assemblato in Italia, l'impatto materiale dovrebbe usare dataset Cina, non Italia.

---

## Obiettivo del Task 1

> Non si procede al CSV lookup finché l'utente non ha confermato esplicitamente: **massa totale**, **nazione di produzione**, e almeno una tra **nazione del fornitore** o **distanza di trasporto**.

---

## File Coinvolti

| File | Tipo modifica |
|------|--------------|
| `agents/schemas.py` | Aggiunta campi `supplier_country` e `destination_country` in `ConstraintsExtract` e `WorkflowAndBOMResponse` |
| `agents/state.py` | Aggiunta campo `interview_attempt_count` |
| `prompts/semantic_ideation_api.yaml` | Riscrittura Step 6 e Step 7 — rimozione "Assumption-First" per dati fondamentali |
| `agents/workflow_node.py` | Rimozione default silenzioso per `dist_km` e `mass`; aggiunta logica blocco |
| `agents/nodes.py` (`constraint_extractor`) | Aggiornamento system prompt per estrarre i nuovi campi |

---

## Modifiche Dettagliate

### 1.A — `agents/schemas.py`: Nuovi campi logistici

**Aggiungere in `ConstraintsExtract`:**
```python
supplier_country: Optional[str] = Field(
    default=None,
    description=(
        "Nazione di origine del materiale/fornitore (es. 'China', 'Germany'). "
        "Diverso da geography (nazione di produzione). Usato per il calcolo del trasporto."
    )
)
destination_country: Optional[str] = Field(
    default=None,
    description=(
        "Nazione di destinazione/assemblaggio (es. 'Italy'). "
        "Usato per calcolare la distanza fornitore→sito."
    )
)
```

**Aggiungere in `WorkflowAndBOMResponse`:**
```python
supplier_country: Optional[str] = Field(
    default=None,
    description=(
        "Nazione di origine del fornitore del materiale principale. "
        "Se esplicitata dall'utente, usata per cercare il dataset di trasporto corretto. "
        "NON inferire se non dichiarata."
    )
)
destination_country: Optional[str] = Field(
    default=None,
    description=(
        "Nazione di destinazione/assemblaggio. "
        "NON inferire se non dichiarata. "
        "Se nota, usata con supplier_country per stimare distance_km."
    )
)
```

**Aggiungere in `AgentState` (`agents/state.py`):**
```python
interview_attempt_count: int   # Contatore tentativi intervista (inizia a 0)
supplier_country: str          # Nazione fornitore estratta
destination_country: str       # Nazione destinazione estratta
```

---

### 1.B — `prompts/semantic_ideation_api.yaml`: Riscrittura Step 6 e Step 7

> 📋 **DA IMPLEMENTARE**: sostituire il testo del file `prompts/semantic_ideation_api.yaml` dalle righe dello Step 6 alla fine dello Step 7 con i blocchi seguenti. Il resto del file rimane invariato.

**Step 6 (nuovo — Logistica, NON Assumption-First):**

Sostituire le righe 157-163 con:

```yaml
  Step 6 — Logistica (DATI OBBLIGATORI — NON INFERIRE)
  ─────────────────────────────────────────────────────
  La logistica richiede due dati FONDAMENTALI che NON possono essere inventati:
    A. Nazione del fornitore del materiale (supplier_country)
    B. Distanza di trasporto (distance_km) OPPURE nazione di destinazione (destination_country)

  REGOLA ASSOLUTA — Non applicare default silenziosi per la logistica:
    - Se l'utente NON specifica supplier_country → imposta is_interview_complete=False
      e inserisci in interview_questions: "Da quale paese/regione proviene il materiale
      principale (es. acciaio dalla Cina, plastica dall'Europa)?"
    - Se l'utente NON specifica né distance_km né destination_country → aggiungi in
      interview_questions: "Qual è la distanza (in km) dal fornitore al sito di
      produzione, oppure la nazione di destinazione/assemblaggio?"
    - ECCEZIONE: Se is_material_only=True (solo materiale grezzo senza processo
      manifatturiero), i dati logistici non sono necessari. Salta questa fase.
    - Se l'utente specifica distance_km esplicitamente → estrailo in distance_km
      e NON chiedere altro.
    - Se l'utente specifica supplier_country E destination_country ma NON distance_km
      → RAGIONA sulla distanza usando la tua conoscenza geografica:
        • Pensa alla distanza stradale/marittima realistica tra i due paesi.
        • Considera il mezzo di trasporto più probabile per quella rotta
          (es. container via mare tra Cina e Italia, camion tra Germania e Italia).
        • Registra esplicitamente il ragionamento in assumptions_made, ad esempio:
          "Stima distanza Cina→Italia: ~9.000 km via mare (container shipping).
           Basato su rotte commerciali standard. Fornire distanza esatta se nota."
        • Imposta distance_km con il valore stimato.
        • NON usare un numero fisso generico (es. 500) senza ragionamento.

  NON aggiungere mai un valore di default (es. 500 km) senza ragionamento
  esplicito e dichiarazione dell'assunzione in assumptions_made.
```

**Step 7 (nuovo — Solo blocco per dati fondamentali):**

Sostituire le righe 165-205 con:

```yaml
Step 7 — Completamento e Verifica Finale
  ─────────────────────────────────────────────────
  Dopo i passi 1-6, verifica i seguenti dati fondamentali:

  ✔ Massa nota?
    SÌ (nel constraints o nell'input) → usa quel valore. NON chiedere di nuovo.
    NO → BLOCCO OBBLIGATORIO. Imposta is_interview_complete=False.
         Aggiungi in interview_questions:
         "Qual è la massa totale del prodotto (in kg)?"
         Motivazione: la massa moltiplica linearmente ogni impatto LCA.
         Un valore errato invalida l'intero calcolo.

  ✔ Nazione di produzione (geography) nota?
    SÌ → usa quel valore.
    NO → BLOCCO OBBLIGATORIO. Imposta is_interview_complete=False.
         Aggiungi in interview_questions:
         "In quale paese viene prodotto/assemblato l'oggetto?"
         Motivazione: il dataset ecoinvent è geo-specifico.
         Usare RER invece di CN o US produce errori sistematici.

  ✔ Dati logistici presenti? (verificati in Step 6)
    Se is_material_only=True → salta (logistica non applicabile).
    Se mancano → BLOCCO (già gestito in Step 6).

  ✔ Materiale noto?
    SÌ → usa (già gestito in Step 3).
    NO → PUOI inferire per esclusione tecnica (Step 3). Registra l'assunzione.
         Il materiale è l'UNICO dato fondamentale per cui è consentita l'inferenza.

  REGOLE FINALI:
    • is_interview_complete=True SOLO SE massa, geography, E dati logistici
      sono tutti noti (o is_material_only=True).
    • Se is_interview_complete=False, interview_questions DEVE contenere
      almeno una domanda precisa.
    • NESSUNA domanda ripetuta: se il dato è già in constraints o in
      [User Interview Response], NON chiedere di nuovo.
    • Geometria/processo mancanti → puoi inferire (non bloccare).
    • Materiale mancante → puoi inferire (non bloccare).
```

---

### 1.C — `agents/workflow_node.py`: Rimozione default silenziosi

> 📋 **DA IMPLEMENTARE**: nel file `agents/workflow_node.py`, sostituire le righe 283-295 con il blocco seguente.

**Righe 283-295 (massa e distanza) — sostituire interamente:**

```python
# ── Massa ────────────────────────────────────────────────────────────────────
# Il valore di massa viene dal LLM (result.total_mass_kg).
# Se è None significa che il prompt Step 7 non ha ricevuto abbastanza info
# per ragionare — guard rail Python di ultima istanza.
mass = result.total_mass_kg
if mass is None:
    missing_text = "Non è stato possibile determinare la massa del prodotto.\n"
    missing_text += "- Qual è la massa totale del prodotto (in kg)?\n"
    return {
        "pending_feedback": missing_text,
        "thought_log": thought_log,
        "assumptions_list": assumptions,
        "current_lca_step": 2,
        "current_phase": "interview",
    }

# ── Distanza/Logistica ────────────────────────────────────────────────────────
# L'LLM (tramite il prompt Step 6 aggiornato) è responsabile di:
#   A. Estrarre distance_km se dichiarata dall'utente.
#   B. Stimare distance_km ragionando su supplier_country + destination_country.
#   C. Impostare is_interview_complete=False e chiedere i dati se non li ha.
# Il codice Python qui serve solo come guard rail per il caso in cui
# il modello non abbia seguito le istruzioni.
dist_km: Optional[float] = result.distance_km
supplier_country: Optional[str] = result.supplier_country
destination_country: Optional[str] = result.destination_country

if dist_km is None and not result.is_material_only:
    # Il LLM avrebbe dovuto o stimare o bloccarsi — se arriviamo qui
    # significa che il modello non ha rispettato Step 6. Blocco Python.
    missing_text = "Dati logistici mancanti per il calcolo del trasporto.\n"
    if not supplier_country:
        missing_text += "- Da quale paese proviene il materiale/fornitore principale?\n"
    if not destination_country:
        missing_text += "- Qual è la nazione di destinazione/assemblaggio?\n"
    missing_text += (
        "\nIn alternativa, puoi specificare direttamente la distanza in km "
        "dal fornitore al sito di produzione."
    )
    return {
        "pending_feedback": missing_text,
        "thought_log": thought_log,
        "assumptions_list": assumptions,
        "current_lca_step": 2,
        "current_phase": "interview",
    }
```

> [!NOTE]
> **Nessuna tabella di distanze hardcoded.** La stima della distanza è responsabilità dell'LLM, che ragiona geograficamente (Step 6 del prompt). Il codice Python fa solo da guardrail nel caso in cui il modello ignori le istruzioni.

---

### 1.D — `agents/nodes.py` (`constraint_extractor`): Aggiornamento system prompt

Aggiornare il system prompt del `constraint_extractor` (righe 96-112) per estrarre i nuovi campi:

```python
SystemMessage(
    content=(
        "You are a product design analyst. Extract the '4 Pillars' "
        "(Dimensions, Mechanical Load, Usage Environment, Target Lifespan) "
        "from the product description, along with budget, aesthetics, "
        "structural requirements, and weight limit.\n\n"
        "GEOGRAPHY RULES:\n"
        "- 'geography': the PRODUCTION/ASSEMBLY location (where the product is made).\n"
        "- 'supplier_country': the ORIGIN of the main raw material (where it comes from).\n"
        "- 'destination_country': the DELIVERY destination (if different from geography).\n"
        "- If the user says 'acciaio dalla Cina, assemblato in Italia':\n"
        "    geography='Italy', supplier_country='China'\n"
        "- If the user says 'prodotto in Europa' with no material origin:\n"
        "    geography='Europe', supplier_country=None (to be asked)\n\n"
        "MASS RULE: Extract 'mass' ONLY if explicitly stated (e.g., '1 kg', '5 tonnes').\n"
        "Do NOT infer mass from product type.\n\n"
        "TASK TYPE: 'modeling' if user wants to calculate/model impact. "
        "'optimization' if user wants alternatives/improvements.\n\n"
        "Return ONLY fields explicitly stated or strongly implied. "
        "RESPOND EXCLUSIVELY IN ENGLISH."
    )
)
```

---

### 1.E — `agents/state.py`: Aggiunta campi

```python
interview_attempt_count: int   # Contatore dei round di intervista (per Task 3)
supplier_country: str          # Nazione del fornitore (origine trasporto)
destination_country: str       # Nazione di destinazione/assemblaggio
```

---

### 1.F — `agents/workflow_node.py`: Propagazione nuovi campi nel dict `logistics`

Aggiornare il dict `logistics` (righe 297-301) per includere i nuovi campi:

```python
logistics = {
    "geography": geography,            # Nazione di produzione
    "supplier_country": supplier_country or geography,  # Fallback: usa geography
    "destination_country": destination_country or geography,
    "distance_km": dist_km,
    "tkm": (mass / 1000.0) * dist_km,
}
```

---

## Criterio di Completamento Task 1

- [ ] Il sistema non procede al CSV lookup se `mass` è `None` e non è stata confermata dall'utente
- [ ] Il sistema non procede al CSV lookup se `geography` (nazione produzione) è `None`
- [ ] Il sistema non procede al CSV lookup se non ci sono dati logistici (`distance_km` o `supplier_country + destination_country`) e `is_material_only=False`
- [ ] `assumptions_list` non contiene mai "using default value 500 km" come prima voce
- [ ] Il prompt `semantic_ideation_api.yaml` non ha più "NO → default to RER" per geography
- [ ] `_build_result()` in `csv_lca_client.py` rimane invariato (non toccare in questo task)

---
---

# TASK 2 — Parsing Ecoinvent e Fallback Logistici

> **📖 Sezioni 2.1–2.5 = SOLO LETTURA (contesto e motivazione)**
> **📋 Sezioni 2.A–2.F = CODICE DA IMPLEMENTARE (copia nel file `data/csv_lca_client.py`)**

## Contesto Architetturale

### Il problema dei nomi ecoinvent con virgola

Il dataset ecoinvent usa una sintassi strutturata con virgole e pipe nei nomi di processo:

```
"market for steel, unalloyed | steel, unalloyed | Italy"
 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
 activity name         | product name | location
```

Il client attuale (`data/csv_lca_client.py`) tratta questo come una stringa piatta. La funzione `get_base_score` (righe 418-432) calcola la penalità di lunghezza contando **tutte le parole**, incluse quelle dopo la virgola:

```python
word_diff = len(text.split()) - len(term.split())
if word_diff > 0:
    score_base -= (0.15 * word_diff)   # -0.15 PER OGNI PAROLA EXTRA
```

Per `"market for steel, unalloyed | steel, unalloyed | Italy"`:
- `text.split()` → 9 parole
- `term.split()` = `"steel"` → 1 parola
- Penalità = 0.15 × 8 = **−1.2** → match scartato anche se è il dataset più rilevante

Il bonus `+0.3` per `"market for"` compensa parzialmente, ma non abbastanza per stringhe lunghe. Risultato: il client preferisce match con nomi brevi (spesso sottoprodotti o processi specifici) rispetto ai dataset "market for [material]" che sono i più corretti per l'LCA.

### Il problema del bonus "market for" indiscriminato

Righe 450-454:
```python
bonus_terms = ["market for", "production", "primary", "unalloyed", "low-alloyed"]
if any(term in name_combined for term in bonus_terms):
    score += 0.3
```

`name_combined = f"{out_name} {proc_name}"` = flowName + processName concatenati.

Un record come `"market for transport, freight, lorry | RER"` ha `"market for"` nel processname → prende `+0.3` bonus anche quando l'utente cerca un materiale, non un servizio di trasporto. Questo crea false promozioni.

### Il problema del `_SEMANTIC_SYNONYMS` incompleto

Il dizionario attuale (righe 97-117) non copre:
- Mezzi di trasporto: `"traghetto"` → `"ferry"`, `"nave"` → `"ship"`, `"aereo"` → `"aircraft"`
- Fibre: `"fibra di carbonio"` → `"carbon fiber"`, `"fibra di vetro"` → `"glass fiber"`
- Polimeri specifici: `"PLA"`, `"ABS"`, `"HDPE"` come sinonimo di polyethylene
- Cemento/calcestruzzo: `"calcestruzzo"` → `"concrete"`, `"cemento"` → `"cement"`
- Gomma: `"gomma"` → `"rubber"`, `"elastomero"` → `"elastomer"`
- Tessuti: `"cotone"` → `"cotton"`, `"lana"` → `"wool"`

### Il bug `exact_match_found` e `geo_level_used`

`nodes.py` righe 229-232 leggono:
```python
exact_str = "SI" if orig_match.get("exact_match_found") else "NO"
geo_used = orig_match.get("geo_level_used", "N/A")
```

Ma `_build_result()` (righe 472-485) non li popola mai:
```python
return {
    "index": ..., "id": ..., "providerName": ..., "flowName": ...,
    "location": ..., "environmental_impact": ...,
    "is_market": ..., "energy_mj": ..., "cost_per_kg": ...,
    "location_fallback_used": location_fallback_used,
    # ← exact_match_found e geo_level_used NON CI SONO
}
```
Risultato: `exact_str` è sempre `"NO"`, `geo_used` è sempre `"N/A"`.

### Il problema `is_market` fragile

Riga 481:
```python
"is_market": "market" in str(row["processname"]).lower(),
```

Cattura qualsiasi record con "market" nel processname, inclusi falsi positivi come:
- `"market gardening"` → `is_market=True` (falso positivo)
- `"supermarket electricity supply"` → `is_market=True` (falso positivo)

Il pattern corretto dovrebbe essere: `"market for"` come prefisso del process name.

### Il problema del filtro waste per task_type="optimization"

Le righe 374-388 filtrano SEMPRE i record waste/scrap, anche quando `task_type="optimization"` potrebbe voler confrontare materiali riciclati. Il filtro dovrebbe essere condizionale:

```python
if require_virgin and re.search(r"waste|scrap|scarto", name_combined):
    continue
# ← non c'è distinzione per task_type che include riciclato
```

---

## Obiettivo del Task 2

> Il client CSV deve:
> 1. Estrarre il nome del prodotto dalla struttura `"activity | product | location"` per lo scoring, non usare la stringa intera
> 2. Applicare il bonus "market for" solo ai record effettivamente ecoinvent "market for [material]"
> 3. Arricchire il dizionario sinonimi con trasporti, fibre, polimeri, materiali naturali
> 4. Popolare `exact_match_found` e `geo_level_used` in `_build_result()`
> 5. Rendere il filtro waste condizionale al task_type e alle preferenze esplicite dell'utente

---

## File Coinvolti

| File | Tipo modifica |
|------|--------------|
| `data/csv_lca_client.py` | Principale — parsing ecoinvent, scoring, `_build_result()`, sinonimi |

---

## Modifiche Dettagliate

### 2.A — Parsing strutturato ecoinvent

Aggiungere funzione helper prima della classe `CSVLcaClient`:

```python
def _parse_ecoinvent_name(raw: str) -> tuple[str, str, str]:
    """
    Parsa un nome ecoinvent nel formato:
      "activity name, attribute | product name | location"
    
    Restituisce (activity_core, product_name, location).
    
    Esempi:
      "market for steel, unalloyed | steel, unalloyed | Italy"
        → ("market for steel", "steel, unalloyed", "Italy")
      "steel production, electric, low-alloyed | steel, low-alloyed | Europe without Switzerland"
        → ("steel production", "steel, low-alloyed", "Europe without Switzerland")
      "polypropylene, granulate"  (formato senza pipe)
        → ("polypropylene", "polypropylene, granulate", "")
    """
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) >= 3:
        # Formato completo: activity | product | location
        activity_full = parts[0]
        product = parts[1]
        location = parts[2]
        # Estrai il nome core dell'attività (prima della virgola)
        activity_core = activity_full.split(",")[0].strip()
    elif len(parts) == 2:
        activity_core = parts[0].split(",")[0].strip()
        product = parts[1]
        location = ""
    else:
        # Formato semplice (no pipe)
        activity_core = raw.split(",")[0].strip()
        product = raw
        location = ""
    return activity_core, product, location
```

### 2.B — `get_base_score`: scoring su nome core, non stringa intera

Modificare `get_base_score` (righe 418-432) per usare il nome core estratto:

```python
def get_base_score(term: str, raw_text: str) -> float:
    """
    Calcola lo score di similarità tra 'term' (query) e 'raw_text' (nome CSV).
    
    Distingue tra:
    - flowName: usa il product name estratto (dopo il primo pipe, prima della virgola)
    - processName: usa l'activity core (prima della prima virgola o del primo pipe)
    
    La Length Penalty viene applicata solo sulle parole del nome core,
    NON sull'intera stringa (evita penalità per attributi tecnici dopo la virgola).
    """
    activity_core, product_name, _ = _parse_ecoinvent_name(raw_text)
    
    # Confronta term contro sia il product_name sia l'activity_core
    # (prendi il migliore dei due)
    best = 0.0
    for candidate in [activity_core.lower(), product_name.lower()]:
        if term == candidate:
            s = 1.0
        elif term in candidate.split():
            s = 0.85
        else:
            s = difflib.SequenceMatcher(None, term, candidate).ratio()
        
        # Length penalty: solo sulle parole del candidate (non della stringa intera)
        word_diff = len(candidate.split()) - len(term.split())
        if word_diff > 0:
            s -= (0.15 * word_diff)
        
        best = max(best, s)
    
    return best
```

### 2.C — Bonus "market for" discriminante

Sostituire le righe 450-454:

```python
# Bonus Industrial Quality — DISCRIMINANTE per "market for [material]"
# Il bonus si applica SOLO a record ecoinvent "market for X" dove X è
# il materiale cercato, NON a qualsiasi record con "market for".
is_market_for_material = (
    proc_name.startswith("market for")
    and any(term in proc_name for term in search_terms)
    and "transport" not in proc_name
    and "electricity" not in proc_name
    and "heat" not in proc_name
)

bonus_terms_non_market = ["production", "primary", "unalloyed", "low-alloyed"]

if is_market_for_material:
    score += 0.3
elif any(term in name_combined for term in bonus_terms_non_market):
    score += 0.2   # Bonus ridotto per altri indicatori di qualità
```

### 2.D — Arricchimento `_SEMANTIC_SYNONYMS`

Sostituire/estendere il dizionario (righe 97-117):

```python
_SEMANTIC_SYNONYMS: dict[str, list[str]] = {
    # Metalli
    "acciaio":    ["steel", "cast iron", "ferro"],
    "steel":      ["steel", "cast iron"],
    "alluminio":  ["aluminum", "aluminium", "alloy"],
    "aluminum":   ["aluminum", "aluminium", "alloy"],
    "aluminium":  ["aluminum", "aluminium", "alloy"],
    "rame":       ["copper"],
    "copper":     ["copper"],
    "ottone":     ["brass"],
    "brass":      ["brass"],
    "ferro":      ["iron", "cast iron", "steel"],
    "iron":       ["iron", "cast iron", "steel"],
    "titanio":    ["titanium"],
    "titanium":   ["titanium"],
    # Polimeri generici
    "plastica":   ["plastic", "polyethylene", "polypropylene", "pet", "hdpe", "ldpe"],
    "plastic":    ["plastic", "polyethylene", "polypropylene", "pet", "hdpe", "ldpe"],
    # Polimeri specifici
    "polipropilene": ["polypropylene", "pp"],
    "polietilene":   ["polyethylene", "hdpe", "ldpe", "pe"],
    "pla":           ["polylactic acid", "pla", "bioplastic"],
    "abs":           ["acrylonitrile butadiene styrene", "abs"],
    "hdpe":          ["polyethylene", "hdpe", "high density polyethylene"],
    "ldpe":          ["polyethylene", "ldpe", "low density polyethylene"],
    # Vetro / minerali
    "vetro":      ["glass", "silica"],
    "glass":      ["glass", "silica"],
    "calcestruzzo": ["concrete", "cement", "mortar"],
    "cemento":    ["cement", "concrete"],
    "concrete":   ["concrete", "cement"],
    "cement":     ["cement", "concrete"],
    # Legno / naturali
    "legno":      ["wood", "timber", "plywood", "mdf", "board"],
    "wood":       ["wood", "timber", "plywood", "mdf", "board"],
    # Fibre
    "fibra di carbonio": ["carbon fiber", "carbon fibre", "cfrp"],
    "carbon fiber":      ["carbon fiber", "carbon fibre"],
    "fibra di vetro":    ["glass fiber", "glass fibre", "gfrp", "fiberglass"],
    "glass fiber":       ["glass fiber", "glass fibre", "fiberglass"],
    # Gomma / elastomeri
    "gomma":      ["rubber", "elastomer", "natural rubber"],
    "rubber":     ["rubber", "elastomer", "natural rubber"],
    # Tessili
    "cotone":     ["cotton"],
    "cotton":     ["cotton"],
    "lana":       ["wool"],
    "wool":       ["wool"],
    # Trasporti (per Task 3)
    "traghetto":  ["ferry", "ship", "vessel"],
    "nave":       ["ship", "vessel", "ferry"],
    "ferry":      ["ferry", "ship", "vessel"],
    "aereo":      ["aircraft", "airplane", "air freight"],
    "aircraft":   ["aircraft", "airplane", "air freight"],
    "camion":     ["lorry", "truck", "freight"],
    "lorry":      ["lorry", "truck", "freight"],
}
```

### 2.E — Popola `exact_match_found` e `geo_level_used` in `_build_result()`

Aggiornare `_build_result()` (righe 472-485):

```python
def _build_result(
    self,
    row: pd.Series,
    location_fallback_used: bool,
    requested_location: str = "",
    pass_number: int = 1,
) -> dict:
    """Construct the result dict from a matched DataFrame row."""
    matched_location = str(row.get("location", "")).strip()
    exact_match_found = (
        matched_location.lower() == requested_location.lower()
        if requested_location else False
    )
    geo_level = "exact" if exact_match_found else (
        "regional" if "europe" in matched_location.lower() or "rer" in matched_location.lower()
        else "global" if matched_location.lower() in ("global", "glo")
        else "row" if matched_location.lower() in ("rest-of-world", "row")
        else "fallback"
    )
    return {
        "index":                 row.name + 2,
        "id":                    row["id"],
        "providerName":          row["processname"],
        "flowName":              row["outputname"],
        "location":              matched_location,
        "environmental_impact":  float(row["climatechangeimpact"]),
        "is_market":             str(row["processname"]).lower().strip().startswith("market for"),
        "energy_mj":             self._estimate_energy_mj(row),
        "cost_per_kg":           self._estimate_cost_per_kg(row),
        "location_fallback_used": location_fallback_used,
        "exact_match_found":     exact_match_found,      # ← FIX BUG
        "geo_level_used":        geo_level,              # ← FIX BUG
        "pass_number":           pass_number,            # 1=virgin-first, 2=standard fallback
    }
```

Aggiornare le chiamate a `_build_result()` in `find_closest_match()` per passare `requested_location` e `pass_number`.

### 2.F — Filtro waste condizionale

Nel metodo `_search_best_match()`, rendere il filtro waste sensibile al task_type:

```python
# task_type="optimization" + richiesta esplicita riciclato → permetti waste/recycled
user_wants_recycled = any(
    term in original_label for term in
    ["recycled", "riciclato", "recycling", "riciclo", "secondary", "secondario"]
)

if require_virgin and not user_wants_recycled:
    if re.search(r"\bwaste\b|\bscrap\b|\bscarto\b", name_combined):
        continue
elif not user_wants_recycled and task_type == "optimization":
    # In optimization mode senza richiesta riciclato: filtra waste nei metalli
    if is_metal and re.search(r"\bwaste\b|\bscrap\b|\bscarto\b", name_combined):
        continue
```

---

## Criterio di Completamento Task 2

- [ ] `"market for steel, unalloyed | steel, unalloyed | Italy"` ottiene score ≥ 0.85 per query `"steel"`
- [ ] `"market for transport, freight, lorry"` NON ottiene bonus `+0.3` quando la query è `"steel"`
- [ ] `_build_result()` popola sempre `exact_match_found` e `geo_level_used`
- [ ] `is_market` è `True` solo se processname inizia con `"market for"` (non substring generica)
- [ ] Il dizionario sinonimi include almeno: ferro→iron, traghetto→ferry, gomma→rubber, PLA→polylactic acid
- [ ] I test esistenti in `tests/` continuano a passare

---
---

# TASK 3 — Trasporto Dinamico e Logica Market-For

> **📖 Sezioni 3.1–3.3 = SOLO LETTURA (contesto e motivazione)**
> **📋 Sezioni 3.A–3.C = CODICE DA IMPLEMENTARE (copia in `agents/nodes.py` e `agents/workflow_node.py`)**

## Contesto Architetturale

### Il problema del trasporto fisso su RER

`agents/nodes.py` — `lca_validator` — riga 401:
```python
transport_match = provider.find_closest_match(
    target_product="transport, freight, lorry, unspecified",
    target_geography="RER"   # ← HARDCODED, SEMPRE Europa
)
```

Questo significa che:
- Se l'utente produce in Cina → usa impatto trasporto europeo (errato)
- Se l'utente usa una nave → usa impatto camion (errato per ordini di grandezza)
- Non esiste distinzione Euro 5 / Euro 6 / unspecified → preferisce il dataset "unspecified"

### Il problema della distanza default a 500 km (dopo Task 1)

Dopo il Task 1, la distanza non verrà più iniettata silenziosamente. Tuttavia serve una **logica di fallback progressiva** per i casi in cui l'utente rifiuta ripetutamente di rispondere:

- Tentativo 1: chiede fornitore/distanza
- Tentativo 2: chiede di nuovo con maggiore urgenza
- Dopo 2 fallimenti: applica 500 km con avviso chiaro (non silenzioso)

Questo richiede il campo `interview_attempt_count` aggiunto nel Task 1.

### La logica "Market For" per il trasporto

Nei dataset ecoinvent, i processi "market for [material]" **includono già il trasporto dal fornitore al mercato locale**. Se il BOM contiene almeno un materiale con `is_market=True`, l'aggiunta di una distanza di trasporto supplementare rischia il **double-counting**.

L'approccio corretto è:
- Se tutti i materiali del BOM sono "market for" → nessun trasporto supplementare aggiuntivo, **o** usa solo la distanza "ultimo miglio" (sito→cliente)
- Se alcuni materiali sono "market for" e altri no → trasporto solo per i non-market
- Se nessun materiale è "market for" → trasporto su tutta la distanza fornitore→destinazione

Questo è il campo `is_market` già presente ma non usato correttamente per questo scopo.

### Il problema dei mezzi di trasporto alternativi

L'attuale sistema supporta solo `"transport, freight, lorry"`. Se il prodotto viene spedito via nave (container shipping) o aereo, l'impatto è radicalmente diverso:
- Camion: ~0.05–0.15 kgCO2/tkm (a seconda del tipo Euro)
- Nave container: ~0.01–0.02 kgCO2/tkm (10× meno impattante del camion)
- Aereo: ~0.50–2.0 kgCO2/tkm (10–40× più impattante del camion)

---

## Obiettivo del Task 3

> 1. Il trasporto cerca il dataset nella nazione di **origine** del fornitore (non RER fisso)
> 2. La distanza di 500 km scatta SOLO dopo 2 tentativi falliti di intervista
> 3. Se tutti i materiali sono "market for", il trasporto non viene duplicato
> 4. Se l'utente menziona nave o aereo, il sistema cerca il dataset corretto

---

## File Coinvolti

| File | Tipo modifica |
|------|--------------|
| `agents/nodes.py` (`lca_validator`) | Trasporto dinamico, logica market-for |
| `agents/workflow_node.py` | Contatore tentativi, distanza ritardata |
| `agents/state.py` | Campo `interview_attempt_count` (già aggiunto in Task 1) |

---

## Modifiche Dettagliate

### 3.A — Trasporto dinamico in `lca_validator`

Sostituire le righe 399-411 con:

```python
# ── Ricerca dataset di trasporto (dinamica per mezzo e geografia) ────────────

# Determina il tipo di mezzo di trasporto dalle assunzioni o dall'input
user_input_lower = (state.get("user_input") or "").lower()
transport_mode = "lorry"  # default
if any(w in user_input_lower for w in ["nave", "ship", "container", "sea freight", "ferry", "traghetto"]):
    transport_mode = "ship"
elif any(w in user_input_lower for w in ["aereo", "aircraft", "air freight", "flight"]):
    transport_mode = "aircraft"

# Mappa del tipo di mezzo → query ecoinvent
_TRANSPORT_QUERIES = {
    "lorry":    "transport, freight, lorry, unspecified",
    "ship":     "transport, freight, sea, container ship",
    "aircraft": "transport, freight, aircraft, unspecified",
}
transport_query = _TRANSPORT_QUERIES.get(transport_mode, _TRANSPORT_QUERIES["lorry"])

# La nazione di ricerca è quella del FORNITORE (origine trasporto), non la destinazione
transport_geography = logistics.get("supplier_country") or geography or "RER"

thought_log.append(
    f"Ricerca servizio di trasporto nel DB: '{transport_query}' @ '{transport_geography}'"
)
transport_match = provider.find_closest_match(
    target_product=transport_query,
    target_geography=transport_geography
)

if transport_match and transport_match.get("environmental_impact") is not None:
    transport_impact_per_tkm = transport_match["environmental_impact"]
    transport_name = (
        transport_match.get("flowName", transport_query)
        + f" | {transport_match.get('location', transport_geography)}"
    )
    thought_log.append(
        f"Servizio trasporto trovato: {transport_name} "
        f"({transport_impact_per_tkm:.4f} kgCO2/tkm)"
    )
else:
    # Fallback per mezzo specifico (non solo per lorry)
    _TRANSPORT_FALLBACKS = {
        "lorry":    TRANSPORT_IMPACT_PER_TKM,   # 0.05
        "ship":     0.012,                       # Container shipping medio
        "aircraft": 0.800,                       # Air freight medio
    }
    transport_impact_per_tkm = _TRANSPORT_FALLBACKS.get(transport_mode, TRANSPORT_IMPACT_PER_TKM)
    transport_name = f"{transport_query} | {transport_geography} (Fallback)"
    transport_fallback_note = (
        f"Dataset trasporto '{transport_query}' non trovato per '{transport_geography}'. "
        f"Usato fallback: {transport_impact_per_tkm} kgCO2/tkm."
    )
    assumptions.append(transport_fallback_note)  # ← FIX: aggiunge a assumptions_list
    thought_log.append(transport_fallback_note)
```

### 3.B — Logica "Market For" migliorata per il trasporto

Aggiornare la sezione `has_market_material` (righe 387-396):

```python
# Conta componenti market vs non-market
market_components = [c for c in (state.get("bom") or []) if c.get("is_market", False)]
non_market_components = [c for c in (state.get("bom") or []) if not c.get("is_market", False)]
all_market = len(market_components) > 0 and len(non_market_components) == 0

if all_market:
    # TUTTI i materiali sono "market for" → il trasporto è già incluso
    # Aggiungere solo il tratto "ultimo miglio" (stimato 50 km) se non specificato
    if dist_km > 200:  # Se distanza > 200 km, probabilmente non è ultimo miglio
        market_assumption = (
            f"ATTENZIONE: Tutti i materiali usano dataset 'market for' che includono "
            f"già il trasporto al mercato locale. I {dist_km:.0f} km aggiuntivi "
            f"rappresentano il tratto aggiuntivo fornitore→sito non incluso nel dataset. "
            f"Se questa è la distanza totale, c'è rischio di double-counting."
        ) if not ita else (
            f"ATTENZIONE: Tutti i materiali usano dataset 'market for' che includono "
            f"già il trasporto. I {dist_km:.0f} km potrebbero causare double-counting."
        )
        assumptions.append(market_assumption)
        thought_log.append(market_assumption)
    total_tkm = sum(
        (c.get("weight_kg", 1.0) / 1000.0) * dist_km
        for c in non_market_components  # Solo componenti non-market (= 0 in questo caso)
    )
elif len(market_components) > 0:
    # Mix market + non-market: trasporto solo per i non-market
    market_assumption = (
        f"Componenti con dataset 'market for' ({', '.join(c.get('name','?') for c in market_components)}): "
        f"trasporto già incluso nel dataset. "
        f"Trasporto aggiuntivo calcolato solo per: "
        f"{', '.join(c.get('name','?') for c in non_market_components)}."
    )
    assumptions.append(market_assumption)
    total_tkm = sum(
        (c.get("weight_kg", 1.0) / 1000.0) * dist_km
        for c in non_market_components
    )
else:
    # Nessun materiale market for → trasporto su tutti i componenti
    total_tkm = sum(
        (c.get("weight_kg", 1.0) / 1000.0) * dist_km
        for c in (state.get("bom") or [])
    )
```

### 3.C — Contatore tentativi in `workflow_node.py`

Aggiornare la logica di blocco intervista (dopo Task 1):

```python
# Leggi il contatore dai tentativi precedenti
attempt_count = state.get("interview_attempt_count", 0)

if not is_interview_complete:
    attempt_count += 1
    
    if attempt_count >= 3:
        # Terzo tentativo fallito: applica 500 km e procedi con avviso
        dist_km = 500.0
        mass = result.total_mass_kg or 1.0
        thought_log.append(
            f"⚠ Intervista fallita dopo {attempt_count} tentativi. "
            f"Applicati valori di default: massa={mass}kg, distanza=500km."
        )
        assumptions.append(
            f"AVVISO: Dati logistici non forniti dopo {attempt_count} richieste. "
            f"Usati valori di default (massa={mass}kg, distanza=500km). "
            f"I risultati LCA potrebbero non essere accurati."
        )
        # NON fare return — procedi con i default
        is_interview_complete = True
    else:
        # Continua a chiedere
        missing_text = "Mi mancano alcune informazioni per poter procedere:\n"
        for q in result.interview_questions:
            missing_text += f"- {q}\n"
        return {
            "pending_feedback": missing_text,
            "thought_log": thought_log,
            "assumptions_list": assumptions,
            "current_lca_step": 2,
            "current_phase": "interview",
            "interview_attempt_count": attempt_count,
        }
```

---

## Criterio di Completamento Task 3

- [ ] `lca_validator` cerca il trasporto con `supplier_country` (non hardcoded "RER")
- [ ] Se `transport_mode = "ship"`, cerca `"transport, freight, sea, container ship"`
- [ ] Il fallback 500 km scatta solo dopo ≥ 3 round di intervista falliti
- [ ] Il fallback trasporto viene aggiunto a `assumptions_list` (non solo al thought log)
- [ ] Se tutti i materiali sono `is_market=True`, viene aggiunto warning double-counting
- [ ] I valori hardcoded in `config.py` per `TRANSPORT_IMPACT_PER_TKM` rimangono invariati (sono il fallback)

---
---

# TASK 4 — UI Narrativa e Correzione Granulare

> **📖 Sezioni 4.1–4.3 = SOLO LETTURA (contesto e motivazione)**
> **📋 Sezioni 4.A–4.D = CODICE DA IMPLEMENTARE (copia in `agents/nodes.py` e `agents/workflow_node.py`)**

## Contesto Architetturale

### Il problema dei thought log telegrafici

`agents/nodes.py` e `agents/workflow_node.py` generano log come:
```
"Esecuzione Workflow & Ideatore BOM (7 Passi)..."
"Passo 3: Selezione del materiale completata."
"Riga Excel trovata: 1234 - steel production | Italy - 2.31"
"Match esatto trovato: NO"
"Livello geografico utilizzato: N/A"
```

Questi sono tecnici e privi di narrativa. Un utente non tecnico non capisce cosa sta succedendo. L'obiettivo è renderli discorsivi, come se il sistema stesse "pensando ad alta voce" in prima persona.

### Il problema della correzione granulare in `human_feedback_processor`

`agents/nodes.py` — `human_feedback_processor` (righe 574-634):

Il sistema attuale, quando riceve feedback non-approval, chiede all'LLM di generare patch JSON:
```python
system_msg = (
    "You are a product design assistant. The user provided natural language feedback "
    "to modify the Bill of Materials or design constraints.\n\n"
    "Return ONLY valid JSON — no markdown, no explanations — with this structure:\n"
    ...
)
```

**Problema 1**: Il prompt non specifica che le modifiche devono essere **minimali**. L'LLM potrebbe riscrivere l'intera BOM invece di cambiare solo il campo richiesto.

**Problema 2**: Non c'è contesto sul feedback precedente. Se l'utente dice "non è una sedia, è un tavolo", il sistema non sa che ha già approvato i materiali.

**Problema 3**: `_APPROVE_TOKENS` (righe 537-543) non riconosce token con punteggiatura: `"ok."`, `"sì!"`, `"bene,"` non vengono riconosciuti perché il confronto non rimuove la punteggiatura.

```python
lower = feedback.lower()
if lower in _APPROVE_TOKENS or any(lower.startswith(t + " ") for t in _APPROVE_TOKENS):
```
`"ok."` → `lower = "ok."` → non è in `_APPROVE_TOKENS` → non viene riconosciuto come approvazione.

### Il bug Austria → Switzerland

`agents/workflow_node.py` — riga 312:
```python
assumptions = [a.replace("Austria", "Switzerland") for a in assumptions]
```
`agents/nodes.py` — riga 433:
```python
assumptions = [a.replace("Austria", "Switzerland") for a in assumptions]
```

Questi due `.replace()` sostituiscono ciecamente qualsiasi occorrenza di "Austria" nelle assunzioni. Se l'utente ha un fornitore in Austria, l'assunzione viene trasformata incorrettamente. Probabilmente è un artifact di sviluppo per mascherare un bug di fallback geografico (ecoinvent usa "Europe without Switzerland" non "Europe without Austria") e dovrebbe essere rimosso o sostituito con un commento esplicativo.

---

## Obiettivo del Task 4

> 1. Thought log narrativi e discorsivi (prima persona, lingua utente)
> 2. Correzione granulare: le patch JSON modificano solo i campi esplicitamente menzionati
> 3. `_APPROVE_TOKENS` riconosce token con punteggiatura
> 4. Rimozione del bug Austria→Switzerland con gestione esplicita del fallback ecoinvent
> 5. Integrazione dei fix `exact_match_found` e `geo_level_used` (se non già fatti in Task 2)

---

## File Coinvolti

| File | Tipo modifica |
|------|--------------|
| `agents/nodes.py` | Thought log narrativi, `_APPROVE_TOKENS` fix, `human_feedback_processor` prompt |
| `agents/workflow_node.py` | Thought log narrativi, rimozione bug Austria |
| `agents/nodes.py` | Rimozione bug Austria in `lca_validator` |

---

## Modifiche Dettagliate

### 4.A — Thought log narrativi

**Principio**: ogni log dovrebbe rispondere a "cosa sto facendo e perché".

Esempi di trasformazione:

| Prima | Dopo |
|-------|------|
| `"Esecuzione Workflow & Ideatore BOM (7 Passi)..."` | `"Ho ricevuto la descrizione del prodotto. Procedo con l'analisi delle 7 fasi per costruire il modello di ciclo di vita."` |
| `"Passo 3: Selezione del materiale completata."` | `"Ho identificato il materiale principale come {material} basandomi su {reason}. Procedo con la ricerca nel database ecoinvent."` |
| `"Riga Excel trovata: 1234 - steel production | Italy - 2.31"` | `"Trovato nel database: '{provider_name}' (riga {idx}), localizzazione: {location}. Impatto: {val_co2:.3f} kgCO2/kg."` |
| `"Match esatto trovato: NO"` | `"Non ho trovato un dataset esatto per '{geography}'. Utilizzo il proxy regionale più vicino: '{geo_used}'."` |
| `"Passo 6: Calcolo logistica (tkm)."` | `"Calcolo il contributo logistico: {mass:.2f} kg × {dist_km:.0f} km = {tkm:.3f} tkm di trasporto ({transport_mode})."` |

**Implementazione in `workflow_node.py`**:

Aggiornare i `thought_log.append()` in tutta la funzione `workflow_bom_ideator`:

```python
# Invece di:
thought_log.append("Executing Workflow & BOM Ideator (7 Steps)...")
# Usare:
thought_log.append(
    f"Ho ricevuto la descrizione: \"{state.get('user_input', '')[:60]}...\". "
    f"Avvio l'analisi in 7 fasi per costruire il modello LCA."
)

# Invece di:
thought_log.append(f"Step 5: BOM generated with {len(bom)} components.")
# Usare:
comp_names = ", ".join(c.get("name", "?") for c in bom[:3])
thought_log.append(
    f"La BOM è composta da {len(bom)} componente/i: {comp_names}"
    + (" e altri..." if len(bom) > 3 else ".")
    + f" Massa totale: {mass:.2f} kg."
)

# Invece di:
thought_log.append("Step 6: Logistics calculation (tkm).")
# Usare:
thought_log.append(
    f"Calcolo logistico: {mass:.2f} kg × {dist_km:.0f} km "
    f"= {(mass/1000.0*dist_km):.4f} tkm "
    f"({'stimati' if result.distance_km is None else 'dichiarati dall\\'utente'})."
)
```

### 4.B — `_APPROVE_TOKENS`: riconoscimento con punteggiatura

Sostituire la logica di confronto (righe 556, 570):

```python
import re

def _clean_token(text: str) -> str:
    """Rimuove punteggiatura finale e normalizza."""
    return re.sub(r"[.,!?;:]+$", "", text.strip().lower())

# ...
lower = _clean_token(feedback)  # invece di: lower = feedback.lower()

if lower in _APPROVE_TOKENS or any(lower.startswith(t + " ") for t in _APPROVE_TOKENS):
    # Approvazione riconosciuta
```

### 4.C — `human_feedback_processor`: prompt granulare

Sostituire il `system_msg` (righe 578-591) con:

```python
system_msg = (
    "You are a product design assistant helping refine a Bill of Materials (BOM) "
    "and design constraints.\n\n"
    "The user has provided corrective feedback in natural language. "
    "Your task is to generate MINIMAL, SURGICAL modifications — only change what the user explicitly mentioned.\n\n"
    "RULES:\n"
    "1. Do NOT modify fields the user did not mention.\n"
    "2. Do NOT regenerate the entire BOM — only patch the specific components/fields mentioned.\n"
    "3. If the user says 'it's a table not a chair', only update 'name' and related fields, "
    "   keep all materials, weights, and constraints unchanged.\n"
    "4. If the user mentions a specific material change (e.g. 'use steel instead of aluminum'), "
    "   only change the 'material' field for the named component.\n"
    "5. constraint_modifications must be EMPTY {} unless the user explicitly mentioned constraints.\n\n"
    "Return ONLY valid JSON with this structure (no markdown, no explanation):\n"
    "{\n"
    "  \"bom_modifications\": [\n"
    "    {\"component_name\": \"<exact component name>\", "
    "\"field\": \"material|weight_kg|name|functional_role\", \"new_value\": \"<value>\"}\n"
    "  ],\n"
    "  \"constraint_modifications\": {\"<key>\": \"<value>\"},\n"
    "  \"thought\": \"Brief explanation in the user's language of exactly what changed and why\"\n"
    "}\n"
    "Use empty arrays/objects when there are no modifications for that category. "
    "RESPOND IN THE SAME LANGUAGE AS THE USER FEEDBACK."
)

user_msg = (
    f"Current BOM:\n{json.dumps(state.get('bom', []), indent=2)}\n\n"
    f"Current Constraints:\n{json.dumps(state.get('constraints', {}), indent=2)}\n\n"
    f"User Feedback: \"{feedback}\"\n\n"
    f"Remember: make ONLY the changes explicitly requested. "
    f"Do NOT change anything the user did not mention."
)
```

### 4.D — Rimozione bug Austria → Switzerland

Rimuovere le seguenti righe (o sostituire con un commento esplicativo):

In `agents/workflow_node.py` — riga 312:
```python
# RIMUOVERE:
assumptions = [a.replace("Austria", "Switzerland") for a in assumptions]

# SOSTITUIRE CON (se necessario spiegare il fallback ecoinvent):
# Nota: ecoinvent usa "Europe without Switzerland" come codice regionale europeo.
# Nessuna sostituzione automatica di nomi di paesi nelle assunzioni.
```

In `agents/nodes.py` (`lca_validator`) — riga 433:
```python
# RIMUOVERE:
assumptions = [a.replace("Austria", "Switzerland") for a in assumptions]
```

---

## Criterio di Completamento Task 4

- [ ] I thought log sono in prima persona e descrivono il ragionamento (non solo l'azione)
- [ ] `"ok."`, `"sì!"`, `"bene,"` vengono riconosciuti come token di approvazione
- [ ] Il prompt di `human_feedback_processor` contiene la regola "MINIMAL, SURGICAL modifications"
- [ ] Il bug Austria→Switzerland è rimosso da entrambi i file
- [ ] Se l'utente dice "non è una sedia, è un tavolo", solo il campo `name` viene aggiornato (non tutta la BOM)

---
---

## Riepilogo Modifiche per File

| File | Task | Tipo |
|------|------|------|
| `agents/schemas.py` | T1 | Aggiunta campi `supplier_country`, `destination_country` |
| `agents/state.py` | T1 | Aggiunta `interview_attempt_count`, `supplier_country`, `destination_country` |
| `prompts/semantic_ideation_api.yaml` | T1 | Riscrittura Step 6, Step 7 |
| `agents/workflow_node.py` | T1, T3, T4 | Rimozione default, contatore, log narrativi, Austria fix |
| `agents/nodes.py` (`constraint_extractor`) | T1 | System prompt aggiornato |
| `data/csv_lca_client.py` | T2 | Parsing ecoinvent, scoring, `_build_result()`, sinonimi |
| `agents/nodes.py` (`lca_validator`) | T3 | Trasporto dinamico, market-for logic |
| `agents/nodes.py` (`human_feedback_processor`) | T4 | Prompt granulare, `_APPROVE_TOKENS` fix |
| `agents/nodes.py` (tutti) | T4 | Thought log narrativi, Austria fix |

---

## Dipendenze tra Task

```
Task 1 (Scudo dei dati)
  │  ← introduce: supplier_country, destination_country, interview_attempt_count
  │
Task 2 (CSV parsing)
  │  ← introduce: exact_match_found, geo_level_used (fix bug usato in T3)
  │                is_market fix (usato in T3 per logica double-counting)
  │
Task 3 (Trasporto dinamico)
  │  ← dipende da: supplier_country (T1), is_market fix (T2)
  │
Task 4 (UI narrativa)
     ← indipendente dagli altri, ma i bug fix (Austria) si applicano a file già modificati
```

> [!WARNING]
> Se Task 2 non è completato prima di Task 3, la ricerca del trasporto con `supplier_country` funzionerà ma i campi `exact_match_found` e `geo_level_used` saranno ancora assenti dai risultati.

---

## Open Questions

> [!IMPORTANT]
> Prima di eseguire Task 1, confermare:
> 1. **Materiale grezzo (`is_material_only=True`)**: Dobbiamo chiedere comunque `supplier_country`? O per i materiali grezzi la logistica è fuori scope?
> 2. **Numero massimo di tentativi intervista**: Task 3 propone 3 tentativi prima del fallback. Preferisci 2 (più aggressivo) o infinito (mai fallback automatico)?
> 3. **Distanza stimata dalla tabella**: La tabella `_DISTANCE_TABLE` in Task 1 copre ~15 coppie di paesi. Vuoi che aggiungiamo altre coppie, o che usiamo un'API di geocoding come fallback?
> 4. **Thought log lingua**: I log narrativi del Task 4 devono seguire la lingua dell'utente (italiano/inglese via `is_italian()`)? Attualmente lo fanno già parzialmente.
