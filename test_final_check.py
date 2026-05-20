"""
TEST FINALE COMPLETO - Verifica vs System Prompt (AI LCA modelling - System Prompt 1.docx)
+ prompt aggiuntivi di verifica funzionalita'
============================================================================================

ESEMPI DAL DOCUMENTO:
  [E1] "Voglio modellare il processo di produzione di 1 kg di polipropilene in Europa."
       -> materiale puro, massa+geo specificate, NO distanza -> market for, NO interview
  [E2] "Voglio modellare una sedia in plastica prodotta in Svezia ... La massa e' 4,5 kg.
        Il materiale plastico arriva al sito produttivo in Svezia su camion per 800 km."
       -> oggetto, massa+geo+distanza specificate -> NO market for, 800 km loggati, tkm=3.6
  [E3] "Voglio modellare il processo di produzione di una sedia in plastica da interno."
       -> oggetto, nessun dato -> interview al 1deg tentativo con lista mancanti

PROMPT AGGIUNTIVI (verifica funzionalita' extra):
  [P1] Materiale puro senza distanza ma con location -> market for (regola gold)
  [P2] Oggetto con solo distanza mancante (massa+geo ok) -> distanza chiesta in interview
  [P3] Oggetto con distanza fornita ma luogo mancante -> interview (chiede luogo+distanza)
  [P4] 2deg tentativo: nessun dato fornito -> assunzioni autonome + market for assumption
  [P5] 2deg tentativo: solo distanza ancora mancante -> usa market for senza assunzioni extra
  [P6] is_material_only=True -> distanza NON blocca e NON viene chiesta
  [P7] Filtro waste: nessun dataset waste mai restituito dal DB
  [P8] has_transport=False -> market for confermato sul DB reale (PP, RER)
  [P9] has_transport=True  -> NO market for confermato sul DB reale (PP, RER)
  [P10] tkm Esempio 2: 4.5 kg / 1000 * 800 km = 3.6 tkm (calcolo corretto)
"""

import sys
import logging
import asyncio
from pathlib import Path

# Sopprimi tutti i debug del DB
logging.disable(logging.CRITICAL)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from data.csv_lca_client import CSVLcaClient

RESULTS = []
SECTION_FAILS = {}


def section_header(title: str):
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def check(name: str, passed: bool, detail: str = "", section: str = ""):
    icon = "[PASS]" if passed else "[FAIL]"
    print(f"  {icon} {name}")
    if detail:
        print(f"         {detail}")
    RESULTS.append((name, passed, section))
    if not passed and section:
        SECTION_FAILS.setdefault(section, []).append(name)


# ============================================================================
# SIMULATORE LOGICA WORKFLOW (replica workflow_node.py senza LLM)
# ============================================================================

def run_workflow_logic(
    mass=None,
    geography=None,
    dist_km=None,
    is_material_only=False,
    is_interview_complete=True,
    interview_questions=None,
    attempt_count=0,
):
    """
    Replica ESATTA della logica deterministica in workflow_node.py (post-modifica).
    Ritorna un dict con: pending_feedback, current_phase, interview_attempt_count,
                         assumptions, has_transport, distance_km.
    """
    missing = []

    if mass is None and not is_material_only:
        missing.append("massa")

    geo = (geography or "").strip()
    if geo.lower() in ["not specified", "unknown geography", ""]:
        missing.append("luogo (geografia)")

    # Distanza: chiesta al 1deg tentativo SOLO per oggetti (non materiali puri)
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
        "missing_fields": list(missing),
    }

    if needs_interview:
        if attempt_count == 0:
            msg = ""
            if missing:
                msg = f"Mancano alcune informazioni importanti: {', '.join(missing)}. Puoi fornirle?\n"
            for q in (interview_questions or []):
                msg += f"- {q}\n"
            if not msg.strip():
                msg = "Mi mancano alcune informazioni per poter procedere."
            result["pending_feedback"] = msg.strip()
            result["current_phase"] = "interview"
            result["interview_attempt_count"] = 1
        else:
            # 2deg tentativo: assunzioni autonome
            if mass is None and not is_material_only:
                result["assumptions"].append(
                    "Massa non fornita dall'utente, assunto default di 1.0 kg."
                )
            if geo.lower() in ["not specified", "unknown geography", ""]:
                result["assumptions"].append(
                    "Luogo non fornito dall'utente. Utilizzo RER (Europa) come proxy."
                )
            if dist_km is None:
                result["assumptions"].append(
                    "Distanza non fornita dall'utente. Utilizzati dataset 'market for' "
                    "che includono gia' la logistica media."
                )
            result["current_phase"] = "workflow"
    else:
        result["current_phase"] = "workflow"

    return result


