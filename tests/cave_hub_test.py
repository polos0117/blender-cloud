"""Integration checks using the generated geometry and public JSON contracts.
Run: BLENDER_BIN=/path/to/blender python3 tests/cave_hub_test.py
"""
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BLENDER = os.environ.get('BLENDER_BIN', 'blender')


def run(folder, *flags, expected=0):
    result = subprocess.run(
        [BLENDER, '-b', '--python', str(ROOT / 'scenes/cave_hub.py'), '--',
         '--check-only', '--no-blend', '--out', str(folder), *flags],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (folder.parent / (folder.name + '.log')).write_text(result.stdout)
    assert result.returncode == expected, result.stdout
    name = 'cave' if '--levels' in flags and flags[flags.index('--levels') + 1] == '1' else 'cave2'
    return json.loads((folder / (name + '-report.json')).read_text())


def read(folder, name):
    return json.loads((folder / name).read_text())


with tempfile.TemporaryDirectory(prefix='cave-test-') as tmp:
    base = Path(tmp)
    legacy = run(base / 'legacy', '--levels', '1')
    current = run(base / 'two')
    assert legacy['all_connected'] and legacy['overlap']['ok']
    assert current['all_connected'] and current['overlap']['ok']
    old = read(base / 'legacy', 'cave-rooms.json')
    new = read(base / 'two', 'cave2-rooms.json')
    assert old['size'] == [1024, 2048] and new['size'] == [1024, 3072]
    assert len(old['rooms']) == 11 and len(new['rooms']) == 16
    assert len(new['ways']) == 5
    for key, room in old['rooms'].items():
        assert room == new['rooms'][key], (key, 'upper projection changed')
    assert {k for k,v in new['rooms'].items() if v['level'] == 2} == {'fav','status','sweep','review','blender'}
    nav = read(base / 'two', 'cave2-nav.json')
    adj = {n: set() for n in nav['nodes']}
    for a,b in nav['edges']:
        assert a != b and a in adj and b in adj
        adj[a].add(b); adj[b].add(a)
        assert math.dist(nav['nodes'][a]['xy'], nav['nodes'][b]['xy']) <= 24.2
    seen = {nav['start']}; stack = [nav['start']]
    while stack:
        for n in adj[stack.pop()] - seen:
            seen.add(n); stack.append(n)
    assert seen == set(nav['nodes'])
    assert set(nav['doors']) == set(new['rooms'])
    assert set(nav['doors'].values()) <= seen
    assert nav['scroll'][-1] == nav['start']
    assert set(nav['doors'].values()) <= set(nav['scroll'])
    assert all(b in adj[a] for a,b in zip(nav['scroll'], nav['scroll'][1:]))
    for node in nav['nodes'].values():
        assert node['level'] in (1,2) and 0 < node['person_px'] < 100
        assert 0 <= node['xy'][0] <= 1024 and 0 <= node['xy'][1] <= 3072
    for key in ('fav', 'spiral'):
        failed = run(base / ('broken-' + key), '--selftest-break', key, expected=2)
        assert not failed['all_connected']
    failed = run(base / 'broken-graph', '--selftest-nav-break', 'fav', expected=2)
    assert failed['navigation']['unreachable'] == ['fav']
    print('PASS: legacy projection; 16 doors reachable; all scroll edges real; 24px spacing; broken bridge, spiral and graph fail')
    print(json.dumps(current['navigation'], ensure_ascii=False))
