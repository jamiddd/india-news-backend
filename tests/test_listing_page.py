import json

from app.services.listing_page import parse_listing_entries


def _page(*blocks):
    return "<html>" + "".join(
        f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks
    ) + "</html>"


def test_extracts_item_list_entries():
    html = _page(
        {"@type": "WebPage", "name": "ignored"},
        {"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": "1", "url": "https://x.com/a-1.html", "name": "Story A"},
            {"@type": "ListItem", "position": "2", "url": "https://x.com/b-2.html", "name": "Story B"},
        ]},
    )
    entries = parse_listing_entries(html)
    assert [(e.title, e.link) for e in entries] == [
        ("Story A", "https://x.com/a-1.html"),
        ("Story B", "https://x.com/b-2.html"),
    ]


def test_skips_duplicates_and_incomplete_items():
    html = _page({"@type": "ItemList", "itemListElement": [
        {"url": "https://x.com/a-1.html", "name": "A"},
        {"url": "https://x.com/a-1.html", "name": "A again"},
        {"url": "https://x.com/no-name.html"},
        {"name": "no url"},
    ]})
    assert [e.link for e in parse_listing_entries(html)] == ["https://x.com/a-1.html"]


def test_bad_json_and_no_list_return_empty():
    assert parse_listing_entries('<script type="application/ld+json">{oops</script>') == []
    assert parse_listing_entries("<html></html>") == []
    assert parse_listing_entries("") == []
