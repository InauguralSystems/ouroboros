#include "gc_probe_metrics.h"
#ifdef GC_PROBE_DIAGNOSTICS
#include <inttypes.h>
static GcProbeStats gcp_stats;
static int gcp_phase;
static int gcp_registered;
static void gcp_report(void) {
    fprintf(stderr, "GC_PROBE_TOTAL {\"calls\":%" PRIu64 ",\"skipped_busy\":%" PRIu64
            ",\"skipped_empty\":%" PRIu64 ",\"started\":%" PRIu64
            ",\"completed\":%" PRIu64 ",\"aborted\":%" PRIu64
            ",\"universe\":%" PRIu64 ",\"reclaimed\":%" PRIu64
            ",\"survivors\":%" PRIu64 ",\"seeds\":%" PRIu64
            ",\"nochild_skips\":%" PRIu64 ",\"slots\":[",
            gcp_stats.calls, gcp_stats.skipped_busy, gcp_stats.skipped_empty,
            gcp_stats.started, gcp_stats.completed, gcp_stats.aborted,
            gcp_stats.universe, gcp_stats.reclaimed, gcp_stats.survivors,
            gcp_stats.seeds, gcp_stats.nochild_skips);
    for (int p=0; p<4; ++p)
        fprintf(stderr, "%s[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]", p?",":"",
                gcp_stats.slots[p][0], gcp_stats.slots[p][1], gcp_stats.slots[p][2]);
    fprintf(stderr, "],\"edges\":[");
    for (int p=0; p<4; ++p)
        fprintf(stderr, "%s[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]", p?",":"",
                gcp_stats.edges[p][0], gcp_stats.edges[p][1], gcp_stats.edges[p][2]);
    fprintf(stderr, "]}\n");
}
static void gcp_enter(void) {
    if (!gcp_registered) {
        if (atexit(gcp_report) != 0) abort();
        gcp_registered=1;
    }
    ++gcp_stats.calls;
}
void gc_probe_snapshot(GcProbeStats *out) { *out=gcp_stats; }
#define GCP_ENTER() gcp_enter()
#define GCP_ADD(field,n) (gcp_stats.field += (uint64_t)(n))
#define GCP_PHASE(p) (gcp_phase=(p))
#define GCP_SLOT(k) (++gcp_stats.slots[gcp_phase][k])
#define GCP_EDGE(k) (++gcp_stats.edges[gcp_phase][k])
#ifdef GC_PROBE_FAULT_ZERO_SKIP_COUNTER
#define GCP_NOCHILD() ((void)0)
#else
#define GCP_NOCHILD() GCP_ADD(nochild_skips,1)
#endif
#define GCP_COLLECTION(nodes,garbage,seed_count) do { \
    GCP_ADD(completed,1); GCP_ADD(universe,nodes); \
    GCP_ADD(reclaimed,garbage); GCP_ADD(survivors,(nodes)-(garbage)); \
    GCP_ADD(seeds,seed_count); \
    fprintf(stderr,"GC_PROBE_COLLECTION {\"ordinal\":%" PRIu64 ",\"universe\":%d,\"reclaimed\":%d,\"survivors\":%d,\"seeds\":%d}\n", \
            gcp_stats.completed,(nodes),(garbage),(nodes)-(garbage),(seed_count)); \
} while(0)
#else
#define GCP_ENTER() ((void)0)
#define GCP_ADD(field,n) ((void)0)
#define GCP_PHASE(p) ((void)0)
#define GCP_SLOT(k) ((void)0)
#define GCP_EDGE(k) ((void)0)
#define GCP_NOCHILD() ((void)0)
#define GCP_COLLECTION(nodes,garbage,seed_count) ((void)0)
#endif
