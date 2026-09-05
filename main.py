"""
SAVAN Certificate Render Microservice — FastAPI on Render
Loads persistent_assets.json from GitHub at startup.
POST /render  →  { certificates: [{cert_id, name, date, pdf_base64}] }
"""

import re, os, io, base64, json, tempfile, logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("savan-render")

app = FastAPI(title="SAVAN Render API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["POST", "GET"], allow_headers=["*"])

# ── Assets loading ─────────────────────────────────────────────────────────────
# GitHub raw URL for persistent_assets.json
GITHUB_ASSETS_URL = "https://raw.githubusercontent.com/ncrptz/savan-render-api/main/persistent_assets.json"
LOCAL_ASSETS_PATH = Path(__file__).parent / "persistent_assets.json"

_assets: Optional[dict] = None
_fonts:  dict = {}

def get_assets() -> dict:
    global _assets
    if _assets is not None:
        return _assets

    # 1. Try local file first (fastest)
    if LOCAL_ASSETS_PATH.exists():
        log.info("Loading assets from local file...")
        with open(LOCAL_ASSETS_PATH) as f:
            _assets = json.load(f)
        log.info("Assets loaded from local file ✓")
        return _assets

    # 2. Download from GitHub
    log.info("Downloading assets from GitHub...")
    import urllib.request
    try:
        with urllib.request.urlopen(GITHUB_ASSETS_URL, timeout=60) as resp:
            _assets = json.loads(resp.read().decode())
        # Cache locally for subsequent requests
        with open(LOCAL_ASSETS_PATH, 'w') as f:
            json.dump(_assets, f)
        log.info("Assets downloaded and cached ✓")
        return _assets
    except Exception as e:
        raise RuntimeError(f"Failed to load assets from GitHub: {e}")

# Pre-load assets at startup
@app.on_event("startup")
async def startup_event():
    try:
        get_assets()
        get_fonts(get_assets())
        log.info("Startup complete — assets and fonts ready ✓")
    except Exception as e:
        log.error(f"Startup error: {e}")

# ── Font loading ───────────────────────────────────────────────────────────────
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from PIL import Image
import numpy as np, cairosvg

def load_font(b64: str, suffix: str = ".ttf") -> dict:
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(base64.b64decode(b64)); tmp.close()
    tt = TTFont(tmp.name)
    return dict(upm=tt['head'].unitsPerEm, cmap=tt.getBestCmap(),
                hmtx=tt['hmtx'], gs=tt.getGlyphSet(), _tmp=tmp.name)

def get_fonts(assets: dict) -> dict:
    if not _fonts:
        _fonts['NF']  = load_font(assets['engoerg'])
        _fonts['RKF'] = load_font(assets['rockwell_regular'], '.otf')
        _fonts['GIF'] = load_font(assets['georgia_italic'])
        log.info("Fonts loaded ✓")
    return _fonts

# ── Constants ──────────────────────────────────────────────────────────────────
UPP=2540/72; CX=7400; MAX_W=11666; NAME_BASE=10500-419.171
NAME_FILL="#000066"; ID_SIZE=282.22; DATE_SIZE=352.77
SIG2_CX=10516; SIG2_LOCAL_CX=SIG2_CX-3201.97
COLLAB_X=10556; COLLAB_Y=1288; COLLAB_W=2489; COLLAB_H=2481
MONTHS=["January","February","March","April","May","June",
        "July","August","September","October","November","December"]

def sfx(n):
    return 'th' if 11<=n<=13 else {1:'st',2:'nd',3:'rd'}.get(n%10,'th')

def _adv(f, ch):
    g = f['cmap'].get(ord(ch))
    if g is None: return int(f['upm']*0.25) if ch==' ' else int(f['upm']*0.4)
    return f['hmtx'][g][0]

def _wem(f, s): return sum(_adv(f, ch) for ch in s)

def outline(f, s, sz, x0, y0, fill, anchor="middle", gid="t"):
    sc=sz/f['upm']; tw=_wem(f,s)*sc; x=(x0-tw/2) if anchor=="middle" else x0; parts=[]
    for ch in s:
        g=f['cmap'].get(ord(ch))
        if g and ch!=' ':
            pen=SVGPathPen(f['gs']); f['gs'][g].draw(pen); d=pen.getCommands()
            if d: parts.append(f'<path d="{d}" transform="translate({x:.3f} {y0:.3f}) scale({sc:.6f} {-sc:.6f})"/>')
        x+=_adv(f,ch)*sc
    return f'<g id="{gid}" fill="{fill}">'+"".join(parts)+"</g>", tw

def fit_pt(NF, s):
    for pt in range(24,13,-1):
        if _wem(NF,s)*(pt*UPP/NF['upm'])<=MAX_W: return pt
    return 14

# ── Regex anchors ──────────────────────────────────────────────────────────────
RECIP_RE     = re.compile(r'<g transform="matrix\(1 0 0 1 -9\.32148 -419\.171\)">.*?</g>', re.S)
CERTID_RE_T1 = re.compile(r'<g transform="matrix\(0\.999997 0 0 0\.999997 -5918\.46 -8978\.04\)">.*?</g>', re.S)
CERTID_RE_T2 = re.compile(r'<g transform="matrix\(0\.999997 0 0 0\.999997 -5918\.46 -6395\.7\)">.*?</g>', re.S)
PROMOTED_RE  = re.compile(r'<g transform="matrix\(1 0 0 1 15\.2385 9990\.25\)">.*?</g>', re.S)
DATE_RE      = re.compile(r'<text[^>]*class="[^"]*fnt0[^"]*"[^>]*>.*?</text>(?:\s*<text[^>]*class="[^"]*fnt0[^"]*"[^>]*>.*?</text>){1,10}', re.S)
SIG2_NAME_RE = re.compile(r'<g transform="matrix\(1 0 0 1 3201\.97 9021\.61\)">.*?</g>', re.S)
SIG2_TITL_RE = re.compile(r'<text[^>]*id="second_Signtory_desigation"[^>]*>.*?</text>', re.S)

# ── Image helpers ──────────────────────────────────────────────────────────────
def remove_white_bg(img_bytes):
    img=Image.open(io.BytesIO(img_bytes)).convert('RGBA'); data=np.array(img)
    r,g,b,a=data[:,:,0],data[:,:,1],data[:,:,2],data[:,:,3]
    br=r.astype(int)+g.astype(int)+b.astype(int)
    data[:,:,3]=np.where(br>680,0,255)
    mid=(br>450)&(br<=680); data[:,:,3][mid]=((680-br[mid])/230*255).astype(np.uint8)
    res=Image.fromarray(data,'RGBA'); buf=io.BytesIO(); res.save(buf,'PNG')
    return base64.b64encode(buf.getvalue()).decode(), res.size[0], res.size[1]

def sig_tag(b64, sw, sh, cx, ytop, w=2970):
    h=int(w*sh/sw); x=int(cx-w/2)
    return (f'<image x="{x}" y="{ytop}" width="{w}" height="{h}" '
            f'preserveAspectRatio="xMidYMid meet" '
            f'xlink:href="data:image/png;base64,{b64}"/>')

def photo_block(pb, x, y, w, h):
    mime="image/png" if pb[:4]==b'\x89PNG' else "image/jpeg"
    uri=f"data:{mime};base64,{base64.b64encode(pb).decode()}"
    return (f'<clipPath id="pc"><rect x="{x}" y="{y}" width="{w}" height="{h}"/></clipPath>'
            f'<g clip-path="url(#pc)"><image x="{x}" y="{y}" width="{w}" height="{h}" '
            f'preserveAspectRatio="xMidYMid slice" xlink:href="{uri}"/></g>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'fill="none" stroke="#000066" stroke-width="20"/>')

def find_close(text, start):
    depth=0; i=start
    while i<len(text):
        if text[i:i+2]=='<g': depth+=1; i+=2
        elif text[i:i+4]=='</g>':
            depth-=1
            if depth==0: return i+4
            i+=4
        else: i+=1
    return -1

def strip_photos(svg, tt):
    svg=re.sub(r'\s*<polygon id="passport_photo_container"[^>]*/>',' ',svg)
    if tt=='T1':
        svg=re.sub(r'\s*<g style="clip-path:url\(#id2\)">.*?</g>',' ',svg,flags=re.S)
        svg=re.sub(r'\s*<polygon class="fil7" points="10595,1808[^"]*"/>',' ',svg)
        svg=re.sub(r'\s*<polygon class="fil1" points="10595,1808[^"]*"/>',' ',svg)
    else:
        svg=re.sub(r'\s*<g style="clip-path:url\(#id3\)">.*?</g>',' ',svg,flags=re.S)
        svg=re.sub(r'\s*<polygon class="fil7" points="6156,1428[^"]*"/>',' ',svg)
    i=svg.find('<g style="clip-path:url(#id4)">')
    if i>=0: j=find_close(svg,i); svg=svg[:i]+' '+svg[j:]
    if tt=='T2':
        i=svg.find('<g style="clip-path:url(#id7)">')
        if i>=0: j=find_close(svg,i); svg=svg[:i]+' '+svg[j:]
    return svg

# ── Build base SVG (shared across all certs in a batch) ───────────────────────
def build_base_svg(assets, fonts, template, sponsored_by,
                   collab_logo_bytes, collab_sig_bytes,
                   collab_signer_name, collab_signer_title):
    NF,RKF,GIF = fonts['NF'],fonts['RKF'],fonts['GIF']
    t2 = (template=='T2')
    svg = base64.b64decode(assets['svg2' if t2 else 'svg1']).decode('utf-8')
    svg = strip_photos(svg, template)

    svg=re.sub(r'xlink:href="[^"]*ImgID1\.png"',f'xlink:href="data:image/png;base64,{assets["trans_logo"]}"',svg)
    svg=re.sub(r'xlink:href="[^"]*ImgID2\.png"',f'xlink:href="data:image/png;base64,{assets["seal"]}"',svg)
    lid = r'ImgID4\.png' if not t2 else r'ImgID3\.png'
    svg=re.sub(rf'xlink:href="[^"]*{lid}"',f'xlink:href="data:image/png;base64,{assets["savan_logo"]}"',svg)

    if t2:
        svg=re.sub(r'xlink:href="[^"]*ImgID6\.png"',f'xlink:href="data:image/png;base64,{assets["savan_logo"]}"',svg)
        if collab_logo_bytes:
            mime="image/png" if collab_logo_bytes[:4]==b'\x89PNG' else "image/jpeg"
            cl=base64.b64encode(collab_logo_bytes).decode()
            cx=COLLAB_X+COLLAB_W//2; cy=COLLAB_Y+COLLAB_H//2; r=min(COLLAB_W,COLLAB_H)//2
            svg=svg.replace('</svg>',
                f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#FFFFCC"/>'
                f'<image x="{COLLAB_X}" y="{COLLAB_Y}" width="{COLLAB_W}" height="{COLLAB_H}" '
                f'preserveAspectRatio="xMidYMid meet" xlink:href="data:{mime};base64,{cl}"/>\n</svg>')

    s1b,s1w,s1h = remove_white_bg(base64.b64decode(assets['sig1']))
    sig1_cx = CX if not t2 else 4100
    svg = svg.replace('</svg>', sig_tag(s1b,s1w,s1h,sig1_cx,17800)+'\n</svg>')

    if t2:
        if collab_sig_bytes:
            s2b,s2w,s2h = remove_white_bg(collab_sig_bytes)
            svg = svg.replace('</svg>', sig_tag(s2b,s2w,s2h,int(SIG2_CX),17800)+'\n</svg>')
        if collab_signer_name:
            cng,_=outline(RKF,collab_signer_name,423.33,SIG2_LOCAL_CX,10500,"black","middle","sig2n")
            svg=SIG2_NAME_RE.sub(f'<g transform="matrix(1 0 0 1 3201.97 9021.61)">{cng}</g>',svg,count=1)
        if collab_signer_title:
            ctg,_=outline(GIF,collab_signer_title,324.56,SIG2_CX,19892,"#373435","middle","sig2t")
            svg=SIG2_TITL_RE.sub(ctg,svg,count=1)

    if sponsored_by and not t2:
        pg,_=outline(GIF,f"Sponsored by {sponsored_by}.",ID_SIZE*0.92,CX,10500,"#000066","middle","promo")
        svg=PROMOTED_RE.sub(f'<g transform="matrix(1 0 0 1 15.2385 9990.25)">{pg}</g>',svg,count=1)
    else:
        svg=PROMOTED_RE.sub('',svg,count=1)

    return svg

# ── Render one certificate ─────────────────────────────────────────────────────
def render_one(base_svg, assets, fonts, name, year, month, session,
               seq, date_str, template, photo_bytes):
    import datetime as dt
    NF,RKF = fonts['NF'],fonts['RKF']
    t2 = (template=='T2')
    d = dt.datetime.strptime(date_str,'%Y-%m-%d')
    date_line = f"{d.day}{sfx(d.day)} of {MONTHS[d.month-1]}, {d.year}."
    cert_id   = f"SAVAN/BLSAED/{year}/{month:02d}{session}/{seq:03d}"
    svg = base_svg

    pt=fit_pt(NF,name)
    ng,_=outline(NF,name,pt*UPP,CX,NAME_BASE,NAME_FILL,"middle","rname")
    svg=RECIP_RE.sub(ng,svg,count=1)

    ig,_=outline(RKF,cert_id,ID_SIZE,7400,10500,"#000066","start","certid")
    cmat="-5918.46 -8978.04" if not t2 else "-5918.46 -6395.7"
    svg=(CERTID_RE_T2 if t2 else CERTID_RE_T1).sub(
        f'<g transform="matrix(0.999997 0 0 0.999997 {cmat})">{ig}</g>',svg,count=1)

    dg,_=outline(RKF,date_line,DATE_SIZE,CX,13327,"black","middle","date")
    svg=DATE_RE.sub(dg,svg,count=1)

    if photo_bytes:
        x,y,w,h=(10344,1525,2989,3285) if not t2 else (5906,1145,2989,3285)
        sx,sy,sw,sh=((10380,1570,3050,3380) if not t2 else (5940,1190,3050,3380))
        shadow=f'<image x="{sx}" y="{sy}" width="{sw}" height="{sh}" preserveAspectRatio="none" xlink:href="data:image/png;base64,{assets["photo_shadow"]}"/>'
        svg=svg.replace('</svg>', shadow+'\n'+photo_block(photo_bytes,x,y,w,h)+'\n</svg>')

    pdf = cairosvg.svg2pdf(bytestring=svg.encode('utf-8'))
    return base64.b64encode(pdf).decode(), cert_id

# ── API Routes ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "savan-render"}

@app.post("/render")
async def render_endpoint(
    params:    str          = Form(...),
    collab_logo: Optional[UploadFile] = File(None),
    collab_sig:  Optional[UploadFile] = File(None),
    photo_0: Optional[UploadFile]=File(None), photo_1: Optional[UploadFile]=File(None),
    photo_2: Optional[UploadFile]=File(None), photo_3: Optional[UploadFile]=File(None),
    photo_4: Optional[UploadFile]=File(None), photo_5: Optional[UploadFile]=File(None),
    photo_6: Optional[UploadFile]=File(None), photo_7: Optional[UploadFile]=File(None),
    photo_8: Optional[UploadFile]=File(None), photo_9: Optional[UploadFile]=File(None),
):
    try: p = json.loads(params)
    except: raise HTTPException(400, "Invalid params JSON")

    try:
        assets = get_assets()
        fonts  = get_fonts(assets)
    except Exception as e:
        raise HTTPException(500, f"Assets error: {e}")

    cl_bytes = await collab_logo.read() if collab_logo else None
    cs_bytes = await collab_sig.read()  if collab_sig  else None
    photos   = [photo_0,photo_1,photo_2,photo_3,photo_4,
                photo_5,photo_6,photo_7,photo_8,photo_9]

    template     = p.get("template","T1")
    sponsored_by = p.get("sponsored_by","")
    col_name     = p.get("collab_signer_name","")
    col_title    = p.get("collab_signer_title","")
    participants = p.get("participants",[])

    base_svg = build_base_svg(assets,fonts,template,sponsored_by,
                              cl_bytes,cs_bytes,col_name,col_title)
    results = []
    for i, part in enumerate(participants):
        ph = None
        if i < len(photos) and photos[i]:
            ph = await photos[i].read()
            if not ph: ph = None
        try:
            pdf_b64, cert_id = render_one(
                base_svg, assets, fonts,
                name=part["name"], year=int(part["year"]),
                month=int(part["month"]), session=int(part["session"]),
                seq=int(part["seq"]), date_str=part["date"],
                template=template, photo_bytes=ph)
            results.append({"cert_id":cert_id,"name":part["name"],
                            "date":part["date"],"pdf_base64":pdf_b64})
        except Exception as e:
            log.error("Render error %s: %s", part.get("name",""), e, exc_info=True)
            results.append({"cert_id":None,"name":part.get("name",""),"error":str(e)})

    return {"certificates": results}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
