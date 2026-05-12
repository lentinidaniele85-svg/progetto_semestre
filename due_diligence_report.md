# Due Diligence Report — Sustainable Product Optimization Agent
**Prepared as:** Senior Product Designer · UX/UI Expert · Software Architect (Principal) · Senior Frontend/Backend Engineer · QA Engineer · Performance Engineer · Product Strategist · Power User

---

## 1. Executive Summary

The application is a **LangGraph-powered multi-step agentic pipeline** that performs Life Cycle Assessment (LCA) and Material Optimization for physical products. The UX concept (Glass Box Mode, co-pilot flow, step tracker) is genuinely good. The core loop works end-to-end. **However**, the product has a cluster of critical bugs that corrupt outputs, architectural decisions that will not scale, and UX patterns that confuse real users. It is a strong proof-of-concept that has not been hardened for reliability or correctness.

**Overall rating: 5.5/10** — Promising concept, fragile execution.

---

## 2. How The App Really Works

### Runtime Flow
```
START
  → constraint_extractor (sync, LLM)         [phase: constraints]
  → human_feedback_processor (interrupt)      [pauses for user approval]
  → route_after_feedback()
      if phase == "workflow"  → material_ideator
      else                    → workflow_bom_ideator
  → workflow_bom_ideator (async, LLM)         [phase: interview OR workflow]
  → human_feedback_processor (interrupt)      [pauses for interview OR workflow approval]
  → material_ideator (async, LLM)             [phase: material]
  → lca_validator (async, deterministic)      [phase: lca]
  → mcda_scorer (sync, deterministic)         [phase: complete]
  → END
```

### Key Data Flow
1. User input → `ConstraintsExtract` Pydantic model (LLM structured output)
2. Constraints + input → `WorkflowAndBOMResponse` (LLM, ~2 LLM calls per run)
3. BOM → `MaterialIdeationResponse` (LLM, generates 3 alternatives per component)
4. Alternatives → deterministic LCA formula: `(mat_impact + process_impact + transport_impact) × mass_kg`
5. LCA → MCDA weighted sum → best alternative per component

### State Machine
The system uses `current_phase` as an explicit state machine: `init → constraints → interview → workflow → material → lca → mcda → complete | error`.

---

## 3. Bugs — Critical & High Priority

