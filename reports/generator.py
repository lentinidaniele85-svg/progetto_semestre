"""HTML report generator for completed AgentState output."""
from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)


def generate_html_report(state: dict) -> str:
    """Return a self-contained HTML string from a completed AgentState dict."""
    user_input = state.get("user_input", "N/A")
    bom: list[dict] = state.get("bom", [])
    lca_results: list[dict] = state.get("lca_results", [])
    mcda_scores: list[dict] = state.get("mcda_scores", [])
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    orig_co2: dict[str, float] = {
        r["component_name"]: r["original_scores"]["co2_eq_kg"]
        for r in lca_results
    }

    total_orig = sum(orig_co2.values())
    total_opt = 0.0
    for comp in mcda_scores:
        orig = orig_co2.get(comp["component_name"], 0.0)
        best = comp.get("best_alternative")
        total_opt += orig * (1 - best["co2_reduction_pct"] / 100) if best else orig

    reduction_pct = ((total_orig - total_opt) / total_orig * 100) if total_orig else 0.0

    def _bom_rows() -> str:
        rows = ""
        for item in bom:
            rows += (
                f"<tr><td>{item.get('name', '')}</td>"
                f"<td>{item.get('material', '')}</td>"
                f"<td>{item.get('weight_kg', '')}</td></tr>"
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
                    f"<td>{best['co2_reduction_pct']:.1f}%</td>"
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
            opt = orig * (1 - best["co2_reduction_pct"] / 100) if best else orig
            delta = ((orig - opt) / orig * 100) if orig else 0.0
            rows += (
                f"<tr><td>{name}</td>"
                f"<td>{orig:.4f}</td>"
                f"<td>{opt:.4f}</td>"
                f"<td>{delta:.1f}%</td></tr>"
            )
        return rows

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sustainable Product Optimization Report</title>
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

<h1>&#127807; Sustainable Product Optimization Report</h1>
<div class="subtitle">Generated {generated_at} &nbsp;&middot;&nbsp; Powered by LangGraph &amp; LCA data</div>

<div class="meta">
  <strong>Product Description:</strong> {user_input}
</div>

<h2>&#128203; Original Bill of Materials</h2>
<table>
  <thead><tr><th>Component</th><th>Material</th><th>Weight (kg)</th></tr></thead>
  <tbody>{_bom_rows()}</tbody>
</table>

<h2>&#128300; Optimization Summary</h2>
<table>
  <thead>
    <tr>
      <th>Component</th>
      <th>Original Material</th>
      <th>Recommended Alternative</th>
      <th>CO&#8322; Reduction</th>
      <th>MCDA Score</th>
      <th>Justification</th>
    </tr>
  </thead>
  <tbody>{_opt_rows()}</tbody>
</table>

<h2>&#127757; CO&#8322; Impact Comparison</h2>
<div class="cards">
  <div class="card orig">
    <div class="label">Original Total CO&#8322;</div>
    <div class="value">{total_orig:.3f}</div>
    <div class="label">kg CO&#8322;-eq</div>
  </div>
  <div class="card opt">
    <div class="label">Optimised Total CO&#8322;</div>
    <div class="value">{total_opt:.3f}</div>
    <div class="label">kg CO&#8322;-eq</div>
  </div>
  <div class="card delta">
    <div class="label">Overall Reduction</div>
    <div class="value">{reduction_pct:.1f}%</div>
    <div class="label">improvement</div>
  </div>
</div>

<h3 style="margin-top:32px;color:#0f3460">Per-Component Breakdown</h3>
<table>
  <thead>
    <tr>
      <th>Component</th>
      <th>Original CO&#8322; (kg)</th>
      <th>Optimised CO&#8322; (kg)</th>
      <th>Reduction</th>
    </tr>
  </thead>
  <tbody>{_impact_rows()}</tbody>
</table>

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
    except Exception as exc:
        logger.warning("WeasyPrint import failed — PDF export unavailable: %s", exc)
        return None

    html_string = generate_html_report(state)
    try:
        pdf_bytes: bytes = HTML(string=html_string).write_pdf()
        return pdf_bytes
    except Exception as exc:
        logger.error("PDF generation failed: %s", exc)
        return None
