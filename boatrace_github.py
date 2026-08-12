import asyncio
import datetime
import json
import re
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).strftime('%Y%m%d')
INDEX_URL = 'https://www.boatrace.jp/owpc/pc/race/index'
RACE_URL = 'https://www.boatrace.jp/owpc/pc/race/raceindex?hd={date}&jcd={code}'
VENUES = {
 '01':('01 桐生','boat.kiryu'),'02':('02 戸田','boat.toda'),'03':('03 江戸川','boat.edogawa'),'04':('04 平和島','boat.heiwajima'),
 '05':('05 多摩川','boat.tamagawa'),'06':('06 浜名湖','boat.hamanako'),'07':('07 蒲郡','boat.gamagori'),'08':('08 常滑','boat.tokoname'),
 '09':('09 津','boat.tsu'),'10':('10 三国','boat.mikuni'),'11':('11 びわこ','boat.biwako'),'12':('12 住之江','boat.suminoe'),
 '13':('13 尼崎','boat.amagasaki'),'14':('14 鳴門','boat.naruto'),'15':('15 丸亀','boat.marugame'),'16':('16 児島','boat.kojima'),
 '17':('17 宮島','boat.miyajima'),'18':('18 徳山','boat.tokuyama'),'19':('19 下関','boat.shimonoseki'),'20':('20 若松','boat.wakamatsu'),
 '21':('21 芦屋','boat.ashiya'),'22':('22 福岡','boat.fukuoka'),'23':('23 唐津','boat.karatsu'),'24':('24 大村','boat.omura')}
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36','Accept-Language':'ja-JP,ja;q=0.9','Cache-Control':'no-cache'}
DEBUG_DIR=Path('boatrace_debug'); OUT_M3U=Path('boatrace_github.m3u'); OUT_JSON=Path('boatrace_today.json')

def get_active_codes():
    r=requests.get(INDEX_URL,headers=HEADERS,timeout=30); r.raise_for_status(); html=r.text
    found=set(re.findall(r'[?&]jcd=(\d{2})(?:[&#\"\'])',html))
    return sorted(c for c in found if c in VENUES)

def parse_times_and_type(html):
    text=BeautifulSoup(html,'html.parser').get_text(' ',strip=True)
    times={}
    for pat in [r'(?:^|\s)(\d{1,2})R\b.*?([0-2]?\d:[0-5]\d)',r'第\s*(\d{1,2})\s*R\b.*?([0-2]?\d:[0-5]\d)']:
        for m in re.finditer(pat,text,re.S):
            no=int(m.group(1))
            if 1<=no<=12: times.setdefault(no,m.group(2))
    ordered=[times[n] for n in sorted(times)]
    first=ordered[0] if ordered else ''; last=ordered[-1] if ordered else ''
    def shift(t,mins):
        if not t:return ''
        h,m=map(int,t.split(':')); x=h*60+m+mins; return f'{(x//60)%24:02d}:{x%60:02d}'
    start,end=shift(first,-15),shift(last,10)
    if 'ミッドナイト' in text:return start,end,'ミッドナイト','🌌'
    if 'ナイター' in text:return start,end,'ナイター','🌙'
    if 'サマータイム' in text:return start,end,'サマータイム','🌇'
    if 'モーニング' in text:return start,end,'モーニング','🌅'
    if first:
        h=int(first.split(':')[0])
        if h<10:return start,end,'モーニング','🌅'
        if h>=14:return start,end,'ナイター','🌙'
    return start,end,'デイ','🌞'

def choose_stream(urls):
    seen=[]
    for u in urls:
        if u and u not in seen: seen.append(u.replace('&amp;','&'))
    cand=[u for u in seen if '.m3u8' in u.lower()]
    for u in cand:
        if 'manifest.streaks.jp' in u:return u
    for u in cand:
        if 'streaks.jp' in u:return u
    return cand[0] if cand else ''

async def safe_perf(page):
    try:return await page.evaluate("() => performance.getEntriesByType('resource').map(x=>x.name).filter(Boolean)")
    except Exception:return []

