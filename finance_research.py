from __future__ import annotations

import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


DEFAULT_ALLOWED_DOMAINS = (
    "mercadolivre.com.br",
    "amazon.com.br",
    "magazineluiza.com.br",
    "kabum.com.br",
    "casasbahia.com.br",
    "pontofrio.com.br",
    "carrefour.com.br",
    "leroymerlin.com.br",
    "madeiramadeira.com.br",
    "pichau.com.br",
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)
DEFAULT_SEARCH_URL = "https://html.duckduckgo.com/html/"
STOP_WORDS = {
    "a",
    "ao",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "sem",
    "um",
    "uma",
}
PRICE_RE = re.compile(r"R\$\s*([0-9\.\,]+)")
TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


class ScraperError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_text(value: str) -> str:
    clean = unicodedata.normalize("NFKD", as_text(value))
    clean = "".join(ch for ch in clean if not unicodedata.combining(ch))
    return clean.lower()


def tokenize(value: str) -> set[str]:
    return {token for token in TOKEN_RE.findall(normalize_text(value)) if token not in STOP_WORDS}


def parse_price_value(value: Any) -> float:
    raw = as_text(value)
    if not raw:
        return 0.0
    raw = raw.replace("R$", "").replace(" ", "").strip()
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return round(float(raw), 2)
    except ValueError:
        return 0.0


