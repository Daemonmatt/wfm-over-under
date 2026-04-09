# WFM dashboard

**Team app link (no Streamlit):** after you enable GitHub Pages on this repo, share:

### → [https://daemonmatt.github.io/wfm-over-under/](https://daemonmatt.github.io/wfm-over-under/)

That URL serves the **static** app from the [`docs/`](docs/) folder (HTML + JavaScript). Your teammates only need a browser.

**Note:** Features are implemented in both **`docs/`** (GitHub Pages) and optional **`app.py`** (Streamlit local). After pulling changes, hard-refresh the Pages site (`Cmd+Shift+R`) so `app.js?v=…` updates load.

---

## Enable the link (one-time)

| Step | Action |
|------|--------|
| 1 | Repo **Settings** → **Pages** |
| 2 | **Build and deployment** → Source: **Deploy from a branch** |
| 3 | Branch **main**, folder **`/docs`** → **Save** |
| 4 | Wait ~1 minute; refresh if you see 404 |

Updates: every `git push` to `main` refreshes the site automatically.

**Do not use** [Streamlit Cloud](https://share.streamlit.io) for the shared link unless you explicitly want Streamlit hosting — this project’s **recommended** sharing path is **GitHub Pages** above.

---

## What this repo contains

| Use case | Where |
|----------|--------|
| **Share with team (browser)** | `docs/` → GitHub Pages → link above |
| Optional: run Streamlit locally | `app.py` (see below) |
| Python logic & tests | `wfm_core.py`, `sanity_check.py` |

---

## Optional: run Streamlit on your machine only

For local development—not required for the team link:

```bash
cd wfm-dashboard
python -m pip install -r requirements.txt
streamlit run app.py
```

Or: `./run.sh` (default port **8511**).

---

## Open the static app on your computer

Double-click `docs/index.html`, or:

```bash
cd docs && python3 -m http.server 8080
```

Then open `http://localhost:8080`.

---

## Sanity check (Python)

```bash
python sanity_check.py
```

## Repo layout

| Path | Purpose |
|------|--------|
| **`docs/`** | **Web app** for GitHub Pages (`index.html`, `js/app.js`, `samples/`) |
| `app.py` | Optional Streamlit UI |
| `wfm_core.py` | Python logic |
| `sample_data/` | CSV samples for tests / Streamlit |

---

### If GitHub still shows an old README

Hard-refresh the repo page (`Cmd+Shift+R` / `Ctrl+Shift+R`) or open the raw file:  
`https://github.com/Daemonmatt/wfm-over-under/blob/main/README.md`
