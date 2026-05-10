from abc import ABC, abstractmethod
from typing import List


class LCADataProvider(ABC):
    """Abstract interface for Life Cycle Assessment data sources."""

    @abstractmethod
    async def search_materials(
        self, query: str, location: str = "Global"
    ) -> List[dict]:
        """
        Search for materials matching query and optional location filter.

        Returns a list of dicts with keys: id, providerName, flowName, location.
        """

    @abstractmethod
    async def get_impact_scores(self, material_id: str) -> dict:
        """
        Retrieve environmental impact scores for a material by its id.

        Returns a dict with keys: co2_eq_kg, energy_mj, water_l, cost_tier.
        """
