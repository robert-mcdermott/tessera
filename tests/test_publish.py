"""The public site's page checks (PLT-07)."""

from __future__ import annotations

from tessera.publish.checks import check_page

GOOD = """<!doctype html><html lang="en"><head><title>A</title></head><body>
<header><nav aria-label="Site"><a href="index.html">Home</a></nav></header>
<main><h1>A</h1><h2>B</h2><h3>C</h3><a href="https://example.org" aria-label="Example">↗</a></main>
<footer><p>x</p></footer></body></html>"""


def problems(html: str) -> list[str]:
    return [p.split(": ", 1)[1] for p in check_page("index.html", html, {"index.html"})]


def test_a_good_page_passes() -> None:
    assert problems(GOOD) == []


def test_each_rule() -> None:
    assert "no language on <html>" in problems(GOOD.replace(' lang="en"', ""))
    assert any("exactly one <h1>" in p for p in problems(GOOD.replace("<h1>A</h1>", "")))
    assert "heading levels jump from h1 to h3" in problems(GOOD.replace("<h2>B</h2>", ""))
    assert "a <nav> has no label" in problems(GOOD.replace(' aria-label="Site"', ""))
    assert "no <footer> landmark" in problems(GOOD.replace("<footer><p>x</p></footer>", ""))
    assert any("has no text" in p for p in problems(GOOD.replace(' aria-label="Example">↗', ">")))
    assert "a link points to a missing page: groups/index.html" in problems(
        GOOD.replace('href="index.html"', 'href="groups/index.html"')
    )
    assert "a page carries a script" in problems(GOOD.replace("<h2>", "<script>x</script><h2>"))
    assert "<a> links to javascript:" in problems(
        GOOD.replace("https://example.org", "javascript:x")
    )
    assert "<p> has an inline style" in problems(GOOD.replace("<p>x", '<p style="color:red">x'))
    assert "<main> has an event handler (onclick)" in problems(
        GOOD.replace("<main>", '<main onclick="x()">')
    )
