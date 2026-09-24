#!/usr/bin/env python3
"""One reviewed manifest -> faithful vector copy -> reproducible QA (no upload)."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import time
import traceback
import unicodedata
import uuid
from pathlib import Path
import pymupdf as fitz
import numpy as np
from scipy.ndimage import affine_transform, binary_dilation, label, find_objects
from frame import (BLUE, PAGE, FRAME, TITLE_BOX, TOLERANCE_BOX, PROJECTION_BOX,
                   draw_frame_and_title)

VERSION = '2.1.0-candidate'
QA_RULESET = 'source-preservation-plus-approved-english-tolerance-v1'
COLOR_PROFILES = {'legacy-v1', 'cyan-gold-v1'}
STROKE_PROFILES = {'source', 'legacy-thin-stroke-boost-v1'}
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
    # A successful output is reusable only with the same recipe, actual engine,
    # frame, QA rules and asset bytes. Source review has its separate hash.
    recipe={k:v for k,v in cfg.items() if k!='review' and not k.startswith('_')}
    assets=cfg.get('assets',{})
    base=Path(cfg['_manifest_path']).parent if cfg.get('_manifest_path') else Path.cwd()
    asset_hashes={key:digest(resolve(base,path)) if resolve(base,path).is_file() else 'MISSING'
                  for key,path in assets.items()}
    runtime={'engine':digest(__file__),'frame':digest(Path(__file__).with_name('frame.py')),
             'version':VERSION,'qa_ruleset':QA_RULESET,'assets':asset_hashes}
    if cfg.get('source_fields'):
        runtime['source_fields']=digest(Path(__file__).with_name('source_fields.py'))
        runtime['dynamic_tolerance']=digest(Path(__file__).with_name('dynamic_tolerance.py'))
    return hashlib.sha256(canonical({'recipe':recipe,'runtime':runtime})).hexdigest()


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
    if cfg.get('approved_tolerance_reflow'):
        value['tolerance_source_map_sha256']=cfg['approved_tolerance_reflow']['source_map_sha256']
    if cfg.get('source_fields'):
        value['source_fields_sha256']=cfg['source_fields']['semantic_sha256']
    if cfg.get('approved_uniform_view_scales'):
        value['approved_uniform_view_scales']=cfg['approved_uniform_view_scales']
        value['view_pcb_scales']=[(g['id'],g['scale']) for g in cfg['groups']
                                  if g['kind'] in {'view','pcb'}]
    return hashlib.sha256(canonical(value)).hexdigest()


def resolve(base, value):
    p=Path(value).expanduser()
    return p.resolve() if p.is_absolute() else (base/p).resolve()


def compact(value):
    return ''.join(unicodedata.normalize('NFKC',str(value)).split())


def tolerance_lines(rows):
    """The accepted four-line typography is data, never a global value default."""
    tiers=['X.','X.X','X.XX','X.XXX']
    require(len(rows)==4 and [row.get('tier') for row in rows]==tiers,
            'Approved English tolerance mode needs four reviewed source tiers')
    require(all(re.fullmatch(r'±\d+\.\d{2}',row.get('value','')) for row in rows),
            'Tolerance sign, digits or decimal places invalid')
    return [row['tier']+' '+row['value'] for row in rows]


def parsed_tolerance_lines(lines):
    result=[]
    for line in lines:
        match=re.fullmatch(r'(X\.(?:X){0,3})\s+(±\d+\.\d{2})',line.strip())
        require(match is not None,'Approved English tolerance line malformed')
        result.append(match[1]+' '+match[2])
    return result


def load_tolerance_reflow(cfg,manifest_path,check_source_raster=True):
    """Validate the authorized region's immutable source and visual evidence."""
    ref=cfg.get('approved_tolerance_reflow')
    if not ref:return None
    require(cfg.get('schema_version')==2,'Approved tolerance reflow requires manifest v2')
    base=Path(manifest_path).parent
    source_map=resolve(base,ref['source_map_path'])
    layout_path=resolve(base,ref['layout_path'])
    approved_pdf=resolve(base,ref['approved_pdf_path'])
    for path,key in [(source_map,'source_map_sha256'),(layout_path,'layout_sha256'),
                     (approved_pdf,'approved_pdf_sha256')]:
        require(path.is_file() and digest(path)==ref[key],f'Approved tolerance evidence changed: {key}')
    evidence=json.loads(source_map.read_text());layout=json.loads(layout_path.read_text())
    require(evidence['source_sha256']==cfg['source']['sha256']==layout['source_sha256']
            and evidence['record_id']==cfg['identity']['record_id']
            and evidence['model']==cfg['fields']['model']==layout['fields']['model'],
            'Approved tolerance source/model/record binding differs')
    require(evidence['source_unit']==cfg['fields']['unit']==layout['fields']['unit'],
            'Approved tolerance unit differs from this source')
    require(evidence.get('source_inventory_complete') is True,
            'Tolerance source field inventory is not declared complete')
    require(evidence.get('additional_tolerance_conditions')==[],
            'Additional condition inside the source tolerance table is outside the approved four-line mode')
    require(evidence.get('source_header')=='未注公差 TOOLERANCE'
            and evidence.get('source_projection_label')=='视图方法 PROJECTION',
            'Source tolerance/projection labels need a reviewed mapping')
    require(evidence.get('authorized_heading')==['UNLESS OTHERWISE','SPECIFIED, TOLERANCE:']
            and evidence.get('authorized_internal_grid') is False,
            'Approved tolerance visual contract differs')
    require(evidence.get('projection_symbol')=='source_projection_group_to_bottom_right',
            'Projection mapping differs')
    group=[g for g in cfg['groups'] if g['kind']=='tolerance']
    projection=[g for g in cfg['groups'] if g['kind']=='projection']
    require(len(group)==len(projection)==1 and
            fitz.Rect(evidence['source_tolerance_box']).contains(fitz.Rect(group[0]['reviewed_source_extent'])),
            'Tolerance/projection source group missing or outside evidenced source box')
    source_path=resolve(base,cfg['source']['path'])
    require(digest(source_path)==evidence['source_sha256'],'Source tolerance evidence PDF changed')
    if check_source_raster:
        with fitz.open(source_path) as doc:
            page=doc[0];page.set_rotation(cfg['source']['rotation']);page.remove_rotation()
            samples=page.get_pixmap(matrix=fitz.Matrix(6,6),
                clip=fitz.Rect(evidence['source_tolerance_box']),alpha=False).samples
        require(hashlib.sha256(samples).hexdigest()==evidence['source_tolerance_6x_samples_sha256'],
                'Source tolerance object/raster evidence changed')
    expected=tolerance_lines(evidence['rows'])
    require(parsed_tolerance_lines(layout['fields']['tolerances'])==expected,
            'Approved template values differ from this product source mapping')
    review_path=resolve(base,evidence['field_review_report'])
    require(review_path.is_file() and digest(review_path)==evidence['field_review_report_sha256'],
            'Independent tolerance field comparison evidence missing or changed')
    review=json.loads(review_path.read_text())
    reviewed=[row for row in review.get('rows',[]) if row.get('record_id')==evidence['record_id']]
    require(review.get('reviewer_identifier') and len(reviewed)==1,
            'Independent tolerance field comparison did not identify this product')
    reviewed=reviewed[0]
    require(reviewed.get('source_sha256')==evidence['source_sha256']
            and reviewed.get('source_tolerance_fields')==expected
            and reviewed.get('tolerance_values_status')=='PASS_VISUAL_FIELD_COMPARISON'
            and reviewed.get('unit_status')=='PASS',
            'Independent tolerance field comparison is not complete for this source')
    external=evidence.get('additional_technical_conditions',[])
    require(isinstance(external,list),'Additional technical conditions inventory malformed')
    if external:
        ext_path=resolve(base,evidence['external_condition_review_report'])
        require(ext_path.is_file() and digest(ext_path)==evidence['external_condition_review_report_sha256'],
                'Independent external condition evidence missing or changed')
        ext_report=json.loads(ext_path.read_text())
        ext_rows=[x for x in ext_report.get('rows',[]) if x.get('record_id')==evidence['record_id']]
        require(len(ext_rows)==1 and ext_rows[0].get('source_sha256')==evidence['source_sha256'],
                'External condition review/source identity differs')
        ext_row=ext_rows[0]
        require(len(external)==1 and external[0]['text']==ext_row.get('condition')
                and external[0]['carrying_group_id']==ext_row.get('carrying_group_id')
                and ext_row.get('condition_piece_qa',{}).get('pass') is True
                and ext_row.get('candidate_pdf_sha256'),
                'Additional technical condition not independently located in its source vector group')
        carrying=[g for g in cfg['groups'] if g['id']==external[0]['carrying_group_id']]
        require(len(carrying)==1 and carrying[0]['kind']=='pcb'
                and carrying[0]['clips']==ext_row.get('source_clips'),
                'Additional technical condition carrying group changed')
    else:
        require(reviewed.get('extra_conditions_status')=='PASS_VISUAL_INSPECTION',
                'Additional tolerance conditions have not been inventoried')
    return {'evidence':evidence,'layout':layout,'approved_pdf':approved_pdf,
            'expected_lines':expected,'source_map_sha256':digest(source_map),
            'approved_pdf_sha256':digest(approved_pdf)}


