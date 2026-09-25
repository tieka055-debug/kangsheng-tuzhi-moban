#!/usr/bin/env python3
"""康生图纸批量流水线（两步）。

第 1 步 draft：原图 PDF → 自动清单 → 引擎 draft 门禁 → 分诊（AUTO_OK / REVIEW / EXCEPTION），
         并为每张图导出公差格、图幅格小图和待填的 tolerance.json。
第 2 步 finish：读完 tolerance.json 后，套英文公差栏，引擎再次全门禁，输出最终草稿与对照图。

  python pipeline/run_batch.py draft  原图.pdf|文件夹 ... --out 输出目录 --font 标题字体 [--font-index N]
  python pipeline/run_batch.py finish 输出目录

程序不写任何发布 PASS，不产生 release.json。
"""
import argparse, csv, json, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
ENGINE = ROOT / 'engine' / 'kangsheng.py'
ASSETS = ROOT / 'assets'
FAMILY = ROOT / 'families' / 'zhiyuan.json'

REVIEW_FLAGS = ('TABLE_TOO_WIDE_FOR_RAIL', 'TABLE_CONTAINS_GRAPHICS', 'EXCLUDED_TEXT_NOT_IN_VOCABULARY',
                'PERFORMANCE_MERGED_WITH_GRAPHICS', 'FRAGMENTED_CLIP', 'SHARES_RULE_WITH_FURNITURE',
                'INTERLEAVES_FURNITURE', 'BLOCK_ENTERS_TITLE_BLOCK', 'TABLE_FONT_TOO_SMALL_IN_RAIL',
                'PERFORMANCE_DOES_NOT_FIT_RAIL', 'TEXT_ONLY_BLOCK', 'OUTLINED_TEXT_BLOCK', 'LINE_SPLIT',
                'NEIGHBOR_DISPLACED', 'TEXT_MISSING_IN_OUTPUT', 'BLOCK_ENTERS_FRAME_BAND')
CANDIDATES = (('single', ['--clip-mode', 'single']), ('multi', ['--clip-mode', 'multi']),
              ('relaxed', ['--clip-mode', 'multi', '--relaxed-rail']),
              ('multi-nofb', ['--clip-mode', 'multi', '--no-fallback']),
              ('relaxed-nofb', ['--clip-mode', 'multi', '--relaxed-rail', '--no-fallback']))
LAYOUT_FLAGS = ('PERFORMANCE_MERGED_WITH_GRAPHICS', 'RAIL_SHRUNK_TO_FIT_CONTENT', 'TABLE_FONT_TOO_SMALL_IN_RAIL')


def engine_draft(manifest, outdir, cache):
    e = subprocess.run([sys.executable, str(ENGINE), 'draft', str(manifest), '--output', str(outdir),
                        '--control-root', str(outdir.parent / 'control'), '--cache', str(cache)],
                       capture_output=True, text=True)
    tail = (e.stdout + e.stderr).strip().splitlines()
    au = outdir / 'draft-audit.json'
    ok = au.exists() and json.loads(au.read_text())['pass']
    return ok, (tail[-1][:400] if tail else '')


def sheet(src_manifest, pdf, png, title):
    import pymupdf as fitz, auto_manifest as am
    m = json.loads(Path(src_manifest).read_text())
    d, p, rot = am.normalized(m['source']['path'])
    pg = fitz.open().new_page(width=1700, height=640)
    pg.show_pdf_page(fitz.Rect(5, 30, 845, 635), d, 0)
    pg.show_pdf_page(fitz.Rect(855, 30, 1695, 635), fitz.open(pdf), 0)
    pg.insert_text((8, 20), title, fontsize=13, fontname='china-s')
    pg.get_pixmap(dpi=110).save(png)


