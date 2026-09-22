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
from scipy.ndimage import affine_transform, binary_dilation, label, find_objects
from frame import (BLUE, PAGE, FRAME, TITLE_BOX, TOLERANCE_BOX, PROJECTION_BOX,
                   draw_frame_and_title)

VERSION = '2.0.0'
S = 4
EDGE = 2  # 0.5 PDF point at 4x rendering, renderer boundary antialiasing only.
DILATE = 2
MAX_MISSING = .002
MIN_EFFECTIVE_FONT_PT = 4.75
MIN_BRAND_DPI = 150
# Exact native asset retained from the user-approved Kangsheng footer. This is
# an identity-bound compatibility profile, not a lower general DPI threshold.
APPROVED_NATIVE_BRAND_SHA256 = '436ab38839ec933f3c295c9b17005cb8942b16245715073c7e69da1bd5d16078'
APPROVED_NATIVE_BRAND_SIZE = (597,92)
GLOBAL_PIXEL_TOLERANCE = 16
HEX_BLUE, HEX_GOLD = '#0642a8', '#d99a00'
RESERVED = [fitz.Rect(TITLE_BOX),fitz.Rect(TOLERANCE_BOX)]
KINDS = {'view','isometric','pcb','table','performance','note','projection','tolerance'}
EXCLUSION_KINDS = {'outer_frame','supplier_title_block','watermark','replaced_title_field','nontechnical_annotation'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def brand_profile(path):
    pix=fitz.Pixmap(str(path))
    sha256=digest(path)
    dpi=pix.width*.976/(330/72)
    approved=(sha256==APPROVED_NATIVE_BRAND_SHA256
              and (pix.width,pix.height)==APPROVED_NATIVE_BRAND_SIZE)
    return {'profile':'approved-native-original' if approved else 'standard',
            'sha256':sha256,'width_px':pix.width,'height_px':pix.height,
            'effective_dpi':round(dpi,1),'minimum_dpi':MIN_BRAND_DPI,
            'normal_minimum_dpi_met':dpi>=MIN_BRAND_DPI,
            'approved_source':'User-approved Kangsheng footer, rows 14-18; original native asset' if approved else None,
            'pass':approved or dpi>=MIN_BRAND_DPI}


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()


def inventory_hash(cfg):
    # Review is the only excluded key. Source paths, assets, fields and geometry are bound.
    return hashlib.sha256(canonical({k:v for k,v in cfg.items() if k!='review'})).hexdigest()


def source_inventory_hash(cfg):
    """Bind source meaning and crops, but not output placement or local assets.

    A source reviewer should not have to repeat a full-sheet inventory review when
    only dst / scale / brand assets change.  Final review still binds the complete
    recipe hash and output hash.
    """
    source={k:v for k,v in cfg['source'].items() if k!='path'}
    groups=[]
    for group in cfg.get('groups',[]):
        groups.append({k:group[k] for k in (
            'id','kind','clips','reviewed_source_extent','restored_source_rules'
        ) if k in group})
    value={'source':source,'identity':cfg.get('identity',{}),'fields':cfg.get('fields',{}),
           'groups':groups,'coverage':cfg.get('coverage',{}),
           'table_headers':cfg.get('table_headers',[])}
    return hashlib.sha256(canonical(value)).hexdigest()


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


def restored_rules(cfg):
    """Yield reviewer-measured table closing rules in source and target space."""
    for group in cfg['groups']:
        if not group.get('restored_source_rules'):
            continue
        origin=union_box(group['clips']);scale=float(group['scale'])
        for rule in group['restored_source_rules']:
            width=float(rule['width'])
            if rule['orientation']=='vertical':
                x=float(rule['x']);y0=float(rule['y0']);y1=float(rule['y1'])
                yield {'id':group['id'],'kind':group['kind'],'source_box':[x-width/2,y0,x+width/2,y1],
                       'target_line':((group['dst'][0]+(x-origin.x0)*scale,
                                       group['dst'][1]+(y0-origin.y0)*scale),
                                      (group['dst'][0]+(x-origin.x0)*scale,
                                       group['dst'][1]+(y1-origin.y0)*scale)),
                       'width':width*scale,'color':rule.get('color','gold')}
            else:
                y=float(rule['y']);x0=float(rule['x0']);x1=float(rule['x1'])
                yield {'id':group['id'],'kind':group['kind'],'source_box':[x0,y-width/2,x1,y+width/2],
                       'target_line':((group['dst'][0]+(x0-origin.x0)*scale,
                                       group['dst'][1]+(y-origin.y0)*scale),
                                      (group['dst'][0]+(x1-origin.x0)*scale,
                                       group['dst'][1]+(y-origin.y0)*scale)),
                       'width':width*scale,'color':rule.get('color','gold')}


def draw_restored_rules(page,cfg):
    for rule in restored_rules(cfg):
        color=BLUE if rule['color']=='blue' else (217/255,154/255,0)
        page.draw_line(*rule['target_line'],color=color,width=rule['width'])


def read_manifest(path,check_review=True):
    path=Path(path).resolve(); cfg=json.loads(path.read_text())
    schema=cfg.get('schema_version')
    require(schema in {1,2},'schema_version must equal 1 or 2')
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
    if schema>=2:
        require(not fields['tolerances'],
                'Manifest v2 does not retype source tolerance values; preserve them as a tolerance group')
        tolerance_groups=[g for g in cfg.get('groups',[]) if g.get('kind')=='tolerance']
        require(len(tolerance_groups)<=1,'Manifest v2 uses one complete source tolerance group')
        require(tolerance_groups
                or fields.get('no_tolerance_block_reason'),
                'Manifest v2 needs a source tolerance group or no_tolerance_block_reason')
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
        # This is a reviewer-declared technical envelope from the full, uncropped
        # source page.  It prevents an operator from silently narrowing a clip
        # (for example, cutting the final column of a part table) after review.
        extent=rect(g.get('reviewed_source_extent',[]))
        clip_union=union_box(g['clips'])
        repaired_rules=g.get('restored_source_rules',[])
        require(len(repaired_rules)<=2,f'At most two reviewed closing rules are allowed: {g["id"]}')
        require(len({r.get('orientation') for r in repaired_rules})==len(repaired_rules),
                f'Duplicate restored rule orientation: {g["id"]}')
        if not clip_union.contains(extent):
            # A table/tolerance outer rule can coincide with an unrelated
            # source-page frame. In that narrow case, keep the technical crop
            # clean and restore only the reviewer-measured rule as a vector line.
            require(g['kind'] in {'table','tolerance'} and repaired_rules,
                    f'Reviewed source extent is not fully placed: {g["id"]}')
            require(extent.x0>=clip_union.x0 and extent.y0>=clip_union.y0
                    and -2<=extent.x1-clip_union.x1<=2 and 0<=extent.y1-clip_union.y1<=2
                    and (extent.x1>clip_union.x1 or extent.y1>clip_union.y1),
                    f'Reviewed source extent is not fully placed: {g["id"]}')
            orientations={r.get('orientation') for r in repaired_rules}
            require((extent.x1<=clip_union.x1 or 'vertical' in orientations)
                    and (extent.y1<=clip_union.y1 or 'horizontal' in orientations),
                    f'Reviewed source extent is not fully placed: {g["id"]}')
        for rule in repaired_rules:
            require(g['kind'] in {'table','tolerance'} and rule.get('orientation') in {'vertical','horizontal'},
                    f'Only a reviewed table/tolerance rule may be restored: {g["id"]}')
            width=float(rule['width'])
            require(width>0 and rule.get('color','gold') in {'gold','blue'},'Invalid restored source rule: '+g['id'])
            if rule['orientation']=='vertical':
                x=float(rule['x']);y0=float(rule['y0']);y1=float(rule['y1'])
                require(extent.x0<=x<=extent.x1 and extent.y0<=y0<y1<=extent.y1
                        and clip_union.x1<=x<=extent.x1+1e-6,
                        f'Invalid restored source rule: {g["id"]}')
            else:
                y=float(rule['y']);x0=float(rule['x0']);x1=float(rule['x1'])
                require(extent.y0<=y<=extent.y1 and extent.x0<=x0<x1<=extent.x1
                        and clip_union.y1<=y<=extent.y1+1e-6,
                        f'Invalid restored source rule: {g["id"]}')
        for i,a in enumerate(g['clips']):
            for b in g['clips'][i+1:]:
                require((fitz.Rect(a)&fitz.Rect(b)).is_empty,'Same-group clips must not duplicate content')
    coverage=cfg['coverage']
    if schema>=2:
        require(coverage.get('mode')=='full-page-minus-exclusions',
                'Manifest v2 coverage must use full-page-minus-exclusions')
        if coverage.get('include'):
            source_doc=fitz.open(source);source_page=source_doc[0]
            source_page.set_rotation(cfg['source']['rotation'])
            expected=source_page.rect
            includes=coverage['include']
            require(len(includes)==1 and fitz.Rect(includes[0]).contains(expected)
                    and expected.contains(fitz.Rect(includes[0])),
                    'Manifest v2 coverage.include, when present, must equal the complete rotated page')
    else:
        require(coverage['include'],'An independently reviewed source technical envelope is required')
        for box in coverage['include']: rect(box)
    for ex in coverage.get('exclude',[]):
        rect(ex['box']);require(bool(ex.get('reason')),'Every coverage exclusion needs a reason')
        if schema>=2:
            require(ex.get('kind') in EXCLUSION_KINDS,
                    'Manifest v2 exclusions need a restricted nontechnical kind')
    # A reviewed nontechnical source-furniture exclusion is also a hard clip
    # boundary. Otherwise a broad crop can leak DATE/title-frame fragments
    # into a tolerance or projection group while raster preservation passes.
    for g in cfg['groups']:
        for b in g['clips']:
            for ex in coverage.get('exclude',[]):
                require((fitz.Rect(b) & fitz.Rect(ex['box'])).is_empty,
                        f'Group enters excluded source furniture: {g["id"]}')
    assets={k:str(resolve(path.parent,v)) for k,v in cfg['assets'].items()}
    for key in ['background','brand_strip','font']:
        require(key in assets and Path(assets[key]).is_file(),f'Asset missing: {key}')
    brand=brand_profile(assets['brand_strip'])
    require(brand['pass'],
            f'Brand strip effective resolution is {brand["effective_dpi"]:.0f} DPI; minimum is {MIN_BRAND_DPI} DPI '
            '(only the hash-bound approved native original has an exception)')
    font=fitz.Font(fontfile=assets['font'])
    require(all(font.has_glyph(ord(c)) for c in fields['model']+fields['title']),
            'Title font lacks model/title glyphs; embedding a different font is required')
    require(font.text_length(fields['title'],fontsize=11)<=113,'Product title overflows its reserved cell')
    if check_review:
        review=cfg.get('review',{})
        require(review.get('verdict')=='PASS' and review.get('reviewer'), 'Source inventory review is required')
        require(review.get('reviewer_role')=='source_inventory_reviewer',
                'Source inventory must name a source_inventory_reviewer')
        require(review.get('coverage_basis')=='uncropped-full-sheet',
                'Coverage must be reviewed from the uncropped full source page')
        require(review.get('full_page_reviewed') is True,
                'Source inventory must record a full-page review')
        require(review.get('source_sha256')==cfg['source']['sha256'],'Review/source hash mismatch')
        if schema>=2:
            require(bool(review.get('reviewer_run_id')),
                    'Manifest v2 source review needs an independent reviewer_run_id')
            require(review.get('source_inventory_sha256')==source_inventory_hash(cfg),
                    'Source clips or inventory changed after review')
        else:
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


def check_geometry(cfg,source_page,source_pixels=None):
    pagebox=source_page.rect
    source_pixels=render_array(source_page) if source_pixels is None else source_pixels
    source_ink=source_pixels[:,:,:3].min(2)<245
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
    words=[(fitz.Rect(w[:4]),str(w[4])) for w in source_page.get_text('words') if str(w[4]).strip()]
    spans=[]
    for block in source_page.get_text('dict').get('blocks',[]):
        for line in block.get('lines',[]):
            for span in line.get('spans',[]):
                if span.get('text','').strip():
                    spans.append((fitz.Rect(span['bbox']),span['text'],float(span['size'])))
    excluded=[fitz.Rect(ex['box']) for ex in cfg['coverage'].get('exclude',[])]
    def covered_by(rectangle,clips,padding=.75):
        expanded=[fitz.Rect(c.x0-padding,c.y0-padding,c.x1+padding,c.y1+padding) for c in clips]
        # PyMuPDF < 1.23 has no Rect.get_area(); keep this compatible with
        # the system runtime used by the installed skill.
        intersections=(c & rectangle for c in expanded)
        covered=sum(max(0.0, part.width) * max(0.0, part.height) for part in intersections)
        target_area=max(0.0, rectangle.width) * max(0.0, rectangle.height)
        return covered+0.1>=target_area
    source_quality=[]
    for group in cfg['groups']:
        clips=[fitz.Rect(box) for box in group['clips']]
        extent=fitz.Rect(group['reviewed_source_extent'])
        # Ink carried outside the reviewer-declared technical extent is source
        # furniture / neighbouring content, not harmless crop padding.
        clip_mask=np.zeros(source_ink.shape,bool);extent_mask=np.zeros_like(clip_mask)
        for box in clips:fill(clip_mask,list(box))
        fill(extent_mask,list(extent))
        outside=source_ink & clip_mask & ~binary_dilation(extent_mask,iterations=EDGE)
        outside_count=int(outside.sum())
        require(outside_count==0,
                f'Clip carries ink outside reviewed source extent: {group["id"]} ({outside_count} pixels)')
        # Reject partial words at a crop edge. This catches DATE / MODEL / final
        # column fragments even when a broad raster coverage declaration is wrong.
        for word,text_value in words:
            if any(e.contains(word) for e in excluded):
                continue
            if any(not (clip & word).is_empty for clip in clips):
                contained=covered_by(word,clips)
                require(contained,f'Source clip cuts text in {group["id"]}: {text_value!r}')
        effective=[]
        for box,text_value,size in spans:
            if covered_by(box,clips):
                effective.append((size*float(group['scale']),text_value))
        minimum=min((x[0] for x in effective),default=None)
        if minimum is not None:
            require(minimum>=MIN_EFFECTIVE_FONT_PT,
                    f'Effective technical text below {MIN_EFFECTIVE_FONT_PT:g}pt in {group["id"]}: {minimum:.2f}pt')
        source_quality.append({'id':group['id'],'clip_extra_ink_pixels':outside_count,
            'minimum_effective_font_pt':None if minimum is None else round(minimum,3),
            'extractable_text_spans':len(effective)})
    for p in ps:
        src=fitz.Rect(p['source_box']);dst=fitz.Rect(p['target_box'])
        require(pagebox.contains(src),f'Source clip outside page: {p["id"]}')
        dx=(outer.x0-dst.x0 if dst.x0<outer.x0 else outer.x1-dst.x1 if dst.x1>outer.x1 else 0)
        dy=(outer.y0-dst.y0 if dst.y0<outer.y0 else outer.y1-dst.y1 if dst.y1>outer.y1 else 0)
        require(outer.contains(dst),
                f'Output clip outside frame: {p["id"]}; move dst by ({dx:.1f}, {dy:.1f}) points')
        if p['kind']=='projection':
            require(fitz.Rect(PROJECTION_BOX).contains(dst),
                    f'Projection must fit its bottom-right cell: {p["id"]}')
        elif cfg.get('schema_version',1)>=2 and p['kind']=='tolerance':
            require(fitz.Rect(TOLERANCE_BOX).contains(dst),
                    f'Source tolerance must fit its dedicated cell: {p["id"]}')
        else:
            require(all(not has_ink(p,dst&r) for r in RESERVED),f'Title/tolerance overlap: {p["id"]}')
    for i,a in enumerate(ps):
        for b in ps[i+1:]:
            overlap=fitz.Rect(a['target_box'])&fitz.Rect(b['target_box'])
            require(overlap.is_empty or overlap.width*overlap.height<1e-7 or not overlap_ink(a,b,overlap),
                    f'Content groups overlap: {a["id"]}/{b["id"]}; move {b["id"]} at least '
                    f'{overlap.width+6:.1f}pt horizontally or {overlap.height+6:.1f}pt vertically; never trim ink')
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
    for rule in restored_rules(cfg):
        (x0,y0),(x1,y1)=rule['target_line']
        linebox=fitz.Rect(min(x0,x1)-rule['width']/2,min(y0,y1)-rule['width']/2,
                          max(x0,x1)+rule['width']/2,max(y0,y1)+rule['width']/2)
        require(outer.contains(linebox),'Restored table rule outside frame')
        if cfg.get('schema_version',1)>=2 and rule['kind']=='tolerance':
            require(fitz.Rect(TOLERANCE_BOX).contains(linebox),
                    'Restored tolerance rule must fit its dedicated cell')
        else:
            require(all((linebox&r).is_empty for r in RESERVED),'Restored table rule overlaps title furniture')
        for p in ps:
            if p['id']!=rule['id']:
                require(not has_ink(p,linebox & fitz.Rect(p['target_box'])),
                        f'Restored rule overlaps source technical ink: {rule["id"]}/{p["id"]}')
    return ps,source_quality


def render_array(page):
    pix=page.get_pixmap(matrix=fitz.Matrix(S,S),alpha=False)
    return np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)


