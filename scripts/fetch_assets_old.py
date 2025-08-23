
import os, json, requests, pathlib, time

# Fetch CC0/CC-BY clips and images from Pexels/Pixabay using search terms.
# Stores results in content/assets/broll/{date}/.
# Requires env: PEXELS_API_KEY or PIXABAY_API_KEY.
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")

SEARCH_TERMS = [
    "kitesurf drone",
    "kiteboarding ocean",
    "beach dunes aerial",
    "sunset ocean drone"
]

OUTDIR = pathlib.Path("content/assets/broll") / time.strftime("%Y-%m-%d")
OUTDIR.mkdir(parents=True, exist_ok=True)

def fetch_pexels(query, per_page=20):
    if not PEXELS_API_KEY:
        print("PEXELS_API_KEY not set; skipping Pexels")
        return []
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": per_page, "orientation": "landscape"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    results = []
    for v in data.get("videos", []):
        files = sorted(
            v.get("video_files", []),
            key=lambda f: f.get("width", 0) * f.get("height", 0),
            reverse=True,
        )
        if files:
            top = files[0]
            results.append({
                "src": top["link"],
                "width": top.get("width", 0),
                "height": top.get("height", 0),
                "credit": v.get("user", {}).get("name", "Pexels Creator"),
            })
    return results

def download(url, path):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)

all_credits = []
for term in SEARCH_TERMS:
    try:
        for item in fetch_pexels(term, per_page=15):
            fname = term.replace(" ", "_") + "_" + os.path.basename(item["src"].split("?")[0])
            out = OUTDIR / fname
            download(item["src"], out)
            all_credits.append(f"{term}: {item['credit']}")
            print("Downloaded", out)
    except Exception as e:
        print("Error:", e)

(OUTDIR / "credits.txt").write_text(".join(sorted(set(all_credits))), encoding="utf-8")
print("Done. Saved credits to", OUTDIR / "credits.txt")