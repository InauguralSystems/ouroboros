from pathlib import Path
import hashlib
import json
import os
import re
import statistics
import subprocess

root = Path('/tmp/ems-gap-20260911/instructions')
root.mkdir(exist_ok=True)
assert not any(root.iterdir()), 'refuse to overwrite previous measurement artifacts'
repo = Path('/home/jon/src/wt/ouro-ems-20260910')
dmg = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/DMG')
old = Path('/tmp/ems-native-20260910')
new = root.parent
ems_case = next(x for x in json.loads((new / 'wall-production/cases.json').read_text()) if x['name'] == 'tseitin-4x4-odd')
cnf = Path(ems_case['cnf'])
reference = (cnf.parent / 'vm.out.normalized').read_bytes()
baseline_dmg = (repo / 'aot/test/canary/dmg_cpu_instrs.out').read_bytes()
cases = {
    'ems-4x4': {'binaries': [old / 'ems-active-count', new / 'ems-production'],
                'args': ['--cdcl', str(cnf)], 'cwd': dmg.parent / 'EigenMiniSat'},
    'dmg-50m': {'binaries': [new / 'dmg-baseline', new / 'dmg-production'],
               'args': ['roms/cpu_instrs.gb', '--cycles', '50000000'], 'cwd': dmg},
}
timing = re.compile(rb' ms=[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$')
def normalized(data, case):
    if case == 'ems-4x4':
        return b'\n'.join(timing.sub(b'', line) if line.startswith(b'c ') else line for line in data.split(b'\n'))
    return b'\n'.join(line for line in data.split(b'\n') if not re.search(rb'MHz|[0-9]ms|seconds|cycles/s|elapsed', line))

records = []
for repetition in range(1, 6):
    for name, case in cases.items():
        for arm in ([0, 1] if repetition % 2 else [1, 0]):
            label = 'baseline' if arm == 0 else 'candidate'
            binary = case['binaries'][arm]
            directory = root / name / f'{repetition}-{label}'
            directory.mkdir(parents=True)
            print(name, repetition, label, flush=True)
            sha = hashlib.sha256(binary.read_bytes()).hexdigest()
            command = ['perf', 'stat', '-e', 'instructions:u,cycles:u', '-x,', '-o', str(directory / 'counters.csv'),
                       str(binary), *case['args']]
            with (directory / 'stdout').open('wb') as stdout, (directory / 'stderr').open('wb') as stderr:
                result = subprocess.run(command, cwd=case['cwd'], env=dict(os.environ, LC_ALL='C'),
                                        stdout=stdout, stderr=stderr, timeout=120)
            assert result.returncode == 0, (name, label, result.returncode)
            assert hashlib.sha256(binary.read_bytes()).hexdigest() == sha
            assert (directory / 'stderr').read_bytes() == b'', (name, label)
            output = normalized((directory / 'stdout').read_bytes(), name)
            expected = reference if name == 'ems-4x4' else normalized(baseline_dmg, name)
            assert output == expected and output.strip(), (name, label, directory)
            events = {}
            for line in (directory / 'counters.csv').read_text().splitlines():
                if not line or line.startswith('#'):
                    continue
                fields = line.split(',')
                event = fields[2]
                assert event in ('instructions:u', 'cycles:u') and event not in events, fields
                events[event] = int(fields[0])
                assert events[event] > 0 and float(fields[4]) >= 99.0, fields
            assert len(events) == 2
            record = {'case': name, 'repetition': repetition, 'arm': label, 'command': command,
                      'binary_sha256': sha, 'validated': True, **events}
            if name == 'dmg-50m':
                cycles = re.findall(rb'^Cycles: ([0-9]+)$', output, re.MULTILINE)
                assert len(cycles) == 1 and int(cycles[0]) == 50000008
                record['emulated_cycles'] = int(cycles[0])
            records.append(record)
            (directory / 'record.json').write_text(json.dumps(record, indent=2) + '\n')
summary = {}
for name in cases:
    summary[name] = {}
    for arm in ('baseline', 'candidate'):
        values = [r for r in records if r['case'] == name and r['arm'] == arm]
        assert len(values) == 5
        summary[name][arm] = {'n': 5}
        for event in ('instructions:u', 'cycles:u'):
            data = [r[event] for r in values]
            summary[name][arm][event] = {'median': statistics.median(data), 'min': min(data), 'max': max(data)}
        if name == 'dmg-50m':
            summary[name][arm]['emulated_cycles'] = 50000008
            summary[name][arm]['host_instructions_per_emulated_cycle'] = summary[name][arm]['instructions:u']['median'] / 50000008
(root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2), flush=True)
print('INSTRUCTIONS_DONE cases=2 arms=2 n=5 validated=20', flush=True)
