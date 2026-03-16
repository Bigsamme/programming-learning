"""
========================================================
  FACEBOOK MARKETPLACE SNOWBOARD SNIPER v5
========================================================
Every approved listing is saved as:
  1. finds/listing_<id>.html  — rich single-listing page
  2. finds/index.html          — live dashboard, auto-refreshes,
                                 shows all finds as cards with
                                 images, AI notes, price, location
                                 and a direct link button.

Just open finds/index.html in your browser and leave it open.
It auto-refreshes every 5 minutes so new finds appear automatically.

SETUP:
------
  pip install playwright schedule requests Pillow
  playwright install chromium
  ollama pull llava:13b
  ollama serve               ← keep running in a terminal
  python fb_sniper.py

OUTPUTS:
  finds/index.html           ← open this in your browser
  finds/listing_<id>.html    ← individual listing pages
  finds.csv                  ← spreadsheet of all finds
  seen_listings.json         ← tracks what's already been checked
  fb_session.json            ← saved Facebook login
========================================================
"""

import base64
import csv
import io
import json
import os
import time
import urllib.parse
import requests
import schedule
from datetime import datetime
from pathlib import Path
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright

# ======================================================
#   CONFIG
# ======================================================

PRIMARY_BOARDS = [
    "Burton Custom snowboard",
    "Capita Mercury snowboard",
    "Never Summer Proto Synthesis snowboard",
    "Jones Mountain Twin snowboard",
    "Lib Tech Travis Rice Pro snowboard",
    "GNU Riders Choice snowboard",
    "Salomon Assassin snowboard",
    "Never Summer Shaper Twin snowboard",
    "Burton Custom Flying V snowboard",
    "Lib Tech TRice Orca snowboard",
]

PROTEUS_BOARDS = [
    "Proteus snowboard",
    "Proteus Luminance snowboard",
    "Proteus Ace snowboard",
    "Proteus CaliBurn snowboard",
    "Proteus Sharknado snowboard",
    "Proteus Yeti snowboard",
    "Proteus Poseidon snowboard",
    "Proteus Destroyer snowboard",
    "Proteus Protector snowboard",
    "Proteus Koi snowboard",
    "Proteus Peaks snowboard",
    "Proteus Pilot snowboard",
    "Proteus Arachnid snowboard",
    "Proteus Alpha Omega snowboard",
    "Proteus Explorer snowboard",
    "Proteus Sasquatch snowboard",
    "Proteus Haze snowboard",
    "Proteus Polygonus snowboard",
    "Proteus Dragon Slayer snowboard",
    "Proteus Full Lotus snowboard",
    "Proteus New Day snowboard",
    "Proteus Fonk snowboard",
    "Proteus High Tide snowboard",
    "Proteus Dear John snowboard",
    "Proteus Hex snowboard",
]

TARGET_SIZES  = ["150", "151", "152", "153", "154", "155", "156"]
MIN_PRICE     = 50
MAX_PRICE     = 400

RIDER_PROFILE = """
Buyer: beginner-intermediate snowboarder (4 sessions), 5'5", 135lbs, bulking to ~155lbs.
Wants a one-board all-mountain solution:
- Holds an edge on hard East Coast ice/hardpack (better than a rental)
- Can handle park, jumps, tricks as they progress
- Full camber or hybrid camber preferred (NOT pure rocker, NOT pure jib board)
- Size 150-156cm, budget $50-$400
- Located Chapel Hill NC, willing to travel or ship
"""

OLLAMA_MODEL  = "llava:7b"   # fits entirely in 12GB VRAM — no CPU spillover
OLLAMA_URL    = "http://localhost:11434/api/generate"
MAX_IMAGES    = 3
MAX_IMG_DIM   = 600
LLM_WORKERS   = 3     # parallel LLM eval threads — GPU queues them, way faster than serial
BATCH_SIZE    = 6     # scrape this many listing detail pages, then run LLM on the batch before continuing
TEST_MODE     = False  # set True OR run: python fb_sniper.py --test
TEST_LIMIT    = 10     # how many listings to collect before stopping and running LLM

SEEN_FILE     = "seen_listings.json"
SESSION_FILE  = "fb_session.json"
CSV_FILE      = "finds.csv"
FINDS_DIR       = Path("finds")
LISTINGS_DIR    = FINDS_DIR / "listings"   # approved: HTML + JSON go here
REJECTED_DIR    = FINDS_DIR / "rejected"   # rejected: JSON only

CSV_HEADERS   = [
    "found_at", "board_searched", "type", "title",
    "price", "location", "description_snippet",
    "url", "ai_verdict", "ai_description", "listing_page"
]

# ======================================================
#   LOGGING
# ======================================================

def ts():
    return datetime.now().strftime("%H:%M:%S")

def log(msg, indent=0):
    print(f"[{ts()}] {'  ' * indent}{msg}")

def log_section(title):
    bar = "─" * max(1, 54 - len(title))
    print(f"\n[{ts()}] ── {title} {bar}")

# ======================================================
#   HTML GENERATION
# ======================================================

# Shared CSS used by both the dashboard and individual pages
SHARED_CSS = """
  @import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

  :root {
    --bg:        #0a0c0f;
    --surface:   #111318;
    --surface2:  #181c23;
    --border:    #232830;
    --accent:    #4fc3f7;
    --accent2:   #81d4fa;
    --green:     #69f0ae;
    --red:       #ff5252;
    --amber:     #ffd740;
    --text:      #e8eaf0;
    --muted:     #6b7280;
    --snow:      #c8d6e5;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
    line-height: 1.6;
    min-height: 100vh;
  }

  /* Subtle snow grain overlay */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
    pointer-events: none;
    z-index: 0;
    opacity: 0.4;
  }

  a { color: var(--accent); text-decoration: none; }
  a:hover { color: var(--accent2); }

  .tag {
    display: inline-block;
    font-family: 'DM Mono', monospace;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: .08em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 3px;
    border: 1px solid currentColor;
  }
  .tag-proteus { color: var(--amber);  border-color: var(--amber);  background: rgba(255,215,64,.07); }
  .tag-primary { color: var(--accent); border-color: var(--accent); background: rgba(79,195,247,.07); }
  .tag-yes     { color: var(--green);  border-color: var(--green);  background: rgba(105,240,174,.07);}
  .tag-no      { color: var(--red);    border-color: var(--red);    background: rgba(255,82,82,.07);  }
"""

