from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.team_intelligence.contracts import (
    DataType,
    RawPayload,
    SourceDefinition,
    SourcePolicy,
    SourceStatus,
    SourceType,
)
from app.team_intelligence.http import CompliantHttpClient, RobotsPolicy, SourceAccessDenied
from app.team_intelligence.parsers import (
    FeedParser,
    SitemapParser,
    StaticHtmlArticleParser,
    StructuredJsonParser,
)
from app.team_intelligence.sources import load_source_registry

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _approved_source() -> SourceDefinition:
    return SourceDefinition(
        name="approved-api",
        base_url="https://example.test",
        source_type=SourceType.PROVIDER_API,
        leagues=("Premier League",),
        clubs=(),
        data_types=frozenset({DataType.INJURY}),
        terms_url="https://example.test/terms",
        robots_url="https://example.test/robots.txt",
        automation_allowed=True,
        storage_allowed=True,
        login_required=False,
        page_structure="JSON",
        update_frequency="hourly",
        parser_difficulty="low",
        status=SourceStatus.APPROVED,
        source_policy=SourcePolicy.OFFICIAL_PUBLIC_API,
        notes="test",
    )


def _raw(content: bytes, media_type: str) -> RawPayload:
    return RawPayload(
        source_name="official-club",
        source_url="https://example.test/news/one",
        captured_at=NOW,
        content=content,
        media_type=media_type,
        source_policy=SourcePolicy.RESEARCH_ONLY,
    )


def test_source_registry_covers_five_leagues_and_two_clubs_each() -> None:
    sources = load_source_registry()
    clubs_by_league: dict[str, set[str]] = {}
    for source in sources:
        for league in source.leagues:
            clubs_by_league.setdefault(league, set()).update(source.clubs)

    assert len(sources) == 17
    assert all(
        len(clubs_by_league[league]) >= 2
        for league in ("Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1")
    )
    assert {source.name for source in sources if source.status is SourceStatus.APPROVED} == {
        "API-Football",
        "Manual official evidence",
    }


def test_robots_longest_match_and_crawl_delay() -> None:
    policy = RobotsPolicy("""User-agent: *
Disallow: /private/
Allow: /private/public/
Crawl-delay: 30
""")
    assert policy.allowed("/news/a", user_agent="FootballAgentBot") is True
    assert policy.allowed("/private/a", user_agent="FootballAgentBot") is False
    assert policy.allowed("/private/public/a", user_agent="FootballAgentBot") is True
    assert policy.crawl_delay(user_agent="FootballAgentBot") == 30


def test_http_client_rejects_nonapproved_source_before_network(tmp_path) -> None:
    source = _approved_source()
    rejected = replace(
        source,
        status=SourceStatus.REJECTED,
        source_policy=SourcePolicy.REJECTED,
        automation_allowed=False,
        storage_allowed=False,
    )
    client = CompliantHttpClient(
        tmp_path,
        request_budget=1,
        user_agent="FootballAgentBot/1.0 contact=ops@example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    with pytest.raises(SourceAccessDenied):
        client.fetch(rejected, "https://example.test/news", cache_ttl=timedelta(0))
    assert client.requests == 0


def test_http_client_rejects_hostname_prefix_spoof_before_network(tmp_path) -> None:
    client = CompliantHttpClient(
        tmp_path,
        request_budget=1,
        user_agent="FootballAgentBot/1.0 contact=ops@example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )

    with pytest.raises(SourceAccessDenied):
        client.fetch(
            _approved_source(),
            "https://example.test.attacker.invalid/data",
            cache_ttl=timedelta(0),
        )

    assert client.requests == 0


def test_http_client_does_not_follow_unaudited_redirect(tmp_path) -> None:
    client = CompliantHttpClient(
        tmp_path,
        request_budget=1,
        user_agent="FootballAgentBot/1.0 contact=ops@example.test",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": "https://other.example.test/private"},
            )
        ),
    )

    with pytest.raises(SourceAccessDenied, match="separately audited"):
        client.fetch(
            _approved_source(),
            "https://example.test/redirect",
            cache_ttl=timedelta(0),
        )

    assert client.requests == 1


def test_http_client_caches_and_uses_etag_conditionals(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={"ok": True},
                headers={"etag": '"v1"', "content-type": "application/json"},
            )
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    ticks = iter((NOW, NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=1)))
    client = CompliantHttpClient(
        tmp_path,
        request_budget=2,
        user_agent="FootballAgentBot/1.0 contact=ops@example.test",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(handler),
        clock=lambda: next(ticks),
    )
    source = _approved_source()
    first = client.fetch(source, "https://example.test/data", cache_ttl=timedelta(0))
    second = client.fetch(source, "https://example.test/data", cache_ttl=timedelta(0))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.status_code == 304
    assert client.requests == 2


def test_static_html_jsonld_parser_preserves_title_text_and_time() -> None:
    html = b"""<html lang='en'><head>
<script type='application/ld+json'>{"@type":"Organization","name":"Club"}</script>
<script type='application/ld+json'>
{"@type":"NewsArticle","headline":"Team update","datePublished":"2026-10-10T10:00:00Z",
"articleBody":"Player returned to training."}</script></head><body></body></html>"""
    article = StaticHtmlArticleParser().parse(_raw(html, "text/html"))

    assert article.title == "Team update"
    assert article.text == "Player returned to training."
    assert article.published_at == datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
    assert article.language == "en"


def test_structured_json_rss_and_sitemap_parsers() -> None:
    document = _raw(json.dumps({"news": {"status": "fit"}}).encode(), "application/json")
    assert StructuredJsonParser().extract(document, paths={"status": ("news", "status")}) == {
        "status": "fit"
    }

    feed = _raw(
        b"""<rss><channel><item><title>Squad news</title>
<link>https://example.test/news/squad</link><pubDate>Sat, 10 Oct 2026 10:00:00 GMT</pubDate>
<description>Official squad</description></item></channel></rss>""",
        "application/rss+xml",
    )
    (entry,) = FeedParser().parse(feed)
    assert entry.title == "Squad news"
    assert entry.published_at == datetime(2026, 10, 10, 10, 0, tzinfo=UTC)

    sitemap = _raw(
        b"""<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url>
<loc>https://example.test/news/squad</loc><lastmod>2026-10-10T10:00:00Z</lastmod>
</url></urlset>""",
        "application/xml",
    )
    (page,) = SitemapParser().parse(sitemap)
    assert page.url == "https://example.test/news/squad"
    assert page.last_modified == datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
