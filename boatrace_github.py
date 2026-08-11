import asyncio
import datetime
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).strftime("%Y%m%d")

INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index"
RACE_URL = "https://www.boatrace.jp/owpc/pc/race/raceindex?hd={date}&jcd={code}"

VENUES = {
    "01": ("01 桐生", "boat.kiryu"),
    "02": ("02 戸田", "boat.toda"),
    "03": ("03 江戸川", "boat.edogawa"),
    "04": ("04 平和島", "boat.heiwajima"),
    "05": ("05 多摩川", "boat.tamagawa"),
    "06": ("06 浜名湖", "boat.hamanako"),
    "07": ("07 蒲郡", "boat.gamagori"),
    "08": ("08 常滑", "boat.tokoname"),
    "09": ("09 津", "boat.tsu"),
    "10": ("10 三国", "boat.mikuni"),
    "11": ("11 びわこ", "boat.biwako"),
    "12": ("12 住之江", "boat.suminoe"),
    "13": ("13 尼崎", "boat.amagasaki"),
    "14": ("14 鳴門", "boat.naruto"),
    "15": ("15 丸亀", "boat.marugame"),
    "16": ("16 児島", "boat.kojima"),
    "17": ("17 宮島", "boat.miyajima"),
    "18": ("18 徳山", "boat.tokuyama"),
    "19": ("19 下関", "boat.shimonoseki"),
    "20": ("20 若松", "boat.wakamatsu"),
    "21": ("21 芦屋", "boat.ashiya"),
    "22": ("22 福岡", "boat.fukuoka"),
    "23": ("23 唐津", "boat.karatsu"),
    "24": ("24 大村", "boat.omura"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.6",
    "Cache-Control": "no-cache",
}


def get_active_codes():
    r = requests.get(INDEX_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text

    found = set(re.findall(r"[?&]jcd=(\d{2})(?:[&#\"'])", html))
    if found:
        return sorted(c for c in found if c in VENUES)

    # フォールバック: 当日のraceindexを軽く確認
    active = []
    for code, (name, _) in VENUES.items():
        try:
            u = RACE_URL.format(date=TODAY, code=code)
            rr = requests.get(u, headers=HEADERS, timeout=10)
            if rr.ok and name.split(" ", 1)[1] in rr.text and re.search(r"\b1R\b", rr.text):
                active.append(code)
        except Exception:
            pass
    return active


def parse_times_and_type(html, code):
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    # 1R～12Rの締切予定時刻を拾う
    pairs = []
    for m in re.finditer(
        r"(?:^|\s)(\d{1,2})R\s+.*?([0-2]?\d:[0-5]\d)",
        text,
        re.S,
    ):
        no = int(m.group(1))
        if 1 <= no <= 12:
            pairs.append((no, m.group(2)))

    unique = {}
    for no, tm in pairs:
        unique.setdefault(no, tm)

    ordered = [unique[n] for n in sorted(unique)]
    first = ordered[0] if ordered else ""
    last = ordered[-1] if ordered else ""

    # 既存EPGとの感覚を合わせ、配信枠は1R締切の約15分前～最終R締切の約10分後
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

    # 時刻から補完
    if first:
        h = int(first.split(":")[0])
        if h < 10:
            return start, end, "モーニング", "🌅"
        if h >= 14:
            return start, end, "ナイター", "🌙"

    return start, end, "デイ", "🌞"


def choose_stream(urls):
    clean = []
    for u in urls:
        if not u:
            continue
        u = u.replace("&amp;", "&")
        if ".m3u8" not in u.lower():
            continue
        if u not in clean:
            clean.append(u)

    # BOAT公式で現在使われているStreaks系を最優先
    for u in clean:
        if "manifest.streaks.jp" in u:
            return u
    for u in clean:
        if "streaks.jp" in u:
            return u
    return clean[0] if clean else ""


async def capture_one(browser, code):
    venue_name, tvg_id = VENUES[code]
    url = RACE_URL.format(date=TODAY, code=code)

    context = await browser.new_context(
        locale="ja-JP",
        user_agent=HEADERS["User-Agent"],
        viewport={"width": 1280, "height": 900},
    )
    page = await context.new_page()
    captured = []

    def on_request(req):
        u = req.url
        if ".m3u8" in u.lower() or "manifest.streaks.jp" in u:
            captured.append(u)

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)

        html = await page.content()
        start, end, day_type, emoji = parse_times_and_type(html, code)

        # HTML中に直書きされたm3u8も拾う
        captured.extend(
            re.findall(r'https?://[^"\'<>\s]+?\.m3u8(?:\?[^"\'<>\s]*)?', html)
        )

        # 「ライブ&リプレイ」等のリンクを探す
        links = await page.locator("a").evaluate_all(
            """els => els.map(a => ({
                text:(a.innerText||a.textContent||'').trim(),
                href:a.href||''
            }))"""
        )

        candidates = []
        for item in links:
            text = item.get("text", "")
            href = item.get("href", "")
            target = (text + " " + href).lower()
            if (
                "ライブ" in text
                or "リプレイ" in text
                or "live" in target
                or "replay" in target
                or "movie" in target
            ):
                if href.startswith("http"):
                    candidates.append(href)

        # liveリンクを順に開いてネットワークを監視
        for live_url in candidates[:5]:
            try:
                p2 = await context.new_page()
                p2.on("request", on_request)
                await p2.goto(live_url, wait_until="domcontentloaded", timeout=30000)
                await p2.wait_for_timeout(4000)

                body = await p2.content()
                captured.extend(
                    re.findall(
                        r'https?://[^"\'<>\s]+?\.m3u8(?:\?[^"\'<>\s]*)?',
                        body,
                    )
                )

                # Performance APIにも残っていないか確認
                perf = await p2.evaluate(
                    """() => performance.getEntriesByType('resource')
                        .map(x => x.name)
                        .filter(x => x.includes('.m3u8') || x.includes('streaks.jp'))"""
                )
                captured.extend(perf or [])
                await p2.close()

                if choose_stream(captured):
                    break
            except Exception as e:
                print(f"  live page error: {type(e).__name__}")

        stream = choose_stream(captured)

        return venue_name, {
            "tvg_id": tvg_id,
            "live": bool(stream),
            "start": start,
            "end": end,
            "day_type": day_type,
            "emoji": emoji,
            "url": stream,
            "source": "GitHub Actions / Playwright",
        }
    except Exception as e:
        return venue_name, {
            "tvg_id": tvg_id,
            "live": False,
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        await context.close()


def write_outputs(results):
    # 24場すべてをJSONに入れる
    data = {}
    for code, (venue_name, tvg_id) in VENUES.items():
        data[venue_name] = results.get(
            venue_name,
            {"tvg_id": tvg_id, "live": False},
        )

    Path("boatrace_today.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    out_dir = Path("IPTV")
    out_dir.mkdir(exist_ok=True)
    lines = [
        '#EXTM3U url-tvg="https://earphone1981.github.io/epg-generator/epg.xml"'
    ]

    for code, (venue_name, tvg_id) in VENUES.items():
        info = data[venue_name]
        if not info.get("live") or not info.get("url"):
            continue
        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" '
            f'tvg-name="{venue_name}" group-title="ボートレース",{venue_name}'
        )
        lines.append(info["url"])

    # GitHub Actions専用ファイル。既存PC版のプレイリストは壊さない。
    Path("IPTV/boatrace_github.m3u").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


async def main():
    print("================================")
    print("BOAT RACE GitHub updater")
    print("DATE:", TODAY)
    print("================================")

    active = get_active_codes()
    print("Active:", ", ".join(active) if active else "none")

    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # 同時実行を抑えてサイトへの負荷を上げすぎない
        for code in active:
            venue_name, _ = VENUES[code]
            print(f"CHECK {venue_name} ...", end="", flush=True)
            name, info = await capture_one(browser, code)
            results[name] = info
            if info.get("live"):
                print(" OK")
            else:
                print(" stream not found")

        await browser.close()

    write_outputs(results)

    ok = sum(1 for v in results.values() if v.get("live"))
    print("================================")
    print("Streams found:", ok)
    print("Saved: boatrace_today.json")
    print("Saved: IPTV/boatrace_github.m3u")
    print("================================")

    # 0件でもJSONは残し、ログで調査できるようexit 0にする


if __name__ == "__main__":
    asyncio.run(main())
