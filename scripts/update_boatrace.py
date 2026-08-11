from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import urllib.request
import urllib.error

PROJECT = "cp-boatrace-prod"

HEADERS = {
    "Origin": "https://front.player.boatrace-cdn.jp",
    "Referer": "https://front.player.boatrace-cdn.jp/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}

VENUES = [
    ("01", "桐生", "01kiryu"),
    ("02", "戸田", "02toda"),
    ("03", "江戸川", "03edogawa"),
    ("04", "平和島", "04heiwajima"),
    ("05", "多摩川", "05tamagawa"),
    ("06", "浜名湖", "06hamanako"),
    ("07", "蒲郡", "07gamagori"),
    ("08", "常滑", "08tokoname"),
    ("09", "津", "09tsu"),
    ("10", "三国", "10mikuni"),
    ("11", "びわこ", "11biwako"),
    ("12", "住之江", "12suminoe"),
    ("13", "尼崎", "13amagasaki"),
    ("14", "鳴門", "14naruto"),
    ("15", "丸亀", "15marugame"),
    ("16", "児島", "16kojima"),
    ("17", "宮島", "17miyajima"),
    ("18", "徳山", "18tokuyama"),
    ("19", "下関", "19shimonoseki"),
    ("20", "若松", "20wakamatsu"),
    ("21", "芦屋", "21ashiya"),
    ("22", "福岡", "22fukuoka"),
    ("23", "唐津", "23karatsu"),
    ("24", "大村", "24omura"),
]

START = "# === BOATRACE AUTO START ==="
END = "# === BOATRACE AUTO END ==="


def get_stream(code, ymd):
    ref = f"lm-br-{code}-tokyo-{ymd}"
    url = (
        f"https://playback.api.streaks.jp/v1/projects/"
        f"{PROJECT}/medias/ref:{ref}?audio_only=false"
    )

    req = urllib.request.Request(url, headers=HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status != 200:
                return None

            data = json.load(res)

            sources = data.get("sources") or []
            if not sources:
                return None

            return sources[0].get("src")

    except urllib.error.HTTPError as e:
    print(f"HTTP ERROR: {e.code} {e.reason}")
    return None

except urllib.error.URLError as e:
    print(f"URL ERROR: {e.reason}")
    return None

except TimeoutError:
    print("TIMEOUT")
    return None

except json.JSONDecodeError as e:
    print(f"JSON ERROR: {e}")
    return None


def make_boatrace_block():
    ymd = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d")

    lines = [START, "#ボートレース"]

    count = 0

    for number, name, code in VENUES:
        stream = get_stream(code, ymd)

        if not stream:
            print(f"SKIP {number} {name}")
            continue

        lines.append(
            f'#EXTINF:-1 tvg-name="{name}" '
            f'group-title="ボートレース",{number} {name}'
        )
        lines.append(stream)

        print(f"OK   {number} {name}")
        count += 1

    lines.append(END)

    print(f"取得: {count}/24")
    return "\n".join(lines)


def main():
    path = Path("IPTV")

    original = path.read_text(encoding="utf-8")

    block = make_boatrace_block()

    if START in original and END in original:
        before = original.split(START, 1)[0]
        after = original.split(END, 1)[1]

        new_text = before + block + after

    else:
        new_text = original.rstrip() + "\n\n" + block + "\n"

    path.write_text(new_text, encoding="utf-8")


if __name__ == "__main__":
    main()
