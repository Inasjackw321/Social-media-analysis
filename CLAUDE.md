# Notes for Claude

## When the user asks for a scan

Examples: "look through social media for X", "what's trending", "scan for topics about Y".

Don't scrape from your own session. Cloud sessions usually can't reach the social sites, so the scan runs on GitHub Actions and publishes to GitHub Pages:

1. Start the workflow with the GitHub MCP tool `actions_run_trigger`:
   owner `Inasjackw321`, repo `Social-media-analysis`, workflow `scan.yml`, ref `main`, with these inputs:
   - `query`: the user's search terms, comma-separated. Use `""` when the user just wants "what's trending" or "latest".
   - `platforms`: default `youtube,twitter,facebook,instagram`. Narrow it if the user names particular platforms.
   - `lookback_hours`: default `"24"`. Use "this week" → `"168"`.
2. Find the run with `actions_list` (list workflow runs for `scan.yml`) and wait for it to finish. Don't poll in a tight loop: check about every 2 minutes, because a scan takes about 3–8 minutes.
3. When it succeeds, read `site/data/reports/run-<run_id>.json` from `main` (`get_file_contents`). Reply with:
   - the top 5–10 topics, with their platforms and post counts, plus any marked `new`
   - any platforms with `status` `error` or `skipped`, and why (from `message`)
   - the link `https://inasjackw321.github.io/Social-media-analysis/#report=run-<run_id>`
4. If the run fails, read the job logs (`get_job_logs`) and explain what happened. Exit code 1 from the Scan step means no platform returned any data.

### Scanning from a branch other than `main`

The "Run workflow" route (`scan.yml`) only works on `main`. On any branch, scan by editing `scan-request.json` and pushing it instead. The file holds `query`, `platforms`, `lookback_hours`, and an optional `config` that overrides parts of `config.json` (e.g. extra accounts). The push runs `scan-request.yml`, which commits `site/data/reports/run-<run_id>.json` back to the same branch. Fetch the branch to read the report. Pages only redeploys when the push is to `main`.

## Development

- `python -m unittest` runs the tests. They must pass offline, so mock `social_topics.scrape.get` / `get_json` / `browse` in tests.
- All scraping goes through `social_topics/scrape.py` (Scrapling `Fetcher` / `StealthyFetcher`).
- Each collector in `social_topics/collectors/` raises `NotConfigured` when it has nothing it can scan. When only part of a scan fails, the collector returns `finish(posts, errors)`, so partial failures show up as warnings instead of failing the scan.
- To use a local Chromium instead of the one `scrapling install` downloads, set `CHROMIUM_PATH` (e.g. `/opt/pw-browsers/chromium` in cloud sessions).
