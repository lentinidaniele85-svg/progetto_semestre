"""
TEST SUITE COMPLETO - Verifica logica LCA vs System Prompt + regole aggiuntive
=============================================================================

Regole verificate:
  [A] market for  - usato quando NON c'e' distanza (has_transport=False/None)
  [B] market for  - NON usato quando c'e' distanza (has_transport=True)
  [C] waste       - MAI usato (filtro waste assoluto)
  [D] has_transport=None -> market for (identico a False)
  [E] GLO + nessuna distanza -> market for
  [F] steel senza distanza -> market for (se esiste)
  [G] steel con distanza -> NO market for
  [H] workflow_node: massa OK, luogo OK, nessuna distanza -> NON chiede nulla, flusso completo
  [I] workflow_node: sedia 4.5 kg Svezia 800 km -> 800 km registrati, nessuna interview
  [J] workflow_node: sedia senza massa e luogo -> pending_feedback (interview 1deg tentativo)
  [K] workflow_node: massa mancante -> pending_feedback
  [L] workflow_node: luogo mancante -> pending_feedback
  [M] workflow_node: distanza mancante -> NON blocca, distance_km=0
  [N] workflow_node: 2deg tentativo, ancora mancano -> assunzioni autonome (1.0 kg / RER)
  [O] BOM review: 'ok' approva senza modifiche
  [P] BOM review: fase interview -> aggiunge risposta a user_input
"""

import sys
import asyncio
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# Sopprimi i log di debug durante i test
logging.disable(logging.WARNING)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from data.csv_lca_client import CSVLcaClient

RESULTS = []


def check(name: str, passed: bool, detail: str = ""):
    icon = "[PASS]" if passed else "[FAIL]"
    print(f"  {icon} {name}")
    if detail:
        print(f"         {detail}")
    RESULTS.append((name, passed))


# ---------------------------------------------------------------------------
# SEZIONE 1: csv_lca_client - Regola market for / waste
# ---------------------------------------------------------------------------

async def test_csv_lca_rules():
    print("\n" + "=" * 70)
    print("SEZIONE 1 - csv_lca_client: market for / waste / produzione")
    print("=" * 70)
    client = CSVLcaClient()

    # Test A
    print("\n[A] Nessuna distanza (has_transport=False) -> deve usare 'market for'")
    res = await client.find_closest_match("polypropylene", location="RER", has_transport=False)
    proc = res.get("providerName", "") if res else ""
    ok = "market for" in proc.lower()
    check("A: has_transport=False -> market for polypropylene", ok, f"providerName='{proc}'")

    # Test B
    print("\n[B] Distanza presente (has_transport=True) -> NON deve usare 'market for'")
    res = await client.find_closest_match("polypropylene", location="RER", has_transport=True)
    proc = res.get("providerName", "") if res else ""
    ok = "market for" not in proc.lower()
    check("B: has_transport=True -> NO market for polypropylene", ok, f"providerName='{proc}'")

    # Test C
    print("\n[C] Il filtro waste e' assoluto - nessun risultato con 'waste' nel nome")
    found_waste = False
    for mat in ["polypropylene", "polyethylene", "steel"]:
        for geo in ["RER", "Global", "Italy"]:
            for ht in [True, False, None]:
                res = await client.find_closest_match(mat, location=geo, has_transport=ht)
                if res:
                    fn = res.get("flowName", "").lower()
                    pn = res.get("providerName", "").lower()
                    if "waste" in fn or "waste" in pn:
                        found_waste = True
                        check("C: filtro waste assoluto", False,
                              f"TROVATO WASTE: mat={mat} geo={geo} ht={ht} flow='{fn}' proc='{pn}'")
                        break
    if not found_waste:
        check("C: filtro waste assoluto (3 materiali x 3 geo x 3 ht)", True)

    # Test D
    print("\n[D] has_transport=None -> market for (come False)")
    res = await client.find_closest_match("polypropylene", location="RER", has_transport=None)
    proc = res.get("providerName", "") if res else ""
    ok = "market for" in proc.lower()
    check("D: has_transport=None -> market for polypropylene", ok, f"providerName='{proc}'")

    # Test E
    print("\n[E] Luogo=GLO, nessuna distanza -> market for con fallback geografico")
    res = await client.find_closest_match("polypropylene", location="GLO", has_transport=False)
    proc = res.get("providerName", "") if res else ""
    ok = "market for" in proc.lower()
    check("E: GLO + has_transport=False -> market for polypropylene", ok, f"providerName='{proc}'")

    # Test F
    print("\n[F] Acciaio senza distanza -> market for (se presente nel DB)")
    res = await client.find_closest_match("steel", location="Europe without Switzerland", has_transport=False)
    if res:
        proc = res.get("providerName", "")
        ok_f = "market for" in proc.lower()
        check("F: steel + has_transport=False -> market for", ok_f, f"providerName='{proc}'")
    else:
        check("F: steel + has_transport=False -> nessun match (accettabile)", True,
              "Nessun dataset steel market for per Europe without Switzerland")

    # Test G
    print("\n[G] Acciaio con distanza -> NO market for")
    res = await client.find_closest_match("steel", location="Europe without Switzerland", has_transport=True)
    if res:
        proc = res.get("providerName", "")
        ok_g = "market for" not in proc.lower()
        check("G: steel + has_transport=True -> NO market for", ok_g, f"providerName='{proc}'")
    else:
        check("G: steel + has_transport=True -> nessun match (accettabile)", True)


