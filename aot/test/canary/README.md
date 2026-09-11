# DMG canary reference

`dmg_cpu_instrs.out` is the existing 50,000,000-cycle `cpu_instrs.gb`
reference used by `aot/canary_dmg.sh`. It was restored byte-for-byte on
2026-09-11 from the canonical ouroboros checkout's artifact (206 bytes,
file modification time 2026-09-01). The file had been excluded by the global
`*.out` ignore rule and was therefore absent from fresh worktrees.

Restored SHA-256:
`2d82872a5de46c278306e8368a1f85971b6c6265868df098d316301572f45938`.
Source: `/home/jon/src/InauguralSystems/EigenScriptEcosystem/ouroboros/aot/test/canary/dmg_cpu_instrs.out`.
The timestamp identifies the recovered file; it is not a compiler revision.

Independent validation on 2026-09-11 reproduced these bytes with both the
pre-change DMG AOT binary and the Dockerfile-pinned EigenScript VM (`v0.43.0`,
commit `a6c50fba6a6250ea347a34500d6c9fa503a5c931`). Both exited 0 with empty
stderr at `--cycles 50000000` (50,000,008 actual cycles). Each stdout matched
after the existing canary timing-line filter. The raw validation outputs are
banked under [dmg-reference-check](../../bench/ems-native-20260911/validation/dmg-reference-check/).
The [DMG provenance](../../bench/ems-native-20260911/instructions/provenance.json)
records source commit `ff29549873f625f99d78dd2e2c3cc884bc17d01a`, ROM hash,
reference hash, and both measured AOT binary hashes.

Do not regenerate this reference from the candidate being checked. Any deliberate
reference update requires independent pinned-VM or pre-change validation.