def cached_render(pdf_path,cache):
    """Cache the expensive 4x normalized source raster across draft/build/verify."""
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(canonical({'pdf':digest(pdf_path),'scale':S,
                                  'fitz':fitz.__version__})).hexdigest()[:24]
    target=cache/(key+'-render.npz')
    if target.exists():
        try:
            with np.load(target) as data:return data['pixels'],True
        except (ValueError,KeyError,EOFError):
            target.unlink(missing_ok=True)
    doc=fitz.open(pdf_path);pixels=render_array(doc[0])
    temp=target.with_suffix('.tmp.npz');np.savez_compressed(temp,pixels=pixels)
    temp.replace(target)
    return pixels,False


def fill(mask,box):
    x0,y0,x1,y1=box
    mask[max(0,int(math.floor(y0*S))):min(mask.shape[0],int(math.ceil(y1*S))),
         max(0,int(math.floor(x0*S))):min(mask.shape[1],int(math.ceil(x1*S)))]=True


def mask_components(mask,min_pixels=4,limit=50):
    labels,count=label(binary_dilation(mask,iterations=1));items=[]
    for component_id,slices in enumerate(find_objects(labels),1):
        if slices is None:continue
        ys,xs=slices;pixels=int((labels[slices]==component_id).sum())
        if pixels<min_pixels:continue
        items.append({'bbox':[round(xs.start/S,3),round(ys.start/S,3),round(xs.stop/S,3),round(ys.stop/S,3)],
                      'pixels':pixels})
    return sorted(items,key=lambda item:item['pixels'],reverse=True)[:limit]


