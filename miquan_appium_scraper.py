#!/usr/bin/env python3
"""Collect visible Miquan script ratings and reviews through Appium.

This is intentionally conservative: it reads the accessibility/source text
available to the logged-in app, scrolls slowly, and deduplicates review rows.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Iterable

from appium import webdriver
from appium.webdriver.client_config import AppiumClientConfig
from appium.options.ios import XCUITestOptions
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.exceptions import MaxRetryError, ProtocolError, ReadTimeoutError


APPIUM_URL = "http://127.0.0.1:4723"
SCRIPT_DIR = Path(__file__).resolve().parent
APPIUM_COMMAND_TIMEOUT_SECONDS = 180
APP_BUNDLE_ID = "com.juhaowan123.www"

DEFAULT_CAPS = {
    "platformName": "iOS",
    "appium:automationName": "XCUITest",
    "appium:deviceName": "iPhone 16 Plus",
    "appium:udid": "00008140-000975A43E41801C",
    "appium:platformVersion": "26.5",
    "appium:bundleId": APP_BUNDLE_ID,
    "appium:noReset": True,
    "appium:newCommandTimeout": 120,
    "appium:updatedWDABundleId": "com.guozhan.WebDriverAgentRunner",
    "appium:usePrebuiltWDA": False,
    "appium:useNewWDA": True,
    "appium:wdaLocalPort": 8101,
    "appium:showXcodeLog": True,
    "appium:wdaLaunchTimeout": 120000,
    "appium:wdaConnectionTimeout": 120000,
    "appium:wdaStartupRetries": 3,
    "appium:wdaStartupRetryInterval": 10000,
    "appium:customSnapshotTimeout": 20000,
    "appium:snapshotMaxDepth": 50,
}


TEXT_TYPES = {"XCUIElementTypeStaticText", "XCUIElementTypeTextView"}
SENTIMENTS = {"推荐", "一般", "不行"}
DIMENSION_COLUMNS = [
    "剧情",
    "推理",
    "悬疑",
    "欢乐",
    "情感",
    "机制",
    "还原",
    "沉浸",
    "演绎",
    "玩法",
    "故事",
    "难度",
    "互动",
    "立意",
    "阵营",
    "本格",
    "变格",
    "恐怖",
    "硬核",
]
DIMENSION_LABELS = set(DIMENSION_COLUMNS)
RATING_BASE_FIELDS = [
    "query",
    "script_title",
    "overall_rating",
    *DIMENSION_COLUMNS,
    "want_count",
    "played_count",
    "review_count",
    "publishers",
    "metadata",
    "tags",
]
SECTION_STOP_WORDS = {
    "本城可玩店铺",
    "正在组局",
    "剧本相关动态",
    "查看更多",
}


@dataclass
class ScriptSummary:
    query: str
    script_title: str = ""
    overall_rating: str = ""
    plot_score: str = ""
    restore_score: str = ""
    gameplay_score: str = ""
    dimension_scores: str = ""
    want_count: str = ""
    played_count: str = ""
    review_count: str = ""
    publishers: str = ""
    metadata: str = ""
    tags: str = ""


@dataclass
class ReviewRow:
    query: str
    script_title: str
    overall_rating: str
    plot_score: str
    restore_score: str
    gameplay_score: str
    want_count: str
    played_count: str
    review_count: str
    reviewer: str = ""
    review_date: str = ""
    sentiment: str = ""
    review_plot_score: str = ""
    review_restore_score: str = ""
    review_gameplay_score: str = ""
    script_dimension_scores: str = ""
    review_dimension_scores: str = ""
    store: str = ""
    review_text: str = ""
    publishers: str = ""
    metadata: str = ""
    tags: str = ""
    source_scroll: int = 0


def make_driver() -> webdriver.Remote:
    options = XCUITestOptions()
    for key, value in DEFAULT_CAPS.items():
        options.set_capability(key, value)
    client_config = AppiumClientConfig(
        remote_server_addr=APPIUM_URL,
        timeout=APPIUM_COMMAND_TIMEOUT_SECONDS,
    )
    try:
        return webdriver.Remote(APPIUM_URL, options=options, client_config=client_config)
    except (WebDriverException, MaxRetryError, ProtocolError, ReadTimeoutError, TimeoutError) as exc:
        message = str(exc)
        if "could not be, unlocked" in message or "BSErrorCodeDescription=Locked" in message:
            raise RuntimeError(
                "The iPhone is locked, so Appium cannot launch 谜圈. "
                "Unlock the phone, keep it on the home screen or in the app, "
                "and rerun this command."
            ) from exc
        if "Connection refused" in message or "Failed to establish a new connection" in message:
            raise RuntimeError(
                f"Could not connect to Appium at {APPIUM_URL}. Start Appium Server, then rerun."
            ) from exc
        if "Remote end closed connection" in message or "Connection aborted" in message:
            raise RuntimeError(
                "Appium closed the session request before WebDriverAgent was ready. "
                "Stop Appium, unlock and reconnect the iPhone, restart Appium, then rerun. "
                "Existing rows in miquan_ratings.csv will be skipped."
            ) from exc
        if "Read timed out" in message or "timed out" in message:
            raise RuntimeError(
                "Timed out while creating a new Appium session. Restart Appium Server, "
                "unlock the iPhone, keep 谜圈 open or on the home screen, then rerun. "
                "Existing rows in miquan_ratings.csv will be skipped."
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
                "Appium could not start WebDriverAgent on the iPhone. Stop Appium, "
                "unlock and reconnect the iPhone, remove any stuck WebDriverAgentRunner "
                "from the phone if needed, then start Appium again. Existing rows in "
                "miquan_ratings.csv will be skipped on rerun.\n"
                f"Last Appium error excerpt: {excerpt}"
            ) from exc
        raise


def wait_for(driver: webdriver.Remote, by: str, selector: str, timeout: int = 10):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, selector)))


def is_app_not_running_error(exc: BaseException) -> bool:
    message = str(exc)
    return APP_BUNDLE_ID in message and "not running" in message


def is_page_source_error(exc: BaseException) -> bool:
    message = str(exc)
    return (
        "Cannot get 'xml' source" in message
        or "mobileGetSource" in message
        or "failed to convert to UTF8" in message
        or "Read timed out" in message
        or "Max retries exceeded" in message
    )


def relaunch_app(driver: webdriver.Remote) -> bool:
    try:
        driver.activate_app(APP_BUNDLE_ID)
    except (AttributeError, WebDriverException):
        try:
            driver.execute_script("mobile: launchApp", {"bundleId": APP_BUNDLE_ID})
        except WebDriverException:
            return False
    time.sleep(3.0)
    return True


def all_texts(root: ET.Element) -> list[str]:
    texts: list[str] = []
    for el in root.iter():
        if el.attrib.get("type") not in TEXT_TYPES:
            continue
        value = el.attrib.get("value") or el.attrib.get("label") or el.attrib.get("name")
        if value:
            texts.append(clean_text(value))
    return texts


def text_entries(root: ET.Element) -> list[dict]:
    entries: list[dict] = []
    for el in root.iter():
        if el.attrib.get("type") not in TEXT_TYPES:
            continue
        value = el.attrib.get("value") or el.attrib.get("label") or el.attrib.get("name")
        if not value:
            continue
        entries.append(
            {
                "text": clean_text(value),
                "x": int(float(el.attrib.get("x", "0"))),
                "y": int(float(el.attrib.get("y", "0"))),
                "width": int(float(el.attrib.get("width", "0"))),
                "height": int(float(el.attrib.get("height", "0"))),
                "visible": el.attrib.get("visible") == "true",
            }
        )
    return entries


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalize_title(text: str) -> str:
    return re.sub(r"\s+", "", clean_text(text))


def is_exact_title_match(candidate: str, query: str) -> bool:
    return normalize_title(candidate) == normalize_title(query)


def parse_xml(source: str) -> ET.Element:
    return ET.fromstring(source)


def tap_if_present(driver: webdriver.Remote, xpath: str, timeout: float = 2.0) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath))).click()
        return True
    except (NoSuchElementException, WebDriverException):
        return False


def tap_text_matching(
    driver: webdriver.Remote,
    text: str,
    min_x: int | None = None,
    min_y: int | None = None,
    max_y: int | None = None,
) -> bool:
    elements = driver.find_elements(
        By.XPATH,
        f"//*[@name={xpath_literal(text)} or @label={xpath_literal(text)} or @value={xpath_literal(text)}]",
    )
    candidates = []
    for element in elements:
        rect = element.rect
        if min_x is not None and rect.get("x", 0) < min_x:
            continue
        if min_y is not None and rect.get("y", 0) < min_y:
            continue
        if max_y is not None and rect.get("y", 0) > max_y:
            continue
        if rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
            continue
        candidates.append((rect.get("y", 9999), rect, element))

    if not candidates:
        return False
    _, rect, element = sorted(candidates, key=lambda item: item[0])[0]
    try:
        element.click()
    except WebDriverException:
        tap_rect_center(driver, rect)
    return True


def window_rect(driver: webdriver.Remote) -> dict:
    try:
        return driver.get_window_rect()
    except WebDriverException:
        return {"x": 0, "y": 0, "width": 430, "height": 932}


def relative_point(driver: webdriver.Remote, x_ratio: float, y_ratio: float) -> dict[str, int]:
    rect = window_rect(driver)
    return {
        "x": int(rect.get("x", 0) + rect.get("width", 430) * x_ratio),
        "y": int(rect.get("y", 0) + rect.get("height", 932) * y_ratio),
    }


def go_to_search(driver: webdriver.Remote) -> None:
    """Open the search screen from the home page, or keep using it if already open."""
    if driver.find_elements(By.XPATH, "//XCUIElementTypeTextField"):
        return

    for _ in range(4):
        if driver.find_elements(By.XPATH, "//XCUIElementTypeTextField"):
            return
        if tap_if_present(driver, "//XCUIElementTypeButton[@name='sys back black' or @label='sys back black']", timeout=1):
            time.sleep(1.0)
            continue
        if tap_if_present(
            driver,
            "//XCUIElementTypeButton[@name='spt level back white' or @label='spt level back white']",
            timeout=1,
        ):
            time.sleep(1.0)
            continue
        if tap_if_present(driver, "//XCUIElementTypeButton[@name='返回' or @label='返回']", timeout=1):
            time.sleep(1.0)
            continue
        break

    candidates = [
        "//XCUIElementTypeButton[contains(@name, '搜索')]",
        "//XCUIElementTypeButton[contains(@label, '搜索')]",
        "//XCUIElementTypeButton[contains(@name, '天命难违')]",
        "//XCUIElementTypeButton[contains(@label, '天命难违')]",
    ]
    for xpath in candidates:
        if tap_if_present(driver, xpath):
            wait_for(driver, By.XPATH, "//XCUIElementTypeTextField", timeout=8)
            return

    # Last resort: the search control was observed near the top center of the app.
    driver.execute_script("mobile: tap", relative_point(driver, 0.49, 0.095))
    wait_for(driver, By.XPATH, "//XCUIElementTypeTextField", timeout=8)


def has_search_field(driver: webdriver.Remote) -> bool:
    try:
        return bool(driver.find_elements(By.XPATH, "//XCUIElementTypeTextField"))
    except WebDriverException:
        return False


def return_to_search_results(driver: webdriver.Remote, debug_dir: Path | None, query: str) -> bool:
    """Return from a detail/profile page to the active search-results screen."""
    for _ in range(6):
        if has_search_field(driver):
            return True
        if tap_if_present(driver, "//XCUIElementTypeButton[@name='sys back black' or @label='sys back black']", timeout=1):
            time.sleep(1.0)
            continue
        if tap_if_present(
            driver,
            "//XCUIElementTypeButton[@name='spt level back white' or @label='spt level back white']",
            timeout=1,
        ):
            time.sleep(1.0)
            continue
        if tap_if_present(driver, "//XCUIElementTypeButton[@name='返回' or @label='返回']", timeout=1):
            time.sleep(1.0)
            continue
        # The back arrow is frequently an unlabeled icon at the top-left.
        try:
            driver.execute_script("mobile: tap", relative_point(driver, 0.065, 0.088))
        except WebDriverException as exc:
            if is_app_not_running_error(exc):
                return False
            raise
        time.sleep(1.0)

    dump_debug_snapshot(driver, debug_dir, f"return_to_search_failed_{query}")
    return has_search_field(driver)


def submit_search(driver: webdriver.Remote, query: str) -> None:
    field = wait_for(driver, By.XPATH, "//XCUIElementTypeTextField", timeout=10)
    field.click()
    try:
        field.clear()
    except WebDriverException:
        pass
    field.send_keys(query + "\n")
    time.sleep(0.5)
    if not tap_search_tab(driver, "剧本"):
        try:
            driver.execute_script("mobile: tap", relative_point(driver, 0.90, 0.94))
        except WebDriverException:
            pass
    time.sleep(1.5)

    # Narrow to scripts. In this app the top tab may be exposed as text, not a button.
    tap_search_tab(driver, "剧本")
    time.sleep(1.0)


def tap_search_tab(driver: webdriver.Remote, tab_name: str) -> bool:
    rect = window_rect(driver)
    tab_min_y = rect.get("height", 932) * 0.10
    tab_max_y = rect.get("height", 932) * 0.22
    candidates = driver.find_elements(
        By.XPATH,
        f"//*[@name={xpath_literal(tab_name)} or @label={xpath_literal(tab_name)} or @value={xpath_literal(tab_name)}]",
    )
    # Prefer the tab row near the top of the search page, not section headings lower down.
    positioned_candidates = []
    for element in candidates:
        try:
            positioned_candidates.append((element.rect.get("y", 9999), element))
        except WebDriverException:
            continue
    for _, element in sorted(positioned_candidates, key=lambda item: item[0]):
        rect = element.rect
        if tab_min_y <= rect.get("y", 0) <= tab_max_y:
            tap_rect_center(driver, rect)
            return True
    return tap_if_present(driver, f"//XCUIElementTypeButton[@name={xpath_literal(tab_name)}]", timeout=1)


def tap_rect_center(driver: webdriver.Remote, rect: dict) -> None:
    driver.execute_script(
        "mobile: tap",
        {
            "x": int(rect["x"] + rect["width"] / 2),
            "y": int(rect["y"] + rect["height"] / 2),
        },
    )


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("_")[:80] or "snapshot"


def dump_debug_snapshot(driver: webdriver.Remote, debug_dir: Path | None, name: str) -> None:
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    source_path = debug_dir / f"{safe_name(name)}.xml"
    try:
        source_path.write_text(get_page_source(driver), encoding="utf-8")
    except Exception as exc:
        source_path.write_text(f"Could not get page source: {exc}", encoding="utf-8")
    try:
        screenshot_path = debug_dir / f"{safe_name(name)}.png"
        driver.save_screenshot(str(screenshot_path))
    except WebDriverException:
        pass


def get_page_source(driver: webdriver.Remote, attempts: int = 3, delay: float = 1.0) -> str:
    last_error: WebDriverException | None = None
    for attempt in range(attempts):
        try:
            return driver.page_source
        except WebDriverException as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last_error or WebDriverException("Could not get page source")


def open_first_script_result(driver: webdriver.Remote, query: str, debug_dir: Path | None = None) -> bool:
    if tap_first_script_result_cell(driver, query):
        time.sleep(2.0)
        return True

    dump_debug_snapshot(driver, debug_dir, f"open_result_failed_{query}")
    try:
        source = get_page_source(driver)
        visible_text = "; ".join(all_texts(parse_xml(source))[:30])
    except WebDriverException:
        visible_text = "source unavailable"
    print(f"  no exact script title found for {query}. visible text: {visible_text[:500]}")
    return False


def tap_first_script_result_cell(driver: webdriver.Remote, query: str) -> bool:
    bounds = get_search_section_bounds(driver)
    cells = driver.find_elements(By.XPATH, "//XCUIElementTypeCell")
    candidates = []
    for cell in cells:
        try:
            rect = cell.rect
            y = rect.get("y", 0)
            if not (bounds["script_top"] <= y <= bounds["script_bottom"]):
                continue
            texts = []
            for child in cell.find_elements(By.XPATH, ".//XCUIElementTypeStaticText"):
                value = (
                    child.get_attribute("value")
                    or child.get_attribute("label")
                    or child.get_attribute("name")
                    or ""
                )
                if value:
                    texts.append(value)
        except WebDriverException:
            continue
        if not any(is_exact_title_match(text, query) for text in texts):
            continue
        candidates.append((y, rect))

    if not candidates:
        return tap_first_visible_result_title(driver, query)

    _, rect = sorted(candidates, key=lambda item: item[0])[0]
    # Tap left/middle of the script card, away from the same-name user result lower down.
    driver.execute_script(
        "mobile: tap",
        {
            "x": int(rect["x"] + rect["width"] * 0.45),
            "y": int(rect["y"] + rect["height"] * 0.5),
        },
    )
    return True


def tap_first_visible_result_title(driver: webdriver.Remote, query: str) -> bool:
    section_bounds = get_search_section_bounds(driver)
    elements = driver.find_elements(
        By.XPATH,
        f"//XCUIElementTypeStaticText[@name={xpath_literal(query)} "
        f"or @label={xpath_literal(query)} "
        f"or @value={xpath_literal(query)}]",
    )
    if not elements:
        return False

    # Search result title is below the top tab row and above the next section
    # heading ("用户"/"店铺"/"发行"). This avoids tapping a matching user name.
    visible_candidates = []
    for element in elements:
        try:
            rect = element.rect
            y = rect.get("y", 0)
        except WebDriverException:
            continue
        if (
            section_bounds["script_top"] <= y <= section_bounds["script_bottom"]
            and rect.get("width", 0) > 0
            and rect.get("height", 0) > 0
        ):
            visible_candidates.append((y, rect, element))
    if not visible_candidates and section_bounds["script_card"]:
        tap_rect_center(driver, section_bounds["script_card"])
        return True
    if not visible_candidates:
        return False

    _, rect, element = sorted(visible_candidates, key=lambda item: item[0])[0]
    try:
        element.click()
    except WebDriverException:
        tap_rect_center(driver, rect)
    return True


def get_search_section_bounds(driver: webdriver.Remote) -> dict:
    screen = window_rect(driver)
    width = screen.get("width", 430)
    height = screen.get("height", 932)
    bounds = {
        "script_top": int(height * 0.225),
        "script_bottom": int(height * 0.42),
        "script_card": None,
    }
    section_heading_min_y = height * 0.19
    headings = driver.find_elements(
        By.XPATH,
        "//*[@name='剧本' or @label='剧本' or @value='剧本' "
        "or @name='用户' or @label='用户' or @value='用户' "
        "or @name='店铺' or @label='店铺' or @value='店铺' "
        "or @name='发行' or @label='发行' or @value='发行']",
    )
    script_heading_y = None
    next_heading_y = None
    for heading in headings:
        try:
            label = heading.get_attribute("name") or heading.get_attribute("label") or heading.get_attribute("value")
            rect = heading.rect
            y = rect.get("y", 0)
        except WebDriverException:
            continue
        # Ignore the top tab row; keep only actual result-section headings.
        if label == "剧本" and y >= section_heading_min_y:
            script_heading_y = y if script_heading_y is None else min(script_heading_y, y)
        if label in {"用户", "店铺", "发行"} and y >= section_heading_min_y:
            next_heading_y = y if next_heading_y is None else min(next_heading_y, y)

    if script_heading_y is not None:
        bounds["script_top"] = script_heading_y + 35
    if next_heading_y is not None:
        bounds["script_bottom"] = next_heading_y - 10
    bounds["script_card"] = {
        "x": int(width * 0.06),
        "y": bounds["script_top"] + 20,
        "width": int(width * 0.88),
        "height": max(80, bounds["script_bottom"] - bounds["script_top"] - 30),
    }
    return bounds


def xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


def parse_summary(source: str, query: str) -> ScriptSummary:
    root = parse_xml(source)
    return parse_summary_entries(text_entries(root), query=query)


def read_rating_summary(driver: webdriver.Remote, query: str) -> ScriptSummary:
    return parse_summary(get_page_source(driver), query=query)


def parse_summary_entries(entries: list[dict], query: str) -> ScriptSummary:
    texts = [entry["text"] for entry in entries]
    summary = ScriptSummary(query=query)
    title_candidates = [
        entry
        for entry in entries
        if entry["visible"]
        and 80 <= entry["y"] <= 190
        and entry["x"] >= 80
        and looks_like_script_title(entry["text"])
    ]
    if title_candidates:
        summary.script_title = sorted(title_candidates, key=lambda entry: (entry["y"], entry["x"]))[0]["text"]

    for text in texts:
        if not summary.script_title and looks_like_script_title(text):
            summary.script_title = text
        if is_rating_score(text) and not summary.overall_rating:
            # On the detail page this is usually 8.6 near the rating block.
            summary.overall_rating = text
        if "男" in text and "女" in text and "小时" in text:
            summary.metadata = text
        if looks_like_tag_line(text) and not summary.tags:
            summary.tags = text
        if text.startswith("发行："):
            summary.publishers = join_unique(summary.publishers, text)
        if "人想玩" in text:
            summary.want_count = text
        if "人玩过" in text:
            summary.played_count = text
        if "人点评" in text:
            summary.review_count = text

    summary_scope_end = next((i for i, entry in enumerate(entries) if entry["text"] == "剧情简介"), min(len(entries), 120))
    summary_entries = entries[: summary_scope_end + 1]
    overall_candidates = [
        entry["text"]
        for entry in summary_entries
        if is_rating_score(entry["text"]) and entry["x"] <= 140 and 260 <= entry["y"] <= 350
    ]
    if overall_candidates:
        summary.overall_rating = overall_candidates[0]
    score_pairs = extract_score_pairs_from_entries(summary_entries)
    summary.dimension_scores = format_score_pairs(score_pairs)
    summary.plot_score = score_for_label(score_pairs, "剧情")
    summary.restore_score = score_for_label(score_pairs, "还原") or score_for_label(score_pairs, "情感")
    summary.gameplay_score = score_for_label(score_pairs, "玩法") or score_for_label(score_pairs, "机制")

    return summary


def looks_like_script_title(text: str) -> bool:
    if text in {
        "谜圈评分",
        "维度评分",
        "剧情",
        "还原",
        "玩法",
        "想玩",
        "玩过",
        "剧情简介",
        "剧本角色",
        "创作者说",
    }:
        return False
    if text.startswith(("TOP ", "发行：")):
        return False
    if any(marker in text for marker in ("人想玩", "人玩过", "人点评", "小时", "发行")):
        return False
    return len(text) >= 2 and not re.fullmatch(r"\d+(?:\.\d+)?", text)


def looks_like_tag_line(text: str) -> bool:
    if any(marker in text for marker in ("人想玩", "人玩过", "人点评", "小时", "发行", "发布")):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return False
    known_tags = {"日式", "古风", "机制", "沉浸", "武侠", "情感", "推理", "硬核", "欢乐", "恐怖", "新手"}
    parts = [part for part in re.split(r"\s+", text) if part]
    return 2 <= len(parts) <= 8 and sum(1 for part in parts if part in known_tags) >= 2


def next_score_after_label(texts: list[str], label: str) -> str:
    for index, text in enumerate(texts):
        if text != label:
            continue
        for later in texts[index + 1 : index + 5]:
            if re.fullmatch(r"\d+(?:\.\d+)?", later):
                return later
    return ""


def is_score_text(text: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", text))


def is_rating_score(text: str) -> bool:
    if not is_score_text(text):
        return False
    return 0 <= float(text) <= 10


def extract_score_pairs(texts: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, text in enumerate(texts):
        if text not in DIMENSION_LABELS:
            continue

        score = ""
        # Review cells often expose score before label: "8 沉浸".
        if index > 0 and is_score_text(texts[index - 1]):
            score = texts[index - 1]
        # Summary blocks often expose label before score: "剧情 ... 7.1".
        if not score:
            for later in texts[index + 1 : index + 5]:
                if is_score_text(later):
                    score = later
                    break
        # Some review cells expose the label after its score with unrelated
        # sentiment text between them.
        if not score:
            for earlier in reversed(texts[max(0, index - 5) : index]):
                if is_score_text(earlier):
                    score = earlier
                    break

        if score and (text, score) not in seen:
            pairs.append((text, score))
            seen.add((text, score))
    return pairs


def format_score_pairs(pairs: list[tuple[str, str]]) -> str:
    return "; ".join(f"{label}:{score}" for label, score in pairs)


def extract_score_pairs_from_entries(entries: list[dict]) -> list[tuple[str, str]]:
    if not any(entry["x"] or entry["y"] for entry in entries):
        return extract_score_pairs([entry["text"] for entry in entries])

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    scores = [entry for entry in entries if is_score_text(entry["text"])]
    for label_entry in entries:
        label = label_entry["text"]
        if label not in DIMENSION_LABELS:
            continue

        same_row_scores = [
            score
            for score in scores
            if abs(score["y"] - label_entry["y"]) <= 8 and score["x"] > label_entry["x"]
        ]
        if same_row_scores:
            score = sorted(same_row_scores, key=lambda item: item["x"] - label_entry["x"])[0]["text"]
        else:
            nearby_scores = [
                score
                for score in scores
                if abs(score["y"] - label_entry["y"]) <= 8
                and abs(score["x"] - label_entry["x"]) <= 60
            ]
            score = sorted(nearby_scores, key=lambda item: abs(item["x"] - label_entry["x"]))[0]["text"] if nearby_scores else ""

        if score and (label, score) not in seen:
            pairs.append((label, score))
            seen.add((label, score))

    return pairs or extract_score_pairs([entry["text"] for entry in entries])


def score_for_label(pairs: list[tuple[str, str]], *labels: str) -> str:
    label_set = set(labels)
    return next((score for label, score in pairs if label in label_set), "")


def join_unique(existing: str, value: str) -> str:
    values = [part for part in existing.split("; ") if part] if existing else []
    if value not in values:
        values.append(value)
    return "; ".join(values)


def scroll_down(driver: webdriver.Remote) -> None:
    try:
        driver.execute_script("mobile: scroll", {"direction": "down"})
    except WebDriverException:
        start = relative_point(driver, 0.5, 0.84)
        end = relative_point(driver, 0.5, 0.28)
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {
                "duration": 0.5,
                "fromX": start["x"],
                "fromY": start["y"],
                "toX": end["x"],
                "toY": end["y"],
            },
        )
    time.sleep(1.0)


def scroll_until_reviews(driver: webdriver.Remote, max_scrolls: int = 8) -> None:
    for _ in range(max_scrolls):
        source = get_page_source(driver)
        if "用户评价" in source:
            return
        scroll_down(driver)
    raise RuntimeError("Could not find the 用户评价 section after scrolling.")


def is_all_reviews_page(source: str) -> bool:
    return "全部剧评" in source


def open_all_reviews_page(driver: webdriver.Remote, debug_dir: Path | None, query: str) -> bool:
    """Open the dedicated full-review list for the current script if possible."""
    if is_all_reviews_page(get_page_source(driver)):
        return True

    if tap_if_present(
        driver,
        "//XCUIElementTypeStaticText[contains(@name, '人点评') or contains(@label, '人点评') or contains(@value, '人点评')]",
        timeout=1,
    ):
        time.sleep(1.5)
        if is_all_reviews_page(get_page_source(driver)):
            return True

    try:
        scroll_until_reviews(driver)
    except RuntimeError:
        pass
    if tap_text_matching(driver, "全部", min_x=300, min_y=240):
        time.sleep(1.5)
        if is_all_reviews_page(get_page_source(driver)):
            return True

    dump_debug_snapshot(driver, debug_dir, f"open_all_reviews_failed_{query}")
    return is_all_reviews_page(get_page_source(driver))


def review_cells(root: ET.Element) -> Iterable[ET.Element]:
    for cell in root.iter("XCUIElementTypeCell"):
        texts = all_texts(cell)
        if not texts:
            continue
        joined = " ".join(texts)
        if any(stop in joined for stop in SECTION_STOP_WORDS):
            continue
        if "发布" not in joined:
            continue
        if not any(re.search(r"\d{4}-\d{2}-\d{2}.*发布", text) for text in texts):
            continue
        if not any(sentiment in texts for sentiment in SENTIMENTS):
            continue
        if not any(is_review_text_candidate(text) for text in texts):
            continue
        yield cell


def is_visible_review_cell(cell: ET.Element) -> bool:
    return any(
        entry["visible"]
        and is_review_text_candidate(entry["text"])
        and 205 <= entry["y"] <= 820
        and entry["width"] > 0
        and entry["height"] > 0
        for entry in text_entries(cell)
    )


def parse_review_cell(cell: ET.Element, summary: ScriptSummary, scroll_index: int) -> ReviewRow | None:
    texts = all_texts(cell)
    if len(texts) < 6:
        return None

    date_index = next((i for i, text in enumerate(texts) if re.search(r"\d{4}-\d{2}-\d{2}.*发布", text)), -1)
    if date_index < 0:
        return None

    score_pairs = extract_score_pairs_from_entries(text_entries(cell))

    row = ReviewRow(
        query=summary.query,
        script_title=summary.script_title,
        overall_rating=summary.overall_rating,
        plot_score=summary.plot_score,
        restore_score=summary.restore_score,
        gameplay_score=summary.gameplay_score,
        want_count=summary.want_count,
        played_count=summary.played_count,
        review_count=summary.review_count,
        reviewer=choose_reviewer(texts, date_index),
        review_date=texts[date_index],
        sentiment=next((text for text in texts if text in SENTIMENTS), ""),
        review_plot_score=score_for_label(score_pairs, "剧情"),
        review_restore_score=score_for_label(score_pairs, "还原")
        or score_for_label(score_pairs, "情感")
        or score_for_label(score_pairs, "沉浸"),
        review_gameplay_score=score_for_label(score_pairs, "玩法")
        or score_for_label(score_pairs, "机制")
        or score_for_label(score_pairs, "演绎"),
        script_dimension_scores=summary.dimension_scores,
        review_dimension_scores=format_score_pairs(score_pairs),
        store=next(
            (text for text in texts if "市·" in text or "区·" in text or ("·" in text and len(text) <= 30)),
            "",
        ),
        publishers=summary.publishers,
        metadata=summary.metadata,
        tags=summary.tags,
        source_scroll=scroll_index,
    )

    row.review_text = choose_review_text(texts, row)
    if not row.review_text:
        return None
    return row


def preview_prefix(text: str) -> str:
    prefix = text.replace("...全文", "").replace("…全文", "").replace("全文", "").strip()
    return prefix[: min(len(prefix), 30)]


def visible_review_text_entry(cell: ET.Element, preview_text: str) -> dict | None:
    candidates = []
    for entry in text_entries(cell):
        text = entry["text"]
        if not entry["visible"] or not is_review_text_candidate(text):
            continue
        if not (205 <= entry["y"] <= 820) or entry["width"] <= 0 or entry["height"] <= 0:
            continue
        if text == preview_text or preview_prefix(preview_text) in text or preview_prefix(text) in preview_text:
            candidates.append(entry)
    if candidates:
        return sorted(candidates, key=lambda item: len(item["text"]), reverse=True)[0]
    return None


def expand_review_text(driver: webdriver.Remote, cell: ET.Element, row: ReviewRow) -> str:
    if "全文" not in row.review_text:
        return row.review_text
    entry = visible_review_text_entry(cell, row.review_text)
    if not entry:
        return row.review_text

    driver.execute_script(
        "mobile: tap",
        {
            "x": min(415, max(20, entry["x"] + entry["width"] - 18)),
            "y": min(820, max(210, entry["y"] + entry["height"] - 12)),
        },
    )
    time.sleep(1.2)
    source = get_page_source(driver)
    expanded = choose_expanded_review_text(source, row)
    if expanded and len(expanded) > len(row.review_text):
        row.review_text = expanded

    if not is_all_reviews_page(source):
        tap_if_present(driver, "//XCUIElementTypeButton[@name='sys back black' or @label='sys back black']", timeout=2)
        time.sleep(1.0)
    return row.review_text


def choose_expanded_review_text(source: str, row: ReviewRow) -> str:
    texts = all_texts(parse_xml(source))
    prefix = preview_prefix(row.review_text)
    blocked = {
        row.reviewer,
        row.review_date,
        row.sentiment,
        row.store,
        *DIMENSION_LABELS,
        row.review_plot_score,
        row.review_restore_score,
        row.review_gameplay_score,
        "全部剧评",
        "综合",
        "近期",
        "只看我关注的人",
    }
    candidates = []
    for text in texts:
        if text in blocked or not is_review_text_candidate(text):
            continue
        if prefix and prefix not in text:
            continue
        candidates.append(text.replace("...全文", "").replace("…全文", "").strip())
    return max(candidates, key=len, default="")


def choose_reviewer(texts: list[str], date_index: int) -> str:
    if date_index > 0 and is_possible_reviewer(texts[date_index - 1]):
        return texts[date_index - 1]
    for text in texts[max(0, date_index - 3) : min(len(texts), date_index + 12)]:
        if is_possible_reviewer(text):
            return text
    return ""


def is_possible_reviewer(text: str) -> bool:
    if (
        not text
        or text in SENTIMENTS
        or text in DIMENSION_LABELS
        or is_score_text(text)
        or "发布" in text
        or "·" in text
        or text.startswith("sys ")
        or text.startswith("  ")
        or is_review_text_candidate(text)
    ):
        return False
    return len(text) <= 30


def is_review_text_candidate(text: str) -> bool:
    if not text or text in SENTIMENTS or text in DIMENSION_LABELS or is_score_text(text):
        return False
    if text.startswith("sys ") or text.startswith("  分享") or text in SECTION_STOP_WORDS:
        return False
    if re.search(r"\d{4}-\d{2}-\d{2}.*发布", text):
        return False
    if "全文" in text or "\n" in text:
        return True
    return len(text) >= 14 and not ("·" in text and len(text) <= 30)


def choose_review_text(texts: list[str], row: ReviewRow) -> str:
    blocked = {
        row.reviewer,
        row.review_date,
        row.sentiment,
        row.store,
        *DIMENSION_LABELS,
        row.review_plot_score,
        row.review_restore_score,
        row.review_gameplay_score,
    }
    candidates = []
    for text in texts:
        if text in blocked:
            continue
        if not is_review_text_candidate(text):
            continue
        if row.review_dimension_scores and any(
            text == score for _, score in extract_score_pairs(texts)
        ):
            continue
        if "全文" in text or len(text) >= 14:
            candidates.append(text)
    return max(candidates, key=len, default="")


def collect_reviews(
    driver: webdriver.Remote,
    summary: ScriptSummary,
    max_scrolls: int,
    target_count: int,
    debug_dir: Path | None,
) -> list[ReviewRow]:
    rows: list[ReviewRow] = []
    seen: set[tuple[str, str, str]] = set()

    if not open_all_reviews_page(driver, debug_dir, summary.query):
        scroll_until_reviews(driver)

    for scroll_index in range(max_scrolls):
        source = get_page_source(driver)
        root = parse_xml(source)
        for cell in review_cells(root):
            if is_all_reviews_page(source) and not is_visible_review_cell(cell):
                continue
            row = parse_review_cell(cell, summary, scroll_index)
            if not row:
                continue
            expand_review_text(driver, cell, row)
            key = (row.reviewer, row.review_date, row.review_text[:80])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            if len(rows) >= target_count:
                return rows
        scroll_down(driver)

    return rows


def read_queries(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [row["query"].strip() for row in reader if row.get("query", "").strip()]


def read_existing_ratings(path: Path) -> list[ScriptSummary]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [rating_row_to_summary(row) for row in reader if row.get("query", "").strip()]


def debug_failed_query_keys(debug_dir: Path | None) -> set[str]:
    if debug_dir is None or not debug_dir.exists():
        return set()
    prefixes = ("open_result_failed_", "title_mismatch_", "error_")
    keys: set[str] = set()
    for path in debug_dir.iterdir():
        if not path.is_file():
            continue
        stem = path.stem
        for prefix in prefixes:
            if stem.startswith(prefix):
                keys.add(stem[len(prefix) :])
                break
    return keys


def rating_row_to_summary(row: dict[str, str]) -> ScriptSummary:
    score_pairs = [
        (label, row.get(label, "").strip())
        for label in DIMENSION_COLUMNS
        if row.get(label, "").strip() not in {"", "0", "0.0"}
    ]
    return ScriptSummary(
        query=row.get("query", "").strip(),
        script_title=row.get("script_title", "").strip(),
        overall_rating=row.get("overall_rating", "").strip(),
        plot_score=row.get("剧情", "").strip(),
        restore_score=(row.get("还原", "").strip() or row.get("情感", "").strip()),
        gameplay_score=(row.get("玩法", "").strip() or row.get("机制", "").strip()),
        dimension_scores=format_score_pairs(score_pairs),
        want_count=row.get("want_count", "").strip(),
        played_count=row.get("played_count", "").strip(),
        review_count=row.get("review_count", "").strip(),
        publishers=row.get("publishers", "").strip(),
        metadata=row.get("metadata", "").strip(),
        tags=row.get("tags", "").strip(),
    )


def parse_dimension_scores(value: str) -> dict[str, str]:
    scores: dict[str, str] = {}
    for part in value.split(";"):
        if ":" not in part:
            continue
        label, score = part.split(":", 1)
        label = label.strip()
        score = score.strip()
        if label in DIMENSION_LABELS and is_rating_score(score):
            scores[label] = score
    return scores


def numeric_count(value: str) -> str:
    match = re.search(r"[\d.]+", value or "")
    if not match:
        return "0"
    number = match.group(0)
    return number[:-2] if number.endswith(".0") else number


def clean_publishers(value: str) -> str:
    publishers = []
    for part in (value or "").split(";"):
        publisher = re.sub(r"^发行[:：]\s*", "", part.strip())
        if publisher:
            publishers.append(publisher)
    return "; ".join(publishers)


def script_summary_dict(row: ScriptSummary) -> dict[str, str]:
    dimension_scores = parse_dimension_scores(row.dimension_scores)
    values = {
        "query": row.query,
        "script_title": row.script_title,
        "overall_rating": row.overall_rating if is_rating_score(row.overall_rating) else "0",
        "want_count": numeric_count(row.want_count),
        "played_count": numeric_count(row.played_count),
        "review_count": numeric_count(row.review_count),
        "publishers": clean_publishers(row.publishers),
        "metadata": row.metadata,
        "tags": row.tags,
    }
    for label in DIMENSION_COLUMNS:
        values[label] = dimension_scores.get(label, "0")
    return values


def write_rows(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_class = rows[0].__class__ if rows else ReviewRow
    fieldnames = RATING_BASE_FIELDS if sample_class is ScriptSummary else [field.name for field in fields(sample_class)]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(script_summary_dict(row) if isinstance(row, ScriptSummary) else row.__dict__)


def flush_rows(path: Path, rows: list, row_class: type = ReviewRow) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    if rows:
        write_rows(tmp_path, rows)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = RATING_BASE_FIELDS if row_class is ScriptSummary else [field.name for field in fields(row_class)]
        with tmp_path.open("w", newline="", encoding="utf-8-sig") as handle:
            csv.DictWriter(handle, fieldnames=fieldnames).writeheader()
    os.replace(tmp_path, path)


def scrape(
    input_path: Path,
    reviews_output_path: Path,
    ratings_output_path: Path,
    mode: str,
    max_scrolls: int,
    reviews_per_script: int,
    limit: int | None,
    debug_dir: Path | None,
    rerun_existing: bool,
) -> None:
    queries = read_queries(input_path)
    if limit is not None:
        queries = queries[:limit]
    all_ratings: list[ScriptSummary] = []
    if mode == "ratings" and not rerun_existing:
        all_ratings = read_existing_ratings(ratings_output_path)
        completed_queries = {row.query for row in all_ratings if row.query}
        if completed_queries:
            original_count = len(queries)
            queries = [query for query in queries if query not in completed_queries]
            print(
                f"Loaded {len(completed_queries)} existing rating rows from {ratings_output_path}; "
                f"skipping {original_count - len(queries)} completed queries."
            )
        failed_debug_keys = debug_failed_query_keys(debug_dir)
        if failed_debug_keys:
            original_count = len(queries)
            queries = [query for query in queries if safe_name(query) not in failed_debug_keys]
            skipped_count = original_count - len(queries)
            if skipped_count:
                print(
                    f"Found {len(failed_debug_keys)} prior failed debug snapshots in {debug_dir}; "
                    f"skipping {skipped_count} debugged queries."
                )
    if not queries:
        flush_rows(ratings_output_path, all_ratings, ScriptSummary)
        print(f"No pending rating queries. Ratings are saved in {ratings_output_path}")
        return
    driver = make_driver()
    all_reviews: list[ReviewRow] = []
    try:
        try:
            for query in queries:
                print(f"Searching: {query}")
                try:
                    go_to_search(driver)
                    submit_search(driver, query)
                    if not open_first_script_result(driver, query, debug_dir=debug_dir):
                        all_ratings.append(ScriptSummary(query=query))
                        if mode in {"reviews", "both"}:
                            flush_rows(reviews_output_path, all_reviews, ReviewRow)
                        flush_rows(ratings_output_path, all_ratings, ScriptSummary)
                        continue
                    summary = read_rating_summary(driver, query=query)
                    if not is_exact_title_match(summary.script_title, query):
                        print(
                            f"  skipped {query}: opened {summary.script_title or 'unknown title'}, "
                            "which is not an exact title match"
                        )
                        dump_debug_snapshot(driver, debug_dir, f"title_mismatch_{query}")
                        all_ratings.append(ScriptSummary(query=query))
                        if mode in {"reviews", "both"}:
                            flush_rows(reviews_output_path, all_reviews, ReviewRow)
                        flush_rows(ratings_output_path, all_ratings, ScriptSummary)
                        continue
                    all_ratings.append(summary)
                    rows: list[ReviewRow] = []
                    if mode in {"reviews", "both"}:
                        rows = collect_reviews(
                            driver,
                            summary,
                            max_scrolls=max_scrolls,
                            target_count=reviews_per_script,
                            debug_dir=debug_dir,
                        )
                        if not rows:
                            dump_debug_snapshot(driver, debug_dir, f"no_reviews_{query}")
                        print(f"  collected {len(rows)} review rows")
                        all_reviews.extend(rows)
                        flush_rows(reviews_output_path, all_reviews, ReviewRow)
                    flush_rows(ratings_output_path, all_ratings, ScriptSummary)
                    if mode in {"reviews", "both"}:
                        print(
                            f"  checkpoint saved {len(all_ratings)} ratings to {ratings_output_path} "
                            f"and {len(all_reviews)} reviews to {reviews_output_path}"
                        )
                    else:
                        print(f"  checkpoint saved {len(all_ratings)} ratings to {ratings_output_path}")
                except Exception as exc:
                    dump_debug_snapshot(driver, debug_dir, f"error_{query}")
                    print(f"  skipped {query}: {exc}")
                    if is_app_not_running_error(exc) or is_page_source_error(exc):
                        print("  Appium could not read the current screen; relaunching 谜圈 before continuing.")
                        relaunch_app(driver)
                    if mode in {"reviews", "both"}:
                        flush_rows(reviews_output_path, all_reviews, ReviewRow)
                    flush_rows(ratings_output_path, all_ratings, ScriptSummary)
                finally:
                    try:
                        if not return_to_search_results(driver, debug_dir, query):
                            relaunch_app(driver)
                    except Exception as cleanup_exc:
                        print(f"  cleanup after {query} failed: {cleanup_exc}")
                        relaunch_app(driver)
        except KeyboardInterrupt:
            if mode in {"reviews", "both"}:
                flush_rows(reviews_output_path, all_reviews, ReviewRow)
            flush_rows(ratings_output_path, all_ratings, ScriptSummary)
            if mode in {"reviews", "both"}:
                print(
                    f"\nInterrupted. Saved {len(all_ratings)} ratings to {ratings_output_path} "
                    f"and {len(all_reviews)} reviews to {reviews_output_path}"
                )
            else:
                print(f"\nInterrupted. Saved {len(all_ratings)} ratings to {ratings_output_path}")
            raise
    finally:
        if mode in {"reviews", "both"}:
            flush_rows(reviews_output_path, all_reviews, ReviewRow)
        flush_rows(ratings_output_path, all_ratings, ScriptSummary)
        driver.quit()
    if mode in {"reviews", "both"}:
        print(
            f"Wrote {len(all_ratings)} ratings to {ratings_output_path} "
            f"and {len(all_reviews)} reviews to {reviews_output_path}"
        )
    else:
        print(f"Wrote {len(all_ratings)} ratings to {ratings_output_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=SCRIPT_DIR / "search_terms.csv")
    parser.add_argument("--output", type=Path, default=SCRIPT_DIR / "miquan_reviews.csv")
    parser.add_argument("--ratings-output", type=Path, default=SCRIPT_DIR / "miquan_ratings.csv")
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
        help="Ignore existing ratings rows and scrape every query again.",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=SCRIPT_DIR / "debug",
        help="Where to save source/screenshot snapshots for failed result opens.",
    )
    args = parser.parse_args()
    scrape(
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


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
