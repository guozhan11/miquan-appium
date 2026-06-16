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
from appium.options.ios import XCUITestOptions
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


APPIUM_URL = "http://127.0.0.1:4723"

DEFAULT_CAPS = {
    "platformName": "iOS",
    "appium:automationName": "XCUITest",
    "appium:deviceName": "iPhone 16 Plus",
    "appium:udid": "00008140-000975A43E41801C",
    "appium:bundleId": "com.juhaowan123.www",
    "appium:noReset": True,
    "appium:updatedWDABundleId": "com.guozhan.WebDriverAgentRunner",
    "appium:usePrebuiltWDA": True,
}


TEXT_TYPES = {"XCUIElementTypeStaticText", "XCUIElementTypeTextView"}
SENTIMENTS = {"推荐", "一般", "不行"}
DIMENSION_LABELS = {
    "剧情",
    "还原",
    "玩法",
    "情感",
    "机制",
    "沉浸",
    "演绎",
    "推理",
    "故事",
    "难度",
    "互动",
}
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
    try:
        return webdriver.Remote(APPIUM_URL, options=options)
    except WebDriverException as exc:
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
        raise


def wait_for(driver: webdriver.Remote, by: str, selector: str, timeout: int = 10):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, selector)))


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

    # Last resort: the search control was observed at the top of the app.
    driver.execute_script("mobile: tap", {"x": 210, "y": 88})
    wait_for(driver, By.XPATH, "//XCUIElementTypeTextField", timeout=8)


def has_search_field(driver: webdriver.Remote) -> bool:
    return bool(driver.find_elements(By.XPATH, "//XCUIElementTypeTextField"))


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
        driver.execute_script("mobile: tap", {"x": 28, "y": 82})
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
    time.sleep(1.5)

    # Narrow to scripts. In this app the top tab may be exposed as text, not a button.
    tap_search_tab(driver, "剧本")
    time.sleep(1.0)


def tap_search_tab(driver: webdriver.Remote, tab_name: str) -> bool:
    candidates = driver.find_elements(
        By.XPATH,
        f"//*[@name={xpath_literal(tab_name)} or @label={xpath_literal(tab_name)} or @value={xpath_literal(tab_name)}]",
    )
    # Prefer the tab row near the top of the search page, not section headings lower down.
    for element in sorted(candidates, key=lambda item: item.rect.get("y", 9999)):
        rect = element.rect
        if 95 <= rect.get("y", 0) <= 180:
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
    source_path.write_text(driver.page_source, encoding="utf-8")
    try:
        screenshot_path = debug_dir / f"{safe_name(name)}.png"
        driver.save_screenshot(str(screenshot_path))
    except WebDriverException:
        pass


def open_first_script_result(driver: webdriver.Remote, query: str, debug_dir: Path | None = None) -> bool:
    if tap_first_script_result_cell(driver, query):
        time.sleep(2.0)
        return True

    xpaths = [
        f"//XCUIElementTypeStaticText[contains(@name, {xpath_literal(query)})]/ancestor::XCUIElementTypeCell[1]",
        "//XCUIElementTypeStaticText[contains(@name, '分')]/ancestor::XCUIElementTypeCell[1]",
        "//XCUIElementTypeTable/XCUIElementTypeCell[1]",
        "//XCUIElementTypeTable//XCUIElementTypeCell[1]",
    ]
    for xpath in xpaths:
        if tap_if_present(driver, xpath, timeout=4):
            time.sleep(2.0)
            return True

    source = driver.page_source
    dump_debug_snapshot(driver, debug_dir, f"open_result_failed_{query}")
    visible_text = "; ".join(all_texts(parse_xml(source))[:30])
    print(f"  no tappable script result found for {query}. visible text: {visible_text[:500]}")
    return False


