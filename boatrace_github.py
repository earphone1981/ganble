import asyncio
import datetime
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).strftime("%Y%m%d")

INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index"
RACE_URL = "https://www.boatrace.jp/owpc/pc/race/raceindex?hd={date}&jcd={code}"

VENUES = {
    "01": ("01 桐生", "boat.kiryu", "01kiryu"),
    "02": ("02 戸田", "boat.toda", "02toda"),
    "03": ("03 江戸川", "boat.edogawa", "03edogawa"),
    "04": ("04 平和島", "boat.heiwajima", "04heiwajima"),
    "05": ("05 多摩川", "boat.tamagawa", "05tamagawa"),
    "06": ("06 浜名湖", "boat.hamanako", "06hamanako"),
    "07": ("07 蒲郡", "boat.gamagori", "07gamagori"),
    "08": ("08 常滑", "boat.tokoname", "08tokoname"),
    "09": ("09 津", "boat.tsu", "09tsu"),
    "10": ("10 三国", "boat.mikuni", "10mikuni"),
    "11": ("11 びわこ", "boat.biwako", "11biwako"),
    "12": ("12 住之江", "boat.suminoe", "12suminoe"),
    "13": ("13 尼崎", "boat.amagasaki", "13amagasaki"),
    "14": ("14 鳴門", "boat.naruto", "14naruto"),
    "15": ("15 丸亀", "boat.marugame", "15marugame"),
    "16": ("16 児島", "boat.kojima", "16kojima"),
    "17": ("17 宮島", "boat.miyajima", "17miyajima"),
    "18": ("18 徳山", "boat.tokuyama", "18tokuyama"),
    "19": ("19 下関", "boat.shimonoseki", "19shimonoseki"),
    "20": ("20 若松", "boat.wakamatsu", "20wakamatsu"),
    "21": ("21 芦屋", "boat.ashiya", "21ashiya"),
    "22": ("22 福岡", "boat.fukuoka", "22fukuoka"),
    "23": ("23 唐津", "boat.karatsu", "23karatsu"),
    "24": ("24 大村", "boat.omura", "24omura"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.6",
    "Cache-Control": "no-cache",
}

DEBUG_DIR = Path("boatrace_debug_v3")
OUT_JSON = Path("boatrace_github_v3.json")
OUT_M3U = Path("boatrace_github_v3.m3u")


def get_active_codes():
    r = requests.get(INDEX_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    found = set(re.findall(r"[?&]jcd=(\d{2})(?:[&#\"'])", r.text))
    return sorted(c for c in found if c in VENUES)


def parse_times_and_type(html):
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    race_times = {}
    for pat in (
        r"(?:^|\s)(\d{1,2})R\b.*?([0-2]?\d:[0-5]\d)",
        r"第\s*(\d{1,2})\s*R\b.*?([0-2]?\d:[0-5]\d)",
    ):
        for m in re.finditer(pat, text, re.S):
            no = int(m.group(1))
            if 1 <= no <= 12:
                race_times.setdefault(no, m.group(2))

    ordered = [race_times[n] for n in sorted(race_times)]
    first = ordered[0] if ordered else ""
    last = ordered[-1] if ordered else ""

    def shift(t, mins):
        if not t:
            return ""
        h, m = map(int, t.split(":"))
        x = h * 60 + m + mins
        return f"{(x // 60) % 24:02d}:{x % 60:02d}"

    start = shift(first, -15)
    end = shift(last, 10)

    if "ミッドナイト" in text:
        return start, end, "ミッドナイト", "🌌"
    if "ナイター" in text:
        return start, end, "ナイター", "🌙"
    if "サマータイム" in text:
        return start, end, "サマータイム", "🌇"
    if "モーニング" in text:
        return start, end, "モーニング", "🌅"
    if first:
        h = int(first.split(":")[0])
        if h < 10:
            return start, end, "モーニング", "🌅"
        if h >= 14:
            return start, end, "ナイター", "🌙"
    return start, end, "デイ", "🌞"


def extract_candidate_strings(obj):
    out = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                out.append(str(k))
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, (str, int, float, bool)):
            out.append(str(x))

    walk(obj)
    return out


def extract_urls_from_text(text):
    if not text:
        return []
    urls = re.findall(r'https?://[^\s"\'<>\\]+', text)
    # escaped JSON URL対策
    urls += [
        u.replace("\\/", "/")
        for u in re.findall(r'https?:\\?/\\?/[^\s"\'<>]+', text)
    ]
    return urls


def score_stream(u):
    lu = u.lower()
    score = 0
    if ".m3u8" in lu:
        score += 100
    if "manifest.streaks.jp" in lu:
        score += 80
    if "streaks.jp" in lu:
        score += 40
    if "manifest" in lu:
        score += 20
    if "playlist" in lu:
        score += 10
    if "master" in lu:
        score += 5
    return score


def choose_stream(values):
    candidates = []
    seen = set()
    for raw in values:
        if not raw:
            continue
        s = str(raw).replace("&amp;", "&").replace("\\/", "/")
        for u in extract_urls_from_text(s) or [s]:
            if not (u.startswith("http://") or u.startswith("https://")):
                continue
            if u in seen:
                continue
            seen.add(u)
            if score_stream(u) > 0:
                candidates.append(u)

    candidates.sort(key=score_stream, reverse=True)
    return candidates[0] if candidates and ".m3u8" in candidates[0].lower() else ""


async def response_body_text(resp):
    try:
        ct = (await resp.header_value("content-type") or "").lower()
        if not any(x in ct for x in ("json", "javascript", "text", "xml", "mpegurl")):
            return ""
        body = await resp.text()
        if len(body) > 1_500_000:
            body = body[:1_500_000]
        return body
    except Exception:
        return ""


async def capture_one(browser, code):
    venue_name, tvg_id, stadium = VENUES[code]
    race_url = RACE_URL.format(date=TODAY, code=code)
    player_url = (
        "https://front.player.boatrace-cdn.jp/player/live"
        f"?service=boatcast&stadium={stadium}"
        "&sourceType=mix&dvr=1&audioMode=0&autoplay=1&bitrate=low"
    )

    context = await browser.new_context(
        locale="ja-JP",
        user_agent=HEADERS["User-Agent"],
        viewport={"width": 1365, "height": 1000},
        java_script_enabled=True,
    )

    all_urls = []
    bodies = {}
    console_lines = []

    async def attach(page):
        page.on("request", lambda req: all_urls.append(req.url))
        page.on("console", lambda msg: console_lines.append(f"{msg.type}: {msg.text}"))

        async def handle_response(resp):
            all_urls.append(resp.url)
            if (
                "setting.json" in resp.url
                or "front.player.boatrace-cdn.jp" in resp.url
                or "streaks" in resp.url.lower()
                or ".m3u8" in resp.url.lower()
            ):
                body = await response_body_text(resp)
                if body:
                    bodies[resp.url] = body

        page.on("response", lambda resp: asyncio.create_task(handle_response(resp)))

    page = await context.new_page()
    await attach(page)

    try:
        # 公式レースページで開催時刻を取得
        await page.goto(race_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)
        race_html = await page.content()
        start, end, day_type, emoji = parse_times_and_type(race_html)

        # V3の本命: プレイヤーURLを直接開く
        p2 = await context.new_page()
        await attach(p2)
        await p2.goto(player_url, wait_until="domcontentloaded", timeout=45000)

        # setting.json → player初期化 → manifest発行を待つ
        for _ in range(12):
            await p2.wait_for_timeout(1000)
            stream = choose_stream(
                all_urls
                + list(bodies.keys())
                + list(bodies.values())
            )
            if stream:
                break

        # Performance API
        try:
            perf = await p2.evaluate(
                """() => performance.getEntriesByType('resource').map(x => x.name)"""
            )
            all_urls.extend(perf or [])
        except Exception:
            pass

        # DOM/inline scriptからも探索
        try:
            html2 = await p2.content()
            all_urls.extend(extract_urls_from_text(html2))
        except Exception:
            pass

        # setting.jsonを明示的にfetchして内容を取得
        setting_url = (
            f"https://front.player.boatrace-cdn.jp/setting/live/{stadium}/setting.json"
        )
        try:
            setting_result = await p2.evaluate(
                """async (u) => {
                    const r = await fetch(u + '?t=' + Date.now(), {cache:'no-store'});
                    return {status:r.status, text:await r.text()};
                }""",
                setting_url,
            )
            bodies["DIRECT_SETTING_FETCH"] = json.dumps(
                setting_result, ensure_ascii=False
            )
            if setting_result and setting_result.get("text"):
                txt = setting_result["text"]
                all_urls.extend(extract_urls_from_text(txt))
                try:
                    obj = json.loads(txt)
                    all_urls.extend(extract_candidate_strings(obj))
                except Exception:
                    pass
        except Exception as e:
            console_lines.append(f"DIRECT_SETTING_ERROR: {type(e).__name__}: {e}")

        stream = choose_stream(
            all_urls
            + list(bodies.keys())
            + list(bodies.values())
        )

        # デバッグ保存
        DEBUG_DIR.mkdir(exist_ok=True)
        debug = {
            "venue": venue_name,
            "race_url": race_url,
            "player_url": player_url,
            "setting_url": setting_url,
            "all_urls": all_urls[-600:],
            "response_bodies": bodies,
            "console": console_lines[-200:],
            "selected_stream": stream,
        }
        (DEBUG_DIR / f"{code}.json").write_text(
            json.dumps(debug, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        await p2.close()

        return venue_name, {
            "tvg_id": tvg_id,
            "live": bool(stream),
            "start": start,
            "end": end,
            "day_type": day_type,
            "emoji": emoji,
            "url": stream,
            "player_url": player_url,
            "source": "GitHub Actions / direct player V3",
        }

    except Exception as e:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"{code}_fatal.txt").write_text(
            f"{type(e).__name__}: {e}\n",
            encoding="utf-8",
        )
        return venue_name, {
            "tvg_id": tvg_id,
            "live": False,
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        await context.close()


def write_outputs(results):
    data = {}
    for code, (venue_name, tvg_id, stadium) in VENUES.items():
        data[venue_name] = results.get(
            venue_name,
            {"tvg_id": tvg_id, "live": False},
        )

    OUT_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        '#EXTM3U url-tvg="https://earphone1981.github.io/epg-generator/epg.xml"'
    ]
    for code, (venue_name, tvg_id, stadium) in VENUES.items():
        info = data[venue_name]
        if info.get("live") and info.get("url"):
            lines.append(
                f'#EXTINF:-1 tvg-id="{tvg_id}" '
                f'tvg-name="{venue_name}" group-title="ボートレース",{venue_name}'
            )
            lines.append(info["url"])

    OUT_M3U.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main():
    print("================================")
    print("BOAT RACE GitHub updater V3")
    print("DATE:", TODAY)
    print("================================")

    active = get_active_codes()
    print("Active:", ", ".join(active) if active else "none")

    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for code in active:
            venue_name, _, _ = VENUES[code]
            print(f"CHECK {venue_name} ...", end="", flush=True)
            name, info = await capture_one(browser, code)
            results[name] = info
            if info.get("live"):
                print(" OK")
                print(" ", info["url"][:220])
            else:
                print(" stream not found")

        await browser.close()

    write_outputs(results)

    ok = sum(1 for v in results.values() if v.get("live"))
    print("================================")
    print("Streams found:", ok)
    print("Saved:", OUT_JSON)
    print("Saved:", OUT_M3U)
    print("Debug:", DEBUG_DIR)
    print("================================")


if __name__ == "__main__":
    asyncio.run(main())
