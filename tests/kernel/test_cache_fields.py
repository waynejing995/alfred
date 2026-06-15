from agentkit.kernel.context import CacheUsage


def test_cache_usage_names_are_explicit():
    usage = CacheUsage(cached_tokens=1, cache_read=2, cache_write=3, prompt_tokens=4)

    assert usage.cached_tokens == 1
    assert usage.cache_read == 2
    assert usage.cache_write == 3