def validate_approved_uniform_scales(cfg,manifest_path):
    """Permit only source-bound, approved *uniform* view/PCB scaling with NTS."""
    ref=cfg.get('approved_uniform_view_scales')
    changed=[g for g in cfg['groups'] if g['kind'] in {'view','pcb'} and
             abs(float(g['scale'])-1)>1e-9]
    if not changed:
        require(not ref,'Unused approved_uniform_view_scales declaration')
        return
    require(ref is not None,'View/PCB scaling needs approved_uniform_view_scales evidence')
    require(cfg['fields']['scale_text']=='NTS',
            'Mixed view/PCB physical scales require SCALE: NTS; source 3:1 cannot be retained')
    base=Path(manifest_path).parent
    layout_path=resolve(base,ref['layout_path'])
    require(layout_path.is_file() and digest(layout_path)==ref['layout_sha256'],
            'Approved view scale reference missing or changed')
    layout=json.loads(layout_path.read_text())
    require(layout['source_sha256']==cfg['source']['sha256'],
            'Approved view scales belong to a different source')
    approved_pdf=resolve(base,layout['output'])
    require(approved_pdf.is_file() and digest(approved_pdf)==layout['output_sha256'],
            'Approved view scale PDF missing or changed')
    for g in changed:
        box=union_box(g['clips']);matches=[]
        for item in layout['placements']:
            src=fitz.Rect(item['source_box'])
            if box.contains(src):matches.append(item)
        require(matches,f'No approved placement for scaled group: {g["id"]}')
        source_union=union_box([x['source_box'] for x in matches])
        target_union=union_box([x['target_box'] for x in matches])
        require(all(abs(float(item['scale'])-float(g['scale']))<1e-6 for item in matches)
                and all(abs(a-b)<.03 for a,b in zip(source_union,box))
                and abs(target_union.x0-float(g['dst'][0]))<.03
                and abs(target_union.y0-float(g['dst'][1]))<.03,
                f'Scaled group differs from source-bound approved placement: {g["id"]}')


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
            cap=rule.get('line_cap','butt')
            end=width/2 if cap in {'round','square'} else 0
            if rule['orientation']=='vertical':
                x=float(rule['x']);y0=float(rule['y0']);y1=float(rule['y1'])
                yield {'id':group['id'],'kind':group['kind'],'source_box':[x-width/2,y0-end,x+width/2,y1+end],
                       'target_line':((group['dst'][0]+(x-origin.x0)*scale,
                                       group['dst'][1]+(y0-origin.y0)*scale),
                                      (group['dst'][0]+(x-origin.x0)*scale,
                                       group['dst'][1]+(y1-origin.y0)*scale)),
                       'width':width*scale,'color':rule.get('color','gold'),'line_cap':cap}
            else:
                y=float(rule['y']);x0=float(rule['x0']);x1=float(rule['x1'])
                yield {'id':group['id'],'kind':group['kind'],'source_box':[x0-end,y-width/2,x1+end,y+width/2],
                       'target_line':((group['dst'][0]+(x0-origin.x0)*scale,
                                       group['dst'][1]+(y-origin.y0)*scale),
                                      (group['dst'][0]+(x1-origin.x0)*scale,
                                       group['dst'][1]+(y-origin.y0)*scale)),
                       'width':width*scale,'color':rule.get('color','gold'),'line_cap':cap}


def draw_restored_rules(page,cfg):
    for rule in restored_rules(cfg):
        color=BLUE if rule['color']=='blue' else (217/255,154/255,0)
        page.draw_line(*rule['target_line'],color=color,width=rule['width'],
                       lineCap={'butt':0,'round':1,'square':2}[rule['line_cap']])


