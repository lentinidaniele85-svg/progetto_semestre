import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from data.csv_lca_client import CSVLcaClient

@pytest.fixture
def client() -> CSVLcaClient:
    return CSVLcaClient()


# ---------------------------------------------------------------------------
# search_materials tests
# ---------------------------------------------------------------------------

def test_search_materials_returns_results(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("electricity"))
    assert len(results) > 0, "Expected at least one electricity material"


def test_search_materials_keys(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("electricity"))
    expected_keys = {"id", "providerName", "flowName", "location"}
    for row in results:
        assert expected_keys.issubset(row.keys()), f"Missing keys in row: {row}"


def test_search_materials_no_match(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("xyzzy_nonexistent_material"))
    assert results == []


def test_search_materials_location_filter(client: CSVLcaClient) -> None:
    results = asyncio.run(client.search_materials("electricity", location="eu"))
    for row in results:
        assert "eu" in row["location"].lower(), f"Unexpected location: {row['location']}"


def test_search_materials_case_insensitive(client: CSVLcaClient) -> None:
    lower = asyncio.run(client.search_materials("wood"))
    upper = asyncio.run(client.search_materials("WOOD"))
    assert len(lower) == len(upper)


# ---------------------------------------------------------------------------
# get_impact_scores tests
# ---------------------------------------------------------------------------

# IDs reali verificati nel DataSet.xlsx (climateChangeImpact > 0)
_REAL_ID_A = "97c91c0b-ebe6-53b3-82b0-b3a3c1823439_85f25c36-edd2-4383-9fbc-4577e0726e00"  # battery NiMH, ~28.76
_REAL_ID_B = "a893d1ad-2d28-5dbd-b3fd-f926b2997eb7_d51e5a32-c20a-4da6-8d58-31f344b50c00"  # market waste graphical paper, ~1.02


def test_get_impact_scores_known_id(client: CSVLcaClient) -> None:
    scores = asyncio.run(client.get_impact_scores(_REAL_ID_A))
    _assert_valid_scores(scores)


def test_get_impact_scores_unknown_id_returns_none(client: CSVLcaClient) -> None:
    scores = asyncio.run(client.get_impact_scores("unknown-id-999"))
    assert scores is None, "Expected None for an id not present in DataSet.xlsx"


def test_get_impact_scores_deterministic(client: CSVLcaClient) -> None:
    scores_a = asyncio.run(client.get_impact_scores(_REAL_ID_B))
    scores_b = asyncio.run(client.get_impact_scores(_REAL_ID_B))
    assert scores_a == scores_b, "Impact scores must be deterministic for same id"


# ---------------------------------------------------------------------------
# find_closest_match / is_market tests
# ---------------------------------------------------------------------------

import pandas as pd
from unittest.mock import patch


@pytest.fixture
def mock_client() -> CSVLcaClient:
    """
    CSVLcaClient con un DataFrame in-memory che contiene due righe note:
      - "market for polypropylene"  → is_market deve essere True
      - "polypropylene production"  → is_market deve essere False
    Bypassa la lettura del file Excel reale.
    """
    client = CSVLcaClient()           # usa il DataSet.xlsx reale per l'init
    # Sostituiamo _df con uno sintetico e ricostruiamo le colonne di supporto
    fake_df = pd.DataFrame([
        {
            "id": "mock-001",
            "processname": "market for polypropylene, global",
            "outputname": "polypropylene, granulate",
            "location": "GLO",
            "climatechangeimpact": 2.1,
        },
        {
            "id": "mock-002",
            "processname": "polypropylene production, mass polymerisation",
            "outputname": "polypropylene production granulate",
            "location": "RER",
            "climatechangeimpact": 1.8,
        },
    ], dtype=str)
    fake_df["climatechangeimpact"] = pd.to_numeric(fake_df["climatechangeimpact"])
    fake_df["_flowname_lower"] = fake_df["outputname"].str.lower()
    client._df = fake_df
    client._search_cache = {}
    client._match_cache = {}
    return client


def _run_find(client: CSVLcaClient, *args, **kwargs):
    """Helper: esegue find_closest_match (async) in modo sincrono per i test."""
    return asyncio.run(client.find_closest_match(*args, **kwargs))


def test_find_closest_match_market_is_market_true(mock_client: CSVLcaClient) -> None:
    """Un processname contenente 'market' deve restituire is_market=True."""
    with patch("data.csv_lca_client.generate_search_queries",
               new_callable=lambda: lambda: AsyncMock(side_effect=lambda m: [m])):
        result = _run_find(mock_client, "polypropylene, granulate")
    assert result is not None, "find_closest_match non deve restituire None"
    assert "is_market" in result, "Il campo is_market deve essere presente nel dict"
    assert result["is_market"] is True, (
        f"Expected is_market=True per processname '{result['providerName']}'"
    )


def test_find_closest_match_production_is_market_false(mock_client: CSVLcaClient) -> None:
    """Un processname senza 'market' deve restituire is_market=False."""
    with patch("data.csv_lca_client.generate_search_queries",
               new_callable=lambda: lambda: AsyncMock(side_effect=lambda m: [m])):
        result = _run_find(mock_client, "polypropylene production granulate")
    assert result is not None, "find_closest_match non deve restituire None"
    assert "is_market" in result, "Il campo is_market deve essere presente nel dict"
    assert result["is_market"] is False, (
        f"Expected is_market=False per processname '{result['providerName']}'"
    )


def test_find_closest_match_returns_all_expected_keys(mock_client: CSVLcaClient) -> None:
    """find_closest_match deve restituire tutti i campi attesi."""
    with patch("data.csv_lca_client.generate_search_queries",
               new_callable=lambda: lambda: AsyncMock(side_effect=lambda m: [m])):
        result = _run_find(mock_client, "polypropylene, granulate")
    expected_keys = {"id", "providerName", "flowName", "location", "environmental_impact", "is_market"}
    assert expected_keys.issubset(result.keys()), (
        f"Campi mancanti: {expected_keys - result.keys()}"
    )


def test_find_closest_match_no_match_returns_none(client: CSVLcaClient) -> None:
    """Con un label totalmente inventato e soglia alta, deve restituire None."""
    with patch("data.csv_lca_client.generate_search_queries",
               new_callable=lambda: lambda: AsyncMock(side_effect=lambda m: [m])):
        result = _run_find(client, "xyzzy_material_that_does_not_exist_9999", threshold=0.9)
    assert result is None, "Expected None per un label senza corrispondenza"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_valid_scores(scores: dict) -> None:
    assert "environmental_impact" in scores
    assert "energy_mj" in scores
    assert "cost_tier" in scores
    assert scores["environmental_impact"] > 0
    assert scores["energy_mj"] >= 0
    assert 0 <= scores["cost_tier"] <= 4
