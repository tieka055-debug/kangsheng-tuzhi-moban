"""Source-bound semantic fields; PDF text extraction alone is not inventory.

Outlined CAD glyphs are common in tolerance cells. Only a hash-bound,
independently visually reviewed ledger authorizes replacing those vectors.
No supplier-wide numeric defaults and no automatic review signatures.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pymupdf as fitz
from dynamic_tolerance import normalize_tolerance_schema, plan_dynamic_tolerance, audit_dynamic_tolerance

SCHEMA = 'kangsheng-source-fields-v1'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def semantic_digest(evidence):
    value=json.loads(json.dumps(evidence))
    value['review'].pop('path',None)
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def require(value, message):
    if not value:
        raise ValueError('SOURCE_FIELDS_BLOCKED: ' + message)

def resolve(base, path):
    p = Path(path).expanduser()
    return p if p.is_absolute() else base / p

def crop_hash(page, box):
    r = fitz.Rect(box)
    require(not r.is_empty and page.rect.contains(r), 'source region outside page')
    pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=r, alpha=False)
    return hashlib.sha256(pix.samples).hexdigest()

def load_source_fields(cfg, manifest_path):
    ref = cfg.get('source_fields')
    if not ref:
        return None
    base = Path(manifest_path).resolve().parent
    evidence_path = resolve(base, ref['path'])
    require(sha(evidence_path) == ref['sha256'], 'ledger changed')
    e = json.loads(evidence_path.read_text())
    require(semantic_digest(e) == ref.get('semantic_sha256'), 'semantic ledger digest differs')
    require(e.get('schema') == SCHEMA, 'unknown ledger schema')
    require(e.get('source_sha256') == cfg['source']['sha256'], 'ledger belongs to another source')
    require(e.get('rotation') == cfg['source']['rotation'], 'source orientation changed')
    require(e.get('pymupdf') == fitz.VersionBind, 'render runtime differs; recheck source crop fingerprints')
    font_path=resolve(base,cfg['assets']['font'])
    require(font_path.read_bytes()[:4] != b'ttcf', 'title font must be standalone TTF/OTF, not a TTC collection')
    title_font=fitz.Font(fontfile=str(font_path))
    require(all(title_font.has_glyph(ord(c)) for key in ('model','title') for c in cfg['fields'][key]), 'title font is missing glyphs')
    source = resolve(base, cfg['source']['path'])
    require(sha(source) == e['source_sha256'], 'source bytes changed')
    schema = normalize_tolerance_schema(e['tolerance_schema'])
    plan_dynamic_tolerance(schema)  # full ledger must fit, never truncate
    review_path = resolve(evidence_path.parent, e['review']['path'])
    require(sha(review_path) == e['review']['sha256'], 'independent field review changed')
    review = json.loads(review_path.read_text())
    require(review.get('reviewer_identifier'), 'independent reviewer missing')
    rows = [r for r in review.get('rows', []) if r.get('source_sha256') == e['source_sha256']]
    require(len(rows) == 1, 'source must have exactly one independent field review')
    row = rows[0]
    require(row.get('field_review_status') == 'PASS_VISUAL_FIELD_COMPARISON', 'source fields still need visual review')
    require(normalize_tolerance_schema(row['tolerance_schema']) == schema, 'ledger omits or changes reviewed tolerance fields')
    for key in ('model', 'title', 'unit'):
        require(e['fields'][key] == row[key] == cfg['fields'][key], f'{key} differs from its own source')
    groups = [g for g in cfg['groups'] if g['kind'] == 'tolerance']
    require(len(groups) == 1, 'one complete source tolerance group required')
    require(fitz.Rect(e['regions']['tolerance']['box']).contains(fitz.Rect(groups[0]['reviewed_source_extent'])),
            'ledger crop must cover the complete tolerance source group')
    require(set(e['regions']) == {'tolerance', 'identity'}, 'complete tolerance and identity regions required')
    with fitz.open(source) as doc:
        require(len(doc) == 1, 'single-page source required')
        page = doc[0]; page.set_rotation(e['rotation']); page.remove_rotation()
        for name, region in e['regions'].items():
            require(crop_hash(page, region['box']) == region['raster_sha256'], name + ' source fingerprint changed')
        # Precise technical title cells are replacements, never furniture.
        # Optional for historical recipes; new ledgers can account for them
        # without excluding the entire original title block.
        field_regions=e.get('field_regions', {})
        require(isinstance(field_regions, dict), 'field_regions must be an object')
        for name, region in field_regions.items():
            require(name in {'model','title','unit','sheet','scale_text','size'}, 'unknown replacement field')
            require(name in e['fields'] and name in row, 'replacement field lacks reviewed value')
            require(e['fields'][name] == row[name] == cfg['fields'][name], name + ' replacement value differs')
            require(row.get('field_regions', {}).get(name) == region, name + ' source region not independently reviewed')
            require(crop_hash(page, region['box']) == region['raster_sha256'], name + ' replacement fingerprint changed')
            r=fitz.Rect(region['box'])
            require(all((r & fitz.Rect(ex['box'])).is_empty for ex in cfg.get('coverage',{}).get('exclude',[])),
                    name + ' technical field overlaps nontechnical exclusion')
            require(all((r & fitz.Rect(b)).is_empty for g in cfg['groups'] for b in g.get('clips',[])),
                    name + ' replacement duplicates a carried group')
        # A source identity may be curves too; the visual ledger remains the
        # authority. Searchable titles, when present, are additional evidence.
        text = ''.join(page.get_text(clip=fitz.Rect(e['regions']['identity']['box'])).split())
        if e.get('identity_text_required', True):
            for key in ('model', 'title'):
                require(''.join(e['fields'][key].split()) in text, f'complete source {key} not found in identity region')
    return e

def authorized_field_regions(evidence):
    """Use only after load_source_fields has validated the reviewed ledger."""
    return [{'field':name, 'source_box':region['box'], 'expected':evidence['fields'][name],
             'classification':'AUTHORIZED_TRANSFORM'}
            for name,region in evidence.get('field_regions',{}).items()]

def audit_source_fields(page, evidence):
    tolerance = audit_dynamic_tolerance(page, plan_dynamic_tolerance(evidence['tolerance_schema']))
    boxes = {'model': [491, 541, 635, 563], 'title': [696, 505, 813, 524], 'unit': [759, 527, 820, 545.5]}
    for name,box in {'sheet':[701,545.5,775,564], 'scale_text':[679,527,759,545.5],
                     'size':[638,545.5,701,564]}.items():
        if name in evidence.get('field_regions',{}):boxes[name]=box
    identity = {}
    for key, box in boxes.items():
        actual = ''.join(page.get_text(clip=fitz.Rect(box)).split())
        expected = ''.join(evidence['fields'][key].split())
        if key == 'unit': expected = 'UNIT:' + expected
        if key in {'sheet','scale_text','size'}:
            expected={'sheet':'SHEET:','scale_text':'SCALE:','size':'SIZE:'}[key]+expected
        identity[key] = {'expected': expected, 'actual': actual, 'pass': actual == expected}
    font_checks=[]
    for info in page.get_fonts(full=True):
        if info[4] == 'ks-title-font':
            _,extension,_,data=page.parent.extract_font(info[0])
            font_checks.append({'name':info[3],'bytes':len(data),
                                'pass':bool(data) and data[:4]!=b'ttcf' and extension in ('ttf','otf','cff')})
    font_ok=bool(font_checks) and all(x['pass'] for x in font_checks)
    ok=tolerance['pass'] and all(x['pass'] for x in identity.values()) and font_ok
    return {'pass': ok,
            'status': 'SOURCE_FIELDS_PASS' if ok else 'FAIL_SOURCE_FIELDS',
            'embedded_title_fonts':font_checks, 'title_font_embedding_pass':font_ok,
            'source_sha256': evidence['source_sha256'], 'tolerance': tolerance, 'identity': identity,
            'basis': 'independently reviewed full source cells, including outlined glyphs; not regex-extracted subset',
            'engineering_release': False}

def inspect_fields(args):
    """Read-only source view for a reviewer. Never autofill PASS or values."""
    src = Path(args.source).resolve(); out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with fitz.open(src) as doc:
        require(len(doc) == 1, 'single-page source required')
        p = doc[0]; p.set_rotation(args.rotation); p.remove_rotation()
        box = [float(x) for x in args.box.split(',')]
        p.get_pixmap(matrix=fitz.Matrix(4,4), clip=fitz.Rect(box)).save(out/'source-fields.png')
        result = {'schema': SCHEMA, 'source_sha256': sha(src), 'rotation': args.rotation,
                  'pymupdf': fitz.VersionBind, 'box': box, 'raster_sha256': crop_hash(p, box),
                  'extractable_text_only': p.get_text(clip=fitz.Rect(box)),
                  'status': 'SOURCE_FIELDS_REVIEW',
                  'warning': 'CAD curves are not included in extracted text. Review the entire rendered cell.'}
    (out/'inspection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))

def recheck_fields(args):
    manifest = Path(args.manifest).resolve(); cfg = json.loads(manifest.read_text())
    evidence = load_source_fields(cfg, manifest)
    require(evidence, 'source_fields ledger required')
    with fitz.open(args.pdf) as doc:
        require(len(doc) == 1, 'single page output required')
        report = audit_source_fields(doc[0], evidence)
    report['output_sha256'] = sha(args.pdf)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))
    require(report['pass'], 'candidate is missing or changed source fields')

def prepare_title_font(args):
    from fontTools import subset
    from fontTools.ttLib import TTFont
    evidence=json.loads(Path(args.ledger).read_text())
    text=''.join(evidence['fields'][k] for k in ('model','title'))
    font=TTFont(args.font,fontNumber=args.font_index)
    cmap=font.getBestCmap()
    require(all(ord(c) in cmap for c in text), 'source font lacks requested title glyphs')
    sub=subset.Subsetter();sub.populate(text=text);sub.subset(font)
    output=Path(args.output)
    require(not output.exists(), 'choose a new title font output path')
    output.parent.mkdir(parents=True,exist_ok=True);font.save(output)
    print(json.dumps({'status':'TITLE_FONT_READY','path':str(output),'sha256':sha(output),
                      'font_index':args.font_index,'scope':'source-bound title/model glyph subset'},ensure_ascii=False))

def register_cli(subs):
    p=subs.add_parser('prepare-title-font', help='Create a standalone source-bound title font, not a TTC collection')
    p.add_argument('ledger');p.add_argument('--font',required=True);p.add_argument('--font-index',type=int,default=0)
    p.add_argument('--output',required=True);p.set_defaults(func=prepare_title_font)
    p = subs.add_parser('inspect-source-fields', help='Render own-source cells for review, no inferred tolerance values')
    p.add_argument('source'); p.add_argument('--rotation', type=int, choices=[0,90,180,270], required=True)
    p.add_argument('--box', required=True); p.add_argument('--output', required=True); p.set_defaults(func=inspect_fields)
    p = subs.add_parser('recheck-source-fields', help='Compare actual PDF against full reviewed source fields')
    p.add_argument('manifest'); p.add_argument('pdf'); p.add_argument('--report', required=True); p.set_defaults(func=recheck_fields)