def make_audit(cfg,neutral,colored,output,ps,assets,source_pixels=None,
               expected_pixels=None,source_quality=None):
    src=fitz.open(neutral);out=fitz.open(output)
    require(len(out)==1 and abs(out[0].rect.width-PAGE[0])<.1 and abs(out[0].rect.height-PAGE[1])<.1,
            'Output is not one landscape A4 page')
    a=render_array(src[0]) if source_pixels is None else source_pixels
    ink=a[:,:,:3].min(2)<150
    interest=np.zeros(ink.shape,bool);covered=np.zeros_like(interest)
    if cfg.get('schema_version',1)>=2:
        fill(interest,list(src[0].rect))
    else:
        for b in cfg['coverage']['include']:fill(interest,b)
    exclusion_audit=[]
    for ex in cfg['coverage'].get('exclude',[]):
        erase=np.zeros_like(interest);fill(erase,ex['box'])
        text=[]
        box=fitz.Rect(ex['box'])
        for word in src[0].get_text('words'):
            if not (box&fitz.Rect(word[:4])).is_empty:text.append(str(word[4]))
        exclusion_audit.append({'kind':ex.get('kind','legacy'),'box':ex['box'],'reason':ex['reason'],
                                'excluded_ink_pixels':int((erase&(a[:,:,:3].min(2)<245)).sum()),
                                'extractable_text':' '.join(text)[:500]})
        interest &= ~erase
    for p in ps:fill(covered,p['source_box'])
    for rule in restored_rules(cfg):fill(covered,rule['source_box'])
    unplaced_mask=(a[:,:,:3].min(2)<245)&interest&~covered
    unplaced=int(unplaced_mask.sum())
    z=render_array(out[0])[:,:,:3].astype(np.float32)
    # Compare at the SAME alpha threshold as black-on-white source <150.
    # Raw R<110 would reject valid thin blue antialiasing strokes on pale background.
    background_doc=fitz.open();bp=background_doc.new_page(width=PAGE[0],height=PAGE[1])
    bp.insert_image(bp.rect,filename=assets['background'])
    bg=render_array(bp)[:,:,:3]
    alpha_cutoff=1-150/255
    def branded_masks(pixels):
        masks=[];pixels=pixels[:,:,:3].astype(np.float32)
        for rgb in [BLUE,(217/255,154/255,0)]:
            numerator=np.zeros(bg.shape[:2],np.float32);denominator=np.zeros_like(numerator)
            for channel in range(3):
                base=bg[:,:,channel].astype(np.float32);direction=base-rgb[channel]*255
                numerator+=(base-pixels[:,:,channel])*direction;denominator+=direction*direction
            alpha=numerator/np.maximum(denominator,1)
            residual=np.zeros_like(alpha)
            for channel in range(3):
                base=bg[:,:,channel].astype(np.float32)
                residual=np.maximum(residual,np.abs(pixels[:,:,channel]-(base-alpha*(base-rgb[channel]*255))))
            masks.append((alpha>alpha_cutoff)&(residual<25))
        return masks[0]|masks[1]
    act_all=branded_masks(z)
    if expected_pixels is None:
        expected_doc,_=compose_document(cfg,colored,assets,ps)
        expected_pixels=render_array(expected_doc[0])
    expected_all=branded_masks(expected_pixels)
    global_missing=expected_all&~binary_dilation(act_all,iterations=DILATE)
    global_extra=act_all&~binary_dilation(expected_all,iterations=DILATE)
    global_missing_count=int(global_missing.sum());global_extra_count=int(global_extra.sum())
    global_pass=(global_missing_count<=GLOBAL_PIXEL_TOLERANCE
                 and global_extra_count<=GLOBAL_PIXEL_TOLERANCE)
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
    # Source-mode tolerance has no generated furniture to exempt from QA.
    for b in RESERVED[:1] if cfg.get('schema_version',1)>=2 else RESERVED:
        fill(furniture,list(b))
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
    # Technical source content can be paths; brand-frame fields remain searchable
    # and must occur exactly once inside the title block (no faux-bold overprint).
    frame_text=compact(out[0].get_text(clip=fitz.Rect(488,450,820,564)))
    field_tokens={'title':compact(cfg['fields']['title']),'model':compact(cfg['fields']['model']),
                  'scale':'SCALE:'+compact(cfg['fields']['scale_text']),
                  'unit':'UNIT:'+compact(cfg['fields']['unit']),
                  'sheet':'SHEET:'+compact(cfg['fields']['sheet'])}
    field_counts={key:frame_text.count(token) for key,token in field_tokens.items()}
    title_ok=all(count==1 for count in field_counts.values())
    ok=unplaced==0 and title_ok and global_pass and all(c['pass'] for c in checks)
    return {'pass':ok,'scope':'Raster ink preservation checks only the independently inventoried full-sheet envelope; an independent full-sheet/number review remains required.',
       'source_sha256':cfg['source']['sha256'],'output_sha256':digest(output),
       'inventory_sha256':inventory_hash(cfg),'source_inventory_sha256':source_inventory_hash(cfg),
       'source_unplaced_technical_ink_pixels':unplaced,
       'title_model_text_present':title_ok,'frame_field_occurrences':field_counts,
       'semantic_frame_fields_pass':title_ok,'mask_scale':S,'edge_pixels':EDGE,'dilation_pixels':DILATE,
       'max_missing_ratio_exclusive':MAX_MISSING,'color_mask':'background-relative blue/gold alpha; source-equivalent cutoff 1-150/255',
       'global_expected_missing_pixels':global_missing_count,
       'global_unexpected_ink_pixels':global_extra_count,'global_page_match_pass':global_pass,
       'source_unplaced_components':mask_components(unplaced_mask),
       'global_missing_components':mask_components(global_missing),
       'global_unexpected_components':mask_components(global_extra),
       'global_pixel_tolerance':GLOBAL_PIXEL_TOLERANCE,
       'brand_effective_dpi':brand_profile(assets['brand_strip'])['effective_dpi'],
       'brand_profile':brand_profile(assets['brand_strip']),
       'source_quality':source_quality or [],'exclusions':exclusion_audit,'engine_version':VERSION,
       'checks':checks}


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


