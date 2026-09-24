"""Private, relocatable handoff; no chat history, network calls or AI required."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
import platform
import shutil
import sys
from pathlib import Path
import pymupdf as fitz

def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def resolve(base,p):
    p=Path(p).expanduser(); return p if p.is_absolute() else base/p

def pack(args):
    root=Path(__file__).resolve().parents[1]; dest=Path(args.output).resolve()
    if dest.exists(): raise ValueError('Choose a new bundle directory; previous bundles are immutable')
    dest.mkdir(parents=True)
    for name in ('scripts','references','supported-layouts','tests'):
        shutil.copytree(root/name,dest/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('SKILL.md','AGENTS.md','requirements.txt'):
        shutil.copy2(root/name,dest/name)
    shutil.copytree(root/'assets',dest/'assets')
    jobs=[]
    evidence_map={}
    def copy_asset(src,sub):
        target=dest/sub/(digest(src)[:16]+Path(src).suffix)
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): shutil.copy2(src,target)
        relative=target.relative_to(dest).as_posix()
        evidence_map[str(Path(src).resolve())]=relative
        return relative
    for m in args.manifests:
        mp=Path(m).resolve(); cfg=json.loads(mp.read_text())
        if not cfg.get('source_fields'): raise ValueError('Current supplier job needs reviewed source_fields')
        if cfg.get('approved_tolerance_reflow') or cfg.get('approved_uniform_view_scales'):
            raise ValueError('Evidence-linked legacy job needs its full existing replay package, not this packer')
        cfg['source']['path']='../'+copy_asset(resolve(mp.parent,cfg['source']['path']),'private/sources')
        cfg['assets']={k:'../'+copy_asset(resolve(mp.parent,v),'private/assets') for k,v in cfg['assets'].items()}
        ep=resolve(mp.parent,cfg['source_fields']['path']); e=json.loads(ep.read_text())
        rp=resolve(ep.parent,e['review']['path'])
        # Retain the exact reviewed report bytes/hash; outside paths in it are
        # historical evidence labels, never executable dependencies.
        review_rel=copy_asset(rp,'private/reviews')
        for row in json.loads(rp.read_text()).get('rows',[]):
            for item in row.get('evidence',[]):
                ep=resolve(rp.parent,item)
                if not ep.is_file(): raise ValueError('Missing field review image: '+str(ep))
                copy_asset(ep,'private/evidence')
        for key in ('evidence',):
            original=cfg.get('review',{}).get(key,[])
            items=[original] if isinstance(original,str) else original
            replaced=[]
            for index,item in enumerate(items):
                ep=resolve(mp.parent,item)
                if not ep.is_file(): raise ValueError('Missing source review image: '+str(ep))
                replaced.append('../'+copy_asset(ep,'private/evidence'))
            if original: cfg['review'][key]=replaced[0] if isinstance(original,str) else replaced
        e['review']['path']='../'+Path(review_rel).relative_to('private').as_posix()
        ledger=dest/'private/ledgers'/f'{mp.stem}.json';ledger.parent.mkdir(exist_ok=True)
        ledger.write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n')
        cfg['source_fields']={'path':'../'+ledger.relative_to(dest).as_posix(),'sha256':digest(ledger),
                              'semantic_sha256':cfg['source_fields']['semantic_sha256']}
        # Keep source review: source semantic digest ignores only locator paths;
        # byte hashes and reviewed values are checked again on every use.
        jp=dest/'jobs'/mp.name;jp.parent.mkdir(exist_ok=True)
        jp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n')
        jobs.append(jp.relative_to(dest).as_posix())
    state={'schema':'kangsheng-private-handoff-v1','supplier_scope':'reviewed source-local manifests only; new input requires family matching and source inventory',
           'entrypoint':'scripts/kangsheng.py','python':platform.python_version(),'pymupdf':fitz.VersionBind,
           'jobs':jobs,'engineering_release':False,'publication_allowed':False,
           'next_action':'Read SKILL.md and references/supplier-fields.md; run handoff-check before using a job',
           'privacy':'PRIVATE: includes supplier drawings and local assets. Do not push to public repositories.'}
    packages=('PyMuPDF','numpy','scipy','pikepdf','fonttools')
    (dest/'requirements-lock.txt').write_text('\n'.join(name+'=='+importlib.metadata.version(name) for name in packages)+'\n')
    (dest/'START_HERE.md').write_text('''# 康生供应商私有接力包

先读 SKILL.md 和 HANDOFF.json；本包含业务原图，勿公开上传。

环境：Python 3.12，依赖按 requirements-lock.txt 安装。首次运行先执行：

```sh
python scripts/kangsheng.py handoff-check .
```

通过后按 references/supplier-fields.md 使用 jobs 中的已审核配方。
不要从聊天重新找坐标；不要换回旧的正则公差提取脚本。
新源先做自身完整清单，禁止借用已有产品的参数或审核结论。

给其他模型的提示：
> 请读取这个目录的 SKILL.md、HANDOFF.json，先做 handoff-check。沿用当前供应商配方和品牌版式，仅处理指定原件，不从零重建。完整保留技术内容，检查公差所有档位及品名/MODEL实际显示。遇到不匹配仅报告局部异常；未经确认不覆盖飞书、不学习失败样本。
''')
    (dest/'HANDOFF.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    (dest/'EVIDENCE_MAP.json').write_text(json.dumps(evidence_map,ensure_ascii=False,indent=2)+'\n')
    files={str(p.relative_to(dest)):digest(p) for p in dest.rglob('*') if p.is_file()}
    (dest/'CHECKSUMS.json').write_text(json.dumps(files,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'PRIVATE_BUNDLE_CREATED','path':str(dest),'jobs':jobs},ensure_ascii=False))

def check(args):
    root=Path(args.bundle).resolve(); state=json.loads((root/'HANDOFF.json').read_text())
    files=json.loads((root/'CHECKSUMS.json').read_text())
    issues=[]
    for name,expected in files.items():
        p=(root/name).resolve()
        if not p.is_relative_to(root) or not p.is_file() or digest(p)!=expected: issues.append(name)
    if state['pymupdf']!=fitz.VersionBind: issues.append('PyMuPDF runtime differs')
    if platform.python_version_tuple()[:2]!=tuple(state['python'].split('.')[:2]):
        issues.append('Python major/minor differs; rerun regression before use')
    from source_fields import load_source_fields
    evidence_map=json.loads((root/'EVIDENCE_MAP.json').read_text())
    for name in state['jobs']:
        try:
            p=root/name; c=json.loads(p.read_text());load_source_fields(c,p)
            ep=resolve(p.parent,c['source_fields']['path']);e=json.loads(ep.read_text())
            review=json.loads(resolve(ep.parent,e['review']['path']).read_text())
            for row in review.get('rows',[]):
                for item in row.get('evidence',[]):
                    mapped=evidence_map.get(item)
                    if not mapped or not (root/mapped).is_file(): issues.append('missing bundled field review evidence')
            inventory_evidence=c.get('review',{}).get('evidence',[])
            for item in ([inventory_evidence] if isinstance(inventory_evidence,str) else inventory_evidence):
                if not resolve(p.parent,item).is_file(): issues.append('missing source inventory review evidence')
            for asset in c['assets'].values():
                if not resolve(p.parent,asset).is_file(): issues.append('missing asset '+asset)
        except (ValueError,KeyError,OSError) as exc: issues.append(name+': '+str(exc))
    report={'status':'HANDOFF_PASS' if not issues else 'HANDOFF_BLOCKED','issues':issues,
            'jobs':state['jobs'],'engineering_release':False,
            'scope':'bytes, paths, runtime and reviewed source fields; not new-source approval or engineering release'}
    print(json.dumps(report,ensure_ascii=False))
    if issues: raise ValueError('Handoff integrity/preflight failed')

def register_cli(subs):
    p=subs.add_parser('pack-local',help='Create a private relocatable skill plus source-bound jobs')
    p.add_argument('manifests',nargs='+');p.add_argument('--output',required=True);p.set_defaults(func=pack)
    p=subs.add_parser('handoff-check',help='Verify bundle integrity and all reviewed source fields offline')
    p.add_argument('bundle');p.set_defaults(func=check)
