#ifndef GC_PROBE_METRICS_H
#define GC_PROBE_METRICS_H
#include <stdint.h>
typedef struct {
    uint64_t calls, skipped_busy, skipped_empty, started, completed, aborted;
    uint64_t universe, reclaimed, survivors, seeds, nochild_skips;
    uint64_t slots[4][3], edges[4][3];
} GcProbeStats;
/* Phases: discovery=0, original internal count=1, mark=2, clear=3.
 * Kinds: value=0, env=1, chunk=2. Clear visits every owned slot, including
 * leaves; its edges counter is unused because CLEAR has no IS_NODE check. */
#ifdef GC_PROBE_DIAGNOSTICS
void gc_probe_snapshot(GcProbeStats *out);
#endif
#endif
