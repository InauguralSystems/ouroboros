"""Ownership gate controls using fake gcc and synthetic child processes only."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import signal
import time
import unittest

DRIVER = Path(__file__).resolve().with_name('slot_index_ownership.sh')
WITNESS = 'slot index: 300 alias cycles; heap/buffer/arena boundaries OK\n'
LSAN = '\n=================================================================\n==123==ERROR: LeakSanitizer: detected memory leaks\n\nDirect leak of {bytes} byte(s) in {count} object(s) allocated from:\n    #0 0x1234 in malloc /runtime/asan.c:12\n    #1 0x5678 in main /fixture.c:1\n\nSUMMARY: AddressSanitizer: {bytes} byte(s) leaked in {count} allocation(s).\n'
FAKE_GCC = '''#!/usr/bin/env python3
import os
from pathlib import Path
import sys
args = sys.argv[1:]
required = ['-O1', '-g', '-fno-omit-frame-pointer', '-fsanitize=address', '-ffp-contract=off', '-UNDEBUG']
assert all(x in args for x in required)
if '-DNDEBUG' in args: assert args.index('-UNDEBUG') > args.index('-DNDEBUG')
assert not any('baseline-include' in x for x in args)
if os.environ.get('FAULT') == 'compile-fail':
    print('planted complete compile diagnostic', file=sys.stderr)
    sys.exit(9)
if os.environ.get('FAULT') == 'no-binary': sys.exit(0)
arm = 'baseline' if '-DSLOT_INDEX_BASELINE' in args else 'candidate'
p = Path(args[args.index('-o') + 1])
p.write_text('#!/usr/bin/env python3\\nARM = ' + repr(arm) + '\\n' + os.environ['FAKE_BINARY'])
p.chmod(0o755)
'''
FAKE_BINARY = '''import os, signal, sys
assert os.environ['ASAN_OPTIONS'] == 'detect_leaks=1:halt_on_error=1:abort_on_error=0:exitcode=1:color=never'
assert os.environ['LSAN_OPTIONS'] == 'exitcode=1'
fault = os.environ.get('FAULT', '')
if fault == 'hang':
    import time
    from pathlib import Path
    Path(os.environ['PIDFILE']).write_text(str(os.getpid()))
    while True: time.sleep(1)
witness = {witness!r}
report = {report!r}
count = 5
if fault == 'increase' and ARM == 'candidate': count = 6
if fault == 'decrease' and ARM == 'candidate': count = 4
report = report.format(bytes=count*16, count=count)
rc = 1
if fault == 'zero': report, rc = '', 0
if fault == 'empty': witness = ''
if fault == 'partial': witness = witness[:-5]
if fault == 'same-wrong-witness': witness = 'incorrect witness\\n'
if fault == 'extra-witness': witness += 'extra\\n'
if fault == 'exit1-empty': report = ''
if fault == 'rc2': rc = 2
if fault == 'rc0-lsan': rc = 0
if fault == 'asan': report = report.replace('Direct leak', 'ERROR: AddressSanitizer: heap-use-after-free\\nDirect leak')
if fault == 'ubsan': report = report.replace('Direct leak', '/bad.c:1: runtime error: overflow\\nDirect leak')
if fault == 'duplicate': report += report
if fault == 'truncated': report = report[:report.index('SUMMARY:')]
if fault == 'missing-block': report = report[:report.index('Direct leak')] + report[report.index('SUMMARY:'):]
if fault == 'missing-frame': report = '\\n'.join(x for x in report.split('\\n') if not x.startswith('    #'))
if fault == 'count-disagrees': report = report.replace('in 5 allocation', 'in 6 allocation')
if fault == 'trailing-error': report += 'fatal error\\n'
if fault == 'lsan-fatal': report = '==123==LeakSanitizer has encountered a fatal error.\\n'
sys.stdout.write(witness)
sys.stdout.flush()
sys.stderr.write(report)
sys.stderr.flush()
if fault == 'signal': os.kill(os.getpid(), signal.SIGSEGV)
sys.exit(rc)
'''.format(witness=WITNESS, report=LSAN)

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='slot-gate-stub-')
        self.root = Path(self.temp.name)
        self.aot = self.root / 'compiler with spaces/aot'
        (self.aot / 'test').mkdir(parents=True)
        (self.aot / 'build/asan').mkdir(parents=True)
        self.runtime = self.root / 'runtime with spaces'
        (self.runtime / 'src').mkdir(parents=True)
        self.driver = self.aot / 'test/slot_index_ownership.sh'
        shutil.copyfile(DRIVER, self.driver)
        for p in [self.aot / 'test/slot_index_ownership.c', self.aot / 'aot_rt.h', self.aot / 'build/asan/libeigsrt.a', self.runtime / 'src/eigenscript.h']:
            p.write_text('synthetic input\n')
        fakebin = self.root / 'bin'
        fakebin.mkdir()
        compiler = fakebin / 'gcc'
        compiler.write_text(FAKE_GCC)
        compiler.chmod(0o755)
        self.env = {**os.environ, 'EIGS_DIR': str(self.runtime), 'PATH': str(fakebin) + os.pathsep + os.environ['PATH'], 'FAKE_BINARY': FAKE_BINARY, 'AOT_ARCH': '-DNDEBUG -march=native', 'TMPDIR': str(self.root), 'LSAN_OPTIONS': 'suppressions=/untrusted/suppression'}

    def tearDown(self):
        self.temp.cleanup()

    def run_case(self, fault, success=False):
        result = subprocess.run(['bash', str(self.driver)], env={**self.env, 'FAULT': fault}, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout)
        self.assertEqual('PASS: slot index ownership arms=2 witnesses=2' in result.stdout, success, result.stdout)
        dirs = list(self.root.glob('aot-slot-index-ownership-*'))
        if success:
            self.assertEqual(dirs, [])
        elif 'missing or empty input:' not in result.stdout:
            self.assertEqual(len(dirs), 1, result.stdout)
            self.assertTrue((dirs[0] / 'failure.txt').is_file())
            self.assertIn('Diagnostics retained:', result.stdout)
            for d in dirs:
                shutil.rmtree(d)
        return result

    def test_positive_controls(self):
        for fault in ['', 'zero', 'decrease']:
            with self.subTest(fault=fault): self.run_case(fault, True)

    def test_planted_faults(self):
        for fault in ['increase', 'empty', 'partial', 'same-wrong-witness', 'extra-witness', 'exit1-empty', 'rc2', 'rc0-lsan', 'asan', 'ubsan', 'duplicate', 'truncated', 'missing-block', 'missing-frame', 'count-disagrees', 'trailing-error', 'signal', 'compile-fail', 'no-binary', 'lsan-fatal']:
            with self.subTest(fault=fault): self.run_case(fault)

    def test_missing_and_empty_inputs(self):
        for relative in ['build/asan/libeigsrt.a', 'test/slot_index_ownership.c', 'aot_rt.h']:
            path = self.aot / relative
            for content in [None, '']:
                with self.subTest(path=relative, content=content):
                    path.unlink()
                    if content is not None: path.write_text(content)
                    self.run_case('')
                    path.write_text('synthetic input\n')

    def test_sigterm_cleans_child(self):
        pidfile = self.root / 'child.pid'
        child_pid = None
        runner = subprocess.Popen(['bash', str(self.driver)], env={**self.env, 'FAULT': 'hang', 'PIDFILE': str(pidfile)}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if pidfile.exists() and pidfile.read_text():
                    child_pid = int(pidfile.read_text())
                    break
                time.sleep(0.01)
            self.assertIsNotNone(child_pid)
            runner.send_signal(signal.SIGTERM)
            output, _ = runner.communicate(timeout=5)
            self.assertEqual(runner.returncode, 1, output)
            self.assertIn('interrupted by signal 15', output)
            self.assertIn('Diagnostics retained:', output)
            with self.assertRaises(ProcessLookupError): os.kill(child_pid, 0)
            self.assertEqual(len(list(self.root.glob('aot-slot-index-ownership-*/baseline.rc'))), 1)
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.wait()
            if runner.stdout: runner.stdout.close()
            if child_pid is not None:
                try: os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def test_empty_leak_population(self):
        source = DRIVER.with_name('leak.sh').read_text()
        (self.aot / 'test/leak.sh').write_text(source)
        result = subprocess.run(['bash', str(self.aot / 'test/leak.sh')], env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('0 fixture(s)', result.stdout)
        self.assertIn('FAIL: leak tier has no test/leak/*.eigs fixtures', result.stdout)
        self.assertNotIn('slot index ownership:', result.stdout)

if __name__ == '__main__': unittest.main(verbosity=2)
