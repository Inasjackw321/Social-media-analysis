# Social-media-analysis

Finds what people are talking about on **YouTube, X/Twitter, Facebook and Instagram**, and publishes the results to a GitHub Pages site:

**https://inasjackw321.github.io/Social-media-analysis/**

The posts are scraped with [Scrapling](https://github.com/D4Vinci/Scrapling):
- **Fetcher** (HTTP with a real Chrome TLS fingerprint) handles YouTube, Instagram and X's embed timelines.
- **StealthySession** keeps one stealth Chromium open for all the Facebook/X pages in a scan. Its `capture_xhr` option collects the JSON those sites' own scripts load in the background, so posts come with exact dates, photos and counts instead of being read off the HTML.
- **Adaptive selectors** handle the HTML fallback. Elements are fingerprinted into `.scrapling/adaptive.db` (cached between Actions runs), so when a site renames its markup, Scrapling relocates them by similarity.

The app then groups the posts into topics: keyphrases and hashtags that several different accounts use, ranked by engagement and by how many platforms mention them. Each scan is saved to `site/data/`, so the site keeps a history you can switch between. Topics that weren't in the previous scan are marked **new**.

## Running a scan

**Ask Claude:** for example, *"scan social media for topics about the election"* or *"look through social media for what's trending"*. Claude starts the workflow, waits for it to finish, and sends you a summary with a link to the report. [`CLAUDE.md`](CLAUDE.md) explains how.

**Run it yourself:** go to **Actions → Scan social media → Run workflow**. You can give it:
- `query`: comma-separated search terms. Leave it blank to analyse the latest posts from the accounts in `config.json`.
- `platforms`: any of `youtube,twitter,facebook,instagram`.
- `lookback_hours`: how far back to look (default 24).

**Run it locally:**
```bash
pip install -r requirements.txt && scrapling install
python -m social_topics scan --query "ai, climate" --platforms youtube,instagram
python -m http.server -d site   # then open http://localhost:8000
```

## What gets scraped

| Platform | No login needed | Optional secret, which also enables keyword search |
|---|---|---|
| YouTube | Search results for your terms, plus the RSS feeds of the channels in `config.json` | — |
| X / Twitter | Recent posts from `twitter.accounts`, via X's public embed timeline, or the profile page's own timeline JSON if that's rate-limited | `X_AUTH_TOKEN` (plus `X_CT0` if needed): a stealth browser searches x.com |
| Instagram | Latest posts from `instagram.accounts` | `INSTAGRAM_SESSIONID`: searches each term as a hashtag |
| Facebook | Public Page feeds from `facebook.pages`, read with a stealth browser | `FACEBOOK_COOKIES` (`c_user=…; xs=…`): full feeds and post search |

To get one of these values, log in to the site in your browser and open DevTools → Application → Cookies. Then add the value under **Settings → Secrets and variables → Actions**. **Use a spare account, not your main one**, because platforms can rate-limit or lock accounts that scrape.

You can edit the accounts, pages and channels to watch in [`config.json`](config.json).

**Known limits.** The platforms change their pages often and limit logged-out visitors, so expect some failures. If a platform fails or is skipped, the report still covers the other platforms, and it says what went wrong with each one. Scraping may go against a platform's terms of service, so use this for personal research.

## One-time setup

1. Merge this into `main`. GitHub only lets you run workflows that are on the default branch.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. Optional: add the login secrets above.

## Layout

```
social_topics/
  scrape.py          Scrapling wrappers (Fetcher for HTTP, StealthyFetcher for JS pages)
  collectors/        one scraper per platform
  topics.py          topic extraction
  report.py          writes site/data/index.json and site/data/reports/<id>.json
site/                the GitHub Pages site (static HTML/JS)
config.json          what to watch
.github/workflows/   scan.yml (scan + deploy), tests.yml
```

To run the tests: `python -m unittest`
