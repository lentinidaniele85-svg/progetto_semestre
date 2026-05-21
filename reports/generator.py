"""HTML report generator for completed AgentState output."""
from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)


def generate_html_report(state: dict) -> str:
    """Return a self-contained HTML string from a completed AgentState dict."""
    user_input = state.get("user_input") or "N/A"
    bom: list[dict] = state.get("bom") or []
    lca_results: list[dict] = state.get("lca_results") or []
    mcda_scores: list[dict] = state.get("mcda_scores") or []
    workflow_steps: list[dict] = state.get("workflow_steps") or []
    assumptions_list: list[str] = state.get("assumptions_list") or []
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    ita = False
    ita_words = {"di", "a", "da", "in", "con", "su", "per", "tra", "fra", "il", "lo", "la", "i", "gli", "le", "un", "una", "e", "o", "ma", "che", "non", "si", "mi", "ti", "ci", "vi", "kg", "cina", "propilene", "plastica"}
    words = set(user_input.lower().replace(".", " ").replace(",", " ").split())
    if len(words.intersection(ita_words)) > 0:
        ita = True

    task_type = (state.get("constraints") or {}).get("task_type", "optimization")
    
    if task_type == "modeling":
        t_report = "Report di Analisi Impatto LCA" if ita else "LCA Impact Analysis Report"
    else:
        t_report = "Report di Ottimizzazione Sostenibile" if ita else "Sustainable Product Optimization Report"
    t_generated = "Generato il" if ita else "Generated"
    t_powered = "Powered by LangGraph & LCA data" if ita else "Powered by LangGraph & LCA data"
    t_bom = "Distinta Base Originale (BOM)" if ita else "Original Bill of Materials"
    t_comp = "Componente" if ita else "Component"
    t_mat = "Materiale" if ita else "Material"
    t_weight = "Peso (kg)" if ita else "Weight (kg)"
    t_opt_sum = "Riepilogo Ottimizzazione" if ita else "Optimization Summary"
    t_orig_mat = "Materiale Originale" if ita else "Original Material"
    t_rec_alt = "Alternativa Consigliata" if ita else "Recommended Alternative"
    t_co2_red = "Riduzione CO&#8322;" if ita else "CO&#8322; Reduction"
    t_just = "Giustificazione" if ita else "Justification"
    t_co2_impact = "Impatto CO&#8322;" if ita else "CO&#8322; Impact Comparison"
    t_orig_co2 = "CO&#8322; Totale Originale" if ita else "Original Total CO&#8322;"
    t_opt_co2 = "CO&#8322; Totale Ottimizzato" if ita else "Optimised Total CO&#8322;"
    t_red_over = "Riduzione Totale" if ita else "Overall Reduction"
    t_improv = "miglioramento" if ita else "improvement"
    t_break = "Dettaglio per Componente" if ita else "Per-Component Breakdown"
    t_orig_co2_kg = "CO&#8322; Originale (kg)" if ita else "Original CO&#8322; (kg)"
    t_opt_co2_kg = "CO&#8322; Ottimizzato (kg)" if ita else "Optimised CO&#8322; (kg)"
    t_red = "Riduzione" if ita else "Reduction"
    t_workflow = "Tabella dei Processi (Workflow)" if ita else "Tabella dei Processi (Workflow)"
    t_phase = "Fase" if ita else "Phase"
    t_proc = "Processo Manifatturiero" if ita else "Manufacturing Process"
    t_out = "Output Atteso" if ita else "Expected Output"
    t_assump = "Assunzioni e Dati Esterni (Ricerca Online)" if ita else "Assumptions & External Data (Online Search)"


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
        rows = ""
        for item in bom:
            name = item.get('name', '')
            mat = item.get('material', '')
            w = item.get('weight_kg', '')
            proc = item.get('manufacturing_process', '')
            
            if task_type == "modeling":
                rows += (
                    f"<tr><td>{name} (Material)</td>"
                    f"<td>{mat}</td>"
                    f"<td>{w}</td></tr>"
                )
                if proc:
                    rows += (
                        f"<tr><td>{name} (Manufacturing)</td>"
                        f"<td>{proc}</td>"
                        f"<td>{w}</td></tr>"
                    )
            else:
                rows += (
                    f"<tr><td>{name}</td>"
                    f"<td>{mat}</td>"
                    f"<td>{w}</td></tr>"
                )
        
        # Aggiunta Trasporto alla BOM per modelling
        if task_type == "modeling":
            transport_comp = next((c for c in lca_results if c.get("component_name") == "Transport"), None)
            if transport_comp:
                t_mode = state.get("logistics_data", {}).get("transport_mode", "lorry")
                mat_name = transport_comp.get("original_material", f"{t_mode.capitalize()} transport")
                amount = transport_comp.get("original_scores", {}).get("amount", "-")
                if amount != "-":
                    mat_name += f" (Amount: {amount:.1f})"
                rows += (
                    f"<tr><td>Transport</td>"
                    f"<td>{mat_name}</td>"
                    f"<td>-</td></tr>"
                )
            else:
                dist = state.get("logistics_data", {}).get("distance_km", 0.0)
                if dist:
                    t_mode = state.get("logistics_data", {}).get("transport_mode", "lorry")
                    rows += (
                        f"<tr><td>Transport</td>"
                        f"<td>{t_mode.capitalize()} transport ({dist} km)</td>"
                        f"<td>-</td></tr>"
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
            rows += (
                f"<tr><td>{name}</td>"
                f"<td>{orig:.4f}</td>"
                f"<td>{opt:.4f}</td>"
                f"<td>{delta:.1f}%</td></tr>"
            )
        return rows

    def _workflow_rows() -> str:
        rows = ""
        for i, step in enumerate(workflow_steps, 1):
            if isinstance(step, dict):
                rows += f"<tr><td>{i}</td><td>{step.get('process_name', '')}</td><td>{step.get('process_output', '')}</td></tr>"
            else:
                rows += f"<tr><td>{i}</td><td colspan='2'>{step}</td></tr>"
        return rows

    def _assumptions_list() -> str:
        unique_assumptions = list(dict.fromkeys(assumptions_list))
        filtered_assumptions = [
            a for a in unique_assumptions
            if "nessuna assunzione" not in a.lower() and "no assumption" not in a.lower()
        ]
        if not filtered_assumptions:
            return "<p>Nessun dato esterno o assunzione aggiuntiva trovata.</p>"
        html = "<ul>"
        for a in filtered_assumptions:
            html += f"<li>{a}</li>"
        html += "</ul>"
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
</style>
</head>
<body>

<h1>&#127807; {t_report}</h1>
<div class="subtitle">{t_generated} {generated_at} &nbsp;&middot;&nbsp; {t_powered}</div>

<div class="meta">
  <strong>Product Description:</strong> {user_input}
</div>

<h2>&#128203; {t_bom}</h2>
<table>
  <thead><tr><th>{t_comp}</th><th>{t_mat}</th><th>{t_weight}</th></tr></thead>
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
