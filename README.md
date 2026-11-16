# Bulana Property Finder

Searches property ads from **ikman.lk** and **LankaPropertyWeb** in one place.
A scraper collects the ads once a day; a small website lets you search and
filter them, see what is new or has changed price, and export results to Excel.

Everything used is free: Python, GitHub Actions (the daily run) and GitHub
Pages (the website).

## What it does

- Search by town, road or any keyword, e.g. `nugegoda 3 bedroom`
- Filter by buy/rent, property type, district, town, price, bedrooms, land size
- Flags ads first seen today / this week, and ads whose price went up or down
- Groups ads that look like the same property (reposts, or on both websites)
- Exports the current results to an Excel file

## Put it online (one-time setup, about 10 minutes)

1. Create a free account at github.com and a new **public** repository
   (GitHub Pages is only free for public repositories).
2. Upload this folder to it:

   ```bash
   git init -b main
   git add .
   git commit -m "Bulana Property Finder"
   git remote add origin https://github.com/YOUR-NAME/YOUR-REPO.git
   git push -u origin main
   ```

3. In the repository on GitHub: **Settings → Pages → Source → GitHub Actions**.
4. **Actions → Daily property update → Run workflow** to do the first run.
   When it finishes (roughly 30 minutes) the website address is shown on the
   run page: `https://YOUR-NAME.github.io/YOUR-REPO/`.

After that it updates itself every day at about 5 am Sri Lanka time. If a run
fails, GitHub emails the account owner and the website keeps showing the
previous day's data.

## LankaPropertyWeb is collected from a Mac

LankaPropertyWeb refuses requests from GitHub's servers (HTTP 403), so GitHub
only scrapes ikman.lk. A Mac does the LankaPropertyWeb part:

- `scripts/mac-lpw-upload.sh` scrapes LankaPropertyWeb (about 12 minutes),
  uploads the listings to the `lpw-upload` branch and asks GitHub to rebuild
  the site.
- A macOS schedule (`~/Library/LaunchAgents/com.bulana.lpw-upload.plist`) runs
  it every day at 7 am, or when the Mac next wakes if it was asleep.
- macOS does not let scheduled jobs read the Documents folder, so the schedule
  runs a separate copy of this project in
  `~/Library/Application Support/Bulana`. It updates itself from GitHub
  before each run.
- Its log is `~/Library/Logs/bulana-lpw-upload.log`.

If the Mac is off for a day, the site keeps the previous LankaPropertyWeb
listings. To stop the schedule:
`launchctl bootout gui/$(id -u)/com.bulana.lpw-upload`

## Run it on your own computer

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m scraper
python3 -m http.server 8765 --directory site
```

Then open http://localhost:8765. For a quick test, `python -m scraper --max-pages 2`
only reads the first two pages of each area.

## Changing what is collected

Edit `config.json`:

| Setting | Meaning |
| --- | --- |
| `districts` | Districts to cover, e.g. `["colombo", "gampaha", "kalutara"]` |
| `property_types` | Any of `house`, `land`, `apartment`, `room` |
| `ikman_max_pages_per_area` | How deep to read each town's list on ikman (25 ads per page) |
| `expire_after_days` | An ad not seen for this many days is removed |
| `seconds_between_requests` | Pause between page requests to each website |
| `lpw_city_aliases` | Towns LankaPropertyWeb names differently, and their district |

More districts means a longer daily run and a bigger data file.

## How it works

- `scraper/ikman.py` reads each town's category pages on ikman.lk.
- `scraper/lpw.py` reads LankaPropertyWeb's sale, land and rental lists and
  keeps the towns that belong to your districts.
- `scraper/__main__.py` merges today's ads with yesterday's, records first-seen
  dates and price changes, groups duplicates and writes `site/data/listings.json`.
- `site/` is the website. It loads that one file and searches it in the browser.

## Good to know

- Only public listing pages are read, at a gentle pace, and each result links
  back to the original ad. Check each website's terms of use before using the
  data commercially.
- If either website changes its page layout, its scraper will need a small fix.
  The daily run reports how many ads each site returned.
- "New" means first seen by this tool, which is within a day of when the ad
  was posted or bumped up.