def restored_rule_vector_checks(page,cfg):
    """Count painted rules, not source paths that remain in a clipped XObject."""
    rules=list(restored_rules(cfg))
    if not rules:return []
    drawings=page.get_drawings()
    result=[]
    for rule in rules:
        p,q=rule['target_line']
        expected_color=BLUE if rule['color']=='blue' else (217/255,154/255,0)
        expected_cap={'butt':0,'round':1,'square':2}[rule['line_cap']]
        matches=[]
        for drawing in drawings:
            for item in drawing['items']:
                if item[0]!='l':continue
                a,b=item[1:3]
                same=(abs(a.x-p[0])<.03 and abs(a.y-p[1])<.03 and
                      abs(b.x-q[0])<.03 and abs(b.y-q[1])<.03)
                reverse=(abs(b.x-p[0])<.03 and abs(b.y-p[1])<.03 and
                         abs(a.x-q[0])<.03 and abs(a.y-q[1])<.03)
                if not (same or reverse):continue
                color=drawing.get('color')
                equivalent=(drawing.get('width') is not None and
                    abs(drawing['width']-rule['width'])<.03 and
                    drawing.get('lineCap') and expected_cap in drawing['lineCap'] and
                    color is not None and all(abs(x-y)<.03 for x,y in zip(color,expected_color)))
                matches.append({'seqno':drawing['seqno'],'attributes_match':bool(equivalent)})
        # PyMuPDF exposes vectors inside show_pdf_page's clipping XObject in
        # get_drawings(), even when none of their stroke can actually paint.
        # The source clip must be strictly outside the measured stroke, and
        # only an early, copied path may be discounted. A second late rule
        # remains a visible duplicate and fails.
        masked=0
        group=next(g for g in cfg['groups'] if g['id']==rule['id'])
        original=next((r for r in group.get('restored_source_rules',[])
                       if r['orientation']==('vertical' if p[0]==q[0] else 'horizontal')),None)
        if rule['kind']=='table' and original and matches:
            clip=union_box(group['clips'])
            outside=(clip.x1<float(original['x'])-float(original['width'])/2-.005
                     if original['orientation']=='vertical' else
                     clip.y1<float(original['y'])-float(original['width'])/2-.005)
            latest=max(x['seqno'] for x in matches)
            if outside and any(latest-x['seqno']>10 for x in matches):masked=1
        visible=len(matches)-masked
        good=sum(x['attributes_match'] for x in matches)
        result.append({'id':rule['id'],'target_line':rule['target_line'],
                       'width':rule['width'],'line_cap':rule['line_cap'],
                       'raw_matching_paths':len(matches),'masked_source_paths':masked,
                       'visible_rule_count':visible,'matching_attributes_count':good,
                       'pass':visible==1 and good==len(matches)})
    return result


def read_manifest(path,check_review=True):
    path=Path(path).resolve(); cfg=json.loads(path.read_text())
    if cfg.get('supplier_id') == 'zhiyuan-precision':
        require(cfg.get('source_fields'), 'Current supplier requires a complete reviewed source_fields ledger')
    schema=cfg.get('schema_version')
    require(schema in {1,2},'schema_version must equal 1 or 2')
    require(cfg.get('color_profile','legacy-v1') in COLOR_PROFILES,'Unknown color profile')
    require(cfg.get('stroke_profile','source') in STROKE_PROFILES,'Unknown stroke profile')
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
    require('approved_tolerance_visual_trial' not in cfg,
            'Temporary tolerance visual trial is superseded by approved_tolerance_reflow')
    if cfg.get('approved_tolerance_reflow'):
        require(schema==2 and len(tolerance_groups)==1,
                'Approved tolerance reflow needs the complete source tolerance group')
        load_tolerance_reflow(cfg,path)
    if cfg.get('source_fields'):
        require(not cfg.get('approved_tolerance_reflow'), 'Choose one authorized tolerance mode')
        from source_fields import load_source_fields
        cfg['_source_fields']=load_source_fields(cfg,path)
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
        require(not any(k in g for k in ('scale_x','scale_y','stretch','transform')),
                f'Anisotropic or hidden transform is forbidden: {g["id"]}')
        if g['kind'] in {'view','pcb'} and not cfg.get('approved_uniform_view_scales'):
            require(abs(float(g['scale'])-1)<1e-9,
                    'Dimensional view/PCB scale must remain 1.0 without approved NTS evidence')
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
            require(rule.get('line_cap','butt') in {'butt','round','square'},
                    'Invalid restored source rule line cap: '+g['id'])
            if rule['orientation']=='vertical':
                x=float(rule['x']);y0=float(rule['y0']);y1=float(rule['y1'])
                require(extent.x0<=x<=extent.x1 and extent.y0<=y0<y1<=extent.y1
                        and clip_union.x1<=x<=extent.x1+1e-6,
                        f'Invalid restored source rule: {g["id"]}')
                if g['kind']=='table':
                    require(clip_union.x1<float(x)-width/2-.005,
                            f'Restored table edge duplicates source stroke: {g["id"]}')
            else:
                y=float(rule['y']);x0=float(rule['x0']);x1=float(rule['x1'])
                require(extent.y0<=y<=extent.y1 and extent.x0<=x0<x1<=extent.x1
                        and clip_union.y1<=y<=extent.y1+1e-6,
                        f'Invalid restored source rule: {g["id"]}')
                if g['kind']=='table':
                    require(clip_union.y1<float(y)-width/2-.005,
                            f'Restored table edge duplicates source stroke: {g["id"]}')
        for i,a in enumerate(g['clips']):
            for b in g['clips'][i+1:]:
                require((fitz.Rect(a)&fitz.Rect(b)).is_empty,'Same-group clips must not duplicate content')
    validate_approved_uniform_scales(cfg,path)
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
    cfg['_manifest_path']=str(path)  # runtime resolution only; excluded from recipe hash
    return cfg,path,source,assets


def classify_color(rgb,profile='legacy-v1'):
    require(profile in COLOR_PROFILES,'Unknown color profile')
    r,g,b=rgb
    if min(rgb)>=.96: return rgb
    if profile=='cyan-gold-v1':
        # Approved contact-pin highlights only. Do not turn red dimensions gold.
        if g>.6 and b>.6 and r<.4:return (217/255,154/255,0)
        return BLUE
    if max(rgb)-min(rgb)<.10 or (b>r+.08 and b>=g): return BLUE
    return (217/255,154/255,0)


