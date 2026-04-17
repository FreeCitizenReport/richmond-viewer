#!/usr/bin/env python3
"""
Richmond City Jail â interactive scraper
========================================
Run this script locally.  A real Chromium window opens so you can solve
each CAPTCHA yourself.  The script intercepts every API response in the
background, auto-clicks all "View More" rows, and writes data.json /
recent.json / latest.json when finished.

Usage:
    pip install playwright
    playwright install chromium
    python scraper.py

Tip: you only have to solve the CAPTCHA once per letter (A-Z).
Everything else â expanding rows, collecting details â is automated.
"""

import asyncio, json, re, sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright, Page

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
AGENCY      = "Richmond_Co_VA"
BASE        = "https://omsweb.public-safety-cloud.com/jtclientweb"
TRACKER_URL = f"{BASE}/jailtracker/index/{AGENCY}"
LETTERS     = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# ---------------------------------------------------------------------------
# shared state (populated by API response callbacks)
# ---------------------------------------------------------------------------
# key -> inmate dict (key = jacket number string)
all_inmates: dict[str, dict] = {}

# letter -> list of offender dicts from NameSearch
pending_search: dict[str, list] = {}

# offenderViewKey -> detail dict from offenderbucket
detail_by_viewkey: dict[str, dict] = {}

# field name cache (discovered at runtime)
FIELD: dict = {}


def detect_fields(offender: dict) -> None:
    """Discover offender field names the first time we see a real result."""
    if FIELD:
        return
    # Internal offender ID (used in detail URL)
    for k in ("offenderId", "id", "personId", "offenderID", "inmateId"):
        if k in offender:
            FIELD["id"] = k
            break
    # Per-offender view key (also used in detail URL)
    for k in ("offenderViewKey", "viewKey", "bucketId", "offenderBucket"):
        if k in offender:
            FIELD["viewKey"] = k
            break
    # Jacket / booking number
    for k in ("jacketNumber", "jacket", "bookingNumber", "Jacket"):
        if k in offender:
            FIELD["jacket"] = k
            break
    if FIELD:
        print(f"\n  [field map detected] id={FIELD.get('id')}  "
              f"viewKey={FIELD.get('viewKey')}  jacket={FIELD.get('jacket')}")
        print(f"  [all keys] {list(offender.keys())}\n")


# ---------------------------------------------------------------------------
# API response interception
# ---------------------------------------------------------------------------

async def on_response(response) -> None:
    url = response.url
    try:
        if "NameSearch" in url and response.status == 200:
            data = await response.json()
            if not data.get("captchaRequired", True):
                offenders = data.get("offenders", [])
                if offenders:
                    detect_fields(offenders[0])
                # Store under a sentinel key so the main loop can pick it up
                pending_search["__latest__"] = offenders
                print(f"\n  v NameSearch captured: {len(offenders)} offender(s)")

        elif "offenderbucket" in url and response.status == 200:
            data = await response.json()
            if not data.get("captchaRequred", True):
                # Pull the offenderViewKey from the URL
                # URL pattern: /Offender/{agency}/{offenderId}/offenderbucket/{viewKey}
                parts = url.rstrip("/").split("/")
                view_key = parts[-1]
                detail_by_viewkey[view_key] = data
                print(f"  v Detail captured (viewKey={view_key})")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

async def fill_last_name(page: Page, letter: str) -> None:
    """Clear the Last Name input and type the letter."""
    # JailTracker has two text inputs: First Name, then Last Name
    inputs = await page.query_selector_all("input[type='text']")
    # Try placeholder-based match first
    for inp in inputs:
        ph = (await inp.get_attribute("placeholder") or "").lower()
        if "last" in ph:
            await inp.triple_click()
            await inp.type(letter)
            return
    # Fallback: second input is Last Name
    if len(inputs) >= 2:
        await inputs[1].triple_click()
        await inputs[1].type(letter)


