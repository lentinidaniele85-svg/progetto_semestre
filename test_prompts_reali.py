"""
Test end-to-end con LLM REALE per tutti i prompt del Prompt.txt.
Esegue la pipeline in modalita' 'auto' (senza interrupt umani) e valuta
la qualita' dell'output per ciascun prompt.

Uso: python test_prompts_reali.py
"""
import asyncio
import io
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# Forza stdout UTF-8 su Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Aggiunge la root del progetto al path --------------------------------
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from agents.graph import build_graph
from agents.state import AgentState

# --- Prompt reali dal Prompt.txt ------------------------------------------
PROMPTS = [
    {
        "id": 1,
        "label": "Sedia in plastica - Germania, 600 km camion",
        "text": (
            "Voglio modellare una sedia da interno in plastica.\n"
            "Pesa 8 kg, la produco in Germania e il materiale viaggia per 600 km su camion."
        ),
        "expected_task_type": "modeling",
    },
    {
        "id": 2,
        "label": "200 kg EPS polistirene - Europa",
        "text": "Analisi dell'impatto ambientale di 200 kg di polistirene espanso (EPS) in Europa.",
        "expected_task_type": "modeling",
    },
    {
        "id": 3,
        "label": "15 kg LDPE sacchetti - Francia, 450 km camion",
        "text": (
            "Voglio ottimizzare un lotto di sacchetti in pellicola di polietilene (LDPE) da 15 kg. "
            "La produzione avviene in Francia e il trasporto prevede una tratta di 450 km su camion."
        ),
        "expected_task_type": "optimization",
    },
    {
        "id": 4,
        "label": "25 kg alluminio estruso - Germania, 150 km",
        "text": (
            "Devo analizzare l'impatto di una barra a profilo continuo in alluminio estruso prodotta in Germania.\n"
            "Il profilo pesa 25 kg e la tratta di trasporto e' di 150 km."
        ),
        "expected_task_type": "modeling",
    },
    {
        "id": 5,
        "label": "50 kg tubo PVC - Germania, 500 km camion",
        "text": (
            "Voglio modellare un tubo rigido per l'edilizia in PVC\n"
            "Il lotto pesa 50 kg, la produzione avviene in Germania e il materiale viaggia per 500 km su camion."
        ),
        "expected_task_type": "modeling",
    },
    {
        "id": 6,
        "label": "Film polivinilfluoruro - USA, 300 km camion",
        "text": (
            "Voglio ottimizzare l'impatto ambientale di un lotto di film in polivinilfluoruro. "
            "La produzione avviene negli United States of America.\n"
            "Il lotto ha un'altezza di spettro di 150 kg e il trasporto e' di 300 km su camion."
        ),
        "expected_task_type": "optimization",
    },
    {
        "id": 7,
        "label": "500 kg profilati alluminio + 1500 km nave transoceanica",
        "text": (
            "Analisi LCA per un lotto di 500 kg di profilati di alluminio, "
            "con un trasporto aggiuntivo di 1500 km effettuato tramite nave transoceanica cargo."
        ),
        "expected_task_type": "modeling",
    },
    {
        "id": 8,
        "label": "200 kg carta grafica riciclata - Polonia",
        "text": (
            "Voglio ottimizzare l'impatto di un lotto di 200 kg di carta grafica riciclata. "
            "La produzione avviene in Polonia."
        ),
        "expected_task_type": "optimization",
    },
    {
        "id": 9,
        "label": "10 kg PP stampato a iniezione - Europa (no trasporto)",
        "text": (
            "Voglio modellare l'impatto di 10 kg di polipropilene (PP) stampato a iniezione "
            "in Europa senza trasporti personalizzati."
        ),
        "expected_task_type": "modeling",
    },
    {
        "id": 10,
        "label": "Racchetta padel fibra di carbonio - Italia, 600 km (stima massa)",
        "text": (
            "Ottimizzazione ambientale di una racchetta da padel con telaio in fibra di carbonio prodotta in Italia.\n"
            "600 km, per la massa procedi pure con una stima autonoma."
        ),
        "expected_task_type": "optimization",
    },
    {
        "id": 11,
        "label": "Microchip silicio - aereo Shenzhen-Milano 9500 km (stima massa)",
        "text": (
            "Valutazione dell'impatto ambientale di un microchip elettronico in silicio "
            "integrato in una centralina in Italia.\n"
            "Il componente viene spedito via aereo da Shenzhen a Milano per un totale di 9500 km. "
            "Peso minimo, stima tu."
        ),
        "expected_task_type": "modeling",
    },
]

