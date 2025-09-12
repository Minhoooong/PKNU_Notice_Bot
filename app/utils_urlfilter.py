from urllib.parse import urlparse

# Domains to block including subdomains
BLOCKED_DOMAINS = {
    "whalebu.pknu.ac.kr",
    "whalebe.com",
    "www.whalebe.com",
}


def is_blocked_url(url: str) -> bool:
    """
    Returns True if the URL's host matches or is a subdomain of any blocked domain.
    Unparseable URLs are treated as blocked.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    for domain in BLOCKED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def filter_blocked(urls):
    """
    Filters out URLs that are blocked.
    """
    return [u for u in urls if not is_blocked_url(u)]