def draft_one(src, out, a, cache):
    t0 = time.perf_counter(); tried = []; win = None
    for label, extra in CANDIDATES:
        d = out / label
        if d.exists(): shutil.rmtree(d)
        d.mkdir(parents=True)
        subprocess.run([sys.executable, str(HERE / 'auto_manifest.py'), str(src), '--out', str(d), '--font', a.font,
                        '--font-index', str(a.font_index), '--assets', str(ASSETS), '--family', str(FAMILY), *extra],
                       capture_output=True, text=True)
        rep = json.loads((d / 'auto-report.json').read_text()) if (d / 'auto-report.json').exists() else {'status': 'CRASH'}
        rec = {'label': label, 'auto_status': rep.get('status'), 'reason': rep.get('reason'),
               'flags': rep.get('flags') or [], 'fields': rep.get('fields'), 'field_flags': rep.get('field_flags')}
        if rep.get('status') == 'MANIFEST_WRITTEN':
            rec['pass'], rec['engine'] = engine_draft(d / 'manifest.json', d / 'draft', cache)
        tried.append(rec)
        if rec.get('pass'):
            # a passing layout with a layout compromise: keep looking for a cleaner candidate
            if win is None: win = rec
            if not any(f.startswith(LAYOUT_FLAGS) for f in rec['flags']):
                win = rec; break
    res = {'source': str(src), 'attempts': tried}
    if not win:
        res.update(triage='EXCEPTION', why=tried[-1].get('reason') or tried[-1].get('engine'),
                   seconds=round(time.perf_counter() - t0, 1))
        return res
    run_dir = out / win['label']
    import line_integrity, neighbor_check, text_crosscheck
    flags = list(win['flags'])
    if line_integrity.split_lines(str(run_dir / 'manifest.json')): flags.append('LINE_SPLIT_ACROSS_GROUPS')
    if neighbor_check.narrow_displaced(str(run_dir / 'manifest.json')): flags.append('NEIGHBOR_DISPLACED')
    fam = json.loads(FAMILY.read_text())
    if text_crosscheck.check(None, str(run_dir / 'manifest.json'), str(run_dir / 'draft' / 'draft.pdf'), fam)['missing']:
        flags.append('TEXT_MISSING_IN_OUTPUT')
    review = sorted({f for f in flags if f.startswith(REVIEW_FLAGS)})
    subprocess.run([sys.executable, str(HERE / 'field_packet.py'), str(run_dir)], capture_output=True)
    pk = json.loads((run_dir / 'field-packet' / 'packet.json').read_text())
    tol = out / 'tolerance.json'
    if not tol.exists():
        tol.write_text(json.dumps({
            'status': 'TO_READ',
            'instruction': '只看本图 field-packet/tolerance-cell.png 与 size-cell.png，逐行照原图填写；不从其他型号抄值。'
                           '填完把 status 改为 READ。',
            'text_layer_hint': pk.get('tolerance_rows_from_text_layer'),
            'size': pk['size'] if not str(pk['size']).startswith('READ_ME') else '',
            'linear_tolerances': [], 'angular_tolerances': [], 'additional_tolerance_conditions': []},
            ensure_ascii=False, indent=1))
    res.update(strategy=win['label'], flags=flags, review_flags=review, fields=win['fields'],
               triage='REVIEW' if review else 'AUTO_OK', seconds=round(time.perf_counter() - t0, 1))
    sheet(run_dir / 'manifest.json', run_dir / 'draft' / 'draft.pdf', out / 'review-sheet.png',
          f"{Path(src).name}  [{res['triage']}]  {' '.join(review)}")
    return res


def cmd_draft(a):
    a.font = str(Path(a.font).expanduser().resolve())
    srcs = []
    for x in a.inputs:
        p = Path(x).expanduser()
        srcs += sorted(p.glob('*.pdf')) if p.is_dir() else [p]
    root = Path(a.out).expanduser().resolve(); root.mkdir(parents=True, exist_ok=True)
    results = []; used = set()
    for s in srcs:
        name = s.stem; n = 2
        while name in used: name = f"{s.stem}_{n}"; n += 1
        used.add(name)
        r = draft_one(s.resolve(), root / name, a, root / '.cache'); r['dir'] = name; results.append(r)
        print(f"{r['triage']:9s} {r['seconds']:6.1f}s  {s.name}  {' '.join(r.get('review_flags', [])) or r.get('why') or ''}", flush=True)
    (root / 'batch-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1))
    with open(root / 'batch-summary.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['原图', '分诊', '秒', '需看原因/例外原因', '图号', '品名'])
        for r in results:
            fl = r.get('fields') or {}
            w.writerow([Path(r['source']).name, r['triage'], r['seconds'],
                        ' '.join(r.get('review_flags', [])) or (r.get('why') or ''), fl.get('model', ''), fl.get('title', '')])
    print(json.dumps({k: sum(1 for r in results if r['triage'] == k) for k in ('AUTO_OK', 'REVIEW', 'EXCEPTION')}, ensure_ascii=False))


def cmd_finish(a):
    root = Path(a.out_dir).expanduser().resolve()
    results = json.loads((root / 'batch-results.json').read_text())
    for r in results:
        if r['triage'] == 'EXCEPTION': continue
        d = root / r.get('dir', Path(r['source']).stem)
        tol = json.loads((d / 'tolerance.json').read_text())
        if tol.get('status') != 'READ':
            print(f"WAIT      {d.name}  tolerance.json 未读"); continue
        fin = d / 'final'
        if fin.exists(): shutil.rmtree(fin)
        p = subprocess.run([sys.executable, str(HERE / 'english_tolerance.py'), str(d / r['strategy']), str(d / 'tolerance.json'),
                            '--engine', str(ENGINE), '--reader', tol.get('reader', 'unrecorded'), '--out', str(fin)],
                           capture_output=True, text=True)
        au = fin / 'draft' / 'draft-audit.json'
        ok = au.exists() and json.loads(au.read_text())['pass']
        if ok:
            shutil.copy(fin / 'draft' / 'draft.pdf', d / f"{d.name}-康生草稿.pdf")
            sheet(fin / 'manifest-en.json', fin / 'draft' / 'draft.pdf', d / f"{d.name}-原图对照.png", d.name)
        r['final'] = 'OK' if ok else 'FAIL'
        print(f"{'FINAL_OK' if ok else 'FINAL_FAIL':9s} {d.name}  {'' if ok else (p.stdout + p.stderr)[-300:]}")
    (root / 'batch-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('draft'); p.add_argument('inputs', nargs='+'); p.add_argument('--out', required=True)
    p.add_argument('--font', required=True); p.add_argument('--font-index', type=int, default=0)
    p.set_defaults(func=cmd_draft)
    p = sub.add_parser('finish'); p.add_argument('out_dir'); p.set_defaults(func=cmd_finish)
    a = ap.parse_args(); a.func(a)


if __name__ == '__main__':
    main()