def domain_label(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return (host.split(".")[0] if host else "").replace("-", " ").title() or "Loja"


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    clean_path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{clean_path}"


def is_allowed_url(url: str, allowed_domains: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def clean_search_result_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    if raw_url.startswith("//"):
        raw_url = f"https:{raw_url}"

    parsed = urlparse(raw_url)
    if "duckduckgo.com" in parsed.netloc.lower():
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return unquote(target)
    return raw_url


def fetch_text(url: str, *, timeout_seconds: int, user_agent: str, referer: str = "") -> str:
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Referer": referer or "https://duckduckgo.com/",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise ScraperError(f"Busca web respondeu HTTP {exc.code}.") from exc
    except URLError as exc:
        raise ScraperError("Nao foi possivel acessar a busca web.") from exc


def search_mercadolivre(query: str, *, timeout_seconds: int, user_agent: str, limit: int) -> list[dict[str, Any]]:
    search_url = f"https://lista.mercadolivre.com.br/{quote_plus(query).replace('+', '-')}"
    html = fetch_text(search_url, timeout_seconds=timeout_seconds, user_agent=user_agent, referer="https://www.mercadolivre.com.br/")
    soup = BeautifulSoup(html, "html.parser")

    offers: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for card in soup.select("li.ui-search-layout__item, div.poly-card"):
        link_node = card.select_one("a.poly-component__title") or card.select_one("a[href]")
        price_node = card.select_one("span.andes-money-amount__fraction")
        cents_node = card.select_one("span.andes-money-amount__cents")
        if link_node is None:
            continue

        url = normalize_url(clean_search_result_url(as_text(link_node.get("href"))))
        title = link_node.get_text(" ", strip=True)
        if (
            not url
            or not title
            or url in seen_urls
            or "click" in urlparse(url).netloc.lower()
            or urlparse(url).path.lower().endswith("/count")
        ):
            continue
        seen_urls.add(url)

        price_text = ""
        if price_node is not None:
            price_text = price_node.get_text(" ", strip=True)
            if cents_node is not None and cents_node.get_text(" ", strip=True):
                price_text = f"{price_text},{cents_node.get_text(' ', strip=True)}"

        offers.append(
            {
                "title": title,
                "store": "Mercado Livre",
                "url": url,
                "priceText": f"R$ {price_text}" if price_text else "Preco nao identificado",
                "priceValue": parse_price_value(price_text),
                "currency": "BRL",
                "reason": "Preco encontrado diretamente na listagem da loja.",
                "imageUrl": "",
                "score": 0.0,
            }
        )
        if len(offers) >= limit:
            break

    return offers


def search_web(query: str, *, allowed_domains: tuple[str, ...], timeout_seconds: int, user_agent: str, limit: int) -> list[dict[str, str]]:
    search_url = f"{DEFAULT_SEARCH_URL}?q={quote_plus(query)}"
    html = fetch_text(search_url, timeout_seconds=timeout_seconds, user_agent=user_agent)
    soup = BeautifulSoup(html, "html.parser")

    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    nodes = soup.select(".result")
    if not nodes:
        nodes = soup.select("article")

    for node in nodes:
        link_node = node.select_one(".result__a") or node.select_one("a[href]")
        if link_node is None:
            continue
        url = clean_search_result_url(as_text(link_node.get("href")))
        if not url or url in seen_urls or not is_allowed_url(url, allowed_domains):
            continue

        seen_urls.add(url)
        title = link_node.get_text(" ", strip=True) or domain_label(url)
        snippet_node = node.select_one(".result__snippet") or node.select_one(".result__extras__url")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        candidates.append({"title": title, "url": url, "snippet": snippet})
        if len(candidates) >= limit:
            break

    return candidates


def json_ld_items(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw_text = script.string or script.get_text()
        if not raw_text:
            continue
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            continue
        items.extend(flatten_json_ld(payload))
    return items


def flatten_json_ld(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items: list[dict[str, Any]] = []
        for item in payload:
            items.extend(flatten_json_ld(item))
        return items

    if isinstance(payload, dict):
        items = [payload]
        if isinstance(payload.get("@graph"), list):
            items.extend(flatten_json_ld(payload["@graph"]))
        return items

    return []


def pick_first(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                if as_text(item):
                    return as_text(item)
        if isinstance(value, dict):
            nested = as_text(value.get("name")) or as_text(value.get("title"))
            if nested:
                return nested
        if as_text(value):
            return as_text(value)
    return ""


def extract_page_offer(candidate: dict[str, str], *, timeout_seconds: int, user_agent: str, query_tokens: set[str]) -> dict[str, Any] | None:
    url = candidate["url"]
    html = fetch_text(url, timeout_seconds=timeout_seconds, user_agent=user_agent, referer=DEFAULT_SEARCH_URL)
    soup = BeautifulSoup(html, "html.parser")

    title = pick_first(
        soup.find("meta", attrs={"property": "og:title"}) and soup.find("meta", attrs={"property": "og:title"}).get("content"),
        soup.title and soup.title.get_text(" ", strip=True),
        candidate.get("title"),
    )
    image_url = pick_first(
        soup.find("meta", attrs={"property": "og:image"}) and soup.find("meta", attrs={"property": "og:image"}).get("content"),
        soup.find("meta", attrs={"name": "twitter:image"}) and soup.find("meta", attrs={"name": "twitter:image"}).get("content"),
    )

    store = domain_label(url)
    currency = pick_first(
        soup.find("meta", attrs={"property": "product:price:currency"}) and soup.find("meta", attrs={"property": "product:price:currency"}).get("content"),
        "BRL",
    )
    price_value = parse_price_value(
        pick_first(
            soup.find("meta", attrs={"property": "product:price:amount"}) and soup.find("meta", attrs={"property": "product:price:amount"}).get("content"),
            soup.find(attrs={"itemprop": "price"}) and soup.find(attrs={"itemprop": "price"}).get("content"),
        )
    )

    for item in json_ld_items(soup):
        item_type = normalize_text(item.get("@type"))
        if "product" not in item_type and "offer" not in item_type:
            continue

        title = pick_first(item.get("name"), title, candidate.get("title"))
        store = pick_first(item.get("seller"), item.get("brand"), store)
        image_url = pick_first(item.get("image"), image_url)

        offers = item.get("offers")
        offer = offers[0] if isinstance(offers, list) and offers else offers if isinstance(offers, dict) else {}
        price_value = parse_price_value(
            pick_first(
                offer.get("price") if isinstance(offer, dict) else "",
                item.get("price"),
                price_value,
            )
        ) or price_value
        currency = pick_first(
            offer.get("priceCurrency") if isinstance(offer, dict) else "",
            item.get("priceCurrency"),
            currency,
        ) or "BRL"
        break

    if price_value <= 0:
        match = PRICE_RE.search(soup.get_text(" ", strip=True)[:20000])
        if match:
            price_value = parse_price_value(match.group(1))

    content_text = f"{title} {candidate.get('snippet', '')}"
    overlap = len(query_tokens & tokenize(content_text))
    if not title:
        return None

    score = round((overlap / max(len(query_tokens), 1)) * 100, 2) if query_tokens else 0.0
    price_text = f"R$ {price_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if price_value > 0 else ""
    reason = (
        "Preco extraido da pagina do produto."
        if price_value > 0
        else "Link encontrado na busca, mas o preco nao foi identificado automaticamente."
    )

    return {
        "title": title,
        "store": store,
        "url": url,
        "priceText": price_text or "Preco nao identificado",
        "priceValue": price_value,
        "currency": currency or "BRL",
        "reason": reason,
        "imageUrl": image_url,
        "score": score,
        "snippet": candidate.get("snippet", ""),
    }


def categorize_offers(raw_offers: list[dict[str, Any]], max_offers: int) -> list[dict[str, Any]]:
    priced = [offer for offer in raw_offers if offer["priceValue"] > 0]
    unpriced = [offer for offer in raw_offers if offer["priceValue"] <= 0]

    priced.sort(key=lambda offer: (-offer["score"], offer["priceValue"]))
    unpriced.sort(key=lambda offer: -offer["score"])

    ordered = priced + unpriced
    final_offers: list[dict[str, Any]] = []
    for index, offer in enumerate(ordered[:max_offers]):
        if index < 2:
            category = "melhor_preco"
        elif index < 4:
            category = "custo_beneficio"
        else:
            category = "alternativa"
        final_offers.append(
            {
                "category": category,
                "title": offer["title"],
                "store": offer["store"],
                "url": offer["url"],
                "priceText": offer["priceText"],
                "priceValue": offer["priceValue"],
                "currency": offer["currency"],
                "reason": offer["reason"],
                "imageUrl": offer["imageUrl"],
            }
        )
    return final_offers


def build_query(payload: dict[str, str]) -> str:
    parts = [payload.get("desc", ""), payload.get("fornecedor", ""), payload.get("obs", "")]
    clean = " ".join(part for part in parts if as_text(part))
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or as_text(payload.get("desc"))


def run_scraper_purchase_research(
    payload: dict[str, str],
    *,
    allowed_domains: tuple[str, ...] = DEFAULT_ALLOWED_DOMAINS,
    max_offers: int = 6,
    timeout_seconds: int = 12,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    query = build_query(payload)
    if len(as_text(query)) < 3:
        raise ScraperError("Informe uma descricao mais detalhada para pesquisar.")

    query_tokens = tokenize(query)
    direct_offers = search_mercadolivre(
        query,
        timeout_seconds=timeout_seconds,
        user_agent=user_agent,
        limit=max_offers,
    )
    for offer in direct_offers:
        offer["score"] = round((len(query_tokens & tokenize(offer["title"])) / max(len(query_tokens), 1)) * 100, 2) if query_tokens else 0.0

    filtered_direct_offers = [offer for offer in direct_offers if offer["score"] >= 20]
    if filtered_direct_offers:
        direct_offers = filtered_direct_offers

    if direct_offers:
        categorized = categorize_offers(direct_offers, max_offers=max_offers)
        return {
            "ok": True,
            "provider": "python_scraper",
            "query": query,
            "summary": f"Foram coletadas {len(categorized)} oferta(s) diretamente da busca da loja.",
            "offers": categorized,
            "sources": [{"title": offer["title"], "url": offer["url"]} for offer in categorized],
            "generatedAt": now_iso(),
            "model": "python-scraper",
        }

    search_terms = [f"{query} preco brasil comprar", query]
    raw_candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for term in search_terms:
        for item in search_web(
            term,
            allowed_domains=allowed_domains,
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
            limit=max(max_offers * 2, 8),
        ):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            raw_candidates.append(item)
            if len(raw_candidates) >= max(max_offers * 2, 8):
                break
        if len(raw_candidates) >= max(max_offers * 2, 8):
            break

    if not raw_candidates:
        raise ScraperError("Nenhum resultado foi encontrado na busca web para essa descricao.")

    offers: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(4, len(raw_candidates))) as executor:
        futures = {
            executor.submit(
                extract_page_offer,
                candidate,
                timeout_seconds=timeout_seconds,
                user_agent=user_agent,
                query_tokens=query_tokens,
            ): candidate
            for candidate in raw_candidates
        }
        for future in as_completed(futures):
            try:
                offer = future.result()
            except ScraperError:
                continue
            if offer is not None:
                offers.append(offer)

    if not offers:
        offers = [
            {
                "title": item["title"],
                "store": domain_label(item["url"]),
                "url": item["url"],
                "priceText": "Preco nao identificado",
                "priceValue": 0.0,
                "currency": "BRL",
                "reason": item["snippet"] or "Resultado encontrado na busca web.",
                "imageUrl": "",
                "score": 0.0,
            }
            for item in raw_candidates[:max_offers]
        ]

    categorized = categorize_offers(offers, max_offers=max_offers)
    return {
        "ok": True,
        "provider": "python_scraper",
        "query": query,
        "summary": f"Foram avaliados {len(raw_candidates)} link(s) e {len(categorized)} oferta(s) foram organizadas automaticamente.",
        "offers": categorized,
        "sources": [{"title": offer["title"], "url": offer["url"]} for offer in categorized],
        "generatedAt": now_iso(),
        "model": "python-scraper",
    }


def build_scraper_diagnostic(
    *,
    allowed_domains: tuple[str, ...] = DEFAULT_ALLOWED_DOMAINS,
    timeout_seconds: int = 12,
    max_offers: int = 6,
    probe: bool = False,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    result = {
        "ok": True,
        "checkedAt": now_iso(),
        "config": {
            "provider": "python_scraper",
            "engine": "mercadolivre_search + duckduckgo_fallback",
            "searchUrl": "https://lista.mercadolivre.com.br/",
            "allowedDomains": list(allowed_domains),
            "allowedDomainsCount": len(allowed_domains),
            "timeoutSeconds": timeout_seconds,
            "maxOffers": max_offers,
            "userAgentConfigured": bool(user_agent),
        },
        "status": {
            "level": "ok",
            "code": "scraper_ready",
            "message": "Pesquisa inteligente em Python habilitada.",
        },
        "probe": {
            "attempted": probe,
            "success": False,
            "httpStatus": None,
            "message": "",
            "resultCount": 0,
        },
    }

    if not probe:
        return result

    try:
        results = search_mercadolivre(
            "mouse usb comprar brasil",
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
            limit=3,
        )
    except ScraperError as exc:
        result["status"] = {"level": "bad", "code": "search_unreachable", "message": str(exc)}
        result["probe"]["message"] = str(exc)
        return result

    result["probe"]["resultCount"] = len(results)
    if results:
        result["probe"]["success"] = True
        result["status"] = {
            "level": "ok",
            "code": "search_ok",
            "message": "Busca web respondendo normalmente para o mecanismo Python.",
        }
        result["probe"]["message"] = f"{len(results)} resultado(s) coletado(s) na verificacao."
        return result

    result["status"] = {
        "level": "warn",
        "code": "search_empty",
        "message": "A busca respondeu, mas nao retornou links validos para os dominios configurados.",
    }
    result["probe"]["message"] = "Ajuste a lista de dominios permitidos ou a conectividade da busca."
    return result
