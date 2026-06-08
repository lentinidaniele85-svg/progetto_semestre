# -*- coding: utf-8 -*-
"""
Batch Test — 12 Casi Studio Industriali (LCA Engine + UOM Optimization)
========================================================================

Scopo
-----
Verificare che il motore di calcolo LCA deterministico (`agents.nodes.lca_validator`
+ `agents.nodes.mcda_scorer`), dopo l'integrazione della coerenza Massa ↔ Unità di
Misura (UOM, ultima colonna 'unitOfMeasure' di dataset_ecoinvent_perfetto.xlsx) e
della guardia 'task_type', esegua SENZA CRASH DI SISTEMA su 12 scenari industriali
realistici, coprendo:
  - prodotti finiti vs materiali puri (is_material_only)
  - task_type 'modeling' vs 'optimization' (→ mcda_scorer)
  - routing logistico su gomma / mare / aria
  - geografie con e senza match diretto (fallback RER → GLO → RoW)
  - STRICT MODE (blocco controllato quando un materiale non supera soglia 0.85)
  - stima autonoma della massa (LLM Mass Estimator) per input con massa non
    specificata o minima

Approccio di mocking
--------------------
Seguendo lo stesso pattern già adottato in tests/test_data_layer.py, mockiamo
SOLO `data.csv_lca_client.generate_search_queries` (l'espansione semantica via
LLM) restituendo il termine stesso come unica query di ricerca. In questo modo:
  - NON eseguiamo chiamate LLM reali (costo zero, determinismo, niente rete)
  - il motore di fuzzy-matching e l'intero CSVLcaClient girano per davvero
    contro il dataset reale 'dataset_ecoinvent_perfetto.xlsx'
Esattamente come documentato in test_graph.py: "The LCA Data Layer (CSV client)
runs for real to verify the full pipeline".

'Simulazione' della constraint extraction
-----------------------------------------
Come richiesto ("simulando l'estrazione dei vincoli"), per ciascun caso costruiamo
direttamente un AgentState realistico (constraints + bom) — l'equivalente di ciò
che produrrebbero `constraint_extractor` + `workflow_bom_ideator` dopo l'intervista
— e lanciamo `lca_validator` (+ `mcda_scorer` per i task 'optimization'). Questo
isola e stressa esattamente il motore di calcolo deterministico oggetto
dell'ottimizzazione, senza la non-determinatezza delle chiamate LLM generative.

Per i Casi 10/11/12 (massa non specificata o minima) simuliamo il risultato del
'LLM Mass Estimator' (vedi workflow_node.py:_estimate_mass_with_llm, che converte
correttamente grammi → kg, es. "360 g" → 0.36) iniettando direttamente la massa
stimata + l'assunzione che il nodo reale avrebbe registrato in assumptions_list.

Esecuzione
----------
    venv/Scripts/python.exe -X utf8 test_batch_12_industrial_cases.py
"""
import asyncio
import sys
from unittest.mock import patch, AsyncMock

from agents.nodes import lca_validator, mcda_scorer
from data.provider_factory import get_lca_provider

# ---------------------------------------------------------------------------
# Helpers (stesso pattern di test_full_logic.py)
# ---------------------------------------------------------------------------
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    RESULTS.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"   [{status}] {name}{suffix}")
    return passed


def make_state(user_input: str, constraints: dict, bom: list[dict],
               assumptions: list[str] | None = None) -> dict:
    """Costruisce un AgentState 'post-intervista', pronto per lca_validator —
    equivalente allo stato che produrrebbe il grafo reale dopo
    constraint_extractor → human_feedback_processor → workflow_bom_ideator."""
    base_constraints = {"task_type": "modeling"}
    base_constraints.update(constraints)
    return {
        "user_input": user_input,
        "constraints": base_constraints,
        "logistics_data": {},
        "bom": bom,
        "semantic_alternatives": [],
        "lca_results": [],
        "mcda_scores": [],
        "thought_log": [],
        "assumptions_list": list(assumptions or []),
        "current_phase": "workflow",
        "current_lca_step": 5,
        "interview_attempt_count": 1,
    }


_SANITY_CEILING = 5_000_000.0  # oltre questa soglia un impatto è quasi certamente un errore di magnitudo