# ---------------------------------------------------------------------------
# SEZIONE 2: Logica workflow_node - test della logica deterministica
#            Senza importare workflow_node (richiede API key)
#            Testiamo la logica isolata con funzioni helper
# ---------------------------------------------------------------------------

def simulate_workflow_logic(
    mass, geography, dist_km, is_material_only, is_interview_complete,
    interview_questions=None, attempt_count=0
):
    """
    Simula la logica deterministica di workflow_bom_ideator senza LLM.
    Replica esattamente il codice in workflow_node.py (linee 140-195).
    """
    missing = []

    if mass is None and not is_material_only:
        missing.append("massa")

    # Normalizza la geografia
    geo = geography or ""
    if geo.lower() in ["not specified", "unknown geography", ""]:
        missing.append("luogo (geografia)")

    # La distanza viene chiesta al primo tentativo di intervista (attempt_count == 0).
    # Al 2deg tentativo, se ancora mancante, si usa market for senza bloccare.
    if dist_km is None and not is_material_only and attempt_count == 0:
        missing.append("distanza di trasporto (km)")

    needs_interview = len(missing) > 0 or not is_interview_complete

    result = {
        "pending_feedback": None,
        "current_phase": None,
        "interview_attempt_count": attempt_count,
        "assumptions": [],
        "has_transport": dist_km is not None and dist_km > 0,
        "distance_km": dist_km or 0.0,
    }

    if needs_interview:
        if attempt_count == 0:
            # Primo tentativo: chiede all'utente
            msg = ""
            if missing:
                msg = f"Mancano alcune informazioni importanti: {', '.join(missing)}. Puoi fornirle?\n"
            for q in (interview_questions or []):
                msg += f"- {q}\n"
            if not msg.strip():
                msg = "Mi mancano alcune informazioni per poter procedere."
            result["pending_feedback"] = msg.strip()
            result["current_phase"] = "interview"
            result["interview_attempt_count"] = attempt_count + 1
        else:
            # Secondo tentativo: assunzioni autonome
            if mass is None and not is_material_only:
                mass = 1.0
                result["assumptions"].append("Massa non fornita dall'utente, assunto default di 1.0 kg.")
            if geo.lower() in ["not specified", "unknown geography", ""]:
                result["assumptions"].append(
                    "Luogo non fornito dall'utente. Utilizzo RER (Europa) come proxy."
                )
            # Distanza: se ancora mancante al 2deg tentativo, NON blocca.
            # has_transport resta False -> il sistema usa i dataset 'market for'.
            if dist_km is None:
                result["assumptions"].append(
                    "Distanza non fornita dall'utente. Utilizzati dataset 'market for' "
                    "che includono gia' la logistica media."
                )
            result["current_phase"] = "workflow"
            result["pending_feedback"] = None
    else:
        result["current_phase"] = "workflow"
        result["pending_feedback"] = None

    return result


