"""HTML report generator for completed AgentState output."""
from __future__ import annotations

import datetime
import logging
from html import escape

logger = logging.getLogger(__name__)


def generate_html_report(state: dict) -> str:
    """Return a self-contained HTML string from a completed AgentState dict."""
    user_input = escape(state.get("user_input") or "N/A")
    bom: list[dict] = state.get("bom") or []
    lca_results: list[dict] = state.get("lca_results") or []
    mcda_scores: list[dict] = state.get("mcda_scores") or []
    workflow_steps: list[dict] = state.get("workflow_steps") or []
    assumptions_list: list[str] = state.get("assumptions_list") or []
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    task_type = (state.get("constraints") or {}).get("task_type", "optimization")

    # ── Estrazione processName Ecoinvent per audit LCA ─────────────────────
    # Il processName reale della colonna Excel viene propagato da nodes.py
    # come 'ecoinvent_process_name' dentro ogni entry di lca_results.
    # Per il baseline prendiamo il primo componente non-Transport con il campo.
    # Per l'alternativa ottimizzata prendiamo il best_alternative del primo
    # componente MCDA che ha un'alternativa vincitrice.
    baseline_ecoinvent_activity = "N/A"
    for r in lca_results:
        if r.get("component_name", "") != "Transport" and r.get("ecoinvent_process_name"):
            baseline_ecoinvent_activity = r["ecoinvent_process_name"]
            break

    optimized_ecoinvent_activity = "N/A"
    for comp in mcda_scores:
        best = comp.get("best_alternative")
        if best and best.get("ecoinvent_process_name"):
            optimized_ecoinvent_activity = best["ecoinvent_process_name"]
            break
    # Fallback: cerca dentro lca_results → alternatives
    if optimized_ecoinvent_activity == "N/A":
        for r in lca_results:
            for alt in r.get("alternatives", []):
                if alt.get("ecoinvent_process_name"):
                    optimized_ecoinvent_activity = alt["ecoinvent_process_name"]
                    break
            if optimized_ecoinvent_activity != "N/A":
                break
    if task_type == "modeling":
        t_report = "Report di Analisi Impatto LCA"
    else:
        t_report = "Report di Ottimizzazione Sostenibile"
        
    t_generated = "Generato il"
    t_powered = "Powered by LangGraph & LCA data"
    t_bom = "Distinta Base Originale (BOM)"
    t_comp = "COMPONENT"
    t_mat = "MATERIAL"
    t_weight = "WEIGHT"
    t_role = "FUNCTIONAL ROLE"
    t_unit_imp = "IMPATTO UNITARIO"
    t_tot_imp = "IMPATTO TOTALE"
    t_opt_sum = "Riepilogo Ottimizzazione"
    t_orig_mat = "Materiale Originale"
    t_rec_alt = "Alternativa Consigliata"
    t_co2_red = "Riduzione CO&#8322;"
    t_just = "Giustificazione"
    t_co2_impact = "Impatto CO&#8322;"
    t_orig_co2 = "CO&#8322; Totale Originale"
    t_opt_co2 = "CO&#8322; Totale Ottimizzato"
    t_red_over = "Riduzione Totale"
    t_improv = "miglioramento"
    t_break = "Dettaglio per Componente"
    t_orig_co2_kg = "CO&#8322; Originale (kg)"
    t_opt_co2_kg = "CO&#8322; Ottimizzato (kg)"
    t_red = "Riduzione"
    t_workflow = "Tabella dei Processi (Workflow)"
    t_phase = "Fase"
    t_proc = "Processo Manifatturiero"
    t_out = "Output Atteso"
    t_assump = "Assunzioni e Dati Esterni (Ricerca Online)"


    orig_co2: dict[str, float] = {
        r["component_name"]: r["original_scores"]["environmental_impact"]
        for r in lca_results
    }

    total_orig = sum(orig_co2.values())
    total_opt = 0.0
    for comp in mcda_scores:
        orig = orig_co2.get(comp["component_name"], 0.0)
        best = comp.get("best_alternative")
        total_opt += orig * (1 - best["impact_reduction_pct"] / 100) if best else orig

    reduction_pct = ((total_orig - total_opt) / total_orig * 100) if total_orig else 0.0

    def _bom_rows() -> str:
        display_bom = []
        lca_map = {r.get("component_name", ""): r for r in lca_results}

        for item in bom:
            comp_name = item.get("name", "")
            weight = item.get("weight_kg", 1.0)
            func_role = item.get("functional_role", "Material")
            
            if task_type == "modeling":
                mat_key = f"{comp_name} (Material)"
                proc_key = f"{comp_name} (Manufacturing)"
                
                mat_lca = lca_map.get(mat_key, {})
                proc_lca = lca_map.get(proc_key, {})
                
                mat_scores = mat_lca.get("original_scores", {})
                proc_scores = proc_lca.get("original_scores", {})
                
                display_bom.append({
                    "component": comp_name,
                    "material": mat_lca.get("original_material", item.get("material", "")),
                    "weight": weight,
                    "role": func_role,
                    "unit_impact": mat_scores.get("unit_material_impact", 0.0),
                    "total_impact": mat_scores.get("environmental_impact", 0.0)
                })
                
                proc_name = item.get("manufacturing_process")
                if proc_name and str(proc_name).lower() != "none":
                    if isinstance(proc_name, list):
                        for p in proc_name:
                            display_bom.append({
                                "component": comp_name,
                                "material": p,
                                "weight": weight,
                                "role": "Manufacturing",
                                "unit_impact": proc_scores.get("unit_material_impact", 0.0),
                                "total_impact": proc_scores.get("environmental_impact", 0.0)
                            })
                    else:
                        display_bom.append({
                            "component": comp_name,
                            "material": proc_name,
                            "weight": weight,
                            "role": "Manufacturing",
                            "unit_impact": proc_scores.get("unit_material_impact", 0.0),
                            "total_impact": proc_scores.get("environmental_impact", 0.0)
                        })
            else:
                comp_lca = lca_map.get(comp_name, {})
                scores = comp_lca.get("original_scores", {})
                
                # Material row
                mat_total = scores.get("environmental_impact", 0.0) - scores.get("process_total_impact", 0.0) if "process_total_impact" in scores else scores.get("environmental_impact", 0.0)
                display_bom.append({
                    "component": comp_name,
                    "material": comp_lca.get("original_material", item.get("material", "")),
                    "weight": weight,
                    "role": func_role,
                    "unit_impact": scores.get("unit_material_impact", 0.0),
                    "total_impact": mat_total
                })
                
                # Process row
                proc_name = item.get("manufacturing_process")
                if proc_name and str(proc_name).lower() != "none":
                    proc_unit = scores.get("process_unit_impact", 0.0)
                    proc_total = scores.get("process_total_impact", 0.0)
                    if isinstance(proc_name, list):
                        for p in proc_name:
                            display_bom.append({
                                "component": comp_name,
                                "material": p,
                                "weight": weight,
                                "role": "Manufacturing",
                                "unit_impact": proc_unit,
                                "total_impact": proc_total
                            })
                    else:
                        display_bom.append({
                            "component": comp_name,
                            "material": proc_name,
                            "weight": weight,
                            "role": "Manufacturing",
                            "unit_impact": proc_unit,
                            "total_impact": proc_total
                        })

        # Trasporto
        transport_comp = lca_map.get("Transport")
        if transport_comp:
            t_mode = state.get("logistics_data", {}).get("transport_mode", "lorry")
            mat_name = transport_comp.get("original_material", f"Trasporto {t_mode}")
            scores = transport_comp.get("original_scores", {})
            amount = scores.get("amount", "-")
            total_imp = scores.get("environmental_impact", 0.0)
            
            unit_imp = 0.0
            if amount != "-" and float(amount) > 0:
                unit_imp = total_imp / float(amount)
                
            display_bom.append({
                "component": "Trasporto",
                "material": mat_name,
                "weight": amount,
                "role": "Logistics",
                "unit_impact": unit_imp,
                "total_impact": total_imp
            })
        else:
            dist = state.get("logistics_data", {}).get("distance_km", 0.0)
            if dist:
                t_mode = state.get("logistics_data", {}).get("transport_mode", "lorry")
                display_bom.append({
                    "component": "Trasporto",
                    "material": f"Trasporto {t_mode}",
                    "weight": "-",
                    "role": f"Logistics ({dist} km)",
                    "unit_impact": 0.0,
                    "total_impact": 0.0
                })

        rows = ""
        for row in display_bom:
            w = f"{row['weight']:.3f}" if isinstance(row['weight'], (int, float)) else row['weight']
            ui = f"{row['unit_impact']:.3f}" if isinstance(row['unit_impact'], (int, float)) else row['unit_impact']
            ti = f"{row['total_impact']:.3f}" if isinstance(row['total_impact'], (int, float)) else row['total_impact']
            rows += (
                f"<tr><td>{row['component']}</td>"
                f"<td>{row['material']}</td>"
                f"<td>{w}</td>"
                f"<td>{row['role']}</td>"
                f"<td>{ui}</td>"
                f"<td>{ti}</td></tr>"
            )
        return rows

    def _opt_rows() -> str:
        rows = ""
        for comp in mcda_scores:
            best = comp.get("best_alternative")
            if best:
                rows += (
                    f"<tr><td>{comp['component_name']}</td>"
                    f"<td>{comp['original_material']}</td>"
                    f"<td>{best['name']}</td>"
                    f"<td>{best['impact_reduction_pct']:.1f}%</td>"
                    f"<td>{best['mcda_score']:.3f}</td>"
                    f"<td>{best.get('justification', '')}</td></tr>"
                )
        return rows

    def _impact_rows() -> str:
        rows = ""
        for comp in mcda_scores:
            name = comp["component_name"]
            orig = orig_co2.get(name, 0.0)
            best = comp.get("best_alternative")
            opt = orig * (1 - best["impact_reduction_pct"] / 100) if best else orig
            delta = ((orig - opt) / orig * 100) if orig else 0.0
            
            orig = round(orig, 3)
            opt = round(opt, 3)
            delta = round(delta, 1)
            
            rows += (
                f"<tr><td>{name}</td>"
                f"<td>{orig}</td>"
                f"<td>{opt}</td>"
                f"<td>{delta}%</td></tr>"
            )
        return rows

    def _workflow_rows() -> str:
        rows = ""
        combined_steps = []
        
        for item in bom:
            proc = item.get('manufacturing_process')
            if proc:
                w = item.get('weight_kg', '')
                combined_steps.append({
                    'process_name': proc,
                    'process_output': f"{w} kg" if str(w).strip() else "-"
                })
                
        for step in workflow_steps:
            if isinstance(step, dict):
                p_name = step.get('process_name', '')
                if not any(isinstance(s, dict) and s.get('process_name') == p_name for s in combined_steps):
                    combined_steps.append(step)
            else:
                combined_steps.append(step)

        for i, step in enumerate(combined_steps, 1):
            if isinstance(step, dict):
                rows += f"<tr><td>{i}</td><td>{step.get('process_name', '')}</td><td>{step.get('process_output', '')}</td></tr>"
            else:
                rows += f"<tr><td>{i}</td><td colspan='2'>{step}</td></tr>"
        return rows

    def _matched_processes_section() -> str:
        """Build an HTML table of all ecoinvent process names matched during LCA."""
        rows = ""
        for r in lca_results:
            comp = r.get("component_name", "—")
            proc = r.get("ecoinvent_process_name", "")
            # Guard difensivo: salta righe senza processo o con processo "none"
            # (materiali grezzi senza manifattura) → niente righe di audit fittizie.
            if not proc or str(proc).strip().lower() == "none":
                continue
            orig_mat = r.get("original_material", "—")
            rows += (
                f"<tr><td>{comp}</td>"
                f"<td>{orig_mat}</td>"
                f"<td style='font-family:monospace; font-size:0.85em;'>{proc}</td></tr>"
            )
            # Anomalia A: riga della LAVORAZIONE MANIFATTURIERA.
            # In modalità optimization la fase manifattura è ripiegata nel singolo
            # componente, quindi non comparirebbe come riga separata: la rendiamo qui
            # leggendo i campi dedicati propagati da lca_validator, così l'audit mostra
            # tutte e tre le fasi (Materiale, Manifattura, Trasporto) come in Streamlit.
            mfg_proc = r.get("manufacturing_ecoinvent_process_name", "")
            if mfg_proc and str(mfg_proc).strip().lower() != "none":
                mfg_label = r.get("manufacturing_process_label") or "Manufacturing"
                rows += (
                    f"<tr><td>{comp} <em>(manuf.)</em></td>"
                    f"<td>{mfg_label}</td>"
                    f"<td style='font-family:monospace; font-size:0.85em;'>{mfg_proc}</td></tr>"
                )
            # Also list alternatives if present
            for alt in r.get("alternatives", []):
                alt_proc = alt.get("ecoinvent_process_name", "")
                if alt_proc:
                    alt_name = alt.get("name", "—")
                    rows += (
                        f"<tr style='background:#f0fff4;'>"
                        f"<td>{comp} <em>(alt.)</em></td>"
                        f"<td>{alt_name}</td>"
                        f"<td style='font-family:monospace; font-size:0.85em;'>{alt_proc}</td></tr>"
                    )
        if not rows:
            return ""
        return (
            "<h2>&#128279; Processi Ecoinvent Matchati</h2>"
            "<p style='font-size:0.88em; color:#555; margin-bottom:12px;'>"
            "Nomi completi dei processi del database Ecoinvent utilizzati nel calcolo LCA."
            "</p>"
            "<table>"
            "<thead><tr>"
            "<th>Componente</th>"
            "<th>Materiale / Processo</th>"
            "<th>Nome Ecoinvent Completo</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
        )

    def _ecoinvent_id_references() -> str:
        """Sezione 'per esperti': l'ID ecoinvent (colonna 'id' del dataset) di OGNI
        record usato nel calcolo — materiale originale, processo, alternative e
        trasporto. Permette di incollare l'id nel database ecoinvent e ritrovare
        l'attività esatta. Tracciabilità completa per chi sa cosa cercare."""

        def _id_row(component: str, tipo: str, name: str, ecoid: str, highlight: bool = False) -> str:
            bg = " style='background:#f0fff4;'" if highlight else ""
            return (
                f"<tr{bg}>"
                f"<td>{component}</td>"
                f"<td>{tipo}</td>"
                f"<td>{name}</td>"
                f"<td style=\"font-family:monospace; font-size:0.82em; word-break:break-all;\">{ecoid}</td>"
                "</tr>"
            )

        rows = ""
        for r in lca_results:
            comp = r.get("component_name", "—")
            if comp == "Transport":
                tipo = "Trasporto"
            elif comp.endswith("(Manufacturing)"):
                tipo = "Processo"
            else:
                tipo = "Materiale"

            # Riga principale (materiale / processo modeling / trasporto)
            eid = r.get("ecoinvent_id")
            if eid:
                rows += _id_row(comp, tipo, r.get("ecoinvent_process_name", "—"), eid)

            # Processo manifattura ripiegato nel componente (modalità optimization)
            mfg_id = r.get("manufacturing_ecoinvent_id")
            if mfg_id:
                rows += _id_row(
                    f"{comp} (manuf.)", "Processo",
                    r.get("manufacturing_ecoinvent_process_name", "—"), mfg_id,
                )

            # Alternative (materiale ottimizzato): nome + id, evidenziate in verde
            for alt in r.get("alternatives", []):
                aid = alt.get("ecoinvent_id")
                if aid:
                    rows += _id_row(
                        f"{comp} (alt.)", "Alternativa",
                        alt.get("ecoinvent_process_name", alt.get("name", "—")), aid,
                        highlight=True,
                    )

        if not rows:
            return ""
        return (
            "<h2>&#128273; Riferimenti ID Ecoinvent <span style='font-size:0.6em; color:#888; font-weight:400;'>(sezione per esperti)</span></h2>"
            "<p style='font-size:0.88em; color:#555; margin-bottom:12px;'>"
            "ID univoco (colonna <code>id</code> del dataset ecoinvent) di ogni record usato nel calcolo: "
            "materiale originale, processo manifatturiero, alternative ottimizzate e trasporto. "
            "Incolla un id nel database ecoinvent per ritrovare l'attivit&agrave; esatta."
            "</p>"
            "<table>"
            "<thead><tr>"
            "<th>Componente</th>"
            "<th>Tipo</th>"
            "<th>Nome Ecoinvent</th>"
            "<th>ID Ecoinvent</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table>"
        )

    def _assumptions_list() -> str:
        constraints = state.get("constraints", {})
        html = ""
        if constraints:
            html += "<strong>Vincoli Estratti (Constraints):</strong><ul>"
            for k, v in constraints.items():
                html += f"<li><strong>{escape(str(k))}</strong>: {escape(str(v))}</li>"
            html += "</ul><hr style='border:none; border-top:1px solid #ddd; margin:12px 0;'/>"

        unique_assumptions = list(dict.fromkeys(assumptions_list))
        filtered_assumptions = [
            a for a in unique_assumptions
            if "nessuna assunzione" not in a.lower() and "no assumption" not in a.lower()
        ]
        if not filtered_assumptions:
            html += "<p>Nessun dato esterno o assunzione aggiuntiva trovata.</p>"
        else:
            html += "<strong>Assunzioni del Modello:</strong><ul>"
            for a in filtered_assumptions:
                html += f"<li>{escape(str(a))}</li>"
            html += "</ul>"

        # ── Blocco Audit Ecoinvent ad Alta Visibilità ────────────────────────
        # Mostra le attività Ecoinvent esatte usate nel calcolo LCA:
        # tracciabilità completa per baseline e alternativa ottimizzata.
        html += (
            "<hr style='border:none; border-top:2px solid #0f3460; margin:18px 0;'/>"
            "<strong style='color:#0f3460; font-size:1.05em;'>&#128270; Attività Ecoinvent — Riferimenti per Audit LCA</strong>"
            "<div style='margin-top:10px; padding:14px 18px; background:#f0f4ff; "
            "border-left:4px solid #0f3460; border-radius:0 6px 6px 0; font-size:0.92em;'>"
        )
        html += (
            f"<p style='margin:6px 0;'>"
            f"<span style='display:inline-block; min-width:240px; font-weight:600; color:#555;'>"
            f"&#9654; Attivit&agrave; Ecoinvent Baseline:</span> "
            f"<code style='background:#e8eaf6; padding:2px 8px; border-radius:4px; color:#1a237e;'>"
            f"{baseline_ecoinvent_activity}</code></p>"
        )
        if task_type == "optimization":
            html += (
                f"<p style='margin:6px 0;'>"
                f"<span style='display:inline-block; min-width:240px; font-weight:600; color:#555;'>"
                f"&#9654; Attivit&agrave; Ecoinvent Ottimizzata:</span> "
                f"<code style='background:#e8f5e9; padding:2px 8px; border-radius:4px; color:#1b5e20;'>"
                f"{optimized_ecoinvent_activity}</code></p>"
            )
        html += "</div>"

        return html

    if task_type == "modeling":
        opt_html = f"""
<h2>&#127757; {t_co2_impact}</h2>
<div class="cards">
  <div class="card orig" style="flex: unset; width: 300px;">
    <div class="label">{t_orig_co2}</div>
    <div class="value">{total_orig:.3f}</div>
    <div class="label">kg CO&#8322;-eq</div>
  </div>
</div>
"""
    else:
        opt_html = f"""
<h2>&#128300; {t_opt_sum}</h2>
<table>
  <thead>
    <tr>
      <th>{t_comp}</th>
      <th>{t_orig_mat}</th>
      <th>{t_rec_alt}</th>
      <th>{t_co2_red}</th>
      <th>MCDA Score</th>
      <th>{t_just}</th>
    </tr>
  </thead>
  <tbody>{_opt_rows()}</tbody>
</table>

<h2>&#127757; {t_co2_impact}</h2>
<div class="cards">
  <div class="card orig">
    <div class="label">{t_orig_co2}</div>
    <div class="value">{total_orig:.3f}</div>
    <div class="label">kg CO&#8322;-eq</div>
  </div>
  <div class="card opt">
    <div class="label">{t_opt_co2}</div>
    <div class="value">{total_opt:.3f}</div>
    <div class="label">kg CO&#8322;-eq</div>
  </div>
  <div class="card delta">
    <div class="label">{t_red_over}</div>
    <div class="value">{reduction_pct:.1f}%</div>
    <div class="label">{t_improv}</div>
  </div>
</div>

<h3 style="margin-top:32px;color:#0f3460">{t_break}</h3>
<table>
  <thead>
    <tr>
      <th>{t_comp}</th>
      <th>{t_orig_co2_kg}</th>
      <th>{t_opt_co2_kg}</th>
      <th>{t_red}</th>
    </tr>
  </thead>
  <tbody>{_impact_rows()}</tbody>
</table>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{t_report}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
    background: #ffffff;
    color: #1a1a2e;
    margin: 0;
    padding: 36px 56px;
    line-height: 1.65;
  }}
  h1 {{
    color: #16213e;
    border-bottom: 3px solid #33b86c;
    padding-bottom: 12px;
    margin-bottom: 8px;
  }}
  h2 {{
    color: #0f3460;
    margin-top: 40px;
    margin-bottom: 4px;
  }}
  .subtitle {{
    color: #666;
    font-size: 0.88em;
    margin-bottom: 28px;
  }}
  .meta {{
    background: #f4f8f4;
    border-left: 4px solid #33b86c;
    padding: 14px 20px;
    border-radius: 0 6px 6px 0;
    margin: 20px 0 32px;
    font-size: 0.95em;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 14px;
    font-size: 0.9em;
  }}
  thead tr {{
    background: #16213e;
    color: #ffffff;
  }}
  th, td {{
    padding: 10px 14px;
    text-align: left;
    border: 1px solid #dde4e6;
  }}
  tbody tr:nth-child(even) {{ background: #f9fbfc; }}
  tbody tr:hover {{ background: #e8f5ed; }}
  .cards {{
    display: flex;
    gap: 20px;
    margin-top: 18px;
    flex-wrap: wrap;
  }}
  .card {{
    flex: 1;
    min-width: 160px;
    border: 1px solid #dde4e6;
    border-radius: 8px;
    padding: 18px 22px;
    text-align: center;
  }}
  .card .label {{
    font-size: 0.78em;
    color: #777;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }}
  .card .value {{
    font-size: 2em;
    font-weight: 700;
    margin: 8px 0 4px;
  }}
  .card.orig .value {{ color: #e05555; }}
  .card.opt  .value {{ color: #33b86c; }}
  .card.delta .value {{ color: #0f3460; }}
  footer {{
    margin-top: 52px;
    font-size: 0.75em;
    color: #aaa;
    border-top: 1px solid #eee;
    padding-top: 14px;
  }}
  @media print {{
    body {{ padding: 20px; background: #fff; color: #000; }}
    .card {{ page-break-inside: avoid; }}
    table {{ page-break-inside: auto; }}
    tr {{ page-break-inside: avoid; page-break-after: auto; }}
  }}
</style>
</head>
<body>

<h1>&#127807; {t_report}</h1>
<div class="subtitle">{t_generated} {generated_at} &nbsp;&middot;&nbsp; {t_powered}</div>

<div class="meta">
  <strong>Descrizione Prodotto:</strong> {user_input}
</div>

<h2>&#128203; {t_bom}</h2>
<table>
  <thead><tr><th>{t_comp}</th><th>{t_mat}</th><th>{t_weight}</th><th>{t_role}</th><th>{t_unit_imp}</th><th>{t_tot_imp}</th></tr></thead>
  <tbody>{_bom_rows()}</tbody>
</table>

{opt_html}

<h2>&#128736;&#65039; {t_workflow}</h2>
<table>
  <thead>
    <tr>
      <th>{t_phase}</th>
      <th>{t_proc}</th>
      <th>{t_out}</th>
    </tr>
  </thead>
  <tbody>{_workflow_rows()}</tbody>
</table>

<h2>&#128269; {t_assump}</h2>
<div class="meta" style="border-left-color: #f59e0b; background: #fffdf5;">
  {_assumptions_list()}
</div>

{_matched_processes_section()}

{_ecoinvent_id_references()}

<footer>
  Sustainable Product Optimization Agent &nbsp;&middot;&nbsp; LangGraph pipeline &nbsp;&middot;&nbsp; {generated_at}
</footer>

</body>
</html>"""


def generate_pdf_report(state: dict) -> bytes | None:
    """Convert the HTML report to PDF bytes using WeasyPrint.

    Returns None if WeasyPrint or its system dependencies (Cairo/Pango/GTK3)
    are unavailable, so callers can degrade gracefully.
    """
    try:
        from weasyprint import HTML  # noqa: PLC0415
    except Exception:
        print("PDF Export disabled (missing dependencies), HTML available")
        return None

    html_string = generate_html_report(state)
    try:
        pdf_bytes: bytes = HTML(string=html_string).write_pdf()
        return pdf_bytes
    except Exception as exc:
        logger.error("PDF generation failed: %s", exc)
        return None
