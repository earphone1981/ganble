#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
当日開催場だけの IPTV.m3u を生成する。

入力（ganble リポジトリ直下想定）
  IPTVold.m3u
  keiba_schedule.json
  keirin_schedule.json
  autorace_schedule.json
  boatrace_today.json

出力
  IPTV.m3u

方針
  - 地方競馬: 当日開催場だけ、IPTVold.m3u の既存URL/ロゴを利用
  - 競輪: 当日開催場だけ、TIPSTAR(m3u8) のみ利用
           競輪(公式)のMPD系は IPTVold.m3u に残し、本番日次版には入れない
  - オート: 当日開催場だけ、IPTVold.m3u の既存URL/ロゴを利用
  - ボート: boatrace_today.json の live=true + urlありだけ利用
           URLは毎日取得した最新版、ロゴはBOAT RACE公式固定URL
  - JRAグリーン等、上記4競技以外の固定チャンネルはそのまま残す
"""

from __future__ import annotations
import datetime
import json
import re
from pathlib import Path

JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).strftime("%Y%m%d")

BASE = Path(__file__).resolve().parent
OLD_M3U = BASE / "IPTVold.m3u"
OUT_M3U = BASE / "IPTV.m3u"

KEIBA_JSON = BASE / "keiba_schedule.json"
KEIRIN_JSON = BASE / "keirin_schedule.json"
AUTO_JSON = BASE / "autorace_schedule.json"
BOAT_JSON = BASE / "boatrace_today.json"

EPG_URL = "https://earphone1981.github.io/epg-generator/epg.xml"

BOAT_LOGOS = {
    "boat.kiryu": "https://www.boatrace.jp/static/uploads/sites/8/01_N.jpg",
    "boat.toda": "https://www.boatrace.jp/static/uploads/sites/8/02_N-1.jpg",
    "boat.edogawa": "https://www.boatrace.jp/static/uploads/sites/8/03_N-1.jpg",
    "boat.heiwajima": "https://www.boatrace.jp/static/uploads/sites/8/04_N-1.jpg",
    "boat.tamagawa": "https://www.boatrace.jp/static/uploads/sites/8/05_N-1.jpg",
    "boat.hamanako": "https://www.boatrace.jp/static/uploads/sites/8/06_N-1.jpg",
    "boat.gamagori": "https://www.boatrace.jp/static/uploads/sites/8/07_N-1.jpg",
    "boat.tokoname": "https://www.boatrace.jp/static/uploads/sites/8/08_N-1.jpg",
    "boat.tsu": "https://www.boatrace.jp/static/uploads/sites/8/09_N-1-1.jpg",
    "boat.mikuni": "https://www.boatrace.jp/static/uploads/sites/8/10_N-1-1.jpg",
    "boat.biwako": "https://www.boatrace.jp/static/uploads/sites/8/11_N-1.jpg",
    "boat.suminoe": "https://www.boatrace.jp/static/uploads/sites/8/12_N-1-1.jpg",
    "boat.amagasaki": "https://www.boatrace.jp/static/uploads/sites/8/13_N-1.jpg",
    "boat.naruto": "https://www.boatrace.jp/static/uploads/sites/8/14_N-1.jpg",
    "boat.marugame": "https://www.boatrace.jp/static/uploads/sites/8/15_N-1.jpg",
    "boat.kojima": "https://www.boatrace.jp/static/uploads/sites/8/16_N-1.jpg",
    "boat.miyajima": "https://www.boatrace.jp/static/uploads/sites/8/17_N-1.jpg",
    "boat.tokuyama": "https://www.boatrace.jp/static/uploads/sites/8/18_N-1.jpg",
    "boat.shimonoseki": "https://www.boatrace.jp/static/uploads/sites/8/19_N-1.jpg",
    "boat.wakamatsu": "https://www.boatrace.jp/static/uploads/sites/8/20_N-1.jpg",
    "boat.ashiya": "https://www.boatrace.jp/static/uploads/sites/8/21_N-1.jpg",
    "boat.fukuoka": "https://www.boatrace.jp/static/uploads/sites/8/22_N-1-1.jpg",
    "boat.karatsu": "https://www.boatrace.jp/static/uploads/sites/8/23_N-1.jpg",
    "boat.omura": "https://www.boatrace.jp/static/uploads/sites/8/24_N-1.jpg",
}

BOAT_ORDER = {
    "boat.kiryu": 1, "boat.toda": 2, "boat.edogawa": 3, "boat.heiwajima": 4,
    "boat.tamagawa": 5, "boat.hamanako": 6, "boat.gamagori": 7, "boat.tokoname": 8,
    "boat.tsu": 9, "boat.mikuni": 10, "boat.biwako": 11, "boat.suminoe": 12,
    "boat.amagasaki": 13, "boat.naruto": 14, "boat.marugame": 15, "boat.kojima": 16,
    "boat.miyajima": 17, "boat.tokuyama": 18, "boat.shimonoseki": 19,
    "boat.wakamatsu": 20, "boat.ashiya": 21, "boat.fukuoka": 22,
    "boat.karatsu": 23, "boat.omura": 24,
}


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        print(f"ERROR: {path.name} 読込失敗: {e}")
        return {}


def validate_schedule(data: dict, label: str) -> set[str]:
    date = str(data.get("date", ""))
    if date and date != TODAY:
        print(f"WARN: {label} JSONの日付が今日と違います: {date} != {TODAY}")
        return set()
    venues = data.get("venues", {})
    if not isinstance(venues, dict):
        return set()
    ids = set()
    for venue, info in venues.items():
        if not isinstance(info, dict):
            continue
        tvg_id = str(info.get("tvg_id", "")).strip()
        if tvg_id:
            ids.add(tvg_id)
    return ids


def parse_entries(text: str):
    """M3Uを (EXTINF, URL) 単位で読む。コメントは捨てる。"""
    entries = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            extinf = line
            url = ""
            j = i + 1
            while j < len(lines):
                candidate = lines[j].strip()
                if candidate.startswith("#EXTINF:"):
                    break
                if candidate and not candidate.startswith("#"):
                    url = candidate
                    break
                j += 1
            if url:
                entries.append((extinf, url))
            i = max(i + 1, j + 1)
        else:
            i += 1
    return entries


def attr(extinf: str, key: str) -> str:
    m = re.search(rf'{re.escape(key)}="([^"]*)"', extinf)
    return m.group(1).strip() if m else ""


def display_name(extinf: str) -> str:
    return extinf.rsplit(",", 1)[-1].strip() if "," in extinf else ""


def normalize_extinf(extinf: str) -> str:
    # 門別の誤記だけ安全に直す
    return extinf.replace("ホッカイドウ競馬(紋別)", "ホッカイドウ競馬(門別)")


def build_boat_entries(boat_data: dict):
    result = []
    for venue_name, info in boat_data.items():
        if not isinstance(info, dict):
            continue
        if not info.get("live") or not info.get("url"):
            continue
        tvg_id = str(info.get("tvg_id", "")).strip()
        if not tvg_id.startswith("boat."):
            continue
        num = BOAT_ORDER.get(tvg_id, 99)
        name = venue_name
        # JSONの名前に番号が無い場合だけ番号を付ける
        if not re.match(r"^\d{2}\s", name):
            name = f"{num:02d} {name}"
        logo = BOAT_LOGOS.get(tvg_id, "")
        extinf = (
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" '
            f'tvg-logo="{logo}" group-title="ボートレース",{name}'
        )
        result.append((num, extinf, str(info["url"]).strip()))
    result.sort(key=lambda x: x[0])
    return [(x[1], x[2]) for x in result]


def main():
    if not OLD_M3U.exists():
        raise SystemExit("IPTVold.m3u がありません。GitHubに退避した旧版を同じフォルダへ置いてください。")

    old_text = OLD_M3U.read_text(encoding="utf-8-sig")
    entries = parse_entries(old_text)

    keiba_ids = validate_schedule(load_json(KEIBA_JSON), "地方競馬")
    keirin_ids = validate_schedule(load_json(KEIRIN_JSON), "競輪")
    auto_ids = validate_schedule(load_json(AUTO_JSON), "オート")
    boat_data = load_json(BOAT_JSON)

    selected = []
    counts = {"keiba": 0, "keirin": 0, "auto": 0, "fixed": 0}

    for extinf, url in entries:
        extinf = normalize_extinf(extinf)
        tvg_id = attr(extinf, "tvg-id")
        group = attr(extinf, "group-title")

        # BOATは旧URLを絶対使わず、後でboatrace_today.jsonから作る
        if tvg_id.startswith("boat.") or group == "ボートレース":
            continue

        # 競輪公式（MPD）は日次版から除外。IPTVold.m3uには残る。
        if group == "競輪(公式)":
            continue

        if tvg_id.startswith("keirin."):
            if tvg_id in keirin_ids and group == "競輪(TIPSTAR)":
                selected.append((extinf, url))
                counts["keirin"] += 1
            continue

        if tvg_id.startswith("chihou."):
            if tvg_id in keiba_ids:
                selected.append((extinf, url))
                counts["keiba"] += 1
            continue

        if tvg_id.startswith("auto."):
            if tvg_id in auto_ids:
                selected.append((extinf, url))
                counts["auto"] += 1
            continue

        # 上記4競技以外（例: JRAグリーン）は固定で残す
        selected.append((extinf, url))
        counts["fixed"] += 1

    boats = build_boat_entries(boat_data)

    out = [f'#EXTM3U url-tvg="{EPG_URL}" tvg-shift=0 m3uautoload=1', ""]

    # 元の順番で固定/地方競馬/オート/競輪TIPSTARを出力
    last_group = None
    for extinf, url in selected:
        group = attr(extinf, "group-title") or "その他"
        if group != last_group:
            if last_group is not None:
                out.append("")
            out.append(f"# ===== {group} =====")
            last_group = group
        out.append(extinf)
        out.append(url)
        out.append("")

    if boats:
        out.append("# ===== ボートレース =====")
        for extinf, url in boats:
            out.append(extinf)
            out.append(url)
            out.append("")

    OUT_M3U.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    print("=" * 58)
    print(" TODAY IPTV BUILD")
    print(" DATE:", TODAY)
    print("=" * 58)
    print(f"地方競馬   : {counts['keiba']}場")
    print(f"競輪TIPSTAR: {counts['keirin']}場")
    print(f"オート     : {counts['auto']}場")
    print(f"ボート     : {len(boats)}場")
    print(f"固定その他 : {counts['fixed']}ch")
    print("出力       :", OUT_M3U.name)
    print("=" * 58)

    # 安全装置：4競技全部0件は異常とみなして、空に近い本番を作らない
    if counts["keiba"] + counts["keirin"] + counts["auto"] + len(boats) == 0:
        raise SystemExit("ERROR: 4競技の開催場が0件です。JSON更新状況を確認してください。")


if __name__ == "__main__":
    main()
