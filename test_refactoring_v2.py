"""
Test di verifica per il Refactoring Maniacale v2.
Eseguire con: venv\Scripts\python.exe test_refactoring_v2.py
"""
import sys
sys.path.insert(0, r'c:\Users\Samue\Downloads\progetto_semestre')

errors = []

# ===========================================================================
# DIRETTIVA 1 — Intent Detector
# ===========================================================================
print("=" * 60)
print("DIRETTIVA 1: Intent Detector")
print("=" * 60)
try:
    from data.csv_lca_client import classify_search_intent
    print("OK  csv_lca_client.py importato correttamente")

    cases = [
        ("pet",                          "plastic_material"),
        ("polyethylene terephthalate",   "plastic_material"),
        ("bottle",                       "plastic_material"),
        ("polypropylene",                "plastic_material"),
        ("petroleum",                    "extraction_activity"),
        ("petroleum pet",                "extraction_activity"),   # priorita' estrazione
        ("crude oil",                    "extraction_activity"),   # bigramma
        ("natural gas",                  "extraction_activity"),   # bigramma
        ("offshore drilling",            "extraction_activity"),
        ("steel",                        "generic"),
        ("wood",                         "generic"),
        ("concrete",                     "generic"),
    ]
    for query, expected in cases:
        result = classify_search_intent(query)
        status = "OK " if result == expected else "FAIL"
        print(f"  {status}  classify_search_intent({query!r}) = {result!r}  (expected: {expected!r})")
        if result != expected:
            errors.append(f"Intent mismatch: {query!r} -> {result!r} (expected {expected!r})")
except Exception as e:
    errors.append(f"csv_lca_client import error: {e}")
    print(f"FAIL csv_lca_client: {e}")

# ===========================================================================
# DIRETTIVA 2 — Pydantic Validators
# ===========================================================================
print()
print("=" * 60)
print("DIRETTIVA 2: Pydantic TransportValidatorMixin")
print("=" * 60)
try:
    from agents.schemas import ConstraintsExtract, WorkflowAndBOMResponse, TransportValidatorMixin
    print("OK  schemas.py importato correttamente")
    print("OK  TransportValidatorMixin presente")

    # Test coerce_distance_km: stringa con unita'
    c = ConstraintsExtract(distance_km="500 km")
    assert c.distance_km == 500.0, f"atteso 500.0, ottenuto {c.distance_km}"
    print("  OK  distance_km: '500 km' -> 500.0")

    # Test coerce_distance_km: intero
    c2 = ConstraintsExtract(distance_km=300)
    assert c2.distance_km == 300.0, f"atteso 300.0, ottenuto {c2.distance_km}"
    print("  OK  distance_km: 300 (int) -> 300.0")

    # Test coerce_distance_km: None passthrough
    c_none = ConstraintsExtract(distance_km=None)
    assert c_none.distance_km is None
    print("  OK  distance_km: None -> None")

    # Test coerce_transport_mode: normalizzazione sinonimi
    for raw, expected_mode in [
        ("camion", "lorry"),
        ("truck", "lorry"),
        ("lorry", "lorry"),
        ("nave", "ship"),
        ("ferry", "ship"),
        ("ship", "ship"),
        ("aereo", "aircraft"),
        ("airplane", "aircraft"),
        ("aircraft", "aircraft"),
    ]:
        c_t = ConstraintsExtract(transport_mode=raw)
        assert c_t.transport_mode == expected_mode, f"{raw!r} -> {c_t.transport_mode!r} (expected {expected_mode!r})"
        print(f"  OK  transport_mode: {raw!r} -> {c_t.transport_mode!r}")

    # Test GUARD: transport_mode con numero solleva errore
    guard_cases = ["500 km", "100", "2 giorni", "3000"]
    for bad_val in guard_cases:
        try:
            ConstraintsExtract(transport_mode=bad_val)
            errors.append(f"transport_mode={bad_val!r} doveva sollevare ValidationError")
            print(f"  FAIL  transport_mode={bad_val!r} NON ha sollevato errore!")
        except Exception:
            print(f"  OK  transport_mode={bad_val!r} correttamente rifiutato (ValidationError)")

    # Test WorkflowAndBOMResponse eredita il mixin
    w = WorkflowAndBOMResponse(
        is_material_only=False,
        is_interview_complete=True,
        distance_km="750 km",
        transport_mode="traghetto",
    )
    assert w.distance_km == 750.0
    assert w.transport_mode == "ship"
    print("  OK  WorkflowAndBOMResponse eredita TransportValidatorMixin correttamente")