# --- Valutazione qualita' output ------------------------------------------

def evaluate_result(prompt: dict, state: dict, elapsed: float) -> dict:
    """Valuta la qualita' dell'output dello stato finale."""
    result = {
        "id": prompt["id"],
        "label": prompt["label"],
        "elapsed_s": round(elapsed, 1),
        "phase": state.get("current_phase", "?"),
        "error": state.get("error_message"),
        "checks": {},
        "score": 0,
        "max_score": 0,
    }

    def check(name: str, passed: bool, weight: int = 1):
        result["checks"][name] = "OK" if passed else "FAIL"
        result["max_score"] += weight
        if passed:
            result["score"] += weight

    # 1. Pipeline completata senza errori
    phase = state.get("current_phase", "")
    check("pipeline_completata", phase in ("complete", "lca", "mcda"), weight=3)

    # 2. Thought log popolato (>= 4 entries)
    thought_log = state.get("thought_log", [])
    check("thought_log_popolato", len(thought_log) >= 4, weight=1)

    # 3. BOM creata con almeno 1 componente
    bom = state.get("bom", [])
    check("bom_presente", len(bom) >= 1, weight=2)

    # 4. LCA results presenti
    lca = state.get("lca_results", [])
    check("lca_results_presenti", len(lca) >= 1, weight=2)

    # 5. CO2 values numericamente validi (tutti >= 0)
    all_co2_valid = all(
        r.get("original_scores", {}).get("environmental_impact", -1) >= 0
        for r in lca
    )
    check("co2_values_validi", all_co2_valid, weight=2)

    # 6. Task type rilevato correttamente
    detected_tt = (state.get("constraints") or {}).get("task_type", "")
    expected_tt = prompt.get("expected_task_type", "")
    check("task_type_corretto", detected_tt == expected_tt, weight=2)

    # 7. Massa estratta o stimata (BOM ha weight_kg > 0)
    mass_ok = all(c.get("weight_kg", 0) > 0 for c in bom)
    check("massa_plausibile", mass_ok and len(bom) > 0, weight=2)

    # 8. Trasporto calcolato (se richiesto)
    has_transport_prompt = any(w in prompt["text"].lower() for w in ["km", "camion", "nave", "aereo"])
    if has_transport_prompt:
        transport_entry = next((r for r in lca if r.get("component_name") == "Transport"), None)
        transport_calculated = (
            transport_entry is not None
            and transport_entry.get("original_scores", {}).get("environmental_impact", -1) >= 0
        )
        check("trasporto_calcolato", transport_calculated, weight=2)

    # 9. Per ottimizzazione: MCDA scores presenti
    if prompt["expected_task_type"] == "optimization":
        mcda = state.get("mcda_scores", [])
        check("mcda_presente", len(mcda) > 0, weight=2)

    # 10. Nessun errore critico nel thought_log
    no_critical_error = not any(
        "STRICT LCA FAIL" in t or "Calcolo interrotto" in t
        for t in thought_log
    )
    check("nessun_errore_critico", no_critical_error, weight=2)

    result["pct"] = round(result["score"] / result["max_score"] * 100) if result["max_score"] else 0
    return result


async def run_prompt(prompt: dict) -> tuple[dict, float]:
    """Esegue un singolo prompt attraverso la pipeline auto."""
    initial_state: AgentState = {
        "user_input": prompt["text"],
        "mode": "auto",
        "thought_log": [],
        "bom": [],
        "workflow_steps": [],
        "semantic_alternatives": [],
        "lca_results": [],
        "mcda_scores": [],
        "constraints": {},
        "chat_history": [],
    }
    graph = build_graph(mode="auto")
    t0 = time.time()
    final_state = await graph.ainvoke(initial_state)
    elapsed = time.time() - t0
    return final_state, elapsed


