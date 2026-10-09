#!/usr/bin/env python3
"""回归：把一批旧任务分别用「基线提交」和「当前代码」各跑一遍，比对输出的矢量内容，任何一张不同都要报出来。
  python tools/regress.py 回归目录 [--base f36b05b] [--jobs 4]
回归目录 = cases.json + cases/NN/{job.json,src.pdf} + font.otf（原图是业务数据，不进仓库，放在本机）。
基线用 git worktree 临时检出，不动你的工作区。输出有差异 → 退出码 1。"""
import argparse, hashlib, json, subprocess, sys, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import fitz

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(); ap.add_argument('cases'); ap.add_argument('--base', default='b2be2cf'); ap.add_argument('--jobs', type=int, default=4)
a = ap.parse_args()
C = Path(a.cases).resolve(); cases = json.load(open(C / 'cases.json', encoding='utf-8')); font = C / 'font.otf'
tmp = Path(tempfile.mkdtemp())
base = tmp / 'base'
subprocess.run(['git', '-C', str(ROOT), 'worktree', 'add', '--detach', str(base), a.base], check=True, capture_output=True)

def h(pdf):
    d = fitz.open(pdf)[0].get_drawings()
    return hashlib.md5(repr([(x['type'], [str(i) for i in x['items']][:50], x.get('width'), x.get('color'), x.get('fill')) for x in d]).encode()).hexdigest(), len(d)

def run(tree, n):
    out = tmp / tree.name / n
    subprocess.run([sys.executable, str(tree / 'pipeline' / 'cad_family.py'), str(C / 'cases' / n / 'job.json'), '--out', str(out), '--font', str(font)],
                   capture_output=True, text=True, timeout=900, cwd=C / 'cases' / n)
    r = sorted(out.glob('*康生图纸.pdf'))
    return h(r[0]) if r else None

try:
    trees = [ROOT, base]
    jobs = [(t, n) for t in trees for n in cases]
    with ThreadPoolExecutor(a.jobs) as ex: res = list(ex.map(lambda tn: run(*tn), jobs))
    R = {(t.name, n): r for (t, n), r in zip(jobs, res)}
    bad = 0
    for n in cases:
        x, y = R[(ROOT.name, n)], R[(base.name, n)]
        if x != y or x is None: print(n, 'DIFF' if x and y else 'MISSING', x, y); bad += 1
    print(f'{len(cases)} 个任务，不一致 {bad}')
    sys.exit(1 if bad else 0)
finally:
    subprocess.run(['git', '-C', str(ROOT), 'worktree', 'remove', '--force', str(base)], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