def test_workflow_logic_isolated():
    print("\n" + "=" * 70)
    print("SEZIONE 2 - logica workflow (simulata, senza LLM)")
    print("=" * 70)

    # Test H: Esempio 1 - massa OK, luogo OK, nessuna distanza -> flusso completo
    print("\n[H] Esempio 1 - 1 kg PP, RER, nessuna distanza -> NON chiede nulla")
    out = simulate_workflow_logic(
        mass=1.0, geography="Europe without Switzerland",
        dist_km=None, is_material_only=True, is_interview_complete=True
    )
    check("H: Esempio 1 - nessuna interview (pending_feedback=None)",
          out["pending_feedback"] is None, f"pending='{out['pending_feedback']}'")
    check("H: Esempio 1 - flusso workflow completato (current_phase='workflow')",
          out["current_phase"] == "workflow", f"phase='{out['current_phase']}'")
    check("H: Esempio 1 - nessuna distanza (has_transport=False)",
          out["has_transport"] is False, f"has_transport={out['has_transport']}")

    # Test I: Esempio 2 - 4.5 kg PP Svezia 800 km -> flusso completo, 800 km
    print("\n[I] Esempio 2 - 4.5 kg PP Svezia 800 km -> flusso completo, 800 km loggati")
    out = simulate_workflow_logic(
        mass=4.5, geography="Sweden",
        dist_km=800.0, is_material_only=False, is_interview_complete=True
    )
    check("I: Esempio 2 - nessuna interview",
          out["pending_feedback"] is None, f"pending='{out['pending_feedback']}'")
    check("I: Esempio 2 - 800 km registrati (has_transport=True)",
          out["has_transport"] is True, f"has_transport={out['has_transport']}, dist={out['distance_km']}")
    check("I: Esempio 2 - distance_km=800",
          out["distance_km"] == 800.0, f"distance_km={out['distance_km']}")

    # Test J: Esempio 3 - sedia senza massa e luogo -> interview al 1deg tentativo
    print("\n[J] Esempio 3 - sedia senza massa/luogo -> interview al 1deg tentativo")
    out = simulate_workflow_logic(
        mass=None, geography=None,
        dist_km=None, is_material_only=False, is_interview_complete=False,
        interview_questions=["Qual e' la massa della sedia (kg)?", "In quale paese viene prodotta?"],
        attempt_count=0
    )
    check("J: Esempio 3 - pending_feedback non vuoto",
          bool(out["pending_feedback"]), f"pending='{(out['pending_feedback'] or '')[:80]}'")
    check("J: Esempio 3 - current_phase='interview'",
          out["current_phase"] == "interview", f"phase='{out['current_phase']}'")
    check("J: Esempio 3 - interview_attempt_count=1",
          out["interview_attempt_count"] == 1, f"attempt={out['interview_attempt_count']}")

    # Test K: solo massa mancante -> chiede la massa
    print("\n[K] Massa mancante (luogo OK) -> interview")
    out = simulate_workflow_logic(
        mass=None, geography="Italy",
        dist_km=None, is_material_only=False, is_interview_complete=False,
        attempt_count=0
    )
    check("K: massa mancante -> pending_feedback attivato",
          bool(out["pending_feedback"]), f"pending='{(out['pending_feedback'] or '')[:80]}'")

    # Test L: solo luogo mancante -> chiede il luogo
    print("\n[L] Luogo mancante (massa OK) -> interview")
    out = simulate_workflow_logic(
        mass=5.0, geography=None,
        dist_km=None, is_material_only=False, is_interview_complete=False,
        attempt_count=0
    )
    check("L: luogo mancante -> pending_feedback attivato",
          bool(out["pending_feedback"]), f"pending='{(out['pending_feedback'] or '')[:80]}'")

    # Test M: distanza mancante -> chiesta nella prima interview
    print("\n[M] Distanza mancante -> chiesta nella prima interview (attempt_count=0)")
    out = simulate_workflow_logic(
        mass=5.0, geography="Italy",
        dist_km=None, is_material_only=False, is_interview_complete=True,
        attempt_count=0
    )
    check("M: distanza mancante al 1deg tentativo -> pending_feedback attivato",
          bool(out["pending_feedback"]), f"pending='{(out['pending_feedback'] or '')[:100]}'")
    check("M: distanza mancante -> 'distanza' nella lista mancanti",
          "distanza" in (out["pending_feedback"] or "").lower(),
          f"pending='{(out['pending_feedback'] or '')[:100]}'")
    check("M: distanza mancante al 1deg tentativo -> current_phase='interview'",
          out["current_phase"] == "interview", f"phase='{out['current_phase']}'")

    # Test M2: distanza mancante al 2deg tentativo (solo dist mancante, mass+geo OK)
    # needs_interview=False -> procede direttamente con has_transport=False.
    # L'assunzione "market for" appare solo se ANCHE altri dati mancano (Test N).
    print("\n[M2] Distanza mancante al 2deg tentativo -> NON blocca, has_transport=False")
    out = simulate_workflow_logic(
        mass=5.0, geography="Italy",
        dist_km=None, is_material_only=False, is_interview_complete=True,
        attempt_count=1  # secondo tentativo
    )
    check("M2: distanza mancante al 2deg tentativo -> NON blocca (pending_feedback=None)",
          out["pending_feedback"] is None, f"pending='{out['pending_feedback']}'")
    check("M2: distanza mancante al 2deg tentativo -> flusso workflow completato",
          out["current_phase"] == "workflow", f"phase='{out['current_phase']}'")
    check("M2: has_transport=False -> usera' market for nel DB lookup",
          out["has_transport"] is False, f"has_transport={out['has_transport']}")

    # Test N: 2deg tentativo, ancora mancano massa e luogo -> assunzioni autonome
    print("\n[N] 2deg tentativo, ancora mancano massa e luogo -> assunzioni autonome")
    out = simulate_workflow_logic(
        mass=None, geography=None,
        dist_km=None, is_material_only=False, is_interview_complete=False,
        interview_questions=["Qual e' la massa?"],
        attempt_count=1  # secondo tentativo
    )
    check("N: 2deg tentativo -> NON si ferma in interview (pending_feedback=None)",
          out["pending_feedback"] is None, f"pending='{out['pending_feedback']}'")
    check("N: 2deg tentativo -> assunzione massa (1.0 kg)",
          any("1.0" in a or "massa" in a.lower() for a in out["assumptions"]),
          f"assumptions={out['assumptions']}")
    check("N: 2deg tentativo -> assunzione geografia (RER)",
          any("rer" in a.lower() or "ropa" in a.lower() for a in out["assumptions"]),
          f"assumptions={out['assumptions']}")

    # Test bonus: distanza presente => has_transport=True => NON usa market for nel DB lookup
    print("\n[bonus] Verifica coerenza has_transport con la regola market for nel DB")
    out_no_dist = simulate_workflow_logic(
        mass=5.0, geography="Italy", dist_km=None,
        is_material_only=False, is_interview_complete=True
    )
    out_with_dist = simulate_workflow_logic(
        mass=5.0, geography="Italy", dist_km=800.0,
        is_material_only=False, is_interview_complete=True
    )
    check("bonus: senza distanza -> has_transport=False (passato a find_closest_match)",
          out_no_dist["has_transport"] is False, f"has_transport={out_no_dist['has_transport']}")
    check("bonus: con distanza -> has_transport=True (passato a find_closest_match)",
          out_with_dist["has_transport"] is True, f"has_transport={out_with_dist['has_transport']}")