def build_cards_html(listings, show_fb_btn=True):
    """Render a list of listings as card HTML."""
    cards_html = ""
    for L in reversed(listings):
        imgs = L.get("images_b64", [])
        if imgs:
            thumbs    = "".join(f'<img src="data:image/jpeg;base64,{b64}" alt="photo">' for b64 in imgs[:3])
            img_strip = f'<div class="img-strip">{thumbs}</div>'
        else:
            img_strip = '<div class="img-strip no-img"><span>No photos available</span></div>'

        verdict     = L.get("ai_verdict", "")
        verdict_cls = "tag-yes" if "YES" in verdict.upper() else "tag-no"
        verdict_lbl = "✓ Approved" if "YES" in verdict.upper() else "✗ Rejected"
        board_type  = "Proteus" if L.get("is_proteus") else "Primary"
        type_cls    = "tag-proteus" if L.get("is_proteus") else "tag-primary"
        found_at    = L.get("found_at", "")
        listing_pg  = L.get("listing_page", "")
        detail_link = f'<a href="{listing_pg}" class="detail-btn">Full page ↗</a>' if listing_pg else ""
        fb_btn      = f'<a href="{L.get("url","#")}" target="_blank" class="btn-primary">Open on Facebook ↗</a>' if show_fb_btn else \
                      f'<a href="{L.get("url","#")}" target="_blank" class="btn-rejected">View anyway ↗</a>'
        desc        = L.get("ai_description", "No AI description available.")
        loc         = L.get("location", "")
        loc_html    = f'<span class="loc">📍 {loc}</span>' if loc else ""

        cards_html += f"""
        <article class="card" data-type="{board_type.lower()}" data-title="{L.get('title','').lower()}">
          {img_strip}
          <div class="card-body">
            <div class="card-meta">
              <span class="tag {type_cls}">{board_type}</span>
              <span class="tag {verdict_cls}">{verdict_lbl}</span>
              <span class="card-time">{found_at}</span>
            </div>
            <h2 class="card-title">{L.get('title','Unknown')}</h2>
            <div class="card-price">{L.get('price','')}</div>
            {loc_html}
            <p class="card-desc">{desc}</p>
            <div class="card-searched">Triggered by: <em>{L.get('board_searched','')}</em></div>
            <div class="card-actions">
              {fb_btn}
              {detail_link}
            </div>
          </div>
        </article>"""
    return cards_html


