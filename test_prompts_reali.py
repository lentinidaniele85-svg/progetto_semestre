"""
Test rapido: esegue ogni prompt con timeout di 3 minuti.
Stampa i risultati prompt per prompt in tempo reale.
"""
import asyncio
import io
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from agents.graph import build_graph
from agents.state import AgentState

TIMEOUT_PER_PROMPT = 180  # 3 minuti max per prompt

PROMPTS = [
    {"id": 1, "label": "Sedia plastica - Germania 600km camion", "expected": "modeling",
     "text": "Voglio modellare una sedia da interno in plastica.\nPesa 8 kg, la produco in Germania e il materiale viaggia per 600 km su camion."},
    {"id": 2, "label": "200kg EPS polistirene - Europa", "expected": "modeling",
     "text": "Analisi dell'impatto ambientale di 200 kg di polistirene espanso (EPS) in Europa."},
    {"id": 3, "label": "15kg LDPE sacchetti - Francia 450km", "expected": "optimization",
     "text": "Voglio ottimizzare un lotto di sacchetti in pellicola di polietilene (LDPE) da 15 kg. La produzione avviene in Francia e il trasporto prevede una tratta di 450 km su camion."},
    {"id": 4, "label": "25kg alluminio estruso - Germania 150km", "expected": "modeling",
     "text": "Devo analizzare l'impatto di una barra a profilo continuo in alluminio estruso prodotta in Germania.\nIl profilo pesa 25 kg e la tratta di trasporto e' di 150 km."},
    {"id": 5, "label": "50kg tubo PVC - Germania 500km", "expected": "modeling",
     "text": "Voglio modellare un tubo rigido per l'edilizia in PVC\nIl lotto pesa 50 kg, la produzione avviene in Germania e il materiale viaggia per 500 km su camion."},
    {"id": 6, "label": "Film polivinilfluoruro - USA 300km", "expected": "optimization",
     "text": "Voglio ottimizzare l'impatto ambientale di un lotto di film in polivinilfluoruro. La produzione avviene negli United States of America.\nIl lotto ha un'altezza di spettro di 150 kg e il trasporto e' di 300 km su camion."},
    {"id": 7, "label": "500kg alluminio + 1500km nave", "expected": "modeling",
     "text": "Analisi LCA per un lotto di 500 kg di profilati di alluminio, con un trasporto aggiuntivo di 1500 km effettuato tramite nave transoceanica cargo."},
    {"id": 8, "label": "200kg carta riciclata - Polonia", "expected": "optimization",
     "text": "Voglio ottimizzare l'impatto di un lotto di 200 kg di carta grafica riciclata. La produzione avviene in Polonia."},
    {"id": 9, "label": "10kg PP iniezione - Europa no trasporto", "expected": "modeling",
     "text": "Voglio modellare l'impatto di 10 kg di polipropilene (PP) stampato a iniezione in Europa senza trasporti personalizzati."},
    {"id": 10, "label": "Racchetta padel carbonio - Italia 600km", "expected": "optimization",
     "text": "Ottimizzazione ambientale di una racchetta da padel con telaio in fibra di carbonio prodotta in Italia.\n600 km, per la massa procedi pure con una stima autonoma."},
    {"id": 11, "label": "Microchip silicio - aereo 9500km stima massa", "expected": "modeling",
     "text": "Valutazione dell'impatto ambientale di un microchip elettronico in silicio integrato in una centralina in Italia.\nIl componente viene spedito via aereo da Shenzhen a Milano per un totale di 9500 km. Peso minimo, stima tu."},
]


async def run_one(prompt: dict) -> tuple[dict, float]:
    state: AgentState = {
        "user_input": prompt["text"],
        "mode": "auto",
        "thought_log": [], "bom": [], "workflow_steps": [],
        "semantic_alternatives": [], "lca_results": [], "mcda_scores": [],
        "constraints": {}, "chat_history": [],
    }
    graph = build_graph(mode="auto")
    t0 = time.time()
    result = await asyncio.wait_for(graph.ainvoke(state), timeout=TIMEOUT_PER_PROMPT)
    return result, time.time() - t0


