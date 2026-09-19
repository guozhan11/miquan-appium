#!/usr/bin/env python3
"""iPad-oriented Miquan Appium scraper.

This file intentionally lives beside, rather than replacing, the existing
iPhone scraper. It reuses the CSV/parsing pipeline from
``miquan_appium_scraper.py`` and swaps in iPad capabilities plus more flexible
navigation/locator logic for the wider iPad XML layout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy
from appium.webdriver.client_config import AppiumClientConfig
from appium.options.ios import XCUITestOptions
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from urllib3.exceptions import MaxRetryError, ProtocolError, ReadTimeoutError

import miquan_appium_scraper as base


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
RATINGS_DIR = DATA_DIR / "ratings"
LOGS_DIR = PROJECT_DIR / "logs"


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv_file(PROJECT_DIR / ".env")
load_dotenv_file(Path.cwd() / ".env")

DEFAULT_IPAD_OUTPUT = RATINGS_DIR / "miquan_ipad_reviews.csv"
DEFAULT_IPAD_RATINGS_OUTPUT = RATINGS_DIR / "miquan_ipad_ratings.csv"
DEFAULT_IPAD_DEBUG_DIR = LOGS_DIR / "debug_ipad"
XML_BROKEN_DETAIL_QUERIES: set[str] = set()
SEARCH_RECOMMENDATIONS: dict[str, list[str]] = {}
RAW_SEARCH_RECOMMENDATIONS: dict[str, list[str]] = {}
AI_SEARCH_QUERIES: dict[str, list[str]] = {}
AI_QUERY_EXAMPLES = [
    {"excel": "极乐都市", "miquan": "THUGS! 极乐都市", "query": "极乐都市"},
    {"excel": "东京恐怖故事自杀森林", "miquan": "东京恐怖故事：自杀森林", "query": "东京恐怖故事：自杀森林"},
    {"excel": "Hi老妖婆", "miquan": "Hi! 老妖婆", "query": "老妖婆"},
    {"excel": "暗黑者惩罚", "miquan": "暗黑者：惩罚", "query": "暗黑者"},
    {"excel": "长生祭（cs祭）", "miquan": "长生祭", "query": "长生祭"},
    {"excel": "怪谈事务所诡楼", "miquan": "怪谈事务所：诡楼", "query": "怪谈事务所：诡楼"},
    {"excel": "雪松都公立医院", "miquan": "K的游戏：雪松都公立医院", "query": "雪松都公立医院"},
]
DEFAULT_IPAD_CAPS = {
    "platformName": "iOS",
    "appium:automationName": "XCUITest",
    "appium:deviceName": os.environ.get("MIQUAN_IPAD_DEVICE_NAME", "iPad (2)"),
    "appium:udid": os.environ.get("MIQUAN_IPAD_UDID", "00008112-0019593922DBA01E"),
    "appium:platformVersion": os.environ.get("MIQUAN_IPAD_PLATFORM_VERSION", "26.3.1"),
    "appium:bundleId": base.APP_BUNDLE_ID,
    "appium:noReset": True,
    "appium:newCommandTimeout": 120,
    "appium:updatedWDABundleId": os.environ.get(
        "MIQUAN_IPAD_WDA_BUNDLE_ID",
        "com.guozhan.WebDriverAgentRunner",
    ),
    "appium:usePrebuiltWDA": False,
    "appium:useNewWDA": False,
    "appium:wdaLocalPort": int(os.environ.get("MIQUAN_IPAD_WDA_PORT", "8102")),
    "appium:showXcodeLog": True,
    "appium:wdaLaunchTimeout": 120000,
    "appium:wdaConnectionTimeout": 120000,
    "appium:wdaStartupRetries": 3,
    "appium:wdaStartupRetryInterval": 10000,
    "appium:customSnapshotTimeout": 25000,
    "appium:snapshotMaxDepth": 65,
}

SEARCH_FIELD_XPATH = (
    "//XCUIElementTypeTextField | "
    "//XCUIElementTypeSearchField | "
    "//*[@type='XCUIElementTypeTextField' or @type='XCUIElementTypeSearchField']"
)
BACK_BUTTON_XPATHS = [
    "//XCUIElementTypeButton[@name='sys back black' or @label='sys back black']",
    "//XCUIElementTypeButton[@name='spt level back white' or @label='spt level back white']",
    "//XCUIElementTypeButton[@name='返回' or @label='返回' or @value='返回']",
    "//XCUIElementTypeButton[contains(@name, 'back') or contains(@label, 'back')]",
]
SEARCH_ENTRY_XPATHS = [
    "//*[contains(@name, '搜索') or contains(@label, '搜索') or contains(@value, '搜索')]",
    "//*[contains(@name, 'Search') or contains(@label, 'Search') or contains(@value, 'Search')]",
    "//*[contains(@name, 'home_top_search') or contains(@label, 'home_top_search')]",
    (
        "//XCUIElementTypeButton[@visible='true' and @x >= 80 and @y >= 40 and @y <= 95 "
        "and @width >= 180 and @height >= 24 and @height <= 44]"
    ),
]
DETAIL_TEXT_XPATH = (
    "//XCUIElementTypeStaticText | "
    "//XCUIElementTypeButton | "
    "//XCUIElementTypeTextView"
)

def clean_caps(caps: dict) -> dict:
    """Drop optional empty caps so Appium can auto-detect when allowed."""
    return {key: value for key, value in caps.items() if value not in {"", None}}


def normalize_title(text: str) -> str:
    text = re.sub(r"[\(（][^\)）]*[\)）]", "", text)
    return "".join(
        char
        for char in ORIGINAL_NORMALIZE_TITLE(text).casefold()
        if not unicodedata.category(char).startswith(("P", "S"))
    )


def title_units(text: str) -> list[str]:
    normalized = normalize_title(text)
    units = []
    index = 0
    while index < len(normalized):
        char = normalized[index]
        if char.isascii() and char.isalnum():
            match = re.match(r"[a-z0-9]+", normalized[index:])
            token = match.group(0) if match else char
            units.append(token)
            index += len(token)
        else:
            units.append(char)
            index += 1
    return units


def is_exact_title_match(candidate: str, query: str) -> bool:
    candidate_normalized = normalize_title(candidate)
    query_normalized = normalize_title(query)
    if candidate_normalized == query_normalized:
        return True
    if (
        len(query_normalized) >= 4
        and query_normalized in candidate_normalized
        and candidate_normalized != query_normalized
    ):
        return True
    candidate_units = title_units(candidate)
    query_units = title_units(query)
    has_latin = any(unit.isascii() and unit.isalnum() for unit in candidate_units + query_units)
    has_non_latin = any(not (unit.isascii() and unit.isalnum()) for unit in candidate_units + query_units)
    return has_latin and has_non_latin and sorted(candidate_units) == sorted(query_units)


def dedupe_keep_order(items: list[str]) -> list[str]:
    deduped = []
    for item in items:
        item = item.strip()
        if item and item not in deduped:
            deduped.append(item)
    return deduped


def title_search_variants(query: str) -> list[str]:
    without_parentheses = re.sub(r"[\(（][^\)）]*[\)）]", "", query).strip()
    symbol_stripped = "".join(
        char
        for char in without_parentheses
        if not unicodedata.category(char).startswith(("P", "S"))
    ).strip()
    variants = [without_parentheses, symbol_stripped]

    han_text = "".join(re.findall(r"[\u3400-\u9fff]+", symbol_stripped))
    if len(han_text) >= 3:
        variants.append(han_text)

    mixed_units = title_units(query)
    if len(mixed_units) >= 2:
        latin_units = [unit for unit in mixed_units if unit.isascii() and unit.isalnum()]
        han_units = [unit for unit in mixed_units if not (unit.isascii() and unit.isalnum())]
        if latin_units and han_units:
            variants.append("".join(latin_units + han_units))
            variants.append("".join(han_units + latin_units))

    return [variant for variant in dedupe_keep_order(variants) if variant != query]


def extract_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def infer_ai_search_queries(query: str) -> list[str]:
    if query in AI_SEARCH_QUERIES:
        return AI_SEARCH_QUERIES[query]

    api_key = os.environ.get("MIQUAN_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        AI_SEARCH_QUERIES[query] = []
        return []

    model = os.environ.get("MIQUAN_AI_MODEL", "gpt-4o-mini")
    api_url = os.environ.get("MIQUAN_AI_API_URL", "https://api.openai.com/v1/chat/completions")
    prompt = {
        "task": "Suggest Miquan app search queries for a Chinese 剧本杀 script title.",
        "context": (
            "谜圈 is a Chinese 剧本杀 app. Excel titles may omit punctuation, series prefixes, "
            "English branding, or subtitles. Miquan titles often use patterns like "
            "系列名：副标题, EnglishPrefix! 中文名, K的游戏：标题, or 标题（alias）."
        ),
        "rules": [
            "Return only JSON with a queries array.",
            "Suggest up to 5 search queries.",
            "Prefer concise, distinctive search terms that are likely to retrieve the correct 剧本杀 script in 谜圈.",
            "For 系列名+副标题 titles, include the subtitle as a query and optionally the full punctuated form.",
            "Remove parenthetical aliases when useful.",
            "Account for punctuation differences, added prefixes, and Chinese/English order changes.",
            "Do not invent unrelated titles.",
            "Avoid meaningless character fragments such as 记雪夜, 者惩罚, 务所诡楼, or other non-word slices.",
            "If unsure, prefer the most distinctive real word or phrase from the title over arbitrary suffixes.",
        ],
        "examples": AI_QUERY_EXAMPLES,
        "excel_title": query,
        "return_format": {"queries": ["..."]},
    }
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You generate precise search queries for matching Chinese 剧本杀 script names "
                    "in the 谜圈 app. You understand Chinese script title conventions, series names, "
                    "subtitles, punctuation variants, and Chinese/English mixed names."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(prompt, ensure_ascii=False),
            },
        ],
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        parsed = extract_json_object(content)
    except (KeyError, json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
        print(f"  AI query inference failed for {query}: {str(exc).splitlines()[0]}")
        AI_SEARCH_QUERIES[query] = []
        return []

    queries = []
    for item in parsed.get("queries", []):
        if not isinstance(item, str):
            continue
        item = base.clean_text(item)
        if item and item != query:
            queries.append(item)
    AI_SEARCH_QUERIES[query] = dedupe_keep_order(queries)[:5]
    return AI_SEARCH_QUERIES[query]


def query_text_variants(query: str) -> list[str]:
    variants = [query, query.casefold(), query.upper(), query.title()]
    deduped = []
    for variant in variants:
        if variant not in deduped:
            deduped.append(variant)
    return deduped


def exact_text_xpath(query: str, relative: bool = False) -> str:
    prefix = ".//*" if relative else "//*"
    clauses = []
    for variant in query_text_variants(query):
        literal = base.xpath_literal(variant)
        clauses.extend((f"@name={literal}", f"@label={literal}", f"@value={literal}"))
    return f"{prefix}[{' or '.join(clauses)}]"


def is_search_chrome_text(text: str) -> bool:
    if text in {
        "取消",
        "全部",
        "剧本",
        "兴趣",
        "店铺",
        "组局",
        "用户",
        "发行",
        "No more data",
        "Pull down to refresh",
        "暂无相关结果，换个搜索词试试",
        "提交反馈，帮助我们尽快收录",
    }:
        return True
    return "搜索" in text or "Search" in text


def search_recommendation_texts(driver: webdriver.Remote) -> list[str]:
    """Read visible recommendation texts shown under the active search field."""
    try:
        source = base.get_page_source(driver, attempts=1)
        entries = base.text_entries(base.parse_xml(source))
    except (WebDriverException, ValueError):
        return []

    screen = base.window_rect(driver)
    height = screen.get("height", 844)
    suggestions = []
    for entry in sorted(entries, key=lambda item: (item["y"], item["x"])):
        text = entry["text"].strip()
        if not entry["visible"] or not text:
            continue
        if entry["y"] < height * 0.10 or entry["y"] > height * 0.62:
            continue
        if is_search_chrome_text(text):
            continue
        if len(normalize_title(text)) < 2:
            continue
        if text not in suggestions:
            suggestions.append(text)
    return suggestions


def wait_for_search_recommendations(driver: webdriver.Remote, timeout: float = 1.8) -> list[str]:
    deadline = time.time() + timeout
    suggestions = []
    while time.time() < deadline:
        suggestions = search_recommendation_texts(driver)
        if suggestions:
            return suggestions
        time.sleep(0.25)
    return suggestions


def compatible_recommendations(driver: webdriver.Remote, query: str) -> list[str]:
    """Read search suggestions after typing and keep only title-compatible ones."""
    suggestions = wait_for_search_recommendations(driver)
    RAW_SEARCH_RECOMMENDATIONS[query] = suggestions
    return [text for text in suggestions if is_exact_title_match(text, query)]


def make_driver() -> webdriver.Remote:
    options = XCUITestOptions()
    for key, value in clean_caps(base.DEFAULT_CAPS).items():
        options.set_capability(key, value)
    client_config = AppiumClientConfig(
        remote_server_addr=base.APPIUM_URL,
        timeout=base.APPIUM_COMMAND_TIMEOUT_SECONDS,
    )
    try:
        return webdriver.Remote(base.APPIUM_URL, options=options, client_config=client_config)
    except (WebDriverException, MaxRetryError, ProtocolError, ReadTimeoutError, TimeoutError) as exc:
        message = str(exc)
        if "could not be, unlocked" in message or "BSErrorCodeDescription=Locked" in message:
            raise RuntimeError(
                "The iPad is locked, so Appium cannot launch 谜圈. "
                "Unlock the iPad, keep it on the home screen or in the app, "
                "and rerun this command."
            ) from exc
        if "Connection refused" in message or "Failed to establish a new connection" in message:
            raise RuntimeError(
                f"Could not connect to Appium at {base.APPIUM_URL}. Start Appium Server, then rerun."
            ) from exc
        if "Remote end closed connection" in message or "Connection aborted" in message:
            raise RuntimeError(
                "Appium closed the session request before WebDriverAgent was ready. "
                "Stop Appium, unlock and reconnect the iPad, restart Appium, then rerun. "
                "Existing iPad rows will be skipped."
            ) from exc
        if "Read timed out" in message or "timed out" in message:
            raise RuntimeError(
                "Timed out while creating a new Appium session. Restart Appium Server, "
                "unlock the iPad, keep 谜圈 open or on the home screen, then rerun."
            ) from exc
        if (
            "Unable to launch WebDriverAgent" in message
            or "Unable to start WebDriverAgent session" in message
            or "xcodebuild failed with code 65" in message
            or "socket hang up" in message
        ):
            excerpt = " ".join(message.split())
            if len(excerpt) > 700:
                excerpt = excerpt[:700] + "..."
            raise RuntimeError(
                "Appium could not start WebDriverAgent on the iPad. Stop Appium, "
                "unlock and reconnect the iPad, remove any stuck WebDriverAgentRunner "
                "from the device if needed, then start Appium again.\n"
                f"Last Appium error excerpt: {excerpt}"
            ) from exc
        raise


def dump_debug_snapshot(driver: webdriver.Remote, debug_dir: Path | None, name: str) -> None:
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    source_path = debug_dir / f"{base.safe_name(name)}.xml"
    try:
        screenshot_path = debug_dir / f"{base.safe_name(name)}.png"
        driver.save_screenshot(str(screenshot_path))
    except WebDriverException:
        pass
    source_risky_prefixes = ("title_mismatch_", "error_", "return_to_search_failed_", "no_reviews_")
    if name.startswith(source_risky_prefixes):
        source_path.write_text("Skipped page source for this iPad failure snapshot.", encoding="utf-8")
        return
    try:
        source_path.write_text(base.get_page_source(driver), encoding="utf-8")
    except Exception as exc:
        source_path.write_text(f"Could not get page source: {exc}", encoding="utf-8")


def search_fields(driver: webdriver.Remote) -> list:
    try:
        return driver.find_elements(By.XPATH, SEARCH_FIELD_XPATH)
    except WebDriverException:
        return []


def has_search_field(driver: webdriver.Remote) -> bool:
    return bool(search_fields(driver))


def is_search_screen(driver: webdriver.Remote) -> bool:
    if not has_search_field(driver):
        return False
    try:
        source = base.get_page_source(driver, attempts=1)
    except WebDriverException:
        return True
    if "取消" in source:
        return True
    return all(tab in source for tab in ("全部", "剧本", "兴趣", "店铺"))


def is_home_screen(driver: webdriver.Remote) -> bool:
    if has_search_field(driver):
        return False
    try:
        source = base.get_page_source(driver, attempts=1)
    except WebDriverException:
        source = ""
    if "bgtop_home_background" in source:
        return True
    if 'name="首页"' in source and 'name="标签页栏"' in source:
        return True
    try:
        return bool(
            driver.find_elements(
                By.XPATH,
                "//XCUIElementTypeTabBar//XCUIElementTypeButton[@name='首页' or @label='首页']",
            )
        )
    except WebDriverException:
        return False


def tap_best_element(elements: list, min_y: int = 0, max_y: int | None = None) -> bool:
    candidates = []
    for element in elements:
        try:
            rect = element.rect
        except WebDriverException:
            continue
        if rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
            continue
        if rect.get("y", 0) < min_y:
            continue
        if max_y is not None and rect.get("y", 0) > max_y:
            continue
        candidates.append((rect.get("y", 99999), rect.get("x", 99999), element, rect))
    if not candidates:
        return False
    _, _, element, rect = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    try:
        element.click()
    except WebDriverException:
        base.tap_rect_center(element.parent, rect)
    return True


def tap_xpath(driver: webdriver.Remote, xpath: str, timeout: float = 1.0) -> bool:
    try:
        return base.tap_if_present(driver, xpath, timeout=timeout)
    except (NoSuchElementException, WebDriverException):
        return False


def xml_attr_text(element: ET.Element) -> str:
    return " ".join(
        element.attrib.get(key, "") for key in ("value", "name", "label", "placeholderValue")
    )


def xml_rect(element: ET.Element) -> dict:
    return {
        "x": int(float(element.attrib.get("x", "0"))),
        "y": int(float(element.attrib.get("y", "0"))),
        "width": int(float(element.attrib.get("width", "0"))),
        "height": int(float(element.attrib.get("height", "0"))),
    }


def visible_xml_element(element: ET.Element) -> bool:
    visible = element.attrib.get("visible")
    return visible in {None, "", "true"}


def search_entry_candidates(root: ET.Element, screen_width: int, screen_height: int) -> list:
    """Return top-page rectangles likely to open search on the home screen."""
    candidates = []
    top_limit = int(screen_height * 0.20)
    search_types = {
        "XCUIElementTypeButton",
        "XCUIElementTypeImage",
        "XCUIElementTypeOther",
        "XCUIElementTypeSearchField",
        "XCUIElementTypeStaticText",
        "XCUIElementTypeTextField",
    }
    for element in root.iter():
        type_ = element.attrib.get("type", "")
        if type_ not in search_types or not visible_xml_element(element):
            continue
        rect = xml_rect(element)
        if rect["width"] <= 0 or rect["height"] <= 0:
            continue
        if rect["y"] > top_limit:
            continue

        text = xml_attr_text(element)
        score = None
        tap_rect = rect
        if type_ in {"XCUIElementTypeTextField", "XCUIElementTypeSearchField"}:
            score = 0
        elif (
            type_ == "XCUIElementTypeButton"
            and rect["x"] >= 80
            and rect["width"] >= 180
            and 40 <= rect["y"] <= 95
            and 24 <= rect["height"] <= 44
        ):
            score = 0
        elif "home_top_search" in text:
            score = 1
            tap_rect = {
                "x": max(0, min(rect["x"] - 20, screen_width - 300)),
                "y": max(0, rect["y"] - 18),
                "width": min(300, screen_width),
                "height": max(44, rect["height"] + 32),
            }
        elif "搜索" in text or "Search" in text:
            score = 2
        elif (
            type_ in {"XCUIElementTypeButton", "XCUIElementTypeOther"}
            and rect["width"] >= 120
            and 24 <= rect["height"] <= 60
            and 35 <= rect["y"] <= 130
        ):
            score = 4

        if score is not None:
            candidates.append((score, tap_rect["y"], tap_rect["x"], tap_rect))

    unique = {}
    for score, y, x, rect in sorted(candidates, key=lambda item: (item[0], item[1], item[2])):
        key = (rect["x"], rect["y"], rect["width"], rect["height"])
        unique.setdefault(key, (score, y, x, rect))
    return list(unique.values())


def tap_home_search_button_element(driver: webdriver.Remote) -> bool:
    """Click the home hot-keyword search button exposed by Miquan."""
    candidates = []
    try:
        buttons = driver.find_elements(By.XPATH, "//XCUIElementTypeButton")
    except WebDriverException:
        return False

    for button in buttons:
        try:
            rect = button.rect
            name = button.get_attribute("name") or button.get_attribute("label") or ""
        except WebDriverException:
            continue
        if (
            rect.get("x", 0) >= 80
            and 40 <= rect.get("y", 0) <= 95
            and rect.get("width", 0) >= 180
            and 24 <= rect.get("height", 0) <= 44
        ):
            candidates.append((rect.get("y", 99999), rect.get("x", 99999), button, rect, name))

    for _, _, button, rect, name in sorted(candidates, key=lambda item: (item[0], item[1])):
        print(f"  tapping home search entry: {name or rect}")
        for x_ratio in (0.50, 0.80, 0.25):
            try:
                driver.execute_script(
                    "mobile: tap",
                    {
                        "x": int(rect["x"] + rect["width"] * x_ratio),
                        "y": int(rect["y"] + rect["height"] / 2),
                    },
                )
            except WebDriverException:
                continue
            time.sleep(1.2)
            if is_search_screen(driver):
                return True
        try:
            button.click()
        except WebDriverException:
            base.tap_rect_center(driver, rect)
        time.sleep(1.4)
        if is_search_screen(driver):
            return True
    return False


def tap_home_search_bar(driver: webdriver.Remote) -> bool:
    """Tap the home search pill, whose label is often a rotating hot keyword."""
    screen = base.window_rect(driver)
    width = int(screen.get("width", 390))
    height = int(screen.get("height", 844))

    if tap_home_search_button_element(driver):
        return True

    try:
        root = base.parse_xml(base.get_page_source(driver, attempts=1))
    except Exception:
        root = None

    if root is not None:
        for _, _, _, rect in search_entry_candidates(root, width, height):
            print(f"  tapping home search candidate rect: {rect}")
            driver.execute_script(
                "mobile: tap",
                {
                    "x": int(rect["x"] + rect["width"] / 2),
                    "y": int(rect["y"] + rect["height"] / 2),
                },
            )
            time.sleep(1.0)
            if is_search_screen(driver):
                return True

    # The app may run in iPhone compatibility size on iPad. These taps target
    # both the virtual app coordinates and the visible top search pill.
    for point in (
        (0.79, 0.082),
        (0.595, 0.082),
        (0.30, 0.082),
        (0.45, 0.082),
        (0.50, 0.082),
        (0.35, 0.082),
        (0.65, 0.082),
        (0.50, 0.115),
        (0.35, 0.115),
        (0.65, 0.115),
        (0.50, 0.155),
    ):
        driver.execute_script(
            "mobile: tap",
            {
                "x": int(width * point[0]),
                "y": int(height * point[1]),
            },
        )
        time.sleep(1.0)
        if is_search_screen(driver):
            return True
    return False


def go_back_once(driver: webdriver.Remote) -> bool:
    for xpath in BACK_BUTTON_XPATHS:
        if tap_xpath(driver, xpath, timeout=0.8):
            time.sleep(0.8)
            return True
    rect = base.window_rect(driver)
    width = rect.get("width", 390)
    height = rect.get("height", 844)
    tapped = False
    for point in ((0.080, 0.095), (0.055, 0.095), (0.115, 0.095), (0.080, 0.075)):
        try:
            driver.execute_script(
                "mobile: tap",
                {
                    "x": int(width * point[0]),
                    "y": int(height * point[1]),
                },
            )
            tapped = True
            time.sleep(0.8)
            if is_search_screen(driver):
                return True
        except WebDriverException:
            continue
    if tapped:
        return True
    try:
        base.tap_rect_center(
            driver,
            {
                "x": int(width * 0.025),
                "y": int(height * 0.045),
                "width": 70,
                "height": 70,
            },
        )
        time.sleep(0.8)
        return True
    except WebDriverException:
        return False


def go_to_search(driver: webdriver.Remote) -> None:
    """Open or return to the iPad search surface."""
    if is_search_screen(driver):
        return

    if is_home_screen(driver) and tap_home_search_bar(driver):
        return

    for _ in range(5):
        if is_search_screen(driver):
            return
        if is_home_screen(driver):
            break
        if not go_back_once(driver):
            break

    if is_home_screen(driver) and tap_home_search_bar(driver):
        return

    rect = base.window_rect(driver)
    top_limit = int(rect.get("height", 1366) * 0.22)
    for xpath in SEARCH_ENTRY_XPATHS:
        try:
            if tap_best_element(driver.find_elements(By.XPATH, xpath), max_y=top_limit):
                time.sleep(1.0)
                if is_search_screen(driver):
                    return
        except WebDriverException:
            pass

    if tap_home_search_bar(driver):
        return

    # iPad builds often expose the search affordance as an unlabeled icon in
    # the top chrome. Probe a few likely locations before giving up.
    for point in ((0.89, 0.055), (0.94, 0.055), (0.50, 0.070), (0.31, 0.070)):
        driver.execute_script("mobile: tap", base.relative_point(driver, *point))
        time.sleep(1.0)
        if is_search_screen(driver):
            return

    base.wait_for(driver, By.XPATH, SEARCH_FIELD_XPATH, timeout=8)
    if not is_search_screen(driver):
        raise RuntimeError("Found a search field but did not enter the active search screen.")


def relaunch_app(driver: webdriver.Remote) -> bool:
    try:
        driver.terminate_app(base.APP_BUNDLE_ID)
        time.sleep(1.0)
    except (AttributeError, WebDriverException):
        pass
    try:
        driver.activate_app(base.APP_BUNDLE_ID)
    except (AttributeError, WebDriverException):
        try:
            driver.execute_script("mobile: launchApp", {"bundleId": base.APP_BUNDLE_ID})
        except WebDriverException:
            return False
    time.sleep(3.0)
    return True


def active_search_field(driver: webdriver.Remote):
    fields = search_fields(driver)
    if not fields:
        return base.wait_for(driver, By.XPATH, SEARCH_FIELD_XPATH, timeout=10)
    visible_fields = []
    for field in fields:
        try:
            rect = field.rect
        except WebDriverException:
            continue
        if rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
            visible_fields.append((rect.get("y", 99999), rect.get("x", 99999), field))
    return sorted(visible_fields, key=lambda item: (item[0], item[1]))[0][2] if visible_fields else fields[0]


def search_field_value(field) -> str:
    for attr in ("value", "label", "name"):
        try:
            value = field.get_attribute(attr) or ""
        except WebDriverException:
            value = ""
        value = base.clean_text(value)
        if value:
            return value
    return ""


def clear_search_field(driver: webdriver.Remote) -> None:
    field = active_search_field(driver)
    field.click()
    time.sleep(0.2)
    for _ in range(5):
        tap_xpath(
            driver,
            "//*[@name='sys clear icon' or @label='sys clear icon' or @value='sys clear icon']",
            timeout=0.3,
        )
        try:
            field.clear()
        except WebDriverException:
            pass
        time.sleep(0.2)
        field = active_search_field(driver)
        value = search_field_value(field)
        if not value or "搜索" in value:
            return
        try:
            field.click()
            field.send_keys("\b" * min(len(value), 40))
        except WebDriverException:
            pass
        time.sleep(0.2)
        field = active_search_field(driver)
        value = search_field_value(field)
        if not value or "搜索" in value:
            return


def enter_search_query(driver: webdriver.Remote, query: str) -> None:
    for _ in range(3):
        clear_search_field(driver)
        field = active_search_field(driver)
        field.click()
        time.sleep(0.2)
        field.send_keys(query)
        time.sleep(0.5)
        field = active_search_field(driver)
        if search_field_value(field) == query:
            return
    raise RuntimeError(f"Search field did not contain expected query: {query}")


def tap_keyboard_search(driver: webdriver.Remote) -> bool:
    rect = base.window_rect(driver)
    min_y = int(rect.get("height", 844) * 0.55)
    for label in ("搜索", "Search", "return"):
        xpath = (
            f"//*[@name={base.xpath_literal(label)} "
            f"or @label={base.xpath_literal(label)} "
            f"or @value={base.xpath_literal(label)}]"
        )
        try:
            if tap_best_element(driver.find_elements(By.XPATH, xpath), min_y=min_y):
                return True
        except WebDriverException:
            continue
    try:
        driver.execute_script("mobile: tap", base.relative_point(driver, 0.90, 0.94))
        return True
    except WebDriverException:
        return False


def submit_search(driver: webdriver.Remote, query: str, capture_recommendations: bool = True) -> None:
    enter_search_query(driver, query)
    if capture_recommendations:
        SEARCH_RECOMMENDATIONS[query] = compatible_recommendations(driver, query)
        raw_recommendations = RAW_SEARCH_RECOMMENDATIONS.get(query, [])
        if raw_recommendations:
            first = raw_recommendations[0]
            verdict = "compatible" if is_exact_title_match(first, query) else "not compatible"
            print(f"  first recommended text under search bar: {first} ({verdict})")
        else:
            print("  no recommended text visible under search bar")
    tap_keyboard_search(driver)
    time.sleep(0.8)

    time.sleep(1.2)
    tap_search_tab(driver, "剧本")
    time.sleep(1.0)


def tap_search_tab(driver: webdriver.Remote, tab_name: str) -> bool:
    rect = base.window_rect(driver)
    height = rect.get("height", 1366)
    candidates = driver.find_elements(
        By.XPATH,
        f"//*[@name={base.xpath_literal(tab_name)} "
        f"or @label={base.xpath_literal(tab_name)} "
        f"or @value={base.xpath_literal(tab_name)}]",
    )
    positioned = []
    for element in candidates:
        try:
            item_rect = element.rect
        except WebDriverException:
            continue
        y = item_rect.get("y", 0)
        if height * 0.05 <= y <= height * 0.25 and item_rect.get("width", 0) > 0:
            positioned.append((y, item_rect.get("x", 0), element, item_rect))
    if positioned:
        _, _, element, item_rect = sorted(positioned, key=lambda item: (item[0], item[1]))[0]
        try:
            element.click()
        except WebDriverException:
            base.tap_rect_center(driver, item_rect)
        return True
    return tap_xpath(driver, f"//XCUIElementTypeButton[@name={base.xpath_literal(tab_name)}]", timeout=1)


def get_search_section_bounds(driver: webdriver.Remote) -> dict:
    screen = base.window_rect(driver)
    width = screen.get("width", 1024)
    height = screen.get("height", 1366)
    bounds = {
        "script_top": int(height * 0.18),
        "script_bottom": int(height * 0.58),
        "script_card": None,
    }

    try:
        entries = base.text_entries(base.parse_xml(base.get_page_source(driver)))
    except (WebDriverException, ValueError):
        entries = []

    headings = [
        entry
        for entry in entries
        if entry["text"] in {"剧本", "用户", "店铺", "发行"}
        and entry["visible"]
        and entry["y"] >= height * 0.12
    ]
    script_heading = sorted(
        [entry for entry in headings if entry["text"] == "剧本"],
        key=lambda entry: (entry["y"], entry["x"]),
    )
    if script_heading:
        bounds["script_top"] = script_heading[0]["y"] + max(28, script_heading[0]["height"])
        lower_headings = [
            entry["y"]
            for entry in headings
            if entry["text"] in {"用户", "店铺", "发行"}
            and entry["y"] > script_heading[0]["y"] + 20
        ]
        if lower_headings:
            bounds["script_bottom"] = min(lower_headings) - 8

    bounds["script_card"] = {
        "x": int(width * 0.03),
        "y": bounds["script_top"] + 12,
        "width": int(width * 0.58),
        "height": max(90, bounds["script_bottom"] - bounds["script_top"] - 18),
    }
    return bounds


def tap_first_script_result_cell(driver: webdriver.Remote, query: str) -> bool:
    return tap_first_visible_result_title(driver, query)


def tap_first_visible_result_title(driver: webdriver.Remote, query: str) -> bool:
    bounds = get_search_section_bounds(driver)
    native_title = native_visible_result_title(driver, query)
    if native_title:
        print(
            "  tapping native matching search result title: "
            f"{native_title['text']} at x={native_title['x']} y={native_title['y']}; "
            f"tap rect x={native_title['tap_rect']['x']} y={native_title['tap_rect']['y']} "
            f"w={native_title['tap_rect']['width']} h={native_title['tap_rect']['height']}"
        )
        if click_result_cell_containing_title(driver, native_title["text"]):
            return True
        try:
            native_title["element"].click()
            return True
        except WebDriverException:
            base.tap_rect_center(driver, native_title["tap_rect"])
        return True

    fuzzy_title = fuzzy_visible_result_title(driver, query, bounds)
    if fuzzy_title:
        print(
            "  tapping matching search result title: "
            f"{fuzzy_title['text']} at x={fuzzy_title['x']} y={fuzzy_title['y']}; "
            f"tap rect x={fuzzy_title['tap_rect']['x']} y={fuzzy_title['tap_rect']['y']} "
            f"w={fuzzy_title['tap_rect']['width']} h={fuzzy_title['tap_rect']['height']}"
        )
        base.tap_rect_center(driver, fuzzy_title["tap_rect"])
        return True

    elements = driver.find_elements(By.XPATH, exact_text_xpath(query))
    visible_candidates = []
    for element in elements:
        try:
            rect = element.rect
        except WebDriverException:
            continue
        if (
            bounds["script_top"] <= rect.get("y", 0) <= bounds["script_bottom"]
            and rect.get("width", 0) > 0
            and rect.get("height", 0) > 0
        ):
            visible_candidates.append((rect.get("y", 99999), rect.get("x", 99999), element, rect))
    if visible_candidates:
        _, _, element, rect = sorted(visible_candidates, key=lambda item: (item[0], item[1]))[0]
        print(f"  tapping exact visible search result title for {query}")
        try:
            element.click()
        except WebDriverException:
            base.tap_rect_center(driver, rect)
        return True
    return False


def native_visible_result_title(driver: webdriver.Remote, query: str) -> dict | None:
    screen = base.window_rect(driver)
    relaxed_top = int(screen.get("height", 844) * 0.14)
    relaxed_bottom = int(screen.get("height", 844) * 0.90)
    candidates = []
    try:
        elements = driver.find_elements(By.XPATH, "//XCUIElementTypeStaticText")
    except WebDriverException:
        return None

    for element in elements:
        text = ""
        for attr in ("value", "label", "name"):
            try:
                text = element.get_attribute(attr) or ""
            except WebDriverException:
                text = ""
            if text:
                break
        text = base.clean_text(text)
        if not text:
            continue
        try:
            rect = element.rect
        except WebDriverException:
            continue
        entry = {
            "text": text,
            "x": int(rect.get("x", 0)),
            "y": int(rect.get("y", 0)),
            "width": int(rect.get("width", 0)),
            "height": int(rect.get("height", 0)),
            "visible": rect.get("width", 0) > 0 and rect.get("height", 0) > 0,
        }
        if not entry["visible"]:
            continue
        if not (relaxed_top <= entry["y"] <= relaxed_bottom):
            continue
        if not looks_like_individual_result_title(entry, screen):
            continue
        if not is_exact_title_match(entry["text"], query):
            continue
        cell_y = max(relaxed_top, entry["y"] - 15)
        candidates.append(
            {
                **entry,
                "element": element,
                "tap_rect": {
                    "x": 0,
                    "y": cell_y,
                    "width": int(screen.get("width", 390)),
                    "height": 185,
                },
            }
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda entry: (entry["y"], entry["x"]))[0]


def click_result_cell_containing_title(driver: webdriver.Remote, title: str) -> bool:
    literal = base.xpath_literal(title)
    xpath = (
        "//XCUIElementTypeCell[.//XCUIElementTypeStaticText["
        f"@value={literal} or @name={literal} or @label={literal}"
        "]]"
    )
    try:
        cells = driver.find_elements(By.XPATH, xpath)
    except WebDriverException:
        return False
    positioned = []
    for cell in cells:
        try:
            rect = cell.rect
        except WebDriverException:
            continue
        if rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
            positioned.append((rect.get("y", 99999), rect.get("x", 99999), cell, rect))
    if not positioned:
        return False
    _, _, cell, rect = sorted(positioned, key=lambda item: (item[0], item[1]))[0]
    print(
        f"  clicking result cell containing title: {title} "
        f"at x={int(rect.get('x', 0))} y={int(rect.get('y', 0))} "
        f"w={int(rect.get('width', 0))} h={int(rect.get('height', 0))}"
    )
    try:
        cell.click()
    except WebDriverException:
        base.tap_rect_center(driver, rect)
    return True


def looks_like_individual_result_title(entry: dict, screen: dict) -> bool:
    text = entry["text"]
    if not base.looks_like_script_title(text):
        return False
    if "\n" in text or "Pull down" in text or "No more data" in text:
        return False
    if entry["width"] >= screen.get("width", 390) * 0.92:
        return False
    if entry["height"] > 50:
        return False
    if len(text) > 32:
        return False
    return True


def xml_rect(element: ET.Element) -> dict:
    return {
        "x": int(float(element.attrib.get("x", "0"))),
        "y": int(float(element.attrib.get("y", "0"))),
        "width": int(float(element.attrib.get("width", "0"))),
        "height": int(float(element.attrib.get("height", "0"))),
    }


def fuzzy_visible_result_title(driver: webdriver.Remote, query: str, bounds: dict | None = None) -> dict | None:
    bounds = bounds or get_search_section_bounds(driver)
    screen = base.window_rect(driver)
    relaxed_top = int(screen.get("height", 844) * 0.14)
    relaxed_bottom = int(screen.get("height", 844) * 0.90)
    try:
        root = base.parse_xml(base.get_page_source(driver, attempts=1))
    except (WebDriverException, ValueError):
        return None

    parent_map = {child: parent for parent in root.iter() for child in parent}
    candidates = []
    for element in root.iter():
        if element.attrib.get("type") not in base.TEXT_TYPES:
            continue
        text = base.clean_text(
            element.attrib.get("value") or element.attrib.get("label") or element.attrib.get("name") or ""
        )
        if not text:
            continue
        entry = {**xml_rect(element), "text": text, "visible": element.attrib.get("visible") == "true"}
        if not entry["visible"]:
            continue
        if not (relaxed_top <= entry["y"] <= relaxed_bottom):
            continue
        if not looks_like_individual_result_title(entry, screen):
            continue
        if not is_exact_title_match(entry["text"], query):
            continue
        cell_rect = None
        parent = parent_map.get(element)
        while parent is not None:
            if parent.attrib.get("type") == "XCUIElementTypeCell":
                rect = xml_rect(parent)
                if rect["width"] > 0 and rect["height"] > 0 and parent.attrib.get("visible") == "true":
                    cell_rect = rect
                break
            parent = parent_map.get(parent)
        if cell_rect:
            entry["tap_rect"] = cell_rect
        candidates.append(entry)
    if not candidates:
        return None

    title = sorted(candidates, key=lambda entry: (entry["y"], entry["x"]))[0]
    title.setdefault(
        "tap_rect",
        {
            "x": title["x"],
            "y": title["y"],
            "width": title["width"],
            "height": title["height"],
        },
    )
    return title


def visible_script_titles(driver: webdriver.Remote, query: str) -> list[str]:
    bounds = get_search_section_bounds(driver)
    screen = base.window_rect(driver)
    relaxed_bottom = max(bounds["script_bottom"], int(screen.get("height", 844) * 0.86))
    try:
        entries = base.text_entries(base.parse_xml(base.get_page_source(driver, attempts=1)))
    except (WebDriverException, ValueError):
        return []
    titles = []
    for entry in sorted(entries, key=lambda item: (item["y"], item["x"])):
        if not entry["visible"]:
            continue
        if not (bounds["script_top"] <= entry["y"] <= relaxed_bottom):
            continue
        if not looks_like_individual_result_title(entry, screen):
            continue
        if entry["text"] not in titles:
            titles.append(entry["text"])
    return titles[:8]


def fuzzy_script_result_rect(driver: webdriver.Remote, query: str, bounds: dict | None = None) -> dict | None:
    bounds = bounds or get_search_section_bounds(driver)
    try:
        entries = base.text_entries(base.parse_xml(base.get_page_source(driver, attempts=1)))
    except (WebDriverException, ValueError):
        return None

    candidates = []
    for entry in entries:
        if not entry["visible"]:
            continue
        if not (bounds["script_top"] <= entry["y"] <= bounds["script_bottom"]):
            continue
        if not base.looks_like_script_title(entry["text"]):
            continue
        if not is_exact_title_match(entry["text"], query):
            continue
        candidates.append(entry)
    if not candidates:
        return None

    title = sorted(candidates, key=lambda entry: (entry["y"], entry["x"]))[0]
    return {
        "x": max(0, title["x"] - 120),
        "y": max(bounds["script_top"], title["y"] - 15),
        "width": min(360, title["width"] + 150),
        "height": max(65, title["height"] + 55),
    }


def source_looks_like_detail(source: str) -> bool:
    detail_markers = (
        "谜圈评分",
        "维度评分",
        "剧情简介",
        "剧本角色",
        "创作者说",
        "人想玩",
        "人玩过",
        "人点评",
    )
    return any(marker in source for marker in detail_markers)


def detail_entries_from_elements(driver: webdriver.Remote) -> list[dict]:
    entries = []
    try:
        elements = driver.find_elements(By.XPATH, DETAIL_TEXT_XPATH)
    except WebDriverException:
        return entries
    for element in elements:
        try:
            text = (
                element.get_attribute("value")
                or element.get_attribute("label")
                or element.get_attribute("name")
                or ""
            )
            rect = element.rect
        except WebDriverException:
            continue
        text = base.clean_text(text)
        if not text:
            continue
        entries.append(
            {
                "text": text,
                "x": int(rect.get("x", 0)),
                "y": int(rect.get("y", 0)),
                "width": int(rect.get("width", 0)),
                "height": int(rect.get("height", 0)),
                "visible": rect.get("width", 0) > 0 and rect.get("height", 0) > 0,
            }
        )
    return entries


def detail_rating_region_entries(driver: webdriver.Remote) -> list[dict]:
    """Read only visible detail-page title/rating texts without full page source."""
    predicates = [
        "type == 'XCUIElementTypeStaticText' AND rect.y >= 90 AND rect.y <= 380",
        "type == 'XCUIElementTypeButton' AND rect.y >= 90 AND rect.y <= 380",
    ]
    entries = []
    for predicate in predicates:
        try:
            elements = driver.find_elements(AppiumBy.IOS_PREDICATE, predicate)
        except WebDriverException:
            continue
        for element in elements:
            try:
                rect = element.rect
            except WebDriverException:
                continue
            text = ""
            for attr in ("value", "label", "name"):
                try:
                    text = element.get_attribute(attr) or ""
                except WebDriverException:
                    text = ""
                if text:
                    break
            text = base.clean_text(text)
            if not text:
                continue
            entries.append(
                {
                    "text": text,
                    "x": int(rect.get("x", 0)),
                    "y": int(rect.get("y", 0)),
                    "width": int(rect.get("width", 0)),
                    "height": int(rect.get("height", 0)),
                    "visible": rect.get("width", 0) > 0 and rect.get("height", 0) > 0,
                }
            )
    return sorted(entries, key=lambda entry: (entry["y"], entry["x"], entry["text"]))


def entries_look_like_detail(entries: list[dict], query: str) -> bool:
    visible_texts = [entry["text"] for entry in entries if entry["visible"]]
    if query and not any(base.is_exact_title_match(text, query) for text in visible_texts):
        return False
    detail_markers = ("谜圈评分", "维度评分", "剧情简介", "剧本角色", "想玩", "玩过", "点评")
    if any(any(marker in text for marker in detail_markers) for text in visible_texts):
        return True
    return any(base.is_rating_score(text) for text in visible_texts)


def current_screen_is_detail(driver: webdriver.Remote) -> bool:
    try:
        source = base.get_page_source(driver, attempts=1)
    except WebDriverException:
        return entries_look_like_detail(detail_entries_from_elements(driver), "")
    if not source_looks_like_detail(source):
        return False
    return not has_search_field(driver)


def current_screen_is_query_detail(driver: webdriver.Remote, query: str) -> bool:
    try:
        source = base.get_page_source(driver, attempts=1)
    except WebDriverException:
        return entries_look_like_detail(detail_entries_from_elements(driver), query)
    if has_search_field(driver) or not source_looks_like_detail(source):
        return False
    try:
        entries = base.text_entries(base.parse_xml(source))
    except ValueError:
        return False
    return entries_look_like_detail(entries, query)


def wait_for_detail_page(driver: webdriver.Remote, query: str, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if current_screen_is_query_detail(driver, query):
            return True
        time.sleep(0.4)
    return current_screen_is_query_detail(driver, query)


def result_title_candidates(driver: webdriver.Remote, query: str) -> list[tuple[int, int, object, dict]]:
    screen = base.window_rect(driver)
    height = screen.get("height", 844)
    candidates = []
    for element in driver.find_elements(By.XPATH, exact_text_xpath(query)):
        try:
            rect = element.rect
        except WebDriverException:
            continue
        y = rect.get("y", 0)
        if y < height * 0.14 or y > height * 0.86:
            continue
        if rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
            continue
        candidates.append((y, rect.get("x", 0), element, rect))
    return sorted(candidates, key=lambda item: (item[0], item[1]))


def result_candidate_tap_points(candidate: tuple[int, int, object, dict]) -> list[tuple[int, int]]:
    _, _, _element, rect = candidate
    points = [
        (rect["x"] + rect["width"] * 0.50, rect["y"] + rect["height"] * 0.50),
        (rect["x"] + rect["width"] * 0.15, rect["y"] + rect["height"] * 0.50),
        (rect["x"] + rect["width"] * 0.50, rect["y"] + rect["height"] + 26),
    ]
    return [(int(x), int(y)) for x, y in points]


def click_result_candidate(candidate: tuple[int, int, object, dict]) -> bool:
    _, _, element, _rect = candidate
    try:
        element.click()
        return True
    except WebDriverException:
        return False


def tap_screen_point(driver: webdriver.Remote, point: tuple[int, int]) -> bool:
    try:
        driver.execute_script("mobile: tap", {"x": point[0], "y": point[1]})
        return True
    except WebDriverException:
        return False


def try_open_current_script_result(
    driver: webdriver.Remote,
    query: str,
    debug_dir: Path | None = None,
    snapshot_name: str | None = None,
) -> bool:
    if current_screen_is_detail(driver):
        return True

    base.dump_debug_snapshot(driver, debug_dir, snapshot_name or f"search_results_{query}")
    tap_attempts: list[callable] = [
        lambda: tap_first_script_result_cell(driver, query),
    ]
    for candidate in result_title_candidates(driver, query):
        tap_attempts.append(lambda candidate=candidate: click_result_candidate(candidate))
        tap_attempts.extend(
            lambda point=point: tap_screen_point(driver, point)
            for point in result_candidate_tap_points(candidate)
        )

    for tap_attempt in tap_attempts:
        try:
            did_tap = tap_attempt()
        except WebDriverException:
            did_tap = False
        if not did_tap:
            continue
        if wait_for_detail_page(driver, query):
            return True

    return False


def try_recommended_search(driver: webdriver.Remote, query: str, debug_dir: Path | None = None) -> bool:
    raw_recommendations = RAW_SEARCH_RECOMMENDATIONS.get(query, [])
    if not raw_recommendations and has_search_field(driver):
        print("  refreshing search recommendations after no-result page")
        try:
            enter_search_query(driver, query)
            SEARCH_RECOMMENDATIONS[query] = compatible_recommendations(driver, query)
            raw_recommendations = RAW_SEARCH_RECOMMENDATIONS.get(query, [])
        except (RuntimeError, WebDriverException) as exc:
            print(f"  could not refresh recommended text: {str(exc).splitlines()[0]}")
    if raw_recommendations:
        recommendation = raw_recommendations[0]
        if recommendation == query:
            print(f"  first recommended text is the same as the query: {recommendation}")
            return False
        if not is_exact_title_match(recommendation, query):
            print(f"  first recommended text is not a title match: {recommendation}")
            return False
        print(f"  retrying search with first recommended text: {recommendation}")
        submit_search(driver, recommendation, capture_recommendations=False)
        snapshot_name = f"search_results_{query}_recommended_{recommendation}"
        return try_open_current_script_result(driver, query, debug_dir, snapshot_name)

    for recommendation in SEARCH_RECOMMENDATIONS.get(query, []):
        if recommendation == query:
            continue
        print(f"  retrying search with compatible recommended text: {recommendation}")
        submit_search(driver, recommendation, capture_recommendations=False)
        snapshot_name = f"search_results_{query}_recommended_{recommendation}"
        if try_open_current_script_result(driver, query, debug_dir, snapshot_name):
            return True
    print("  no compatible recommended text was captured for retry")
    return False


def try_variant_search(driver: webdriver.Remote, query: str, debug_dir: Path | None = None) -> bool:
    for variant in title_search_variants(query):
        print(f"  retrying search with title variant: {variant}")
        submit_search(driver, variant, capture_recommendations=False)
        snapshot_name = f"search_results_{query}_variant_{variant}"
        if try_open_current_script_result(driver, query, debug_dir, snapshot_name):
            return True
    return False


def try_ai_search(driver: webdriver.Remote, query: str, debug_dir: Path | None = None) -> bool:
    inferred_queries = infer_ai_search_queries(query)
    if not inferred_queries:
        print("  no AI-inferred search queries available")
        return False
    print(f"  AI-inferred search queries: {', '.join(inferred_queries)}")
    for inferred_query in inferred_queries:
        print(f"  retrying search with AI-inferred query: {inferred_query}")
        submit_search(driver, inferred_query, capture_recommendations=False)
        snapshot_name = f"search_results_{query}_ai_{inferred_query}"
        if try_open_current_script_result(driver, query, debug_dir, snapshot_name):
            return True
        titles = visible_script_titles(driver, query)
        if titles:
            print(f"  no matching title tapped for {query}; visible script titles: {'; '.join(titles)}")
    return False


def open_first_script_result(driver: webdriver.Remote, query: str, debug_dir: Path | None = None) -> bool:
    if try_open_current_script_result(driver, query, debug_dir):
        return True
    if try_recommended_search(driver, query, debug_dir):
        return True
    if try_ai_search(driver, query, debug_dir):
        return True
    if try_variant_search(driver, query, debug_dir):
        return True

    titles = visible_script_titles(driver, query)
    if titles:
        print(f"  no matching result opened for {query}; visible script titles: {'; '.join(titles)}")
    base.dump_debug_snapshot(driver, debug_dir, f"open_result_failed_{query}")
    try:
        source = base.get_page_source(driver)
        visible_text = "; ".join(base.all_texts(base.parse_xml(source))[:30])
    except WebDriverException:
        visible_text = "source unavailable"
    print(f"  could not open detail page for {query}. visible text: {visible_text[:500]}")
    return False


def read_rating_summary(driver: webdriver.Remote, query: str) -> base.ScriptSummary:
    try:
        source = base.get_page_source(driver)
    except WebDriverException as exc:
        print(f"  could not read rating page for {query}: {str(exc).splitlines()[0]}")
        XML_BROKEN_DETAIL_QUERIES.add(query)
        entries = detail_rating_region_entries(driver)
        if entries:
            summary = parse_summary_entries(entries, query)
            if base.is_exact_title_match(summary.script_title, query):
                return summary
        relaunch_app(driver)
        try:
            go_to_search(driver)
        except (RuntimeError, WebDriverException):
            pass
        return base.ScriptSummary(query=query)
    if has_search_field(driver) or not source_looks_like_detail(source):
        return base.ScriptSummary(query=query)
    return base.parse_summary(source, query=query)


def return_to_search_results(driver: webdriver.Remote, debug_dir: Path | None, query: str) -> bool:
    if query in XML_BROKEN_DETAIL_QUERIES:
        XML_BROKEN_DETAIL_QUERIES.discard(query)
        if relaunch_app(driver):
            try:
                go_to_search(driver)
                return is_search_screen(driver)
            except (RuntimeError, WebDriverException):
                return False
        return False

    for _ in range(10):
        if is_search_screen(driver):
            return True
        if not go_back_once(driver):
            break
        time.sleep(0.5)
    if is_search_screen(driver):
        return True
    base.dump_debug_snapshot(driver, debug_dir, f"return_to_search_failed_{query}")
    if relaunch_app(driver):
        try:
            go_to_search(driver)
            return is_search_screen(driver)
        except (RuntimeError, WebDriverException):
            pass
    return False


def parse_summary_entries(entries: list[dict], query: str) -> base.ScriptSummary:
    summary = ORIGINAL_PARSE_SUMMARY_ENTRIES(entries, query)
    if base.is_exact_title_match(summary.script_title, query):
        return summary

    exact_visible_titles = [
        entry
        for entry in entries
        if entry["visible"]
        and base.is_exact_title_match(entry["text"], query)
        and entry["y"] <= 520
        and base.looks_like_script_title(entry["text"])
    ]
    if exact_visible_titles:
        summary.script_title = sorted(exact_visible_titles, key=lambda entry: (entry["y"], entry["x"]))[0]["text"]
    return summary


def is_nonzero_score(value: str) -> bool:
    if not value or not base.is_rating_score(value):
        return False
    return float(value) > 0


def has_obtained_rating(summary: base.ScriptSummary) -> bool:
    if not summary.query or not base.is_exact_title_match(summary.script_title, summary.query):
        return False
    if any(
        is_nonzero_score(value)
        for value in (summary.overall_rating, summary.plot_score, summary.restore_score, summary.gameplay_score)
    ):
        return True
    return any(is_nonzero_score(score) for score in base.parse_dimension_scores(summary.dimension_scores).values())


def completed_rating_queries(paths: list[Path]) -> set[str]:
    completed: set[str] = set()
    for path in paths:
        for summary in read_existing_successful_ratings(path):
            if has_obtained_rating(summary):
                completed.add(summary.query)
    return completed


def read_existing_successful_ratings(path: Path) -> list[base.ScriptSummary]:
    return [summary for summary in ORIGINAL_READ_EXISTING_RATINGS(path) if has_obtained_rating(summary)]


def install_completed_query_filter(skip_ratings_from: list[Path]) -> None:
    completed = completed_rating_queries(skip_ratings_from)
    if not completed:
        return

    def read_queries_without_completed(path: Path) -> list[str]:
        queries = ORIGINAL_READ_QUERIES(path)
        filtered = [query for query in queries if query not in completed]
        skipped = len(queries) - len(filtered)
        if skipped:
            sources = ", ".join(str(path) for path in skip_ratings_from)
            print(
                f"Loaded {len(completed)} successful rating rows from {sources}; "
                f"skipping {skipped} queries already rated outside the iPad output."
            )
        return filtered

    base.read_queries = read_queries_without_completed


def configure_base_for_ipad(args: argparse.Namespace) -> None:
    caps = DEFAULT_IPAD_CAPS.copy()
    if args.device_name:
        caps["appium:deviceName"] = args.device_name
    if args.udid:
        caps["appium:udid"] = args.udid
    if args.platform_version:
        caps["appium:platformVersion"] = args.platform_version
    if args.wda_port:
        caps["appium:wdaLocalPort"] = args.wda_port
    if args.wda_bundle_id:
        caps["appium:updatedWDABundleId"] = args.wda_bundle_id
    if args.use_new_wda:
        caps["appium:useNewWDA"] = True

    base.APPIUM_URL = args.appium_url
    base.DEFAULT_CAPS = caps
    base.make_driver = make_driver
    base.normalize_title = normalize_title
    base.is_exact_title_match = is_exact_title_match
    base.dump_debug_snapshot = dump_debug_snapshot
    base.relaunch_app = relaunch_app
    base.has_search_field = has_search_field
    base.go_to_search = go_to_search
    base.submit_search = submit_search
    base.tap_search_tab = tap_search_tab
    base.get_search_section_bounds = get_search_section_bounds
    base.open_first_script_result = open_first_script_result
    base.tap_first_script_result_cell = tap_first_script_result_cell
    base.tap_first_visible_result_title = tap_first_visible_result_title
    base.read_rating_summary = read_rating_summary
    base.return_to_search_results = return_to_search_results
    base.parse_summary_entries = parse_summary_entries
    base.debug_failed_query_keys = lambda debug_dir: set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Miquan ratings/reviews from the iPad UI.")
    parser.add_argument("--input", type=Path, default=DATA_DIR / "search_terms" / "search_terms.csv")
    parser.add_argument("--output", type=Path, default=DEFAULT_IPAD_OUTPUT)
    parser.add_argument("--ratings-output", type=Path, default=DEFAULT_IPAD_RATINGS_OUTPUT)
    parser.add_argument(
        "--mode",
        choices=("ratings", "reviews", "both"),
        default="ratings",
        help="ratings is the fast metadata-only pass; reviews opens full review pages.",
    )
    parser.add_argument("--max-scrolls", type=int, default=12)
    parser.add_argument("--reviews-per-script", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N search terms.")
    parser.add_argument(
        "--rerun-existing",
        action="store_true",
        help="Ignore existing iPad ratings rows and scrape every query again.",
    )
    parser.add_argument("--debug-dir", type=Path, default=DEFAULT_IPAD_DEBUG_DIR)
    parser.add_argument(
        "--skip-ratings-from",
        type=Path,
        default=RATINGS_DIR / "miquan_ratings.csv",
        help="Skip queries that already have successful ratings in this CSV, defaulting to the iPhone output.",
    )
    parser.add_argument(
        "--no-skip-iphone-ratings",
        action="store_true",
        help="Do not skip queries already rated in miquan_ratings.csv.",
    )
    parser.add_argument("--appium-url", default=os.environ.get("APPIUM_URL", base.APPIUM_URL))
    parser.add_argument("--device-name", default=os.environ.get("MIQUAN_IPAD_DEVICE_NAME", "iPad (2)"))
    parser.add_argument(
        "--udid",
        default=os.environ.get("MIQUAN_IPAD_UDID", "00008112-0019593922DBA01E"),
    )
    parser.add_argument("--platform-version", default=os.environ.get("MIQUAN_IPAD_PLATFORM_VERSION", "26.3.1"))
    parser.add_argument("--wda-port", type=int, default=int(os.environ.get("MIQUAN_IPAD_WDA_PORT", "8102")))
    parser.add_argument(
        "--wda-bundle-id",
        default=os.environ.get("MIQUAN_IPAD_WDA_BUNDLE_ID", "com.guozhan.WebDriverAgentRunner"),
    )
    parser.add_argument(
        "--use-new-wda",
        action="store_true",
        help="Force Appium to rebuild/reinstall WebDriverAgent. Default is to reuse the existing WDA.",
    )
    args = parser.parse_args()

    configure_base_for_ipad(args)
    if args.no_skip_iphone_ratings:
        base.read_queries = ORIGINAL_READ_QUERIES
    else:
        install_completed_query_filter([args.skip_ratings_from])
    base.scrape(
        args.input,
        args.output,
        args.ratings_output,
        args.mode,
        args.max_scrolls,
        args.reviews_per_script,
        args.limit,
        args.debug_dir,
        args.rerun_existing,
    )


ORIGINAL_PARSE_SUMMARY_ENTRIES = base.parse_summary_entries
ORIGINAL_READ_QUERIES = base.read_queries
ORIGINAL_READ_EXISTING_RATINGS = base.read_existing_ratings
ORIGINAL_NORMALIZE_TITLE = base.normalize_title


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
