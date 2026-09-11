#ifndef EMS_LEN_PROBE_H
#define EMS_LEN_PROBE_H
/* Timing-only experiment. The production runtime header is unmodified. */
#include "aot_rt.h"
extern Value *builtin_len(Value *arg);
#ifdef LEN_PROBE_DIAGNOSTICS
#include <inttypes.h>
static uint64_t len_probe_entry, len_probe_eligible, len_probe_fallback;
static void len_probe_report(void) {
    fprintf(stderr, "LEN_PROBE {\"entry\":%" PRIu64 ",\"eligible\":%" PRIu64
            ",\"fallback\":%" PRIu64 "}\n",
            len_probe_entry, len_probe_eligible, len_probe_fallback);
}
static void len_probe_enter(void) {
    if (!len_probe_entry && atexit(len_probe_report) != 0) abort();
    ++len_probe_entry;
}
#define LEN_PROBE_ENTER() len_probe_enter()
#define LEN_PROBE_COUNT(name) (++len_probe_##name)
#else
#define LEN_PROBE_ENTER() ((void)0)
#define LEN_PROBE_COUNT(name) ((void)0)
#endif

static Value *len_probe_dispatch(Value *fn, Value *arg) {
    LEN_PROBE_ENTER();
    if (fn && fn->type == VAL_BUILTIN && fn->data.builtin == builtin_len &&
        arg && (arg->type == VAL_LIST || arg->type == VAL_BUFFER)
#ifdef LEN_PROBE_CONTROL_FORCE_FALLBACK
        && 0 /* Deliberate reach-control variant; never a timing variant. */
#endif
    ) {
        LEN_PROBE_COUNT(eligible);
        /* Same invocation, boxed result, and post-call checks as dispatch.
         * builtin_len returns make_num(count) for these two tags, an owned
         * result; it never borrows a list element. Thus omit ONLY dispatch's
         * list-element identity scan and its borrowed-result compensation. */
        Value *res = aot_call_vm_builtin(fn, arg);
        if (g_exit_requested) exit(g_exit_code);
        if (g_has_error) aot_error_exit();
        if (!res) { val_decref(arg); val_decref(fn); return make_null(); }
        if (res == arg) { val_decref(fn); return res; }
        val_decref(arg);
        val_decref(fn);
        return res;
    }
    LEN_PROBE_COUNT(fallback);
    return aot_call_dispatch(fn, arg);
}
#endif