def score_result(prompt: dict, state: dict) -> dict:
    checks = {}
    s, mx = 0, 0

    def chk(k, ok, w=1):
        checks[k] = "OK" if ok else "FAIL"
        nonlocal s, mx
        mx += w
        if ok: s += w

    phase = state.get("current_phase", "")
    chk("pipeline", phase in ("complete", "lca", "mcda"), 3)
    bom = state.get("bom", [])
    chk("bom", len(bom) >= 1, 2)
    lca = state.get("lca_results", [])
    chk("lca", len(lca) >= 1, 2)
    chk("co2_valid", all(r.get("original_scores", {}).get("environmental_impact", -1) >= 0 for r in lca), 2)
    detected_tt = (state.get("constraints") or {}).get("task_type", "")
    chk("task_type", detected_tt == prompt["expected"], 2)
    chk("mass", all(c.get("weight_kg", 0) > 0 for c in bom) and len(bom) > 0, 2)

    has_km = any(w in prompt["text"].lower() for w in ["km", "camion", "nave", "aereo"])
    if has_km:
        t_entry = next((r for r in lca if r.get("component_name") == "Transport"), None)
        chk("transport", t_entry is not None and t_entry.get("original_scores", {}).get("environmental_impact", -1) >= 0, 2)

    if prompt["expected"] == "optimization":
        chk("mcda", len(state.get("mcda_scores", [])) > 0, 2)

    no_err = not any("STRICT LCA FAIL" in t for t in state.get("thought_log", []))
    chk("no_error", no_err, 2)

    pct = round(s / mx * 100) if mx else 0
    total_co2 = sum(r.get("original_scores", {}).get("environmental_impact", 0) for r in lca)
    error_msg = state.get("error_message", "")

    return {"checks": checks, "score": s, "max": mx, "pct": pct,
            "phase": phase, "total_co2": total_co2, "error": error_msg}


async def main():
    print("=" * 70)
    print("  TEST REALI LLM - LCA Co-Pilot")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Timeout: {TIMEOUT_PER_PROMPT}s/prompt")
    print("=" * 70)

    results = []
    for i, p in enumerate(PROMPTS):
        print(f"\n[{i+1}/{len(PROMPTS)}] {p['label']}")
        print(f"  Atteso: {p['expected']}")
        t0 = time.time()
        try:
            state, elapsed = await run_one(p)
            r = score_result(p, state)
            results.append({"id": p["id"], "label": p["label"], **r, "elapsed": round(elapsed, 1)})

            status = "PASS" if r["pct"] >= 70 else ("PARZ" if r["pct"] >= 40 else "FAIL")
            print(f"  [{status}] Score: {r['score']}/{r['max']} ({r['pct']}%)  |  Fase: {r['phase']}  |  {elapsed:.0f}s")
            print(f"  CO2 totale: {r['total_co2']:.4f} kg CO2-eq")

            bom = state.get("bom", [])
            if bom:
                bom_str = ", ".join(f"{c.get('name','?')} ({c.get('weight_kg','?')}kg)" for c in bom)
                print(f"  BOM: {bom_str}")

            for comp in state.get("mcda_scores", []):
                best = comp.get("best_alternative")
                if best:
                    print(f"  BEST {comp['component_name']}: {best['name']} -> -{best['impact_reduction_pct']:.1f}% CO2")

            checks_str = " | ".join(f"{k}:{v}" for k, v in r["checks"].items())
            print(f"  {checks_str}")

            if r.get("error"):
                print(f"  ERRORE: {str(r['error'])[:180]}")

        except asyncio.TimeoutError:
            elapsed = time.time() - t0
            print(f"  [TIMEOUT] Superati {TIMEOUT_PER_PROMPT}s - prompt saltato")
            results.append({"id": p["id"], "label": p["label"], "pct": 0, "score": 0, "max": 1,
                           "phase": "TIMEOUT", "elapsed": round(elapsed, 1), "checks": {}, "error": "TIMEOUT"})
        except Exception as exc:
            print(f"  [ERRORE] {exc}")
            traceback.print_exc()
            results.append({"id": p["id"], "label": p["label"], "pct": 0, "score": 0, "max": 1,
                           "phase": "EXCEPTION", "elapsed": 0, "checks": {}, "error": str(exc)})

    # Riepilogo
    print("\n" + "=" * 70)
    print("  RIEPILOGO")
    print("=" * 70)
    ok = sum(1 for r in results if r["pct"] >= 70)
    med = sum(1 for r in results if 40 <= r["pct"] < 70)
    ko = sum(1 for r in results if r["pct"] < 40)
    tot_s = sum(r["score"] for r in results)
    tot_m = sum(r["max"] for r in results)
    pct_overall = round(tot_s / tot_m * 100) if tot_m else 0

    for r in results:
        tag = "PASS" if r["pct"] >= 70 else ("PARZ" if r["pct"] >= 40 else "FAIL")
        print(f"  [{tag}] [{r['id']:2d}] {r['label']:<45} {r['pct']:3d}%  {r['elapsed']}s")

    print(f"\n  Totale: {tot_s}/{tot_m} = {pct_overall}%")
    print(f"  PASS(>=70%): {ok}   PARZ(40-69%): {med}   FAIL(<40%): {ko}")

    out = ROOT / "test_results_reali.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "overall_pct": pct_overall,
                   "pass": ok, "partial": med, "fail": ko, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n  Salvato: {out}")
    return pct_overall


if __name__ == "__main__":
    r = asyncio.run(main())
    sys.exit(0 if r >= 60 else 1)