except Exception as e:
    errors.append(f"schemas error: {e}")
    print(f"FAIL schemas.py: {e}")
    import traceback; traceback.print_exc()

# ===========================================================================
# DIRETTIVA 3 — ProcessMapper
# ===========================================================================
print()
print("=" * 60)
print("DIRETTIVA 3: COMPONENT_PROCESS_MAPPER")
print("=" * 60)
try:
    from agents.workflow_node import COMPONENT_PROCESS_MAPPER, get_process_by_component_name
    print("OK  workflow_node.py importato correttamente")
    print(f"OK  COMPONENT_PROCESS_MAPPER: {len(COMPONENT_PROCESS_MAPPER)} regole caricate")

    proc_cases = [
        # Tappi/chiusure -> sempre Injection Moulding
        ("tappo",          "Injection moulding"),
        ("cap",            "Injection moulding"),
        ("lid",            "Injection moulding"),
        ("closure",        "Injection moulding"),
        ("bottle cap",     "Injection moulding"),  # "cap" prima di "bottle"
        # Bottiglie -> Blow Moulding
        ("bottle",         "Blow moulding"),
        ("bottiglia",      "Blow moulding"),
        ("container",      "Blow moulding"),
        # Tubi -> Extrusion
        ("pipe",           "Extrusion"),
        ("tube",           "Extrusion"),
        ("tubo",           "Extrusion"),
        # Film -> Extrusion (film)
        ("film",           "Extrusion (film)"),
        ("sheet",          "Extrusion (film)"),
        # Etichette -> Printing
        ("label",          "Printing"),
        ("etichetta",      "Printing"),
        # Fastener -> Metal working
        ("screw",          "Metal working"),
        ("vite",           "Metal working"),
        ("bolt",           "Metal working"),
        # Non in mapper -> None (fallback geometrico)
        ("flangia",        None),
        ("chair",          None),
        ("sedia",          None),
        ("pannello solare",None),
    ]
    for comp_name, expected in proc_cases:
        result = get_process_by_component_name(comp_name)
        status = "OK " if result == expected else "FAIL"
        print(f"  {status}  get_process_by_component_name({comp_name!r}) = {result!r}")
        if result != expected:
            errors.append(f"ProcessMapper mismatch: {comp_name!r} -> {result!r} (expected {expected!r})")

except Exception as e:
    errors.append(f"workflow_node error: {e}")
    print(f"FAIL workflow_node.py: {e}")
    import traceback; traceback.print_exc()

