"""Platform fingerprinting for the discovery census.
For each body: fetch homepage, look for known agenda-platform link patterns.
If none found, follow up to 4 same-site links whose text/URL mentions 'board' and look again.
Polite: 1 request/sec per site, honest User-Agent, robots.txt respected."""
import csv, re, sys, time, urllib.robotparser
from urllib.parse import urljoin, urlparse
import requests
UA="MeetingsCensus/0.1 (research; contact via github.com/StephanBK/Meetings)"
FP={"BoardDocs":r"boarddocs\.com","Legistar":r"legistar\.com","BoardBook":r"boardbook\.org","Simbli":r"simbli\.eboardsolutions|eboardsolutions\.com",
    "CivicClerk/CivicPlus":r"civicclerk|civicplus|agendacenter","Granicus":r"granicus\.com","Diligent Community":r"diligent\.community|community\.diligentoneplatform",
    "BoardOnTrack":r"boardontrack\.com","Sparq":r"sparqdata|meetings\.sparq","Google Drive/Docs":r"drive\.google\.com|docs\.google\.com","YouTube":r"youtube\.com|youtu\.be"}
CMS={"Finalsite":r"finalsite|fsPageLayout|resources\.finalsite","Edlio":r"edlio","Blackboard/SchoolWires":r"schoolwires|blackboard|/site/Default\.aspx|/cms/lib","Apptegy/Thrillshare":r"apptegy|thrillshare","SchoolMessenger":r"schoolmessenger|/pages/","ParentSquare/Smore":r"parentsquare"}
def get(s,url):
    # Safety: stream the page, stop at 3 MB or 25 seconds so one odd site cannot hang the run.
    try:
        t0=time.time(); r=s.get(url,timeout=(10,15),allow_redirects=True,stream=True)
        if "html" not in r.headers.get("content-type","html").lower(): r.close(); return r.status_code,r.url,""
        buf=b""
        for ch in r.iter_content(65536):
            buf+=ch
            if len(buf)>3_000_000 or time.time()-t0>25: break
        r.close(); return r.status_code,r.url,buf.decode(r.encoding or "utf-8","replace")
    except Exception as e: return 0,url,""
def allowed(rp,url):
    try: return rp.can_fetch(UA,url)
    except: return True
def scan(html):
    return {k for k,p in FP.items() if re.search(p,html,re.I)}
def run(row):
    s=requests.Session(); s.headers["User-Agent"]=UA
    base=row["website"]; 
    if not base.startswith("http"): base="http://"+base
    rp=urllib.robotparser.RobotFileParser()
    # Fetch robots.txt with our own session. Per RFC 9309: 4xx = no rules (allowed), 5xx = assume disallowed.
    try:
        rr=s.get(urljoin(base,"/robots.txt"),timeout=(10,10))
        if rr.status_code>=500: rp.parse(["User-agent: *","Disallow: /"])
        elif rr.status_code>=400 or "<html" in rr.text[:200].lower(): rp.parse([])
        else: rp.parse(rr.text.splitlines())
    except Exception: rp.parse([])
    res=dict(status="",final_url="",platforms="",platform_links="",board_pages="",cms="",pdf_links=0,note="")
    if not allowed(rp,base): res["note"]="robots.txt disallows"; return res
    code,final,html=get(s,base); res["status"]=code; res["final_url"]=final
    if not html: res["note"]="no response"; return res
    res["cms"]=";".join(k for k,p in CMS.items() if re.search(p,html,re.I))
    found=scan(html); links=set(re.findall(r'href=["\']([^"\']+)["\']',html,re.I))
    plinks={l for l in links if any(re.search(p,l,re.I) for k,p in FP.items() if k not in("YouTube","Google Drive/Docs"))}
    host=urlparse(final).netloc
    cand=[]
    for l in links:
        u=urljoin(final,l)
        if urlparse(u).netloc==host and re.search(r"board|boe|trustee|meeting|agenda|minutes",u,re.I) and not re.search(r"\.(pdf|jpg|png|docx?)$|keyboard|dashboard|onboard",u,re.I):
            cand.append(u)
    cand=sorted(set(cand),key=lambda u:(not re.search(r"agenda|minutes|meeting",u,re.I),len(u)))[:4]
    pdfs=0
    for u in cand:
        if not allowed(rp,u): continue
        time.sleep(1.0)
        c,f,h=get(s,u)
        if not h: continue
        found|=scan(h)
        ls=set(re.findall(r'href=["\']([^"\']+)["\']',h,re.I))
        plinks|={l for l in ls if any(re.search(p,l,re.I) for k,p in FP.items() if k not in("YouTube","Google Drive/Docs"))}
        pdfs+=sum(1 for l in ls if re.search(r"\.pdf|GetFile|DocumentCenter|/fs/resource-manager",l,re.I) and re.search(r"agenda|minute|meeting|boe|board",l,re.I))
    res["platforms"]=";".join(sorted(found)); res["platform_links"]=" ".join(sorted(plinks))[:300]
    res["board_pages"]=" ".join(cand)[:400]; res["pdf_links"]=pdfs
    return res
if __name__=="__main__":
    from concurrent.futures import ThreadPoolExecutor
    rows=list(csv.DictReader(open(sys.argv[1]))); lim=int(sys.argv[3]) if len(sys.argv)>3 else len(rows)
    rows=rows[:lim]
    def work(row):
        try: r=run(row)
        except Exception as e: r=dict(status="",final_url="",platforms="",platform_links="",board_pages="",cms="",pdf_links=0,note="error: "+str(e)[:80])
        row.update(r); print(row["name"][:40],"|",r["status"],"|",r["platforms"],"| pdfs",r["pdf_links"],"|",r["note"],flush=True); return row
    # 8 sites in parallel. Each site still gets max 1 request per second.
    with ThreadPoolExecutor(8) as ex: out=list(ex.map(work,rows))
    with open(sys.argv[2],"w",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
