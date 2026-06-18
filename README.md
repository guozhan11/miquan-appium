# Miquan Appium Scraper

Scrapers for collecting visible script ratings and review text from the iOS app `谜圈`.

## Prerequisites

1. Keep Appium Server running on `http://127.0.0.1:4723`.
2. Keep the iPhone or iPad connected, trusted, unlocked, and awake.
3. Make sure `谜圈` is logged in.
4. Install Python dependencies:

```bash
python3 -m pip install -r miquan_appium/requirements.txt
```

## Input

`miquan_appium/search_terms.csv` has been generated from:

`/Users/norazhan/Desktop/LARP/Text_Analysis/ocr/scripts/剧本杀合集目录-8月份已更新.xlsx`

## iPad pipeline

Use the iPad scraper for the current ratings pipeline:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --input miquan_appium/search_terms.csv --limit 3
```

Default outputs:

- `miquan_appium/miquan_ipad_ratings.csv`
- `miquan_appium/miquan_ipad_reviews.csv`
- `miquan_appium/debug_ipad/`

Common modes:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --mode ratings
python3 miquan_appium/miquan_ipad_appium_scraper.py --mode reviews
python3 miquan_appium/miquan_ipad_appium_scraper.py --mode both
```

The iPad scraper skips scripts that already have successful rows in:

`miquan_appium/miquan_ratings.csv`

To ignore that skip list:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --no-skip-iphone-ratings
```

To rerun items already present in the iPad output:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --rerun-existing
```

## Matching behavior

The iPad scraper uses relaxed title matching for common `谜圈` differences:

- punctuation-only differences, e.g. `怪谈事务所诡楼` vs `怪谈事务所：诡楼`
- full-width symbols, e.g. `Hi老妖婆` vs `Hi！老妖婆`
- parenthetical suffixes, e.g. `长生祭（cs祭）` vs `长生祭`
- Chinese/English reordered names, e.g. `往事ThePast` vs `ThePast往事`
- prefix titles, e.g. `猎人笔记雪夜` can match `猎人笔记：雪夜`

If exact search has no result, it can ask an AI model for better Chinese `剧本杀` search queries, then scans visible search results for the best matching title instead of blindly clicking the first row.

## AI fallback

Copy `.env.example` to `.env` and set your key:

```bash
cp miquan_appium/.env.example miquan_appium/.env
```

Supported variables:

- `MIQUAN_AI_API_KEY` or `OPENAI_API_KEY`
- `MIQUAN_AI_MODEL`, default `gpt-4o-mini`
- `MIQUAN_AI_API_URL`, default OpenAI chat completions URL

## iPad Appium config

Defaults are set for the current iPad:

- `00008112-0019593922DBA01E`
- `com.guozhan.WebDriverAgentRunner`

Override if needed:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --udid YOUR_UDID --platform-version 26.3.1 --limit 3
```

You can also set `MIQUAN_IPAD_UDID`, `MIQUAN_IPAD_PLATFORM_VERSION`, `MIQUAN_IPAD_DEVICE_NAME`, `MIQUAN_IPAD_WDA_PORT`, and `MIQUAN_IPAD_WDA_BUNDLE_ID`.

## iPhone scraper

The original iPhone scraper is still available:

```bash
python3 miquan_appium/miquan_appium_scraper.py --input miquan_appium/search_terms.csv --output miquan_appium/miquan_reviews.csv
```

The scraper only reads text exposed through Appium from your logged-in app session. Keep scroll counts modest and respect the app's terms.