# ===========================================================================
# AZIONE 1 — SemanticNormalizer
# ===========================================================================
print()
print("=" * 60)
print("AZIONE 1: SemanticNormalizer")
print("=" * 60)
try:
    from data.csv_lca_client import SemanticNormalizer, NormalizationResult
    print("OK  SemanticNormalizer importato correttamente")

    normalizer = SemanticNormalizer()

    # ── Test: base_material extraction ──────────────────────────────────
    base_material_cases = [
        # (input, expected_base_material)
        ("PVC",                          "PVC"),
        ("PVC rigido",                   "PVC"),
        ("PVC rigido per edilizia",      "PVC"),
        ("PVC rigido per tubi",          "PVC"),
        ("polipropilene",                "polypropylene"),
        ("polipropilene estruso",        "polypropylene"),
        ("PET",                          "PET"),
        ("polietilene tereftalato",      "PET"),
        ("acciaio",                      "steel"),
        ("alluminio",                    "aluminium"),
        ("fibra di carbonio",            "carbon fiber"),
        ("legno per costruzioni",        "wood"),
        ("vetro",                        "glass"),
        ("nylon",                        "nylon"),
    ]
    print("\n  --- Base Material Extraction ---")
    for inp, expected in base_material_cases:
        result = normalizer.normalize(inp)
        status = "OK " if result.base_material.lower() == expected.lower() else "FAIL"
        print(f"  {status}  normalize({inp!r}).base_material = {result.base_material!r}  (expected: {expected!r})")
        if result.base_material.lower() != expected.lower():
            errors.append(f"SemanticNormalizer base_material: {inp!r} → {result.base_material!r} (expected {expected!r})")

    # ── Test: geometry_hint detection ────────────────────────────────────
    geometry_cases = [
        # (input, expected_geometry_hint)
        ("tubo in PVC",              "profile"),
        ("tubo PVC rigido",          "profile"),
        ("bottiglia d'acqua",        "hollow"),
        ("bottiglia in PET",         "hollow"),
        ("foglio di polietilene",    "flat"),
        ("film di plastica",         "flat"),
        ("tappo in polipropilene",   "solid"),
        ("PVC rigido",               None),   # nessuna geometria esplicita
        ("polypropylene",            None),   # solo materiale
        ("fibra di carbonio",        "fabric"),
    ]
    print("\n  --- Geometry Hint Detection ---")
    for inp, expected in geometry_cases:
        result = normalizer.normalize(inp)
        status = "OK " if result.geometry_hint == expected else "FAIL"
        print(f"  {status}  normalize({inp!r}).geometry_hint = {result.geometry_hint!r}  (expected: {expected!r})")
        if result.geometry_hint != expected:
            errors.append(f"SemanticNormalizer geometry_hint: {inp!r} → {result.geometry_hint!r} (expected {expected!r})")

    # ── Test: strong modifiers preservation ──────────────────────────────
    modifier_cases = [
        # (input, expected_modifier_present)
        ("polipropilene riciclato",   "riciclato"),
        ("PVC estruso",               "estruso"),
        ("PET espanso",               "espanso"),
        ("acciaio vergine",           "vergine"),
    ]
    print("\n  --- Strong Modifiers Preservation ---")
    for inp, expected_mod in modifier_cases:
        result = normalizer.normalize(inp)
        status = "OK " if expected_mod in result.strong_modifiers else "FAIL"
        print(f"  {status}  normalize({inp!r}).strong_modifiers = {result.strong_modifiers!r}  (expected: {expected_mod!r} presente)")
        if expected_mod not in result.strong_modifiers:
            errors.append(f"SemanticNormalizer strong_modifiers: {inp!r} → modifiers={result.strong_modifiers!r}, expected '{expected_mod}' presente")

    # ── Test: container content stripping ────────────────────────────────
    print("\n  --- Container Content Stripping ---")
    content_cases = [
        # (input, expected_material, expected_geometry)
        ("bottiglia d'acqua",    "PET",  "hollow"),
        ("bottiglia di vino",    "PET",  "hollow"),
        ("contenitore di olio",  "PET",  "hollow"),
    ]
    for inp, exp_mat, exp_geom in content_cases:
        result = normalizer.normalize(inp)
        mat_ok = result.base_material.lower() == exp_mat.lower()
        geom_ok = result.geometry_hint == exp_geom
        status = "OK " if mat_ok and geom_ok else "FAIL"
        print(f"  {status}  normalize({inp!r}) -> mat={result.base_material!r}, geom={result.geometry_hint!r}")
        if not mat_ok:
            errors.append(f"SemanticNormalizer content strip: {inp!r} -> mat={result.base_material!r} (expected {exp_mat!r})")
        if not geom_ok:
            errors.append(f"SemanticNormalizer content strip: {inp!r} -> geom={result.geometry_hint!r} (expected {exp_geom!r})")

except Exception as e:
    errors.append(f"SemanticNormalizer error: {e}")
    print(f"FAIL SemanticNormalizer: {e}")
    import traceback; traceback.print_exc()

