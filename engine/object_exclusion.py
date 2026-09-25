"""Verify exact, source-bound PDF object exclusions used as source render input.

This accepts only a reviewed ledger that removes whole Tj operations from
single-stream, single-page vector sources. It is deliberately narrower than a
general PDF sanitizer. Inventory identity remains the original PDF SHA.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import fitz
import pikepdf


SCHEMA = 'kangsheng-object-exclusion-evidence-v1'
REVIEW_SCHEMA = 'kangsheng-object-exclusion-review-v1'
REVIEW_STATUS = 'PASS_OBJECT_PRESERVATION_NOT_INVENTORY_PASS'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError('OBJECT_EXCLUSION_BLOCKED: ' + message)


def _content_ops(page):
    content = page.get('/Contents')
    require(content is not None and not isinstance(content, pikepdf.Array),
            'only one direct page content stream is supported')
    return list(pikepdf.parse_content_stream(page))


def _encoded(ops):
    return pikepdf.unparse_content_stream(ops)


def _operator_hash(ops):
    return hashlib.sha256(_encoded(ops)).hexdigest()


def _font_maps(page):
    fonts = page.get('/Resources', {}).get('/Font', {})
    result = {}
    for name, font in fonts.items():
        cmap = font.get('/ToUnicode')
        result[str(name)] = {
            'subtype': str(font.get('/Subtype', '')),
            'base_font': str(font.get('/BaseFont', '')),
            'encoding': str(font.get('/Encoding', '')),
            'unicode_sha256': (hashlib.sha256(cmap.read_bytes()).hexdigest()
                               if cmap is not None else None),
        }
    return result


def _pdf_signature(obj, ancestors=None):
    """Content signature for page resources, ignoring indirect object numbers."""
    ancestors=set() if ancestors is None else ancestors
    if isinstance(obj,pikepdf.Stream):
        items={str(k):v for k,v in obj.items()
               if str(k) not in {'/Length','/Filter','/DecodeParms'}}
        return ('stream',_pdf_signature(items,ancestors),hashlib.sha256(obj.read_bytes()).hexdigest())
    if isinstance(obj,pikepdf.Dictionary):
        ref=tuple(obj.objgen) if getattr(obj,'is_indirect',False) else None
        if ref and ref in ancestors:return ('cycle','dictionary')
        next_ancestors=ancestors|{ref} if ref else ancestors
        return ('dictionary',tuple(sorted((str(k),_pdf_signature(v,next_ancestors))
                                           for k,v in obj.items())))
    if isinstance(obj,pikepdf.Array):
        return ('array',tuple(_pdf_signature(v,ancestors) for v in obj))
    if isinstance(obj,pikepdf.Name):return ('name',str(obj))
    if isinstance(obj,pikepdf.String):return ('string',bytes(obj).hex())
    return (type(obj).__name__,str(obj))


def _page_geometry(path):
    with fitz.open(path) as doc:
        require(len(doc) == 1, 'only one-page source and derived PDFs are supported')
        page = doc[0]
        return {
            'page_count': len(doc),
            'rotation': page.rotation,
            'rect': [round(v, 5) for v in page.rect],
            'mediabox': [round(v, 5) for v in page.mediabox],
            'cropbox': [round(v, 5) for v in page.cropbox],
        }


def validate_transform(source_path, derived_path, ledger_path,
                       expected_source_sha256, expected_derived_sha256,
                       expected_ledger_sha256):
    """Recompute every binding and prove that only ledger-listed Tj ops differ."""
    source_path = Path(source_path).resolve()
    derived_path = Path(derived_path).resolve()
    ledger_path = Path(ledger_path).resolve()
    require(source_path.is_file(), 'original source PDF is missing')
    require(derived_path.is_file(), 'derived source PDF is missing')
    require(ledger_path.is_file(), 'object-transform ledger is missing')
    source_sha = sha256(source_path)
    derived_sha = sha256(derived_path)
    ledger_sha = sha256(ledger_path)
    require(source_sha == expected_source_sha256, 'original source SHA256 differs from manifest')
    require(derived_sha == expected_derived_sha256, 'derived PDF SHA256 differs from manifest')
    require(ledger_sha == expected_ledger_sha256, 'object ledger SHA256 differs from manifest')

    ledger = json.loads(ledger_path.read_text())
    require(ledger.get('schema') == SCHEMA, 'unsupported evidence schema')
    require(ledger.get('kind') == 'watermark', 'only reviewed watermark exclusions are supported')
    require(ledger.get('status') == 'OBJECT_PRESERVATION_VERIFIED_NOT_INVENTORY_PASS',
            'ledger does not claim the required object-preservation evidence state')
    require(ledger.get('not_approved_for_production') is True,
            'diagnostic ledger must retain its not-approved-for-production marker')
    require(ledger.get('source_sha256') == source_sha
            and ledger.get('derived_sha256') == derived_sha,
            'ledger source/derived binding differs')

    with pikepdf.Pdf.open(source_path) as source_pdf, pikepdf.Pdf.open(derived_path) as derived_pdf:
        require(len(source_pdf.pages) == len(derived_pdf.pages) == 1,
                'only one-page PDFs are supported')
        source_page, derived_page = source_pdf.pages[0], derived_pdf.pages[0]
        original_ops = _content_ops(source_page)
        derived_ops = _content_ops(derived_page)
        require(all(str(op.operator) not in {'Do', 'BI', 'sh'} for op in original_ops),
                'external form/image/shading operations need a separate witness')
        require(ledger.get('source_operator_sha256') == _operator_hash(original_ops),
                'source operator stream does not match the evidence ledger')

        targets = ledger.get('targets')
        require(isinstance(targets, list) and bool(targets), 'ledger has no target text blocks')
        target_indices = []
        for block in targets:
            start, end = int(block['start']), int(block['end'])
            require(0 <= start < end < len(original_ops), 'target block indices are out of range')
            require(str(original_ops[start].operator) == 'BT'
                    and str(original_ops[end].operator) == 'ET',
                    'target range is not one whole text block')
            require(block.get('block_sha256') == _operator_hash(original_ops[start:end + 1]),
                    'target text block no longer matches the original source')
            shows = [i for i in range(start, end + 1)
                     if str(original_ops[i].operator) in {'Tj', 'TJ', "'", '"'}]
            declared = [int(i) for i in block.get('show_indices', [])]
            require(shows and shows == declared and all(str(original_ops[i].operator) == 'Tj'
                                                        for i in shows),
                    'target must list all show operations in a whole Tj-only text block')
            target_indices.extend(shows)
        require(len(target_indices) == len(set(target_indices)), 'duplicate target show operation')

        removed = ledger.get('removed_ops')
        require(isinstance(removed, list) and bool(removed), 'ledger has no removed operations')
        removed_indices = [int(item['index']) for item in removed]
        require(len(removed_indices) == len(set(removed_indices)), 'duplicate removed operator index')
        require(sorted(removed_indices) == sorted(target_indices),
                'removed operators do not equal the reviewed whole-block target list')
        for item in removed:
            index = int(item['index'])
            require(0 <= index < len(original_ops)
                    and str(original_ops[index].operator) == 'Tj',
                    'only Tj text-show operators may be removed')
            require(item.get('serialized') == _encoded([original_ops[index]]).decode('ascii'),
                    'removed operator serialization differs from the source')

        retained = [op for i, op in enumerate(original_ops) if i not in set(removed_indices)]
        require(ledger.get('retained_operator_sha256') == _operator_hash(retained),
                'retained operator digest differs from the evidence ledger')
        require(_encoded(derived_ops) == _encoded(retained),
                'derived stream changes an operation outside the reviewed Tj removals')
        require(_font_maps(source_page) == _font_maps(derived_page),
                'font resources or ToUnicode maps changed')
        require(_pdf_signature(source_page.get('/Resources', {}))
                == _pdf_signature(derived_page.get('/Resources', {})),
                'page resource semantics changed')

    geometry_source = _page_geometry(source_path)
    geometry_derived = _page_geometry(derived_path)
    require(geometry_source == geometry_derived, 'page geometry or rotation changed')
    return {
        'source_sha256': source_sha,
        'derived_sha256': derived_sha,
        'ledger_sha256': ledger_sha,
        'removed_show_ops': len(removed_indices),
        'retained_operator_sha256': ledger['retained_operator_sha256'],
        'operation_preservation_verified': True,
        'inventory_pass': False,
    }


def load_review(review_path, expected_source_sha256, expected_derived_sha256,
                expected_ledger_sha256, expected_review_sha256):
    review_path = Path(review_path).resolve()
    require(review_path.is_file(), 'independent object-exclusion review is missing')
    review_sha = sha256(review_path)
    require(review_sha == expected_review_sha256, 'independent review SHA256 differs from manifest')
    review = json.loads(review_path.read_text())
    require(review.get('schema') == REVIEW_SCHEMA, 'unsupported independent review schema')
    require(review.get('status') == REVIEW_STATUS,
            'object review must preserve the distinction from source inventory PASS')
    require(review.get('verdict') == 'PASS', 'independent object review is not PASS')
    require(review.get('reviewer') and review.get('reviewer_role') == 'independent_object_exclusion_reviewer'
            and review.get('reviewer_run_id'), 'independent reviewer identity/run is incomplete')
    require(review.get('source_sha256') == expected_source_sha256
            and review.get('derived_sha256') == expected_derived_sha256
            and review.get('ledger_sha256') == expected_ledger_sha256,
            'independent review is not bound to the original, derivative and ledger')
    require(review.get('technical_vector_paths_exact') is True
            and review.get('technical_text_traces_exact') is True
            and review.get('watermark_render_residuals') == 0
            and review.get('no_rectangular_watermark_exclusion') is True
            and review.get('not_inventory_pass') is True,
            'independent review is missing preservation/residual or scope findings')
    return review
