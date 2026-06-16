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

The script uses the WebDriverAgent bundle ID and device UDID that were confirmed during setup:

- `00008140-000975A43E41801C`
- `com.guozhan.WebDriverAgentRunner`

The scraper only reads text exposed through Appium from your logged-in app session. Keep scroll counts modest and respect the app's terms.
