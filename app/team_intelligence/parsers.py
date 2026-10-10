"""Pure parsers for static HTML, JSON, RSS/Atom and XML sitemaps."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

from app.team_intelligence.contracts import (
    CaptureWindow,
    DataType,
    EvidenceLevel,
    Observation,
    RawPayload,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class ParsedArticle:
    source_url: str
    title: str | None
    text: str
    published_at: datetime | None
    language: str


@dataclass(frozen=True, slots=True)
class FeedEntry:
    title: str
    url: str
    published_at: datetime | None
    summary: str | None


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    url: str
    last_modified: datetime | None


class _ArticleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.article_parts: list[str] = []
        self.json_ld_documents: list[str] = []
        self.metadata: dict[str, str] = {}
        self.language = "und"
        self._in_title = False
        self._in_article = 0
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag == "html" and attributes.get("lang"):
            self.language = attributes["lang"]
        if tag == "title":
            self._in_title = True
        if tag in {"article", "main"}:
            self._in_article += 1
        if tag == "meta":
            name = (attributes.get("property") or attributes.get("name") or "").casefold()
            if name and attributes.get("content"):
                self.metadata[name] = attributes["content"]
        if tag == "script" and attributes.get("type", "").casefold() == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title":
            self._in_title = False
        if tag in {"article", "main"} and self._in_article:
            self._in_article -= 1
        if tag == "script" and self._in_json_ld:
            self.json_ld_documents.append("".join(self._json_ld_parts))
            self._in_json_ld = False
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        if self._in_article:
            self.article_parts.append(value)
        if self._in_json_ld:
            self._json_ld_parts.append(data)


class StaticHtmlArticleParser:
    parser_version = "official-html-v1"

    def parse(self, raw: RawPayload) -> ParsedArticle:
        parser = _ArticleHtmlParser()
        parser.feed(raw.content.decode("utf-8", errors="replace"))
        structured: dict[str, Any] = {}
        for document in parser.json_ld_documents:
            structured = _json_ld_article(document)
            if structured:
                break
        title = (
            _optional_text(structured.get("headline"))
            or _optional_text(parser.metadata.get("og:title"))
            or _optional_text(" ".join(parser.title_parts))
        )
        text = (
            _optional_text(structured.get("articleBody"))
            or _optional_text(" ".join(parser.article_parts))
            or ""
        )
        published = _parse_datetime(
            structured.get("datePublished")
            or parser.metadata.get("article:published_time")
            or parser.metadata.get("date")
        )
        return ParsedArticle(
            source_url=raw.source_url,
            title=title,
            text=text,
            published_at=published,
            language=parser.language or "und",
        )


class StructuredJsonParser:
    parser_version = "official-json-v1"

    def extract(
        self, raw: RawPayload, *, paths: Mapping[str, tuple[str, ...]]
    ) -> dict[str, object]:
        document: object = json.loads(raw.content)
        return {name: _walk(document, path) for name, path in paths.items()}


class FeedParser:
    parser_version = "official-rss-atom-v1"

    def parse(self, raw: RawPayload) -> tuple[FeedEntry, ...]:
        root = ElementTree.fromstring(raw.content)
        entries: list[FeedEntry] = []
        for node in root.iter():
            local = _local_name(node.tag)
            if local not in {"item", "entry"}:
                continue
            children = {_local_name(child.tag): child for child in node}
            title = _element_text(children.get("title"))
            link = _element_text(children.get("link"))
            if not link and children.get("link") is not None:
                link = children["link"].attrib.get("href", "")
            published = _parse_datetime(
                _element_text(children.get("published"))
                or _element_text(children.get("updated"))
                or _element_text(children.get("pubDate"))
            )
            summary = _element_text(children.get("summary")) or _element_text(
                children.get("description")
            )
            if title and link.startswith("https://"):
                entries.append(FeedEntry(title, link, published, summary or None))
        return tuple(entries)


class SitemapParser:
    parser_version = "official-sitemap-v1"

    def parse(self, raw: RawPayload) -> tuple[SitemapEntry, ...]:
        root = ElementTree.fromstring(raw.content)
        entries: list[SitemapEntry] = []
        for node in root.iter():
            if _local_name(node.tag) != "url":
                continue
            children = {_local_name(child.tag): child for child in node}
            location = _element_text(children.get("loc"))
            if location.startswith("https://"):
                entries.append(
                    SitemapEntry(
                        url=location,
                        last_modified=_parse_datetime(_element_text(children.get("lastmod"))),
                    )
                )
        return tuple(entries)


class OfficialArticleAdapter:
    parser_version = "official-article-evidence-v1"

    def create_observation(
        self,
        raw: RawPayload,
        article: ParsedArticle,
        *,
        team_id: str,
        player_id: str | None,
        fixture_id: str | None,
        data_type: DataType,
        evidence_level: EvidenceLevel,
        capture_window: CaptureWindow,
        normalized: Mapping[str, object],
        original_quote: str | None = None,
    ) -> Observation:
        return Observation(
            team_id=team_id,
            player_id=player_id,
            fixture_id=fixture_id,
            source_name=raw.source_name,
            source_url=article.source_url,
            published_at=article.published_at,
            captured_at=raw.captured_at,
            original_title=article.title,
            original_text=article.text or None,
            original_quote=original_quote,
            language=article.language,
            parser_version=self.parser_version,
            raw_payload_hash=raw.sha256,
            data_type=data_type,
            evidence_level=evidence_level,
            source_policy=raw.source_policy,
            capture_window=capture_window,
            normalized=normalized,
        )


def _json_ld_article(value: str) -> dict[str, Any]:
    if not value.strip():
        return {}
    try:
        document = json.loads(value)
    except json.JSONDecodeError:
        return {}
    candidates = document if isinstance(document, list) else [document]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        graph = candidate.get("@graph")
        if isinstance(graph, list):
            candidates.extend(graph)
        kind = candidate.get("@type")
        kinds = {kind} if isinstance(kind, str) else set(kind or [])
        if kinds & {"Article", "NewsArticle", "Report"}:
            return candidate
    return {}


def _walk(document: object, path: tuple[str, ...]) -> object:
    current = document
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            raise ValueError(f"JSON path is missing: {'.'.join(path)}")
        current = current[segment]
    return current


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        from email.utils import parsedate_to_datetime

        try:
            parsed = parsedate_to_datetime(normalized)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(element: ElementTree.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""