async def click_view_more_all(page: Page, expected_count: int) -> None:
    """Expand every result row, wait for the detail API call, then collapse."""
    processed = 0
    stuck_count = 0

    while processed < expected_count and stuck_count < 5:
        # Re-query: after collapse the list refreshes
        buttons = await page.query_selector_all(
            "button, [role='button'], td > *"
        )
        view_more_btns = []
        for b in buttons:
            try:
                txt = (await b.inner_text()).strip()
                if txt == "View More":
                    view_more_btns.append(b)
            except Exception:
                pass

        if not view_more_btns:
            break

        btn = view_more_btns[0]
        try:
            await btn.scroll_into_view_if_needed()
            await btn.click()
        except Exception as e:
            print(f"  [warn] click failed: {e}")
            stuck_count += 1
            continue

        # Wait for detail API response (up to 6 s)
        for _ in range(12):
            await asyncio.sleep(0.5)
            if len(detail_by_viewkey) > processed:
                break

        # Collapse
        try:
            vl = await page.query_selector(
                "button:text('View Less'), [role='button']:text('View Less')"
            )
            if vl:
                await vl.click()
                await asyncio.sleep(0.4)
        except Exception:
            pass

        processed += 1
        stuck_count = 0

    print(f"  -> {processed} row(s) expanded")


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def extract_other_info(text: str) -> dict:
    info: dict[str, str] = {}
    start = text.find("Other Info")
    if start == -1:
        return info
    block = text[start + 10: start + 1500]
    for line in block.splitlines():
        if ":\t" in line:
            key, _, val = line.partition(":\t")
            key = key.strip()
            val = val.strip()
            if key and val:
                info[key] = val
    return info


def extract_charges(text: str) -> list:
    charges = []
    start = text.find("Charges")
    end   = text.find("Other Info", start) if start != -1 else -1
    if start == -1:
        return charges
    block = text[start: end] if end != -1 else text[start: start + 4000]

    current_desc = ""
    for line in block.splitlines():
        line = line.strip()
        if not line or line == "Charges":
            continue
        if line.startswith("Code\t") or line.startswith("Code "):
            continue
        if re.match(r"^[A-Z]{2,5}\d", line):
            parts = [p.strip() for p in line.split("\t")]
            charges.append({
                "code":         parts[0] if len(parts) > 0 else "",
                "description":  current_desc,
                "courtDate":    parts[1] if len(parts) > 1 else "",
                "courtType":    parts[2] if len(parts) > 2 else "",
                "courtName":    parts[3] if len(parts) > 3 else "",
                "status":       parts[4] if len(parts) > 4 else "",
                "offenseDate":  parts[5] if len(parts) > 5 else "",
                "arrestDate":   parts[6] if len(parts) > 6 else "",
                "arrestAgency": parts[7] if len(parts) > 7 else "",
                "caseNumber":   parts[8] if len(parts) > 8 else "",
                "bondType":     parts[9] if len(parts) > 9 else "",
                "bondAmount":   parts[10] if len(parts) > 10 else "",
            })
            current_desc = ""
        else:
            current_desc = line
    return charges


def build_inmate(offender: dict, page_text: str) -> dict | None:
    jacket = str(offender.get(FIELD.get("jacket", "jacketNumber"), "")
                 or offender.get("jacketNumber", "")
                 or offender.get("jacket", ""))
    if not jacket:
        return None

    info    = extract_other_info(page_text)
    charges = extract_charges(page_text)

    return {
        "jacket":          jacket,
        "lastName":        offender.get("lastName", ""),
        "firstName":       offender.get("firstName", ""),
        "middleName":      offender.get("middleName", ""),
        "bookDate":        offender.get("bookDate", ""),
        "releaseDate":     offender.get("releaseDate", ""),
        "race":            info.get("Race", ""),
        "sex":             info.get("Sex", ""),
        "age":             info.get("Current Age", ""),
        "bookDateTime":    info.get("Booking Date", ""),
        "schedRelease":    info.get("Sched Release", ""),
        "height":          info.get("Height", ""),
        "weight":          info.get("Weight", ""),
        "hairColor":       info.get("Hair Color", ""),
        "eyeColor":        info.get("Eye Color", ""),
        "alias":           info.get("Alias", ""),
        "address":         info.get("Address", ""),
        "zip":             info.get("Zip", ""),
        "classification":  info.get("Inmate Classification", ""),
        "arrestAgency":    info.get("Arresting Agency", ""),
        "arrestDate":      info.get("Arrest Date", ""),
        "charges":         charges,
    }


# ---------------------------------------------------------------------------
# Per-letter scrape loop
# ---------------------------------------------------------------------------

