#!/usr/bin/env python3
import html
import json
import re
import urllib.request
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

BASE = "https://pin.portofrotterdam.com/"
OUT = Path("notices.json")
TZ = ZoneInfo("Europe/Amsterdam")
BRIDGES = {
    "botlekbrug": "Botlekbrug",
    "spijkenisserbrug": "Spijkenisserbrug",
    "papendrechtsebrug": "Papendrechtsebrug",
    "brug over de noord": "Brug over de Noord",
    "alblasserdamsebrug": "Brug over de Noord",
    "calandbrug": "Calandbrug",
    "van brienenoordbrug": "Brienenoordbrug",
    "brienenoordbrug": "Brienenoordbrug",
    "wantijbrug": "Wantijbrug",
    "hartelbrug": "Hartelbrug",
}
MONTHS = {
    "jan":1,"januari":1,"january":1,
    "feb":2,"februari":2,"february":2,
    "mrt":3,"maart":3,"mar":3,"march":3,
    "apr":4,"april":4,
    "mei":5,"may":5,
    "jun":6,"juni":6,"june":6,
    "jul":7,"juli":7,"july":7,
    "aug":8,"augustus":8,"august":8,
    "sep":9,"sept":9,"september":9,
    "okt":10,"oct":10,"oktober":10,"october":10,
    "nov":11,"november":11,
    "dec":12,"december":12,
}
MONTH_RE = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links=[]; self.href=None; self.anchor=[]; self.lines=[]; self.buf=[]
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href"); self.anchor=[]
        if tag in {"p","li","h1","h2","h3","div","br","tr","td"}:
            self._flush()
    def handle_endtag(self, tag):
        if tag == "a":
            text=" ".join("".join(self.anchor).split())
            if self.href and text: self.links.append((self.href,text))
            self.href=None; self.anchor=[]
        if tag in {"p","li","h1","h2","h3","div","tr","td"}: self._flush()
    def handle_data(self, data):
        self.buf.append(data)
        if self.href is not None: self.anchor.append(data)
    def _flush(self):
        text=" ".join(html.unescape("".join(self.buf)).split())
        if text: self.lines.append(text)
        self.buf=[]


def fetch(url):
    req=urllib.request.Request(url, headers={"User-Agent":"Brugopen/1.0 (+https://brugwachterrhoon.github.io/brugopen-live/)"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_page(url):
    p=PageParser(); p.feed(fetch(url)); p._flush(); return p


def nearest_year(month, now):
    year=now.year
    if now.month == 12 and month == 1: year += 1
    if now.month == 1 and month == 12: year -= 1
    return year


def dt_for(day, month_name, hh, mm, now):
    month=MONTHS[month_name.lower()]
    return datetime(nearest_year(month, now), month, int(day), int(hh), int(mm), tzinfo=TZ)


def parse_ranges(line, now):
    s=line.lower().replace("uur", " ").replace("hrs.", " ").replace("hrs", " ")
    s=re.sub(r"\s+", " ", s)
    ranges=[]

    rx_cross=re.compile(rf"(\d{{1,2}})\s+({MONTH_RE}).*?(\d{{1,2}})[.:](\d{{2}}).*?(?:tot|to|-)\s+(?:[a-z]+\s+)?(\d{{1,2}})\s+({MONTH_RE})\s+(\d{{1,2}})[.:](\d{{2}})", re.I)
    for m in rx_cross.finditer(s):
        a=dt_for(m.group(1),m.group(2),m.group(3),m.group(4),now)
        b=dt_for(m.group(5),m.group(6),m.group(7),m.group(8),now)
        if b<a: b=b.replace(year=a.year+1)
        ranges.append((a,b))

    rx_same=re.compile(rf"(\d{{1,2}})\s+({MONTH_RE}).*?(\d{{1,2}})[.:](\d{{2}}).*?(?:tot|to|en|and|-)\s+(\d{{1,2}})[.:](\d{{2}})", re.I)
    for m in rx_same.finditer(s):
        a=dt_for(m.group(1),m.group(2),m.group(3),m.group(4),now)
        b=dt_for(m.group(1),m.group(2),m.group(5),m.group(6),now)
        if b<=a: b += timedelta(days=1)
        if not any(abs((a-x[0]).total_seconds())<60 and abs((b-x[1]).total_seconds())<60 for x in ranges): ranges.append((a,b))
    return ranges


def discover_relevant_links():
    found={}
    for page in range(0,10):
        url=BASE if page==0 else f"{BASE}?page={page}"
        try: p=parse_page(url)
        except Exception as e:
            print(f"listing page {page} failed: {e}"); continue
        page_hits=0
        for href,text in p.links:
            low=text.lower()
            if any(k in low for k in BRIDGES):
                full=urljoin(BASE, href)
                if "/node/" in full or "/pin-" in full:
                    found[full]=text; page_hits+=1
        if page>2 and page_hits==0: break
    return found


def bridge_for(text):
    low=text.lower()
    for key,name in BRIDGES.items():
        if key in low: return name
    return None


def make_override(bridge, a, b, is_open, source, priority):
    rows=[]; cur=a
    while cur.date() < b.date():
        rows.append({"bridge":bridge,"startDate":cur.date().isoformat(),"endDate":cur.date().isoformat(),"startMin":cur.hour*60+cur.minute,"endMin":1440,"open":is_open,"priority":priority,"source":source})
        cur=datetime(cur.year,cur.month,cur.day,tzinfo=TZ)+timedelta(days=1)
    if cur < b:
        endmin=b.hour*60+b.minute
        if endmin==0 and b.date()>cur.date(): endmin=1440
        rows.append({"bridge":bridge,"startDate":cur.date().isoformat(),"endDate":cur.date().isoformat(),"startMin":cur.hour*60+cur.minute,"endMin":endmin,"open":is_open,"priority":priority,"source":source})
    return rows


def parse_notice(url,title,now):
    p=parse_page(url)
    text="\n".join(p.lines)
    bridge=bridge_for(title+"\n"+text)
    if not bridge: return []
    rows=[]; mode=False
    for line in p.lines:
        low=line.lower()
        if any(x in low for x in ["passage possible","mogelijkheid van passage","mogelijkheden van passage","tussentijdse opening","intermediate opening"]): mode=True
        elif any(x in low for x in ["geen bediening","no service","gestremd","closed","niet bedienbaar","not be operated","complete obstruction"]): mode=False
        for a,b in parse_ranges(line,now):
            rows.extend(make_override(bridge,a,b,mode,url,100 if mode else 50))
    return rows


def main():
    now=datetime.now(TZ)
    links=discover_relevant_links()
    print(f"Relevant PIN/BAS pages: {len(links)}")
    rows=[]
    for url,title in links.items():
        try: rows.extend(parse_notice(url,title,now))
        except Exception as e: print(f"notice failed {url}: {e}")
    cutoff=(now.date()-timedelta(days=1)).isoformat()
    unique={}
    for r in rows:
        if r["endDate"] < cutoff: continue
        key=(r["bridge"],r["startDate"],r["startMin"],r["endMin"],r["open"],r["source"])
        unique[key]=r
    data=sorted(unique.values(), key=lambda r:(r["startDate"],r["bridge"],r["startMin"],r["priority"]))
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Wrote {len(data)} overrides")

if __name__ == "__main__": main()
