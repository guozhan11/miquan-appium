# Miquan Appium Scraper

Starter scraper for collecting visible script ratings and review text from the iOS app `谜圈`.

## Prerequisites

1. Keep Appium Server running on `http://127.0.0.1:4723`.
2. Keep the iPhone connected and trusted.
3. Unlock the iPhone and keep it awake while the scraper starts.
4. Make sure the app is logged in.
4. Install Python dependencies:

```bash
python3 -m pip install -r miquan_appium/requirements.txt
```

## Run

`miquan_appium/search_terms.csv` has been generated from:

`/Users/norazhan/Desktop/LARP/Text_Analysis/ocr/scripts/剧本杀合集目录-8月份已更新.xlsx`

Start with a small test run:

```bash
python3 miquan_appium/miquan_appium_scraper.py --input miquan_appium/search_terms.csv --output miquan_appium/miquan_reviews_sample.csv --limit 3 --max-scrolls 4
```

Then run the full list:

```bash
python3 miquan_appium/miquan_appium_scraper.py --input miquan_appium/search_terms.csv --output miquan_appium/miquan_reviews.csv
```

## iPad run

Use the separate iPad scraper so the iPhone pipeline remains unchanged:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --input miquan_appium/search_terms.csv --limit 3
```

By default, iPad results and debug snapshots are written separately:

- `miquan_appium/miquan_ipad_ratings.csv`
- `miquan_appium/miquan_ipad_reviews.csv`
- `miquan_appium/debug_ipad/`

The iPad scraper also skips scripts that already have successful ratings in the
iPhone output `miquan_appium/miquan_ratings.csv`. Failed or zero-only iPhone rows
are still retried on iPad. To scrape without using the iPhone output as a skip
list, pass:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --no-skip-iphone-ratings
```

To use a different completed-ratings CSV as the skip list:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --skip-ratings-from path/to/ratings.csv
```

If Appium cannot auto-select the iPad, pass the iPad UDID directly:

```bash
python3 miquan_appium/miquan_ipad_appium_scraper.py --udid 00008112-0019593922DBA01E --platform-version 26.3.1 --limit 3
```

You can also set `MIQUAN_IPAD_UDID`, `MIQUAN_IPAD_PLATFORM_VERSION`, `MIQUAN_IPAD_DEVICE_NAME`, `MIQUAN_IPAD_WDA_PORT`, and `MIQUAN_IPAD_WDA_BUNDLE_ID`.

The script uses the WebDriverAgent bundle ID and device UDID that were confirmed during setup:

- `00008112-0019593922DBA01E`
- `com.guozhan.WebDriverAgentRunner`

The scraper only reads text exposed through Appium from your logged-in app session. Keep scroll counts modest and respect the app's terms.
