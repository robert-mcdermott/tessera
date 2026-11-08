"""The public site's stylesheet and icon, in the organization's colors (see tessera.branding)."""

from __future__ import annotations

from tessera.branding import Colors, palette
from tessera.config import Config


def _vars(c: Colors) -> str:
    return (
        f"--bg:{c.bg};--surface:{c.surface};--text:{c.text};--muted:{c.muted};"
        f"--border:{c.border};--accent:{c.accent};--on-accent:{c.on_accent};--stripe:{c.stripe};"
    )


def stylesheet(config: Config) -> str:
    day, night = palette(config)
    # Pages follow the reader's preference; an embed can be held to one mode (data-theme).
    return (
        f":root{{color-scheme:light dark;{_vars(day)}}}\n"
        f"@media (prefers-color-scheme: dark){{:root:not([data-theme=light]){{{_vars(night)}}}}}\n"
        f":root[data-theme=light]{{color-scheme:light}}\n"
        f":root[data-theme=dark]{{color-scheme:dark;{_vars(night)}}}\n" + BASE
    )


BASE = """
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;font-feature-settings:"kern","liga"}
a{color:var(--accent);text-underline-offset:.18em;text-decoration-thickness:.06em}
a:hover{text-decoration-thickness:.12em}
a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:3px}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px}
.skip{position:absolute;left:-9999px;top:8px;background:var(--accent);color:var(--on-accent);padding:8px 14px;border-radius:6px;z-index:10}
.skip:focus{left:16px}
.site-header{background:var(--surface);border-bottom:3px solid var(--stripe)}
.site-header .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:64px;flex-wrap:wrap}
.brand{display:inline-flex;align-items:center;gap:10px;color:var(--text);text-decoration:none;font-weight:650;letter-spacing:.01em}
.brand-mark{width:18px;height:18px;border-radius:4px;background:var(--accent);box-shadow:6px 6px 0 -2px var(--stripe)}
.site-header nav ul{display:flex;gap:4px;list-style:none;margin:0;padding:0}
.site-header nav a{display:block;padding:8px 12px;border-radius:8px;color:var(--text);text-decoration:none;font-weight:550}
.site-header nav a:hover{background:color-mix(in srgb,var(--accent) 10%,transparent)}
.site-header nav a[aria-current="page"]{color:var(--accent);box-shadow:inset 0 -2px 0 var(--accent)}
main.wrap{padding-top:40px;padding-bottom:64px}
.page-head{margin-bottom:32px;max-width:780px}
.page-head h1{font-size:clamp(28px,4vw,40px);line-height:1.15;margin:4px 0 12px;letter-spacing:-.01em}
.eyebrow{margin:0;font:600 12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}
.eyebrow a{color:var(--muted)}
.lede{font-size:18px;color:var(--muted);margin:0 0 12px}
.contact{display:flex;gap:16px;flex-wrap:wrap;margin:0}
.columns{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:32px;align-items:start}
@media (max-width:860px){.columns{grid-template-columns:minmax(0,1fr)}}
.block{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px 24px;margin-bottom:20px}
.block h2{font-size:18px;margin:0 0 12px;letter-spacing:.01em}
.block h3{font-size:15px;margin:16px 0 6px}
.block p{margin:0 0 10px}
.plain{list-style:none;margin:0;padding:0}
.plain>li{padding:6px 0}
.plain.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:0 20px}
.meta{color:var(--muted);font-size:14px}
.cards{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}
.card{border:1px solid var(--border);border-radius:12px;padding:16px 18px;background:var(--bg)}
.card h3{margin:0 0 6px;font-size:16px}
.card p{margin:0 0 6px;font-size:15px}
.citations{margin:0;padding-left:1.4em}
.cite{padding:6px 0;line-height:1.5}
.cite .title{font-weight:600}
.cite .authors,.cite .venue,.cite .links{color:var(--muted);font-size:14px}
.flag{color:var(--text);background:color-mix(in srgb,var(--accent) 14%,transparent);padding:0 6px;border-radius:4px;font-size:12px}
.records>li{border-top:1px solid var(--border)}
.records>li:first-child{border-top:0}
.records .title{font-weight:600;display:block}
.chips{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
.chips li{border:1px solid var(--border);border-radius:999px;padding:2px 10px;font-size:13px;color:var(--text)}
.areas{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:minmax(0,1fr) 72px 3em;gap:6px 10px;align-items:center;font-size:14px}
.areas li{display:contents}
.meter{width:72px;height:6px}
.meter rect{fill:var(--accent)}
.meter .track{fill:var(--border)}
.pct{text-align:right;color:var(--muted)}
.chart svg{height:96px;width:auto;max-width:100%;display:block}
.chart rect{fill:var(--accent)}
.chart text{fill:var(--muted);font-size:9px}
.chart{margin:0 0 12px}
.tag{display:inline-block;font:600 11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);border:1px solid var(--border);border-radius:4px;padding:0 6px;vertical-align:2px}
.themes>li,.openings>li,.news>li{border-top:1px solid var(--border);padding:12px 0}
.themes>li:first-child,.openings>li:first-child,.news>li:first-child{border-top:0}
.links{margin:6px 0 0;padding-left:1.2em;font-size:14px}
time{font:600 12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);margin-right:6px}
details summary{cursor:pointer;color:var(--accent);font-weight:550;padding:6px 0}
.site-footer{border-top:1px solid var(--border);color:var(--muted);font-size:14px;padding:24px 0 40px}
.site-footer p{margin:0 0 4px}
.footer-links{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:4px 16px}
body.embed{background:var(--bg)}
body.embed main{padding:12px 16px}
.embed-title{font-size:15px;font-weight:600;margin:0 0 8px;color:var(--muted)}
.embed-title a{color:inherit}
body.embed .block{border:0;padding:0;margin:0 0 16px;background:transparent}
.embed-footer{padding:0 16px 12px;font-size:13px;color:var(--muted)}
.embed-footer p{margin:0}
@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
@media print{.site-header nav,.skip{display:none}.block{border:0;padding:0}}
"""


def favicon(config: Config) -> str:
    day, _ = palette(config)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        f'<rect x="3" y="3" width="20" height="20" rx="5" fill="{day.accent}"/>'
        f'<rect x="11" y="11" width="18" height="18" rx="5" fill="{day.stripe}" opacity=".55"/>'
        "</svg>"
    )


__all__ = ["favicon", "stylesheet"]
