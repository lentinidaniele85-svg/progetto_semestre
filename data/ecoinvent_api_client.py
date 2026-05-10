from typing import List

from data.lca_interface import LCADataProvider

_NOT_IMPLEMENTED_MSG = "Ecoinvent API integration pending license."


class EcoinventAPIClient(LCADataProvider):
    """Stub for the commercial Ecoinvent API. Requires a valid license to implement."""

    async def search_materials(
        self, query: str, location: str = "Global"
    ) -> List[dict]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    async def get_impact_scores(self, material_id: str) -> dict:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
