"""External registry validation tools for looking up biological entities."""

import re
from typing import Any

import httpx

_TIMEOUT = 15.0

# ---------------------------------------------------------------------------
# Addgene HTML parsing
# ---------------------------------------------------------------------------

# Markdown-based patterns (httpx returns HTML that we scan for result links)
# Matches links to individual plasmid pages: [plasmid_name](/12345/)
_MD_RESULT_RE = re.compile(
    r'\[([^\]]+)\]\(/(\d{4,6})/?\)',
)
# Matches catalog number followed (possibly across newlines) by Purpose/Description
_MD_PURPOSE_RE = re.compile(
    r'#(\d{4,6})[\s\S]*?(?:Purpose|Description)\s*\n\s*([^\n]{5,200})',
    re.IGNORECASE,
)
# HTML link pattern: <a href="/12345/">plasmid_name</a>
_HTML_LINK_RE = re.compile(
    r'<a[^>]+href="(/(\d{4,6})/?)"[^>]*>\s*([^<]+?)\s*</a>',
)
# HTML purpose pattern
_HTML_PURPOSE_RE = re.compile(
    r'>\s*#(\d{4,6})\s*<.*?(?:Purpose|purpose).*?>\s*([^<]{5,200})',
    re.DOTALL,
)


def _parse_addgene_results(text: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Extract plasmid entries from Addgene search page content (HTML or markdown).

    Returns a list of dicts with keys: catalog_number, name, description, url.
    """
    results: dict[str, dict[str, Any]] = {}  # keyed by catalog_number to dedupe

    # Try markdown-style links first (e.g. [pAAV2/11](/240486/))
    for match in _MD_RESULT_RE.finditer(text):
        name, catalog = match.group(1).strip(), match.group(2)
        if catalog not in results:
            results[catalog] = {
                "catalog_number": catalog,
                "name": name,
                "description": "",
                "url": f"https://www.addgene.org/{catalog}/",
            }

    # Try HTML-style links as fallback
    if not results:
        for match in _HTML_LINK_RE.finditer(text):
            _, catalog, name = match.group(1), match.group(2), match.group(3).strip()
            if catalog not in results and name and not name.startswith("#"):
                results[catalog] = {
                    "catalog_number": catalog,
                    "name": name,
                    "description": "",
                    "url": f"https://www.addgene.org/{catalog}/",
                }

    # Fill in descriptions from purpose fields
    for pattern in [_MD_PURPOSE_RE, _HTML_PURPOSE_RE]:
        for match in pattern.finditer(text):
            catalog = match.group(1)
            desc = match.group(2).strip()
            if catalog in results and not results[catalog]["description"]:
                desc = re.sub(r'<[^>]+>', '', desc).strip()
                results[catalog]["description"] = desc

    return list(results.values())[:max_results]


async def lookup_addgene(query: str) -> dict[str, Any]:
    """Search Addgene for a plasmid or vector by name or catalog number.

    Parameters
    ----------
    query : str
        Plasmid name or Addgene catalog number (e.g. "pAAV-EF1a-DIO-hChR2" or "26973").

    Returns
    -------
    dict
        Search results including plasmid name, ID, and URL, or an error message.
    """
    url = "https://www.addgene.org/search/catalog/plasmids/"
    params = {"q": query}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            # Try numeric catalog lookup first
            if query.strip().isdigit():
                detail_url = f"https://www.addgene.org/{query.strip()}/"
                resp = await client.get(detail_url)
                if resp.status_code == 200:
                    return {
                        "found": True,
                        "catalog_number": query.strip(),
                        "url": str(resp.url),
                        "status_code": resp.status_code,
                    }

            resp = await client.get(url, params=params)

            # Parse actual search results from the page content
            plasmids = _parse_addgene_results(resp.text)

            if plasmids:
                return {
                    "query": query,
                    "found": True,
                    "results": plasmids,
                    "url": str(resp.url),
                }

            return {
                "query": query,
                "found": False,
                "results": [],
                "url": str(resp.url),
            }
    except httpx.HTTPError as exc:
        return {"error": str(exc), "query": query}


async def lookup_ncbi_gene(query: str) -> dict[str, Any]:
    """Search NCBI Gene database via E-utilities API.

    Parameters
    ----------
    query : str
        Gene symbol, name, or NCBI Gene ID (e.g. "Slc17a7" or "100379223").

    Returns
    -------
    dict
        Matching gene records including ID, name, and summary.
    """
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # Search
            search_resp = await client.get(
                f"{base}/esearch.fcgi",
                params={"db": "gene", "term": query, "retmode": "json", "retmax": 5},
            )
            search_data = search_resp.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])

            if not id_list:
                return {"query": query, "found": False, "results": []}

            # Fetch summaries
            summary_resp = await client.get(
                f"{base}/esummary.fcgi",
                params={"db": "gene", "id": ",".join(id_list), "retmode": "json"},
            )
            summary_data = summary_resp.json().get("result", {})

            results = []
            for gid in id_list:
                info = summary_data.get(gid, {})
                results.append({
                    "gene_id": gid,
                    "symbol": info.get("name", ""),
                    "description": info.get("description", ""),
                    "organism": info.get("organism", {}).get("scientificname", ""),
                    "url": f"https://www.ncbi.nlm.nih.gov/gene/{gid}",
                })

            return {"query": query, "found": True, "results": results}
    except httpx.HTTPError as exc:
        return {"error": str(exc), "query": query}


async def lookup_mgi(query: str) -> dict[str, Any]:
    """Search Mouse Genome Informatics (MGI) for a mouse gene or allele.

    Parameters
    ----------
    query : str
        Gene symbol, allele symbol, or MGI ID (e.g. "Ai14" or "MGI:5013199").

    Returns
    -------
    dict
        Matching MGI records.
    """
    url = "https://www.informatics.jax.org/quicksearch/summary"
    params = {"queryType": "exactPhrase", "query": query}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            return {
                "query": query,
                "status_code": resp.status_code,
                "url": str(resp.url),
                "found": resp.status_code == 200,
            }
    except httpx.HTTPError as exc:
        return {"error": str(exc), "query": query}