def tap_first_script_result_cell(driver: webdriver.Remote, query: str) -> bool:
    bounds = get_search_section_bounds(driver)
    cells = driver.find_elements(By.XPATH, "//XCUIElementTypeCell")
    candidates = []
    for cell in cells:
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
        joined = " ".join(texts)
        if query not in joined:
            continue
        if not re.search(r"\d+(?:\.\d+)?分", joined):
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
        f"//XCUIElementTypeStaticText[contains(@name, {xpath_literal(query)}) "
        f"or contains(@label, {xpath_literal(query)}) "
        f"or contains(@value, {xpath_literal(query)})]",
    )
    if not elements:
        return False

    # Search result title is below the top tab row and above the next section
    # heading ("用户"/"店铺"/"发行"). This avoids tapping a matching user name.
    visible_candidates = []
    for element in elements:
        rect = element.rect
        y = rect.get("y", 0)
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
    bounds = {
        "script_top": 210,
        "script_bottom": 390,
        "script_card": None,
    }
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
        label = heading.get_attribute("name") or heading.get_attribute("label") or heading.get_attribute("value")
        rect = heading.rect
        y = rect.get("y", 0)
        # y < 180 is the top tab row. y >= 180 is the actual result section heading.
        if label == "剧本" and y >= 180:
            script_heading_y = y if script_heading_y is None else min(script_heading_y, y)
        if label in {"用户", "店铺", "发行"} and y >= 180:
            next_heading_y = y if next_heading_y is None else min(next_heading_y, y)

    if script_heading_y is not None:
        bounds["script_top"] = script_heading_y + 35
    if next_heading_y is not None:
        bounds["script_bottom"] = next_heading_y - 10
    bounds["script_card"] = {
        "x": 25,
        "y": bounds["script_top"] + 20,
        "width": 390,
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
    texts = all_texts(parse_xml(source))
    summary = ScriptSummary(query=query)

    for text in texts:
        if not summary.script_title and looks_like_script_title(text):
            summary.script_title = text
        if re.fullmatch(r"\d+(?:\.\d+)?", text) and not summary.overall_rating:
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

    summary_scope_end = next((i for i, text in enumerate(texts) if text == "剧情简介"), min(len(texts), 120))
    score_pairs = extract_score_pairs(texts[: summary_scope_end + 1])
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
        driver.execute_script(
            "mobile: dragFromToForDuration",
            {"duration": 0.5, "fromX": 215, "fromY": 780, "toX": 215, "toY": 260},
        )
    time.sleep(1.0)


def scroll_until_reviews(driver: webdriver.Remote, max_scrolls: int = 8) -> None:
    for _ in range(max_scrolls):
        source = driver.page_source
        if "用户评价" in source:
            return
        scroll_down(driver)
    raise RuntimeError("Could not find the 用户评价 section after scrolling.")


def is_all_reviews_page(source: str) -> bool:
    return "全部剧评" in source


def open_all_reviews_page(driver: webdriver.Remote, debug_dir: Path | None, query: str) -> bool:
    """Open the dedicated full-review list for the current script if possible."""
    if is_all_reviews_page(driver.page_source):
        return True

    if tap_if_present(
        driver,
        "//XCUIElementTypeStaticText[contains(@name, '人点评') or contains(@label, '人点评') or contains(@value, '人点评')]",
        timeout=1,
    ):
        time.sleep(1.5)
        if is_all_reviews_page(driver.page_source):
            return True

    try:
        scroll_until_reviews(driver)
    except RuntimeError:
        pass
    if tap_text_matching(driver, "全部", min_x=300, min_y=240):
        time.sleep(1.5)
        if is_all_reviews_page(driver.page_source):
            return True

    dump_debug_snapshot(driver, debug_dir, f"open_all_reviews_failed_{query}")
    return is_all_reviews_page(driver.page_source)


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
    source = driver.page_source
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
        root = parse_xml(driver.page_source)
        for cell in review_cells(root):
            if is_all_reviews_page(driver.page_source) and not is_visible_review_cell(cell):
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


def write_rows(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_class = rows[0].__class__ if rows else ReviewRow
    fieldnames = [field.name for field in fields(sample_class)]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def flush_rows(path: Path, rows: list, row_class: type = ReviewRow) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    if rows:
        write_rows(tmp_path, rows)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [field.name for field in fields(row_class)]
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
) -> None:
    queries = read_queries(input_path)
    if limit is not None:
        queries = queries[:limit]
    driver = make_driver()
    all_reviews: list[ReviewRow] = []
    all_ratings: list[ScriptSummary] = []
    try:
        try:
            for query in queries:
                print(f"Searching: {query}")
                try:
                    go_to_search(driver)
                    submit_search(driver, query)
                    if not open_first_script_result(driver, query, debug_dir=debug_dir):
                        if mode in {"reviews", "both"}:
                            flush_rows(reviews_output_path, all_reviews, ReviewRow)
                        flush_rows(ratings_output_path, all_ratings, ScriptSummary)
                        continue
                    summary = parse_summary(driver.page_source, query=query)
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
                    if mode in {"reviews", "both"}:
                        flush_rows(reviews_output_path, all_reviews, ReviewRow)
                    flush_rows(ratings_output_path, all_ratings, ScriptSummary)
                finally:
                    return_to_search_results(driver, debug_dir, query)
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
    parser.add_argument("--input", type=Path, default=Path("miquan_appium/search_terms.csv"))
    parser.add_argument("--output", type=Path, default=Path("miquan_appium/miquan_reviews.csv"))
    parser.add_argument("--ratings-output", type=Path, default=Path("miquan_appium/miquan_ratings.csv"))
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
        "--debug-dir",
        type=Path,
        default=Path("miquan_appium/debug"),
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
    )


if __name__ == "__main__":
    main()