def compose_document(cfg,colored,assets,ps):
    """Deterministically compose every expected output layer.

    verify() calls the same function independently and compares the complete
    rendered page.  This makes blank space and title cells observable instead of
    exempting them with large RESERVED rectangles.
    """
    doc=fitz.open();page=doc.new_page(width=PAGE[0],height=PAGE[1])
    page.insert_image(page.rect,filename=assets['background'])
    color=fitz.open(colored)
    for item in ps:
        if item['kind']!='projection':
            page.show_pdf_page(fitz.Rect(item['target_box']),color,0,clip=fitz.Rect(item['source_box']))
    draw_table_headers(page,cfg)
    fields=dict(cfg['fields']);fields['cjk_font_file']=assets['font']
    draw_frame_and_title(page,fields,assets,
                         tolerance_mode='source' if cfg.get('schema_version',1)>=2 else 'legacy')
    for item in ps:
        if item['kind']=='projection':
            page.show_pdf_page(fitz.Rect(item['target_box']),color,0,clip=fitz.Rect(item['source_box']))
    draw_restored_rules(page,cfg)
    doc.subset_fonts()
    doc.set_metadata({'title':fields['model']+' | 康生图纸',
                      'subject':'Technical content copied from a hash-bound supplier source, not retyped.'})
    return doc,page


