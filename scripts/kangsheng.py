#!/usr/bin/env python3
"""One reviewed manifest -> faithful vector copy -> reproducible QA (no upload)."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path
import pymupdf as fitz
import numpy as np
from scipy.ndimage import affine_transform, binary_dilation
from frame import BLUE, PAGE, FRAME, draw_frame_and_title

VERSION = '1.0.0'
S = 4
EDGE = 2  # 0.5 PDF point at 4x rendering, renderer boundary antialiasing only.
DILATE = 2
MAX_MISSING = .002
HEX_BLUE, HEX_GOLD = '#0642a8', '#d99a00'
RESERVED = [fitz.Rect(488,450,820,564),fitz.Rect(400,493,488,564)]
KINDS = {'view','isometric','pcb','table','performance','note','projection','tolerance'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()


def inventory_hash(cfg):
    # Review is the only excluded key. Source paths, assets, fields and geometry are bound.
    return hashlib.sha256(canonical({k:v for k,v in cfg.items() if k!='review'})).hexdigest()


def resolve(base, value):
    p=Path(value).expanduser()
    return p.resolve() if p.is_absolute() else (base/p).resolve()


def compact(value):
    return ''.join(unicodedata.normalize('NFKC',str(value)).split())


def rect(value):
    require(len(value)==4 and all(math.isfinite(float(x)) for x in value),'Invalid rectangle')
    r=fitz.Rect(value)
    require(r.width>0 and r.height>0,'Empty or inverted rectangle')
    return r


def union_box(boxes):
    result=fitz.Rect(boxes[0])
    for box in boxes[1:]: result |= fitz.Rect(box)
    return result


def placements(cfg):
    for group in cfg['groups']:
        origin=union_box(group['clips'])
        scale=float(group['scale'])
        for i,box in enumerate(group['clips']):
            r=fitz.Rect(box)
            x=group['dst'][0]+(r.x0-origin.x0)*scale
            y=group['dst'][1]+(r.y0-origin.y0)*scale
            yield {'id':group['id'],'piece':i,'kind':group['kind'],'source_box':list(r),
                   'target_box':[x,y,x+r.width*scale,y+r.height*scale],'scale':scale}


def read_manifest(path,check_review=True):
    path=Path(path).resolve(); cfg=json.loads(path.read_text())
    require(cfg.get('schema_version')==1,'schema_version must equal 1')
    source=resolve(path.parent,cfg['source']['path'])
    require(source.is_file(),'Source PDF missing')
    require(digest(source)==cfg['source']['sha256'],'Source hash changed; inspect again')
    require(cfg['source'].get('rotation') in [0,90,180,270],'Explicit final source rotation required')
    ident=cfg['identity']; fields=cfg['fields']
    require(ident['expected_model'] and ident['observed_model'],'Both expected/observed source model required')
    require(compact(ident['expected_model'])==compact(ident['observed_model'])==compact(fields['model']),
            'Identity conflict: expected model, source model and output model differ')
    require(fields['model']==fields['model'].strip() and not re.search(r'\s{2,}|[\t\r\n]',fields['model']),
            'Output model has leading/trailing/repeated whitespace; preserve legitimate single spaces')
    require(ident.get('model_evidence'),'Source model evidence box/note required')
    parts=ident.get('observed_parts',[])
    if parts:
        require(ident.get('part_pattern'),'Observed parts need expected part_pattern')
        require(all(re.fullmatch(ident['part_pattern'],p) for p in parts),
                'Part identity conflict; do not borrow a related model')
        require(len(parts)==len(set(parts)),'Duplicate observed part IDs')
    elif ident.get('no_part_table_reason') is None:
        raise ValueError('Declare original parts or an explicit no_part_table_reason')
    require(fields.get('title'),'Product title missing')
    for key in ['scale_text','unit','sheet','tolerances']:
        require(key in fields,f'Original title field must be explicitly reviewed: {key}')
    require(isinstance(fields['tolerances'],list) and len(fields['tolerances'])<=4,
            'Frame supports at most four tolerance rows; copy longer original tolerance block as a group')
    small_font=fitz.Font(fontname='helv')
    small_values=[fields[k] for k in ['scale_text','unit','sheet']]+[fields.get('revision','')]+fields['tolerances']
    require(all(small_font.has_glyph(ord(c)) for value in small_values for c in value),
            'A footer field has unsupported glyphs; supply a reviewed supported font/layout, do not substitute characters')
    for value,width in [(fields['scale_text'],52),(fields['unit'],30),(fields['sheet'],40),(fields.get('revision',''),20)]:
        require(small_font.text_length(value,fontsize=6.5)<=width,'Footer field overflows its cell')
    require(all(small_font.text_length(v,fontsize=7)<=76 for v in fields['tolerances']),'Tolerance text overflows its cell')
    require(cfg.get('groups'),'No content groups')
    ids=[g['id'] for g in cfg['groups']]
    require(len(ids)==len(set(ids)),'Group IDs must be unique')
    for g in cfg['groups']:
        require(g['kind'] in KINDS and g['clips'],f'Invalid group {g["id"]}')
        require(0<float(g['scale'])<=4 and len(g['dst'])==2,'Invalid uniform placement scale/point')
        if g['kind'] in {'view','pcb'}:
            require(abs(float(g['scale'])-1)<1e-9, 'Dimensional view/PCB scale must remain 1.0; preserve source physical scale')
        for b in g['clips']: rect(b)
        for i,a in enumerate(g['clips']):
            for b in g['clips'][i+1:]:
                require((fitz.Rect(a)&fitz.Rect(b)).is_empty,'Same-group clips must not duplicate content')
    coverage=cfg['coverage']
    require(coverage['include'],'An independently reviewed source technical envelope is required')
    for box in coverage['include']: rect(box)
    for ex in coverage.get('exclude',[]):
        rect(ex['box']);require(bool(ex.get('reason')),'Every coverage exclusion needs a reason')
    assets={k:str(resolve(path.parent,v)) for k,v in cfg['assets'].items()}
    for key in ['background','brand_strip','font']:
        require(key in assets and Path(assets[key]).is_file(),f'Asset missing: {key}')
    font=fitz.Font(fontfile=assets['font'])
    require(all(font.has_glyph(ord(c)) for c in fields['model']+fields['title']),
            'Title font lacks model/title glyphs; embedding a different font is required')
    require(font.text_length(fields['title'],fontsize=11)<=113,'Product title overflows its reserved cell')
    if check_review:
        review=cfg.get('review',{})
        require(review.get('verdict')=='PASS' and review.get('reviewer'), 'Source inventory review is required')
        require(review.get('source_sha256')==cfg['source']['sha256'],'Review/source hash mismatch')
        require(review.get('inventory_sha256')==inventory_hash(cfg),'Inventory changed after review')
    return cfg,path,source,assets


def classify_color(rgb):
    r,g,b=rgb
    if min(rgb)>=.96: return rgb
    if max(rgb)-min(rgb)<.10 or (b>r+.08 and b>=g): return BLUE
    return (217/255,154/255,0)


def svg_recolor(svg):
    def color(m):
        raw=m.group()[1:]
        if len(raw)==3:raw=''.join(c*2 for c in raw)
        rgb=tuple(int(raw[i:i+2],16)/255 for i in [0,2,4])
        return '#'+''.join(f'{round(v*255):02x}' for v in classify_color(rgb))
    svg=re.sub(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b',color,svg)
    return svg.replace('<svg ',f'<svg fill="{HEX_BLUE}" ',1)


def native_recolor(source,target):
    # A PDF parser, not regex replacement: preserves all text/font and geometry operators.
    import pikepdf
    pdf=pikepdf.open(source);seen=set()
    def convert(obj,resources,root=False):
        instructions=[]
        if root:
            instructions.extend([([*BLUE],pikepdf.Operator('rg')),([*BLUE],pikepdf.Operator('RG'))])
        for inst in pikepdf.parse_content_stream(obj):
            require(isinstance(inst,pikepdf.ContentStreamInstruction),'Inline image requires SVG fallback')
            args,op=inst.operands,str(inst.operator)
            require(op not in {'cs','CS','sc','SC','scn','SCN'},'Custom/pattern color requires SVG fallback')
            if op in {'rg','RG','g','G','k','K'}:
                nums=[float(x) for x in args]
                if op in {'g','G'}: rgb=(nums[0],)*3
                elif op in {'k','K'}:
                    c,m,y,k=nums;rgb=(1-min(1,c+k),1-min(1,m+k),1-min(1,y+k))
                else: rgb=nums
                instructions.append((list(classify_color(rgb)),pikepdf.Operator('RG' if op.isupper() else 'rg')))
            else: instructions.append(inst)
        data=pikepdf.unparse_content_stream(instructions)
        if isinstance(obj,pikepdf.Page): obj.Contents=pdf.make_stream(data)
        else: obj.write(data)
        for x in resources.get('/XObject',{}).values():
            if x.get('/Subtype')=='/Form' and x.objgen not in seen:
                seen.add(x.objgen);convert(x,x.get('/Resources',resources))
    for p in pdf.pages:convert(p,p.Resources,True)
    pdf.save(target)


def cached_source(cfg,source,cache):
    original=fitz.open(source)
    require(len(original)==cfg['source']['expected_pages'],'Page count changed; review all pages')
    require(len(original)==1,'This single-sheet command requires a reviewed one-page PDF; never silently drops pages')
    require(cfg['source']['page']==1,'One-page manifest page must equal 1')
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(canonical({'sha':digest(source),'page':cfg['source']['page'],
          'rotation':cfg['source']['rotation'],'version':VERSION,'engine':digest(__file__),
          'fitz':fitz.__version__,'renderer':cfg.get('renderer','auto')})).hexdigest()[:24]
    neutral=cache/(key+'-source.pdf');colored=cache/(key+'-blue.pdf');info=cache/(key+'.json')
    if neutral.exists() and colored.exists() and info.exists():
        old=json.loads(info.read_text())
        if digest(neutral)==old['neutral_sha256'] and digest(colored)==old['colored_sha256']:
            return neutral,colored,old['renderer'],True
    # Bake the explicitly inspected display rotation BEFORE source-coordinate clipping.
    p=original[0];p.set_rotation(cfg['source']['rotation']);p.remove_rotation()
    original.save(neutral,garbage=4,deflate=True)
    renderer=cfg.get('renderer','auto')
    require(renderer in {'auto','native','svg'},'Unknown renderer')
    used='native'
    try:
        if renderer=='svg':raise RuntimeError('Explicit SVG path renderer')
        native_recolor(neutral,colored)
    except (ImportError,ValueError,RuntimeError):
        if renderer=='native':raise
        used='svg'
        doc=fitz.open(neutral)
        svg=svg_recolor(doc[0].get_svg_image(text_as_path=True))
        vec=fitz.open('svg',svg.encode());converted=fitz.open('pdf',vec.convert_to_pdf())
        converted.save(colored,garbage=4,deflate=True)
    info.write_text(json.dumps({'renderer':used,'neutral_sha256':digest(neutral),
                                'colored_sha256':digest(colored)},indent=2))
    return neutral,colored,used,False


def check_geometry(cfg,source_page):
    pagebox=source_page.rect
    source_ink=render_array(source_page)[:,:,:3].min(2)<245
    def has_ink(item,region):
        src=fitz.Rect(item['source_box']);dst=fitz.Rect(item['target_box']);sc=item['scale']
        if region.is_empty:return False
        x0=src.x0+(region.x0-dst.x0)/sc;y0=src.y0+(region.y0-dst.y0)/sc
        x1=src.x0+(region.x1-dst.x0)/sc;y1=src.y0+(region.y1-dst.y0)/sc
        return bool(source_ink[max(0,int(math.floor(y0*S))):int(math.ceil(y1*S)),
                               max(0,int(math.floor(x0*S))):int(math.ceil(x1*S))].any())
    def overlap_ink(a,b,region):
        if region.is_empty:return False
        xs,ys=int(math.floor(region.x0*S)),int(math.floor(region.y0*S))
        xe,ye=int(math.ceil(region.x1*S)),int(math.ceil(region.y1*S))
        masks=[]
        for item in [a,b]:
            sc=item['scale'];src=item['source_box'];dst=item['target_box']
            masks.append(affine_transform(source_ink.astype(np.uint8),matrix=np.eye(2)/sc,
                offset=[src[1]*S+(ys-dst[1]*S)/sc,src[0]*S+(xs-dst[0]*S)/sc],
                output_shape=(ye-ys,xe-xs),order=0,cval=0).astype(bool))
        return bool((masks[0]&binary_dilation(masks[1],iterations=2)).any())
    ps=list(placements(cfg));outer=fitz.Rect(FRAME)
    for p in ps:
        src=fitz.Rect(p['source_box']);dst=fitz.Rect(p['target_box'])
        require(pagebox.contains(src),f'Source clip outside page: {p["id"]}')
        require(outer.contains(dst),f'Output clip outside frame: {p["id"]}')
        if p['kind']!='projection':
            require(all(not has_ink(p,dst&r) for r in RESERVED),f'Title/tolerance overlap: {p["id"]}')
    for i,a in enumerate(ps):
        for b in ps[i+1:]:
            overlap=fitz.Rect(a['target_box'])&fitz.Rect(b['target_box'])
            require(overlap.is_empty or overlap.width*overlap.height<1e-7 or not overlap_ink(a,b,overlap),
                    f'Content groups overlap: {a["id"]}/{b["id"]}; reshape blank clip boundary, never trim ink')
    tables=[p for p in ps if p['kind']=='table']
    specs=[p for p in ps if p['kind']=='performance']
    require(tables or cfg['identity'].get('no_part_table_reason'),'Table group missing')
    for p in tables:require(p['target_box'][0]>=PAGE[0]*.62 and p['target_box'][1]<PAGE[1]*.4,'Table must be top-right')
    for p in specs:require(p['target_box'][0]>=PAGE[0]*.62 and p['target_box'][1]>=PAGE[1]*.30,'Performance must be right-middle')
    for h in cfg.get('table_headers',[]):
        hr=rect([h['xs'][0],h['y'][0],h['xs'][-1],h['y'][1]])
        require(outer.contains(hr),'Table header outside frame')
        joined=[t for t in tables if abs(hr.y1-t['target_box'][1])<=1
                and hr.x0>=t['target_box'][0]-2 and hr.x1<=t['target_box'][2]+2]
        require(joined,'Header must adjoin the top edge of its original table, not cover technical data')
        for p in ps:
            overlap=hr & fitz.Rect(p['target_box'])
            if p not in joined:require(not has_ink(p,overlap),'Header covers source technical ink')
    if tables and specs:
        require(max(p['target_box'][3] for p in tables)<min(p['target_box'][1] for p in specs),
                'Performance must be below table, with a real gap')
    return ps


def render_array(page):
    pix=page.get_pixmap(matrix=fitz.Matrix(S,S),alpha=False)
    return np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)


def fill(mask,box):
    x0,y0,x1,y1=box
    mask[max(0,int(math.floor(y0*S))):min(mask.shape[0],int(math.ceil(y1*S))),
         max(0,int(math.floor(x0*S))):min(mask.shape[1],int(math.ceil(x1*S)))]=True


def make_audit(cfg,neutral,output,ps,assets):
    src=fitz.open(neutral);out=fitz.open(output)
    require(len(out)==1 and abs(out[0].rect.width-PAGE[0])<.1 and abs(out[0].rect.height-PAGE[1])<.1,
            'Output is not one landscape A4 page')
    a=render_array(src[0]);ink=a[:,:,:3].min(2)<150
    interest=np.zeros(ink.shape,bool);covered=np.zeros_like(interest)
    for b in cfg['coverage']['include']:fill(interest,b)
    for ex in cfg['coverage'].get('exclude',[]):
        erase=np.zeros_like(interest);fill(erase,ex['box']);interest &= ~erase
    for p in ps:fill(covered,p['source_box'])
    unplaced=int(((a[:,:,:3].min(2)<245)&interest&~covered).sum())
    z=render_array(out[0])[:,:,:3].astype(np.float32)
    # Compare at the SAME alpha threshold as black-on-white source <150.
    # Raw R<110 would reject valid thin blue antialiasing strokes on pale background.
    background_doc=fitz.open();bp=background_doc.new_page(width=PAGE[0],height=PAGE[1])
    bp.insert_image(bp.rect,filename=assets['background'])
    bg=render_array(bp)[:,:,:3]
    alpha_cutoff=1-150/255
    masks=[]
    for rgb in [BLUE,(217/255,154/255,0)]:
        numerator=np.zeros(bg.shape[:2],np.float32);denominator=np.zeros_like(numerator)
        for channel in range(3):
            base=bg[:,:,channel].astype(np.float32);direction=base-rgb[channel]*255
            numerator+=(base-z[:,:,channel])*direction;denominator+=direction*direction
        alpha=numerator/np.maximum(denominator,1)
        residual=np.zeros_like(alpha)
        for channel in range(3):
            base=bg[:,:,channel].astype(np.float32)
            residual=np.maximum(residual,np.abs(z[:,:,channel]-(base-alpha*(base-rgb[channel]*255))))
        masks.append((alpha>alpha_cutoff)&(residual<25))
    act_all=masks[0]|masks[1]
    weak_ink=a[:,:,:3].min(2)<245
    furniture=np.zeros(act_all.shape,bool)
    for b in RESERVED:fill(furniture,list(b))
    for h in cfg.get('table_headers',[]):fill(furniture,[h['xs'][0],h['y'][0],h['xs'][-1],h['y'][1]])
    furniture=binary_dilation(furniture,iterations=2)  # approved frame stroke antialiasing outside its geometric centerline
    expected_union=np.zeros(act_all.shape,bool)
    for item in ps:
        u0,v0,u1,v1=item['target_box'];sx,sy,_,_=item['source_box'];sc=item['scale']
        xs,ys=int(math.floor(u0*S)),int(math.floor(v0*S));xe,ye=int(math.ceil(u1*S)),int(math.ceil(v1*S))
        weak=affine_transform(weak_ink.astype(np.uint8),matrix=np.eye(2)/sc,
            offset=[sy*S+(ys-v0*S)/sc,sx*S+(xs-u0*S)/sc],
            output_shape=(ye-ys,xe-xs),order=0,cval=0).astype(bool)
        expected_union[ys:ye,xs:xe] |= weak
    expected_allowed=binary_dilation(expected_union,iterations=DILATE)
    checks=[]
    for p in ps:
        x0,y0,x1,y1=p['source_box'];u0,v0,u1,v1=p['target_box'];sc=p['scale']
        xs,ys=int(math.floor(u0*S)),int(math.floor(v0*S));xe,ye=int(math.ceil(u1*S)),int(math.ceil(v1*S))
        exp=affine_transform(ink.astype(np.uint8),matrix=np.eye(2)/sc,
            offset=[y0*S+(ys-v0*S)/sc,x0*S+(xs-u0*S)/sc],
            output_shape=(ye-ys,xe-xs),order=0,cval=0).astype(bool)
        act=act_all[ys:ye,xs:xe]
        exp[:EDGE]=False;exp[-EDGE:]=False;exp[:,:EDGE]=False;exp[:,-EDGE:]=False
        missing=exp&~binary_dilation(act,iterations=DILATE)
        extra=act & ~expected_allowed[ys:ye,xs:xe] & ~furniture[ys:ye,xs:xe]
        extra[:EDGE]=False;extra[-EDGE:]=False;extra[:,:EDGE]=False;extra[:,-EDGE:]=False
        count=int(exp.sum());ratio=float(missing.sum()/max(1,count))
        extra_ratio=float(extra.sum()/max(1,count))
        checks.append({'id':p['id'],'piece':p['piece'],'expected_ink_pixels':count,
          'missing_pixels':int(missing.sum()),'missing_ratio':ratio,
          'unexpected_ink_pixels':int(extra.sum()),'unexpected_ink_ratio':extra_ratio,
          'pass':count>0 and ratio<MAX_MISSING and extra_ratio<MAX_MISSING})
    # Technical source content can be paths; title must remain searchable embedded text.
    text=compact(out[0].get_text())
    title_ok=all(compact(cfg['fields'][k]) in text for k in ['title','model'])
    ok=unplaced==0 and title_ok and all(c['pass'] for c in checks)
    return {'pass':ok,'scope':'Raster ink preservation is not engineering certification; independent full-sheet/number review remains required.',
       'source_sha256':cfg['source']['sha256'],'output_sha256':digest(output),
       'inventory_sha256':inventory_hash(cfg),'source_unplaced_technical_ink_pixels':unplaced,
       'title_model_text_present':title_ok,'mask_scale':S,'edge_pixels':EDGE,'dilation_pixels':DILATE,
       'max_missing_ratio_exclusive':MAX_MISSING,'color_mask':'background-relative blue/gold alpha; source-equivalent cutoff 1-150/255', 'checks':checks}


def draw_table_headers(page,cfg):
    # Only labels/grid. No row data, Part IDs, dimensions or values are accepted here.
    for header in cfg.get('table_headers',[]):
        labels=header['labels'];xs=header['xs'];top,bottom=header['y']
        require(all(x in {'PART NO.','DIM A','DIM B','DIM C','PIN'} for x in labels),'Only canonical table header labels are allowed')
        require(len(xs)==len(labels)+1 and all(a<b for a,b in zip(xs,xs[1:])),'Header columns invalid')
        page.draw_rect(fitz.Rect(xs[0],top,xs[-1],bottom),color=BLUE,width=.65)
        for x in xs[1:-1]:page.draw_line((x,top),(x,bottom),color=BLUE,width=.55)
        for i,label in enumerate(labels):
            ret=page.insert_textbox(fitz.Rect(xs[i],top+5,xs[i+1],bottom),label,fontsize=8,fontname='hebo',align=1,color=BLUE)
            require(ret>=0,'Header label overflow')


def save_json(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')


def build(args):
    start=time.perf_counter();cfg,mp,source,assets=read_manifest(args.manifest)
    outdir=Path(args.output).resolve()
    require(not any((outdir/name).exists() for name in ['drawing.pdf','candidate.pdf','audit.json','layout.json','preview.png','verify.json']),
            'Output directory contains prior artifacts; choose a new output directory to avoid stale delivery')
    outdir.mkdir(parents=True,exist_ok=True)
    neutral,colored,renderer,hit=cached_source(cfg,source,args.cache or outdir/'.cache')
    n=fitz.open(neutral);ps=check_geometry(cfg,n[0])
    d=fitz.open();p=d.new_page(width=PAGE[0],height=PAGE[1])
    p.insert_image(p.rect,filename=assets['background'])
    color=fitz.open(colored)
    for item in ps:
        if item['kind']!='projection':p.show_pdf_page(fitz.Rect(item['target_box']),color,0,clip=fitz.Rect(item['source_box']))
    draw_table_headers(p,cfg)
    fields=dict(cfg['fields']);fields['cjk_font_file']=assets['font']
    draw_frame_and_title(p,fields,assets)
    for item in ps:
        if item['kind']=='projection':p.show_pdf_page(fitz.Rect(item['target_box']),color,0,clip=fitz.Rect(item['source_box']))
    d.subset_fonts()
    d.set_metadata({'title':fields['model']+' | 康生图纸','subject':'Technical content copied from a hash-bound supplier source, not retyped.'})
    output=outdir/'drawing.pdf';tmp=outdir/'candidate.pdf'
    d.save(tmp,garbage=4,deflate=True,deflate_fonts=True,deflate_images=True,use_objstms=1)
    audit=make_audit(cfg,neutral,tmp,ps,assets);audit['generation_seconds']=round(time.perf_counter()-start,3)
    audit['renderer']=renderer;audit['source_cache_hit']=hit
    save_json(outdir/'audit.json',audit)
    require(audit['pass'],'Automatic preservation QA failed. candidate.pdf retained for diagnosis; no accepted drawing emitted')
    tmp.replace(output)
    p.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(outdir/'preview.png')
    save_json(outdir/'layout.json',{'manifest_sha256':digest(mp),'source_sha256':digest(source),
        'output_sha256':digest(output),'placements':ps,'fields':cfg['fields'],'renderer':renderer})
    print(json.dumps({'status':'AUTO_QA_PASS_REQUIRES_VISUAL_REVIEW','pdf':str(output),'audit':str(outdir/'audit.json'),
                      'seconds':audit['generation_seconds'],'renderer':renderer,'cache_hit':hit},ensure_ascii=False))


def verify(args):
    cfg,mp,source,assets=read_manifest(args.manifest)
    output=Path(args.pdf).resolve()
    neutral,_,_,_=cached_source(cfg,source,args.cache or output.parent/'.cache')
    doc=fitz.open(neutral);ps=check_geometry(cfg,doc[0])
    audit=make_audit(cfg,neutral,output,ps,assets)
    audit['visual_review_pass']=False
    if args.review:
        review=json.loads(Path(args.review).read_text())
        require(review.get('source_sha256')==digest(source) and review.get('output_sha256')==digest(output)
            and review.get('inventory_sha256')==inventory_hash(cfg),'Visual review evidence hash mismatch')
        require(review.get('verdict')=='PASS' and review.get('reviewer') and review.get('checks'), 'Incomplete visual review evidence')
        audit['visual_review_pass']=True
    save_json(args.report or output.parent/'verify.json',audit)
    require(audit['pass'],'Verification failed')
    print(json.dumps({'automatic_pass':True,'visual_review_pass':audit['visual_review_pass'],
                      'release_ready':audit['visual_review_pass']},ensure_ascii=False))


def init(args):
    source=Path(args.source).resolve();out=Path(args.manifest).resolve();doc=fitz.open(source)
    page=doc[0];page.set_rotation(args.rotation);page.remove_rotation()
    root=Path(__file__).resolve().parents[1]
    cfg={'schema_version':1,'source':{'path':str(source),'sha256':digest(source),'page':1,
        'rotation':args.rotation,'expected_pages':len(doc)},'renderer':'auto',
        'identity':{'expected_model':args.model,'observed_model':'','model_evidence':'','observed_parts':[],
                    'part_pattern':''},
        'fields':{'model':args.model,'title':'','scale_text':'','unit':'','sheet':'','tolerances':[]},
        'assets':{'background':str(root/'assets/background.png'),'brand_strip':str(root/'assets/brand-strip.png'),'font':''},
        'groups':[], 'coverage':{'include':[list(page.rect)],'exclude':[]},
        'review':{'verdict':'REVIEW','reviewer':'','source_sha256':digest(source),'inventory_sha256':''}}
    out.parent.mkdir(parents=True,exist_ok=True);save_json(out,cfg)
    page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(out.parent/(out.stem+'-source.png'))
    print('Draft manifest and source preview created. Inspect original identity and all content before build.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('init');p.add_argument('source');p.add_argument('--manifest',required=True);p.add_argument('--model',required=True);p.add_argument('--rotation',type=int,choices=[0,90,180,270],required=True);p.set_defaults(func=init)
    p=subs.add_parser('build');p.add_argument('manifest');p.add_argument('--output',required=True);p.add_argument('--cache');p.set_defaults(func=build)
    p=subs.add_parser('verify');p.add_argument('manifest');p.add_argument('pdf');p.add_argument('--cache');p.add_argument('--review');p.add_argument('--report');p.set_defaults(func=verify)
    p=subs.add_parser('inventory-hash');p.add_argument('manifest');p.set_defaults(func=lambda a: print(inventory_hash(json.loads(Path(a.manifest).read_text()))))
    args=parser.parse_args()
    try:args.func(args)
    except (ValueError,KeyError,FileNotFoundError) as exc:
        print('STOP: '+str(exc),file=sys.stderr);return 2
    return 0


if __name__=='__main__':sys.exit(main())