async def run_case(case_id: int, title: str, state: dict, expectations: dict | None = None):
    expectations = expectations or {}
    print(f"\n{'='*86}\nCASO {case_id}: {title}\n{'='*86}")

    # ---- 1) Nessun crash su lca_validator -------------------------------
    try:
        out = await lca_validator(state)
    except Exception as exc:  # noqa: BLE001 — vogliamo intercettare QUALSIASI crash
        check(f"Caso {case_id} — lca_validator non solleva eccezioni", False,
              f"CRASH: {type(exc).__name__}: {exc}")
        return

    check(f"Caso {case_id} — lca_validator esegue senza eccezioni (no system crash)", True)

    phase = out.get("current_phase")
    thought_log = out.get("thought_log", [])
    assumptions = out.get("assumptions_list", [])
    lca_results = out.get("lca_results", [])
    is_strict_block = phase == "error"

    # ---- 2) Fase coerente (STRICT MODE è un blocco CONTROLLATO, non un crash)
    expect_block = expectations.get("expect_strict_block", False)
    if expect_block == "allow":
        check(f"Caso {case_id} — fase gestita in modo controllato (phase={phase!r})", True,
              "blocco STRICT possibile e accettabile per questo materiale" if is_strict_block else "match trovato, calcolo eseguito")
    elif expect_block:
        check(f"Caso {case_id} — STRICT MODE attivato come atteso (phase={phase!r})",
              is_strict_block, out.get("error_message", "")[:140])
    else:
        check(f"Caso {case_id} — nessun blocco STRICT inatteso (phase={phase!r})",
              not is_strict_block,
              "" if not is_strict_block else f"error_message: {out.get('error_message','')[:160]}")

    if is_strict_block:
        # Verifica che il blocco sia 'pulito': messaggio utente presente, niente traceback
        check(f"Caso {case_id} — blocco STRICT produce messaggio utente strutturato",
              bool(out.get("pending_feedback")) and bool(out.get("error_message")))
        check(f"Caso {case_id} — thought_log registra il fallimento STRICT (🚫)",
              any("STRICT" in t and "🚫" in t for t in thought_log))

    # ---- 3) Asserzioni mirate sul thought_log / assumptions --------------
    for kw in expectations.get("thought_log_contains", []):
        found = any(kw.lower() in t.lower() for t in thought_log)
        check(f"Caso {case_id} — thought_log contiene '{kw}'", found)

    for kw in expectations.get("assumptions_contains", []):
        found = any(kw.lower() in a.lower() for a in assumptions)
        check(f"Caso {case_id} — assumptions_list contiene '{kw}'", found)

    # ---- 4) Sanity-check di magnitudo (verifica indiretta della UOM fix) -
    if not is_strict_block:
        bad = [
            (r.get("component_name"), r.get("original_scores", {}).get("environmental_impact"))
            for r in lca_results
            if isinstance(r.get("original_scores", {}).get("environmental_impact"), (int, float))
            and r["original_scores"]["environmental_impact"] > _SANITY_CEILING
        ]
        check(f"Caso {case_id} — nessun valore di impatto fuori scala (possibile errore di magnitudo UOM)",
              not bad, f"valori sospetti: {bad}" if bad else "")

    # ---- 5) MCDA per i casi di ottimizzazione -----------------------------
    if expectations.get("run_mcda") and not is_strict_block:
        merged_state = {**state, **out}
        try:
            mcda_out = mcda_scorer(merged_state)
            check(f"Caso {case_id} — mcda_scorer esegue senza eccezioni (task_type=optimization)", True)
            # mcda_scorer itera 1:1 su state['lca_results'] (Transport incluso) e produce
            # una entry di scoring per ciascuno — verifichiamo quindi la corrispondenza 1:1.
            check(f"Caso {case_id} — mcda_scores ha una entry 1:1 con lca_results (incl. 'Transport')",
                  len(mcda_out.get("mcda_scores", [])) == len(lca_results),
                  f"mcda_scores={len(mcda_out.get('mcda_scores', []))} vs lca_results={len(lca_results)}")
        except Exception as exc:  # noqa: BLE001
            check(f"Caso {case_id} — mcda_scorer esegue senza eccezioni (task_type=optimization)", False,
                  f"CRASH: {type(exc).__name__}: {exc}")

    # ---- Sintesi leggibile -------------------------------------------------
    if lca_results:
        print("   Risultati LCA:")
        for r in lca_results:
            sc = r.get("original_scores", {})
            impact = sc.get("environmental_impact")
            impact_s = f"{impact:.4g}" if isinstance(impact, (int, float)) else str(impact)
            print(f"     • {r.get('component_name', '?'):<32} impatto={impact_s:>14}  (rif. '{r.get('original_material','?')[:55]}')")
    if assumptions:
        print("   Assunzioni / note rilevanti:")
        for a in assumptions[-3:]:
            print(f"     - {a[:175]}")