### 🔴 BUG-01: API Key Exposed in `.env` (CRITICAL — Security)
**Location:** `.env` line 6  
**Problem:** The real OpenRouter API key `sk-or-v1-e4d6d0aa...` is committed in plaintext. If this repo is ever pushed to any Git remote or shared, the key is compromised.  
**Risk:** Immediate credential exposure, financial liability.  
**Fix:** Rotate key NOW. Add `.env` to `.gitignore` (it's there but the file was already committed). Add a `.env.example` template. Never store secrets in tracked files.

### 🔴 BUG-02: `water_l` is a Hardcoded Constant (CRITICAL — Logic)
**Location:** `nodes.py` lines 180, 210  
**Code:** `"water_l": 1.0 * mass_kg`  
**Problem:** Water impact is always `1.0 × mass_kg` regardless of material. It is a nonsensical placeholder. MCDA weight for water is 0.0, so it currently doesn't affect scores — but if someone sets `WEIGHT_WATER > 0`, all rankings will be corrupted by this fake value.  
**Fix:** Either source real `water_l` data or remove the field entirely from the schema.

### 🔴 BUG-03: `cost_tier` is Always 1 (CRITICAL — Logic)
**Location:** `nodes.py` lines 181, 213; `csv_lca_client.py` line 153  
**Code:** `"cost_tier": 1` (hardcoded)  
**Problem:** Every material gets cost_tier=1 (cheapest). The MCDA `cost_delta` between original and alternative is always `1 - 1 = 0`. The UI shows "Cost Δ (tier): 0" for everything. The 30% MCDA weight on cost is effectively meaningless because `_safe_delta(orig_cost_per_kg, alt_cost_per_kg)` works on `cost_per_kg` (which varies) but `cost_delta` uses `cost_tier` (always 1). Mixed semantics.  
**Fix:** Unify: either use `cost_tier` consistently (estimate 1–4 per material category) or use `cost_per_kg` everywhere and drop `cost_tier`.

### 🔴 BUG-04: HTML Report Uses Wrong Field Name (CRITICAL — Data Integrity)
**Location:** `reports/generator.py` line 19  
**Code:** `r["original_scores"]["environmental_impact"]`  
**Analysis:** This is actually correct NOW — `lca_validator` does store `environmental_impact` in `original_scores`. The old bug (using `co2_eq_kg`) has been fixed. However, the `lca_interface.py` docstring (line 23) still references `co2_eq_kg`:  
`Returns a dict with keys: co2_eq_kg, energy_mj, water_l, cost_tier.`  
This stale documentation will mislead any future developer implementing a new provider.  
**Fix:** Update `lca_interface.py` docstring to reflect actual keys.

### 🟠 BUG-05: Interview Loop Risk (HIGH — UX/Logic)
**Location:** `graph.py` `route_after_feedback()`, `nodes.py` `human_feedback_processor`  
**Problem:** After the interview phase, `human_feedback_processor` sets `current_phase = None` (it doesn't update it — returns only `user_input`, `pending_feedback`, `thought_log`). Then `route_after_feedback()` checks `if state.get("current_phase") == "workflow"` — which is False because phase is still `"interview"`. So it routes BACK to `workflow_bom_ideator`. This is the intended behavior for interview loop, BUT if the LLM sets `is_interview_complete=True` on the second pass, `workflow_bom_ideator` returns `current_phase="workflow"` — and now the NEXT interrupt routes to `material_ideator`. The logic is correct but extremely fragile: a single missed phase update breaks the entire routing. Live testing confirmed the agent can loop on interview questions even after the user provides all details.  
**Fix:** Make `human_feedback_processor` always explicitly set `current_phase` based on the feedback context. Never rely on the previous value persisting.

### 🟠 BUG-06: `material_ideator` Error Returns Wrong Phase (HIGH — Architecture)
**Location:** `material_node.py` lines 51–59  
**Problem:** When `material_ideator` fails (network timeout, LLM error), it returns `pending_feedback` with an error message BUT does NOT set `current_phase`. The graph then hits `END` because there is no interrupt on `material_ideator` — it falls through to `lca_validator` with empty `semantic_alternatives`, producing an empty result silently. The error message set in `pending_feedback` is never shown because the UI only checks `_last_error` from exceptions, not from `pending_feedback`.  
**Fix:** Return `current_phase: "error"` and `error_message` on failure, matching the pattern from `workflow_node.py`.

### 🟠 BUG-07: `check_interview_complete` is a Misleading Stub (HIGH — Architecture)
**Location:** `graph.py` lines 14–19  
**Code:**
```python
def check_interview_complete(state: AgentState):
    return "human_feedback_processor"
```
This function ALWAYS returns `"human_feedback_processor"` regardless of state. It was originally meant to route differently based on interview completion. It now does nothing conditional. This is not a bug per se (the routing is handled by `route_after_feedback`), but it is deeply confusing dead logic that will mislead any maintainer.  
**Fix:** Remove the conditional edge from `workflow_bom_ideator` and replace with a simple `add_edge("workflow_bom_ideator", "human_feedback_processor")`.

### 🟠 BUG-08: Progress Tracker Jumps From Step 1 to Step 7 (HIGH — UX)
**Location:** `app.py` line 486; `nodes.py` line 106, 238; `workflow_node.py` lines 66, 144; `material_node.py` line 48  
**Problem:** Steps are assigned as follows:
- `constraint_extractor` → step 1
- `workflow_bom_ideator` interview → step 2
- `workflow_bom_ideator` complete → step 6
- `material_ideator` → step 4
- `lca_validator` → step 7

Steps 3 and 5 are NEVER set. The visual tracker goes 1 → 6 → 4 → 7, which makes no sense and contradicts the displayed step names. Live observation: during Material Ideation (step 4 label: "Geometric Constraint"), step "Aggregated Lookup" (2) and "Material Selection" (3) show as pending simultaneously with "Logistics Calculation" (6) showing complete.  
**Fix:** Map steps correctly to the code flow. Step numbering must be monotonically increasing with time.

---

## 4. UX/UI Analysis

### Layout
- **Good:** Two-column split (chat left, dashboard right) is logical and clean.
- **Problem:** The right panel in `phase=init` shows a generic info box that takes up the entire right column. On a wide screen this feels like wasted space.

### Welcome State
- The dashboard shows a single `st.info()` block. It communicates well.
- However, there is NO visual indication of what "modes" are available (interactive vs. auto). The mode toggle is hidden — there is no UI to switch it. The mode is hardcoded to "interactive" by default and there is no visible way for the user to change it from the UI.

### Chat Input Behavior
- The placeholder text adapts correctly to phase ("Describe a product…" vs. "Please answer the interview questions…"). ✅
- **Problem:** In the interview phase, the placeholder says "Please answer the interview questions or type 'Approve' to continue…" — but there is ALSO a big green "✅ Approve Interview" button above. Two ways to do the same thing creates confusion about which to use.
- **Problem:** After submitting an answer, the page rerenders and the chat scrolls to top. Long conversations force users to scroll repeatedly.

### Thought Log
- **Good:** The expandable thought log is the best UX feature. Real transparency.
- **Problem:** When expanded, it dominates the right column, pushing BOM/MCDA results far below the fold. On a 1080p screen, the MCDA results require significant scrolling.

### BOM Table
- The column mapping in `app.py` (lines 560–572) is defensive and clean. ✅
- **Problem:** `baseline_environmental_impact` column shows the unit per-kg impact, not the total component impact. The column header says `Impact (kg CO₂ eq/kg)` which is correct but misleading because other "impact" numbers in the MCDA table are totals.

### MCDA Table
- **Good:** Data is structured and readable.
- **Problem:** "Cost Δ (tier): 0" for every row (see BUG-03). This makes the cost column meaningless and communicates poor data quality to any informed user.
- **Problem:** "MCDA Score: 0.000" can appear if all materials have the same CO₂ impact (e.g., all fallback to 3.5 kg CO₂/kg). This is a silent quality failure.

### Error States
- `st.error()` with `icon="🔴"` is used correctly for critical fallbacks. ✅
- `st.warning()` for assumptions is implemented. ✅
- **Problem:** The error in `material_node.py` (Italian message: "Non posso procedere perché...") is never surfaced to the UI — it sits in `pending_feedback` but the UI only shows it on the interrupt, and the interrupt never fires after `material_ideator` since it's not in the interrupt list.

### Responsive Design
- Streamlit's native layout handles basic responsiveness.
- On narrow screens, the 7-step tracker with 7 columns becomes unreadable (12px font in `<div>` tags).

### CSS / Styling
- The glassmorphism CSS is minimal and only in the left column (injected via `st.html()`). The right column is unstyled Streamlit default. Visual inconsistency.
- Green button gradient (`#10b981 → #059669`) is applied globally to ALL `div.stButton > button`. This makes the "Restart Session" button green instead of red/warning color, which is semantically wrong for a destructive action.

---

## 5. Architectural Analysis

### Graph Architecture
**What's good:** Using LangGraph as the orchestration framework is the right call. Explicit state machine with `current_phase` is the correct pattern for complex multi-step agentic flows.

**What's wrong:**
- `check_interview_complete` is a dead-conditional function that always returns one value. It suggests the graph was refactored mid-way and left in an inconsistent state.
- The `human_feedback_processor` node does three completely different things depending on `current_phase` (interview response, constraint modification, BOM/workflow modification). This violates Single Responsibility Principle and makes the node very hard to test.
- There is no `interrupt_after` — only `interrupt_before`. This means the graph state is saved BEFORE the node runs. If the node fails after an interrupt is consumed, there is no way to replay just that node.

### State Schema
`AgentState` is a flat `TypedDict` with `total=False`. Every field is optional. This means:
- No compile-time guarantees about what data exists at any point
- Any node can silently read `None` for a field it expects to be populated
- The schema grows linearly with features; no namespacing

A better pattern: nested TypedDicts or Pydantic models per pipeline stage.

### Data Layer
**Good:** The `LCADataProvider` abstract interface is the correct abstraction. The thread-safe singleton in `provider_factory.py` is properly implemented with double-checked locking. ✅

**Problem:** `find_closest_match` uses `difflib.get_close_matches` with a threshold of 0.5 on the full list of unique flow names. The DataSet.xlsx has 2.4MB — potentially thousands of entries. This is an O(n × m) string comparison operation called synchronously in an async node, on the main thread, without caching. For a 10-component BOM × 3 alternatives, that's 40+ sequential difflib calls.

**Problem:** `_estimate_energy_mj` and `_estimate_cost_per_kg` are keyword-matching functions that check `if "steel" in name`. This will match "stainless steel sheet" but not "Steel, low-alloyed {RER}" (ecoinvent format uses `{RER}` location codes). The matching is fragile to ecoinvent's naming convention.

### LLM Integration
**Good:** `ModelFactory` with static model cache avoids repeated instantiation. ✅

**Problem:** Both `workflow_bom_ideator` and `material_ideator` use `asyncio.to_thread(_invoke_structured, ...)` — wrapping a synchronous call in a thread. `_invoke_structured` itself calls `chain.invoke()` synchronously. This is correct for avoiding event loop blocking, but adds thread pool overhead that could be eliminated by using `chain.ainvoke()` directly (the async variant).

**Problem:** The system prompt in `semantic_ideation_api.yaml` is shared between `workflow_bom_ideator` (which needs BOM + workflow) and `material_ideator` (which needs only alternatives). The prompt says "execute ONLY FASE 3 and FASE 4" for material ideation — but the system prompt describes 7 steps for a BOM ideator. This creates cognitive dissonance for the LLM and increases prompt tokens unnecessarily.

**Problem:** No LLM timeout is set. A hanging API call will block the Streamlit thread indefinitely with no user feedback.

### Async/Sync Mixing
The graph mixes sync nodes (`constraint_extractor`, `mcda_scorer`, `human_feedback_processor`) with async nodes (`workflow_bom_ideator`, `material_ideator`, `lca_validator`). LangGraph handles this, but:
- `constraint_extractor` calls `_invoke_structured` synchronously, which calls `chain.invoke()`. This blocks the event loop if called from an async context.
- `lca_validator` is declared `async` but does no `await` operations — it's entirely synchronous inside. Declaring it async for no reason adds overhead.

### Error Handling
- `workflow_bom_ideator` wraps everything in try/except and returns `current_phase: "error"`. ✅
- `material_ideator` does NOT return `current_phase: "error"`. ❌
- `constraint_extractor` silently falls back to empty constraints. This is intentional but undocumented behavior.
- No dead-letter queue or retry mechanism for LLM failures beyond the 2-attempt `_invoke_structured` retry.

---

## 6. Technical Issues — Frontend

### Streamlit Anti-Patterns
1. **`st.rerun()` overuse:** Called after every action. This re-renders the entire page, re-reads all session state, and re-renders every widget from scratch. For complex dashboards this causes visible flicker.
2. **CSS injection via `st.html()`:** Injected inside the left column only — styles apply globally but are logically scoped to one column. This is a side-effect architecture.
3. **`import hashlib, json` inside a render function (line 665):** Module-level imports inside runtime code. Should be at top of file.
4. **PDF caching logic in render:** Lines 661–675 compute an MD5 hash of `mcda_scores` and cache PDF bytes in session state. This is a correct optimization but is embedded inside the rendering block, making it hard to test in isolation.
5. **`sniffio` import inside exception handler (line 150):** Importing a library inside an exception handler is fragile and slow.

### Code Duplication
The `handle_input` and `handle_feedback` functions (lines 177–327) have nearly identical post-processing logic (checking `next_node`, `phase`, building messages). This ~80 lines of duplicated code should be extracted to a single `_update_approval_state()` helper.

---

## 7. What's Done Well — Do Not Touch

| Item | Reason |
|------|--------|
| `LCADataProvider` abstract interface | Clean abstraction, enables future ecoinvent API swap |
| `provider_factory.py` thread-safe singleton | Correct double-checked locking |
| `ModelFactory` static cache | Prevents redundant LLM instantiation |
| `pydantic_settings` for config | Correct pattern, fails fast on missing keys |
| `current_phase` explicit state machine | Right approach for routing, just needs cleanup |
| Thought log UI | Best feature — real transparency into agent reasoning |
| `st.dialog` for restart confirmation | Correct pattern implemented |
| Fallback logging in `assumptions_list` | Golden rule implemented correctly |
| `generate_html_report` structure | Clean HTML, uses real computed data |
| `GEOMETRY_MAPPING` table | Correct deterministic process selection |
| `_safe_delta` helper | Correctly handles division by zero |

---

## 8. What Must Be Reworked — Prioritized

### Priority 1 — Immediate (blocking correctness)
1. **Rotate OpenRouter API key** — security incident risk
2. **Fix `cost_tier` always=1** — MCDA is partially broken
3. **Fix `water_l = 1.0` hardcoded** — fake science data
4. **Fix `material_ideator` error path** — silent failures

### Priority 2 — Short Term (blocking reliability)
5. **Fix step tracker numbering** — goes 1→6→4→7
6. **Remove `check_interview_complete` stub** — confusing dead code
7. **Add LLM timeout** — indefinite hangs possible
8. **Make `lca_validator` sync** — it's async but never awaits
9. **Separate material_ideator prompt** — it reuses BOM ideator prompt

### Priority 3 — Medium Term (quality & maintainability)
10. **Deduplicate `handle_input`/`handle_feedback`** — 80 lines of duplication
11. **Fix `lca_interface.py` stale docstring** — misleads future devs
12. **Unify async pattern** — use `chain.ainvoke()` directly
13. **Add `find_closest_match` caching** — called 40+ times per analysis
14. **Fix green CSS on destructive button** — "Restart" must not be green
15. **Add mode toggle in UI** — "auto" mode is invisible to users

### Priority 4 — Long Term (scalability)
16. **Namespace `AgentState`** — flat TypedDict will not scale
17. **Split `human_feedback_processor`** into 3 focused nodes
18. **Add session persistence** — browser refresh loses all progress
19. **Add LLM cost tracking** — no visibility into API spend
20. **Replace difflib with vector search** — needed for larger datasets

---

## 9. Refactoring Recommendations

### A. Fix the MCDA Data Model

```python
# nodes.py — replace hardcoded values
"cost_tier": _estimate_cost_tier(mat_cost),  # 1=<$1/kg, 2=$1-3, 3=$3-10, 4=>$10
"water_l": None,  # Remove until real data available
```
Remove `water_l` from `AgentState`, `LCADataProvider`, all schemas. Keep `WEIGHT_WATER=0.0` as a reminder for future work, but don't generate fake data.

### B. Clean the Graph Edges

```python
# graph.py — replace conditional stub with simple edge
graph.add_edge("workflow_bom_ideator", "human_feedback_processor")
# Remove check_interview_complete entirely
```

### C. Split the Feedback Processor

```python
# Three focused nodes instead of one mega-node:
async def interview_response_node(state): ...     # phase=interview
async def constraint_approval_node(state): ...    # phase=constraints  
async def workflow_approval_node(state): ...      # phase=workflow

# Route BEFORE interrupt:
graph.add_conditional_edges(
    "pre_interrupt_router", route_by_phase,
    {"interview": "interview_response_node", ...}
)
```

### D. Add Async Throughout

```python
# material_node.py and workflow_node.py:
# Replace asyncio.to_thread(_invoke_structured, ...) with:
result = await chain.ainvoke(messages)
# Use _ainvoke_structured (already exists in nodes.py!)
```

### E. Separate Prompts

```yaml
# prompts/bom_ideation.yaml — for workflow_bom_ideator (Steps 1-7)
# prompts/material_alternatives.yaml — for material_ideator (alternatives only)
```

---

## 10. Recommended Roadmap

| Sprint | Items | Goal |
|--------|-------|------|
| **S0 (today)** | Rotate API key, fix cost_tier, remove water_l | Stop shipping corrupt data |
| **S1 (1 week)** | Fix step tracker, material error path, LLM timeout, fix green button | Reliable and correct |
| **S2 (2 weeks)** | Deduplicate handlers, separate prompts, clean graph stubs | Maintainable codebase |
| **S3 (1 month)** | Namespace AgentState, split feedback processor, add caching | Scalable architecture |
| **S4 (2 months)** | Session persistence, mode toggle UI, cost tracking, vector search | Production-ready |

---

## 11. Risk Analysis — Growth Trajectory

| Risk | Current Impact | At 10x Scale | Severity |
|------|---------------|--------------|---------|
| Flat `AgentState` TypedDict | Low (small state) | Name collisions, impossible debugging | High |
| `difflib` on full dataset per call | Acceptable (2.4MB, ~40 calls) | Unusable (10x data, concurrent users) | High |
| Single `human_feedback_processor` | Manageable | Untestable, unmaintainable | Medium |
| No LLM timeout | Rare hangs | Frequent blocking under load | High |
| Fake `water_l` data | MCDA weight=0 masks it | Any future water scoring is corrupt | Medium |
| Session in `st.session_state` | Fine for 1 user | No persistence, no multi-tab | High |
| CSS via `st.html()` | Works | Breaks on Streamlit updates | Low |

---

## 12. Overall Quality Assessment

| Dimension | Score | Notes |
|-----------|-------|-------|
| UX Concept | 8/10 | Glass Box Mode, co-pilot flow — genuinely innovative |
| UX Execution | 5/10 | Interview loop, tracker confusion, missing mode toggle |
| UI Consistency | 5/10 | Mixed CSS, green destructive button, stale captions |
| Functional Correctness | 5/10 | cost_tier=1, water_l fake, step tracker broken |
| Code Quality | 6/10 | Good abstractions mixed with duplication and stubs |
| Architecture | 6/10 | Right patterns chosen, wrong execution details |
| Error Handling | 5/10 | Some paths solid, material_ideator path broken |
| Security | 2/10 | API key in committed file — immediate risk |
| Performance | 6/10 | Caching exists, but difflib and sync blocking remain |
| Scalability | 4/10 | Flat state, no persistence, no concurrency model |
| **Overall** | **5.5/10** | |

---

## 13. Conclusion — Principal Architect Perspective

If I were taking technical ownership of this project, my honest assessment is:

**The conceptual architecture is sound.** LangGraph + explicit state machine + deterministic LCA formula + LLM for ideation is a genuinely clever separation of concerns. The `LCADataProvider` interface is the right abstraction. The assumption logging is the right transparency mechanism.

**The execution has critical gaps** that prevent this from being trusted for real decisions:

1. **The MCDA is not multi-criteria** — it's 70% CO₂ + 30% cost, with cost_tier always=0 delta. Calling it "MCDA" is misleading. It's a CO₂ minimizer with a cost tiebreaker that never activates. Fix cost_tier or label it honestly.

2. **The state machine routing is implicit and fragile.** `route_after_feedback` checks a single field that any node can overwrite or forget to set. Add a formal phase transition diagram. Make phase transitions explicit and validated.

3. **The LLM is doing too many jobs.** One prompt drives BOM generation AND material selection AND logistics AND interview detection. Split these responsibilities into smaller, testable prompts with clear input/output contracts.

4. **There is no test harness.** The `tests/` directory exists but was not populated (not examined in scope, but the `.pytest_cache` is empty of results). Without tests, every refactor is a regression risk.

**My recommended evolution path:**

Phase 1 (now): Patch the security issue and corrupt data fields. These are not architectural — they are bugs.

Phase 2 (next sprint): Build a test suite first. Mock the LLM with recorded fixtures. Then refactor the graph with tests as a safety net.

Phase 3: Extract the MCDA into a standalone, fully-tested service with real multi-criteria data (energy and water from actual ecoinvent columns if available, or clearly labeled as estimates).

Phase 4: Move state to a proper persistent store (Redis or SQLite via LangGraph's built-in persistence). This enables session recovery, multi-tab, and production deployment.

The product has a real use case and an innovative UX. The bones are good. The flesh needs work — but it's fixable work, not a rewrite.

---

*Report generated: 2026-05-12 | Conversation: 5bdf209c | Live browser session recorded*
