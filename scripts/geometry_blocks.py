"""Geometry-first, lossless page inventory for new supplier drawings.

Semantic names are annotations on complete foreground components, never a
prerequisite for accounting for source ink.  Approved nontechnical removals
are explicit; all remaining pixels (including isolated one-pixel objects) are
owned by a block.  Raster masks are retained so a later renderer can prove
that an output carried precisely the source content selected for each block.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Iterable

import fitz
import numpy as np
from scipy.ndimage import binary_dilation, find_objects, label


KINDS = frozenset({"engineering_view", "pcb", "part_table", "performance",
                   "tolerance", "projection", "technical_note", "UNKNOWN_TECHNICAL"})
NONTECHNICAL_KINDS = frozenset({"supplier_logo", "supplier_company",
    "supplier_title_furniture", "outer_frame", "watermark", "nontechnical_annotation"})


class GeometryInventoryReview(RuntimeError):
    """Full-page geometry exists, but its exclusions or coverage need review."""


def require_geometry_inventory_pass(graph_or_report: dict) -> dict:
    """Fail closed before generating a delivery candidate, never self-approve."""
    report=graph_or_report.get('report',graph_or_report)
    if report.get('SOURCE_INVENTORY_STATUS')!='SOURCE_INVENTORY_PASS':
        raise GeometryInventoryReview(
            f"geometry inventory needs review: unaccounted={report.get('UNACCOUNTED_TECHNICAL_INK')}, "
            f"pending_exclusions={report.get('pending_nontechnical_approval_count')}")
    return report


def _rect(mask: np.ndarray, box: Iterable[float]) -> None:
    coordinates = tuple(box)
    if len(coordinates) != 4 or any(not isinstance(x, (int, float)) or not math.isfinite(x)
                                     for x in coordinates):
        raise ValueError("region bbox must have four finite coordinates")
    x0, y0, x1, y1 = coordinates
    if not (0 <= x0 < x1 <= mask.shape[1] and 0 <= y0 < y1 <= mask.shape[0]):
        raise ValueError("region bbox must be nonempty and inside the complete rendered page")
    mask[int(math.floor(y0)):int(math.ceil(y1)),
         int(math.floor(x0)):int(math.ceil(x1))] = True


def _bbox(mask: np.ndarray) -> list[int]:
    yy, xx = np.where(mask)
    return [int(xx.min()), int(yy.min()), int(xx.max()+1), int(yy.max()+1)]


def _near(a: list[int], b: list[int], gap: int) -> bool:
    return not (a[2]+gap < b[0] or b[2]+gap < a[0] or
                a[3]+gap < b[1] or b[3]+gap < a[1])


def _object_pixel_box(box: Iterable[float], shape: tuple[int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return (max(0, int(math.floor(x0))), max(0, int(math.floor(y0))),
            min(shape[1], int(math.ceil(x1))), min(shape[0], int(math.ceil(y1))))


def _has_owned_ink(mask: np.ndarray, box: Iterable[float]) -> bool:
    x0, y0, x1, y1 = _object_pixel_box(box, mask.shape)
    return x0 < x1 and y0 < y1 and bool(mask[y0:y1, x0:x1].any())


def extract_page_blocks(pdf_path, *, page_index=0, zoom=2.2,
                        approved_nontechnical=(), protected_technical=(),
                        semantic_regions=(), partition_regions=(), merge_gap_px=8,
                        foreground_threshold=245):
    """Return complete source page graph plus exact ownership masks.

    Boxes supplied by the caller are rendered-pixel coordinates.  They may be
    detected from a supplier profile, text anchors or table rules.  Protected
    boxes make an overlapping technical object win over a title-furniture
    exclusion; no ambiguous source ink is silently discarded.  Each block's
    private ``mask`` ndarray is also returned for exact QA, but omitted from
    the serializable ``report``.
    """
    if not isinstance(zoom, (int, float)) or not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("zoom must be positive and finite")
    if not isinstance(foreground_threshold, int) or not 1 <= foreground_threshold <= 255:
        raise ValueError("foreground_threshold must be an integer from 1 to 255")
    if not isinstance(merge_gap_px, int) or merge_gap_px < 0:
        raise ValueError("merge_gap_px must be a nonnegative integer")
    source_sha256 = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    with fitz.open(pdf_path) as doc:
        if len(doc) != 1:
            raise ValueError('single complete source page required')
        if page_index != 0:
            raise ValueError("complete single source page has index zero")
        page=doc[0]
        pix=page.get_pixmap(matrix=fitz.Matrix(zoom,zoom),alpha=False)
        rgb=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)[:,:,:3].copy()
        drawings=[]
        for i,item in enumerate(page.get_drawings()):
            r=item['rect'];drawings.append({'id':f'd{i}',
                'bbox':[r.x0*zoom,r.y0*zoom,r.x1*zoom,r.y1*zoom],
                'color':item.get('color'),'type':item.get('type')})
        spans=[]
        for bi,block in enumerate(page.get_text('dict')['blocks']):
            for li,line in enumerate(block.get('lines',[])):
                for si,span in enumerate(line['spans']):
                    spans.append({'id':f't{bi}.{li}.{si}','bbox':[v*zoom for v in span['bbox']],
                                  'text':span['text'],'color':span.get('color')})
    ink=rgb.min(axis=2)<foreground_threshold
    excluded=np.zeros(ink.shape,bool); protected=np.zeros(ink.shape,bool)
    pending_exclusion=np.zeros(ink.shape,bool)
    exclusion_audit=[]
    for item in approved_nontechnical:
        if item.get('kind') not in NONTECHNICAL_KINDS or not item.get('reason'):
            raise ValueError('every nontechnical exclusion requires approved kind and reason')
        region=np.zeros(ink.shape,bool);_rect(region,item['bbox'])
        excluded|=region
        if item.get('review_status')!='APPROVED_NONTECHNICAL' or not item.get('reviewer'):
            pending_exclusion|=region
        exclusion_audit.append({'kind':item['kind'],'reason':item['reason'],
            'bbox':list(item['bbox']),'ink_pixels':int((ink&region).sum()),
            'review_status':item.get('review_status','PENDING'),
            'reviewer':item.get('reviewer')})
    for item in protected_technical:
        _rect(protected,item['bbox'])
    original_exclusions=excluded.copy()
    excluded &= ~protected
    pending_exclusion &= ~protected
    for audit in exclusion_audit:
        region=np.zeros(ink.shape,bool);_rect(region,audit['bbox'])
        audit['effective_excluded_ink_pixels']=int((ink&excluded&region).sum())
    # A protected technical span can enter an old administrative cell.  If
    # that same protected rectangle also catches a supplier-only text span,
    # preserve the ink but flag the local collision instead of silently
    # claiming a clean exclusion.
    protected_span_conflicts=[]
    for span in spans:
        x0,y0,x1,y1=_object_pixel_box(span['bbox'],ink.shape)
        if (x0<x1 and y0<y1 and original_exclusions[y0:y1,x0:x1].all() and
                _has_owned_ink(ink & original_exclusions & protected, span['bbox'])):
            protected_span_conflicts.append({'id':span['id'],'text':span['text'],
                                             'bbox':span['bbox']})
    technical=ink & ~excluded
    # A title rail or an attached table may share a border with a drawing.
    # Partition ownership at the known *region boundary* rather than trying to
    # find a whitespace corridor through a continuous rule.  The border ink
    # belongs to one side, and every pixel remains owned exactly once.
    partition_masks=[];partition_used=np.zeros(ink.shape,bool)
    for region in partition_regions:
        region_mask=np.zeros(ink.shape,bool);_rect(region_mask,region['bbox'])
        region_mask &= technical & ~partition_used
        if region_mask.any():
            partition_masks.append((region_mask,region))
            partition_used|=region_mask
    unpartitioned=technical & ~partition_used
    # Dilation is used solely to build connected neighborhoods.  Ownership
    # always comes from the original undilated foreground pixels.
    grown=(binary_dilation(unpartitioned,iterations=merge_gap_px)
           if merge_gap_px else unpartitioned)
    labels,count=label(grown,structure=np.ones((3,3),np.uint8))
    block_masks=[(mask,region) for mask,region in partition_masks]
    owned=partition_used.copy()
    for i,slices in enumerate(find_objects(labels),1):
        if slices is None:continue
        local=(labels[slices]==i) & unpartitioned[slices]
        if not local.any():continue
        mask=np.zeros(ink.shape,bool);mask[slices]=local
        block_masks.append((mask,None));owned|=mask
    # Defensive singleton accounting if an underlying label implementation
    # ever changes: source conservation beats semantic classification.
    missing=technical & ~owned
    if missing.any():
        for y,x in np.argwhere(missing):
            singleton=np.zeros(ink.shape,bool);singleton[y,x]=True
            block_masks.append((singleton,None));owned[y,x]=True
    blocks=[]
    for i,(mask,region) in enumerate(block_masks,1):
        box=_bbox(mask)
        objects=[];texts=[];colors={};text_spans=[]
        for obj in drawings+spans:
            # A nearby object can belong to the adjacent title cell.  Require
            # source ink actually owned inside its bounding box, not just a
            # bbox-distance match, before attaching text to a block.
            if _near(box,obj['bbox'],0) and _has_owned_ink(mask,obj['bbox']):
                objects.append(obj['id'])
                if 'text' in obj:
                    texts.append(obj['text']);text_spans.append(obj['id'])
                if obj.get('color') is not None:
                    key=str(obj['color']);colors[key]=colors.get(key,0)+1
        classification='UNKNOWN_TECHNICAL';slot=None;reason='geometry-owned; semantic role unresolved'
        if region is not None:
            classification=region.get('classification',classification)
            slot=region.get('slot');reason=region.get('reason','geometric partition')
        else:
            for rule in semantic_regions:
                if _near(box,rule['bbox'],rule.get('match_gap_px',0)):
                    classification=rule['classification'];slot=rule.get('slot');reason=rule.get('reason','profile rule')
                    break
        if classification not in KINDS:raise ValueError(classification)
        crop=np.ascontiguousarray(mask[box[1]:box[3],box[0]:box[2]])
        blocks.append({'id':f'block-{i:03d}','bbox':box,'objects':objects,
                       'text':' '.join(texts),'vector_count':sum(x.startswith('d') for x in objects),
                       'text_span_ids':text_spans,
                       'colors':colors,'source_ink_mask_sha256':hashlib.sha256(crop.tobytes()).hexdigest(),
                       'ink_pixels':int(mask.sum()),'classification':classification,
                       'slot':slot,'classification_reason':reason,'neighbors':[],
                       '_mask':mask})
    for i,a in enumerate(blocks):
        for j,b in enumerate(blocks):
            if i!=j and _near(a['bbox'],b['bbox'],merge_gap_px*3):a['neighbors'].append(b['id'])
    geometry_unaccounted=int((technical&~owned).sum())
    # A proposed but unsigned exclusion is not an authorized subtraction from
    # full-source technical scope.  Keep it visible in the graph ledger as
    # pending ink, never convert zero connected-component gaps into PASS.
    pending_ink=ink&pending_exclusion
    unaccounted=geometry_unaccounted+int(pending_ink.sum())
    pending=sum(x['review_status']!='APPROVED_NONTECHNICAL' or not x['reviewer']
                for x in exclusion_audit)
    report={'schema':'kangsheng-geometry-page-graph-v1','source_path':str(pdf_path),
            'source_sha256':source_sha256,
            'page_px':[int(rgb.shape[1]),int(rgb.shape[0])],
            'source_ink_pixels':int(ink.sum()),
            'approved_nontechnical_ink_pixels':int((ink&excluded&~pending_exclusion).sum()),
            'pending_nontechnical_exclusion_ink_pixels':int(pending_ink.sum()),
            'technical_or_unknown_ink_pixels':int(technical.sum())+int(pending_ink.sum()),
            'GEOMETRY_UNACCOUNTED_TECHNICAL_INK':geometry_unaccounted,
            'UNACCOUNTED_TECHNICAL_INK':unaccounted,
            'UNKNOWN_TECHNICAL_BLOCKS':sum(b['classification']=='UNKNOWN_TECHNICAL' for b in blocks),
            'SOURCE_INVENTORY_STATUS':('SOURCE_INVENTORY_PASS' if not unaccounted and not pending
                and not protected_span_conflicts else 'SOURCE_INVENTORY_REVIEW'),
            'pending_nontechnical_approval_count':pending,
            'protected_nontechnical_span_conflicts':protected_span_conflicts,
            'nontechnical_exclusions':exclusion_audit,
            'block_count':len(blocks),
            'blocks':[{k:v for k,v in b.items() if k!='_mask'} for b in blocks]}
    return {'report':report,'blocks':blocks,'rgb':rgb,'ink':ink,
            'technical_mask':technical,'approved_exclusion_mask':excluded}
