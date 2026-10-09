#!/usr/bin/env python3
"""把做好的康生图纸追加到飞书多维表格的附件字段（需要本机已登录的 lark-cli）。

  python tools/feishu_backfill.py --base <base_token> --table <table_id> --field <附件字段ID> --plan plan.json

plan.json：[{"record_id": "...", "source_key": "原图名的一部分", "file": "本地PDF路径"}, ...]
- 只追加，不删除、不改任何已有附件，也不动「2D图纸」字段。
- 上传前核对：该记录的「2D图纸」里必须有名字包含 source_key 的图，否则跳过（防止传错行）。
- 同一记录里已有同名文件就跳过（可重复运行）。
- 每个上传后下载回来比对 SHA256；结果写 result.json。
- 下载路径一律用相对路径（lark-cli 对绝对路径会静默失败）。
base/table/字段 ID 属于业务数据，不要写进仓库，运行时传参。"""
import argparse, hashlib, json, os, shutil, subprocess, time
from collections import Counter
from pathlib import Path

LARK = shutil.which('lark-cli') or 'lark-cli'   # Windows installs it as lark-cli.cmd, which a bare name does not find


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True); ap.add_argument('--table', required=True)
    ap.add_argument('--field', required=True); ap.add_argument('--plan', required=True)
    ap.add_argument('--source-field', default='2D图纸'); ap.add_argument('--target-field-name', default='替换图纸')
    a = ap.parse_args()
    B, T, FID = a.base, a.table, a.field
    plan_path = Path(a.plan).resolve(); os.chdir(plan_path.parent)
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def lark(*x):
        p = subprocess.run([LARK, 'base', *x, '--as', 'user'], capture_output=True, text=True, encoding='utf-8')
        try: return json.loads(p.stdout)
        except Exception: return {'ok': False, 'raw': (p.stdout + p.stderr)[-400:]}

    subprocess.run([LARK, 'base', '+record-list', '--base-token', B, '--table-id', T, '--as', 'user',
                    '--format', 'ndjson', '--output', 'before.ndjson', '--overwrite'], capture_output=True)
    recs = {r['record_id']: r for r in map(json.loads, filter(str.strip, open('before.ndjson', encoding='utf-8')))}
    plan = json.load(open(plan_path, encoding='utf-8')); res = []
    for i, it in enumerate(plan):
        rid = it['record_id']; r = recs.get(rid); f = Path(it['file']); row = dict(it)
        if not r or not f.exists():
            row['status'] = 'SKIP_MISSING'; res.append(row); print('跳过（记录或文件不存在）', f.name); continue
        if not any(it['source_key'] in x['name'] for x in (r.get(a.source_field) or [])):
            row['status'] = 'SKIP_2D_MISMATCH'; res.append(row); print('跳过（原图对不上）', f.name); continue
        cur = r.get(a.target_field_name) or []
        if any(x['name'] == f.name for x in cur):
            row['status'] = 'ALREADY'; res.append(row); print('已有', f.name); continue
        before = {x['file_token'] for x in cur}
        j = lark('+record-upload-attachment', '--base-token', B, '--table-id', T, '--record-id', rid,
                 '--field-id', FID, '--file', str(f))
        lst = next(iter(((j.get('data') or {}).get('attachments') or {}).get(rid, {}).values()), [])
        new = [x for x in lst if x['file_token'] not in before and x['name'] == f.name]
        if len(new) != 1:
            row.update(status='UPLOAD_FAILED', detail=j); res.append(row); print('上传失败', f.name); continue
        r[a.target_field_name] = lst   # later uploads to the same record compare against this list
        tok = new[0]['file_token']; want = sha(f); ok = False
        for _ in range(3):
            d = Path('roundtrip') / f'{i:03d}.pdf'; d.parent.mkdir(exist_ok=True)
            lark('+record-download-attachment', '--base-token', B, '--table-id', T, '--record-id', rid,
                 '--file-token', tok, '--output', str(d), '--overwrite')
            if d.exists() and sha(d) == want: ok = True; break
            time.sleep(3)
        if not ok:   # single-token download sometimes reports "not found": fetch the whole cell and match by hash
            d = Path('roundtrip') / f'{i:03d}-all'; d.mkdir(parents=True, exist_ok=True)
            lark('+record-download-attachment', '--base-token', B, '--table-id', T, '--record-id', rid,
                 '--output', str(d), '--overwrite')
            ok = any(sha(p) == want for p in d.iterdir() if p.is_file())
        row.update(status='UPLOADED_VERIFIED' if ok else 'UPLOADED_NOT_VERIFIED', token=tok, sha256=want)
        res.append(row); print('完成' if ok else '已上传但回读未核对上', f.name, flush=True)
    json.dump(res, open('result.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(dict(Counter(x['status'] for x in res)))


if __name__ == '__main__':
    main()