def svg_recolor(svg,profile='legacy-v1'):
    def color(m):
        raw=m.group()[1:]
        if len(raw)==3:raw=''.join(c*2 for c in raw)
        rgb=tuple(int(raw[i:i+2],16)/255 for i in [0,2,4])
        return '#'+''.join(f'{round(v*255):02x}' for v in classify_color(rgb,profile))
    svg=re.sub(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b',color,svg)
    return svg.replace('<svg ',f'<svg fill="{HEX_BLUE}" ',1)


def native_recolor(source,target,profile='legacy-v1',stroke_profile='source'):
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
            if op == 'w' and stroke_profile == 'legacy-thin-stroke-boost-v1' \
                    and len(args) == 1 and 0 < float(args[0]) < .5:
                # Compatibility with an explicitly approved legacy print.
                # This changes stroke weight, never geometry or source values.
                instructions.append(([.5], pikepdf.Operator('w')))
            elif op in {'rg','RG','g','G','k','K'}:
                nums=[float(x) for x in args]
                if op in {'g','G'}: rgb=(nums[0],)*3
                elif op in {'k','K'}:
                    c,m,y,k=nums;rgb=(1-min(1,c+k),1-min(1,m+k),1-min(1,y+k))
                else: rgb=nums
                instructions.append((list(classify_color(rgb,profile)),pikepdf.Operator('RG' if op.isupper() else 'rg')))
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
    profile=cfg.get('color_profile','legacy-v1')
    stroke_profile=cfg.get('stroke_profile','source')
    require(profile in COLOR_PROFILES,'Unknown color profile')
    require(stroke_profile in STROKE_PROFILES,'Unknown stroke profile')
    original=fitz.open(source)
    require(len(original)==cfg['source']['expected_pages'],'Page count changed; review all pages')
    require(len(original)==1,'This single-sheet command requires a reviewed one-page PDF; never silently drops pages')
    require(cfg['source']['page']==1,'One-page manifest page must equal 1')
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(canonical({'sha':digest(source),'page':cfg['source']['page'],
          'rotation':cfg['source']['rotation'],'version':VERSION,'engine':digest(__file__),
          'fitz':fitz.__version__,'renderer':cfg.get('renderer','auto'),
          'color_profile':profile,'stroke_profile':stroke_profile})).hexdigest()[:24]
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
        native_recolor(neutral,colored,profile,stroke_profile)
    except (ImportError,ValueError,RuntimeError):
        if renderer=='native':raise
        used='svg'
        doc=fitz.open(neutral)
        svg=svg_recolor(doc[0].get_svg_image(text_as_path=True),profile)
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
    if not mask.any():
        return []
    labels,count=label(binary_dilation(mask,iterations=1));items=[]
    for component_id,slices in enumerate(find_objects(labels),1):
        if slices is None:continue
        ys,xs=slices;pixels=int((labels[slices]==component_id).sum())
        if pixels<min_pixels:continue
        items.append({'bbox':[round(xs.start/S,3),round(ys.start/S,3),round(xs.stop/S,3),round(ys.stop/S,3)],
                      'pixels':pixels})
    return sorted(items,key=lambda item:item['pixels'],reverse=True)[:limit]


def source_coverage_preflight(cfg, source_page, source_pixels, ps):
    """Whole-sheet accounting before composing any deliverable candidate."""
    a=source_pixels
    ink=a[:,:,:3].min(2)<150
    weak_ink=a[:,:,:3].min(2)<245
    interest=np.zeros(ink.shape,bool);covered=np.zeros_like(interest)
    if cfg.get('schema_version',1)>=2:
        fill(interest,list(source_page.rect))
    else:
        for b in cfg['coverage']['include']:fill(interest,b)
    exclusion_audit=[]
    for ex in cfg['coverage'].get('exclude',[]):
        erase=np.zeros_like(interest);fill(erase,ex['box'])
        text=[]
        box=fitz.Rect(ex['box'])
        for word in source_page.get_text('words'):
            if not (box&fitz.Rect(word[:4])).is_empty:text.append(str(word[4]))
        exclusion_audit.append({'kind':ex.get('kind','legacy'),'box':ex['box'],'reason':ex['reason'],
                                'excluded_ink_pixels':int((erase&weak_ink).sum()),
                                'extractable_text':' '.join(text)[:500]})
        interest &= ~erase
    for p in ps:fill(covered,p['source_box'])
    for rule in restored_rules(cfg):fill(covered,rule['source_box'])
    authorized_fields=[]
    if cfg.get('source_fields'):
        from source_fields import load_source_fields, authorized_field_regions
        evidence=cfg.get('_source_fields') or load_source_fields(cfg,cfg['_manifest_path'])
        authorized_fields=authorized_field_regions(evidence)
        for field in authorized_fields:fill(covered,field['source_box'])
    unplaced_mask=weak_ink&interest&~covered
    unplaced=int(unplaced_mask.sum())
    return {"unplaced":unplaced,"unplaced_mask":unplaced_mask,
            "exclusions":exclusion_audit,"authorized_fields":authorized_fields}


def make_audit(cfg,neutral,colored,output,ps,assets,source_pixels=None,
               expected_pixels=None,source_quality=None):
    src=fitz.open(neutral);out=fitz.open(output)
    require(len(out)==1 and abs(out[0].rect.width-PAGE[0])<.1 and abs(out[0].rect.height-PAGE[1])<.1,
            'Output is not one landscape A4 page')
    a=render_array(src[0]) if source_pixels is None else source_pixels
    coverage=source_coverage_preflight(cfg,src[0],a,ps)
    ink=a[:,:,:3].min(2)<150
    weak_ink=a[:,:,:3].min(2)<245
    unplaced=coverage['unplaced'];unplaced_mask=coverage['unplaced_mask']
    exclusion_audit=coverage['exclusions'];authorized_fields=coverage['authorized_fields']
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
    furniture=np.zeros(act_all.shape,bool)
    raw_source_furniture=np.zeros(act_all.shape,bool)
    # Only in the approved reflow mode, the generated English footer is
    # furniture for *other groups' extra-ink accounting*. Source coverage and
    # all non-tolerance missing-ink checks remain unchanged; this exact cell
    # receives a separate field+approved-visual check below.
    furniture_boxes=(RESERVED if cfg.get('source_fields') or cfg.get('approved_tolerance_reflow') or cfg.get('schema_version',1)<2
                     else RESERVED[:1])
    for b in furniture_boxes:
        fill(furniture,list(b))
    fill(raw_source_furniture,list(RESERVED[0]))
    for h in cfg.get('table_headers',[]):fill(furniture,[h['xs'][0],h['y'][0],h['xs'][-1],h['y'][1]])
    for h in cfg.get('table_headers',[]):fill(raw_source_furniture,[h['xs'][0],h['y'][0],h['xs'][-1],h['y'][1]])
    furniture=binary_dilation(furniture,iterations=2)  # approved frame stroke antialiasing outside its geometric centerline
    raw_source_furniture=binary_dilation(raw_source_furniture,iterations=2)
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
        furniture_for_piece=(raw_source_furniture if p['kind']=='tolerance' and
                             cfg.get('approved_tolerance_reflow') else furniture)
        extra=act & ~expected_allowed[ys:ye,xs:xe] & ~furniture_for_piece[ys:ye,xs:xe]
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
    tolerance_reflow=None
    if cfg.get('approved_tolerance_reflow'):
        ref=load_tolerance_reflow(cfg,cfg['_manifest_path'])
        footer=out[0].get_text(clip=fitz.Rect(395,488,493,566))
        actual=[]
        for line in footer.splitlines():
            match=re.fullmatch(r'(X\.(?:X){0,3})\s+(±\d+\.\d{2})',line.strip())
            if match:actual.append(match[1]+' '+match[2])
        headings=(footer.count('UNLESS OTHERWISE')==1 and
                  footer.count('SPECIFIED, TOLERANCE:')==1)
        fields_ok=headings and actual==ref['expected_lines']
        box=fitz.Rect(395,488,493,566)
        with fitz.open(ref['approved_pdf']) as approved:
            approved_pix=approved[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=box,alpha=False)
        actual_pix=out[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=box,alpha=False)
        visual_ok=approved_pix.samples==actual_pix.samples
        tol_ids={g['id'] for g in cfg['groups'] if g['kind']=='tolerance'}
        projection_checks=[c for c in checks if c['id'] in
                           {g['id'] for g in cfg['groups'] if g['kind']=='projection'}]
        projection_ok=bool(projection_checks) and all(c['pass'] for c in projection_checks)
        external_ids={item['carrying_group_id'] for item in ref['evidence'].get('additional_technical_conditions',[])}
        external_checks=[c for c in checks if c['id'] in external_ids]
        external_ok=(not external_ids or (bool(external_checks) and all(c['pass'] for c in external_checks)))
        tolerance_reflow={'mode':'approved_english_source_bound','pass':fields_ok and visual_ok and projection_ok and external_ok,
            'source_map_sha256':ref['source_map_sha256'],
            'approved_pdf_sha256':ref['approved_pdf_sha256'],
            'source_fields':ref['expected_lines'],'actual_fields':actual,
            'source_unit':ref['evidence']['source_unit'],
            'headings_pass':headings,'approved_visual_pixels_equal':visual_ok,
            'projection_source_group_pass':projection_ok,
            'additional_technical_conditions':ref['evidence'].get('additional_technical_conditions',[]),
            'additional_technical_condition_groups_pass':external_ok,
            'source_review_status':'REQUIRES_INDEPENDENT_REVIEW',
            'raw_original_tolerance_checks':[c for c in checks if c['id'] in tol_ids]}
        group_ok=all(c['pass'] for c in checks if c['id'] not in tol_ids) and tolerance_reflow['pass']
    elif cfg.get('source_fields'):
        from source_fields import load_source_fields, audit_source_fields
        evidence=cfg.get('_source_fields') or load_source_fields(cfg,cfg['_manifest_path'])
        tolerance_reflow=audit_source_fields(out[0],evidence)
        tol_ids={g['id'] for g in cfg['groups'] if g['kind']=='tolerance'}
        tolerance_reflow['raw_original_tolerance_checks']=[c for c in checks if c['id'] in tol_ids]
        group_ok=all(c['pass'] for c in checks if c['id'] not in tol_ids) and tolerance_reflow['pass']
    else:
        group_ok=all(c['pass'] for c in checks)
    restored_vector_checks=restored_rule_vector_checks(out[0],cfg)
    restored_vectors_ok=all(item['pass'] for item in restored_vector_checks)
    ok=unplaced==0 and title_ok and global_pass and group_ok and restored_vectors_ok
    return {'pass':ok,'scope':'Raw source-table raster mismatch is retained in checks for approved reflow; only the source-bound English tolerance region uses field and approved-visual checks. Independent source/final review remains required.',
       'source_sha256':cfg['source']['sha256'],'output_sha256':digest(output),
       'inventory_sha256':inventory_hash(cfg),'source_inventory_sha256':source_inventory_hash(cfg),
       'source_unplaced_technical_ink_pixels':unplaced,
       'authorized_source_field_regions':authorized_fields,
       'title_model_text_present':title_ok,'frame_field_occurrences':field_counts,
       'semantic_frame_fields_pass':title_ok,'mask_scale':S,'edge_pixels':EDGE,'dilation_pixels':DILATE,
       'max_missing_ratio_exclusive':MAX_MISSING,'color_mask':'background-relative blue/gold alpha; source-equivalent cutoff 1-150/255',
       'global_expected_missing_pixels':global_missing_count,
       'global_unexpected_ink_pixels':global_extra_count,'global_page_match_pass':global_pass,
       'source_unplaced_components':mask_components(unplaced_mask),
       'global_missing_components':mask_components(global_missing),
       'global_unexpected_components':mask_components(global_extra),
       'global_pixel_tolerance':GLOBAL_PIXEL_TOLERANCE,
       'brand_effective_dpi':(brand:=brand_profile(assets['brand_strip']))['effective_dpi'],
       'brand_profile':brand,
       'source_quality':source_quality or [],'exclusions':exclusion_audit,'engine_version':VERSION,
       'checks':checks,'approved_tolerance_reflow':tolerance_reflow,
       'restored_rule_vector_checks':restored_vector_checks,
       'restored_rule_vectors_pass':restored_vectors_ok}


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
    visual_trial=cfg.get('approved_tolerance_reflow')
    source_fields=cfg.get('source_fields')
    for item in ps:
        if item['kind']!='projection' and not ((visual_trial or source_fields) and item['kind']=='tolerance'):
            page.show_pdf_page(fitz.Rect(item['target_box']),color,0,clip=fitz.Rect(item['source_box']))
    draw_table_headers(page,cfg)
    fields=dict(cfg['fields']);fields['cjk_font_file']=assets['font']
    if visual_trial:
        ref=load_tolerance_reflow(cfg,cfg.get('_manifest_path',''))
        fields['tolerances']=ref['layout']['fields']['tolerances']
    draw_frame_and_title(page,fields,assets,
                         tolerance_mode='legacy' if visual_trial or cfg.get('schema_version',1)==1 else 'source')
    if source_fields:
        from source_fields import load_source_fields
        from dynamic_tolerance import render_dynamic_tolerance
        evidence=cfg.get('_source_fields') or load_source_fields(cfg,cfg['_manifest_path'])
        render_dynamic_tolerance(page,evidence['tolerance_schema'])
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


def _build_generate(args):
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
    coverage=source_coverage_preflight(cfg,n[0],source_pixels,ps)
    save_json(outdir/'source-inventory-gate.json',{
        'status':'SOURCE_INVENTORY_PASS' if coverage['unplaced']==0 else 'SOURCE_INVENTORY_BLOCKED',
        'source_sha256':cfg['source']['sha256'],
        'scope':'complete_original_page',
        'unplaced_technical_ink_pixels':coverage['unplaced'],
        'unplaced_regions':mask_components(coverage['unplaced_mask']),
        'authorized_source_field_regions':coverage['authorized_fields'],
        'nontechnical_exclusions':coverage['exclusions']})
    require(coverage['unplaced']==0,'FAIL_SOURCE_COMPLETENESS before generation; inspect source-inventory-gate.json')
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


def _draft_generate(args):
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


CONTROL_SCHEMA = 2
ARTIFACTS = {
    'draft': ('draft.pdf', 'draft-audit.json', 'draft-board.png', 'draft-details.png', 'draft-preview.png'),
    'build': ('drawing.pdf', 'audit.json', 'review-board.png', 'review-details.png',
              'preview.png', 'layout.json', 'run.json', 'final-review-template.json')
}


def _control_path(args):
    require(args.control_root, 'A stable project --control-root is required for draft/build/batch')
    root=Path(args.control_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextlib.contextmanager
def _locked_state(root):
    lock=(root/'batch-state.lock').open('a+')
    try:
        deadline=time.monotonic()+10
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                require(time.monotonic()<deadline,
                        'Project control ledger lock timed out; inspect the writer')
                time.sleep(.02)
        path=root/'batch-state.json'
        if path.exists():
            state=json.loads(path.read_text())
            require(state.get('schema_version')==CONTROL_SCHEMA,
                    'Legacy/unknown batch-state.json: migration is not supported; preserve it and stop')
            require(isinstance(state.get('runs'),dict) and isinstance(state.get('jobs'),dict)
                    and isinstance(state.get('events'),list), 'Control ledger structure is invalid')
        else:
            state={'schema_version':CONTROL_SCHEMA,'runs':{},'jobs':{},'events':[]}
        yield state
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _persist_state(root,state):
    state['updated_at_epoch']=int(time.time())
    path=root/'batch-state.json'
    temp=path.with_suffix('.tmp')
    save_json(temp,state)
    temp.replace(path)


def _entry(state,record_id,stage,entry):
    state['jobs'].setdefault(record_id,{})[stage]=entry


def _identity(cfg,record_id=None):
    ident=cfg.get('identity',{})
    value=ident.get('record_id')
    require(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9._-]+',value),
            'Verified identity.record_id is required')
    require(record_id is None or value==record_id,'Batch id and manifest identity.record_id differ')
    sha=cfg.get('source',{}).get('sha256')
    require(isinstance(sha,str) and re.fullmatch(r'[0-9a-f]{64}',sha),
            'Verified source SHA256 is required')
    return value,sha


def _success_valid(success,recipe,stage):
    if not success or success.get('inventory_sha256')!=recipe:
        return False
    out=Path(success.get('run_dir',''))
    try:
        if any(not (out/name).is_file() for name in ARTIFACTS[stage]):
            return False
        if any(digest(out/name)!=success.get('files',{}).get(name) for name in ARTIFACTS[stage]):
            return False
        if digest(out/ARTIFACTS[stage][0])!=success.get('artifact_sha256'):
            return False
        audit=json.loads((out/ARTIFACTS[stage][1]).read_text())
        if audit.get('pass') is not True or digest(out/ARTIFACTS[stage][1])!=success.get('audit_sha256'):
            return False
        if stage=='build':
            run=json.loads((out/'run.json').read_text())
            if run.get('inventory_sha256')!=recipe or run.get('output_sha256')!=success['artifact_sha256']:
                return False
    except (OSError,ValueError,KeyError):
        return False
    return True


def _release_ready(success):
    out=Path(success['run_dir'])
    try:
        release=json.loads((out/'release.json').read_text())
        review=json.loads((out/'review.snapshot.json').read_text())
        return (release.get('release_ready') is True
                and release.get('inventory_sha256')==success['inventory_sha256']
                and release.get('output_sha256')==success['artifact_sha256']
                and all((out/name).is_file() for name in ('verify.json','review.snapshot.json'))
                and release.get('verify_sha256')==digest(out/'verify.json')
                and review.get('verdict')=='PASS'
                and review.get('inventory_sha256')==success['inventory_sha256']
                and review.get('output_sha256')==success['artifact_sha256']
                and json.loads((out/'verify.json').read_text()).get('pass') is True)
    except (OSError,ValueError,KeyError):
        return False


def _result(record_id,stage,status,attempts,evidence,seconds=0,error_category=None):
    return {'product_id':record_id,'stage':stage,'status':status,'seconds':round(seconds,3),
            'attempts':attempts,'error_category':error_category,'evidence_path':str(evidence)}


def _run_stage(args,stage,record_id=None,output_for_attempt=None):
    root=_control_path(args)
    manifest=Path(args.manifest).resolve()
    # Validate identity and source bytes before opening the ledger or starting expensive PDF work.
    cfg=json.loads(manifest.read_text())
    record_id,source_sha=_identity(cfg,record_id)
    source=resolve(manifest.parent,cfg['source']['path'])
    require(source.is_file(),'Source PDF missing')
    require(digest(source)==source_sha,'Source hash changed; inspect again')
    # Manifest/source-review errors are cheap preflight failures, not generation attempts.
    checked_cfg,*_=read_manifest(manifest,check_review=(stage=='build'))
    recipe=inventory_hash(checked_cfg)
    key=f'{record_id}|{source_sha}|{stage}'
    with _locked_state(root) as state:
        history=state['runs'].setdefault(key,{'attempts':0,'last_recipe':None,'successes':{}})
        attempts=history['attempts']
        success=history['successes'].get(recipe)
        if _success_valid(success,recipe,stage):
            status='RELEASE_READY' if stage=='build' and _release_ready(success) else (
                'AUTO_QA_PASS' if stage=='build' else 'DRAFT_QA_PASS')
            entry={'status':status,'attempts':attempts,'inventory_sha256':recipe,'run_dir':success['run_dir']}
            _entry(state,record_id,stage,entry);_persist_state(root,state)
            return _result(record_id,stage,'REUSED_'+status,attempts,success['run_dir'])
        if history.get('last_status')=='RUNNING':
            pid=history.get('running_pid')
            try:
                if pid:os.kill(pid,0)
                alive=bool(pid)
            except ProcessLookupError:
                alive=False
            if alive:
                return _result(record_id,stage,'IN_PROGRESS',attempts,
                               history.get('last_run_dir',root/'batch-state.json'))
            # A terminated writer consumed its reserved attempt.  The next
            # attempt remains subject to the same two-attempt ceiling.
            history['last_status']='INTERRUPTED'
            state['events'].append({'record_id':record_id,'source_sha256':source_sha,
                'stage':stage,'attempt':attempts,'status':'INTERRUPTED','epoch':int(time.time())})
            _persist_state(root,state)
        if history.get('last_recipe')==recipe and history.get('last_status') in {'AUTO_QA_FAIL','PREFLIGHT_FAIL'} and not args.allow_retry:
            return _result(record_id,stage,'UNCHANGED_FAILURE',attempts,history.get('last_run_dir',root),
                           error_category=history.get('last_error_category'))
        if attempts>=2:
            entry={'status':'NEEDS_REVIEW_RETRY_LIMIT','attempts':attempts,'inventory_sha256':recipe}
            _entry(state,record_id,stage,entry);_persist_state(root,state)
            return _result(record_id,stage,'NEEDS_REVIEW_RETRY_LIMIT',attempts,root/'batch-state.json')
        run=Path(output_for_attempt(attempts+1,recipe) if output_for_attempt else args.output).resolve()
        if stage=='build':
            prior=ARTIFACTS['build']+('candidate.pdf','release.json','verify.json',
                'review.snapshot.json','manifest.snapshot.json')
            require(not any((run/name).exists() for name in prior),
                    'Build output contains prior artifacts; choose a new directory')
        run.mkdir(parents=True,exist_ok=True)
        token=uuid.uuid4().hex
        # Reserve under the project lock, then release it for expensive PDF/QA.
        history.update(attempts=attempts+1,last_recipe=recipe,last_run_dir=str(run),
                       last_status='RUNNING',running_pid=os.getpid(),running_token=token)
        _entry(state,record_id,stage,{'status':'RUNNING','attempts':attempts+1,
                'inventory_sha256':recipe,'run_dir':str(run)})
        state['events'].append({'record_id':record_id,'source_sha256':source_sha,'stage':stage,
            'attempt':attempts+1,'status':'RUNNING','inventory_sha256':recipe,'epoch':int(time.time())})
        _persist_state(root,state)

    log=run/f'{stage}-generation-a{attempts+1}.log'
    start=time.perf_counter()
    success_data=None
    try:
        ns=argparse.Namespace(manifest=str(manifest),output=str(run),cache=args.cache)
        with log.open('w') as stream,contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
            (_build_generate if stage=='build' else _draft_generate)(ns)
        artifact=run/ARTIFACTS[stage][0]
        audit=run/ARTIFACTS[stage][1]
        require(artifact.is_file() and audit.is_file(),'Generation artifacts incomplete')
        status='AUTO_QA_PASS' if stage=='build' else 'DRAFT_QA_PASS'
        success_data={'inventory_sha256':recipe,'run_dir':str(run),
            'artifact_sha256':digest(artifact),'audit_sha256':digest(audit),
            'files':{name:digest(run/name) for name in ARTIFACTS[stage]}}
        error=None
    except Exception as exc:
        with log.open('a') as stream:
            stream.write('\n'+traceback.format_exc())
        status='AUTO_QA_FAIL' if (run/ARTIFACTS[stage][1]).exists() else 'PREFLIGHT_FAIL'
        error=type(exc).__name__
        error_message=str(exc)
    elapsed=time.perf_counter()-start
    with _locked_state(root) as state:
        history=state['runs'][key]
        require(history.get('running_token')==token and history.get('last_status')=='RUNNING',
                'Run reservation changed during generation; inspect project ledger')
        if success_data is not None:history['successes'][recipe]=success_data
        history['last_status']=status;history['last_error_category']=error
        history.pop('running_pid',None);history.pop('running_token',None)
        entry={'status':status,'attempts':attempts+1,'inventory_sha256':recipe,'run_dir':str(run),
               'seconds':round(elapsed,3),'error_category':error}
        _entry(state,record_id,stage,entry)
        state['events'].append({'record_id':record_id,'source_sha256':source_sha,'stage':stage,
            'attempt':attempts+1,'status':status,'inventory_sha256':recipe,'epoch':int(time.time()),
            'evidence_path':str(log),'error_category':error})
        _persist_state(root,state)
    result=_result(record_id,stage,status,attempts+1,log,elapsed,error)
    if error:result['_error_message']=error_message
    return result


def draft(args):
    result=_run_stage(args,'draft')
    message=result.pop('_error_message',None)
    print(json.dumps(result,ensure_ascii=False))
    if message:print('STOP: '+message,file=sys.stderr)
    return result


def build(args):
    result=_run_stage(args,'build')
    message=result.pop('_error_message',None)
    print(json.dumps(result,ensure_ascii=False))
    if message:print('STOP: '+message,file=sys.stderr)
    return result


def reset_attempts(args):
    """Explicit, audited administrative reset; never invoked by generation."""
    root=_control_path(args)
    require(len(args.reason.strip())>=12,'Reset reason needs at least 12 characters')
    require(args.operator.strip(),'Reset operator is required')
    require(re.fullmatch(r'[A-Za-z0-9._-]+',args.record_id),'Invalid record ID')
    require(re.fullmatch(r'[0-9a-f]{64}',args.source_sha256),'Invalid source SHA256')
    key=f'{args.record_id}|{args.source_sha256}|{args.stage}'
    with _locked_state(root) as state:
        require(key in state['runs'],'No matching attempt history to reset')
        before=state['runs'][key].copy()
        state['events'].append({'status':'MANUAL_RESET','record_id':args.record_id,
            'source_sha256':args.source_sha256,'stage':args.stage,'operator':args.operator,
            'reason':args.reason,'previous':before,'epoch':int(time.time())})
        state['runs'][key]={'attempts':0,'last_recipe':None,'successes':{},'last_status':'MANUAL_RESET'}
        _persist_state(root,state)
    print(json.dumps({'product_id':args.record_id,'stage':args.stage,'status':'MANUAL_RESET',
        'attempts':0,'evidence_path':str(root/'batch-state.json')},ensure_ascii=False))


def batch(args):
    """Use the same project ledger and generation limits as direct entry points."""
    spec_path=Path(args.jobs).resolve();spec=json.loads(spec_path.read_text())
    if 'products' in spec:
        from stable_batch import run_products
        return run_products(args,spec,spec_path)
    require(isinstance(spec.get('jobs'),list) and spec['jobs'],'Batch file needs a non-empty jobs list')
    require(args.max_attempts==2,'The generation ceiling is fixed at two; --max-attempts cannot override it')
    root=Path(args.output_root).resolve();root.mkdir(parents=True,exist_ok=True)
    control=_control_path(args)
    with _locked_state(control):pass  # Reject legacy ledger before any batch work.
    ids=[str(job.get('id','')) for job in spec['jobs']]
    require(all(re.fullmatch(r'[A-Za-z0-9._-]+',x) for x in ids),'Batch ids must be filesystem-safe')
    require(len(ids)==len(set(ids)),'Batch ids must be unique')
    pilots=[j for j in spec['jobs'] if j.get('pilot')]
    require(pilots,'Batch needs at least one pilot; expansion cannot bypass the pilot gate')
    ordered=pilots+[j for j in spec['jobs'] if not j.get('pilot')]
    results=[]
    for job in ordered:
        manifest=resolve(spec_path.parent,job['manifest'])
        ns=argparse.Namespace(manifest=str(manifest),output=None,cache=args.cache,
                              control_root=args.control_root,allow_retry=args.allow_retry)
        if job not in pilots and pilots:
            with _locked_state(control) as state:
                needed='DRAFT_QA_PASS' if args.mode=='draft' else 'RELEASE_READY'
                ready=all(state['jobs'].get(p['id'],{}).get(args.mode,{}).get('status')==needed for p in pilots)
                if not ready:
                    _entry(state,job['id'],args.mode,{'status':'BLOCKED_PILOT_GATE'})
                    _persist_state(control,state)
            if not ready:
                results.append(_result(job['id'],args.mode,'BLOCKED_PILOT_GATE',0,control/'batch-state.json'))
                continue
        try:
            result=_run_stage(ns,args.mode,job['id'],
                lambda n,recipe:root/job['id']/f'{recipe[:16]}-{args.mode}-a{n}')
        except Exception as exc:
            # Identity/source errors are preflight failures, not attempts.
            result=_result(job['id'],args.mode,'PREFLIGHT_FAIL',0,control/'batch-state.json',
                           error_category=type(exc).__name__)
            with _locked_state(control) as state:
                _entry(state,job['id'],args.mode,{'status':'PREFLIGHT_FAIL','attempts':0,
                    'error_category':type(exc).__name__,'message':str(exc)})
                state['events'].append({'record_id':job['id'],'stage':args.mode,'status':'PREFLIGHT_FAIL',
                    'message':str(exc),'epoch':int(time.time())})
                _persist_state(control,state)
        result.pop('_error_message',None)
        results.append(result)
        print(json.dumps(result,ensure_ascii=False))
    save_json(root/'job-summary.json',{'results':results,'state':str(control/'batch-state.json')})


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


def recheck_candidate(args):
    """Audit an existing draft with current rules, without a generation attempt."""
    started=time.perf_counter()
    cfg,mp,source,assets=read_manifest(args.manifest,check_review=False)
    output=Path(args.pdf).resolve();require(output.is_file(),'Candidate PDF missing')
    report=Path(args.report).resolve()
    recipe=inventory_hash(cfg);output_sha=digest(output)
    if report.is_file():
        old=json.loads(report.read_text())
        if (old.get('recipe_sha256')==recipe and old.get('output_sha256')==output_sha
                and old.get('engine_sha256')==digest(__file__)
                and old.get('frame_sha256')==digest(Path(__file__).with_name('frame.py'))
                and old.get('automatic_pass') is True):
            print(json.dumps({'product_id':cfg['identity']['record_id'],'stage':'candidate-recheck',
                'status':'REUSED_AUTO_QA_PASS_REVIEW_REQUIRED','seconds':round(time.perf_counter()-started,3),
                'evidence_path':str(report)},ensure_ascii=False))
            return
    cache=args.cache or output.parent/'.cache'
    neutral,colored,_,_=cached_source(cfg,source,cache)
    source_pixels,_=cached_render(neutral,cache)
    with fitz.open(neutral) as doc:
        ps,source_quality=check_geometry(cfg,doc[0],source_pixels)
    expected_doc,_=compose_document(cfg,colored,assets,ps)
    try:
        audit=make_audit(cfg,neutral,colored,output,ps,assets,source_pixels,
                         render_array(expected_doc[0]),source_quality)
    finally:expected_doc.close()
    value={'product_id':cfg['identity']['record_id'],'stage':'candidate-recheck',
        'automatic_pass':audit['pass'],'independent_source_review':'REQUIRED',
        'independent_final_review':'REQUIRED','recipe_sha256':recipe,
        'engine_sha256':digest(__file__),'frame_sha256':digest(Path(__file__).with_name('frame.py')),
        'source_sha256':digest(source),'output_sha256':output_sha,
        'manifest_sha256':digest(mp),'check_seconds':round(time.perf_counter()-started,3),
        'audit':audit}
    save_json(report,value)
    print(json.dumps({'product_id':value['product_id'],'stage':value['stage'],
        'status':'AUTO_QA_PASS_REVIEW_REQUIRED' if audit['pass'] else 'AUTO_QA_FAIL',
        'seconds':value['check_seconds'],'evidence_path':str(report)},ensure_ascii=False))
    require(audit['pass'],'Candidate recheck failed')


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
        'identity':{'record_id':'','expected_model':args.model,'observed_model':'','model_evidence':'','observed_parts':[],
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
    from approved_recipe import register_cli
    register_cli(subs)
    from source_fields import register_cli as register_source_fields_cli
    register_source_fields_cli(subs)
    from portable_handoff import register_cli as register_handoff_cli
    register_handoff_cli(subs)
    p=subs.add_parser('init');p.add_argument('source');p.add_argument('--manifest',required=True);p.add_argument('--model',required=True);p.add_argument('--rotation',type=int,choices=[0,90,180,270],required=True);p.set_defaults(func=init)
    p=subs.add_parser('draft');p.add_argument('manifest');p.add_argument('--output',required=True);p.add_argument('--cache');p.add_argument('--control-root',required=True);p.add_argument('--allow-retry',action='store_true');p.set_defaults(func=draft)
    p=subs.add_parser('build');p.add_argument('manifest');p.add_argument('--output',required=True);p.add_argument('--cache');p.add_argument('--control-root',required=True);p.add_argument('--allow-retry',action='store_true');p.set_defaults(func=build)
    p=subs.add_parser('batch');p.add_argument('jobs');p.add_argument('--output-root',required=True);p.add_argument('--control-root',required=True);p.add_argument('--cache');p.add_argument('--mode',choices=['draft','build'],default='build');p.add_argument('--max-attempts',type=int,default=2);p.add_argument('--allow-retry',action='store_true');p.add_argument('--golden-ledger');p.add_argument('--catalog-dir');p.add_argument('--workers',type=int,default=1);p.add_argument('--runtime-126',help='Pinned Python with PyMuPDF 1.26.5 for exact legacy visual replay');p.set_defaults(func=batch)
    p=subs.add_parser('reset-attempts');p.add_argument('--control-root',required=True);p.add_argument('--record-id',required=True);p.add_argument('--source-sha256',required=True);p.add_argument('--stage',choices=['draft','build'],required=True);p.add_argument('--operator',required=True);p.add_argument('--reason',required=True);p.set_defaults(func=reset_attempts)
    p=subs.add_parser('verify');p.add_argument('manifest');p.add_argument('pdf');p.add_argument('--cache');p.add_argument('--review');p.add_argument('--report');p.set_defaults(func=verify)
    p=subs.add_parser('recheck-candidate');p.add_argument('manifest');p.add_argument('pdf');p.add_argument('--cache');p.add_argument('--report',required=True);p.set_defaults(func=recheck_candidate)
    p=subs.add_parser('inventory-hash');p.add_argument('manifest');p.set_defaults(func=lambda a: print(inventory_hash(json.loads(Path(a.manifest).read_text()))))
    p=subs.add_parser('hashes');p.add_argument('manifest');p.set_defaults(func=lambda a: print(json.dumps({
        'source_inventory_sha256':source_inventory_hash(json.loads(Path(a.manifest).read_text())),
        'inventory_sha256':inventory_hash(json.loads(Path(a.manifest).read_text()))},ensure_ascii=False)))
    args=parser.parse_args()
    try:
        result=args.func(args)
        if isinstance(result,dict) and result.get('status') in {
                'PREFLIGHT_FAIL','AUTO_QA_FAIL','NEEDS_REVIEW_RETRY_LIMIT','UNCHANGED_FAILURE'}:
            return 2
    except (ValueError,KeyError,FileNotFoundError) as exc:
        print('STOP: '+str(exc),file=sys.stderr);return 2
    return 0


if __name__=='__main__':sys.exit(main())