def fit_inside(box,source_box,pad=6):
    box=fitz.Rect(box);source_box=fitz.Rect(source_box)
    scale=min((box.width-2*pad)/source_box.width,(box.height-2*pad)/source_box.height)
    width=source_box.width*scale;height=source_box.height*scale
    x=box.x0+(box.width-width)/2;y=box.y0+(box.height-height)/2
    return fitz.Rect(x,y,x+width,y+height)


def make_review_pack(neutral,output,cfg,ps,audit,outdir,prefix='review'):
    """Create two stable, single-look QA images and a hash-bound review template."""
    outdir=Path(outdir);source_doc=fitz.open(neutral);output_doc=fitz.open(output)
    colors=[(0.88,0.12,0.12),(0.05,0.45,0.82),(0.10,0.62,0.28),(0.72,0.25,0.78),
            (0.95,0.48,0.05),(0.05,0.68,0.68)]
    margin=25;header=42;gap=25;w=PAGE[0]*2+gap+margin*2;h=PAGE[1]+header+margin
    board=fitz.open();page=board.new_page(width=w,height=h)
    page.insert_text((margin,24),'SOURCE - complete rotated page',fontsize=11,fontname='hebo')
    page.insert_text((margin+PAGE[0]+gap,24),'OUTPUT - deterministic candidate',fontsize=11,fontname='hebo')
    left=fitz.Rect(margin,header,margin+PAGE[0],header+PAGE[1])
    right=fitz.Rect(margin+PAGE[0]+gap,header,margin+PAGE[0]*2+gap,header+PAGE[1])
    page.show_pdf_page(left,source_doc,0);page.show_pdf_page(right,output_doc,0)
    def shifted(value,dx,dy):
        r=fitz.Rect(value);return fitz.Rect(r.x0+dx,r.y0+dy,r.x1+dx,r.y1+dy)
    for ex in cfg['coverage'].get('exclude',[]):
        r=shifted(ex['box'],left.x0,left.y0)
        page.draw_rect(r,color=(.85,.1,.1),width=.7,dashes='3 2')
    groups={g['id']:g for g in cfg['groups']}
    by_group={}
    for item in ps:by_group.setdefault(item['id'],[]).append(item)
    for index,(group_id,items) in enumerate(by_group.items()):
        color=colors[index%len(colors)]
        for clip in groups[group_id]['clips']:
            page.draw_rect(shifted(clip,left.x0,left.y0),color=color,width=.8)
        for item in items:
            page.draw_rect(shifted(item['target_box'],right.x0,right.y0),color=color,width=.8)
    for item in audit.get('source_unplaced_components',[]):
        page.draw_rect(shifted(item['bbox'],left.x0,left.y0),color=(1,0,0),width=2)
    for item in audit.get('global_unexpected_components',[]):
        page.draw_rect(shifted(item['bbox'],right.x0,right.y0),color=(1,0,0),width=2)
    for item in audit.get('global_missing_components',[]):
        page.draw_rect(shifted(item['bbox'],right.x0,right.y0),color=(.8,.1,.8),width=2)
    board_path=outdir/(prefix+'-board.png')
    page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).save(board_path)

    row_h=150;label_w=190;col_w=500;detail_w=label_w+col_w*2+40
    detail_h=35+row_h*len(by_group)
    details=fitz.open();dp=details.new_page(width=detail_w,height=detail_h)
    dp.insert_text((label_w+20,23),'SOURCE GROUP',fontsize=10,fontname='hebo')
    dp.insert_text((label_w+col_w+20,23),'OUTPUT GROUP',fontsize=10,fontname='hebo')
    checks_by_id={}
    for check in audit.get('checks',[]):checks_by_id.setdefault(check['id'],[]).append(check)
    for index,(group_id,items) in enumerate(by_group.items()):
        y=35+index*row_h;color=colors[index%len(colors)]
        group=groups[group_id];source_box=union_box(group['clips'])
        target_box=union_box([item['target_box'] for item in items])
        metrics=checks_by_id.get(group_id,[])
        miss=max((m['missing_ratio'] for m in metrics),default=0)
        dp.insert_text((8,y+18),group_id[:28],fontsize=8,fontname='hebo',color=color)
        dp.insert_text((8,y+34),f"{group['kind']}  scale={group['scale']}",fontsize=7)
        dp.insert_text((8,y+49),f'max missing={miss:.4%}',fontsize=7)
        src_slot=fitz.Rect(label_w,y,label_w+col_w,y+row_h-5)
        out_slot=fitz.Rect(label_w+col_w+20,y,label_w+col_w*2+20,y+row_h-5)
        dp.show_pdf_page(fit_inside(src_slot,source_box),source_doc,0,clip=source_box,keep_proportion=True)
        dp.show_pdf_page(fit_inside(out_slot,target_box),output_doc,0,clip=target_box,keep_proportion=True)
        dp.draw_rect(src_slot,color=(.75,.75,.75),width=.4);dp.draw_rect(out_slot,color=(.75,.75,.75),width=.4)
    details_path=outdir/(prefix+'-details.png')
    dp.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(details_path)
    return board_path,details_path


FINAL_CHECKS=['same-source-identity','all-views-and-edge-dimensions','pcb-and-shared-notes',
              'all-part-table-rows-and-columns','performance-material-plating',
              'title-series-unit-scale-sheet','tolerance-and-projection','full-page-legibility',
              'no-source-furniture-fragments']


def write_final_review_template(path,cfg,source,output,board,details):
    value={'source_sha256':digest(source),'output_sha256':digest(output),
           'source_inventory_sha256':source_inventory_hash(cfg),
           'inventory_sha256':inventory_hash(cfg),'review_board_sha256':digest(board),
           'review_details_sha256':digest(details),'reviewer':'','reviewer_run_id':'',
           'reviewer_role':'independent_final_reviewer','full_page_compared':False,
           'verdict':'REVIEW','required_checks':FINAL_CHECKS,'checks':[]}
    save_json(path,value)