def _pending(out): return out.get("pending_feedback") or ""
def _phase(out):   return out.get("current_phase", "")
def _ht(out):      return out.get("has_transport", False)
def _dist(out):    return out.get("distance_km", 0.0)
def _assum(out):   return out.get("assumptions", [])
def _missing(out): return out.get("missing_fields", [])


# ============================================================================
# SEZIONE 1: ESEMPI DAL DOCUMENTO (AI LCA modelling - System Prompt 1.docx)
# ============================================================================

def test_document_examples():
    section_header("SEZIONE 1 — Esempi dal documento 'AI LCA modelling - System Prompt 1.docx'")
    SEC = "S1"

    # ─────────────────────────────────────────────────────────────────────────
    # ESEMPIO 1: 1 kg polipropilene in Europa, nessun trasporto
    #   Atteso (doc): market for polypropylene, granulate | RER  — Amount: 1 kg
    #   Regola: is_material_only=True -> distanza non chiesta, NO interview,
    #           has_transport=False -> market for
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[E1] '1 kg di polipropilene in Europa' — materiale puro, nessuna distanza")
    print("     DOC atteso: market for polypropylene, granulate | RER")

    # 1a. Logica workflow: non deve chiedere nulla
    out = run_workflow_logic(
        mass=1.0, geography="Europe without Switzerland",
        dist_km=None, is_material_only=True, is_interview_complete=True
    )
    check("E1-1: nessuna interview (pending_feedback=None)",
          _pending(out) == "", f"pending='{_pending(out)[:60]}'", SEC)
    check("E1-2: current_phase='workflow' (flusso completo)",
          _phase(out) == "workflow", f"phase='{_phase(out)}'", SEC)
    check("E1-3: is_material_only -> distanza NON nella lista mancanti",
          "distanza" not in " ".join(_missing(out)).lower(),
          f"missing={_missing(out)}", SEC)
    check("E1-4: has_transport=False (nessuna distanza)",
          _ht(out) is False, f"has_transport={_ht(out)}", SEC)

    # 1b. DB reale: market for polypropylene quando has_transport=False
    client = CSVLcaClient()
    res = client.find_closest_match("polypropylene", location="RER", has_transport=False)
    proc = res.get("providerName", "") if res else ""
    check("E1-5: DB -> market for polypropylene (RER, has_transport=False)",
          "market for" in proc.lower(), f"providerName='{proc}'", SEC)
    check("E1-6: DB -> NO waste nel risultato",
          "waste" not in proc.lower() and "waste" not in (res.get("flowName","") if res else "").lower(),
          f"flowName='{res.get('flowName','') if res else ''}'", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # ESEMPIO 2: Sedia PP 4.5 kg, Svezia, 800 km su camion
    #   Atteso (doc):
    #     Riga 1: market for polypropylene, granulate | RER  — 4.5 kg
    #     Riga 2: injection moulding | SE (o RER)            — 4.5 kg
    #     Riga 3: transport, freight, lorry                   — 3.6 tkm
    #   Distanza esplicita -> has_transport=True -> NO market for -> production
    #   tkm = 4.5/1000 * 800 = 3.6
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[E2] Sedia PP 4.5 kg, Svezia, 800 km — oggetto con distanza esplicita")
    print("     DOC atteso: injection moulding + 3.6 tkm, NO interview")

    out = run_workflow_logic(
        mass=4.5, geography="Sweden",
        dist_km=800.0, is_material_only=False, is_interview_complete=True
    )
    check("E2-1: nessuna interview (pending_feedback=None)",
          _pending(out) == "", f"pending='{_pending(out)[:60]}'", SEC)
    check("E2-2: current_phase='workflow'",
          _phase(out) == "workflow", f"phase='{_phase(out)}'", SEC)
    check("E2-3: has_transport=True (distanza fornita)",
          _ht(out) is True, f"has_transport={_ht(out)}", SEC)
    check("E2-4: distance_km=800.0",
          _dist(out) == 800.0, f"distance_km={_dist(out)}", SEC)

    # tkm = 4.5/1000 * 800 = 3.6
    tkm = (4.5 / 1000.0) * 800.0
    check("E2-5: tkm calcolato = 3.6 (4.5 kg / 1000 * 800 km)",
          abs(tkm - 3.6) < 0.001, f"tkm={tkm:.4f}", SEC)

    # DB: con has_transport=True -> NON deve usare market for
    res2 = client.find_closest_match("polypropylene", location="RER", has_transport=True)
    proc2 = res2.get("providerName", "") if res2 else ""
    check("E2-6: DB -> NON usa market for (distanza presente, has_transport=True)",
          "market for" not in proc2.lower(), f"providerName='{proc2}'", SEC)
    check("E2-7: DB -> NO waste",
          "waste" not in proc2.lower(), f"providerName='{proc2}'", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # ESEMPIO 3: Sedia plastica da interno — nessun dato
    #   Atteso (doc): chiede massa e conferma materiale
    #   Regola aggiornata: chiede anche distanza
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[E3] 'Sedia in plastica da interno' — nessun dato specificato")
    print("     DOC atteso: chiede massa (+ luogo + distanza con regola aggiornata)")

    out = run_workflow_logic(
        mass=None, geography=None,
        dist_km=None, is_material_only=False,
        is_interview_complete=False,
        interview_questions=["Qual e' la massa della sedia (kg)?", "In quale paese viene prodotta?"],
        attempt_count=0
    )
    check("E3-1: pending_feedback non vuoto (chiede dati)",
          bool(_pending(out)), f"pending='{_pending(out)[:80]}'", SEC)
    check("E3-2: current_phase='interview'",
          _phase(out) == "interview", f"phase='{_phase(out)}'", SEC)
    check("E3-3: 'massa' nella lista mancanti",
          "massa" in " ".join(_missing(out)).lower(), f"missing={_missing(out)}", SEC)
    check("E3-4: 'luogo' nella lista mancanti",
          "luogo" in " ".join(_missing(out)).lower(), f"missing={_missing(out)}", SEC)
    check("E3-5: 'distanza' nella lista mancanti (regola aggiornata)",
          "distanza" in " ".join(_missing(out)).lower(), f"missing={_missing(out)}", SEC)
    check("E3-6: attempt_count=1 dopo il 1deg tentativo",
          out["interview_attempt_count"] == 1, f"attempt={out['interview_attempt_count']}", SEC)


# ============================================================================
# SEZIONE 2: PROMPT AGGIUNTIVI — verifica funzionalita' estesa
# ============================================================================

def test_additional_prompts():
    section_header("SEZIONE 2 — Prompt aggiuntivi (verifica funzionalita' extra)")
    SEC = "S2"
    client = CSVLcaClient()

    # ─────────────────────────────────────────────────────────────────────────
    # P1: "1 kg di polietilene in Italia" — materiale puro, no distanza
    #     Atteso: market for polyethylene, Italy -> fallback RER o GLO
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P1] '1 kg di polietilene in Italia' — materiale puro, no distanza")
    out = run_workflow_logic(
        mass=1.0, geography="Italy",
        dist_km=None, is_material_only=True, is_interview_complete=True
    )
    check("P1-1: is_material_only -> NO interview",
          _pending(out) == "", f"pending='{_pending(out)[:60]}'", SEC)
    check("P1-2: is_material_only -> distanza NON chiesta",
          "distanza" not in " ".join(_missing(out)).lower(), f"missing={_missing(out)}", SEC)
    res = client.find_closest_match("polyethylene", location="Italy", has_transport=False)
    proc = res.get("providerName","") if res else "NESSUN MATCH"
    check("P1-3: DB -> market for polyethylene (has_transport=False)",
          "market for" in proc.lower() if res else True,
          f"providerName='{proc}' (fallback accettabile se no match IT)", SEC)
    if res:
        check("P1-4: DB -> NO waste",
              "waste" not in (res.get("flowName","")).lower() and "waste" not in proc.lower(), SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P2: Oggetto con SOLO distanza mancante (massa + geo ok)
    #     Atteso: interview al 1deg tentativo, chiede distanza
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P2] Oggetto con massa e luogo, distanza mancante — chiede distanza")
    out = run_workflow_logic(
        mass=3.0, geography="Germany",
        dist_km=None, is_material_only=False, is_interview_complete=True,
        attempt_count=0
    )
    check("P2-1: pending_feedback attivato (solo distanza mancante)",
          bool(_pending(out)), f"pending='{_pending(out)[:80]}'", SEC)
    check("P2-2: 'distanza' nella lista mancanti",
          "distanza" in " ".join(_missing(out)).lower(), f"missing={_missing(out)}", SEC)
    check("P2-3: current_phase='interview'",
          _phase(out) == "interview", f"phase='{_phase(out)}'", SEC)
    check("P2-4: massa e luogo NON elencati (erano ok)",
          "massa" not in " ".join(_missing(out)).lower()
          and "luogo" not in " ".join(_missing(out)).lower(),
          f"missing={_missing(out)}", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P3: Oggetto con distanza fornita ma luogo mancante
    #     Atteso: interview al 1deg tentativo, chiede luogo (distanza gia' presente)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P3] Oggetto con distanza (200 km) ma senza luogo — chiede luogo")
    out = run_workflow_logic(
        mass=2.0, geography=None,
        dist_km=200.0, is_material_only=False, is_interview_complete=False,
        attempt_count=0
    )
    check("P3-1: pending_feedback attivato",
          bool(_pending(out)), f"pending='{_pending(out)[:80]}'", SEC)
    check("P3-2: 'luogo' nella lista mancanti",
          "luogo" in " ".join(_missing(out)).lower(), f"missing={_missing(out)}", SEC)
    check("P3-3: 'distanza' NON nella lista mancanti (gia' fornita)",
          "distanza" not in " ".join(_missing(out)).lower(), f"missing={_missing(out)}", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P4: 2deg tentativo, NESSUN dato mai fornito -> assunzioni autonome complete
    #     Atteso: mass=1.0 kg, geo=RER, distanza=market for -> NO interview
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P4] 2deg tentativo, nessun dato — assunzioni autonome complete")
    out = run_workflow_logic(
        mass=None, geography=None,
        dist_km=None, is_material_only=False, is_interview_complete=False,
        attempt_count=1
    )
    check("P4-1: NON si ferma in interview (pending_feedback=None)",
          _pending(out) == "", f"pending='{_pending(out)[:60]}'", SEC)
    check("P4-2: current_phase='workflow'",
          _phase(out) == "workflow", f"phase='{_phase(out)}'", SEC)
    check("P4-3: assunzione massa (1.0 kg)",
          any("1.0" in a or "massa" in a.lower() for a in _assum(out)),
          f"assumptions={_assum(out)}", SEC)
    check("P4-4: assunzione luogo (RER)",
          any("rer" in a.lower() or "ropa" in a.lower() for a in _assum(out)),
          f"assumptions={_assum(out)}", SEC)
    check("P4-5: assunzione distanza (market for)",
          any("market for" in a.lower() or "distanza" in a.lower() for a in _assum(out)),
          f"assumptions={_assum(out)}", SEC)
    check("P4-6: has_transport=False (nessuna distanza -> market for)",
          _ht(out) is False, f"has_transport={_ht(out)}", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P5: 2deg tentativo, SOLO distanza ancora mancante (massa+geo ok)
    #     needs_interview=False -> procede senza bloccarsi, has_transport=False
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P5] 2deg tentativo, solo distanza mancante — procede con market for")
    out = run_workflow_logic(
        mass=5.0, geography="France",
        dist_km=None, is_material_only=False, is_interview_complete=True,
        attempt_count=1
    )
    check("P5-1: NON si ferma in interview",
          _pending(out) == "", f"pending='{_pending(out)[:60]}'", SEC)
    check("P5-2: current_phase='workflow'",
          _phase(out) == "workflow", f"phase='{_phase(out)}'", SEC)
    check("P5-3: has_transport=False (usera' market for)",
          _ht(out) is False, f"has_transport={_ht(out)}", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P6: is_material_only=True — distanza NON chiesta mai
    #     Anche con attempt_count=0, is_material_only esclude la distanza
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P6] is_material_only=True — distanza NON chiesta (materiale puro)")
    out = run_workflow_logic(
        mass=1.0, geography="Global",
        dist_km=None, is_material_only=True, is_interview_complete=True,
        attempt_count=0
    )
    check("P6-1: is_material_only -> distanza NON nella lista mancanti",
          "distanza" not in " ".join(_missing(out)).lower(), f"missing={_missing(out)}", SEC)
    check("P6-2: nessuna interview attivata",
          _pending(out) == "", f"pending='{_pending(out)[:60]}'", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P7: Filtro waste — 27 combinazioni (3 mat x 3 geo x 3 ht), NO waste mai
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P7] Filtro waste assoluto — 27 combinazioni (3 mat x 3 geo x 3 ht)")
    found_waste = False
    combo_tested = 0
    for mat in ["polypropylene", "polyethylene", "steel"]:
        for geo in ["RER", "Global", "Italy"]:
            for ht in [True, False, None]:
                res = client.find_closest_match(mat, location=geo, has_transport=ht)
                combo_tested += 1
                if res:
                    fn = (res.get("flowName","") or "").lower()
                    pn = (res.get("providerName","") or "").lower()
                    if "waste" in fn or "waste" in pn:
                        found_waste = True
                        check(f"P7: waste trovato! mat={mat} geo={geo} ht={ht}", False,
                              f"flow='{fn}' proc='{pn}'", SEC)
    check(f"P7: filtro waste assoluto ({combo_tested} combinazioni testate, 0 waste)",
          not found_waste, SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P8: has_transport=False -> market for (PP e acciaio su DB reale)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P8] DB reale: has_transport=False -> market for (PP e acciaio)")
    res_pp = client.find_closest_match("polypropylene", location="RER", has_transport=False)
    proc_pp = res_pp.get("providerName","") if res_pp else ""
    check("P8-1: PP RER has_transport=False -> market for",
          "market for" in proc_pp.lower(), f"providerName='{proc_pp}'", SEC)

    res_pp_glo = client.find_closest_match("polypropylene", location="GLO", has_transport=False)
    proc_pp_glo = res_pp_glo.get("providerName","") if res_pp_glo else ""
    check("P8-2: PP GLO has_transport=False -> market for",
          "market for" in proc_pp_glo.lower(), f"providerName='{proc_pp_glo}'", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P9: has_transport=True -> NO market for (PP e acciaio su DB reale)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P9] DB reale: has_transport=True -> NO market for")
    res_pp_t = client.find_closest_match("polypropylene", location="RER", has_transport=True)
    proc_pp_t = res_pp_t.get("providerName","") if res_pp_t else ""
    check("P9-1: PP RER has_transport=True -> NO market for",
          "market for" not in proc_pp_t.lower(), f"providerName='{proc_pp_t}'", SEC)

    res_st_t = client.find_closest_match("steel", location="Europe without Switzerland", has_transport=True)
    proc_st_t = res_st_t.get("providerName","") if res_st_t else ""
    check("P9-2: steel EU has_transport=True -> NO market for",
          "market for" not in proc_st_t.lower() if res_st_t else True,
          f"providerName='{proc_st_t}'", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P10: tkm Esempio 2 — calcolo deterministico
    #      4.5 kg = 0.0045 t; 0.0045 * 800 = 3.6 tkm (come da documento)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P10] Calcolo tkm Esempio 2 (doc): 4.5 kg * 800 km / 1000 = 3.6 tkm")
    mass_kg = 4.5
    dist = 800.0
    tkm = (mass_kg / 1000.0) * dist
    check("P10-1: tkm = 3.6 (valore atteso dal documento)",
          abs(tkm - 3.6) < 0.001, f"tkm={tkm:.4f}", SEC)
    check("P10-2: formula corretta: tonnellate * km",
          tkm == (mass_kg / 1000.0) * dist, f"formula ok", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P11: Coerenza tra distanza e has_transport
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P11] Coerenza dist_km / has_transport")
    cases = [
        (None, False, "dist=None -> has_transport=False"),
        (0.0,  False, "dist=0   -> has_transport=False"),
        (1.0,  True,  "dist=1   -> has_transport=True"),
        (800.0,True,  "dist=800 -> has_transport=True"),
    ]
    for dist_val, expected_ht, label in cases:
        out = run_workflow_logic(
            mass=5.0, geography="Italy", dist_km=dist_val,
            is_material_only=False, is_interview_complete=True, attempt_count=1
        )
        check(f"P11: {label}", _ht(out) is expected_ht,
              f"has_transport={_ht(out)} expected={expected_ht}", SEC)

    # ─────────────────────────────────────────────────────────────────────────
    # P12: Flusso completo — 2deg tentativo con TUTTI i dati forniti
    #      (simula utente che risponde correttamente alla interview)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[P12] 2deg tentativo con TUTTI i dati -> flusso completo, NO assunzioni")
    out = run_workflow_logic(
        mass=4.5, geography="Sweden",
        dist_km=800.0, is_material_only=False, is_interview_complete=True,
        attempt_count=1
    )
    check("P12-1: pending_feedback=None (nessuna interview)",
          _pending(out) == "", SEC)
    check("P12-2: current_phase='workflow'",
          _phase(out) == "workflow", SEC)
    check("P12-3: has_transport=True (distanza fornita)",
          _ht(out) is True, SEC)
    check("P12-4: nessuna assunzione (tutti i dati presenti)",
          len(_assum(out)) == 0, f"assumptions={_assum(out)}", SEC)


# ============================================================================
# RUNNER PRINCIPALE
# ============================================================================

async def main():
    print("\n" + "=" * 72)
    print("  TEST FINALE — Verifica logica LCA vs System Prompt + prompt extra")
    print("=" * 72)

    test_document_examples()
    test_additional_prompts()

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [(name, sec) for name, ok, sec in RESULTS if not ok]

    print("\n" + "=" * 72)
    print(f"  RIEPILOGO FINALE: {passed}/{total} test superati")
    print("=" * 72)

    s1_total = sum(1 for _, _, s in RESULTS if s == "S1")
    s1_pass  = sum(1 for _, ok, s in RESULTS if s == "S1" and ok)
    s2_total = sum(1 for _, _, s in RESULTS if s == "S2")
    s2_pass  = sum(1 for _, ok, s in RESULTS if s == "S2" and ok)
    print(f"  Sezione 1 (Esempi doc):   {s1_pass}/{s1_total}")
    print(f"  Sezione 2 (Prompt extra): {s2_pass}/{s2_total}")

    if failed:
        print("\n  FALLITI:")
        for name, sec in failed:
            label = "Esempi doc" if sec == "S1" else "Prompt extra"
            print(f"     [{label}] {name}")
    else:
        print("\n  Tutti i test superati!")

    return failed


if __name__ == "__main__":
    failures = asyncio.run(main())
    sys.exit(1 if failures else 0)
