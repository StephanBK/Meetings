"""Second pass: deeper crawl for bodies the first pass could not classify.
Differences: tries https/no-www URL variants, reads sitemap.xml, follows up to 8 board links and then up to 6 agenda/minutes links one level deeper."""
import csv,re,sys,time
from urllib.parse import urljoin,urlparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
spec=importlib.util.spec_from_file_location("fp","fingerprint.py"); fp=importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)
import requests, urllib.robotparser
HREF=re.compile(r'href=["\']([^"\']+)["\']',re.I)
KW1=re.compile(r"board|boe\b|/boe|trustee|district/|about",re.I); KW2=re.compile(r"agenda|minutes|meeting|boarddocs",re.I)
BAD=re.compile(r"\.(pdf|jpg|png|docx?|xlsx?)($|\?)|keyboard|dashboard|onboard|mailto:|javascript:|#",re.I)
PDF=re.compile(r"\.pdf|GetFile|DocumentCenter|resource-manager|/files/|drive\.google",re.I)
def variants(u):
    p=urlparse(u if u.startswith("http") else "http://"+u); h=p.netloc; bare=re.sub(r"^www\.","",h)
    out=[]
    for sch in("https","http"):
        for host in(h,bare,"www."+bare):
            v=f"{sch}://{host}/"
            if v not in out: out.append(v)
    return out
def robots(s,base):
    rp=urllib.robotparser.RobotFileParser()
    try:
        rr=s.get(urljoin(base,"/robots.txt"),timeout=(10,10))
        if rr.status_code>=500: rp.parse(["User-agent: *","Disallow: /"])
        elif rr.status_code>=400 or "<html" in rr.text[:200].lower(): rp.parse([])
        else: rp.parse(rr.text.splitlines())
    except Exception: rp.parse([])
    return rp
def run(row):
    s=requests.Session(); s.headers["User-Agent"]=fp.UA
    res=dict(p2_status="",p2_url="",p2_platforms="",p2_platform_link="",p2_best_page="",p2_pdf_links=0,p2_pages=0,p2_note="")
    html="";final=""
    for v in variants(row["website"]):
        code,final,html=fp.get(s,v); time.sleep(1)
        res["p2_status"]=code
        if html and code==200: break
    if not html or res["p2_status"]!=200: res["p2_note"]="still no usable homepage"; return res
    res["p2_url"]=final; rp=robots(s,final)
    if not rp.can_fetch(fp.UA,final): res["p2_note"]="robots.txt disallows"; return res
    host=urlparse(final).netloc; found=fp.scan(html); plink=""; best="";bestpdf=0; seen={final}
    def links(h,base): return {urljoin(base,l) for l in HREF.findall(h) if not BAD.search(l)}
    def note(h,u):
        nonlocal found,plink,best,bestpdf
        found|=fp.scan(h)
        for l in HREF.findall(h):
            if re.search(r"boarddocs\.com|diligent|legistar|boardbook|eboardsolutions",l,re.I) and not plink: plink=l
        n=sum(1 for l in HREF.findall(h) if PDF.search(l) and re.search(r"agenda|minute|meeting|boe|board",l,re.I))
        if n>bestpdf: bestpdf=n;best=u
    note(html,final)
    l1=[u for u in links(html,final) if urlparse(u).netloc==host and (KW1.search(u) or KW2.search(u))]
    # sitemap
    try:
        sm=s.get(urljoin(final,"/sitemap.xml"),timeout=(10,10))
        if sm.status_code==200: l1+=[u for u in re.findall(r"<loc>([^<]+)</loc>",sm.text) if KW2.search(u) or re.search(r"board",u,re.I)][:30]
    except Exception: pass
    l1=sorted(set(l1),key=lambda u:(not KW2.search(u),not re.search(r"board|boe",u,re.I),len(u)))[:8]
    l2=[]
    for u in l1:
        if u in seen or not rp.can_fetch(fp.UA,u): continue
        seen.add(u); time.sleep(1); c,f,h=fp.get(s,u)
        if not h: continue
        res["p2_pages"]+=1; note(h,u)
        l2+=[x for x in links(h,f) if urlparse(x).netloc==host and KW2.search(x)]
    for u in sorted(set(l2),key=len)[:6]:
        if u in seen or not rp.can_fetch(fp.UA,u): continue
        seen.add(u); time.sleep(1); c,f,h=fp.get(s,u)
        if not h: continue
        res["p2_pages"]+=1; note(h,u)
    res["p2_platforms"]=";".join(sorted(found)); res["p2_platform_link"]=plink; res["p2_best_page"]=best; res["p2_pdf_links"]=bestpdf
    return res
if __name__=="__main__":
    rows=list(csv.DictReader(open("manual_queue_li.csv")))
    rows=[r for r in rows if r["blocker_code"] in("NOT_DETECTED","NO_RESPONSE","STALE_URL")]
    def work(r):
        try: x=run(r)
        except Exception as e: x=dict(p2_status="",p2_url="",p2_platforms="",p2_platform_link="",p2_best_page="",p2_pdf_links=0,p2_pages=0,p2_note="error "+str(e)[:60])
        r.update(x); print(r["name"][:38],"|",x["p2_status"],"|",x["p2_platforms"],"| pdf",x["p2_pdf_links"],"| pages",x["p2_pages"],"|",x["p2_note"],flush=True); return r
    with ThreadPoolExecutor(8) as ex: out=list(ex.map(work,rows))
    w=csv.DictWriter(open("pass2.csv","w",newline=""),fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