def save_json(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')


def write_provenance(outdir,cfg,manifest,source,assets,audit):
    import platform
    outdir=Path(outdir)
    save_json(outdir/'manifest.snapshot.json',cfg)
    save_json(outdir/'run.json',{'engine_version':VERSION,'engine_sha256':digest(__file__),
        'frame_sha256':digest(Path(__file__).with_name('frame.py')),
        'python':platform.python_version(),'pymupdf':fitz.__version__,'numpy':np.__version__,
        'source_sha256':digest(source),'manifest_sha256':digest(manifest),
        'source_inventory_sha256':source_inventory_hash(cfg),'inventory_sha256':inventory_hash(cfg),
        'asset_sha256':{key:digest(value) for key,value in assets.items()},
        'output_sha256':audit['output_sha256']})


def build(args):
    start=time.perf_counter();cfg,mp,source,assets=read_manifest(args.manifest)
    outdir=Path(args.output).resolve()
    require(not any((outdir/name).exists() for name in ['drawing.pdf','candidate.pdf','audit.json','layout.json','preview.png','verify.json',
            'review-board.png','review-details.png','release.json','review.snapshot.json','run.json',
            'manifest.snapshot.json','final-review-template.json']),
            'Output directory contains prior artifacts; choose a new output directory to avoid stale delivery')
    outdir.mkdir(parents=True,exist_ok=True)
    cache=args.cache or outdir/'.cache'
    neutral,colored,renderer,hit=cached_source(cfg,source,cache)
    source_pixels,render_hit=cached_render(neutral,cache)
    n=fitz.open(neutral);ps,source_quality=check_geometry(cfg,n[0],source_pixels)
    d,p=compose_document(cfg,colored,assets,ps);expected_pixels=render_array(p)
    output=outdir/'drawing.pdf';tmp=outdir/'candidate.pdf'
    d.save(tmp,garbage=4,deflate=True,deflate_fonts=True,deflate_images=True,use_objstms=1)
    audit=make_audit(cfg,neutral,colored,tmp,ps,assets,source_pixels,expected_pixels,source_quality)
    audit['generation_seconds']=round(time.perf_counter()-start,3)
    audit['renderer']=renderer;audit['source_cache_hit']=hit
    audit['source_render_cache_hit']=render_hit
    board,details=make_review_pack(neutral,tmp,cfg,ps,audit,outdir)
    audit['review_board_sha256']=digest(board);audit['review_details_sha256']=digest(details)
    save_json(outdir/'audit.json',audit)
    require(audit['pass'],'Automatic preservation QA failed. candidate.pdf retained for diagnosis; no accepted drawing emitted')
    tmp.replace(output)
    p.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(outdir/'preview.png')
    save_json(outdir/'layout.json',{'manifest_sha256':digest(mp),'source_sha256':digest(source),
        'output_sha256':digest(output),'source_inventory_sha256':source_inventory_hash(cfg),
        'inventory_sha256':inventory_hash(cfg),'placements':ps,'fields':cfg['fields'],'renderer':renderer})
    write_final_review_template(outdir/'final-review-template.json',cfg,source,output,board,details)
    write_provenance(outdir,cfg,mp,source,assets,audit)
    print(json.dumps({'status':'AUTO_QA_PASS_REQUIRES_VISUAL_REVIEW','pdf':str(output),'audit':str(outdir/'audit.json'),
                      'seconds':audit['generation_seconds'],'renderer':renderer,'cache_hit':hit},ensure_ascii=False))


def draft(args):
    """Repeatable layout iteration that never claims review or release readiness."""
    start=time.perf_counter();cfg,mp,source,assets=read_manifest(args.manifest,check_review=False)
    outdir=Path(args.output).resolve();outdir.mkdir(parents=True,exist_ok=True)
    for name in ['draft.pdf','draft.tmp.pdf','draft-preview.png','draft-audit.json',
                 'draft-board.png','draft-details.png','draft-diagnostics.json']:
        (outdir/name).unlink(missing_ok=True)
    cache=args.cache or outdir/'.cache'
    neutral,colored,renderer,hit=cached_source(cfg,source,cache)
    source_pixels,render_hit=cached_render(neutral,cache)
    normalized=fitz.open(neutral)
    try:
        ps,source_quality=check_geometry(cfg,normalized[0],source_pixels)
    except ValueError as exc:
        save_json(outdir/'draft-diagnostics.json',{'status':'PREFLIGHT_FAIL','message':str(exc),
            'source_inventory_sha256':source_inventory_hash(cfg),'inventory_sha256':inventory_hash(cfg)})
        raise
    doc,page=compose_document(cfg,colored,assets,ps);expected_pixels=render_array(page)
    temp=outdir/'draft.tmp.pdf';output=outdir/'draft.pdf'
    doc.save(temp,garbage=4,deflate=True,deflate_fonts=True,deflate_images=True,use_objstms=1)
    audit=make_audit(cfg,neutral,colored,temp,ps,assets,source_pixels,expected_pixels,source_quality)
    audit['generation_seconds']=round(time.perf_counter()-start,3);audit['renderer']=renderer
    audit['source_cache_hit']=hit;audit['source_render_cache_hit']=render_hit
    board,details=make_review_pack(neutral,temp,cfg,ps,audit,outdir,prefix='draft')
    audit['review_board_sha256']=digest(board);audit['review_details_sha256']=digest(details)
    save_json(outdir/'draft-audit.json',audit)
    temp.replace(output);page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(outdir/'draft-preview.png')
    print(json.dumps({'status':'DRAFT_AUTO_QA_PASS' if audit['pass'] else 'DRAFT_AUTO_QA_FAIL',
        'pdf':str(output),'audit':str(outdir/'draft-audit.json'),'seconds':audit['generation_seconds'],
        'source_review_required_for_build':True,'source_inventory_sha256':source_inventory_hash(cfg)},ensure_ascii=False))
    require(audit['pass'],'Draft QA failed; inspect draft-audit.json and the two draft review images')


def batch(args):
    """Single local ledger for small batches; no upload and no hidden retries."""
    spec_path=Path(args.jobs).resolve();spec=json.loads(spec_path.read_text())
    require(isinstance(spec.get('jobs'),list) and spec['jobs'],'Batch file needs a non-empty jobs list')
    root=Path(args.output_root).resolve();root.mkdir(parents=True,exist_ok=True)
    cache=Path(args.cache).resolve() if args.cache else root/'.cache'
    state_path=root/'batch-state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {
        'schema_version':1,'mode':args.mode,'jobs':{},'events':[]}
    ids=[str(job.get('id','')) for job in spec['jobs']]
    require(all(re.fullmatch(r'[A-Za-z0-9._-]+',job_id) for job_id in ids),'Batch job ids must be filesystem-safe')
    require(len(ids)==len(set(ids)),'Batch job ids must be unique')

    def persist():
        state['updated_at_epoch']=int(time.time())
        temp=state_path.with_suffix('.tmp');save_json(temp,state);temp.replace(state_path)

    def current(job):
        manifest=resolve(spec_path.parent,job['manifest']);cfg=json.loads(manifest.read_text())
        recipe=inventory_hash(cfg);entry=state['jobs'].get(job['id'])
        if entry and entry.get('inventory_sha256')==recipe and entry.get('run_dir'):
            run=Path(entry['run_dir'])
            if (run/'release.json').is_file() and (run/'drawing.pdf').is_file():
                release=json.loads((run/'release.json').read_text())
                if (release.get('inventory_sha256')==recipe
                        and release.get('output_sha256')==digest(run/'drawing.pdf')):
                    entry['status']='RELEASE_READY';return manifest,cfg,recipe,entry,True
            if entry.get('status') in {'AUTO_QA_FAIL','PREFLIGHT_FAIL'} and args.allow_retry:
                return manifest,cfg,recipe,entry,False
            elif entry.get('status') in {'AUTO_QA_FAIL','PREFLIGHT_FAIL','AUTO_QA_PASS','DRAFT_QA_PASS'}:
                return manifest,cfg,recipe,entry,True
        return manifest,cfg,recipe,entry,False

    pilots=[job for job in spec['jobs'] if job.get('pilot')]
    regular=[job for job in spec['jobs'] if not job.get('pilot')]
    ordered=pilots+regular
    pilot_blocked=False
    for job in ordered:
        manifest,cfg,recipe,previous,unchanged=current(job)
        if job in regular and pilots:
            required_status='DRAFT_QA_PASS' if args.mode=='draft' else 'RELEASE_READY'
            ready=all(state['jobs'].get(p['id'],{}).get('status')==required_status for p in pilots)
            if not ready:
                state['jobs'][job['id']]={'status':'BLOCKED_PILOT_GATE','manifest':str(manifest),
                    'inventory_sha256':recipe,'message':'All pilot outputs need independent verify/release before expansion.'}
                pilot_blocked=True;persist();continue
        if unchanged:
            continue
        attempts=int(previous.get('attempts',0)) if previous else 0
        if attempts>=int(args.max_attempts) and not args.allow_retry:
            state['jobs'][job['id']]={'status':'NEEDS_REVIEW_RETRY_LIMIT','manifest':str(manifest),
                'inventory_sha256':recipe,'attempts':attempts,
                'message':'Retry limit reached; stop for review or rerun only with explicit --allow-retry.'}
            persist();continue
        run=root/job['id']/(f'{recipe[:16]}-a{attempts+1}' if args.mode=='build' else f'draft-a{attempts+1}')
        run.mkdir(parents=True,exist_ok=True)
        entry={'status':'RUNNING','manifest':str(manifest),'inventory_sha256':recipe,
               'source_inventory_sha256':source_inventory_hash(cfg),'attempts':attempts+1,'run_dir':str(run)}
        state['jobs'][job['id']]=entry;persist()
        ns=argparse.Namespace(manifest=str(manifest),output=str(run),cache=str(cache))
        try:
            (build if args.mode=='build' else draft)(ns)
            entry['status']='AUTO_QA_PASS' if args.mode=='build' else 'DRAFT_QA_PASS'
            artifact=run/('drawing.pdf' if args.mode=='build' else 'draft.pdf')
            entry['output_sha256']=digest(artifact);entry['artifact']=str(artifact)
        except (ValueError,KeyError,FileNotFoundError) as exc:
            entry['status']='AUTO_QA_FAIL' if (run/('audit.json' if args.mode=='build' else 'draft-audit.json')).exists() else 'PREFLIGHT_FAIL'
            entry['message']=str(exc)
        state['events'].append({'job_id':job['id'],'status':entry['status'],'inventory_sha256':recipe,
                                'epoch':int(time.time())})
        state['events']=state['events'][-200:];persist()
    persist()
    summary={'state':str(state_path),'mode':args.mode,
             'counts':{status:sum(1 for value in state['jobs'].values() if value.get('status')==status)
                       for status in sorted({v.get('status') for v in state['jobs'].values()})},
             'pilot_gate_blocked':pilot_blocked}
    save_json(root/'job-summary.json',summary);print(json.dumps(summary,ensure_ascii=False))