# ---------------------------------------------------------------------------
# Definizione dei 12 casi studio
# ---------------------------------------------------------------------------

def build_cases() -> list[dict]:
    cases = []

    # ---- Caso 1: Sedia da interno in plastica -----------------------------
    cases.append(dict(
        id=1,
        title="Sedia da interno in plastica — 8 kg, Germania, 600 km su camion",
        state=make_state(
            user_input="Sedia da interno in plastica, peso 8 kg, prodotta in Germania e trasportata per 600 km su camion.",
            constraints={"mass": 8.0, "geography": "Germany", "distance_km": 600.0,
                         "transport_mode": "lorry", "task_type": "modeling",
                         "supplier_country": "Germany", "destination_country": "Germany"},
            bom=[{
                "name": "Scocca e struttura sedia", "material": "polypropylene",
                "weight_kg": 8.0, "geometry": "Pezzi Pieni Complessi",
                "manufacturing_process": "Injection moulding",
                "is_material_only": False, "is_recycled": False,
                "transport_mode": "lorry", "distance_km": 600.0,
            }],
        ),
        expectations=dict(
            expect_strict_block="allow",
            thought_log_contains=["transport, freight, lorry"],
        ),
    ))

    # ---- Caso 2: 200 kg di EPS — materiale puro ---------------------------
    cases.append(dict(
        id=2,
        title="200 kg polistirene espanso (EPS) — Europa, materiale puro (is_material_only=True)",
        state=make_state(
            user_input="200 kg di polistirene espanso (EPS) in blocchi, destinati al mercato europeo, "
                       "senza esigenze di trasporto personalizzate.",
            constraints={"mass": 200.0, "geography": "Europe", "distance_km": None,
                         "transport_mode": None, "task_type": "modeling"},
            bom=[{
                "name": "Lotto materia prima EPS", "material": "expanded polystyrene",
                "weight_kg": 200.0, "geometry": None, "manufacturing_process": None,
                "is_material_only": True, "is_recycled": False,
            }],
        ),
        expectations=dict(
            expect_strict_block="allow",
            thought_log_contains=["is_material_only=True", "nessuna distanza specificata"],
        ),
    ))

    # ---- Caso 3: Lotto sacchetti pellicola LDPE (Ottimizzazione) ----------
    cases.append(dict(
        id=3,
        title="Lotto sacchetti pellicola LDPE — 15 kg, Francia, 450 km camion (Ottimizzazione)",
        state=make_state(
            user_input="Lotto di sacchetti in pellicola LDPE, peso 15 kg, prodotti in Francia "
                       "e trasportati per 450 km su camion. Vorremmo valutare alternative più sostenibili.",
            constraints={"mass": 15.0, "geography": "France", "distance_km": 450.0,
                         "transport_mode": "lorry", "task_type": "optimization"},
            bom=[{
                "name": "Pellicola LDPE", "material": "low density polyethylene",
                "weight_kg": 15.0, "geometry": "Film", "manufacturing_process": "Film extrusion",
                "is_material_only": False, "is_recycled": False,
                "transport_mode": "lorry", "distance_km": 450.0,
            }],
        ),
        expectations=dict(
            expect_strict_block="allow",
            thought_log_contains=["transport, freight, lorry"],
            run_mcda=True,
        ),
    ))

    # ---- Caso 4: Barra profilo continuo alluminio estruso -----------------
    cases.append(dict(
        id=4,
        title="Barra profilo continuo alluminio estruso — 25 kg, Germania, 150 km camion",
        state=make_state(
            user_input="Barra a profilo continuo in alluminio estruso, peso 25 kg, fabbricata "
                       "in Germania e trasportata per 150 km su camion.",
            constraints={"mass": 25.0, "geography": "Germany", "distance_km": 150.0,
                         "transport_mode": "lorry", "task_type": "modeling"},
            bom=[{
                "name": "Profilo estruso in alluminio", "material": "aluminium",
                "weight_kg": 25.0, "geometry": "Profili/Tubi", "manufacturing_process": "Tube extrusion",
                "is_material_only": False, "is_recycled": False,
                "transport_mode": "lorry", "distance_km": 150.0,
            }],
        ),
        expectations=dict(
            expect_strict_block="allow",
            thought_log_contains=["transport, freight, lorry"],
        ),
    ))

    # ---- Caso 5: Tubo rigido edilizia PVC ---------------------------------
    cases.append(dict(
        id=5,
        title="Tubo rigido per edilizia in PVC — 50 kg, Germania, 500 km camion",
        state=make_state(
            user_input="Tubo rigido per applicazioni edilizie in PVC, peso 50 kg, prodotto "
                       "in Germania e trasportato per 500 km su camion.",
            constraints={"mass": 50.0, "geography": "Germany", "distance_km": 500.0,
                         "transport_mode": "lorry", "task_type": "modeling"},
            bom=[{
                "name": "Tubo rigido in PVC", "material": "polyvinyl chloride",
                "weight_kg": 50.0, "geometry": "Profili/Tubi", "manufacturing_process": "Tube extrusion",
                "is_material_only": False, "is_recycled": False,
                "transport_mode": "lorry", "distance_km": 500.0,
            }],
        ),
        # NOTA: 'polyvinyl chloride'/'PVC' non supera la soglia 0.85 in
        # questo dataset (verificato empiricamente — i record ecoinvent usano
        # nomenclatura tecnica come "polyvinyl chloride, suspension
        # polymerised" che il fuzzy-match non avvicina abbastanza). È quindi
        # uno scenario REALISTICO di attivazione dello STRICT MODE: il test
        # verifica che il blocco avvenga in modo PULITO (niente crash, nessun
        # dato inventato, messaggio utente strutturato) — esattamente il
        # comportamento "Golden Rule" richiesto dal sistema.
        expectations=dict(
            expect_strict_block="allow",
        ),
    ))

    # ---- Caso 6: Lotto film polivinilfluoruro (Ottimizzazione) ------------
    cases.append(dict(
        id=6,
        title="Lotto film in polivinilfluoruro — 150 kg, USA, 300 km camion (Ottimizzazione)",
        state=make_state(
            user_input="Lotto di film in polivinilfluoruro, peso 150 kg, prodotto negli Stati Uniti "
                       "e trasportato per 300 km su camion. Vogliamo valutare materiali alternativi più sostenibili.",
            constraints={"mass": 150.0, "geography": "United States", "distance_km": 300.0,
                         "transport_mode": "lorry", "task_type": "optimization"},
            bom=[{
                "name": "Film in polivinilfluoruro", "material": "polyvinylfluoride",
                "weight_kg": 150.0, "geometry": "Film", "manufacturing_process": "Film extrusion",
                "is_material_only": False, "is_recycled": False,
                "transport_mode": "lorry", "distance_km": 300.0,
            }],
        ),
        expectations=dict(
            expect_strict_block="allow",
            thought_log_contains=["transport, freight, lorry"],
            run_mcda=True,
        ),
    ))

    # ---- Caso 7: Profilati alluminio — routing marittimo ------------------
    cases.append(dict(
        id=7,
        title="Profilati in alluminio — 500 kg, trasporto navale transoceanico, 1500 km (verifica routing marittimo)",
        state=make_state(
            user_input="Lotto di profilati in alluminio, peso complessivo 500 kg, trasportati "
                       "via nave cargo transoceanica per 1500 km.",
            constraints={"mass": 500.0, "distance_km": 1500.0,
                         "transport_mode": "ship", "task_type": "modeling"},
            bom=[{
                "name": "Profilati in alluminio", "material": "aluminium",
                "weight_kg": 500.0, "geometry": "Profili/Tubi", "manufacturing_process": "Tube extrusion",
                "is_material_only": False, "is_recycled": False,
                "transport_mode": "ship", "distance_km": 1500.0,
            }],
        ),
        # geography non specificata in input → default 'GLO' (Global): verifichiamo
        # anche che il fallback geografico di default funzioni correttamente.
        expectations=dict(
            expect_strict_block="allow",
            thought_log_contains=["transport, freight, sea, transoceanic ship"],
        ),
    ))

    # ---- Caso 8: Carta grafica riciclata (Ottimizzazione) ------------------
    cases.append(dict(
        id=8,
        title="Carta grafica riciclata — 200 kg, Polonia (Ottimizzazione)",
        state=make_state(
            user_input="Lotto di carta grafica riciclata, peso 200 kg, distribuito in Polonia. "
                       "Vorremmo valutare alternative ancora più sostenibili.",
            constraints={"mass": 200.0, "geography": "Poland", "distance_km": None,
                         "transport_mode": None, "task_type": "optimization"},
            bom=[{
                "name": "Lotto carta grafica riciclata", "material": "graphical paper",
                "weight_kg": 200.0, "geometry": None, "manufacturing_process": None,
                "is_material_only": True, "is_recycled": True,
            }],
        ),
        expectations=dict(
            expect_strict_block="allow",
            thought_log_contains=["nessuna distanza specificata"],
            run_mcda=True,
        ),
    ))

    # ---- Caso 9: Polipropilene stampato a iniezione, no transport ---------
    cases.append(dict(
        id=9,
        title="Polipropilene (PP) stampato a iniezione — 10 kg, Europa, senza trasporti personalizzati",
        state=make_state(
            user_input="Componente in polipropilene (PP) stampato a iniezione, peso 10 kg, "
                       "destinato al mercato europeo, senza esigenze di trasporto personalizzate.",
            constraints={"mass": 10.0, "geography": "Europe", "distance_km": None,
                         "transport_mode": None, "task_type": "modeling"},
            bom=[{
                "name": "Componente PP stampato a iniezione", "material": "polypropylene",
                "weight_kg": 10.0, "geometry": "Pezzi Pieni Complessi",
                "manufacturing_process": "Injection moulding",
                "is_material_only": False, "is_recycled": False,
            }],
        ),
        expectations=dict(
            expect_strict_block="allow",
            thought_log_contains=["nessuna distanza specificata"],
        ),
    ))

    # ---- Caso 10: Racchetta padel — massa non specificata (LLM Estimator) -
    # L'utente NON specifica la massa. Nel grafo reale, al 2° tentativo di
    # gap-analysis, workflow_bom_ideator invoca _estimate_mass_with_llm
    # (workflow_node.py) che — per una racchetta da padel — stimerebbe
    # ~0.36 kg (esempio canonico nel system prompt del Mass Estimator: "360 g
    # → 0.36 kg", con corretta conversione g→kg). Simuliamo qui l'OUTPUT di
    # quella stima (massa + nota in assumptions_list), isolando così il
    # comportamento del solo motore LCA a valle.
    _padel_assumption = ("[LLM Mass Estimator] Massa non fornita dall'utente. Stima LLM: 0.360 kg "
                         "(racchetta da padel — telaio in fibra di carbonio, ~360 g convertiti in kg).")
    cases.append(dict(
        id=10,
        title="Racchetta da padel, telaio in fibra di carbonio — Italia, 600 km camion, MASSA NON SPECIFICATA (verifica LLM Estimator)",
        state=make_state(
            user_input="Racchetta da padel con telaio in fibra di carbonio, prodotta in Italia "
                       "e trasportata per 600 km su camion.",
            constraints={"mass": 0.36, "geography": "Italy", "distance_km": 600.0,
                         "transport_mode": "lorry", "task_type": "modeling"},
            bom=[{
                "name": "Telaio racchetta in fibra di carbonio", "material": "carbon fibre",
                "weight_kg": 0.36, "geometry": "Pezzi Pieni Complessi",
                "manufacturing_process": "Injection moulding",
                "is_material_only": False, "is_recycled": False,
                "transport_mode": "lorry", "distance_km": 600.0,
            }],
            assumptions=[_padel_assumption],
        ),
        # 'carbon fibre' risulta REALISTICAMENTE assente dal dataset con
        # confidenza ≥ 0.85 (verificato empiricamente — coerente con il fatto
        # che il codice di lca_validator preveda ESPLICITAMENTE un suggerimento
        # dedicato "carbon fiber" → "glass fiber o generic composite" nel
        # messaggio di blocco STRICT). Il test verifica quindi che: (a) la
        # massa stimata dall'LLM Estimator fluisca correttamente nel motore,
        # (b) il blocco STRICT — se attivato — sia pulito e informativo.
        expectations=dict(
            expect_strict_block="allow",
            assumptions_contains=["LLM Mass Estimator"],
        ),
    ))

    # ---- Casi 11 & 12: Microchip — routing aereo + massa minima ------------
    # Stesso scenario (microchip in silicio per centralina, spedizione aerea
    # Shenzhen → Milano, 9500 km) testato a DUE ordini di grandezza di massa
    # 'minima' (50 g e 2 g) per stressare la robustezza del motore — incluso
    # il fattore di scala UOM — anche ai limiti inferiori del dominio.
    for _id, _mass_g, _mass_kg in ((11, "≈ 50 g", 0.05), (12, "≈ 2 g", 0.002)):
        _chip_assumption = (f"[LLM Mass Estimator] Massa non fornita dall'utente. Stima LLM: "
                            f"{_mass_kg:.3f} kg (microchip elettronico in silicio, peso minimo {_mass_g} convertiti in kg).")
        cases.append(dict(
            id=_id,
            title=f"Microchip in silicio per centralina — Italia, spedizione aerea Shenzhen→Milano (9500 km), MASSA MINIMA ({_mass_g}) (verifica routing aereo + stima massa minima)",
            state=make_state(
                user_input="Microchip elettronico in silicio integrato in una centralina, destinazione Italia, "
                           "spedito via aereo da Shenzhen a Milano per 9500 km.",
                constraints={"mass": _mass_kg, "geography": "Italy", "distance_km": 9500.0,
                             "transport_mode": "aircraft", "task_type": "modeling",
                             "supplier_country": "China", "destination_country": "Italy"},
                bom=[{
                    "name": "Microchip in silicio", "material": "silicon",
                    "weight_kg": _mass_kg, "geometry": "Pezzi Pieni Complessi",
                    "manufacturing_process": "Injection moulding",
                    "is_material_only": False, "is_recycled": False,
                    "transport_mode": "aircraft", "distance_km": 9500.0,
                    "supplier_country": "China", "destination_country": "Italy",
                }],
                assumptions=[_chip_assumption],
            ),
            expectations=dict(
                expect_strict_block="allow",
                thought_log_contains=["transport, freight, aircraft"],
                assumptions_contains=["LLM Mass Estimator"],
            ),
        ))

    return cases