# ===========================================================================
# AZIONE 2 — ProcessResolver
# ===========================================================================
print()
print("=" * 60)
print("AZIONE 2: ProcessResolver")
print("=" * 60)
try:
    from agents.workflow_node import (
        resolve_process, _classify_material, _classify_geometry,
        _PROCESS_RESOLUTION_TABLE,
    )
    print("OK  resolve_process importato correttamente")
    print(f"OK  _PROCESS_RESOLUTION_TABLE: {len(_PROCESS_RESOLUTION_TABLE)} regole caricate")

    # ── Test: _classify_material ──────────────────────────────────────────
    mat_class_cases = [
        ("PVC",             "thermoplastic"),
        ("polypropylene",   "thermoplastic"),
        ("polipropilene",   "thermoplastic"),
        ("PET",             "thermoplastic"),
        ("nylon",           "thermoplastic"),
        ("steel",           "metal"),
        ("acciaio",         "metal"),
        ("alluminio",       "metal"),
        ("copper",          "metal"),
        ("rubber",          "elastomer"),
        ("gomma",           "elastomer"),
        ("legno",           "wood_based"),
        ("wood",            "wood_based"),
        ("vetro",           "glass_ceramic"),
        ("cotton",          "textile"),
        ("cotone",          "textile"),
        ("xyz_unknown",     None),
    ]
    print("\n  --- Material Classification ---")
    for mat, expected in mat_class_cases:
        result = _classify_material(mat)
        status = "OK " if result == expected else "FAIL"
        print(f"  {status}  _classify_material({mat!r}) = {result!r}  (expected: {expected!r})")
        if result != expected:
            errors.append(f"_classify_material: {mat!r} → {result!r} (expected {expected!r})")

    # ── Test: _classify_geometry ──────────────────────────────────────────
    geom_class_cases = [
        # (geometry_hint, geometry_label, component_name, expected)
        ("profile",  None,             None,        "profile"),
        (None,       "Profili/Tubi",   None,        "profile"),
        (None,       "Corpi Cavi",     None,        "hollow"),
        (None,       "Film",           None,        "flat"),
        (None,       "Pezzi Pieni Complessi", None, "solid"),
        (None,       None,             "tubo",      "profile"),
        (None,       None,             "bottiglia", "hollow"),
        (None,       None,             "sedia",     "solid"),   # default
        (None,       None,             None,        "solid"),   # puro default
    ]
    print("\n  --- Geometry Classification ---")
    for hint, label, name, expected in geom_class_cases:
        result = _classify_geometry(hint, label, name)
        status = "OK " if result == expected else "FAIL"
        print(f"  {status}  _classify_geometry(hint={hint!r}, label={label!r}, name={name!r}) = {result!r}")
        if result != expected:
            errors.append(f"_classify_geometry: ({hint!r},{label!r},{name!r}) → {result!r} (expected {expected!r})")

    # ── Test: resolve_process end-to-end ─────────────────────────────────
    resolve_cases = [
        # (material, geometry_hint, geometry_label, component_name, expected_process)
        ("PVC",          "profile",  None,             None,        "Extrusion"),
        ("PVC",          None,       "Profili/Tubi",   None,        "Extrusion"),
        ("PVC",          None,       None,             "tubo",      "Extrusion"),
        ("polypropylene","hollow",   None,             None,        "Blow moulding"),
        ("PET",          "hollow",   None,             None,        "Blow moulding"),
        ("PET",          None,       "Corpi Cavi",     None,        "Blow moulding"),
        ("polyethylene", "flat",     None,             None,        "Extrusion (film)"),
        ("polypropylene","solid",    None,             None,        "Injection moulding"),
        ("steel",        "profile",  None,             None,        "Section bar rolling"),
        ("steel",        None,       "Profili/Tubi",   None,        "Section bar rolling"),
        ("alluminio",    "flat",     None,             None,        "Metal sheet rolling"),
        ("acciaio",      "solid",    None,             None,        "Metal working"),
        ("rubber",       "solid",    None,             None,        "Injection moulding"),
        ("legno",        "flat",     None,             None,        "Woodworking"),
        ("vetro",        "hollow",   None,             None,        "Glass blowing"),
        ("cotton",       "fabric",   None,             None,        "Textile weaving"),
        # Material sconosciuto → fallback
        ("xyz_material", None,       None,             None,        "Injection moulding"),
    ]
    print("\n  --- Process Resolution End-to-End ---")
    for mat, hint, label, name, expected in resolve_cases:
        result = resolve_process(mat, geometry_hint=hint, geometry_label=label, component_name=name)
        status = "OK " if result == expected else "FAIL"
        args = f"mat={mat!r}, hint={hint!r}, label={label!r}"
        print(f"  {status}  resolve_process({args}) = {result!r}  (expected: {expected!r})")
        if result != expected:
            errors.append(f"resolve_process: ({mat!r},{hint!r},{label!r}) → {result!r} (expected {expected!r})")

    # ── Test: wrapper deprecato funziona ancora ───────────────────────────
    from agents.workflow_node import determine_manufacturing_process
    dep_result = determine_manufacturing_process("steel", "Profili/Tubi")
    status = "OK " if dep_result == "Section bar rolling" else "FAIL"
    print(f"\n  {status}  determine_manufacturing_process (deprecated wrapper): {dep_result!r}")
    if dep_result != "Section bar rolling":
        errors.append(f"deprecated wrapper: 'steel'+'Profili/Tubi' → {dep_result!r} (expected 'Section bar rolling')")

except Exception as e:
    errors.append(f"ProcessResolver error: {e}")
    print(f"FAIL ProcessResolver: {e}")
    import traceback; traceback.print_exc()

# ===========================================================================
# RISULTATO FINALE
# ===========================================================================
print()
print("=" * 60)
if errors:
    print(f"RISULTATO FINALE: {len(errors)} ERRORE/I TROVATI")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)
else:
    print("RISULTATO FINALE: TUTTI I TEST SUPERATI.")
    print("Il refactoring e' stato applicato correttamente.")
