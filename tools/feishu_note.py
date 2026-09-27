#!/usr/bin/env python3
"""在飞书多维表格的文本字段（默认「备注」）里追加一句说明，用来标记缺图、原图损坏等情况。

  python tools/feishu_note.py --base <base_token> --table <table_id> --notes notes.json [--field 备注]

notes.json：[{"record_id": "...", "note": "缺原图：……"}, ...]
- 原有备注一个字都不改，新说明接在后面（另起一行），并带「【康生图纸】」前缀。
- 已经有「【康生图纸】」说明的记录跳过，可重复运行。
- 先对第一条做 --dry-run 确认命令格式，再逐条写入；写完重新导出，核对备注和其他字段。"""
import argparse, json, subprocess, sys
from pathlib import Path

TAG = '【康生图纸】'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True); ap.add_argument('--table', required=True)
    ap.add_argument('--notes', required=True); ap.add_argument('--field', default='备注')
    a = ap.parse_args()
    B, T = a.base, a.table
    workdir = Path(a.notes).resolve().parent

    def run(*x):
        return subprocess.run(['lark-cli', 'base', *x], capture_output=True, text=True, cwd=workdir)

    def export(name):
        run('+record-list', '--base-token', B, '--table-id', T, '--as', 'user', '--format', 'ndjson', '--output', name, '--overwrite')
        return {r['record_id']: r for r in map(json.loads, filter(str.strip, open(workdir / name)))}

    helptext = run('+record-upsert', '--help').stdout
    (workdir / 'record-upsert-help.txt').write_text(helptext)
    import re
    flags = set(re.findall(r'(--[a-z][a-z-]*) string', helptext))   # flags that take a value
    flag = next((f for f in ('--json', '--fields', '--data') if f in flags), None)
    if '--record-id' not in flags or not flag:
        print('lark-cli 的 +record-upsert 参数和预期不同，未写入任何内容。请把 record-upsert-help.txt 发给我。'); sys.exit(1)

    before = export('notes-before.ndjson')
    items = json.load(open(a.notes)); todo = []
    for it in items:
        r = before.get(it['record_id'])
        if r is None: print('记录不存在，跳过', it['record_id']); continue
        old = r.get(a.field) or ''
        if not isinstance(old, str): print('备注不是文本字段，跳过', it['record_id']); continue
        if TAG in old: print('已有说明，跳过', it['record_id']); continue
        todo.append((it['record_id'], old, (old + '\n' if old.strip() else '') + TAG + it['note']))

    def cmd(rid, text, dry):
        c = ['+record-upsert', '--base-token', B, '--table-id', T, '--record-id', rid,
             flag, json.dumps({a.field: text}, ensure_ascii=False), '--as', 'user']
        return run(*(c + (['--dry-run'] if dry else [])))

    if todo:
        p = cmd(todo[0][0], todo[0][2], True)
        if p.returncode != 0:
            print('试运行失败，未写入任何内容：', (p.stdout + p.stderr)[-600:]); sys.exit(1)
    for rid, old, new in todo:
        p = cmd(rid, new, False)
        print('写入' if p.returncode == 0 else '失败', rid, '' if p.returncode == 0 else (p.stdout + p.stderr)[-300:], flush=True)

    after = export('notes-after.ndjson'); bad = []
    for rid, old, new in todo:
        x, y = before[rid], after.get(rid) or {}
        same_rest = all(x.get(k) == y.get(k) for k in ('2D图纸', '替换图纸', '内部型号', '中文产品名称'))
        if (y.get(a.field) or '') != new or not same_rest: bad.append(rid)
    print(f'写入 {len(todo)} 条，核对不一致 {len(bad)} 条', bad)


if __name__ == '__main__':
    main()
