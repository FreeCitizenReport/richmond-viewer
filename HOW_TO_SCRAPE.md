# How to run the Richmond scraper

## One-time setup

```bash
pip install playwright
playwright install chromium
```

## Running

```bash
python scraper.py
```

A **real Chrome window opens**.  For each letter A–Z the script will:

1. Navigate to the JailTracker page and auto-fill the Last Name field.
2. Print a prompt like:
   ```
   ──────────────────────────────────────────────
     LETTER A
     The browser is ready.  Solve the CAPTCHA and click Search.
   ──────────────────────────────────────────────
   ```
3. **You**: look at the browser, type the CAPTCHA answer, click **Search**.
4. The script detects the results, auto-expands every row, reads all the detail data, then moves on to the next letter.

Total effort per run: **solve 26 CAPTCHAs** (one per letter).  Everything else is automated.

## After running

The script writes three files:

| File | Contents |
|---|---|
| `data.json` | All inmates, sorted by book date descending |
| `recent.json` | Booked in the last 24 h |
| `latest.json` | 50 most recent |

Commit and push to update the live viewer:

```bash
git add data.json recent.json latest.json
git commit -m "chore: update jail data $(date -u '+%Y-%m-%d %H:%M UTC')"
git push
```

## Want full automation?

Add a `TWOCAPTCHA_API_KEY` environment variable (from https://2captcha.com, ~$1 / 1 000 solves)
and swap the CAPTCHA logic in `scraper.py` to call `solve_with_2captcha()` — then it can run
unattended in GitHub Actions.