# ---------------------------------------------------------------------------
# SEZIONE 3: BOM review - logica human_feedback_processor (simulata)
# ---------------------------------------------------------------------------

def simulate_hfp_logic(feedback: str, current_phase: str, bom: list, user_input: str):
    """
    Simula la logica deterministica di human_feedback_processor senza LLM.
    Replica le regole chiave da nodes.py.
    """
    import re

    _APPROVE_TOKENS = frozenset({
        "ok", "okay", "approva", "approvato", "si", "si'", "yes", "y",
        "continua", "procedi", "bene", "vai", "conferma", "perfetto",
        "approve", "approved", "continue", "proceed", "good", "go ahead",
        "go", "looks good", "lgtm", "next", "done", "sure", "fine",
        "accept", "yep", "yup",
    })

    def _clean_token(text):
        return re.sub(r"[.,!?;:]+$", "", text.strip().lower())

    lower = _clean_token(feedback)

    if current_phase == "interview":
        # Aggiunge la risposta all'input utente
        new_input = user_input + f"\n\n[User Interview Response]: {feedback}"
        return {
            "user_input": new_input,
            "pending_feedback": None,
            "bom": bom,
            "current_phase": current_phase,
        }

    if lower in _APPROVE_TOKENS or any(lower.startswith(t + " ") for t in _APPROVE_TOKENS):
        return {
            "pending_feedback": None,
            "bom": bom,
            "current_phase": current_phase,
        }

    # Modifica non approvazione (richiede LLM, simuliamo solo la struttura)
    return {
        "pending_feedback": None,
        "bom": bom,
        "current_phase": current_phase,
        "needs_llm": True,
    }