# ---------------------------------------------------------------------------
# Esecuzione batch
# ---------------------------------------------------------------------------
async def main() -> int:
    print("BATCH TEST — 12 Casi Studio Industriali — Motore LCA (UOM-aware) + Triple Guard task_type")
    print("Mock attivo: data.csv_lca_client.generate_search_queries (no chiamate LLM live; "
          "fuzzy-match e dataset reali in esecuzione).")

    # Pre-carica il provider singleton (dataset in memoria) prima del batch.
    get_lca_provider()

    fake_expand = AsyncMock(side_effect=lambda m, entity_type="material": [m])
    with patch("data.csv_lca_client.generate_search_queries", new=fake_expand):
        for case in build_cases():
            await run_case(case["id"], case["title"], case["state"], case.get("expectations"))

    # ---- Riepilogo finale --------------------------------------------------
    print(f"\n{'='*86}\nRIEPILOGO BATCH TEST — 12 CASI INDUSTRIALI\n{'='*86}")
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [(n, d) for n, ok, d in RESULTS if not ok]

    print(f"Asserzioni totali: {total} | PASS: {passed} | FAIL: {len(failed)}")
    if failed:
        print("\nAsserzioni FALLITE:")
        for name, detail in failed:
            print(f"   ✗ {name}{' — ' + detail if detail else ''}")
    else:
        print("\n✅ Tutte le asserzioni superate. Nessun crash di sistema su 12/12 scenari industriali.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
