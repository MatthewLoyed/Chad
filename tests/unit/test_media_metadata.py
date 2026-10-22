"""Unit tests for OG/Twitter image + JSON-LD VideoObject parsing (issue #331)."""

from __future__ import annotations

from src.tools.web.media_metadata import parse_media_metadata


def test_parses_og_image_and_alt():
    html = """
    <html><head>
      <meta property="og:image" content="/img/hero.jpg">
      <meta property="og:image:alt" content="Vendor hero shot">
    </head><body></body></html>
    """
    result = parse_media_metadata(html, base_url="https://example.com/vendors/acme")
    assert result["images"] == [
        {"url": "https://example.com/img/hero.jpg", "alt": "Vendor hero shot", "role": "og:image"}
    ]
    assert result["videos"] == []


def test_parses_twitter_image_and_dedupes_against_og():
    html = """
    <html><head>
      <meta property="og:image" content="https://cdn.example.com/hero.jpg">
      <meta name="twitter:image" content="https://cdn.example.com/hero.jpg">
      <meta name="twitter:image" content="https://cdn.example.com/alt.jpg">
    </head><body></body></html>
    """
    result = parse_media_metadata(html)
    urls = [img["url"] for img in result["images"]]
    assert urls == ["https://cdn.example.com/hero.jpg", "https://cdn.example.com/alt.jpg"]


def test_parses_json_ld_video_object():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "VideoObject", "name": "Highlight Reel",
     "embedUrl": "https://www.youtube.com/embed/abc123"}
    </script>
    </head><body></body></html>
    """
    result = parse_media_metadata(html)
    assert result["videos"] == [
        {"url": "https://www.youtube.com/embed/abc123", "title": "Highlight Reel", "platform": "youtube"}
    ]


def test_parses_json_ld_graph_wrapper():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@graph": [{"@type": "WebPage"}, {"@type": "VideoObject", "name": "Demo",
                 "contentUrl": "https://vimeo.com/12345"}]}
    </script>
    </head><body></body></html>
    """
    result = parse_media_metadata(html)
    assert result["videos"] == [
        {"url": "https://vimeo.com/12345", "title": "Demo", "platform": "vimeo"}
    ]


def test_malformed_json_ld_does_not_raise():
    html = """
    <html><head>
    <script type="application/ld+json">{not valid json</script>
    </head><body></body></html>
    """
    result = parse_media_metadata(html)
    assert result == {"images": [], "videos": []}


def test_empty_html_returns_empty_result():
    assert parse_media_metadata("") == {"images": [], "videos": []}
    assert parse_media_metadata("   ") == {"images": [], "videos": []}


def test_no_media_tags_returns_empty_lists():
    html = "<html><body><h1>Just a page</h1><p>No metadata here.</p></body></html>"
    result = parse_media_metadata(html)
    assert result == {"images": [], "videos": []}


def test_dom_fallback_disabled_by_default():
    html = """
    <html><body><main><img src="https://cdn.example.com/hero.jpg" width="1200" height="800" alt="Hero">
    </main></body></html>
    """
    result = parse_media_metadata(html)
    assert result["images"] == []


def test_dom_fallback_picks_largest_content_image():
    html = """
    <html><body>
      <header><img src="https://cdn.example.com/logo.png" width="120" height="40"></header>
      <main>
        <img src="https://cdn.example.com/thumb.jpg" width="150" height="100" alt="thumb">
        <img data-src="https://cdn.example.com/hero.jpg" width="1200" height="800" alt="Hero shot">
      </main>
    </body></html>
    """
    result = parse_media_metadata(html, dom_image_fallback=True)
    assert result["images"] == [
        {"url": "https://cdn.example.com/hero.jpg", "alt": "Hero shot", "role": "dom:hero"}
    ]


def test_dom_fallback_resolves_srcset_and_skips_logo_icon():
    html = """
    <html><body><main>
      <img class="logo" src="https://cdn.example.com/logo-icon.png" width="300" height="300">
      <img srcset="https://cdn.example.com/small.jpg 400w, https://cdn.example.com/large.jpg 1200w"
           width="1200" height="900" alt="Gallery">
    </main></body></html>
    """
    result = parse_media_metadata(html, dom_image_fallback=True)
    assert result["images"] == [
        {"url": "https://cdn.example.com/large.jpg", "alt": "Gallery", "role": "dom:hero"}
    ]


def test_dom_fallback_skips_tiny_images():
    html = """
    <html><body><main>
      <img src="https://cdn.example.com/pixel.gif" width="1" height="1">
    </main></body></html>
    """
    result = parse_media_metadata(html, dom_image_fallback=True)
    assert result["images"] == []


def test_dom_fallback_not_used_when_og_image_present():
    html = """
    <html><head><meta property="og:image" content="https://cdn.example.com/og.jpg"></head>
    <body><main><img src="https://cdn.example.com/hero.jpg" width="2000" height="2000"></main></body></html>
    """
    result = parse_media_metadata(html, dom_image_fallback=True)
    assert result["images"] == [
        {"url": "https://cdn.example.com/og.jpg", "alt": "", "role": "og:image"}
    ]