def build_dashboard_html(listings, rejected_listings):
    """Build the full index.html dashboard with Approved / Rejected tabs."""

    approved_cards = build_cards_html(listings,          show_fb_btn=True)
    rejected_cards = build_cards_html(rejected_listings, show_fb_btn=False)

    count          = len(listings)
    rej_count      = len(rejected_listings)
    now_str        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    approved_empty = '<div class="empty-state"><div class="icon">🏔️</div><h2>No approved finds yet</h2><p>The sniper is running — check back soon.</p></div>'
    rejected_empty = '<div class="empty-state"><div class="icon">🗑️</div><h2>No rejected listings yet</h2><p>Rejections will appear here once the sniper has run.</p></div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>🏂 Snowboard Sniper</title>
  <style>
    {SHARED_CSS}

    /* ── HEADER ─────────────────────────────────────── */
    header {{
      position: relative; z-index: 1;
      padding: 48px 40px 0;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(79,195,247,.06) 0%, transparent 100%);
    }}
    .header-top {{
      max-width: 1400px; margin: 0 auto;
      display: flex; align-items: flex-end;
      justify-content: space-between; gap: 24px; flex-wrap: wrap;
      padding-bottom: 28px;
    }}
    .logo {{
      font-family: 'Bebas Neue', sans-serif; font-size: 52px;
      letter-spacing: .04em; line-height: 1; color: var(--snow);
      text-shadow: 0 0 40px rgba(79,195,247,.3);
    }}
    .logo span {{ color: var(--accent); }}
    .subtitle {{ font-size: 13px; color: var(--muted); margin-top: 6px; font-family: 'DM Mono', monospace; }}
    .stats {{ display: flex; gap: 32px; align-items: center; }}
    .stat {{ text-align: right; }}
    .stat-val {{ font-family: 'Bebas Neue', sans-serif; font-size: 36px; color: var(--accent); line-height: 1; }}
    .stat-lbl {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .1em; font-family: 'DM Mono', monospace; }}
    .last-run {{ font-family: 'DM Mono', monospace; font-size: 11px; color: var(--muted); margin-top: 4px; }}
    .pulse {{
      display: inline-block; width: 7px; height: 7px; border-radius: 50%;
      background: var(--green); margin-right: 6px;
      animation: pulse 2s ease-in-out infinite;
    }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50%       {{ opacity: .4; transform: scale(.7); }}
    }}

    /* ── TABS ────────────────────────────────────────── */
    .tab-bar {{
      max-width: 1400px; margin: 0 auto;
      display: flex; gap: 0; align-items: flex-end;
    }}
    .tab {{
      font-family: 'DM Mono', monospace;
      font-size: 12px; text-transform: uppercase; letter-spacing: .1em;
      padding: 12px 28px; cursor: pointer; border: none;
      background: transparent; color: var(--muted);
      border-bottom: 2px solid transparent;
      transition: color .15s, border-color .15s;
      display: flex; align-items: center; gap: 8px;
    }}
    .tab:hover {{ color: var(--text); }}
    .tab.active {{ color: var(--text); border-bottom-color: var(--accent); }}
    .tab.tab-rejected.active {{ border-bottom-color: var(--red); color: var(--red); }}
    .tab-badge {{
      font-size: 10px; padding: 2px 7px; border-radius: 10px;
      font-weight: 600;
    }}
    .tab-approved .tab-badge {{ background: rgba(105,240,174,.15); color: var(--green); }}
    .tab-rejected .tab-badge {{ background: rgba(255,82,82,.15);   color: var(--red);   }}

    /* ── PANELS ──────────────────────────────────────── */
    .panel {{ display: none; }}
    .panel.active {{ display: block; }}

    /* ── MAIN ────────────────────────────────────────── */
    main {{
      position: relative; z-index: 1;
      max-width: 1400px; margin: 0 auto; padding: 40px;
    }}

    .empty-state {{
      text-align: center; padding: 120px 40px; color: var(--muted);
    }}
    .empty-state .icon {{ font-size: 64px; margin-bottom: 16px; }}
    .empty-state h2 {{
      font-family: 'Bebas Neue', sans-serif; font-size: 32px;
      color: var(--snow); margin-bottom: 8px;
    }}

    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 24px;
    }}

    /* ── CARD ────────────────────────────────────────── */
    .card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 12px; overflow: hidden;
      display: flex; flex-direction: column;
      transition: transform .2s, border-color .2s, box-shadow .2s;
    }}
    .card:hover {{
      transform: translateY(-3px);
      border-color: rgba(79,195,247,.3);
      box-shadow: 0 12px 40px rgba(0,0,0,.4), 0 0 0 1px rgba(79,195,247,.1);
    }}
    .panel-rejected .card:hover {{
      border-color: rgba(255,82,82,.25);
      box-shadow: 0 12px 40px rgba(0,0,0,.4), 0 0 0 1px rgba(255,82,82,.08);
    }}
    .panel-rejected .card {{ opacity: .85; }}

    .img-strip {{
      width: 100%; height: 220px;
      background: var(--surface2); display: flex;
      overflow: hidden; position: relative;
    }}
    .img-strip img {{
      flex: 1; object-fit: cover; height: 100%;
      border-right: 1px solid var(--border); transition: transform .3s;
    }}
    .img-strip img:last-child {{ border-right: none; }}
    .card:hover .img-strip img {{ transform: scale(1.04); }}
    .no-img {{
      align-items: center; justify-content: center;
      color: var(--muted); font-family: 'DM Mono', monospace; font-size: 12px;
    }}

    .card-body {{ padding: 20px; display: flex; flex-direction: column; gap: 10px; flex: 1; }}
    .card-meta {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .card-time {{ font-family: 'DM Mono', monospace; font-size: 10px; color: var(--muted); margin-left: auto; }}
    .card-title {{ font-size: 16px; font-weight: 600; color: var(--snow); line-height: 1.3; }}
    .card-price {{ font-family: 'Bebas Neue', sans-serif; font-size: 28px; color: var(--green); letter-spacing: .04em; line-height: 1; }}
    .loc {{ font-size: 12px; color: var(--muted); }}
    .card-desc {{
      font-size: 13px; color: #9ca3af; line-height: 1.5;
      border-left: 2px solid var(--border); padding-left: 10px; font-style: italic;
    }}
    .card-searched {{ font-family: 'DM Mono', monospace; font-size: 10px; color: var(--muted); }}
    .card-searched em {{ color: var(--accent); font-style: normal; }}

    .card-actions {{ display: flex; gap: 8px; margin-top: auto; padding-top: 4px; }}
    .btn-primary {{
      flex: 1; display: inline-block; text-align: center;
      background: var(--accent); color: #000 !important;
      font-weight: 600; font-size: 12px; padding: 8px 14px;
      border-radius: 6px; transition: background .15s, transform .1s; letter-spacing: .03em;
    }}
    .btn-primary:hover {{ background: var(--accent2); transform: translateY(-1px); color: #000 !important; }}
    .btn-rejected {{
      flex: 1; display: inline-block; text-align: center;
      border: 1px solid rgba(255,82,82,.3); color: var(--red) !important;
      font-size: 12px; padding: 8px 14px; border-radius: 6px;
      transition: border-color .15s, background .15s;
    }}
    .btn-rejected:hover {{ background: rgba(255,82,82,.08); border-color: var(--red); }}
    .detail-btn {{
      display: inline-block; text-align: center;
      border: 1px solid var(--border); color: var(--muted) !important;
      font-size: 12px; padding: 8px 14px; border-radius: 6px;
      transition: border-color .15s, color .15s;
    }}
    .detail-btn:hover {{ border-color: var(--accent); color: var(--accent) !important; }}

    /* ── TOOLBAR ─────────────────────────────────────── */
    .toolbar {{
      display: flex; gap: 12px; margin-bottom: 28px;
      flex-wrap: wrap; align-items: center;
    }}
    .filter-btn {{
      background: var(--surface); border: 1px solid var(--border);
      color: var(--muted); font-family: 'DM Mono', monospace;
      font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
      padding: 6px 14px; border-radius: 20px; cursor: pointer; transition: all .15s;
    }}
    .filter-btn:hover, .filter-btn.active {{
      border-color: var(--accent); color: var(--accent);
      background: rgba(79,195,247,.07);
    }}
    .search-box {{
      background: var(--surface); border: 1px solid var(--border);
      color: var(--text); font-family: 'DM Sans', sans-serif;
      font-size: 13px; padding: 6px 14px; border-radius: 20px;
      outline: none; width: 220px; transition: border-color .15s;
    }}
    .search-box:focus {{ border-color: var(--accent); }}
    .search-box::placeholder {{ color: var(--muted); }}
  </style>
</head>
<body>
  <header>
    <div class="header-top">
      <div>
        <div class="logo">🏂 BOARD<span>SNIPER</span></div>
        <div class="subtitle">Facebook Marketplace · Auto-refreshes every 5 min</div>
        <div class="last-run"><span class="pulse"></span>Last scan: {now_str}</div>
      </div>
      <div class="stats">
        <div class="stat">
          <div class="stat-val" style="color:var(--green)">{count}</div>
          <div class="stat-lbl">Approved</div>
        </div>
        <div class="stat">
          <div class="stat-val" style="color:var(--red)">{rej_count}</div>
          <div class="stat-lbl">Rejected</div>
        </div>
        <div class="stat">
          <div class="stat-val" style="color:var(--amber)">${MIN_PRICE}–{MAX_PRICE}</div>
          <div class="stat-lbl">Price Range</div>
        </div>
      </div>
    </div>

    <div class="tab-bar">
      <button class="tab tab-approved active" onclick="switchTab('approved', this)">
        ✓ Approved <span class="tab-badge">{count}</span>
      </button>
      <button class="tab tab-rejected" onclick="switchTab('rejected', this)">
        ✗ Rejected <span class="tab-badge">{rej_count}</span>
      </button>
    </div>
  </header>

  <main>
    <!-- APPROVED PANEL -->
    <div class="panel panel-approved active" id="panel-approved">
      {'<div class="empty-state"><div class="icon">🏔️</div><h2>No approved finds yet</h2><p>The sniper is running — check back soon.</p></div>' if not listings else f"""
      <div class="toolbar">
        <button class="filter-btn active" onclick="filterCards('approved','all',this)">All ({count})</button>
        <button class="filter-btn" onclick="filterCards('approved','primary',this)">Primary</button>
        <button class="filter-btn" onclick="filterCards('approved','proteus',this)">Proteus</button>
        <input class="search-box" type="text" placeholder="Search title or board…" oninput="searchCards('approved',this.value)">
      </div>
      <div class="grid" id="grid-approved">
        {approved_cards}
      </div>"""}
    </div>

    <!-- REJECTED PANEL -->
    <div class="panel panel-rejected" id="panel-rejected">
      {'<div class="empty-state"><div class="icon">🗑️</div><h2>No rejections yet</h2><p>Filtered-out listings will appear here.</p></div>' if not rejected_listings else f"""
      <div class="toolbar">
        <button class="filter-btn active" onclick="filterCards('rejected','all',this)">All ({rej_count})</button>
        <button class="filter-btn" onclick="filterCards('rejected','primary',this)">Primary</button>
        <button class="filter-btn" onclick="filterCards('rejected','proteus',this)">Proteus</button>
        <input class="search-box" type="text" placeholder="Search title or board…" oninput="searchCards('rejected',this.value)">
      </div>
      <div class="grid" id="grid-rejected">
        {rejected_cards}
      </div>"""}
    </div>
  </main>

  <script>
    function switchTab(name, btn) {{
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('panel-' + name).classList.add('active');
    }}
    function filterCards(panel, type, btn) {{
      const grid = document.getElementById('grid-' + panel);
      if (!grid) return;
      grid.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      grid.querySelectorAll('.card').forEach(card => {{
        if (type === 'all') {{ card.style.display = ''; return; }}
        card.style.display = card.dataset.type === type ? '' : 'none';
      }});
    }}
    function searchCards(panel, q) {{
      const grid = document.getElementById('grid-' + panel);
      if (!grid) return;
      q = q.toLowerCase();
      grid.querySelectorAll('.card').forEach(card => {{
        card.style.display = card.dataset.title.includes(q) || card.textContent.toLowerCase().includes(q) ? '' : 'none';
      }});
    }}
  </script>
</body>
</html>"""


def build_listing_html(listing):
    """Build a rich single-listing detail page."""
    imgs = listing.get("images_b64", [])
    gallery = ""
    if imgs:
        slides = "".join(
            f'<div class="slide{"" if i else " active"}"><img src="data:image/jpeg;base64,{b64}" alt="photo {i+1}"></div>'
            for i, b64 in enumerate(imgs)
        )
        dots = "".join(
            f'<button class="dot{"" if i else " active"}" onclick="goTo({i})"></button>'
            for i in range(len(imgs))
        )
        gallery = f'<div class="gallery"><div class="slides" id="slides">{slides}</div><div class="dots">{dots}</div></div>'
    else:
        gallery = '<div class="gallery no-photo"><span>No photos available</span></div>'

    verdict     = listing.get("ai_verdict", "")
    verdict_cls = "tag-yes" if "YES" in verdict.upper() else "tag-no"
    verdict_lbl = "✓ AI Approved" if "YES" in verdict.upper() else "✗ AI Rejected"
    board_type  = "Proteus" if listing.get("is_proteus") else "Primary"
    type_cls    = "tag-proteus" if listing.get("is_proteus") else "tag-primary"
    desc_full   = listing.get("description", "No description scraped.").replace("\n", "<br>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{listing.get('title','Listing')} — BoardSniper</title>
  <style>
    {SHARED_CSS}

    body {{ padding: 40px 20px; }}

    .back {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: 'DM Mono', monospace;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 32px;
      transition: color .15s;
    }}
    .back:hover {{ color: var(--accent); }}

    .layout {{
      max-width: 1100px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 1fr 420px;
      gap: 40px;
    }}
    @media (max-width: 800px) {{
      .layout {{ grid-template-columns: 1fr; }}
    }}

    /* Gallery */
    .gallery {{
      border-radius: 12px;
      overflow: hidden;
      background: var(--surface);
      border: 1px solid var(--border);
      position: sticky;
      top: 20px;
    }}
    .gallery.no-photo {{
      height: 300px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-family: 'DM Mono', monospace;
    }}
    .slides {{ position: relative; }}
    .slide {{
      display: none;
    }}
    .slide.active {{ display: block; }}
    .slide img {{
      width: 100%;
      aspect-ratio: 4/3;
      object-fit: cover;
      display: block;
    }}
    .dots {{
      display: flex;
      justify-content: center;
      gap: 6px;
      padding: 12px;
      background: var(--surface2);
    }}
    .dot {{
      width: 8px; height: 8px;
      border-radius: 50%;
      background: var(--border);
      border: none;
      cursor: pointer;
      transition: background .15s;
    }}
    .dot.active {{ background: var(--accent); }}

    /* Info panel */
    .info {{ display: flex; flex-direction: column; gap: 20px; }}

    .page-title {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 42px;
      color: var(--snow);
      line-height: 1.1;
      letter-spacing: .02em;
    }}

    .price-row {{
      display: flex;
      align-items: baseline;
      gap: 16px;
    }}
    .big-price {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 56px;
      color: var(--green);
      line-height: 1;
    }}
    .loc-text {{
      font-size: 13px;
      color: var(--muted);
    }}

    .tags-row {{ display: flex; gap: 8px; flex-wrap: wrap; }}

    .section {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
    }}
    .section-title {{
      font-family: 'DM Mono', monospace;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: .12em;
      color: var(--muted);
      margin-bottom: 10px;
    }}
    .section-body {{
      font-size: 13px;
      color: #9ca3af;
      line-height: 1.7;
    }}
    .ai-description {{
      font-size: 14px;
      color: var(--text);
      font-style: italic;
      line-height: 1.6;
    }}

    .meta-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}
    .meta-item {{
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
    }}
    .meta-label {{
      font-family: 'DM Mono', monospace;
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: .1em;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .meta-value {{
      font-size: 13px;
      color: var(--text);
      font-weight: 500;
    }}

    .cta {{
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .btn-big {{
      display: block;
      text-align: center;
      background: var(--accent);
      color: #000 !important;
      font-weight: 700;
      font-size: 14px;
      padding: 14px;
      border-radius: 8px;
      letter-spacing: .04em;
      transition: background .15s, transform .1s;
    }}
    .btn-big:hover {{
      background: var(--accent2);
      transform: translateY(-2px);
      color: #000 !important;
    }}
    .btn-secondary {{
      display: block;
      text-align: center;
      border: 1px solid var(--border);
      color: var(--muted) !important;
      font-size: 13px;
      padding: 10px;
      border-radius: 8px;
      transition: all .15s;
    }}
    .btn-secondary:hover {{
      border-color: var(--accent);
      color: var(--accent) !important;
    }}
  </style>
</head>
<body>
  <div style="max-width:1100px;margin:0 auto;position:relative;z-index:1">
    <a href="../index.html" class="back">← Back to dashboard</a>
  </div>

  <div class="layout" style="position:relative;z-index:1">
    {gallery}

    <div class="info">
      <div>
        <div class="tags-row" style="margin-bottom:12px">
          <span class="tag {type_cls}">{board_type}</span>
          <span class="tag {verdict_cls}">{verdict_lbl}</span>
        </div>
        <h1 class="page-title">{listing.get('title','Unknown')}</h1>
        <div class="price-row">
          <div class="big-price">{listing.get('price','')}</div>
          <div class="loc-text">📍 {listing.get('location','Location unknown')}</div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">🤖 AI Assessment</div>
        <p class="ai-description">{listing.get('ai_description','No AI assessment available.')}</p>
      </div>

      <div class="section">
        <div class="section-title">Seller Description</div>
        <div class="section-body">{desc_full or 'No description was available on this listing.'}</div>
      </div>

      <div class="meta-grid">
        <div class="meta-item">
          <div class="meta-label">Triggered by</div>
          <div class="meta-value">{listing.get('board_searched','')}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Found at</div>
          <div class="meta-value">{listing.get('found_at','')}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">AI Verdict</div>
          <div class="meta-value">{listing.get('ai_verdict','')[:60]}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Type</div>
          <div class="meta-value">{board_type}</div>
        </div>
      </div>

      <div class="cta">
        <a href="{listing.get('url','#')}" target="_blank" class="btn-big">
          Open Listing on Facebook ↗
        </a>
        <a href="../index.html" class="btn-secondary">← Back to all finds</a>
      </div>
    </div>
  </div>

  <script>
    let current = 0;
    const slides = document.querySelectorAll('.slide');
    const dots   = document.querySelectorAll('.dot');
    function goTo(n) {{
      slides[current].classList.remove('active');
      dots[current].classList.remove('active');
      current = n;
      slides[current].classList.add('active');
      dots[current].classList.add('active');
    }}
  </script>
</body>
</html>"""


# ======================================================
#   SAVE LISTING TO DISK
# ======================================================

# In-memory stores — approved and rejected
ALL_LISTINGS = []
ALL_REJECTED = []

def load_all_listings():
    """Load approved + rejected JSON files from finds/ into memory."""
    global ALL_LISTINGS, ALL_REJECTED
    ALL_LISTINGS = []
    ALL_REJECTED = []
    FINDS_DIR.mkdir(parents=True, exist_ok=True)
    if not any(FINDS_DIR.iterdir()):
        log(f"📂 {FINDS_DIR}/ is empty — nothing to load", indent=1)
        return
    for folder, store in [(LISTINGS_DIR, ALL_LISTINGS), (REJECTED_DIR, ALL_REJECTED)]:
        folder.mkdir(parents=True, exist_ok=True)
        for f in sorted(folder.glob("*.json")):
            try:
                with open(f) as fh:
                    store.append(json.load(fh))
            except Exception:
                pass
    log(f"📂 Loaded {len(ALL_LISTINGS)} approved from {LISTINGS_DIR}/", indent=1)
    log(f"📂 Loaded {len(ALL_REJECTED)} rejected from {REJECTED_DIR}/", indent=1)

def rebuild_dashboard():
    """Rebuild index.html from current ALL_LISTINGS + ALL_REJECTED."""
    FINDS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = FINDS_DIR / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(build_dashboard_html(ALL_LISTINGS, ALL_REJECTED))

def save_listing(listing, rebuild=True):
    """
    Save approved listing → finds/listings/<id>.json + finds/listings/<id>.html
    Pass rebuild=False inside a batch for efficiency.
    """
    global ALL_LISTINGS
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    import re as _re
    lid = _re.sub(r'[^a-zA-Z0-9_\-]', '', str(listing["id"]))
    if not lid:
        log("⚠️  Listing has invalid ID — skipping save", indent=2)
        return

    # Save JSON (full source of truth including base64 images)
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = LISTINGS_DIR / f"{lid}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(listing, f, ensure_ascii=False, indent=2)

    # Individual HTML page lives in listings/ subfolder
    # listing_page is stored as relative path from finds/ so index.html links work
    listing["listing_page"] = f"listings/{lid}.html"
    html_path = LISTINGS_DIR / f"{lid}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(build_listing_html(listing))

    ALL_LISTINGS.append(listing)
    if rebuild:
        rebuild_dashboard()
    log(f"💾 Approved → listings/{lid}.html  |  {len(ALL_LISTINGS)} approved / {len(ALL_REJECTED)} rejected", indent=2)

def save_rejected(listing, rebuild=True):
    """
    Save rejected listing → finds/rejected/<id>.json only (no HTML page).
    Pass rebuild=False inside a batch for efficiency.
    """
    global ALL_REJECTED
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    import re as _re
    lid = _re.sub(r'[^a-zA-Z0-9_\-]', '', str(listing["id"]))
    if not lid:
        log("⚠️  Rejected listing has invalid ID — skipping save", indent=2)
        return

    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REJECTED_DIR / f"{lid}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(listing, f, ensure_ascii=False, indent=2)

    ALL_REJECTED.append(listing)
    if rebuild:
        rebuild_dashboard()
    log(f"🚫 Rejected → saved  |  {len(ALL_LISTINGS)} approved / {len(ALL_REJECTED)} rejected", indent=2)

def save_to_csv(listing):
    Path(CSV_FILE).parent.mkdir(parents=True, exist_ok=True)
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_HEADERS).writeheader()
        log(f"📄 Created {CSV_FILE}", indent=1)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writerow({
            "found_at":            listing.get("found_at",""),
            "board_searched":      listing.get("board_searched",""),
            "type":                "Proteus" if listing.get("is_proteus") else "Primary",
            "title":               listing.get("title",""),
            "price":               listing.get("price",""),
            "location":            listing.get("location",""),
            "description_snippet": listing.get("description","")[:300],
            "url":                 listing.get("url",""),
            "ai_verdict":          listing.get("ai_verdict",""),
            "ai_description":      listing.get("ai_description",""),
            "listing_page":        str(FINDS_DIR / listing["listing_page"]).replace("\\", "/") if listing.get("listing_page") else "",
        })

# ======================================================
#   PERSISTENCE
# ======================================================

def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                data = json.load(f)
            log(f"💾 Loaded {len(data)} seen IDs", indent=1)
            return set(data)
        except (json.JSONDecodeError, ValueError):
            log("⚠️  seen_listings.json was corrupt — resetting", indent=1)
    # File missing or corrupt — create a fresh empty one
    with open(SEEN_FILE, "w") as f:
        json.dump([], f)
    log("💾 Created fresh seen_listings.json", indent=1)
    return set()

def save_seen(seen):
    with open(SEEN_FILE,"w") as f:
        json.dump(list(seen), f)
    log(f"💾 Saved {len(seen)} seen IDs", indent=1)

# ======================================================
#   FACEBOOK SESSION
# ======================================================

def login_and_save_session():
    log("🔐 No saved session — opening browser for Facebook login.")
    log("   Log in then press Enter here.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page    = context.new_page()
        page.goto("https://www.facebook.com/login")
        input(f"[{ts()}]    ✅ Press Enter once logged in...")
        context.storage_state(path=SESSION_FILE)
        browser.close()
    log("✅ Session saved.\n")

# ======================================================
#   IMAGE HELPERS
# ======================================================

def fetch_and_encode_image(url):
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img.thumbnail((MAX_IMG_DIM, MAX_IMG_DIM), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        log(f"   → Image encode failed: {e}", indent=3)
        return None

# ======================================================
#   LISTING PAGE DETAIL SCRAPER
# ======================================================

def scrape_listing_detail(page, url):
    detail = {"description":"", "location":"", "image_urls":[]}
    try:
        log(f"   → Scraping listing page...", indent=2)
        page.goto(url, timeout=25000)
        page.wait_for_timeout(3000)

        # Description
        for sel in [
            "[data-testid='marketplace-listing-description']",
            "div[class*='description']",
            "span[class*='x193iq5w']",
        ]:
            try:
                el = page.query_selector(sel)
                if el:
                    text = el.inner_text().strip()
                    if len(text) > 20:
                        detail["description"] = text[:1500]
                        log(f"   → Description: {text[:70]}...", indent=2)
                        break
            except Exception:
                continue

        if not detail["description"]:
            try:
                body = page.inner_text("main") if page.query_selector("main") else ""
                lines = [l.strip() for l in body.split("\n") if len(l.strip()) > 15]
                detail["description"] = " | ".join(lines[:12])
            except Exception:
                pass

        # Location
        try:
            for sel in ["a[href*='marketplace/']", "span[class*='x193iq5w']"]:
                for el in page.query_selector_all(sel):
                    text = el.inner_text().strip()
                    if 3 < len(text) < 60 and "$" not in text and "marketplace" not in text.lower():
                        detail["location"] = text
                        break
                if detail["location"]:
                    break
            log(f"   → Location: {detail['location'] or 'not found'}", indent=2)
        except Exception:
            pass

        # Images
        try:
            seen_urls = set()
            for img in page.query_selector_all("img[src*='scontent']"):
                src = img.get_attribute("src") or ""
                if src and src not in seen_urls and len(src) > 50:
                    try:
                        w = page.evaluate("el => el.naturalWidth", img)
                        h = page.evaluate("el => el.naturalHeight", img)
                        if w and h and w > 150 and h > 150:
                            seen_urls.add(src)
                            detail["image_urls"].append(src)
                    except Exception:
                        seen_urls.add(src)
                        detail["image_urls"].append(src)
                if len(detail["image_urls"]) >= MAX_IMAGES:
                    break
            log(f"   → {len(detail['image_urls'])} image(s) found", indent=2)
        except Exception as e:
            log(f"   → Image scrape error: {e}", indent=2)

    except Exception as e:
        log(f"   → Detail page failed: {e}", indent=2)

    return detail

# ======================================================
#   LLM EVALUATION
# ======================================================

def llm_evaluate(listing):
    log(f"🤖 LLM evaluating: '{listing['title'][:55]}'", indent=2)

    images_b64 = []
    for img_url in listing.get("image_urls",[])[:MAX_IMAGES]:
        log(f"   → Fetching image...", indent=2)
        b64 = fetch_and_encode_image(img_url)
        if b64:
            images_b64.append(b64)
            log(f"   → Image ready ({len(b64)//1024}KB)", indent=2)

    log(f"   → Sending {len(images_b64)} image(s) + full description to {OLLAMA_MODEL}", indent=2)

    prompt = f"""You are an expert at identifying snowboards in used marketplace listings.
Your ONLY job is to decide: is the main item being SOLD a snowboard (or snowboards)?

LISTING DATA:
  Search term  : {listing['board_searched']}
  Title        : {listing['title']}
  Price        : {listing['price']}
  Location     : {listing.get('location','unknown')}
  Description  : {listing.get('description','none available')}

{"IMAGES ATTACHED: " + str(len(images_b64)) + " photo(s). Look carefully at what the SELLER IS HOLDING or what is PROPPED UP / LAID OUT for sale. Ignore backgrounds — a board leaning against a truck, RV, wall, or tree is still a board being sold. A board inside a bag or carry case is still a board." if images_b64 else "No images — judge on text only."}

BUYER PROFILE (use this to judge fit, NOT to disqualify snowboards):
{RIDER_PROFILE}

WHAT COUNTS AS YES (approve these):
- A snowboard with bindings attached — still a snowboard, approve it
- A snowboard in a bag or carry case — still a snowboard, approve it
- A snowboard leaning against ANY background (vehicle, wall, tree, fence) — the background is NOT the item
- Multiple snowboards sold together — approve it
- A snowboard that is slightly outside the size range (140-160cm) — still approve, buyer can decide
- Any listing where the primary item is clearly a snowboard deck

WHAT COUNTS AS NO (reject only these):
- ONLY boots with no board visible or mentioned
- ONLY bindings with no board visible or mentioned  
- A completely unrelated item (car, clothing, furniture, electronics)
- A wakeboard, skateboard, surfboard (different sports, not snowboards)
- A children's toy or foam balance trainer clearly under 100cm

IMPORTANT: When in doubt, say YES. It is better to show the buyer a listing they can dismiss themselves than to hide a real snowboard from them. The buyer is smart enough to look at the listing themselves.

Reply in EXACTLY this 3-line format:
VERDICT: YES or NO
REASON: one sentence — focus on what the item actually is
DESCRIPTION: one sentence for the buyer covering brand, size if visible, condition notes, whether bindings are included, price assessment
"""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model":   OLLAMA_MODEL,
            "prompt":  prompt,
            "stream":  False,
            "images":  images_b64,
            "options": {"temperature": 0.1, "num_predict": 180}
        }, timeout=90)

        if resp.status_code == 200:
            raw     = resp.json().get("response","").strip()
            log(f"   🤖 Response: {raw[:130]}", indent=2)
            verdict = "UNKNOWN"
            reason  = raw
            desc    = raw
            for line in raw.splitlines():
                line = line.strip()
                if line.upper().startswith("VERDICT:"):
                    verdict = "YES" if "YES" in line.upper() else "NO"
                elif line.upper().startswith("REASON:"):
                    reason = line.split(":",1)[-1].strip()
                elif line.upper().startswith("DESCRIPTION:"):
                    desc = line.split(":",1)[-1].strip()
            return (verdict=="YES"), f"[{verdict}] {reason}", desc, images_b64
        else:
            log(f"   ⚠️  Ollama HTTP {resp.status_code}", indent=2)
            return True, "LLM error", "LLM unavailable", images_b64

    except requests.exceptions.ConnectionError:
        log("   ⚠️  Ollama not running — skipping filter (run: ollama serve)", indent=2)
        return True, "Ollama offline", "No LLM filter", []
    except Exception as e:
        log(f"   ⚠️  LLM error: {e}", indent=2)
        return True, f"Error: {e}", "LLM error", []

# ======================================================
#   QUERY BUILDER + SEARCH URL
# ======================================================

def build_all_queries():
    queries = []
    for board in PRIMARY_BOARDS:
        for size in TARGET_SIZES:
            queries.append({"query":f"{board} {size}cm","board_searched":board,"is_proteus":False})
    for board in PROTEUS_BOARDS:
        queries.append({"query":board,"board_searched":board,"is_proteus":True})
    return queries

def build_search_url(query):
    encoded = urllib.parse.quote(query)
    return (f"https://www.facebook.com/marketplace/search?"
            f"query={encoded}&minPrice={MIN_PRICE}&maxPrice={MAX_PRICE}"
            f"&sortBy=creation_time_descend")

# ======================================================
#   MAIN SCRAPER
# ======================================================

class _TestLimitReached(Exception):
    """Sentinel used to break out of nested browser loops in test mode."""
    pass


def evaluate_batch(batch):
    """
    Takes a list of listings (already detail-scraped), runs them through
    the LLM in parallel (up to LLM_WORKERS at once), saves results, and
    returns (approved_list, rejected_count).
    """
    if not batch:
        return [], 0

    log_section(f"LLM BATCH — {len(batch)} listing(s), {LLM_WORKERS} worker(s)")

    def evaluate_one(listing):
        try:
            is_rel, verdict, ai_desc, imgs_b64 = llm_evaluate(listing)
            listing["ai_verdict"]     = verdict
            listing["ai_description"] = ai_desc
            listing["images_b64"]     = imgs_b64
            listing["_approved"]      = is_rel
            return listing
        except Exception as e:
            log(f"⚠️  LLM worker error: {e}", indent=2)
            listing["ai_verdict"]     = "ERROR"
            listing["ai_description"] = f"LLM error: {e}"
            listing["images_b64"]     = []
            listing["_approved"]      = False
            return listing

    approved   = []
    rejected_n = 0
    completed  = 0

    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as pool:
        futures = {pool.submit(evaluate_one, lst): lst for lst in batch}
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                pct    = int(completed / len(batch) * 100)
                log(f"   [{pct:3d}%] ({completed}/{len(batch)}) '{result.get('title','')[:50]}'", indent=1)

                if result.get("_approved"):
                    log(f"        ✅ APPROVED — saving", indent=1)
                    save_listing(result, rebuild=False)   # batch: no rebuild per item
                    save_to_csv(result)
                    approved.append(result)
                else:
                    rejected_n += 1
                    log(f"        🚫 REJECTED — {result.get('ai_verdict','')[:70]}", indent=1)
                    save_rejected(result, rebuild=False)  # batch: no rebuild per item
            except Exception as e:
                log(f"   ⚠️  Future error: {e}", indent=2)

    # One dashboard rebuild for the whole batch
    rebuild_dashboard()
    log(f"🖥️  Dashboard updated — {len(ALL_LISTINGS)} approved / {len(ALL_REJECTED)} rejected total", indent=1)
    return approved, rejected_n


def scrape_marketplace(test_mode=False):
    """
    Batched scraper — collects BATCH_SIZE listing detail pages, then immediately
    fires that batch at the LLM in parallel, then continues scraping.
    This keeps the GPU busy while the browser is working and prevents huge
    memory buildup from storing hundreds of base64 images at once.

    test_mode: stop after TEST_LIMIT new listings.
    """
    log_section("STARTING SCRAPE" + (" — TEST MODE 🧪" if test_mode else ""))
    seen          = load_seen()
    current_batch = []   # listings currently being collected for next LLM batch
    new_listings  = []
    rejected      = 0
    total_cards   = 0
    total_new_raw = 0
    batch_num     = 0
    queries       = build_all_queries()

    if test_mode:
        log(f"🧪 TEST MODE — stopping after {TEST_LIMIT} new listings", indent=1)

    log(f"📋 {len(queries)} queries | Batch size: {BATCH_SIZE} | LLM workers: {LLM_WORKERS} | Model: {OLLAMA_MODEL}", indent=1)

    def flush_batch():
        """Send current_batch to LLM, save results, clear batch."""
        nonlocal batch_num, rejected
        if not current_batch:
            return
        batch_num += 1
        log(f"📦 Flushing batch #{batch_num} ({len(current_batch)} listing(s)) to LLM...", indent=1)
        approved, rej = evaluate_batch(current_batch)
        new_listings.extend(approved)
        rejected += rej
        current_batch.clear()
        log(f"📦 Batch #{batch_num} done — {len(approved)} approved, {rej} rejected", indent=1)

    with sync_playwright() as p:
        log("🌐 Launching headless Chromium...", indent=1)
        browser     = p.chromium.launch(headless=True)
        _ss = SESSION_FILE if os.path.exists(SESSION_FILE) else None
        if not _ss:
            log("⚠️  No FB session — launching without saved login", indent=1)
        context     = browser.new_context(storage_state=_ss)
        srch_page   = context.new_page()
        detail_page = context.new_page()
        log("✅ Browser ready (2 tabs — search + detail)", indent=1)

        try:
            for idx, item in enumerate(queries, 1):
                log(f"🔎 [{idx}/{len(queries)}] '{item['query']}'", indent=1)
                try:
                    srch_page.goto(build_search_url(item["query"]), timeout=30000)
                    srch_page.wait_for_timeout(4000)
                    cards = srch_page.query_selector_all("a[href*='/marketplace/item/']")
                    total_cards += len(cards)
                    log(f"   → {len(cards)} card(s) on page", indent=2)

                    new_this = 0
                    for card in cards:
                        try:
                            href = card.get_attribute("href")
                            if not href:
                                continue

                            import re as _re
                            lid = href.split("/marketplace/item/")[-1].strip("/").split("?")[0]
                            lid = _re.sub(r'[^a-zA-Z0-9_\-]', '', lid)
                            if not lid:
                                continue
                            if lid in seen:
                                continue

                            seen.add(lid)
                            total_new_raw += 1
                            new_this += 1

                            text  = card.inner_text().strip()
                            lines = [l.strip() for l in text.split("\n") if l.strip()]
                            title = lines[0] if lines else "Unknown"
                            price = next((l for l in lines if "$" in l), "Price unknown")
                            url   = f"https://www.facebook.com/marketplace/item/{lid}/"
                            log(f"   ✨ [{total_new_raw}] '{title}' — {price}", indent=2)

                            listing = {
                                "id":             lid,
                                "board_searched": item["board_searched"],
                                "is_proteus":     item["is_proteus"],
                                "title":          title,
                                "price":          price,
                                "url":            url,
                                "found_at":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            }

                            # Scrape full listing detail page (serial — Playwright not thread safe)
                            detail = scrape_listing_detail(detail_page, url)
                            listing.update(detail)
                            current_batch.append(listing)
                            log(f"   📥 Batch {len(current_batch)}/{BATCH_SIZE}", indent=2)

                            # When batch is full, flush to LLM before continuing
                            if len(current_batch) >= BATCH_SIZE:
                                flush_batch()

                            # Test mode cap
                            if test_mode and total_new_raw >= TEST_LIMIT:
                                log(f"🧪 TEST LIMIT ({TEST_LIMIT}) reached — stopping browser", indent=1)
                                raise _TestLimitReached()

                        except _TestLimitReached:
                            raise
                        except Exception as e:
                            log(f"   ⚠️  Card error: {e}", indent=2)
                            import traceback; traceback.print_exc()

                    if new_this == 0:
                        log("   → All already seen", indent=2)
                    else:
                        log(f"   → {new_this} new listing(s) scraped", indent=2)

                except _TestLimitReached:
                    raise
                except Exception as e:
                    log(f"   ❌ Query failed: {e}", indent=2)

                time.sleep(1.5)

        except _TestLimitReached:
            pass   # clean exit from test mode

        finally:
            log("🌐 Closing browser...", indent=1)
            browser.close()

    # Flush any remaining listings that didn't fill a full batch
    if current_batch:
        log(f"📦 Flushing final partial batch ({len(current_batch)} listing(s))...", indent=1)
        flush_batch()

    save_seen(seen)

    log_section("SCRAPE COMPLETE")
    log(f"📊 Total cards processed : {total_cards}", indent=1)
    log(f"📊 New unseen scraped    : {total_new_raw}", indent=1)
    log(f"📊 LLM batches run       : {batch_num}", indent=1)
    log(f"📊 Approved & saved      : {len(new_listings)}", indent=1)
    log(f"📊 Rejected & logged     : {rejected}", indent=1)
    log(f"📂 Dashboard             : {FINDS_DIR}/index.html", indent=1)
    log(f"📊 Rejected         : {rejected}", indent=1)
    log(f"📂 Dashboard       : {FINDS_DIR}/index.html", indent=1)
    return new_listings

# ======================================================
#   RUN + SCHEDULE
# ======================================================

def run_search(test_mode=False):
    log_section("RUN TRIGGERED" + (" — TEST MODE 🧪" if test_mode else ""))
    try:
        results = scrape_marketplace(test_mode=test_mode)
        if not results:
            log("😴 No new matches this run.")
        else:
            log(f"🎯 {len(results)} new match(es)! Open finds/index.html to view.")
        if test_mode:
            log("🧪 Test run complete — check finds/index.html to verify everything looks right.")
    except Exception as e:
        log(f"❌ Error: {e} — retrying next hour.")

def main(test_mode=False):
    total_q = len(PRIMARY_BOARDS)*len(TARGET_SIZES) + len(PROTEUS_BOARDS)
    mode_str = f"🧪 TEST MODE — collecting {TEST_LIMIT} listings then stopping" if test_mode else "🔁 Normal mode — runs every hour"
    print(f"""
================================================================
  🏂  SNOWBOARD MARKETPLACE SNIPER v5
================================================================
  Mode           : {mode_str}
  Queries/run    : {total_q}
  Price range    : ${MIN_PRICE}–${MAX_PRICE}
  Size range     : {TARGET_SIZES[0]}–{TARGET_SIZES[-1]}cm
  LLM            : {OLLAMA_MODEL} (vision — sees images + description)
  Dashboard      : finds/index.html  ← open in browser
================================================================
  USAGE:
    python fb_sniper.py           normal hourly mode
    python fb_sniper.py --test    test mode (first {TEST_LIMIT} listings only)
================================================================
  SETUP (if not done):
    pip install playwright schedule requests Pillow
    playwright install chromium
    ollama pull llava:7b
    ollama serve
================================================================
""")

    FINDS_DIR.mkdir(parents=True, exist_ok=True)
    LISTINGS_DIR.mkdir(parents=True, exist_ok=True)
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    load_all_listings()
    rebuild_dashboard()
    log(f"🖥️  Dashboard ready → open {FINDS_DIR}/index.html in your browser")

    if not os.path.exists(SESSION_FILE):
        login_and_save_session()

    if test_mode:
        log(f"🧪 Running ONE test scan ({TEST_LIMIT} listings max) then exiting...")
        run_search(test_mode=True)
        log("🧪 Test complete. Check finds/index.html — if it looks right, run normally.")
        return   # exit after single test run, no scheduler

    log("🚀 Running first search now...")
    run_search(test_mode=False)

    schedule.every(1).hours.do(run_search)
    log("⏰ Scheduler active — next run in 1 hour. Ctrl+C to stop.\n")

    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    import sys
    _test = "--test" in sys.argv or "-t" in sys.argv
    main(test_mode=_test)