async def capture_one(browser,code):
    venue_name,tvg_id=VENUES[code]; race_url=RACE_URL.format(date=TODAY,code=code)
    context=await browser.new_context(locale='ja-JP',user_agent=HEADERS['User-Agent'],viewport={'width':1365,'height':1000})
    page=await context.new_page(); reqs=[]; resps=[]; console=[]
    page.on('request',lambda r:reqs.append(r.url)); page.on('response',lambda r:resps.append(r.url)); page.on('console',lambda m:console.append(f'{m.type}: {m.text}'))
    try:
        await page.goto(race_url,wait_until='domcontentloaded',timeout=45000); await page.wait_for_timeout(2500)
        html=await page.content(); start,end,day_type,emoji=parse_times_and_type(html)
        links=await page.locator('a').evaluate_all("els=>els.map(a=>({text:(a.innerText||a.textContent||'').trim(),href:a.href||''}))")
        candidates=[]
        for item in links:
            text=item.get('text',''); href=item.get('href',''); target=(text+' '+href).lower()
            if any(k in text for k in ('ライブ','リプレイ','中継','映像')) or any(k in target for k in ('live','replay','movie','stream','video')):
                if href.startswith('http') and href not in candidates:candidates.append(href)
        extracted=re.findall(r'https?://[^\"\'< >\s]+?(?:\.m3u8|manifest(?:/|\.m3u8))[^\"\'< >\s]*',html,re.I)
        perf0=await safe_perf(page); visited=[]
        for live_url in candidates[:10]:
            p2=await context.new_page(); p2.on('request',lambda r:reqs.append(r.url)); p2.on('response',lambda r:resps.append(r.url))
            try:
                await p2.goto(live_url,wait_until='domcontentloaded',timeout=30000); await p2.wait_for_timeout(5000); visited.append(live_url)
                body=await p2.content(); extracted+=re.findall(r'https?://[^\"\'< >\s]+?(?:\.m3u8|manifest(?:/|\.m3u8))[^\"\'< >\s]*',body,re.I); extracted+=await safe_perf(p2)
                if choose_stream(reqs+resps+extracted): break
            except Exception as e: console.append(f'LIVE_PAGE_ERROR {live_url} {type(e).__name__}: {e}')
            finally: await p2.close()
        stream=choose_stream(reqs+resps+perf0+extracted)
        DEBUG_DIR.mkdir(exist_ok=True)
        debug={'venue':venue_name,'race_url':race_url,'candidate_links':candidates,'visited_links':visited,'requests':reqs[-250:],'responses':resps[-250:],'performance':perf0[-250:],'extracted':extracted[-250:],'console':console[-100:],'selected_stream':stream}
        (DEBUG_DIR/f'{code}.json').write_text(json.dumps(debug,ensure_ascii=False,indent=2),encoding='utf-8')
        return venue_name,{'tvg_id':tvg_id,'live':bool(stream),'start':start,'end':end,'day_type':day_type,'emoji':emoji,'url':stream,'source':'GitHub Actions / Playwright V2'}
    except Exception as e:
        DEBUG_DIR.mkdir(exist_ok=True); (DEBUG_DIR/f'{code}_fatal.txt').write_text(f'{type(e).__name__}: {e}\n',encoding='utf-8')
        return venue_name,{'tvg_id':tvg_id,'live':False,'error':f'{type(e).__name__}: {e}'}
    finally: await context.close()

def write_outputs(results):
    data={}
    for code,(venue_name,tvg_id) in VENUES.items(): data[venue_name]=results.get(venue_name,{'tvg_id':tvg_id,'live':False})
    OUT_JSON.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['#EXTM3U url-tvg="https://earphone1981.github.io/epg-generator/epg.xml"']
    for code,(venue_name,tvg_id) in VENUES.items():
        info=data[venue_name]
        if info.get('live') and info.get('url'):
            lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{venue_name}" group-title="ボートレース",{venue_name}'); lines.append(info['url'])
    OUT_M3U.write_text('\n'.join(lines)+'\n',encoding='utf-8')

async def main():
    print('================================'); print('BOAT RACE GitHub updater V2'); print('DATE:',TODAY); print('================================')
    active=get_active_codes(); print('Active:',', '.join(active) if active else 'none'); results={}
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        for code in active:
            venue_name,_=VENUES[code]; print(f'CHECK {venue_name} ...',end='',flush=True)
            name,info=await capture_one(browser,code); results[name]=info
            print(' OK' if info.get('live') else ' stream not found')
        await browser.close()
    write_outputs(results); ok=sum(1 for v in results.values() if v.get('live'))
    print('================================'); print('Streams found:',ok); print('Saved:',OUT_JSON); print('Saved:',OUT_M3U); print('Debug:',DEBUG_DIR); print('================================')

if __name__=='__main__': asyncio.run(main())