def verify(args):
    cfg,mp,source,assets=read_manifest(args.manifest)
    output=Path(args.pdf).resolve()
    cache=args.cache or output.parent/'.cache'
    neutral,colored,_,_=cached_source(cfg,source,cache)
    source_pixels,_=cached_render(neutral,cache)
    doc=fitz.open(neutral);ps,source_quality=check_geometry(cfg,doc[0],source_pixels)
    expected_doc,_=compose_document(cfg,colored,assets,ps)
    audit=make_audit(cfg,neutral,colored,output,ps,assets,source_pixels,
                     render_array(expected_doc[0]),source_quality)
    audit['visual_review_pass']=False
    if args.review:
        review=json.loads(Path(args.review).read_text())
        require(review.get('source_sha256')==digest(source) and review.get('output_sha256')==digest(output)
            and review.get('inventory_sha256')==inventory_hash(cfg),'Visual review evidence hash mismatch')
        require(review.get('verdict')=='PASS' and review.get('reviewer') and review.get('checks'), 'Incomplete visual review evidence')
        require(review.get('reviewer_role')=='independent_final_reviewer',
                'Final evidence must name an independent_final_reviewer')
        require(review.get('full_page_compared') is True,
                'Final evidence must record a full-page source comparison')
        producer=cfg.get('review',{}).get('reviewer')
        require(review.get('reviewer')!=producer,
                'Producer/source reviewer cannot self-approve final delivery')
        if cfg.get('schema_version',1)>=2:
            require(review.get('source_inventory_sha256')==source_inventory_hash(cfg),
                    'Final review/source inventory hash mismatch')
            require(bool(review.get('reviewer_run_id'))
                    and review.get('reviewer_run_id')!=cfg.get('review',{}).get('reviewer_run_id'),
                    'Final review needs a distinct reviewer_run_id')
            require(set(FINAL_CHECKS).issubset(set(review.get('checks',[]))),
                    'Final review checklist is incomplete')
            board=output.parent/'review-board.png';details=output.parent/'review-details.png'
            require(board.is_file() and details.is_file()
                    and review.get('review_board_sha256')==digest(board)
                    and review.get('review_details_sha256')==digest(details),
                    'Final review images are missing or changed')
        audit['visual_review_pass']=True
    save_json(args.report or output.parent/'verify.json',audit)
    require(audit['pass'],'Verification failed')
    if audit['visual_review_pass']:
        save_json(output.parent/'review.snapshot.json',review)
        save_json(output.parent/'release.json',{'release_ready':True,'engine_version':VERSION,
            'source_sha256':digest(source),'output_sha256':digest(output),
            'source_inventory_sha256':source_inventory_hash(cfg),'inventory_sha256':inventory_hash(cfg),
            'review_sha256':digest(args.review),'verify_sha256':digest(args.report or output.parent/'verify.json')})
    print(json.dumps({'automatic_pass':True,'visual_review_pass':audit['visual_review_pass'],
                      'release_ready':audit['visual_review_pass']},ensure_ascii=False))


