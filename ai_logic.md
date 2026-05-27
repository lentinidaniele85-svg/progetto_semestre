# Logica AI — Sustainable Product Optimization Agent

Il sistema implementa un **Agente Neuro-Simbolico** per l'analisi LCA (Life Cycle Assessment) di prodotti industriali.

---

## 1. Paradigma Neuro-Simbolico

Il sistema divide le responsabilità tra due motori complementari:

| Motore | Tecnologia | Responsabilità |
|--------|-----------|---------------|
| **Neurale** | LLM (OpenRouter) | Comprensione testo, estrazione vincoli, generazione BOM, inferenza materiali |
| **Simbolico** | Python deterministico | Calcoli LCA, fuzzy matching DB, regole market for, filtri waste, calcolo tkm |

> **Regola d'Oro:** L'LLM non ha mai l'autorità di inventare numeri di impatto ambientale. È confinato all'ideazione; Python verifica i dati con il dataset ecoinvent locale (`DataSet.xlsx`).

---

## 2. Il Flusso Logico a 7 Passi (System Prompt)

L'agente segue la pipeline definita nel prompt di sistema:

| Passo | Nome | Descrizione |
|-------|------|-------------|
| 1 | **Analisi Entità** | Materiale grezzo vs prodotto complesso (`is_material_only`) |
| 2 | **Lookup Aggregato** | Verifica se esiste già un dataset ecoinvent per il prodotto intero |
| 3 | **Selezione Materiale** | Inferenza LLM per esclusione tecnica; ogni inferenza va in `assumptions_list` |
| 4 | **Vincolo Geometrico** | Geometria → processo manifatturiero (mapping deterministico Python) |
| 5 | **Scomposizione BOM** | Generazione distinta base per componenti |
| 6 | **Calcolo Logistica** | `tkm = (massa_kg / 1000) × distanza_km`; anti double-counting market |
| 7 | **Gap Analysis** | Controllo dati mancanti → interview o assunzioni autonome |

---

## 3. Regole di Dataset: market for vs production

La scelta tra dataset `market for [material]` e `[material] production` è deterministica, basata su `has_transport`:

```
has_transport = (dist_km is not None and dist_km > 0)
```

| Situazione | has_transport | Dataset scelto |
|------------|--------------|----------------|
| Nessuna distanza specificata | `False` | `market for [material]` (include logistica media) |
| Distanza esplicita (es. 800 km) | `True` | `[material] production` (+ trasporto separato) |
| `dist_km = None` | `False` | `market for [material]` |

**Razionale (da System Prompt):** Il dataset `market for` include già una quota di trasporto medio al punto di consegna. Se si aggiunge una distanza esplicita questa è un tratto *aggiuntivo e specifico*, non coperto dal dataset di mercato.

**Implementazione:** `csv_lca_client.py → _search_best_match()`:
- `is_market_for_material` and `has_transport=False` → bonus `+0.3` sullo score
- `is_market_for_material` and `has_transport=True` → penalità `−0.3` sullo score
- `[material] production` and `has_transport=True` → bonus `+0.4`

---

## 4. Filtro Waste Assoluto

Il sistema **non restituisce mai** dataset con `waste`, `scrap` o `scarto` nel nome (a meno che l'utente non chieda esplicitamente materiali riciclati).

```python
if re.search(r"\bwaste\b|\bscrap\b|\bscarto\b", name_combined):
    continue  # scartato prima del calcolo score
```

Verificato su 27 combinazioni (3 materiali × 3 geografie × 3 valori `has_transport`) — 0 eccezioni.

---

## 5. Interview Flow (Passo 7 — Gap Analysis)

L'agente gestisce i dati mancanti con una logica a due tentativi:

### 1° Tentativo (`attempt_count == 0`)
Se mancano: **massa**, **luogo** o **distanza** (solo per prodotti, non materiali puri) → il sistema chiede all'utente tramite `pending_feedback`:

```
"Mancano alcune informazioni importanti: massa, luogo (geografia),
 distanza di trasporto (km). Puoi fornirle?"
```

- `current_phase = "interview"`
- Il grafo si interrompe (HITL — Human In The Loop)
- La risposta dell'utente viene concatenata a `user_input` da `human_feedback_processor`

### 2° Tentativo (`attempt_count == 1`)
Se i dati mancano ancora → **assunzioni autonome** (non si blocca più):

| Campo mancante | Assunzione |
|---------------|-----------|
| Massa | `1.0 kg` |
| Luogo | `RER` (Europa) |
| Distanza | nessun default — `has_transport=False` → usa `market for` |

> **Nota:** La distanza non ha un default numerico perché il sistema non può inventare chilometri. Invece, l'assenza di distanza è gestita implicitamente con `market for`.

### Materiali puri (`is_material_only=True`)
Per i materiali puri la distanza **non viene mai chiesta** (passi 2-6 del system prompt non si applicano).

---

## 6. Ricerca nel DB — Logica Multi-Stadio

`csv_lca_client.find_closest_match()` opera in tre stadi:

1. **Espansione Semantica:** il termine di ricerca viene espanso con sinonimi industriali (es. `acciaio` → `["steel", "cast iron"]`)
2. **Fuzzy Match con Filtro Dinamico:** difflib + filtri (waste, metallo/plastica, penalty prodotti finiti)
3. **Fallback Geografico:** gerarchia `[location] → RER → GLO → RoW`

**Pass 1 (soglia 0.85):** solo materiali "virgin" (no waste/scrap)  
**Pass 2 (soglia 0.70):** fallback standard se non esiste materiale vergine

---

## 7. Calcolo LCA Deterministico

Formula in `agents/nodes.py → lca_validator()`:

```
Impatto_Totale = (Impatto_Materiale × Massa_kg)
               + (Impatto_Processo × Massa_kg)
               + (tkm × Impatto_Trasporto_per_tkm)
```

**Anti double-counting logistico:**
- Componenti con dataset `market for` → trasporto già incluso nel dataset → nessun tkm aggiuntivo (o solo per il tratto extra)
- Mix market/non-market → tkm calcolato solo per i componenti non-market

---

## 8. MCDA (Multi-Criteria Decision Analysis)

Dopo la generazione delle alternative (`material_ideator`), l'algoritmo MCDA calcola:

```
score = Δ_CO2 × w_co2 + Δ_costo × w_cost + Δ_energia × w_energy
```

I pesi (`w_co2`, `w_cost`, `w_energy`) sono configurabili in `core/config.py`. L'alternativa con score più alto viene presentata come "migliore scelta bilanciata".