async def scrape_letter(page: Page, letter: str) -> int:
    added = 0
    detail_by_viewkey.clear()
    pending_search.pop("__latest__", None)

    # 1. Navigate fresh for each letter (resets Blazor state & CAPTCHA)
    await page.goto(TRACKER_URL, wait_until="networkidle")
    await asyncio.sleep(1.5)

    # 2. Auto-fill Last Name
    await fill_last_name(page, letter)

    # 3. Prompt user
    print(f"\n{'--'*27}")
    print(f"  LETTER {letter}")
    print(f"  The browser is ready.  Solve the CAPTCHA and click Search.")
    print(f"  (The scraper will take over once results appear.)")
    print(f"{'--'*27}")

    # 4. Wait for NameSearch success (up to 3 minutes per letter)
    for tick in range(360):
        await asyncio.sleep(0.5)
        if "__latest__" in pending_search:
            break
    else:
        print(f"  [timeout] No results for {letter}, skipping.")
        return 0

    offenders = pending_search["__latest__"]
    if not offenders:
        print(f"  No offenders returned for {letter}.")
        return 0

    print(f"  Automating {len(offenders)} row(s)...")

    # 5. Expand each row and collect details from the DOM
    for i, offender in enumerate(offenders):
        jacket = str(offender.get(FIELD.get("jacket", "jacketNumber"), "")
                     or offender.get("jacketNumber", offender.get("jacket", "")))
        if jacket and jacket in all_inmates:
            continue  # Already have this one

        # Find and click the i-th "View More" button
        try:
            buttons = []
            for b in await page.query_selector_all("button, [role='button'], td > *"):
                try:
                    if (await b.inner_text()).strip() == "View More":
                        buttons.append(b)
                except Exception:
                    pass

            if i < len(buttons):
                await buttons[i].scroll_into_view_if_needed()
                await buttons[i].click()
                await asyncio.sleep(0.8)

                # Read expanded DOM
                page_text = await page.inner_text("body")
                inmate = build_inmate(offender, page_text)
                if inmate and inmate["jacket"]:
                    all_inmates[inmate["jacket"]] = inmate
                    added += 1
                    print(f"    + {inmate['lastName']}, {inmate['firstName']}  "
                          f"jacket={inmate['jacket']}  race={inmate['race']}  sex={inmate['sex']}")

                # Collapse
                vl = await page.query_selector(
                    "button:text-is('View Less'), [role='button']:text-is('View Less')"
                )
                if vl:
                    await vl.click()
                    await asyncio.sleep(0.4)

        except Exception as e:
            print(f"    [warn] row {i}: {e}")

    return added


# ---------------------------------------------------------------------------
# Save output files
# ---------------------------------------------------------------------------

def save_files(inmates: list) -> None:
    def parse_date(s: str) -> datetime:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%-m/%-d/%Y"):
            try:
                return datetime.strptime(s.split()[0], fmt)
            except Exception:
                pass
        return datetime.min

    # Carry over released inmates from previous data.json
    if Path("data.json").exists():
        try:
            old_data = json.loads(Path("data.json").read_text())
            live_jackets = {r['jacket'] for r in inmates}
            today_str = datetime.now().strftime('%m/%d/%Y')
            carried = 0
            for rec in old_data:
                if rec.get('jacket') and rec['jacket'] not in live_jackets:
                    if not rec.get('releaseDate'):
                        rec['releaseDate'] = today_str
                    inmates.append(rec)
                    carried += 1
            print(f'Carried over {carried} released/dropped records from previous data')
        except Exception as e:
            print(f'Warning: could not merge existing data.json - {e}')
    inmates.sort(key=lambda x: parse_date(x.get("bookDate", "")), reverse=True)

    Path("data.json").write_text(json.dumps(inmates))
    print(f"\nWrote data.json  ({len(inmates)} inmates)")

    cutoff = datetime.now() - timedelta(hours=24)
    recent = [i for i in inmates if parse_date(i.get("bookDate", "")) >= cutoff]
    Path("recent.json").write_text(json.dumps(recent))
    print(f"Wrote recent.json ({len(recent)} in last 24 h)")

    Path("latest.json").write_text(json.dumps(inmates[:50]))
    print("Wrote latest.json (top 50 most recent)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 55)
    print("  Richmond City Jail -- interactive scraper")
    print("=" * 55)
    print("  A browser window will open.")
    print("  For each letter A-Z you will be asked to solve a CAPTCHA.")
    print("  Everything else is automated.")
    print("=" * 55)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--window-size=1100,900"],
        )
        page = await browser.new_page(viewport={"width": 1100, "height": 900})
        page.on("response", on_response)

        for letter in LETTERS:
            try:
                n = await scrape_letter(page, letter)
                print(f"  v {letter}: {n} new  (running total: {len(all_inmates)})")
            except Exception as e:
                print(f"  x {letter}: error -- {e}")

        await browser.close()

    save_files(list(all_inmates.values()))
    print("\nDone! Run 'git add data.json recent.json latest.json && git commit -m \"update\" && git push'")


if __name__ == "__main__":
    asyncio.run(main())
