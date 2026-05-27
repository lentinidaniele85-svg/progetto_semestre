import threading
from core.config import settings
from data.lca_interface import LCADataProvider

_provider_cache: dict[str, LCADataProvider] = {}
_cache_lock = threading.Lock()


def get_lca_provider() -> LCADataProvider:
    """Return the configured LCA data provider based on LCA_DATA_SOURCE env var.

    The provider instance is cached at module level so the (potentially large)
    CSV is read and processed only once per process lifetime.
    """
    source = settings.lca_data_source

    if source in _provider_cache:
        return _provider_cache[source]

    with _cache_lock:
        # Double-checked locking
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


def flush_provider_cache() -> None:
    """Evict stale match/search cache entries from the LCA provider singleton.

    Call this at the start of every new agent session (e.g. on chat reset in
    the UI) to ensure that coroutine objects written by any pre-fix code path
    cannot be returned to callers in subsequent runs.

    The provider instance itself is NOT recreated — the (large) DataFrame stays
    in memory.  Only the lightweight result caches are cleared.
    """
    for provider in _provider_cache.values():
        if hasattr(provider, "flush_match_cache"):
            provider.flush_match_cache()

