from core.config import settings
from data.lca_interface import LCADataProvider

_provider_cache: dict[str, LCADataProvider] = {}


def get_lca_provider() -> LCADataProvider:
    """Return the configured LCA data provider based on LCA_DATA_SOURCE env var.

    The provider instance is cached at module level so the (potentially large)
    CSV is read and processed only once per process lifetime.
    """
    source = settings.lca_data_source

    if source in _provider_cache:
        return _provider_cache[source]

    if source == "csv":
        from data.csv_lca_client import CSVLcaClient
        _provider_cache[source] = CSVLcaClient()
        return _provider_cache[source]

    if source == "ecoinvent_api":
        from data.ecoinvent_api_client import EcoinventAPIClient
        _provider_cache[source] = EcoinventAPIClient()
        return _provider_cache[source]

    raise ValueError(f"Unknown LCA data source: {source!r}")
