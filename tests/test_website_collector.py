"""Offline tests for the WebsiteCollector's pure helpers (no browser needed)."""

from backend.collectors.website import extract_text, subpage_links

SAMPLE = """
<html><head>
  <title>  Acme Robotics </title>
  <meta name="description" content="We build robots.">
  <meta property="og:site_name" content="Acme">
  <script>var tracking = "junk";</script>
  <style>.x{color:red}</style>
</head><body>
  <nav><a href="/about-us">About us</a><a href="/pricing">Pricing</a></nav>
  <main>
    <h1>Acme Robotics</h1>
    <p>Robots for warehouses.</p>
    <p>Robots for warehouses.</p>
    <script>console.log("should not appear")</script>
  </main>
  <footer>
    <a href="https://twitter.com/acme">Twitter</a>
    <a href="/team">Meet the team</a>
    <a href="/about-us#history">History</a>
    <a href="mailto:hi@acme.io">Mail</a>
    <a href="/blog/post-1">Random post</a>
  </footer>
</body></html>
"""


def test_extract_text_strips_junk_and_dedupes():
    text, facts = extract_text(SAMPLE)
    assert "Robots for warehouses." in text
    assert text.count("Robots for warehouses.") == 1  # consecutive dupes dropped
    assert "tracking" not in text and "should not appear" not in text
    assert facts["title"] == "Acme Robotics"
    assert facts["meta_description"] == "We build robots."
    assert facts["og_site_name"] == "Acme"


def test_subpage_links_allowlist_and_same_host():
    links = subpage_links(SAMPLE, "https://acme.io/")
    urls = {u for _, u in links}
    assert "https://acme.io/about-us" in urls          # path hint
    assert "https://acme.io/team" in urls              # anchor-text hint
    assert all("twitter.com" not in u for u in urls)   # off-host dropped
    assert all("mailto" not in u for u in urls)
    assert all("blog" not in u for u in urls)          # no hint → dropped
    # fragment variant of about-us deduped to one entry
    assert sum("about-us" in u for u in urls) == 1


def test_subpage_links_skips_self():
    html = '<a href="https://acme.io/">About our homepage</a>'
    assert subpage_links(html, "https://acme.io/") == []
