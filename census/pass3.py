"""Third pass: render pages in a real (headless) browser so JavaScript-built menus become visible.
Skips robots-disallowed and bot-challenged sites. Max 1 page load per second per site."""
import asyncio,csv,re,sys
from urllib.parse import urljoin,urlparse
from playwright.async_api import async_playwright
import importlib.util
spec=importlib.util.spec_from_file_location("fp","fingerprint.py"); fp=importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)
KW=re.compile(r"board|boe\b|/boe|trustee|agenda|minutes|meeting",re.I); KW2=re.compile(r"agenda|minutes|meeting|boarddocs",re.I)
BAD=re.compile(r"\.(pdf|jpg|png|docx?)($|\?)|keyboard|dashboard|onboard|mailto:|javascript:|tel:",re.I)
PLAT=re.compile(r"boarddocs\.com|diligent|legistar\.com|boardbook\.org|eboardsolutions|boardontrack",re.I)
PDF=re.compile(r"\.pdf|GetFile|DocumentCenter|resource-manager|/files/|drive\.google|docs\.google",re.I)
async def links_of(page):
    return await page.eval_on_selector_all("a[href]","els=>els.map(e=>[e.href,(e.innerText||'').trim().slice(0,80)])")
async def run(browser,row,sem):
    res=dict(p3_platforms="",p3_platform_link="",p3_best_page="",p3_doc_links=0,p3_pages=0,p3_note="")
    async with sem:
        ctx=await browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 "+fp.UA); page=await ctx.new_page()
        try:
            url=row.get("p2_url") or row["website"]
            if not url.startswith("http"): url="http://"+url
            await page.goto(url,timeout=30000,wait_until="domcontentloaded"); await page.wait_for_timeout(2500)
            host=urlparse(page.url).netloc; found=set(); plink=""; best="";bestn=0
            async def note(u):
                nonlocal found,plink,best,bestn
                html=await page.content(); found|=fp.scan(html); ls=await links_of(page)
                for h,t in ls:
                    if PLAT.search(h) and not plink: plink=h
                n=sum(1 for h,t in ls if PDF.search(h) and re.search(r"agenda|minute|meeting",h+" "+t,re.I))
                if n>bestn: bestn=n;best=u
                return ls
            ls=await note(page.url); res["p3_pages"]=1
            cand=[h for h,t in ls if urlparse(h).netloc==host and not BAD.search(h) and KW.search(h+" "+t)]
            cand=sorted(set(cand),key=lambda u:(not KW2.search(u),len(u)))[:6]
            seen={page.url}
            for u in cand:
                if u in seen or plink: continue
                seen.add(u); await page.wait_for_timeout(1000)
                try:
                    await page.goto(u,timeout=25000,wait_until="domcontentloaded"); await page.wait_for_timeout(1500)
                    ls2=await note(u); res["p3_pages"]+=1
                    for h,t in ls2:
                        if urlparse(h).netloc==host and not BAD.search(h) and KW2.search(h+" "+t) and h not in seen and len(cand)<10: cand.append(h)
                except Exception: pass
            res.update(p3_platforms=";".join(sorted(found)),p3_platform_link=plink,p3_best_page=best,p3_doc_links=bestn)
        except Exception as e: res["p3_note"]="error "+str(e)[:70].replace("\n"," ")
        finally: await ctx.close()
    row.update(res); print(row["name"][:38],"|",res["p3_platforms"],"| docs",res["p3_doc_links"],"| pages",res["p3_pages"],"|",res["p3_platform_link"][:60],"|",res["p3_note"],flush=True); return row
async def main():
    rows=list(csv.DictReader(open("pass2.csv")))
    def resolved(r): return re.search(r"BoardDocs|Diligent",r["p2_platforms"]) or int(r["p2_pdf_links"] or 0)>0 or "Google Drive" in r["p2_platforms"]
    todo=[r for r in rows if not resolved(r) and str(r["p2_status"])=="200"]
    print("todo",len(todo),flush=True)
    async with async_playwright() as p:
        b=await p.chromium.launch(); sem=asyncio.Semaphore(5)
        out=await asyncio.gather(*[run(b,r,sem) for r in todo]); await b.close()
    w=csv.DictWriter(open("pass3.csv","w",newline=""),fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
asyncio.run(main())