def write_source_map(doc,page,stem):
    """One compact coordinate/index pack replaces repeated ad-hoc screenshots."""
    blocks=[]
    for index,block in enumerate(page.get_text('blocks')):
        text_value=' '.join(str(block[4]).split())
        if text_value:
            blocks.append({'id':f'T{index:03d}','bbox':[round(float(x),3) for x in block[:4]],
                           'text':text_value[:500]})
    pix=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
    array=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)
    ink=array[:,:,:3].min(2)<245
    merged=binary_dilation(ink,iterations=5);labels,count=label(merged)
    components=[]
    for component_id,slices in enumerate(find_objects(labels),1):
        if slices is None:continue
        ys,xs=slices;area=int((labels[slices]==component_id).sum())
        if area<40:continue
        components.append({'id':f'C{component_id:04d}','bbox':[round(xs.start/2,2),round(ys.start/2,2),
            round(xs.stop/2,2),round(ys.stop/2,2)],'merged_ink_pixels':area})
    components=sorted(components,key=lambda item:item['merged_ink_pixels'],reverse=True)[:200]
    overlay=fitz.open();op=overlay.new_page(width=page.rect.width,height=page.rect.height)
    op.show_pdf_page(op.rect,doc,0)
    for x in range(0,int(page.rect.width)+1,50):
        op.draw_line((x,0),(x,page.rect.height),color=(.3,.45,.75),width=.25,dashes='2 4')
        if x:op.insert_text((x+2,10),str(x),fontsize=5,color=(.15,.3,.65))
    for y in range(0,int(page.rect.height)+1,50):
        op.draw_line((0,y),(page.rect.width,y),color=(.3,.45,.75),width=.25,dashes='2 4')
        if y:op.insert_text((2,y-2),str(y),fontsize=5,color=(.15,.3,.65))
    for block in blocks:
        box=fitz.Rect(block['bbox']);op.draw_rect(box,color=(.8,.1,.15),width=.45)
        op.insert_text((box.x0,max(6,box.y0-1)),block['id'],fontsize=5,color=(.8,.1,.15))
    map_path=Path(str(stem)+'-source-map.png')
    op.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(map_path)
    inspection={'engine_version':VERSION,'page':[round(page.rect.width,3),round(page.rect.height,3)],
        'coordinate_system':'rotated visible PDF points; origin top-left','text_blocks':blocks,
        'raster_component_candidates':components,'drawing_object_count':len(page.get_drawings()),
        'note':'Candidates are navigation aids, not approved technical groups. Review the complete source map.'}
    inspection_path=Path(str(stem)+'-inspection.json');save_json(inspection_path,inspection)
    return map_path,inspection_path


def init(args):
    source=Path(args.source).resolve();out=Path(args.manifest).resolve();doc=fitz.open(source)
    page=doc[0];page.set_rotation(args.rotation);page.remove_rotation()
    root=Path(__file__).resolve().parents[1]
    cfg={'schema_version':2,'source':{'path':str(source),'sha256':digest(source),'page':1,
        'rotation':args.rotation,'expected_pages':len(doc)},'renderer':'auto',
        'identity':{'expected_model':args.model,'observed_model':'','model_evidence':'','observed_parts':[],
                    'part_pattern':''},
        'fields':{'model':args.model,'title':'','scale_text':'','unit':'','sheet':'','tolerances':[],
                  'no_tolerance_block_reason':''},
        'assets':{'background':str(root/'assets/background.png'),'brand_strip':str(root/'assets/brand-strip.png'),'font':''},
        'groups':[], 'coverage':{'mode':'full-page-minus-exclusions','exclude':[]},
        'review':{'verdict':'REVIEW','reviewer':'','reviewer_run_id':'','reviewer_role':'source_inventory_reviewer',
                  'coverage_basis':'uncropped-full-sheet','full_page_reviewed':False,
                  'source_sha256':digest(source),'source_inventory_sha256':''}}
    out.parent.mkdir(parents=True,exist_ok=True);save_json(out,cfg)
    page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(out.parent/(out.stem+'-source.png'))
    map_path,inspection_path=write_source_map(doc,page,out.with_suffix(''))
    print(json.dumps({'status':'INSPECTION_READY','manifest':str(out),'source_map':str(map_path),
                      'inspection':str(inspection_path),'next':'Fill groups/exclusions, run draft, then obtain one source inventory review.'},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('init');p.add_argument('source');p.add_argument('--manifest',required=True);p.add_argument('--model',required=True);p.add_argument('--rotation',type=int,choices=[0,90,180,270],required=True);p.set_defaults(func=init)
    p=subs.add_parser('draft');p.add_argument('manifest');p.add_argument('--output',required=True);p.add_argument('--cache');p.set_defaults(func=draft)
    p=subs.add_parser('build');p.add_argument('manifest');p.add_argument('--output',required=True);p.add_argument('--cache');p.set_defaults(func=build)
    p=subs.add_parser('batch');p.add_argument('jobs');p.add_argument('--output-root',required=True);p.add_argument('--cache');p.add_argument('--mode',choices=['draft','build'],default='build');p.add_argument('--max-attempts',type=int,default=2);p.add_argument('--allow-retry',action='store_true');p.set_defaults(func=batch)
    p=subs.add_parser('verify');p.add_argument('manifest');p.add_argument('pdf');p.add_argument('--cache');p.add_argument('--review');p.add_argument('--report');p.set_defaults(func=verify)
    p=subs.add_parser('inventory-hash');p.add_argument('manifest');p.set_defaults(func=lambda a: print(inventory_hash(json.loads(Path(a.manifest).read_text()))))
    p=subs.add_parser('hashes');p.add_argument('manifest');p.set_defaults(func=lambda a: print(json.dumps({
        'source_inventory_sha256':source_inventory_hash(json.loads(Path(a.manifest).read_text())),
        'inventory_sha256':inventory_hash(json.loads(Path(a.manifest).read_text()))},ensure_ascii=False)))
    args=parser.parse_args()
    try:args.func(args)
    except (ValueError,KeyError,FileNotFoundError) as exc:
        print('STOP: '+str(exc),file=sys.stderr);return 2
    return 0


if __name__=='__main__':sys.exit(main())
