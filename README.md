# WFM dashboard

Single-day workforce management: hourly **volume** and **staffing** by channel (**case** & **chat**), **HC required** from AHT + shrinkage, and **variance** (over/under).

## Share with your team (recommended): GitHub Pages

Git stores the code; a **shareable app link** comes from hosting the static site in the `docs/` folder.

1. Push this repo to GitHub (already set up for [Daemonmatt/wfm-over-under](https://github.com/Daemonmatt/wfm-over-under)).
2. In the repo: **Settings → Pages**.
3. **Build and deployment**: Source = **Deploy from a branch**, Branch = **main**, folder = **/docs** → Save.
4. After a minute, GitHub shows a URL like:

   **`https://daemonmatt.github.io/wfm-over-under/`**

   Share that link with your team. The app runs entirely in the browser (no Streamlit server, no login required for basic use).

To update the live site: `git push` to `main`; Pages rebuilds automatically.

## Run locally (optional): Python + Streamlit

For development or offline use:

```bash
cd wfm-dashboard
python -m pip install -r requirements.txt
streamlit run app.py
```

Or: `./run.sh` (default port 8511).

## Open the static app locally

Open `docs/index.html` in a browser, or from the repo folder run:

```bash
cd docs && python3 -m http.server 8080
```

Then visit `http://localhost:8080`.

## Sanity check (Python)

```bash
python sanity_check.py
```

## Repo layout

| Path | Purpose |
|------|--------|
| `docs/` | **Static web app** (GitHub Pages) — `index.html`, `js/app.js`, samples |
| `app.py` | Streamlit UI (optional) |
| `wfm_core.py` | Shared Python logic |
| `sample_data/` | CSV samples for Streamlit / tests |
