from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup


SITE_URL = "https://www.energiezukunft.eu/"
OUTPUT_FILE = Path("public/rss.xml")
MAX_ITEMS = 30

# Artikel erscheinen überwiegend unter diesen Bereichen.
ARTICLE_SECTIONS = {
    "politik",
    "wirtschaft",
    "mobilitaet",
    "umwelt",
    "erneuerbare-energien",
    "energie",
    "bauen",
    "wissen",
    "meinung",
}


def clean_text(value: str | None) -> str:
    """Leerzeichen und Zeilenumbrüche vereinheitlichen."""
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def is_probable_article(url: str) -> bool:
    """Prüft, ob eine URL wahrscheinlich zu einem Artikel gehört."""
    parsed = urlparse(url)

    if parsed.netloc not in {"energiezukunft.eu", "www.energiezukunft.eu"}:
        return False

    parts = [part for part in parsed.path.split("/") if part]

    # Erwartetes Muster: /bereich/artikel-slug
    if len(parts) != 2:
        return False

    section, slug = parts

    if section in {
        "magazine",
        "medientipps",
        "suche",
        "kontakt",
        "impressum",
        "datenschutz",
    }:
        return False

    if section not in ARTICLE_SECTIONS:
        # Nicht automatisch verwerfen, da sich Kategorien ändern können.
        # Der Slug muss aber wie ein echter Artikelname aussehen.
        return len(slug) >= 12 and "-" in slug

    return len(slug) >= 5


def get_meta_content(soup: BeautifulSoup, *selectors: dict[str, str]) -> str:
    """Liest den ersten vorhandenen Meta-Wert aus."""
    for selector in selectors:
        element = soup.find("meta", attrs=selector)
        if element and element.get("content"):
            return clean_text(element["content"])

    return ""


def parse_date(value: str) -> datetime | None:
    """Verarbeitet typische ISO-Datumsformate."""
    if not value:
        return None

    normalized = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def extract_article_urls(session: requests.Session) -> list[str]:
    """Sammelt Artikel-URLs von der Startseite."""
    response = session.get(SITE_URL, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    urls: list[str] = []

    for link in soup.find_all("a", href=True):
        absolute_url = urljoin(SITE_URL, link["href"])
        parsed = urlparse(absolute_url)

        # Fragment und Query-Parameter entfernen.
        clean_url = parsed._replace(query="", fragment="").geturl()

        if is_probable_article(clean_url) and clean_url not in urls:
            urls.append(clean_url)

    return urls[:MAX_ITEMS]


def extract_article(session: requests.Session, url: str) -> dict[str, str] | None:
    """Liest Titel, Beschreibung und Datum einer Artikelseite."""
    response = session.get(url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    title = get_meta_content(
        soup,
        {"property": "og:title"},
        {"name": "twitter:title"},
    )

    if not title and soup.title:
        title = clean_text(soup.title.get_text())

    description = get_meta_content(
        soup,
        {"property": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
    )

    published_raw = get_meta_content(
        soup,
        {"property": "article:published_time"},
        {"name": "date"},
        {"name": "datePublished"},
    )

    published = parse_date(published_raw)

    # Fallback: sichtbares deutsches Datum suchen.
    if published is None:
        page_text = clean_text(soup.get_text(" "))
        match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", page_text)

        if match:
            day, month, year = map(int, match.groups())
            published = datetime(year, month, day, 12, tzinfo=timezone.utc)

    if not title:
        return None

    return {
        "title": title,
        "description": description or title,
        "url": url,
        "published": format_datetime(
            published or datetime.now(timezone.utc),
            usegmt=True,
        ),
    }


def indent_xml(element: ET.Element, level: int = 0) -> None:
    """Formatiert die XML-Ausgabe lesbar."""
    indentation = "\n" + level * "  "

    if len(element):
        if not element.text or not element.text.strip():
            element.text = indentation + "  "

        for child in element:
            indent_xml(child, level + 1)

        if not child.tail or not child.tail.strip():
            child.tail = indentation

    if level and (not element.tail or not element.tail.strip()):
        element.tail = indentation


def create_feed(articles: list[dict[str, str]]) -> None:
    """Erzeugt eine RSS-2.0-Datei."""
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
        },
    )

    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = "energiezukunft.eu – inoffizieller RSS-Feed"
    ET.SubElement(channel, "link").text = SITE_URL
    ET.SubElement(channel, "description").text = (
        "Inoffizieller RSS-Feed mit aktuellen Beiträgen von energiezukunft.eu"
    )
    ET.SubElement(channel, "language").text = "de-DE"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc),
        usegmt=True,
    )

    # Diese URL später an deinen Benutzernamen anpassen.
    atom_link = ET.SubElement(
        channel,
        "{http://www.w3.org/2005/Atom}link",
    )
    atom_link.set(
        "href",
        "https://beneme1.github.io/energiezukunft-rss/rss.xml",
    )
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for article in articles:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = article["title"]
        ET.SubElement(item, "link").text = article["url"]
        ET.SubElement(item, "description").text = article["description"]
        ET.SubElement(item, "pubDate").text = article["published"]

        guid = ET.SubElement(item, "guid")
        guid.set("isPermaLink", "true")
        guid.text = article["url"]

    indent_xml(rss)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    tree = ET.ElementTree(rss)
    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )


def main() -> None:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "energiezukunft-rss/1.0 "
                "(personal RSS reader; respectful request frequency)"
            )
        }
    )

    urls = extract_article_urls(session)
    articles: list[dict[str, str]] = []

    for url in urls:
        try:
            article = extract_article(session, url)
        except requests.RequestException as error:
            print(f"Artikel konnte nicht geladen werden: {url}: {error}")
            continue

        if article:
            articles.append(article)

    if not articles:
        raise RuntimeError(
            "Es wurden keine Artikel erkannt. "
            "Möglicherweise wurde die Website-Struktur geändert."
        )

    create_feed(articles)
    print(f"{len(articles)} Artikel nach {OUTPUT_FILE} geschrieben.")


if __name__ == "__main__":
    main()
