"""
Live web search via DuckDuckGo (no API key required).
"""

import logging

logger = logging.getLogger(__name__)


def web_search(query: str, max_results: int = 3) -> list[dict]:
    """
    Search DuckDuckGo and return up to max_results results.
    Each result is a dict with keys: title, snippet, url.
    Returns an empty list on failure.
    """
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                })
        return results
    except Exception as exc:
        logger.error("Web search error for query '%s': %s", query, exc)
        return []


def format_search_results(results: list[dict], lang: str = "en") -> str:
    """Format search results into a readable string for injecting into LLM context."""
    if not results:
        no_result = {
            "ru": "По вашему запросу ничего не найдено.",
            "uz": "So'rovingiz bo'yicha hech narsa topilmadi.",
            "en": "No results found for your query.",
        }
        return no_result.get(lang, no_result["en"])

    header = {
        "ru": "🔎 **Результаты поиска:**",
        "uz": "🔎 **Qidiruv natijalari:**",
        "en": "🔎 **Search results:**",
    }.get(lang, "🔎 **Search results:**")

    lines = [header]
    for i, r in enumerate(results, 1):
        lines.append(f"\n**{i}. {r['title']}**")
        if r["snippet"]:
            lines.append(r["snippet"])
        if r["url"]:
            lines.append(f"🔗 {r['url']}")
    return "\n".join(lines)
