
# Blue Horizon Kitesurf — Zero-Touch Channel (Starter Kit)

This starter kit sets up an **automated YouTube channel** for cinematic kitesurf & scenic views.
It includes folder structure, templates, and scripts to fetch CC0 b-roll, assemble a montage,
generate thumbnails & metadata, and (placeholder) upload to YouTube.

## Quickstart
1) Python 3.10+ and `ffmpeg` installed.
2) `pip install -r requirements.txt`
3) Set API keys:
   - `export PEXELS_API_KEY=...` (or Windows: `set PEXELS_API_KEY=...`)
   - Optional: `PIXABAY_API_KEY=...`
4) Fetch assets: `python scripts/fetch_assets.py`
5) Generate metadata: `python scripts/generate_metadata.py`
6) Assemble video: `python scripts/assemble_video.py`
7) Create thumbnail: `python scripts/generate_thumbnail.py`
8) Upload (implement): `python scripts/upload_youtube.py`

## Structure
- `content/` — assets, plan, and exports
- `templates/` — script, metadata, thumbnail frame
- `scripts/` — automation scripts
- `config/` — channel config & credentials (keep private)
- `.github/workflows/pipeline.yml` — optional GitHub Actions workflow

## Automation (CI/CD)
Use GitHub Actions cron to run daily:
- Fetch b-roll → Assemble 60–180s montage → Thumbnail → Metadata → Upload private → Schedule publish.

## Notes
- Respect licenses: **Only CC0/CC-BY**. Auto-append credits from `credits.txt` to description.
- For PT-BR, duplicate metadata fields.
- For more control, swap music per episode and extend duration to 3–5 minutes.