async def main():
    print("=" * 72)
    print("  TEST PROMPT REALI - LCA AI Co-Pilot (LLM VERO)")
    print(f"  Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)
    print()

    all_results = []
    all_states = []

    for i, prompt in enumerate(PROMPTS):
        print(f"[{i+1}/{len(PROMPTS)}] {prompt['label']}")
        print(f"  Prompt: {prompt['text'][:90]}...")
        try:
            final_state, elapsed = await run_prompt(prompt)
            eval_r = evaluate_result(prompt, final_state, elapsed)
            all_results.append(eval_r)
            all_states.append({"prompt_id": prompt["id"], "state": final_state})

            # Stampa checks
            checks_str = "  ".join(f"{k}:{v}" for k, v in eval_r["checks"].items())
            print(f"  Tempo: {elapsed:.1f}s  |  Fase: {eval_r['phase']}  |  Score: {eval_r['score']}/{eval_r['max_score']} ({eval_r['pct']}%)")
            print(f"  Checks: {checks_str}")

            # CO2 totale
            lca = final_state.get("lca_results", [])
            total_co2 = sum(
                r.get("original_scores", {}).get("environmental_impact", 0)
                for r in lca
            )
            print(f"  CO2 totale stimata: {total_co2:.4f} kg CO2-eq")

            # BOM sommario
            bom = final_state.get("bom", [])
            if bom:
                bom_summary = ", ".join(
                    f"{c.get('name', '?')} ({c.get('weight_kg', '?')} kg)"
                    for c in bom
                )
                print(f"  BOM: {bom_summary}")

            # MCDA best (se ottimizzazione)
            mcda = final_state.get("mcda_scores", [])
            for comp in mcda:
                best = comp.get("best_alternative")
                if best:
                    print(f"  BEST ({comp['component_name']}): {best['name']} -> -{best['impact_reduction_pct']:.1f}% CO2 | MCDA={best['mcda_score']:.3f}")

            if eval_r.get("error"):
                print(f"  ERRORE: {str(eval_r['error'])[:200]}")

        except Exception as exc:
            print(f"  ECCEZIONE: {exc}")
            traceback.print_exc()
            all_results.append({
                "id": prompt["id"],
                "label": prompt["label"],
                "elapsed_s": 0,
                "phase": "EXCEPTION",
                "error": str(exc),
                "checks": {},
                "score": 0,
                "max_score": 1,
                "pct": 0,
            })

        print()

    # --- Riepilogo finale -------------------------------------------------
    print("=" * 72)
    print("  RIEPILOGO FINALE")
    print("=" * 72)
    print()
    passed = sum(1 for r in all_results if r["pct"] >= 70)
    partial = sum(1 for r in all_results if 40 <= r["pct"] < 70)
    failed = sum(1 for r in all_results if r["pct"] < 40)
    total_score = sum(r["score"] for r in all_results)
    total_max = sum(r["max_score"] for r in all_results)
    overall_pct = round(total_score / total_max * 100) if total_max else 0

    for r in all_results:
        status = "OK " if r["pct"] >= 70 else ("MED" if r["pct"] >= 40 else "ERR")
        print(f"  [{status}] [{r['id']:2d}] {r['label']:<52} {r['pct']:3d}%  ({r['elapsed_s']}s)")

    print()
    print(f"  Totale: {total_score}/{total_max} ({overall_pct}%)")
    print(f"  BUONI (>=70%): {passed}  PARZIALI (40-69%): {partial}  FALLITI (<40%): {failed}")
    print()

    # --- Salva risultati JSON ---------------------------------------------
    output_path = ROOT / "test_results_reali.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "overall_pct": overall_pct,
            "passed": passed,
            "partial": partial,
            "failed": failed,
            "results": all_results,
        }, f, ensure_ascii=False, indent=2)
    print(f"  Risultati salvati in: {output_path}")

    # --- Dettaglio thought_log per i failed -------------------------------
    failed_ids = {r["id"] for r in all_results if r["pct"] < 40}
    if failed_ids:
        print()
        print("  --- DETTAGLIO ERRORI -------------------------------------------")
        for s in all_states:
            if s["prompt_id"] in failed_ids:
                state = s["state"]
                print(f"\n  Prompt {s['prompt_id']} - thought_log (ultimi 5):")
                for t in list(state.get("thought_log", []))[-5:]:
                    print(f"    * {str(t)[:150]}")

    return overall_pct


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result >= 60 else 1)