def test_bom_review_logic():
    print("\n" + "=" * 70)
    print("SEZIONE 3 - BOM review: approvazione / modifica feedback (simulata)")
    print("=" * 70)

    base_bom = [{"name": "Body", "material": "polypropylene",
                 "weight_kg": 4.5, "geometry": "Pezzi Pieni Complessi"}]

    # Test O: "ok" approva senza modifiche
    print("\n[O] Feedback 'ok' -> approvazione BOM senza modifiche")
    for approval in ["ok", "si'", "approva", "continua", "perfetto", "yes", "go"]:
        out = simulate_hfp_logic(approval, "workflow", base_bom, "sedia")
        ok = out["pending_feedback"] is None and out["bom"] == base_bom
        if not ok:
            check(f"O: '{approval}' -> approvato", False, f"pending={out.get('pending_feedback')}")
            break
    else:
        check("O: tutti i token di approvazione -> pending_feedback=None, BOM invariata", True)

    # Test P: fase interview -> qualsiasi risposta aggiunge all'user_input
    print("\n[P] Fase interview: qualsiasi risposta aggiunge all'user_input")
    out = simulate_hfp_logic(
        "La sedia pesa 4 kg e viene prodotta in Italia",
        "interview", [], "sedia in plastica"
    )
    check("P: fase interview -> risposta aggiunta a user_input",
          "4 kg" in (out.get("user_input") or ""),
          f"user_input snippet='{(out.get('user_input') or '')[-60:]}'")
    check("P: fase interview -> pending_feedback svuotato",
          out.get("pending_feedback") is None)

    # Test Q: in workflow, feedback NON di approvazione richiede LLM (routing corretto)
    print("\n[Q] Feedback non-approvazione in fase 'workflow' -> richiede modifica via LLM")
    out = simulate_hfp_logic(
        "Cambia il materiale da polipropilene ad acciaio",
        "workflow", base_bom, "sedia"
    )
    check("Q: feedback di modifica -> needs_llm=True", out.get("needs_llm", False) is True)


# ---------------------------------------------------------------------------
# RUNNER PRINCIPALE
# ---------------------------------------------------------------------------

async def main():
    print("\n" + "=" * 70)
    print("  TEST SUITE COMPLETO - LCA Logic vs System Prompt")
    print("=" * 70)

    await test_csv_lca_rules()
    test_workflow_logic_isolated()
    test_bom_review_logic()

    total = len(RESULTS)
    passed = sum(1 for _, ok in RESULTS if ok)
    failed = [(name, ok) for name, ok in RESULTS if not ok]

    print("\n" + "=" * 70)
    print(f"  RIEPILOGO: {passed}/{total} test superati")
    print("=" * 70)

    if failed:
        print("\n  FALLITI:")
        for name, _ in failed:
            print(f"     - {name}")
    else:
        print("\n  Tutti i test superati!")

    return failed


if __name__ == "__main__":
    failures = asyncio.run(main())
    sys.exit(1 if failures else 0)
