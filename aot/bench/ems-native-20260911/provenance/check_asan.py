from pathlib import Path
import os
import re
import subprocess

repo = Path('/home/jon/src/wt/ouro-ems-20260910')
runtime = Path('/home/jon/src/wt/es-v043')
old_repo = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/ouroboros')
root = Path('/tmp/ems-gap-20260911/asan')
root.mkdir(exist_ok=True)
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1')

def build(source, output, old=False, baseline=False):
    header = Path('/tmp/ems-gap-20260911/baseline-include') if old else repo / 'aot'
    command = ['gcc', '-O1', '-g', '-fno-omit-frame-pointer', '-fsanitize=address',
               '-ffp-contract=off', '-march=native', '-DEIGENSCRIPT_EXT_HTTP=0',
               '-DEIGENSCRIPT_EXT_MODEL=0', '-DEIGENSCRIPT_EXT_DB=0', '-DEIGENSCRIPT_VERSION="aot"',
               '-DAOT_SCRIPT_DIR="' + str(repo / 'aot/test') + '"',
               '-DAOT_EXE_DIR="' + str(runtime / 'src') + '"',
               '-I' + str(header), '-I' + str(runtime / 'src')]
    if baseline:
        command.append('-DSLOT_INDEX_BASELINE')
    command += [str(source), str(repo / 'aot/build/asan/libeigsrt.a'), '-lm', '-lpthread', '-o', str(output)]
    with output.with_suffix('.build.log').open('wb') as log:
        subprocess.run(command, stdout=log, stderr=log, check=True, timeout=180)

def run(binary, label):
    result = subprocess.run([str(binary)], cwd=repo, env=env, capture_output=True, timeout=120)
    (root / (label + '.stdout')).write_bytes(result.stdout)
    (root / (label + '.stderr')).write_bytes(result.stderr)
    err = result.stderr.decode()
    summaries = re.findall(r'SUMMARY: AddressSanitizer: ([0-9]+) byte\(s\) leaked in ([0-9]+) allocation\(s\)\.', err)
    assert not re.search(r'AddressSanitizer: (?:heap|stack|global|use|double|DEADLYSIGNAL)|Assertion .* failed|runtime error:', err), (label, err)
    if summaries:
        assert result.returncode == 1 and len(summaries) == 1 and 'ERROR: LeakSanitizer: detected memory leaks' in err, (label, result.returncode, err)
        count = int(summaries[0][1])
    else:
        assert result.returncode == 0 and not err, (label, result.returncode, err)
        count = 0
    assert result.stdout, label
    print(label, 'rc', result.returncode, 'leak_allocations', count, 'witness', result.stdout.decode().strip(), flush=True)
    return result.stdout, count

c = repo / 'aot/test/slot_index_ownership.c'
build(c, root / 'direct-old', old=True, baseline=True)
build(c, root / 'direct-new')
old, new = run(root / 'direct-old', 'direct-old'), run(root / 'direct-new', 'direct-new')
assert old[0] == new[0] and new[1] <= old[1], (old, new)

source = repo / 'aot/test/t341_slot_index_ownership.eigs'
with (root / 't341-old.c').open('wb') as out, (root / 't341-old.transpile.err').open('wb') as err:
    subprocess.run([str(runtime / 'src/eigenscript'), 'aot/compile.eigs', str(source), str(runtime)],
                   cwd=old_repo, stdout=out, stderr=err, check=True, timeout=180)
build(root / 't341-old.c', root / 't341-old', old=True)
old = run(root / 't341-old', 't341-old')
new = run(Path('/tmp/ems-gap-20260911/ownership-eigs-asan'), 't341-new')
assert old[0] == new[0] and new[1] <= old[1], (old, new)
print('ASAN_AB_DONE direct_and_eigs=2 no_new_leaks=2', flush=True)
