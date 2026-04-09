# WFM dashboard

Single-day workforce management view: hourly **volume** and **staffing** (by channel: case & chat), **HC required** from AHT + shrinkage, and **variance** (over/under).

## Run locally

```bash
cd wfm-dashboard
python -m pip install -r requirements.txt
streamlit run app.py
```

Or: `./run.sh` (default port 8511; override with `PORT=8501 ./run.sh`).

## Public link (Streamlit Community Cloud)

1. Push this repo to **GitHub** (see below).
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. **New app** → pick the repo → **Main file path:** `app.py` → Deploy.
4. Cloud gives you a URL like `https://<app-name>.streamlit.app`.

No API keys are required for this app.

## Push to GitHub

```bash
cd wfm-dashboard
git init   # skip if already a repo
git add -A
git commit -m "Initial WFM dashboard"
```

Create an empty repository on GitHub, then:

```bash
git remote add origin https://github.com/<you>/<repo>.git
git branch -M main
git push -u origin main
```

## Sanity check

```bash
python sanity_check.py
```
