"""Free keyword scan. Reads taxonomy.yaml, scans text files, reports hits per trade and stage.
A 'passage' is a block of text between blank lines (roughly one agenda item or resolution)."""
import re,sys,os,yaml,glob,json
from collections import Counter,defaultdict
HERE=os.path.dirname(os.path.abspath(__file__))
T=yaml.safe_load(open(os.path.join(HERE,'taxonomy.yaml')))
def rx(k):
    k=re.escape(k.lower()).replace(r'\ ',r'[\s\-]+')
    return re.compile(r'(?<![a-z])'+k+r'(?:s|es|ing|ed)?(?![a-z])',re.I)
TR={t:[(k,rx(k)) for k in v['keywords']] for t,v in T['trades'].items()}
ST={s:[(k,rx(k)) for k in v['keywords']] for s,v in T['stages'].items()}
TG=[(k,rx(k)) for k in T['triggers']]
NEG=[re.compile(re.escape(n),re.I) for n in T['negatives']]
SKIP=[re.compile(x,re.I) for x in T.get('skip_patterns',[])]
def passages(txt):
    for p in re.split(r'\n\s*\n',txt):
        p=re.sub(r'\s+',' ',p).strip()
        if len(p)>25: yield p
def scan(txt):
    out=[]
    for p in passages(txt):
        if any(x.search(p) for x in SKIP): continue
        clean=p
        for n in NEG: clean=n.sub(' ',clean)       # remove negative phrases, then match on what is left
        tr={t:[k for k,r in ks if r.search(clean)] for t,ks in TR.items()}; tr={t:k for t,k in tr.items() if k}
        st={s:[k for k,r in ks if r.search(clean)] for s,ks in ST.items()}; st={s:k for s,k in st.items() if k}
        tg=[k for k,r in TG if r.search(clean)]
        if tr or tg: out.append(dict(text=p,trades=tr,stages=st,triggers=tg))
    return out
if __name__=="__main__":
    res={}
    folder=sys.argv[1] if len(sys.argv)>1 else '.'   # usage: python3 scan.py <folder with .txt files>
    for f in sorted(glob.glob(os.path.join(folder,'*.txt'))):
        txt=open(f,errors='replace').read(); ps=list(passages(txt)); hits=scan(txt); res[f]=hits
        tc=Counter(t for h in hits for t in h['trades']); sc=Counter(s for h in hits for s in h['stages'])
        print(f"\n=== {f}: {len(ps)} passages, {len(hits)} hit passages ({len(hits)/max(1,len(ps))*100:.0f}%), hit text {sum(len(h['text']) for h in hits)} of {len(txt)} chars")
        print("   trades:",dict(tc)); print("   stages:",dict(sc))
    json.dump(res,open('hits.json','w'))
