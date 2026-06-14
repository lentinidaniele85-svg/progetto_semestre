"""Regression tests per _has_word_boundary_match (data/csv_lca_client.py).

Contesto del bug:
    L'helper interno a `_search_best_match` faceva riferimento a un oggetto
    inesistente `_re_match` (mai importato né definito). Quando l'LLM Semantic
    Expander produceva un termine di ricerca corto (<= 3 caratteri, es. 'pp',
    'pe', 'abs') il ramo word-boundary veniva eseguito e sollevava
    `NameError: name '_re_match' is not defined`, propagando fino al try/except
    del nodo e mostrando all'utente un errore di analisi apparentemente casuale.

    Il fix estrae la logica a livello di modulo (`_has_word_boundary_match`)
    usando nativamente il modulo standard `re`. Questi test blindano il
    percorso critico: garantiscono che (1) nessun NameError/scoping error venga
    più sollevato per termini corti e (2) la semantica dei confini di parola
    (`\\b`) resti intatta — fondamentale per isolare acronimi puri ed evitare
    falsi positivi interni a parole più lunghe (il classico 'pet' in 'petroleum').
"""
import pytest

from data.csv_lca_client import _has_word_boundary_match


# ---------------------------------------------------------------------------
# 1. REGRESSIONE PRINCIPALE: termini corti (<= 3 char) non sollevano NameError
# ---------------------------------------------------------------------------

_SHORT_TERMS = ["pp", "pe", "abs", "pvc", "pet", "pa", "pc", "ps"]
_REAL_TARGETS = [
    "polypropylene production, granulate",
    "market for abs plastic",
    "polyethylene, high density, granulate | polyethylene, hdpe | rer",
    "market for polyethylene terephthalate, granulate, bottle grade",
    "petroleum and gas production, onshore",
]


@pytest.mark.parametrize("term", _SHORT_TERMS)
@pytest.mark.parametrize("target", _REAL_TARGETS)
def test_short_terms_never_raise(term: str, target: str) -> None:
    """Il bug originale: termini <= 3 char attivavano il ramo `_re_match`
    inesistente. Qui verifichiamo solo che NON venga sollevata alcuna
    eccezione (NameError o altro) e che il risultato sia un bool valido."""
    result = _has_word_boundary_match(term, target)
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 2. SEMANTICA WORD-BOUNDARY: l'acronimo corto matcha SOLO come parola intera
# ---------------------------------------------------------------------------

def test_short_term_matches_as_standalone_word() -> None:
    # 'abs' è una parola a sé in "market for abs plastic" -> match
    assert _has_word_boundary_match("abs", "market for abs plastic") is True


def test_short_term_does_not_match_inside_longer_word() -> None:
    # 'pp' NON deve matchare dentro "polypropylene" (nessun confine di parola).
    # Questo è il comportamento che protegge dai falsi positivi interni.
    assert _has_word_boundary_match("pp", "polypropylene production, granulate") is False
    # 'pe' NON deve matchare dentro "polyethylene"
    assert _has_word_boundary_match("pe", "polyethylene, high density") is False


def test_pet_does_not_match_petroleum() -> None:
    # Caso emblematico del dataset: 'pet' (plastica) non deve agganciarsi a
    # 'petroleum' (attività estrattiva) grazie al confine di parola.
    assert _has_word_boundary_match("pet", "petroleum and gas production, onshore") is False
    # ...ma deve matchare 'pet' come parola intera in un record bottiglie PET.
    assert _has_word_boundary_match("pet", "market for pet, granulate, bottle grade") is True


# ---------------------------------------------------------------------------
# 3. TERMINI LUNGHI (> 3 char): substring match, comportamento invariato
# ---------------------------------------------------------------------------

def test_long_term_substring_match() -> None:
    assert _has_word_boundary_match("polypropylene", "polypropylene production, granulate") is True
    # substring interna ammessa per i termini lunghi (nessun word-boundary)
    assert _has_word_boundary_match("propylene", "polypropylene production") is True


def test_long_term_no_match() -> None:
    assert _has_word_boundary_match("polyamide", "market for abs plastic") is False


# ---------------------------------------------------------------------------
# 4. CASI LIMITE: stringa vuota e confine esatto a 3 caratteri
# ---------------------------------------------------------------------------

def test_empty_target_returns_false_without_error() -> None:
    assert _has_word_boundary_match("pp", "") is False


def test_three_char_boundary_threshold() -> None:
    # esattamente 3 char -> ramo word-boundary
    assert _has_word_boundary_match("pvc", "market for pvc pipe") is True
    assert _has_word_boundary_match("pvc", "pvcfoam panel") is False  # no boundary
