"""Build a live "camera wall" HTML page of REAL public cameras (Caltrans CWWP2).

Each Caltrans camera publishes a live JPEG snapshot that updates every few seconds.
Those display directly in an <img> grid (no inference, no OpenCV, no CORS issues) —
so this renders a wall of N genuinely-live public cameras you can watch all at once,
auto-refreshing in the browser.

    python tools/build_camera_wall.py --count 121 --out camera-wall.html

Open the output file in a browser. This is a VIEWER of real DOT camera imagery, not
the CamAI analytics pipeline (no detection overlay) — it answers "see all the real
cameras in one dashboard".
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Busy urban districts first (Bay Area, LA, Sacramento, San Diego, OC) for variety.
DISTRICTS = [4, 7, 3, 11, 12, 8, 5, 6, 10, 1]
URL = "https://cwwp2.dot.ca.gov/data/d{n}/cctv/cctvStatusD{n:02d}.json"


def fetch(n: int) -> list[dict]:
    try:
        with urllib.request.urlopen(URL.format(n=n), timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        print(f"  ! d{n}: {e}")
        return []
    out = []
    for row in data.get("data", []):
        c = row.get("cctv", {})
        img = ((c.get("imageData") or {}).get("static") or {}).get("currentImageURL", "")
        loc = c.get("location", {})
        if img:
            out.append({"name": loc.get("locationName", "camera"),
                        "img": img, "d": f"D{n}",
                        "area": loc.get("nearbyPlace") or loc.get("county") or ""})
    return out


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CamAI — Live Camera Wall</title>
<style>
  :root {{ --bg:#0b0d12; --panel:#141821; --line:#262c38; --fg:#e8ecf3; --muted:#8b95a7; --accent:#4c8dff; --ok:#35c28a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }}
  header {{ position:sticky; top:0; z-index:5; display:flex; align-items:center; gap:14px; padding:12px 18px; background:color-mix(in srgb,var(--bg) 85%,transparent); backdrop-filter:blur(8px); border-bottom:1px solid var(--line); }}
  .brand {{ font-weight:700; font-size:18px; }} .brand span {{ color:var(--accent); }}
  .meta {{ color:var(--muted); font-size:13px; }}
  .live {{ display:inline-flex; align-items:center; gap:6px; margin-left:auto; color:var(--ok); font-size:12px; }}
  .live .dot {{ width:8px; height:8px; border-radius:50%; background:var(--ok); animation:p 1.6s infinite; }}
  @keyframes p {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:8px; padding:12px; }}
  .tile {{ position:relative; background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; aspect-ratio:16/10; }}
  .tile img {{ width:100%; height:100%; object-fit:cover; display:block; background:#0f131a; }}
  .cap {{ position:absolute; left:0; right:0; bottom:0; padding:5px 8px; font-size:11px; background:linear-gradient(transparent,rgba(0,0,0,.78)); color:#dfe6f0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .tag {{ position:absolute; top:6px; left:6px; font-size:9px; font-weight:700; letter-spacing:.04em; background:rgba(0,0,0,.6); color:#9fb4d8; padding:2px 6px; border-radius:5px; }}
</style></head>
<body>
<header>
  <div class="brand">Cam<span>AI</span></div>
  <div class="meta">Live Camera Wall · <b>{count}</b> real public cameras (Caltrans) · auto-refresh {refresh}s</div>
  <span class="live"><span class="dot"></span>LIVE</span>
</header>
<div class="grid" id="grid"></div>
<script>
  const CAMS = {cams};
  const REFRESH = {refresh} * 1000;
  const grid = document.getElementById("grid");
  CAMS.forEach((c, i) => {{
    const t = document.createElement("div"); t.className = "tile";
    t.innerHTML = `<span class="tag">${{c.d}}</span>
      <img loading="lazy" alt="${{c.name}}" src="${{c.img}}?t=${{Date.now()}}"
           onerror="this.style.opacity=.25">
      <div class="cap">${{c.name}}</div>`;
    grid.appendChild(t);
  }});
  // Stagger refreshes so we don't hammer the source all at once.
  const imgs = [...grid.querySelectorAll("img")];
  setInterval(() => {{
    imgs.forEach((im, i) => setTimeout(() => {{
      im.src = CAMS[i].img + "?t=" + Date.now();
    }}, (i % 20) * 150));
  }}, REFRESH);
</script>
</body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=121)
    ap.add_argument("--refresh", type=int, default=8, help="seconds between image refreshes")
    ap.add_argument("--out", type=Path, default=REPO / "camera-wall.html")
    args = ap.parse_args()

    cams: list[dict] = []
    for n in DISTRICTS:
        cams.extend(fetch(n))
        print(f"  d{n}: total {len(cams)} cameras collected")
        if len(cams) >= args.count:
            break
    cams = cams[: args.count]

    html = HTML.format(count=len(cams), refresh=args.refresh,
                       cams=json.dumps(cams, ensure_ascii=False))
    args.out.write_text(html, encoding="utf-8")
    print(f"wrote {args.out} — {len(cams)} real live cameras")


if __name__ == "__main__":
    main()
