#ifndef AOT_RT_H
#define AOT_RT_H
/* AOT runtime support (slice 1).
 *
 * Transpiled EigenScript calls the EXISTING runtime through this thin layer.
 * Discipline: OPERANDS ARE CONSUMED, RESULT IS OWNED — mirroring the stack VM
 * (pop operands, push result), so generated C is leak-clean by linear value
 * flow. Each aot_* op adopts its argument refs and returns one new ref.
 *
 * The handful of ops inlined in vm.c's dispatch loop (arith/compare) are
 * mirrored here so the VM hot path stays untouched and the VM remains the
 * differential oracle. */
#include "eigs_embed.h"
#include "eigenscript.h"
#include "value_slot.h"
#include "trace.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- uncaught-error death (#103) ------------------------------------------
 * The AOT has no try/catch, so EVERY runtime error is uncaught and fatal —
 * the VM's uncaught path exits 1 (main.c: g_has_error -> 1). Two problems
 * with letting the runtime's rt_error handle that itself:
 *   1. its uncaught print path calls vm_print_stack_trace, whose first read
 *      is g_vm.frame_count == (*eigs_current->vm).frame_count — and a native
 *      binary never attaches a VM, so ->vm is NULL: the diagnostic printed,
 *      then SIGSEGV (rc 139), core dumps and all. That was #103, verified by
 *      inspection of the pin's rt_error/vm_print_stack_trace/g_vm macro.
 *   2. rt_error RETURNS, so the generated code ran on past the error — the
 *      run-past class this repo exists to kill.
 * The fix: aot_boot elevates g_try_depth to 1 for the whole process, so
 * rt_error (from our helpers AND from raising builtins inside the runtime)
 * only RECORDS the error — never touching the NULL VM — and the AOT prints
 * the recorded message and exits 1 itself: clean nonzero death, same code
 * and same message frame as the VM. */
static void aot_error_exit(void) {
    fprintf(stderr, "%s\n", g_error_msg);
    exit(1);
}
/* Painting the name blue: the macro's inner rt_error is the real function;
 * every call site in this header and in generated C dies cleanly after it. */
#define rt_error(...) do { rt_error(__VA_ARGS__); aot_error_exit(); } while (0)

/* ---- portable SIMD layer for vectorized element-wise numeric loops ----
 * The emitter writes vectorized loops in terms of AOT_VW / aot_v*; this maps
 * them to the widest available ISA at compile time. The packed guard uses
 * min/max INTRINSICS (vector-extension select-clamp is ~4x more ops and loses).
 * aot_vguard is byte-exact vs num_guard (NaN->0 via cmp+and, +/-1e308 clamp). */
#if defined(__x86_64__) || defined(__i386__)
#include <immintrin.h>
#endif
#if defined(__AVX2__)
typedef __m256d aot_vec;
#define AOT_VW 4
#define aot_vset   _mm256_set1_pd
#define aot_vload  _mm256_loadu_pd
#define aot_vstore _mm256_storeu_pd
#define aot_vmul   _mm256_mul_pd
#define aot_vadd   _mm256_add_pd
#define aot_vsub   _mm256_sub_pd
static inline aot_vec aot_vguard(aot_vec x){
    x = _mm256_and_pd(x, _mm256_cmp_pd(x, x, _CMP_EQ_OQ));
    x = _mm256_min_pd(x, _mm256_set1_pd(1e308));
    return _mm256_max_pd(x, _mm256_set1_pd(-1e308));
}
static inline aot_vec aot_viota(long base){ return _mm256_add_pd(_mm256_set1_pd((double)base), _mm256_set_pd(3.0,2.0,1.0,0.0)); }
static inline aot_vec aot_vdiv(aot_vec a, aot_vec b){   /* any b==0 lane RAISES (round 72: the VM raises post-fail-soft-reform; the old lane mask was the same fossil as aot_ddiv's) */
    aot_vec z = _mm256_cmp_pd(b, _mm256_setzero_pd(), _CMP_EQ_OQ);
    if (_mm256_movemask_pd(z))
        rt_error(EK_VALUE, g_trace_current_line, "division by zero");
    return aot_vguard(_mm256_div_pd(a, b));
}
static inline double aot_vhsum(aot_vec x){
    __m128d lo = _mm256_castpd256_pd128(x), hi = _mm256_extractf128_pd(x, 1);
    __m128d s = _mm_add_pd(lo, hi);
    return _mm_cvtsd_f64(_mm_add_pd(s, _mm_unpackhi_pd(s, s)));
}
#elif defined(__SSE2__)
typedef __m128d aot_vec;
#define AOT_VW 2
#define aot_vset   _mm_set1_pd
#define aot_vload  _mm_loadu_pd
#define aot_vstore _mm_storeu_pd
#define aot_vmul   _mm_mul_pd
#define aot_vadd   _mm_add_pd
#define aot_vsub   _mm_sub_pd
static inline aot_vec aot_vguard(aot_vec x){
    x = _mm_and_pd(x, _mm_cmpeq_pd(x, x));
    x = _mm_min_pd(x, _mm_set1_pd(1e308));
    return _mm_max_pd(x, _mm_set1_pd(-1e308));
}
static inline aot_vec aot_viota(long base){ return _mm_add_pd(_mm_set1_pd((double)base), _mm_set_pd(1.0,0.0)); }
static inline aot_vec aot_vdiv(aot_vec a, aot_vec b){   /* any b==0 lane RAISES (round 72, see the AVX2 variant) */
    aot_vec z = _mm_cmpeq_pd(b, _mm_setzero_pd());
    if (_mm_movemask_pd(z))
        rt_error(EK_VALUE, g_trace_current_line, "division by zero");
    return aot_vguard(_mm_div_pd(a, b));
}
static inline double aot_vhsum(aot_vec x){ return _mm_cvtsd_f64(_mm_add_pd(x, _mm_unpackhi_pd(x, x))); }
#else
typedef double aot_vec;
#define AOT_VW 1
#define aot_vset(s)     (s)
#define aot_vload(p)    (*(p))
#define aot_vstore(p,v) (*(p) = (v))
#define aot_vmul(a,b)   ((a)*(b))
#define aot_vadd(a,b)   ((a)+(b))
#define aot_vsub(a,b)   ((a)-(b))
static inline aot_vec aot_vguard(aot_vec x){ return num_guard(x); }
static inline aot_vec aot_viota(long base){ return (double)base; }
static inline aot_vec aot_vdiv(aot_vec a, aot_vec b){ if (b == 0.0) rt_error(EK_VALUE, g_trace_current_line, "division by zero"); return num_guard(a / b); }
static inline double aot_vhsum(aot_vec x){ return x; }
#endif

/* ---- lifecycle ---- */
static Env *aot_boot(void) {
    EigsState *st = eigs_open();           /* new + attach + init runtime + builtins */
    if (!st) { fprintf(stderr, "aot: eigs_open failed\n"); exit(1); }
    /* Pretend-caught for the whole run (#103, comment at the top): rt_error
     * records without printing, so it can never walk the NULL VM; every
     * AOT error exit prints g_error_msg itself via aot_error_exit. */
    g_try_depth = 1;
    /* #915 (EigenScript v0.41.0): the runtime's observer gate is per-state
     * and armed by compile_ast — which an AOT binary never runs. Left at 0,
     * the LINKED bookkeeping (observer_slot_update et al.) returns early via
     * eigs_obs_gate_open() and every predicate silently answers from an
     * empty window: t27_observer read all-zeros against the VM's impr=1,
     * the exact silent-wrong the gate's own header forbids. Arm it here,
     * unconditionally: the AOT does its own elision at COMPILE time (the
     * g_observed bit decides whether observer calls are emitted at all), so
     * a non-observing program never reaches the bookkeeping and the flag
     * costs nothing. eigs_obs_enable() is the runtime's one sanctioned
     * arming path (obs_exec_started is still 0 here, so no history gap). */
    eigs_obs_enable();
    /* ---- load_file/import path resolution (#86, F-OURO-34) ----
     * resolve_eigenscript_file_from_ex's chain is anchored on two EigsState
     * dirs the CLI's main.c sets and eigs_open leaves at the "." default:
     *   g_script_dir — dirname(argv[1]), the .eigs program's own directory;
     *   g_exe_dir    — dirname(/proc/self/exe), the eigenscript binary's dir,
     *                  whose ../lib is the stdlib in a source checkout.
     * With both at "." every non-cwd step of the chain was dead in a native
     * binary: `load_file of "lib/int_vector.eigs"` failed anywhere the cwd
     * didn't happen to contain the file (the EigenMiniSat stop on #86).
     *
     * THE RULE: the AOT binary resolves exactly the files the VM would
     * resolve for `eigenscript <original .eigs>` run from the same cwd. So
     * both anchors are baked at BUILD time by build.sh — AOT_SCRIPT_DIR is
     * the absolutized dirname of the compiled program, AOT_EXE_DIR is the
     * absolutized $EIGS_DIR/src of the runtime it linked (NOT the native
     * binary's own location: the binary is a stand-in for the original
     * program, and its imports live next to the SOURCE and that runtime's
     * stdlib). Measured VM order (v0.39.0 probe, 2026-08-16): cwd-relative
     * first, then script dir, script parent, exe-dir stdlib roots. The
     * cwd-relative step stays naturally runtime-dependent — identical on
     * both sides. Consequence, documented not accidental: a moved/deleted
     * source tree breaks the binary's load_file the same way it breaks the
     * VM invocation it mirrors. */
#ifdef AOT_SCRIPT_DIR
    snprintf(g_script_dir, sizeof(g_script_dir), "%s", AOT_SCRIPT_DIR);
#endif
#ifdef AOT_EXE_DIR
    snprintf(g_exe_dir, sizeof(g_exe_dir), "%s", AOT_EXE_DIR);
#endif
    return g_global_env;
}
static void aot_shutdown(Env *g) { (void)g; /* process exit reclaims (slice 1) */ }

/* ---- variable access ----
 * Two tiers, mirroring the VM's two read opcodes:
 *   aot_get       — GET_LOCAL semantics: a miss is an UNSET slot -> null
 *                   (used for the per-call local env __eigs_l; a function
 *                   local read before its branch-conditional first assignment
 *                   answers null in the VM, never an error).
 *   aot_get_named — GET_NAME semantics: a miss RAISES "undefined variable"
 *                   (used for the global env __eigs_g; the VM raises there —
 *                   the old silent make_null let a program print `null` where
 *                   the VM stops, the #100 item-2 leak probe). */
/* ---- per-SITE inline cache for env NAME resolution (#130) -----------------
 * Every boxed read of a global or frame-local compiles to a name lookup: a
 * hash, a chain walk, and a strcmp per access. On DMG's dispatch that is two
 * lookups (_op_table, _exec_ctx) plus a third for the callee, PER EMULATED
 * INSTRUCTION.
 *
 * The VM's own JIT already solved this and its guard is the design copied
 * here: cache the home env and slot index, and validate BOTH the starting
 * env's binding_version and the home env's (jit.c ~2600). A new binding
 * anywhere on the walked chain bumps a version, so a shadowing insert cannot
 * be missed; value writes do not bump, and must not — they land in the same
 * slot, which is exactly what the cache points at.
 *
 * Env and EnvHash are public in eigenscript.h, so the finder is mirrored here
 * rather than requiring a new upstream export. The multithreaded fall-back is
 * deliberate: the runtime takes a shared lock around find+load (#607) because
 * a concurrent module-env grow republishes `values`, and the fast path holds
 * no lock. */
typedef struct {
    Env *start, *home;
    const char *iname;   /* interned name at [idx] — the depth-0 guard */
    int idx, depth;
    uint32_t sver, tver, h;
} AotNameIC;

static inline int aot_env_hash_find(const EnvHash *ht, const char *name,
                                    uint32_t h, char **names) {
    if (!ht->generations) return -1;
    int slot = h & ht->mask;
    uint32_t gen = ht->generation;
    while (ht->generations[slot] == gen) {
        if (ht->hashes[slot] == h) {
            const char *stored = names[ht->indices[slot]];
            if (stored == name || strcmp(stored, name) == 0) return ht->indices[slot];
        }
        slot = (slot + 1) & ht->mask;
    }
    return -1;
}

static EigsSlot *aot_name_slot_slow(Env *start, const char *name, AotNameIC *c) {
    if (!c->h) c->h = env_hash_name(name);
    int depth = 0;
    for (Env *e = start; e; e = e->parent, depth++) {
        int idx = aot_env_hash_find(&e->hash, name, c->h, e->names);
        if (idx >= 0) {
            c->start = start; c->home = e; c->idx = idx; c->depth = depth;
            c->iname = e->names[idx];      /* interned */
            c->sver = start->binding_version;
            c->tver = e->binding_version;
            return &e->values[idx];
        }
    }
    c->home = NULL;
    return NULL;
}

/* Two guards, because the two cases have different stability.
 *
 * depth 0 — the binding lives in the STARTING env. A frame env (__eigs_l) is
 * a fresh Env on every call, so an env-identity guard can never hit there:
 * that was aot_name_slot_slow burning 3.9% of DMG with an inline cache
 * nominally installed. Validate the SHAPE instead — names are interned, so
 * `names[idx] == iname` says this slot holds this exact binding, whichever
 * Env object we are looking at. Nothing can shadow the starting env, so a
 * hit needs no version check at all.
 *
 * depth > 0 — the walk passed through envs that could gain a shadowing
 * binding, which is precisely what binding_version is for; keep the JIT's
 * two-version guard (start's and home's). */
static inline EigsSlot *aot_name_slot(Env *start, const char *name, AotNameIC *c) {
    if (__builtin_expect(c->depth == 0 && c->home != NULL
                         && c->idx < start->count
                         && start->names[c->idx] == c->iname
                         && !g_vm_multithreaded, 1))
        return &start->values[c->idx];
    Env *home = c->home;
    if (home != NULL && c->depth > 0 && c->start == start
        && c->sver == start->binding_version
        && c->tver == home->binding_version
        && !g_vm_multithreaded)
        return &home->values[c->idx];
    return aot_name_slot_slow(start, name, c);
}

/* Materialize an immediate into the slot exactly as env_get_hashed does, so
 * the returned pointer's lifetime matches the slot's. */
static inline Value *aot_slot_value(EigsSlot *sp) {
    if (slot_is_ptr(*sp)) return slot_as_ptr(*sp);
    Value *v = slot_to_value(*sp);
    slot_decref(*sp);
    *sp = slot_from_heap(v);
    return v;
}

static Value *aot_get_named_ic(Env *g, const char *name, AotNameIC *c) {
    EigsSlot *sp = aot_name_slot(g, name, c);
    if (!sp) rt_error(EK_UNDEFINED_NAME, 0, "undefined variable '%s'", name);
    Value *v = aot_slot_value(sp);
    val_incref(v);
    return v;
}
static Value *aot_getb_named_ic(Env *g, const char *name, AotNameIC *c) {
    EigsSlot *sp = aot_name_slot(g, name, c);
    if (!sp) rt_error(EK_UNDEFINED_NAME, 0, "undefined variable '%s'", name);
    return aot_slot_value(sp);
}
static Value *aot_get_ic(Env *l, const char *name, AotNameIC *c) {
    EigsSlot *sp = aot_name_slot(l, name, c);
    if (!sp) return make_null();
    Value *v = aot_slot_value(sp);
    val_incref(v);
    return v;
}
static Value *aot_getb_ic(Env *l, const char *name, AotNameIC *c) {
    EigsSlot *sp = aot_name_slot(l, name, c);
    return sp ? aot_slot_value(sp) : NULL;   /* env_get's NULL-on-miss contract */
}

static Value *aot_get(Env *g, const char *name) {
    Value *v = env_get(g, name);
    if (!v) return make_null();
    val_incref(v);
    return v;
}
static Value *aot_get_named(Env *g, const char *name) {
    Value *v = env_get(g, name);
    if (!v) rt_error(EK_UNDEFINED_NAME, 0, "undefined variable '%s'", name);
    val_incref(v);
    return v;
}
/* borrowed variant (call-argument reads: no incref, callee doesn't consume) */
static Value *aot_getb_named(Env *g, const char *name) {
    Value *v = env_get(g, name);
    if (!v) rt_error(EK_UNDEFINED_NAME, 0, "undefined variable '%s'", name);
    return v;
}
static void aot_set(Env *g, const char *name, Value *val) {
    env_set_local_owned(g, name, val);     /* adopts val's birth ref */
}

/* ---- boxed module-global `local` shadow (#86, F-OURO-35) ----
 * The VM's chain walk in miniature: once `local NAME is <boxed>` has
 * EXECUTED in this call, the frame binding (__eigs_l) wins; before it (and
 * in calls where the decl never runs), the module binding (__eigs_g)
 * serves reads and plain writes. env_get returns C NULL only for
 * "unbound" (a bound null is a VAL_NULL Value), so binding presence IS
 * the dispatch — which makes pre-decl occurrences, iteration carry,
 * block-outliving shadows, re-decls and per-call freshness exact without
 * any compile-time occurrence analysis (all oracle-probed at v0.39.0). */
static Value *aot_get_sh(Env *l, Env *g, const char *name) {
    Value *v = env_get(l, name);
    if (v) { val_incref(v); return v; }
    return aot_get_named(g, name);
}
/* borrowed variant (call-argument reads) */
static Value *aot_getb_sh(Env *l, Env *g, const char *name) {
    Value *v = env_get(l, name);
    if (v) return v;
    return aot_getb_named(g, name);
}
/* plain (non-`local`) write to a shadow name: the shadow if bound, else
 * the module binding — the VM's SET_NAME chain walk. Adopts val. */
static void aot_set_sh(Env *l, Env *g, const char *name, Value *val) {
    if (env_get(l, name)) env_set_local_owned(l, name, val);
    else env_set_local_owned(g, name, val);
}

/* forward declarations: the out-of-line bodies live further down, next to the
 * other dict/index helpers they belong with. */
static Value *aot_dot_get_tb_slow(Value *target, const char *key, int *ic, const char **ick);
static double aot_dot_num_tb_slow(Value *target, const char *key, int *ic, const char **ick, const char *site);
static void   aot_dot_set_num_tb_slow(Value *target, const char *key, double d, int *ic, const char **ick);
static Value *aot_index_get_ib_slow(Value *target, double d);

/* ---- inlinable fast paths (#133) -----------------------------------------
 * The three dict-field helpers plus the integer index read are 20.8% of DMG's
 * runtime across 968 call sites, and each was a plain out-of-line `static`:
 * every access paid a real call for what is, on a cache hit, a bounds check, a
 * pointer compare and an array index. Split them — an inlinable fast path that
 * handles exactly the hit, and the original body kept out of line behind
 * `noinline` so the fast path stays small enough not to cost I-cache on a
 * machine where that is the binding constraint.
 *
 * Semantics are unchanged BY CONSTRUCTION: every case the fast path does not
 * itself answer (wrong type, cold cache, missing key, negative or non-integral
 * index, a shared or non-numeric slot) falls through to the untouched original.
 * There is no second implementation to keep in step. */
static inline Value *aot_dot_get_tb_ic(Value *target, const char *key,
                                       int *ic, const char **ick) {
    if (__builtin_expect(target != NULL && target->type == VAL_DICT, 1)) {
        int i = *ic;
        if (__builtin_expect(i >= 0 && i < target->data.dict.count
                             && target->data.dict.keys[i] == *ick, 1)) {
            Value *v = target->data.dict.vals[i];
            if (v) { val_incref(v); return v; }
        }
    }
    return aot_dot_get_tb_slow(target, key, ic, ick);
}
static inline double aot_dot_num_tb_ic(Value *target, const char *key,
                                       int *ic, const char **ick, const char *site) {
    if (__builtin_expect(target != NULL && target->type == VAL_DICT, 1)) {
        int i = *ic;
        if (__builtin_expect(i >= 0 && i < target->data.dict.count
                             && target->data.dict.keys[i] == *ick, 1)) {
            Value *v = target->data.dict.vals[i];
            if (v && v->type == VAL_NUM) return v->data.num;
        }
    }
    return aot_dot_num_tb_slow(target, key, ic, ick, site);
}
static inline void aot_dot_set_num_tb_ic(Value *target, const char *key, double d,
                                         int *ic, const char **ick) {
    if (__builtin_expect(target != NULL && target->type == VAL_DICT, 1)) {
        int i = *ic;
        if (__builtin_expect(i >= 0 && i < target->data.dict.count
                             && target->data.dict.keys[i] == *ick, 1)) {
            Value *old = target->data.dict.vals[i];
            if (old && old->type == VAL_NUM && old->refcount == 1) {
                old->data.num = num_guard(d);
                return;
            }
        }
    }
    aot_dot_set_num_tb_slow(target, key, d, ic, ick);
}
static inline Value *aot_index_get_ib(Value *target, double d) {
    if (__builtin_expect(target != NULL && target->type == VAL_LIST, 1)) {
        long i = (long)d;
        if (__builtin_expect((double)i == d && i >= 0
                             && i < (long)target->data.list.count, 1)) {
            Value *r = target->data.list.items[i];
            /* The `if (r)` mirrors the slow path's `result ? result :
             * make_null()` and the dict fast paths' own guard. val_incref is
             * NULL-safe, so without it a NULL live slot would hand back a bare
             * NULL where the slow path hands back a VAL_NULL. A review traced
             * every list-slot writer and found no reachable NULL-within-count,
             * so this is not a live bug — but the asymmetry was real, and a
             * fast path that answers a case differently from the arm it
             * shortcuts is the one thing this split must never do. */
            if (r) { val_incref(r); return r; }
        }
    }
    return aot_index_get_ib_slow(target, d);
}

/* ---- truthiness (consumes) ---- */
static int aot_truthy(Value *v) { int t = is_truthy(v); val_decref(v); return t; }

/* ---- string literal ---- */
static Value *aot_str(const char *s) { return make_str(s); }

/* ---- arithmetic / compare: consume operands, return owned ---- */
static Value *aot_add(Value *a, Value *b) {
    Value *r;
    if (a->type == VAL_NUM && b->type == VAL_NUM) {
        r = make_num(num_guard(a->data.num + b->data.num));
    } else if (a->type == VAL_STR && b->type == VAL_STR) {
        size_t la = strlen(a->data.str), lb = strlen(b->data.str);
        char *s = (char *)malloc(la + lb + 1);
        memcpy(s, a->data.str, la); memcpy(s + la, b->data.str, lb); s[la + lb] = 0;
        r = make_str_owned(s);
    } else {
        fprintf(stderr, "cannot apply '+' to mixed types\n"); exit(1);
    }
    val_decref(a); val_decref(b); return r;
}
#define AOT_NUMOP(NAME, EXPR) \
    static Value *NAME(Value *a, Value *b) { \
        if (a->type != VAL_NUM || b->type != VAL_NUM) { \
            fprintf(stderr, "non-numeric operand\n"); exit(1); } \
        double x = a->data.num, y = b->data.num; (void)x; (void)y; \
        Value *r = make_num(num_guard(EXPR)); \
        val_decref(a); val_decref(b); return r; }
AOT_NUMOP(aot_sub, x - y)
AOT_NUMOP(aot_mul, x * y)

/* Round 72: division/modulo by zero RAISES, mirroring vm.c's OP_DIV/OP_MOD
 * (rt_error EK_VALUE "division by zero"/"modulo by zero"). The old b==0->0
 * arms carried a comment saying "matching the VM" -- a FOSSIL of the
 * pre-fail-soft-reform VM (the 2026-08 reform made these raise upstream;
 * the pinned oracle raises, and `print of (7 / 0)` printed 0 rc 0 here
 * against the VM's rc 1 -- silent-wrong on the most common operator pair,
 * with zero fixtures covering it). Same rule in every storage regime:
 * boxed NUMOP, unboxed ddiv/dmod, and the int-classified emit (aot_imod
 * below -- raw C `%` by zero compiled to ud2/SIGILL under -O3). */
static inline double aot_div_zero_check(double y) {
    if (y == 0.0)
        rt_error(EK_VALUE, g_trace_current_line, "division by zero");
    return y;
}
static inline double aot_mod_zero_check(double y) {
    if (y == 0.0)
        rt_error(EK_VALUE, g_trace_current_line, "modulo by zero");
    return y;
}
AOT_NUMOP(aot_div, x / aot_div_zero_check(y))
AOT_NUMOP(aot_mod, fmod(x, aot_mod_zero_check(y)))

/* Specialized (unboxed double) div/mod. Single-eval helpers so the emitter
   doesn't have to duplicate the operand expressions. */
static inline double aot_ddiv(double a, double b) { return num_guard(a / aot_div_zero_check(b)); }
static inline double aot_dmod(double a, double b) { return num_guard(fmod(a, aot_mod_zero_check(b))); }
/* Int-classified modulo: raw C `%` is UB on zero (measured: gcc -O3 emitted
 * ud2 -- rc 132 SIGILL, no diagnostic). Negative operands keep C truncated
 * semantics, which match the VM's (probed both paths). */
static inline long aot_imod(long a, long b) {
    if (b == 0)
        rt_error(EK_VALUE, g_trace_current_line, "modulo by zero");
    return a % b;
}

/* Ordering comparisons — the VM's NUM_CMP macro exactly (vm.c ~3100):
 * num/num numeric compare; str/str byte-wise strcmp with the SAME operator
 * applied to the strcmp result; ANY other pair raises EK_TYPE
 * "cannot compare X and Y with 'op'". The old macro silently returned 0 for
 * every non-num/num pair — `"a" < "b"` printed 0 where the VM prints 1, and
 * `1 < "a"` printed 0 where the VM stops (#100 item 1). Type names mirror
 * slot_type_name: a null VALUE reports "none" (the VM stores null as an
 * immediate null slot on every probed path — literal, dict miss, list
 * element — so "none" is what its message prints). */
static const char *aot_cmp_type_name(Value *v) {
    if (!v || v->type == VAL_NULL) return "none";
    return val_type_name(v->type);
}
#define AOT_CMP(NAME, OP, OPNAME) \
    static Value *NAME(Value *a, Value *b) { \
        double res; \
        if (a->type == VAL_NUM && b->type == VAL_NUM) { \
            res = (a->data.num OP b->data.num) ? 1.0 : 0.0; \
        } else if (a->type == VAL_STR && b->type == VAL_STR) { \
            int c = strcmp(a->data.str ? a->data.str : "", \
                           b->data.str ? b->data.str : ""); \
            res = (c OP 0) ? 1.0 : 0.0; \
        } else { \
            rt_error(EK_TYPE, 0, "cannot compare %s and %s with '%s'", \
                     aot_cmp_type_name(a), aot_cmp_type_name(b), OPNAME); \
        } \
        val_decref(a); val_decref(b); \
        return make_num(res); }
AOT_CMP(aot_lt, <,  "<")
AOT_CMP(aot_gt, >,  ">")
AOT_CMP(aot_le, <=, "<=")
AOT_CMP(aot_ge, >=, ">=")

/* ---- comparison against a numeric operand, without the boxes (#130) -------
 * DMG's emulation loop is full of `cpu.halted == 1`, `mem.x > 0`, and the
 * emitter rendered every one of them fully boxed:
 *
 *   aot_truthy(aot_eq(aot_dot_get_ic(cpu, "halted"), make_num(1)))
 *
 * — a box for the literal, a box for the result, and two frees, per
 * conditional, several times per emulated instruction. make_num called
 * DIRECTLY from run_headless_loop was 5.66% of runtime.
 *
 * The `_n` forms take the numeric side as a C double and the `_t` forms
 * return a C int for condition position. Semantics are unchanged, including
 * for a non-numeric left operand: equality falls back to values_equal against
 * a stack Value (arena=1, so the decrefs are no-ops), and the ordering forms
 * fall back to the raising AOT_CMP path. A null or string field still
 * compares false rather than dying — which is why this is not simply
 * "treat dict fields as numeric". */
static inline int aot_eq_n_t(Value *a, double b) {
    if (a && a->type == VAL_NUM) { int e = (a->data.num == b); val_decref(a); return e; }
    Value bv; memset(&bv, 0, sizeof bv);
    bv.type = VAL_NUM; bv.data.num = b; bv.arena = 1;
    int e = values_equal(a, &bv);
    val_decref(a);
    return e;
}
static inline Value *aot_eq_n(Value *a, double b)  { return make_num(aot_eq_n_t(a, b) ? 1.0 : 0.0); }
static inline int    aot_ne_n_t(Value *a, double b){ return !aot_eq_n_t(a, b); }
static inline Value *aot_ne_n(Value *a, double b)  { return make_num(aot_eq_n_t(a, b) ? 0.0 : 1.0); }

#define AOT_CMP_N(NAME, BASE, OP) \
    static inline int NAME##_t(Value *a, double b) { \
        if (a && a->type == VAL_NUM) { int r = (a->data.num OP b) ? 1 : 0; val_decref(a); return r; } \
        Value bv; memset(&bv, 0, sizeof bv); \
        bv.type = VAL_NUM; bv.data.num = b; bv.arena = 1; \
        return aot_truthy(BASE(a, &bv)); } \
    static inline Value *NAME(Value *a, double b) { return make_num(NAME##_t(a, b) ? 1.0 : 0.0); }
AOT_CMP_N(aot_lt_n, aot_lt, <)
AOT_CMP_N(aot_gt_n, aot_gt, >)
AOT_CMP_N(aot_le_n, aot_le, <=)
AOT_CMP_N(aot_ge_n, aot_ge, >=)

/* Mirrored forms: `<numeric> OP <expr>`, numeric side on the LEFT. The
 * fallback must NOT delegate to the swapped operator: the raise names both
 * operand types AND the operator, so `1 < "a"` has to report
 * `cannot compare num and str with '<'`, not `str and num with '>'`
 * (t65_cmp_mixed_err caught exactly that). Keep the order, keep the op. */
#define AOT_CMP_NL(NAME, BASE, OP) \
    static inline int NAME##_t(Value *b, double a) { \
        if (b && b->type == VAL_NUM) { int r = (a OP b->data.num) ? 1 : 0; val_decref(b); return r; } \
        Value av; memset(&av, 0, sizeof av); \
        av.type = VAL_NUM; av.data.num = a; av.arena = 1; \
        return aot_truthy(BASE(&av, b)); } \
    static inline Value *NAME(Value *b, double a) { return make_num(NAME##_t(b, a) ? 1.0 : 0.0); }
AOT_CMP_NL(aot_lt_nl, aot_lt, <)
AOT_CMP_NL(aot_gt_nl, aot_gt, >)
AOT_CMP_NL(aot_le_nl, aot_le, <=)
AOT_CMP_NL(aot_ge_nl, aot_ge, >=)

/* ---- fully borrowed field-compare (#130) ---------------------------------
 * After the boxes came out of the comparisons, what was left on top of the
 * profile was pure bookkeeping: aot_dot_get_ic 11.0% with val_incref +
 * val_decref another 12.3%. A read like `mem.tima_reload_pending > 0` did
 * FOUR refcount operations — incref the target because the reader consumes
 * it, incref the result, decref the target, decref the result — every one of
 * which is provably balanced within a single expression.
 *
 * When the target is a plain C Value* variable (a dict/buffer/generic local or
 * parameter, never a temporary) it is live for the whole expression, and the
 * result is a slot of that dict which nothing in the comparison can free. So
 * borrow both. A missing key borrows as C NULL; the helpers below substitute a
 * stack VAL_NULL so `null == 1` is false and `null < 1` raises naming "null",
 * exactly as the owned path does. */
static inline int aot_eq_nb_t(Value *a, double b) {
    if (a && a->type == VAL_NUM) return a->data.num == b;
    Value nv; memset(&nv, 0, sizeof nv); nv.type = VAL_NULL; nv.arena = 1;
    Value bv; memset(&bv, 0, sizeof bv);
    bv.type = VAL_NUM; bv.data.num = b; bv.arena = 1;
    return values_equal(a ? a : &nv, &bv);
}
static inline int aot_ne_nb_t(Value *a, double b) { return !aot_eq_nb_t(a, b); }

#define AOT_CMP_NB(NAME, BASE, OP) \
    static inline int NAME(Value *a, double b) { \
        if (a && a->type == VAL_NUM) return (a->data.num OP b) ? 1 : 0; \
        Value nv; memset(&nv, 0, sizeof nv); nv.type = VAL_NULL; nv.arena = 1; \
        Value bv; memset(&bv, 0, sizeof bv); \
        bv.type = VAL_NUM; bv.data.num = b; bv.arena = 1; \
        return aot_truthy(BASE(a ? a : &nv, &bv)); }
#define AOT_CMP_NBL(NAME, BASE, OP) \
    static inline int NAME(Value *b, double a) { \
        if (b && b->type == VAL_NUM) return (a OP b->data.num) ? 1 : 0; \
        Value nv; memset(&nv, 0, sizeof nv); nv.type = VAL_NULL; nv.arena = 1; \
        Value av; memset(&av, 0, sizeof av); \
        av.type = VAL_NUM; av.data.num = a; av.arena = 1; \
        return aot_truthy(BASE(&av, b ? b : &nv)); }
AOT_CMP_NB(aot_lt_nb_t, aot_lt, <)
AOT_CMP_NB(aot_gt_nb_t, aot_gt, >)
AOT_CMP_NB(aot_le_nb_t, aot_le, <=)
AOT_CMP_NB(aot_ge_nb_t, aot_ge, >=)
AOT_CMP_NBL(aot_lt_nlb_t, aot_lt, <)
AOT_CMP_NBL(aot_gt_nlb_t, aot_gt, >)
AOT_CMP_NBL(aot_le_nlb_t, aot_le, <=)
AOT_CMP_NBL(aot_ge_nlb_t, aot_ge, >=)

static Value *aot_eq(Value *a, Value *b) {
    int e = values_equal(a, b); val_decref(a); val_decref(b);
    return make_num(e ? 1.0 : 0.0);
}
static Value *aot_ne(Value *a, Value *b) {
    int e = values_equal(a, b); val_decref(a); val_decref(b);
    return make_num(e ? 0.0 : 1.0);
}

/* ---- numeric buffers (VAL_BUFFER = double[] + count) ---- */
/* Index resolution mirrors vm_index_is_int + vm_index_resolve: integer-only,
   negative indexes count from the end, bounds-checked. (Those runtime fns are
   static to vm.c, so replicated here.) */
/* Type guard: the compiler's buffer class is inferred from usage, so an
   out-of-envelope input (a string or list reaching a buf-classified param)
   would otherwise misread the value union — a silent wrong number. Loud beats
   silent; in-envelope programs never take the branch (predictable, cold). */
/* (#86) Buffer ops name the offending VALUE. The wrapper macros at the end
 * of this block stringify the first argument's token text, so a failure
 * reports the C variable (which is the .eigs name) with no emitter change:
 *     buffer op on a non-buffer value: `asg` is str
 */
static void aot_buf_expect_at(Value *b, const char *site) {
    if (!b || b->type != VAL_BUFFER) {
        fprintf(stderr, "buffer op on a non-buffer value: `%s` is %s\n",
                site, b ? val_type_name(b->type) : "null");
        exit(1);
    }
}
static void aot_buf_expect(Value *b) { aot_buf_expect_at(b, "?"); }
/* (round 80) these deaths carry the "Error line N:" frame the VM's raises
 * print -- the bare fprintf form failed the _err tier's normalization on
 * the frame prefix alone (line number is blanked by the normalizer; the
 * prefix is semantics). Line is g_trace_current_line: 0 unless traced,
 * per the documented siting contract. */
static long aot_idx(double d, int count) {
    long i = (long)d;
    if ((double)i != d) { fprintf(stderr, "Error line %d: index must be an integer, got %g\n", g_trace_current_line, d); exit(1); }
    if (i < 0) i += count;
    if (i < 0 || i >= count) { fprintf(stderr, "Error line %d: buffer index %ld out of range (length %d)\n", g_trace_current_line, (long)d, count); exit(1); }
    return i;
}
/* The "buf" class is really INDEXABLE VALUE: inference assigns it from
 * `x[i]` / `len of x` usage, and in consumer code the runtime value is as
 * often a VAL_LIST of numbers as a VAL_BUFFER (EigenMiniSat's DIMACS
 * `parts[i]`). Mirror the VM's polymorphic indexing rather than asserting
 * one representation — same rule aot_buf_len already follows for strings. */
static double aot_list_num_at(Value *b, long i, const char *site) {
    Value *e = b->data.list.items[i];
    if (!e || e->type != VAL_NUM) {
        fprintf(stderr, "non-numeric element in `%s`[%ld] (%s)\n",
                site, i, e ? val_type_name(e->type) : "null");
        exit(1);
    }
    return e->data.num;
}
static double aot_buf_get_at(Value *b, double idx, const char *site) {
    if (b && b->type == VAL_LIST) return aot_list_num_at(b, aot_idx(idx, b->data.list.count), site);
    aot_buf_expect_at(b, site); return b->data.buffer.data[aot_idx(idx, b->data.buffer.count)];
}
static void   aot_buf_set_at(Value *b, double idx, double v, const char *site) {
    if (b && b->type == VAL_LIST) {
        long i = aot_idx(idx, b->data.list.count);
        Value *old = b->data.list.items[i];
        b->data.list.items[i] = make_num(v);
        if (old) val_decref(old);
        return;
    }
    aot_buf_expect_at(b, site); b->data.buffer.data[aot_idx(idx, b->data.buffer.count)] = v;
}
/* Integer-index fast path: the index was computed in native `long` arithmetic
   (provably-integer induction vars + dimensions), so skip the float integer
   check. Negative-resolve + bounds-check still mirror the VM. */
static long aot_idx_i(long i, int count) {
    if (i < 0) i += count;
    if (i < 0 || i >= count) { fprintf(stderr, "Error line %d: buffer index %ld out of range (length %d)\n", g_trace_current_line, i, count); exit(1); }
    return i;
}
static double aot_buf_get_i_at(Value *b, long idx, const char *site) {
    if (b && b->type == VAL_LIST) return aot_list_num_at(b, aot_idx_i(idx, b->data.list.count), site);
    aot_buf_expect_at(b, site); return b->data.buffer.data[aot_idx_i(idx, b->data.buffer.count)];
}
static void   aot_buf_set_i_at(Value *b, long idx, double v, const char *site) {
    if (b && b->type == VAL_LIST) {
        long i = aot_idx_i(idx, b->data.list.count);
        Value *old = b->data.list.items[i];
        b->data.list.items[i] = make_num(v);
        if (old) val_decref(old);
        return;
    }
    aot_buf_expect_at(b, site); b->data.buffer.data[aot_idx_i(idx, b->data.buffer.count)] = v;
}
/* max |element|, for the matmul's once-per-call overflow precheck: if
   reduction_len * maxabs(A) * maxabs(B) <= 1e308, no product or partial sum can
   exceed 1e308, so num_guard is identity over the whole reduction and the
   unguarded (faster) accumulation is byte-identical to the VM's guarded one. */
static double aot_buf_maxabs(Value *b) {
    double *d = b->data.buffer.data; long n = b->data.buffer.count, i;
    double m = 0.0;
    for (i = 0; i < n; i++) { double a = d[i] < 0 ? -d[i] : d[i]; if (a > m) m = a; }
    return m;
}
static double aot_buf_len_at(Value *b, const char *site) {
    /* the buf class is a string/buffer union (checksum's `_blen of "abc"`,
     * EigenMiniSat's `_is_int_token`, #86): mirror the VM's `len` on both. */
    if (b && b->type == VAL_STR) return (double)strlen(b->data.str);
    if (b && b->type == VAL_LIST) return (double)b->data.list.count;
    aot_buf_expect_at(b, site);
    return (double)b->data.buffer.count;
}
/* Raw element pointer for the proven-safe (in-bounds, non-negative) loop path. */
static double *aot_buf_data_at(Value *b, const char *site) { aot_buf_expect_at(b, site); return b->data.buffer.data; }

/* Token-stringifying wrappers: `aot_buf_get(asg, i)` reports `asg`. */
#define aot_buf_get(b, i)        aot_buf_get_at((b), (i), #b)
#define aot_buf_set(b, i, v)     aot_buf_set_at((b), (i), (v), #b)
#define aot_buf_get_i(b, i)      aot_buf_get_i_at((b), (i), #b)
#define aot_buf_set_i(b, i, v)   aot_buf_set_i_at((b), (i), (v), #b)
#define aot_buf_len(b)           aot_buf_len_at((b), #b)
#define aot_buf_data(b)          aot_buf_data_at((b), #b)

/* dot of [a,b] = sum_i a[i]*b[i]. The `dot` builtin's spec leaves the summation
 * ASSOCIATION unspecified, which licenses this REASSOCIATED SIMD reduction:
 * AOT_VW parallel partial-sum lanes + horizontal sum + scalar tail. (A strict
 * left-to-right loop cannot vectorize — FP add is non-associative.) Per-lane
 * vguard + final num_guard keep the no-NaN/Inf invariant. Result agrees with
 * the VM within tolerance, not byte-for-byte — see aot/test/run.sh. */
static inline double aot_dot(Value *a, Value *b) {
    double *ad = a->data.buffer.data, *bd = b->data.buffer.data;
    long an = a->data.buffer.count, bn = b->data.buffer.count;
    long n = an < bn ? an : bn, i = 0;
    aot_vec acc = aot_vset(0.0);
    for (; i + AOT_VW <= n; i += AOT_VW)
        acc = aot_vguard(aot_vadd(acc, aot_vguard(aot_vmul(aot_vload(ad + i), aot_vload(bd + i)))));
    double s = aot_vhsum(acc);
    for (; i < n; i++) s = num_guard(s + num_guard(ad[i] * bd[i]));
    return num_guard(s);
}

/* (round 91) The fast reductions index data.buffer.data with NO type check,
 * but the `bt` class is "indexable value" and `zeros of N` produces a LIST in
 * this VM -- `sum of z` returned 2.05e-309 and `norm of z` returned 0 against
 * the VM's 3 and 2.236, rc 0 both sides. The _v wrappers keep the SIMD path
 * for real buffers and defer every other shape to the runtime's OWN builtin:
 * a hand-written fallback is not faithful (measured, this VM's `dot` over
 * LISTS yields 0 while sum/norm fold elementwise), so the oracle answers by
 * construction at a cost only non-buffer shapes pay. */
static Value *aot_call_name(Env *g, const char *name, Value *arg);
static inline long aot_any_len(Value *A) {
    if (A && A->type == VAL_LIST) return A->data.list.count;
    if (A && A->type == VAL_BUFFER) return A->data.buffer.count;
    return 0;
}
static inline double aot_any_at(Value *A, long i) {
    if (A && A->type == VAL_LIST) {
        Value *e = A->data.list.items[i];
        return (e && e->type == VAL_NUM) ? e->data.num : 0.0;
    }
    return A->data.buffer.data[i];
}
static double aot_reduce_poly1(Env *g, const char *name, Value *a) {
    if (a) val_incref(a);
    Value *r = aot_call_name(g, name, a);
    double d = (r && r->type == VAL_NUM) ? r->data.num : 0.0;
    if (r) val_decref(r);
    return d;
}

/* sum of a / norm of a — sibling association-unspecified reductions (same
 * reassociated-SIMD license as aot_dot; tolerance oracle, not byte-exact). */
static inline double aot_sum(Value *a) {
    double *d = a->data.buffer.data;
    long n = a->data.buffer.count, i = 0;
    aot_vec acc = aot_vset(0.0);
    for (; i + AOT_VW <= n; i += AOT_VW)
        acc = aot_vguard(aot_vadd(acc, aot_vload(d + i)));
    double s = aot_vhsum(acc);
    for (; i < n; i++) s = num_guard(s + d[i]);
    return num_guard(s);
}
static inline double aot_norm(Value *a) {
    double *d = a->data.buffer.data;
    long n = a->data.buffer.count, i = 0;
    aot_vec acc = aot_vset(0.0);
    for (; i + AOT_VW <= n; i += AOT_VW) {
        aot_vec v = aot_vload(d + i);
        acc = aot_vguard(aot_vadd(acc, aot_vguard(aot_vmul(v, v))));
    }
    double s = aot_vhsum(acc);
    for (; i < n; i++) s = num_guard(s + num_guard(d[i] * d[i]));
    return num_guard(sqrt(s));
}

static inline double aot_sum_v(Env *g, Value *a) {
    if (a && a->type == VAL_BUFFER) return aot_sum(a);
    return aot_reduce_poly1(g, "sum", a);
}
static inline double aot_norm_v(Env *g, Value *a) {
    if (a && a->type == VAL_BUFFER) return aot_norm(a);
    return aot_reduce_poly1(g, "norm", a);
}
static inline double aot_dot_v(Env *g, Value *a, Value *b) {
    if (a && b && a->type == VAL_BUFFER && b->type == VAL_BUFFER) return aot_dot(a, b);
    Value *l = make_list(2);
    if (a) val_incref(a);
    list_append_owned(l, a);
    if (b) val_incref(b);
    list_append_owned(l, b);
    Value *r = aot_call_name(g, "dot", l);
    double d = (r && r->type == VAL_NUM) ? r->data.num : 0.0;
    if (r) val_decref(r);
    return d;
}

/* ---- ranged reductions over a slice `buf[lo:hi]` (zero-copy) ----
 * `dot of [A[sa:ea], B[sb:eb]]` / `sum of A[s:e]` / `norm of A[s:e]` lower to
 * these instead of materializing the slice. Bound resolution mirrors the VM's
 * OP_SLICE_GET exactly (vm.c): integer-only, negatives count from len, then
 * 0<=start<=end<=len (<= upper end; out-of-range errors like the VM). */
static inline long aot_sbound(double x, long len) {
    long i = (long)x;
    if ((double)i != x) { rt_error(EK_VALUE, g_trace_current_line, "slice bound must be an integer, got %g", x); return 0; }
    if (i < 0) i += len;
    return i;
}
/* (round 92) The VM raises ONE message for every bad slice -- out-of-range and
 * start>end alike -- naming the ORIGINAL (pre-negative-resolve) bounds:
 * "slice %d:%d out of range (length %d)". The per-bound checks printed a
 * different text ("slice bound %ld out of range") and a bare "slice start >
 * end", so these deaths matched on rc and diverged on stderr. Resolve both
 * bounds, then check once, exactly as OP_SLICE_GET does. */
static inline void aot_srange(double sa, double ea, long len, long *s_out, long *e_out) {
    long s = aot_sbound(sa, len), e = aot_sbound(ea, len);
    if (s < 0 || s > len || e < 0 || e > len || s > e)
        rt_error(EK_INDEX, g_trace_current_line, "slice %d:%d out of range (length %d)",
                 (int)sa, (int)ea, (int)len);
    *s_out = s; *e_out = e;
}
static inline double aot_dot_range(Value *A, double sa, double ea, Value *B, double sb, double eb) {
    long la = A->data.buffer.count, lb = B->data.buffer.count;
    long s1, e1, s2, e2;
    aot_srange(sa, ea, la, &s1, &e1);
    aot_srange(sb, eb, lb, &s2, &e2);
    long n1 = e1 - s1, n2 = e2 - s2, n = n1 < n2 ? n1 : n2, i = 0;
    double *a = A->data.buffer.data + s1, *b = B->data.buffer.data + s2;
    aot_vec acc = aot_vset(0.0);
    for (; i + AOT_VW <= n; i += AOT_VW)
        acc = aot_vguard(aot_vadd(acc, aot_vguard(aot_vmul(aot_vload(a + i), aot_vload(b + i)))));
    double s = aot_vhsum(acc);
    for (; i < n; i++) s = num_guard(s + num_guard(a[i] * b[i]));
    return num_guard(s);
}
static inline double aot_sum_range(Value *A, double sa, double ea) {
    long la = A->data.buffer.count;
    long s1, e1;
    aot_srange(sa, ea, la, &s1, &e1);
    long n = e1 - s1, i = 0;
    double *a = A->data.buffer.data + s1;
    aot_vec acc = aot_vset(0.0);
    for (; i + AOT_VW <= n; i += AOT_VW)
        acc = aot_vguard(aot_vadd(acc, aot_vload(a + i)));
    double s = aot_vhsum(acc);
    for (; i < n; i++) s = num_guard(s + a[i]);
    return num_guard(s);
}
static inline double aot_norm_range(Value *A, double sa, double ea) {
    long la = A->data.buffer.count;
    long s1, e1;
    aot_srange(sa, ea, la, &s1, &e1);
    long n = e1 - s1, i = 0;
    double *a = A->data.buffer.data + s1;
    aot_vec acc = aot_vset(0.0);
    for (; i + AOT_VW <= n; i += AOT_VW) {
        aot_vec v = aot_vload(a + i);
        acc = aot_vguard(aot_vadd(acc, aot_vguard(aot_vmul(v, v))));
    }
    double s = aot_vhsum(acc);
    for (; i < n; i++) s = num_guard(s + num_guard(a[i] * a[i]));
    return num_guard(sqrt(s));
}
/* (round 92) Ranged siblings of the _v wrappers. Round 91 shipped these with
 * a HAND-WRITTEN materializer -- breaking the rule its own commit stated for
 * the scalar ones. Measured consequences: a nested list element was coerced
 * to 0.0 (`sum of [[1,2],[3,4]][0:2]` gave 0 where the VM FLATTENS to 10);
 * VAL_STR was called length-0 though the bt class carries strings; and dict/
 * num/null were treated as empty containers, turning the VM's "cannot slice
 * dict" into a silent 0. The slice is now a faithful mirror of the VM's
 * OP_SLICE_GET -- same length rule per type, same negative resolution, same
 * error text with the ORIGINAL bounds, element VALUES rather than doubles --
 * and its result goes to the runtime's own builtin, so the oracle answers. */
static Value *aot_slice_any(Value *A, double sa, double ea) {
    if (!A || A->type == VAL_NUM) {
        rt_error(EK_TYPE, g_trace_current_line, "cannot slice number");
        return make_null();
    }
    int len;
    if (A->type == VAL_LIST)        len = A->data.list.count;
    else if (A->type == VAL_STR)    len = (int)strlen(A->data.str);
    else if (A->type == VAL_BUFFER) len = A->data.buffer.count;
    else {
        rt_error(EK_TYPE, g_trace_current_line, "cannot slice %s", val_type_name(A->type));
        return make_null();
    }
    long si = (long)sa, ei = (long)ea;
    if ((double)si != sa) { rt_error(EK_VALUE, g_trace_current_line, "slice bound must be an integer, got %g", sa); return make_null(); }
    if ((double)ei != ea) { rt_error(EK_VALUE, g_trace_current_line, "slice bound must be an integer, got %g", ea); return make_null(); }
    int orig_start = (int)si, orig_end = (int)ei;
    int start = orig_start, end = orig_end;
    if (start < 0) start += len;
    if (end   < 0) end   += len;
    if (start < 0 || start > len || end < 0 || end > len || start > end) {
        rt_error(EK_INDEX, g_trace_current_line,
                 "slice %d:%d out of range (length %d)", orig_start, orig_end, len);
        return make_null();
    }
    int n = end - start;
    if (A->type == VAL_LIST) {
        Value *r = make_list(n > 0 ? n : 1);
        for (int i = 0; i < n; i++) {
            Value *e = A->data.list.items[start + i];
            val_incref(e);
            list_append_owned(r, e);
        }
        return r;
    }
    if (A->type == VAL_STR) {
        char *buf = (char *)xmalloc((size_t)n + 1);
        if (n > 0) memcpy(buf, A->data.str + start, (size_t)n);
        buf[n] = '\0';
        return make_str_owned(buf);
    }
    Value *r = make_list(n > 0 ? n : 1);
    for (int i = 0; i < n; i++) list_append_owned(r, make_num(A->data.buffer.data[start + i]));
    return r;
}
static inline double aot_range_poly(Env *g, const char *name, Value *A, double sa, double ea) {
    Value *r = aot_call_name(g, name, aot_slice_any(A, sa, ea));
    double d = (r && r->type == VAL_NUM) ? r->data.num : 0.0;
    if (r) val_decref(r);
    return d;
}
static inline double aot_sum_range_v(Env *g, Value *A, double sa, double ea) {
    if (A && A->type == VAL_BUFFER) return aot_sum_range(A, sa, ea);
    return aot_range_poly(g, "sum", A, sa, ea);
}
static inline double aot_norm_range_v(Env *g, Value *A, double sa, double ea) {
    if (A && A->type == VAL_BUFFER) return aot_norm_range(A, sa, ea);
    return aot_range_poly(g, "norm", A, sa, ea);
}
static inline double aot_dot_range_v(Env *g, Value *A, double sa, double ea, Value *B, double sb, double eb) {
    if (A && B && A->type == VAL_BUFFER && B->type == VAL_BUFFER) return aot_dot_range(A, sa, ea, B, sb, eb);
    Value *l = make_list(2);
    list_append_owned(l, aot_slice_any(A, sa, ea));
    list_append_owned(l, aot_slice_any(B, sb, eb));
    Value *r = aot_call_name(g, "dot", l);
    double d = (r && r->type == VAL_NUM) ? r->data.num : 0.0;
    if (r) val_decref(r);
    return d;
}

/* ---- observer system ----
 * An observed numeric variable lives in the env (so it has a tracked slot) and
 * its assignment mirrors the VM's OBSERVE_NAME_POST: store, resolve the slot,
 * update its entropy/dH window, and mark it the global last-observer. Bare
 * predicates (converged/stable/...) read that last-observer's slot — exactly
 * CASE(PREDICATE). Both call the runtime's observer fns, so the VM stays the
 * oracle. (Unboxing is off for observed programs — observation needs the slot.) */
/* Boxed sibling of aot_observe_num: same store + slot update + last-observer,
 * via the runtime's own observer_slot_update, which handles every value type
 * (containers walk entropy exactly as the VM does — it IS the VM's updater).
 * Added when a module-dict call RHS demoted an observed variable off the
 * numeric class: the boxed store recorded nothing, the history stayed empty,
 * and every predicate answered from an empty window (VM 1 / AOT 0, both rc 0).
 * Takes ownership of val, like the set it replaces. */
static void aot_observe_val(Env *e, const char *name, Value *val) {
    /* Hold a ref across the store: env_set_local_owned consumes val, and for a
     * numeric Value slot_from_value collapses it -- reading it afterwards for
     * the observer update was use-after-free. The symptom was subtle, not a
     * crash: trajectories agreed for four samples and split on the fifth (VM
     * `diverging`, AOT `moving`), because the freed box's bytes still LOOKED
     * like a plausible number. */
    val_incref(val);
    env_set_local_owned(e, name, val);
    if (g_unobserved_depth != 0) { val_decref(val); return; }
    int oidx = -1, odepth = 0;
    Env *oe = env_resolve_chain(e, name, env_hash_name(name), &oidx, &odepth);
    if (oe && oidx >= 0) {
        observer_slot_update(oe, oidx, val);
        g_last_obs_slot_env = oe;
        g_last_obs_slot_idx = oidx;
        if (g_trace_obs_hist) {
            const ObserverSlot *os = &oe->obs[oidx];
            trace_record_obs(name, os->entropy, os->dH, os->last_entropy);
        }
    }
    val_decref(val);
}

static void aot_observe_num(Env *e, const char *name, double val) {
    env_set_local_owned(e, name, make_num(val));
    if (g_unobserved_depth != 0) return;
    int oidx = -1, odepth = 0;
    Env *oe = env_resolve_chain(e, name, env_hash_name(name), &oidx, &odepth);
    if (oe && oidx >= 0) {
        observer_slot_update_num(oe, oidx, val);
        g_last_obs_slot_env = oe;
        g_last_obs_slot_idx = oidx;
        /* `where/why/how is x at L`: stamp the slot's entropy/dH onto the most
         * recent trace history entry (created by aot_trace_assign just before
         * this call), exactly like OBSERVE_NAME_POST under g_trace_obs_hist. */
        if (g_trace_obs_hist) {
            const ObserverSlot *os = &oe->obs[oidx];
            trace_record_obs(name, os->entropy, os->dH, os->last_entropy);
        }
    }
}
/* #871, mirrored from the VM's vm_pred_unobserved: a predicate asked inside an
 * `unobserved:` block cannot be answered, and the block's depth is DYNAMIC so
 * it covers callees too. The VM raises; generated code used to return a
 * confident verdict instead (ouroboros#122 — a bare `diverging` inside the
 * block printed 1 where the VM raised and exited 1). rt_error is macro-wrapped
 * here to exit rather than return, so this matches the VM's control flow as
 * well as its message. */
static void aot_pred_unobserved_check(int kind) {
    if (g_unobserved_depth == 0) return;
    rt_error(EK_VALUE, g_trace_current_line,
             "%s: the observer is off inside an 'unobserved:' block, so this "
             "predicate has no trajectory to classify — the block's depth is "
             "dynamic, so it also covers functions called from inside it",
             eigs_predicate_name((unsigned)kind));
}

/* Bare predicate — the last-observed binding's slot.
 *
 * CALLS THE VM's classifier rather than reimplementing it (ouroboros#119/#122).
 * The previous version switched straight onto observer_slot_* and so skipped
 * the #708 opaque band, the #711 query view and the #871 unobserved raise —
 * three documented behaviours the differential never caught, because t27/t28
 * exercise exactly the region where the copy and the original agree.
 * require_used=0 matches the bare opcode, which has never tested `used`. */
static int aot_predicate(int kind) {
    aot_pred_unobserved_check(kind);
    return observer_predicate_at(g_last_obs_slot_env, g_last_obs_slot_idx, kind, 0);
}

/* `<predicate> of <name>` — the NAMED binding's slot (ouroboros#119), mirroring
 * OP_PREDICATE_NAME. This is the only form a multi-channel program can use
 * correctly: bare predicates read the last-observed alias, so a loop assigning
 * several observed variables per iteration can only ever ask about the last.
 * Undefined name raises, exactly like the VM's GET_NAME path. */
static int aot_predicate_of(Env *e, const char *name, int kind, int is_compiled_fn) {
    aot_pred_unobserved_check(kind);
    int oidx = -1, odepth = 0;
    Env *oe = env_resolve_chain(e, name, env_hash_name(name), &oidx, &odepth);
    if (!oe) {
        /* A COMPILED FUNCTION is not env-bound here — the AOT emits it as a C
         * function and only materialises a Value when it is used AS a value —
         * but in the VM the same name IS bound, resolves fine, and answers 0
         * through the #708 opaque band (a function has no content to sample).
         * So an env miss on a known function name is not an undefined name:
         * return the band's answer rather than raising. Without this,
         * `equilibrium of g` printed `undefined variable 'g'` and exited 1
         * against the VM's `fn.equi=0` and exit 0 — a divergence this very
         * change introduced, found by attacking the fix.
         *
         * The env lookup still runs FIRST, so a later `g is 5.0` rebinding
         * wins and is classified as the number it now is, matching the VM. */
        if (is_compiled_fn) return 0;
        rt_error(EK_UNDEFINED_NAME, g_trace_current_line,
                 "undefined variable '%s'", name);
        return 0;
    }
    return observer_predicate_at(oe, oidx, kind, 1);
}
/* `report of x` — the most-specific predicate of x's observed slot, as a string
 * (mirrors CASE(REPORT_NAME)): resolve the binding's slot, classify it, else
 * "equilibrium" for a BOUND-but-unobserved binding. Round 70: the first
 * version conflated two distinct env misses — a NEVER-BOUND name (oe==NULL)
 * fell into the bound-but-unobserved arm and answered "equilibrium" where the
 * VM raises "undefined variable" rc 1 (silent-wrong: clean exit, plausible
 * verdict). The sibling aot_predicate_of ten lines up already had the split;
 * this mirrors it, compiled-fn opaque band included (a compiled function is
 * not env-bound here but IS bound in the VM, whose unobserved slot reports
 * "equilibrium"). */
static Value *aot_report(Env *e, const char *name, int is_compiled_fn) {
    int oidx = -1, odepth = 0;
    Env *oe = env_resolve_chain(e, name, env_hash_name(name), &oidx, &odepth);
    if (!oe) {
        if (is_compiled_fn) return make_str("equilibrium");
        rt_error(EK_UNDEFINED_NAME, g_trace_current_line,
                 "undefined variable '%s'", name);
        return make_str("equilibrium"); /* unreachable */
    }
    if (oidx >= 0 && oidx < oe->obs_cap && oe->obs[oidx].used)
        return make_str(observer_slot_report(&oe->obs[oidx]));
    return make_str("equilibrium");
}
/* ---- temporal interrogatives (the trace tape) ----
 * `prev of x`, `what is x at L`. trace_assign feeds the per-name prev-map +
 * (line, value) history independent of the env (so temporal vars stay unboxed)
 * and independent of any flag, stamping each entry with g_trace_current_line —
 * which the emitter sets per source line, mirroring OP_LINE. Numeric values
 * only for now; an unknown/one-assignment name yields 0 (as the VM's miss). */
/* Boxed sibling of aot_trace_assign. Genuinely borrows val -- which means
 * slot_from_value is EXACTLY the wrong constructor: it TAKES OWNERSHIP,
 * collapsing (freeing) a numeric box. The first version used it anyway, under
 * a comment saying "Borrows val", and the traced+observed boxed store then
 * freed the box one line before aot_observe_val increfed it -- the SAME
 * consume-use-after-free caught in aot_observe_val the round before, rebuilt
 * by the same author in the sibling function the fixture did not reach
 * (t112 has no interrogate, so g_traced was 0 there). Symptom, all silent:
 *
 *   ... prev of a / report of a / diverging of a
 *   VM  130 / diverging / 1        AOT  10 / moving / 0     both rc 0
 *
 * Wrap by hand exactly as vm.c's dot-read does, per its own NOTE: num ->
 * slot_from_num, heap -> slot_from_heap (borrow, no incref), null -> null. */
static void aot_trace_assign_val(const char *name, Value *val) {
    if (!val || val->type == VAL_NULL) { EigsSlot s0 = slot_null(); trace_assign(name, s0); return; }
    if (val->type == VAL_NUM) { trace_assign(name, slot_from_num(val->data.num)); return; }
    trace_assign(name, slot_from_heap(val));
}
static void aot_trace_assign(const char *name, double v) {
    EigsSlot s; s.d = v;
    trace_assign(name, s);
}
/* The query result is polymorphic — a number, a string (`who`), or `null` on a
 * miss — so route the slot through slot_to_value (exactly like the VM's
 * INTERROGATE_NAMED_AT) rather than assuming a double, then drop the slot's ref. */
/* Compile-time-known "undefined variable" raise, in Value* position: the VM's
 * exact error for a module-scope interrogate of a never-module-bound name. */
static Value *aot_undefined_name(const char *name) {
    rt_error(EK_UNDEFINED_NAME, 0, "undefined variable '%s'", name);
    return NULL; /* unreachable */
}
static Value *aot_prev_val(const char *name) {
    /* NO env bound-check here, deliberately -- and one was added and reverted.
     * The VM does require the name bound in scope before the tape is read
     * (`prev of t` of a returned function's local raises "undefined
     * variable"), but an env resolve is the WRONG instrument: traced-only
     * numeric variables live in C doubles, not the env, so the check raised on
     * every unboxed temporal variable and broke t33/t36 at runtime. Boundness
     * is a compile-time fact; the emitter decides it (g_module_names) and
     * emits aot_undefined_name for the unbound case. */
    EigsSlot out;
    if (trace_query_prev(name, &out)) {
        Value *v = slot_to_value(out);
        slot_decref(out);
        return v;
    }
    return make_null();
}
static Value *aot_query_at_val(int kind, const char *name, int line) {
    EigsSlot out;
    if (trace_query_at(kind, name, line, &out)) {
        Value *v = slot_to_value(out);
        slot_decref(out);
        return v;
    }
    return make_null();
}
/* read an observed numeric var back out of the env (consumes the fetched ref) */
static double aot_num(Value *v) {
    double d = (v && v->type == VAL_NUM) ? v->data.num : 0.0;
    val_decref(v);
    return d;
}
/* CHECKED numeric read (consumes): a boxed value flowing into an arithmetic
 * context must be a number — the VM raises there ("cannot apply ... to ..."),
 * so a silent 0.0 would be the worst-outcome class (compiles, prints
 * garbage). Cold, predictable branch: in-envelope programs never take it. */
static double aot_num_ck_at(Value *v, const char *site) {
    if (!v || v->type != VAL_NUM) {
        fprintf(stderr, "non-numeric value in a numeric context at %s (type %s)\n",
                site, v ? val_type_name(v->type) : "null");
        exit(1);
    }
    double d = v->data.num;
    val_decref(v);
    return d;
}

/* Checked unbox that does NOT consume — the boxed-wrapper argument convention
 * borrows __a. Same diagnostic as aot_num_ck_at. */
static double aot_num_ck_bat(Value *v, const char *site) {
    if (!v || v->type != VAL_NUM) {
        fprintf(stderr, "non-numeric value in a numeric context at %s (type %s)\n",
                site, v ? val_type_name(v->type) : "null");
        exit(1);
    }
    return v->data.num;
}

static double aot_num_ck(Value *v) {
    if (!v || v->type != VAL_NUM) {
        fprintf(stderr, "non-numeric value in a numeric context (type %d; the VM raises here)\n",
                v ? (int)v->type : -1);
        exit(1);
    }
    double d = v->data.num;
    val_decref(v);
    return d;
}

/* ---- LOWERED FRAME LOCALS (#132) ------------------------------------------
 * A function's boxed locals used to live in a per-call `Env* __eigs_l`, so
 * every read and write paid a name lookup — 16% of DMG's runtime across
 * aot_get_ic / aot_set_ic / env_set_local_hashed / env_decref even with a
 * per-site inline cache in front of it. The names are private to one call and
 * statically known, so they can simply be C variables.
 *
 * They are lowered to EigsSlot, NOT Value*. That is the whole point: an env
 * slot is NaN-boxed, so a number lives there UNBOXED, and lowering to Value*
 * would reintroduce a malloc/free per numeric assignment — trading a lookup
 * for an allocation and losing. An EigsSlot is a plain 8-byte union that GCC
 * register-allocates, so the representation is preserved exactly and only the
 * lookup disappears.
 *
 * GET_LOCAL semantics are preserved: an unset local is slot_null(), and
 * slot_to_value turns that into a fresh null Value — the same answer aot_get
 * gave for a miss. Lowering is refused for any function carrying a boxed
 * module shadow, because #86's aot_get_sh dispatches on binding PRESENCE in
 * __eigs_l and a C variable has no presence to test. `when is x` reads
 * assign_counts, which a C local does not have — but a `when` qualifier parses
 * to an interrogate node, and env-locals only exist when the body has none
 * (g_traced == 0), so no lowered function can observe it. */
static inline Value *aot_lv_get(EigsSlot *s) {      /* owned; miss -> null */
    return slot_to_value(*s);
}
static inline Value *aot_lv_getb(EigsSlot *s) {     /* borrowed, like env_get */
    if (slot_is_ptr(*s)) return slot_as_ptr(*s);
    Value *v = slot_to_value(*s);
    slot_decref(*s);
    *s = slot_from_heap(v);                          /* the local now owns it */
    return v;
}
static inline void aot_lv_set(EigsSlot *s, Value *val) {   /* adopts val */
    Value *p = promote_if_arena(val);
    if (p == val) val_incref(p);
    EigsSlot ns = slot_from_value(p);
    slot_decref(*s);
    *s = ns;
    val_decref(val);
}
static inline void aot_lv_set_num(EigsSlot *s, double d) {
    slot_decref(*s);
    *s = slot_from_num(num_guard(d));
}
static inline double aot_lv_num(EigsSlot *s, const char *site) {
    if (slot_is_num(*s)) return s->d;
    return aot_num_ck_at(slot_to_value(*s), site);
}


/* ---- numeric env access straight through the NaN-boxed slot (#130) --------
 * An env slot holds a number as an IMMEDIATE double (value_slot.h); only heap
 * types are pointers. The Value-level accessors round-trip through a box in
 * both directions and that round trip is pure waste for a numeric binding:
 *
 *   write  aot_set(e, n, make_num(d))  -> make_num allocates a VAL_NUM,
 *          env_set_local_hashed calls slot_from_value which collapses it back
 *          to an immediate, then the birth ref is dropped. One malloc and one
 *          free per numeric assignment, for a double that never needed a box.
 *   read   env_get_hashed on an immediate MATERIALIZES a heap Value and
 *          writes it back into the slot, so the next write re-collapses it.
 *
 * Measured on DMG after the dict ICs landed: make_num 10.7% + free_value 9.0%
 * of runtime, with env_get_hashed/env_set_local_hashed another 11.7%. These
 * read and write the slot directly: no allocation, no refcount traffic, and
 * no slot mutation on the read side.
 *
 * The hash is cached per site (a static, computed once) so the name is hashed
 * once per site rather than once per access. MISS AND NON-NUMERIC CASES DELEGATE
 * TO THE ORIGINAL EXPRESSION — the error text and exit behaviour are then
 * produced by the identical code that produced them before, which is why the
 * three variants below differ only in which accessor they fall back to
 * (aot_get_named raises on a miss; aot_get answers null and lets aot_num_ck_at
 * die; aot_get_sh walks the shadow chain first). */
static inline double aot_get_num_named_ic(Env *g, const char *name,
                                          AotNameIC *c, const char *site) {
    EigsSlot *sp = aot_name_slot(g, name, c);
    if (sp && slot_is_num(*sp)) return sp->d;
    return aot_num_ck_at(aot_get_named_ic(g, name, c), site);
}
static inline double aot_get_num_local_ic(Env *l, const char *name,
                                          AotNameIC *c, const char *site) {
    EigsSlot *sp = aot_name_slot(l, name, c);
    if (sp && slot_is_num(*sp)) return sp->d;
    return aot_num_ck_at(aot_get_ic(l, name, c), site);
}
static inline double aot_get_num_sh(Env *l, Env *g, const char *name,
                                    uint32_t *hc, const char *site) {
    if (!*hc) *hc = env_hash_name(name);
    int found = 0;
    EigsSlot s = env_get_hashed_slot(l, name, *hc, &found);
    if (found && slot_is_num(s)) return s.d;
    return aot_num_ck_at(aot_get_sh(l, g, name), site);
}

/* Numeric env write. The cached arm writes the slot in place — no box, no
 * lookup — and bumps assign_counts exactly as env_set_local_hashed does
 * (`when is x` reads that counter). Local-only, matching env_set_local_owned:
 * a cache whose home is not `e` itself does not qualify. */
static inline void aot_set_num_ic(Env *e, const char *name,
                                  AotNameIC *c, double d) {
    if (__builtin_expect(c->home != NULL && c->depth == 0
                         && c->idx < e->count && e->names[c->idx] == c->iname
                         && !g_vm_multithreaded, 1)) {
        EigsSlot *sp = &e->values[c->idx];
        slot_decref(*sp);
        *sp = slot_from_num(num_guard(d));
        if (e->assign_counts) e->assign_counts[c->idx]++;
        return;
    }
    if (!c->h) c->h = env_hash_name(name);
    env_set_local_hashed_slot(e, name, c->h, slot_from_num(num_guard(d)));
    int idx = aot_env_hash_find(&e->hash, name, c->h, e->names);
    if (idx >= 0) {
        c->start = e; c->home = e; c->idx = idx; c->depth = 0;
        c->iname = e->names[idx];
        c->sver = e->binding_version; c->tver = c->sver;
    }
}

/* Boxed env write, local-only (env_set_local_owned's contract). The cached arm
 * replicates env_set_local_hashed's replace arm exactly — promote_if_arena,
 * conditional incref, slot_from_value, decref the old — then drops the caller's
 * ref like env_set_local_owned. */
static inline void aot_set_ic(Env *e, const char *name, AotNameIC *c, Value *val) {
    if (__builtin_expect(c->home != NULL && c->depth == 0
                         && c->idx < e->count && e->names[c->idx] == c->iname
                         && !g_vm_multithreaded, 1)) {
        Value *promoted = promote_if_arena(val);
        if (promoted == val) val_incref(promoted);
        EigsSlot ns = slot_from_value(promoted);
        slot_decref(e->values[c->idx]);
        e->values[c->idx] = ns;
        if (e->assign_counts) e->assign_counts[c->idx]++;
        val_decref(val);
        return;
    }
    if (!c->h) c->h = env_hash_name(name);
    env_set_local_hashed(e, name, c->h, val);
    val_decref(val);
    int idx = aot_env_hash_find(&e->hash, name, c->h, e->names);
    if (idx >= 0) {
        c->start = e; c->home = e; c->idx = idx; c->depth = 0;
        c->iname = e->names[idx];
        c->sver = e->binding_version; c->tver = c->sver;
    }
}

/* plain (non-`local`) numeric write to a shadow name — aot_set_sh's dispatch. */
static inline void aot_set_num_sh(Env *l, Env *g, const char *name,
                                  uint32_t *hc, double d) {
    if (!*hc) *hc = env_hash_name(name);
    Env *home = env_get(l, name) ? l : g;
    env_set_local_hashed_slot(home, name, *hc, slot_from_num(num_guard(d)));
}


/* Unary minus on a boxed value — the VM's OP_NEG exactly: negate a number
 * (no guard: negation preserves the finite invariant), raise EK_TYPE
 * "cannot negate non-numeric" on anything else. The old emission coerced
 * through aot_num, so `-s` on a string printed 0 where the VM stops
 * (#100 item 5). Consumes v, returns owned. */
static Value *aot_neg(Value *v) {
    if (!v || v->type != VAL_NUM)
        rt_error(EK_TYPE, 0, "cannot negate non-numeric");
    double d = v->data.num;
    val_decref(v);
    return make_num(-d);
}

/* ---- tensor handles -------------------------------------------------------
 * Flat row-major double buffers that bridge the runtime's nested-list tensor
 * Values. The runtime's own tensor builtins (builtins_tensor.c) already flatten
 * to double[], compute, and rebuild nested lists; we run the SAME kernels on the
 * flat form WITHOUT the per-call flatten/rebuild churn. Byte-exact vs the VM:
 *   - matmul: raw i-k-j accumulation, NO num_guard (matches ne_matmul_buf)
 *   - add:    elementwise num_guard(a+b)            (matches op_add elementwise)
 *   - relu:   clamp negatives to 0                  (matches builtin_tensor_relu)
 * A per-call frame POOL owns every produced tensor; aot_tframe_leave frees them
 * once the return Value is built — no per-op free threading in generated C.
 * rows/cols are the EFFECTIVE dims (rows>=1); is1d marks a vector (serialized
 * 1-D). kind: 0=list-backed, 1=buffer-backed (the result Value type mirrors the
 * input, so a buffer program returns a buffer byte-for-byte like the VM). owns:
 * 1 => data is heap-allocated and pool-freed; 0 => a ZERO-COPY VIEW borrowed
 * from a shaped VAL_BUFFER (no flatten, no copy — the Phase-2 win). */
typedef struct { double *data; long rows; long cols; int is1d; int kind; int owns; } AotTensor;

static double **g_tpool = NULL;
static long g_tpool_n = 0, g_tpool_cap = 0;

static AotTensor aot_tensor_null(void) { AotTensor t; t.data=NULL; t.rows=0; t.cols=0; t.is1d=0; t.kind=0; t.owns=0; return t; }
static long aot_tframe_enter(void) { return g_tpool_n; }
static AotTensor aot_treg(AotTensor t) {       /* pool-own t.data IFF owned (views are borrowed) */
    if (t.owns && t.data) {
        if (g_tpool_n == g_tpool_cap) {
            g_tpool_cap = g_tpool_cap ? g_tpool_cap * 2 : 64;
            g_tpool = (double**)realloc(g_tpool, (size_t)g_tpool_cap * sizeof(double*));
        }
        g_tpool[g_tpool_n++] = t.data;
    }
    return t;
}
static void aot_tframe_leave(long mark) { while (g_tpool_n > mark) free(g_tpool[--g_tpool_n]); }

/* A tensor Value -> AotTensor. A shaped VAL_BUFFER becomes a ZERO-COPY VIEW
 * (data borrowed; rows==0 buffer => 1-D row vector). A nested list is flattened
 * into an owned copy (byte-exact vs tensor_to_flat; transitional path). */
static AotTensor aot_tensor_from_value(Value *v) {
    AotTensor t = aot_tensor_null();
    if (!v) return t;
    if (v->type == VAL_BUFFER) {
        t.data = v->data.buffer.data;   /* borrowed: no copy */
        t.kind = 1; t.owns = 0;
        if (v->data.buffer.rows > 0) { t.rows = v->data.buffer.rows; t.cols = v->data.buffer.cols; t.is1d = 0; }
        else { t.rows = 1; t.cols = v->data.buffer.count; t.is1d = 1; }
        return t;
    }
    if (v->type == VAL_LIST && v->data.list.count > 0) {
        Value *first = v->data.list.items[0];
        t.kind = 0; t.owns = 1;
        if (first->type == VAL_NUM) {
            t.rows = 1; t.cols = v->data.list.count; t.is1d = 1;
            t.data = (double*)calloc((size_t)t.cols, sizeof(double));
            for (long i = 0; i < t.cols; i++) {
                Value *e = v->data.list.items[i];
                t.data[i] = (e->type == VAL_NUM) ? e->data.num : 0.0;
            }
        } else if (first->type == VAL_LIST) {
            t.rows = v->data.list.count; t.cols = first->data.list.count; t.is1d = 0;
            t.data = (double*)calloc((size_t)(t.rows * t.cols), sizeof(double));
            for (long r = 0; r < t.rows; r++) {
                Value *row = v->data.list.items[r];
                long rc = (row->type == VAL_LIST) ? row->data.list.count : 0;
                for (long c = 0; c < t.cols && c < rc; c++) {
                    Value *e = row->data.list.items[c];
                    t.data[r * t.cols + c] = (e->type == VAL_NUM) ? e->data.num : 0.0;
                }
            }
        }
    }
    return t;
}

/* a tensor field of a dict (policy.w1) -> view (buffer) or copy (list). */
static AotTensor aot_tensor_field(Value *dict, const char *key) {
    Value *f = dict ? dict_get(dict, key) : NULL;
    return aot_tensor_from_value(f);
}

/* build an owned shaped VAL_BUFFER (copies src), matching the VM's make_buffer. */
static Value *aot_make_buffer(int count, int rows, int cols, const double *src) {
    Value *v = (Value*)calloc(1, sizeof(Value));
    v->type = VAL_BUFFER;
    v->data.buffer.count = count;
    v->data.buffer.rows = rows;
    v->data.buffer.cols = cols;
    v->data.buffer.data = (double*)calloc(count > 0 ? (size_t)count : 1, sizeof(double));
    if (src && count > 0) memcpy(v->data.buffer.data, src, (size_t)count * sizeof(double));
    v->refcount = 1;
    return v;
}

/* AotTensor -> Value, mirroring the input representation byte-for-byte:
 *   buffer-backed -> shaped VAL_BUFFER (is1d => rows=0 1-D, else 2-D)
 *   list-backed   -> nested list       (is1d => 1-D list, else 2-D) */
static Value *aot_tensor_to_value(AotTensor t) {
    if (t.kind == 1) {
        if (t.is1d) return aot_make_buffer((int)t.cols, 0, 0, t.data);
        return aot_make_buffer((int)(t.rows * t.cols), (int)t.rows, (int)t.cols, t.data);
    }
    if (t.is1d) {
        Value *out = make_list(t.cols);
        for (long i = 0; i < t.cols; i++) list_append_owned(out, make_num(t.data[i]));
        return out;
    }
    Value *outer = make_list(t.rows);
    for (long r = 0; r < t.rows; r++) {
        Value *row = make_list(t.cols);
        for (long c = 0; c < t.cols; c++) list_append_owned(row, make_num(t.data[r * t.cols + c]));
        list_append_owned(outer, row);
    }
    return outer;
}

static AotTensor aot_tensor_matmul(AotTensor a, AotTensor b) {
    AotTensor o = aot_tensor_null();
    /* Shape mismatch: the VM raises EK_VALUE (#512 upstream — builtins_tensor.c
     * builtin_tensor_matmul), same message and dims (a 1-D operand reports as
     * 1xN on both tiers). The old silent null tensor serialized to `[]` and the
     * program ran on (#100 item 8). */
    if (a.cols != b.rows)
        rt_error(EK_VALUE, 0, "matmul: incompatible shapes (%ldx%ld · %ldx%ld)",
                 a.rows, a.cols, b.rows, b.cols);
    o.rows = a.rows; o.cols = b.cols;
    o.is1d = a.is1d;                           /* 1-D result iff the left operand is a vector */
    o.kind = a.kind; o.owns = 1;
    long ac = a.cols, bc = b.cols;
    o.data = (double*)calloc((size_t)(o.rows * bc), sizeof(double));
    /* i-k-j with j (the output column) innermost: o[i, :] += a[i,k] * b[k, :] is a
     * CONTIGUOUS axpy over j that the compiler auto-vectorizes with perfect cache
     * locality (b swept row-by-row, linearly). Raw, no guard — matches
     * ne_matmul_buf byte-for-byte; -ffp-contract=off keeps the mul+add unfused.
     * A hand-rolled output-axis SIMD (k innermost) instead STRIDES b by bc and
     * measured SLOWER — the contiguous form already wins. */
    for (long i = 0; i < a.rows; i++) {
        const double *arow = a.data + i * ac;
        double *orow = o.data + i * bc;
        for (long k = 0; k < ac; k++) {
            double aik = arow[k];
            const double *brow = b.data + k * bc;
            for (long j = 0; j < bc; j++) orow[j] += aik * brow[j];
        }
    }
    return o;
}

/* Elementwise add, mirroring the VM's `add` (builtins_tensor.c) on the shapes
 * this kernel supports; the old version blindly read b.data over a's count —
 * an OOB heap read when b is shorter (#100 item 8):
 *   - buffer/buffer: the VM's fast path keys on EQUAL COUNT (shape ignored)
 *     -> elementwise, result shaped like a. Unequal counts fall through the
 *     VM's tensor_elementwise to a scalar 0.0 — unrepresentable here -> loud.
 *   - list/list: equal dims -> elementwise; both 1-D -> the VM's min-count
 *     elementwise (list-op-list truncates to the shorter); 2-D mismatches
 *     broadcast in the VM (matrix±vector) — not implemented -> loud.
 *   - mixed buffer/list: the VM's tensor_elementwise handles only NUM/LIST,
 *     so a buffer operand falls through to scalar 0.0 — unrepresentable
 *     here -> loud. */
static AotTensor aot_tensor_add(AotTensor a, AotTensor b) {
    AotTensor o = a;                           /* inherit rows/cols/is1d/kind */
    o.owns = 1;
    long n = -1;
    if (a.rows == b.rows && a.cols == b.cols && a.kind == b.kind) {
        n = a.rows * a.cols;
    } else if (a.kind == 1 && b.kind == 1 &&
               a.rows * a.cols == b.rows * b.cols) {
        n = a.rows * a.cols;                   /* VM fast path: count, not shape */
    } else if (a.kind == 0 && b.kind == 0 && a.is1d && b.is1d) {
        n = a.cols < b.cols ? a.cols : b.cols; /* VM list-op-list min-count */
        o.cols = n;
    }
    if (n < 0) {
        fprintf(stderr, "aot: tensor add on mismatched shapes "
                "(%ldx%ld vs %ldx%ld) — the VM broadcasts/coerces here; "
                "not supported by the AOT tensor kernel\n",
                a.rows, a.cols, b.rows, b.cols);
        exit(1);
    }
    o.data = (double*)calloc(n > 0 ? (size_t)n : 1, sizeof(double));
    for (long i = 0; i < n; i++) o.data[i] = num_guard(a.data[i] + b.data[i]);
    return o;
}

static AotTensor aot_tensor_relu(AotTensor a) {
    AotTensor o = a;
    o.owns = 1;
    long n = a.rows * a.cols;
    o.data = (double*)calloc((size_t)n, sizeof(double));
    for (long i = 0; i < n; i++) { double x = a.data[i]; o.data[i] = (x < 0.0) ? 0.0 : x; }
    return o;
}

/* ---- call a global/builtin by name, single arg (consumes arg) ---- */
/* ---- value-context indexing: target[idx] ----------------------------------
 * Mirrors vm.c jit_helper_index_get's slow path byte-for-byte: list/dict/string/
 * buffer, integer check, negative resolution then bounds (vm_index_resolve).
 * Consumes both operands; returns an owned result (null on a dict miss). The
 * error line is 0 (the AOT doesn't track VM line numbers), but a valid index —
 * the only path the differential harness exercises — is byte-exact. */
static int aot_idx_is_int(double d, int *out) { int i = (int)d; if ((double)i != d) return 0; *out = i; return 1; }
static int aot_idx_resolve(int *i, int len) { int r = (*i < 0) ? *i + len : *i; if (r < 0 || r >= len) return 0; *i = r; return 1; }

/* Target-borrowed integer index read/write (#130): `mem.data[addr]` is the
 * emulator's single commonest expression, and the container it indexes is a
 * dict slot that outlives the expression. Borrowing it removes the last
 * refcount pair on that path. Result ownership is unchanged. */
static __attribute__((noinline)) Value *aot_index_get_ib_slow(Value *target, double d) {
    Value *result = NULL;
    int i;
    if (target && target->type == VAL_LIST) {
        if (!aot_idx_is_int(d, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
        else if (aot_idx_resolve(&i, target->data.list.count)) {
            result = target->data.list.items[i]; val_incref(result);
        } else
            rt_error(EK_INDEX, 0, "index %d out of range (list length %d)", i, target->data.list.count);
    } else if (target && target->type == VAL_STR) {
        if (!aot_idx_is_int(d, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
        else if (aot_idx_resolve(&i, (int)strlen(target->data.str))) {
            char b[2] = { target->data.str[i], 0 }; result = make_str(b);
        } else
            rt_error(EK_INDEX, 0, "string index %d out of range (length %d)", i, (int)strlen(target->data.str));
    } else if (target && target->type == VAL_BUFFER) {
        if (!aot_idx_is_int(d, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
        else if (aot_idx_resolve(&i, target->data.buffer.count))
            result = make_num(target->data.buffer.data[i]);
        else
            rt_error(EK_INDEX, 0, "buffer index %d out of range (length %d)", i, target->data.buffer.count);
    } else {
        rt_error(EK_TYPE, 0, "cannot index %s",
                 target ? val_type_name(target->type) : "null");
    }
    return result ? result : make_null();
}

/* Numeric element read straight to a double: no box for the index, and none
 * for the element when the list already holds a VAL_NUM. */
static double aot_index_num_ib(Value *target, double d, const char *site) {
    Value *v = aot_index_get_ib(target, d);
    if (v && v->type == VAL_NUM) { double r = v->data.num; val_decref(v); return r; }
    return aot_num_ck_at(v, site);
}

static void aot_index_set_ib(Value *target, double d, Value *val) {
    int i;
    if (target && target->type == VAL_LIST) {
        if (!aot_idx_is_int(d, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
        else if (aot_idx_resolve(&i, target->data.list.count)) {
            Value *old = target->data.list.items[i];
            target->data.list.items[i] = val; val = NULL;   /* adopt */
            if (old) val_decref(old);
        } else
            rt_error(EK_INDEX, 0, "index %d out of range (list length %d)", i, target->data.list.count);
    } else if (target && target->type == VAL_BUFFER) {
        if (val && val->type == VAL_NUM) {
            if (!aot_idx_is_int(d, &i))
                rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
            else if (aot_idx_resolve(&i, target->data.buffer.count))
                target->data.buffer.data[i] = val->data.num;
            else
                rt_error(EK_INDEX, 0, "buffer index %d out of range (length %d)", i, target->data.buffer.count);
        } else
            rt_error(EK_TYPE, 0, "buffer elements must be numbers");
    } else {
        rt_error(EK_TYPE, 0, "cannot index-assign into %s",
                 target ? val_type_name(target->type) : "null");
    }
    if (val) val_decref(val);
}

/* Integer-index read (#130). Identical to aot_index_get with a VAL_NUM index
 * that is already known integral, minus the box: 474 sites in DMG's generated
 * C read `aot_index_get(x, make_num(<literal>))`, each allocating and freeing
 * a VAL_NUM purely to carry an index. The `d` argument is the same double the
 * boxed form would have carried, so the non-integral and out-of-range
 * diagnostics stay byte-identical. */
static Value *aot_index_get_i(Value *target, double d) {
    Value *result = NULL;
    int i;
    if (target->type == VAL_LIST) {
        if (!aot_idx_is_int(d, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
        else if (aot_idx_resolve(&i, target->data.list.count)) {
            result = target->data.list.items[i]; val_incref(result);
        } else
            rt_error(EK_INDEX, 0, "index %d out of range (list length %d)", i, target->data.list.count);
    } else if (target->type == VAL_STR) {
        if (!aot_idx_is_int(d, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
        else if (aot_idx_resolve(&i, (int)strlen(target->data.str))) {
            char b[2] = { target->data.str[i], 0 }; result = make_str(b);
        } else
            rt_error(EK_INDEX, 0, "string index %d out of range (length %d)", i, (int)strlen(target->data.str));
    } else if (target->type == VAL_BUFFER) {
        if (!aot_idx_is_int(d, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
        else if (aot_idx_resolve(&i, target->data.buffer.count))
            result = make_num(target->data.buffer.data[i]);
        else
            rt_error(EK_INDEX, 0, "buffer index %d out of range (length %d)", i, target->data.buffer.count);
    } else {
        /* A VAL_DICT with a numeric index falls here in the oracle too: its
         * dict arm requires VAL_STR, so the final else raises. */
        rt_error(EK_TYPE, 0, "cannot index %s", val_type_name(target->type));
    }
    val_decref(target);
    return result ? result : make_null();
}

static Value *aot_index_get(Value *target, Value *idx) {
    Value *result = NULL;
    if (target->type == VAL_LIST && idx->type == VAL_NUM) {
        int i;
        if (!aot_idx_is_int(idx->data.num, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", idx->data.num);
        else if (aot_idx_resolve(&i, target->data.list.count)) {
            result = target->data.list.items[i]; val_incref(result);
        } else
            rt_error(EK_INDEX, 0, "index %d out of range (list length %d)", i, target->data.list.count);
    } else if (target->type == VAL_DICT && idx->type == VAL_STR) {
        Value *v = dict_get(target, idx->data.str);
        if (v) { result = v; val_incref(result); }
    } else if (target->type == VAL_STR && idx->type == VAL_NUM) {
        int i;
        if (!aot_idx_is_int(idx->data.num, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", idx->data.num);
        else if (aot_idx_resolve(&i, (int)strlen(target->data.str))) {
            char b[2] = { target->data.str[i], 0 }; result = make_str(b);
        } else
            rt_error(EK_INDEX, 0, "string index %d out of range (length %d)", i, (int)strlen(target->data.str));
    } else if (target->type == VAL_BUFFER && idx->type == VAL_NUM) {
        int i;
        if (!aot_idx_is_int(idx->data.num, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", idx->data.num);
        else if (aot_idx_resolve(&i, target->data.buffer.count))
            result = make_num(target->data.buffer.data[i]);
        else
            rt_error(EK_INDEX, 0, "buffer index %d out of range (length %d)", i, target->data.buffer.count);
    } else {
        /* The oracle's final else (vm.c ~2018). Missing here, an unindexable
         * target — `null["k"]` above all, the index-side twin of the #898 dot
         * read — answered null instead of raising. */
        rt_error(EK_TYPE, 0, "cannot index %s", val_type_name(target->type));
    }
    val_decref(target);
    val_decref(idx);
    return result ? result : make_null();
}

/* (#86) native argv -> the VM's args convention: builtin_args reads
 * g_argv[2..] (slot 0 = runtime, 1 = script). The native binary's user
 * args start at argv[1], so shift by one synthetic slot. */
static void aot_args(int argc, char **argv) {
    char **shifted = (char**)malloc(((size_t)argc + 1) * sizeof(char*));
    shifted[0] = argv[0];
    shifted[1] = argv[0];
    for (int i = 1; i < argc; i++) shifted[i + 1] = argv[i];
    eigenscript_set_args(argc + 1, shifted);
}

/* general index-assign (#86): `target[idx] is val` on a boxed value.
 * Mirrors OP_INDEX_SET: list (integer index, in range -> replace, old ref
 * dropped), dict (string key -> set), buffer (numeric value). Consumes
 * target and idx; adopts val. */
/* Integer-index write (#130) — aot_index_set with the index already a known
 * double, minus the box. The twin of aot_index_get_i; the diagnostics are
 * byte-identical because `d` is the same double the box would have carried.
 * A VAL_DICT target falls to the final raise here exactly as it does there,
 * since the dict arm requires a VAL_STR index. */
static void aot_index_set_i(Value *target, double d, Value *val) {
    int i;
    if (target && target->type == VAL_LIST) {
        if (!aot_idx_is_int(d, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
        else if (aot_idx_resolve(&i, target->data.list.count)) {
            Value *old = target->data.list.items[i];
            target->data.list.items[i] = val; val = NULL;   /* adopt */
            if (old) val_decref(old);
        } else
            rt_error(EK_INDEX, 0, "index %d out of range (list length %d)", i, target->data.list.count);
    } else if (target && target->type == VAL_BUFFER) {
        if (val && val->type == VAL_NUM) {
            if (!aot_idx_is_int(d, &i))
                rt_error(EK_VALUE, 0, "index must be an integer, got %g", d);
            else if (aot_idx_resolve(&i, target->data.buffer.count))
                target->data.buffer.data[i] = val->data.num;
            else
                rt_error(EK_INDEX, 0, "buffer index %d out of range (length %d)", i, target->data.buffer.count);
        } else
            rt_error(EK_TYPE, 0, "buffer elements must be numbers");
    } else {
        rt_error(EK_TYPE, 0, "cannot index-assign into %s",
                 target ? val_type_name(target->type) : "null");
    }
    if (val) val_decref(val);
    if (target) val_decref(target);
}

static void aot_index_set(Value *target, Value *idx, Value *val) {
    if (target && target->type == VAL_LIST && idx && idx->type == VAL_NUM) {
        int i;
        if (!aot_idx_is_int(idx->data.num, &i))
            rt_error(EK_VALUE, 0, "index must be an integer, got %g", idx->data.num);
        else if (aot_idx_resolve(&i, target->data.list.count)) {
            Value *old = target->data.list.items[i];
            target->data.list.items[i] = val; val = NULL;   /* adopt */
            if (old) val_decref(old);
        } else
            rt_error(EK_INDEX, 0, "index %d out of range (list length %d)", i, target->data.list.count);
    } else if (target && target->type == VAL_DICT && idx && idx->type == VAL_STR) {
        dict_set_owned(target, idx->data.str, val); val = NULL;
    } else if (target && target->type == VAL_BUFFER && idx && idx->type == VAL_NUM) {
        if (val && val->type == VAL_NUM) {
            int i;
            if (!aot_idx_is_int(idx->data.num, &i))
                rt_error(EK_VALUE, 0, "index must be an integer, got %g", idx->data.num);
            else if (aot_idx_resolve(&i, target->data.buffer.count))
                target->data.buffer.data[i] = val->data.num;
            else
                rt_error(EK_INDEX, 0, "buffer index %d out of range (length %d)", i, target->data.buffer.count);
        } else
            rt_error(EK_TYPE, 0, "buffer elements must be numbers");
    } else {
        rt_error(EK_TYPE, 0, "cannot index-assign into %s",
                 target ? val_type_name(target->type) : "null");
    }
    if (val) val_decref(val);
    if (target) val_decref(target);
    if (idx) val_decref(idx);
}

/* ---- dot field access (#86) — mirrors the VM's field opcodes exactly ----
 * READ (vm.c ~1178): dict -> the field's value (missing -> null); ANY other
 * type, null included, raises EK_TYPE "cannot access field". The null
 * exemption was removed upstream by EigenScript #898 ("null is not a dict")
 * — optional-chaining through a miss is now an error rather than a quiet
 * null, so the AOT must raise where it used to answer. SET (vm.c ~1285):
 * dict -> set; a NULL-typed target is STILL a silent no-op (#898 changed the
 * read only — do not "symmetrize" this, it would diverge from the oracle);
 * any other type raises EK_TYPE "cannot set field". Both consume the owned
 * target (emit_val results are owned, same convention as aot_index_get);
 * dot_get returns owned. */
static Value *aot_dot_get(Value *target, const char *key) {
    Value *result = NULL;
    if (target && target->type == VAL_DICT) {
        Value *v = dict_get(target, key);
        if (v) { result = v; val_incref(v); }
    } else if (target) {
        rt_error(EK_TYPE, 0, "cannot access field '%s' on %s",
                 key, val_type_name(target->type));
    }
    if (target) val_decref(target);
    return result ? result : make_null();
}

/* MONOMORPHIC INLINE CACHE for a dict field read (ouroboros#130).
 *
 * Profiled on DMG's emulation loop: dict_get 14.9% and
 * __strcmp_sse2_unaligned 11.8%, against 3.5% for the emulation itself.
 * Two cheaper ideas were measured and BOTH did nothing — fusing the
 * read+unbox (1.025x) and pre-hashing the key (0.998x) — because the cost
 * is neither refcounting nor computing the hash: it is the bucket walk and
 * the key strcmp inside the lookup.
 *
 * So cache per SITE. A site like `cpu.pc` sees the same dict object every
 * time, so the slot index and the dict's own key POINTER are stable; a hit
 * is a bounds check plus a pointer compare plus an array index, with no
 * hash and no strcmp. A miss (different shape, grown dict, rehash) falls
 * back to the normal lookup and re-arms. Correct for any dict because the
 * guard validates the cached slot still holds that exact key pointer. */
/* Non-static in eigenscript.c but absent from eigenscript.h (EigenScript#1055);
 * declared here so the IC helpers below are not implicitly declared. */
int env_hash_find_dict(Value *dict, const char *key, uint32_t h);

/* Resolve a field to its slot index through the per-site cache, re-arming on
 * a miss. Returns -1 if the dict has no such key. Shared by all three ICs. */
static inline int aot_ic_slot(Value *target, const char *key,
                              int *ic, const char **ick) {
    int i = *ic;
    if (i >= 0 && i < target->data.dict.count &&
        target->data.dict.keys[i] == *ick) return i;
    i = env_hash_find_dict(target, key, env_hash_name(key));
    if (i >= 0) { *ic = i; *ick = target->data.dict.keys[i]; }
    return i;
}

static inline Value *aot_dot_borrow_ic(Value *target, const char *key,
                                       int *ic, const char **ick) {
    if (target && target->type == VAL_DICT) {
        int i = aot_ic_slot(target, key, ic, ick);
        return (i >= 0) ? target->data.dict.vals[i] : NULL;
    }
    if (target)
        rt_error(EK_TYPE, 0, "cannot access field '%s' on %s",
                 key, val_type_name(target->type));
    return NULL;   /* null target reads null, silently — the #898 contract */
}

/* Target-borrowed variants (#130). When the target expression is a plain C
 * Value* variable it is live for the whole statement, so the incref/decref
 * pair the consuming forms require is pure bookkeeping — measured at 11.7% of
 * runtime across val_incref/val_decref once the boxes were gone. These do not
 * consume the target; the RESULT keeps the same ownership as the form each
 * mirrors, so only the emitter's target expression changes. */
static __attribute__((noinline)) Value *aot_dot_get_tb_slow(Value *target, const char *key,
                                                           int *ic, const char **ick) {
    if (target && target->type == VAL_DICT) {
        int i = aot_ic_slot(target, key, ic, ick);
        Value *v = (i >= 0) ? target->data.dict.vals[i] : NULL;
        if (v) { val_incref(v); return v; }
        return make_null();
    }
    if (target)
        rt_error(EK_TYPE, 0, "cannot access field '%s' on %s",
                 key, val_type_name(target->type));
    return make_null();
}

static __attribute__((noinline)) double aot_dot_num_tb_slow(Value *target, const char *key,
                                                           int *ic, const char **ick, const char *site) {
    if (target && target->type == VAL_DICT) {
        int i = aot_ic_slot(target, key, ic, ick);
        Value *v = (i >= 0) ? target->data.dict.vals[i] : NULL;
        if (v && v->type == VAL_NUM) return v->data.num;
        fprintf(stderr, "non-numeric value in a numeric context at %s (type %s)\n",
                site, v ? val_type_name(v->type) : "null");
        exit(1);
    }
    if (target)
        rt_error(EK_TYPE, 0, "cannot access field '%s' on %s",
                 key, val_type_name(target->type));
    fprintf(stderr, "non-numeric value in a numeric context at %s (null)\n", site);
    exit(1);
}

static __attribute__((noinline)) void aot_dot_set_num_tb_slow(Value *target, const char *key, double d,
                                                             int *ic, const char **ick) {
    d = num_guard(d);
    if (target && target->type == VAL_DICT) {
        int i = aot_ic_slot(target, key, ic, ick);
        if (i >= 0) {
            Value *old = target->data.dict.vals[i];
            if (old && old->type == VAL_NUM && old->refcount == 1) {
                old->data.num = d;
            } else {
                Value *nv = promote_if_arena(make_num(d));
                val_decref(old);
                target->data.dict.vals[i] = nv;
            }
        } else {
            dict_set_owned(target, key, make_num(d));
        }
    } else if (target && target->type != VAL_NULL) {
        rt_error(EK_TYPE, 0, "cannot set field '%s' on %s",
                 key, val_type_name(target->type));
    }
}

static double aot_dot_num_ic(Value *target, const char *key,
                             int *ic, const char **ick, const char *site) {
    if (target && target->type == VAL_DICT) {
        int i = aot_ic_slot(target, key, ic, ick);
        Value *v = (i >= 0) ? target->data.dict.vals[i] : NULL;
        if (v && v->type == VAL_NUM) {
            double d = v->data.num;
            val_decref(target);
            return d;
        }
        fprintf(stderr, "non-numeric value in a numeric context at %s (type %s)\n",
                site, v ? val_type_name(v->type) : "null");
        exit(1);
    }
    if (target) {
        rt_error(EK_TYPE, 0, "cannot access field '%s' on %s",
                 key, val_type_name(target->type));
    }
    fprintf(stderr, "non-numeric value in a numeric context at %s (null)\n", site);
    exit(1);
}

/* IC'd generic field read — same contract as aot_dot_get (consumes the owned
 * target, returns owned). The numeric IC above only covers sites the numeric
 * inference already proved numeric; measured on DMG that was 0.9 reads per
 * emulated cycle against a dict_get still holding 10.8% of runtime, i.e. most
 * field traffic is BOXED and never saw the cache. This covers the rest. */
static Value *aot_dot_get_ic(Value *target, const char *key,
                             int *ic, const char **ick) {
    if (target && target->type == VAL_DICT) {
        int i = aot_ic_slot(target, key, ic, ick);
        Value *v = (i >= 0) ? target->data.dict.vals[i] : NULL;
        if (v) val_incref(v);
        val_decref(target);
        return v ? v : make_null();
    }
    if (target) {
        rt_error(EK_TYPE, 0, "cannot access field '%s' on %s",
                 key, val_type_name(target->type));
        val_decref(target);
    }
    return make_null();
}

/* IC'd NUMERIC field write. The rhs arrives as a C double, so the common
 * case — the slot already holds a sole-owned VAL_NUM — allocates nothing and
 * frees nothing: the existing box is reused. That is the point. `ctx.pc is
 * ctx.pc + 1` in DMG's dispatch was a make_num plus a free_value per write
 * (8.5% + 7.1% of runtime between them) on top of the lookup.
 *
 * Reuse is gated on refcount == 1, so no other holder can observe the box
 * change identity-for-value: anything that read the field through
 * aot_dot_get(_ic) increfs, which forces the replacing path instead. Slots
 * holding a shared or non-numeric value, and keys not present at all, fall
 * back to semantics identical to aot_dot_set. */
static void aot_dot_set_num_ic(Value *target, const char *key, double d,
                               int *ic, const char **ick) {
    d = num_guard(d);   /* make_num guards; this path must not depend on its
                         * caller having done so (emit_num does, today). */
    if (target && target->type == VAL_DICT) {
        int i = aot_ic_slot(target, key, ic, ick);
        if (i >= 0) {
            Value *old = target->data.dict.vals[i];
            if (old && old->type == VAL_NUM && old->refcount == 1) {
                old->data.num = d;
            } else {
                Value *nv = promote_if_arena(make_num(d));
                val_decref(old);
                target->data.dict.vals[i] = nv;
            }
        } else {
            dict_set_owned(target, key, make_num(d));
        }
    } else if (target && target->type != VAL_NULL) {
        rt_error(EK_TYPE, 0, "cannot set field '%s' on %s",
                 key, val_type_name(target->type));
    }
    if (target) val_decref(target);
}

/* IC'd generic field write — mirrors dict_set_hashed's replace arm exactly
 * (promote_if_arena, then decref-old/adopt) and then the owned-argument
 * convention of dict_set_owned. */
static void aot_dot_set_ic(Value *target, const char *key, Value *val,
                           int *ic, const char **ick) {
    if (target && target->type == VAL_DICT) {
        int i = aot_ic_slot(target, key, ic, ick);
        if (i >= 0) {
            Value *promoted = promote_if_arena(val);
            if (promoted != val) {
                val_decref(target->data.dict.vals[i]);
                target->data.dict.vals[i] = promoted;
            } else {
                val_incref(val);
                val_decref(target->data.dict.vals[i]);
                target->data.dict.vals[i] = val;
            }
            val_decref(val);
        } else {
            dict_set_owned(target, key, val);
        }
    } else {
        if (target && target->type != VAL_NULL)
            rt_error(EK_TYPE, 0, "cannot set field '%s' on %s",
                     key, val_type_name(target->type));
        if (val) val_decref(val);
    }
    if (target) val_decref(target);
}

static void aot_dot_set(Value *target, const char *key, Value *val) {
    if (target && target->type == VAL_DICT) {
        dict_set_owned(target, key, val);   /* adopts val's ref */
    } else {
        if (target && target->type != VAL_NULL)
            rt_error(EK_TYPE, 0, "cannot set field '%s' on %s",
                     key, val_type_name(target->type));
        if (val) val_decref(val);
    }
    if (target) val_decref(target);
}

/* ---- for-loop iteration over a list/buffer (range materializes to a list) ---
 * Round 71: the first version conflated "not iterable" with "iterable of
 * length 0" — a `for` over a runtime number/string/null/dict silently ran
 * zero iterations and execution continued (VM: "'for' requires a list or
 * buffer, got num", rc 1; AOT printed the untouched accumulator, rc 0 — a
 * silent wrong number). The iterable's kind is a runtime fact (`xs[1]`),
 * so no static refusal can stand in for this check. */
static long aot_iter_len(Value *v) {
    if (v) {
        if (v->type == VAL_LIST)   return v->data.list.count;
        if (v->type == VAL_BUFFER) return v->data.buffer.count;
    }
    rt_error(EK_TYPE, g_trace_current_line,
             "'for' requires a list or buffer, got %s",
             v ? val_type_name(v->type) : "null");
    return 0; /* unreachable */
}
static Value *aot_iter_get(Value *v, long k) {   /* owned element k */
    if (v->type == VAL_LIST)   { Value *e = v->data.list.items[k]; val_incref(e); return e; }
    if (v->type == VAL_BUFFER) return make_num(v->data.buffer.data[k]);
    rt_error(EK_TYPE, g_trace_current_line,
             "'for' requires a list or buffer, got %s", val_type_name(v->type));
    return make_null(); /* unreachable */
}

/* Same as aot_call_name with the callee resolution cached per site. The AOT
 * already refuses fn-body writes to builtin names (F-OURO-31/32), and the
 * version guard covers rebinding of user functions, so the cached callee
 * cannot go stale unnoticed. */
static Value *aot_call_name_ic(Env *g, const char *name, Value *arg, AotNameIC *c);

/* `dispatch of [table, key, ctx]` without heap-allocating the argument vector
 * (#130). Measured on DMG: builtin_dispatch is 27.7% of total runtime with
 * children, and every emulated instruction paid a make_list(3) plus a
 * make_num for the key before any emulation happened.
 *
 * builtin_dispatch reads items[0..2], validates, and calls the handler with
 * items[2] ALONE — the vector never escapes it — so the vector can live on
 * the stack. Both stack Values carry arena=1, which is the runtime's existing
 * "not refcount-managed" flag: val_incref/val_decref are no-ops on them, and
 * anything that tried to retain one would go through promote_if_arena and get
 * a heap copy instead of a dangling stack pointer.
 *
 * Consumes table and ctx and returns owned, matching aot_call_name. The
 * res == ctx case is builtin_dispatch's documented raw borrow (it passes
 * caller_owns_arg=1 to vm_borrow_compensate precisely so the caller settles
 * it), so ctx's ref transfers to the result instead of being dropped. */
static Value *aot_dispatch(Value *table, double key, Value *ctx) {
    Value keyv;
    memset(&keyv, 0, sizeof keyv);
    keyv.type = VAL_NUM; keyv.data.num = key; keyv.arena = 1;

    Value *items[3] = { table, &keyv, ctx };
    Value lst;
    memset(&lst, 0, sizeof lst);
    lst.type = VAL_LIST; lst.arena = 1;
    lst.data.list.items = items; lst.data.list.count = 3; lst.data.list.capacity = 3;

    Value *res = builtin_dispatch(&lst);
    if (g_exit_requested) exit(g_exit_code);
    if (g_has_error) aot_error_exit();
    /* aot_call_name's direct-borrow compensation, verbatim: incref a result
     * that IS one of the argument-vector elements, then release the vector's
     * refs. Whether the handler returned a raw borrow (a builtin: see
     * builtin_dispatch's caller_owns_arg=1) or a fresh ref (a user fn), this
     * is what the aot_call_name path this replaces already did — matching it
     * is the point, so the substitution cannot introduce a new divergence.
     * &keyv is unreachable as a result: builtin_dispatch hands the handler
     * items[2] alone. */
    if (res == ctx || res == table) val_incref(res);
    val_decref(table);
    val_decref(ctx);
    return res ? res : make_null();
}

static Value *aot_call_name(Env *g, const char *name, Value *arg) {
    Value *fn = env_get(g, name);
    if (!fn) { fprintf(stderr, "aot: undefined function '%s'\n", name); exit(1); }
    /* Round 71: the non-callable guard aot_call_value already had, mirrored
     * here (the round-70 sibling-asymmetry pattern). call_eigs_fn returns
     * make_null() for a non-FN/non-BUILTIN with NO error flag, so calling a
     * string through an alias printed null, rc 0, where the VM raises
     * "cannot call str" rc 1. */
    if (fn->type != VAL_BUILTIN && fn->type != VAL_FN)
        rt_error(EK_TYPE, g_trace_current_line, "cannot call %s",
                 val_type_name(fn->type));
    Value *res;
    if (fn->type == VAL_BUILTIN) res = fn->data.builtin(arg);
    else                         res = call_eigs_fn(fn, arg);
    /* `exit of N` unwinds via g_has_error TOO (builtin_exit sets both flags);
     * it is a clean requested exit, not an error — honor the code, print
     * nothing (the VM's main clears g_has_error when g_exit_requested). */
    if (g_exit_requested) exit(g_exit_code);
    /* A raising builtin recorded its error via rt_error/builtin_throw (silent
     * under the elevated g_try_depth) and RETURNED; running on from here is
     * the run-past-error class (#103's sibling — the old code continued with
     * a null result). Uncaught == fatal in the AOT: print the recorded
     * diagnostic and die with the VM's uncaught exit code. */
    if (g_has_error) aot_error_exit();
    if (!res) { val_decref(arg); return make_null(); }
    /* A builtin may return the arg itself, or one of its elements BORROWED
     * (e.g. append -> target = arg[0]). Mirror vm.c's direct-borrow heuristic:
     * keep arg's ref if res IS arg; incref a borrowed arg-element before the arg
     * is torn down. Owned returns aren't arg elements, so they're untouched. */
    if (res == arg) return res;
    if (arg && arg->type == VAL_LIST) {
        for (int i = 0; i < arg->data.list.count; i++) {
            if (arg->data.list.items[i] == res) { val_incref(res); break; }
        }
    }
    val_decref(arg);
    return res;
}

/* One `match` case comparison (#140 follow-on). The VM compiles match as
 * compare-and-jump using ordinary equality, so a non-numeric subject is just
 * values_equal. The PATTERN is owned (emit_val's result) and consumed here;
 * the SUBJECT is borrowed, because it is compared against every pattern in
 * turn and released once by the caller. */
static inline int aot_match_eq(Value *subj, Value *pat) {
    int e = values_equal(subj, pat);
    val_decref(pat);
    return e;
}

/* Borrowed element k of a value-wrapper's argument list (#140). For arity 1
 * the builtin convention hands the value itself as __a; for arity > 1 it hands
 * a LIST, and user functions take boxed parameters BORROWED (see emit_args),
 * so this must not incref. Out of range answers NULL, which the callee's own
 * checks then report — the VM likewise sees a missing argument as null. */
static inline Value *aot_arg_at(Value *a, int k) {
    if (a && a->type == VAL_LIST && k >= 0 && k < a->data.list.count)
        return a->data.list.items[k];
    return NULL;
}

/* Call a callee that is an EXPRESSION rather than a name (#140) — `m.fn of x`,
 * `table[i] of x`. That is how EigenScript's module pattern works: `import x`
 * binds a dict of functions and every use is a dot call, so requiring a
 * statically-known name made every stdlib-using program un-compilable.
 *
 * Identical to aot_call_name from the dispatch onward; only the resolution
 * differs. `fn` arrives OWNED (emit_val's result) and is consumed here. A
 * non-callable raises the VM's own message rather than being coerced. */
static Value *aot_call_value(Value *fn, Value *arg) {
    if (!fn || (fn->type != VAL_BUILTIN && fn->type != VAL_FN)) {
        rt_error(EK_TYPE, 0, "cannot call %s",
                 fn ? val_type_name(fn->type) : "null");
    }
    Value *res;
    if (fn->type == VAL_BUILTIN) res = fn->data.builtin(arg);
    else                         res = call_eigs_fn(fn, arg);
    if (g_exit_requested) exit(g_exit_code);
    if (g_has_error) aot_error_exit();
    if (!res) { val_decref(arg); val_decref(fn); return make_null(); }
    /* aot_call_name's direct-borrow compensation, verbatim. */
    if (res == arg) { val_decref(fn); return res; }
    if (arg && arg->type == VAL_LIST) {
        for (int i = 0; i < arg->data.list.count; i++) {
            if (arg->data.list.items[i] == res) { val_incref(res); break; }
        }
    }
    val_decref(arg);
    val_decref(fn);
    return res;
}

static Value *aot_call_name_ic(Env *g, const char *name, Value *arg, AotNameIC *c) {
    EigsSlot *sp = aot_name_slot(g, name, c);
    Value *fn = sp ? aot_slot_value(sp) : NULL;
    if (!fn) { fprintf(stderr, "aot: undefined function '%s'\n", name); exit(1); }
    /* Round 71: non-callable guard, see aot_call_name. */
    if (fn->type != VAL_BUILTIN && fn->type != VAL_FN)
        rt_error(EK_TYPE, g_trace_current_line, "cannot call %s",
                 val_type_name(fn->type));
    Value *res;
    if (fn->type == VAL_BUILTIN) res = fn->data.builtin(arg);
    else                         res = call_eigs_fn(fn, arg);
    /* `exit of N` unwinds via g_has_error TOO (builtin_exit sets both flags);
     * it is a clean requested exit, not an error — honor the code, print
     * nothing (the VM's main clears g_has_error when g_exit_requested). */
    if (g_exit_requested) exit(g_exit_code);
    /* A raising builtin recorded its error via rt_error/builtin_throw (silent
     * under the elevated g_try_depth) and RETURNED; running on from here is
     * the run-past-error class (#103's sibling — the old code continued with
     * a null result). Uncaught == fatal in the AOT: print the recorded
     * diagnostic and die with the VM's uncaught exit code. */
    if (g_has_error) aot_error_exit();
    if (!res) { val_decref(arg); return make_null(); }
    /* A builtin may return the arg itself, or one of its elements BORROWED
     * (e.g. append -> target = arg[0]). Mirror vm.c's direct-borrow heuristic:
     * keep arg's ref if res IS arg; incref a borrowed arg-element before the arg
     * is torn down. Owned returns aren't arg elements, so they're untouched. */
    if (res == arg) return res;
    if (arg && arg->type == VAL_LIST) {
        for (int i = 0; i < arg->data.list.count; i++) {
            if (arg->data.list.items[i] == res) { val_incref(res); break; }
        }
    }
    val_decref(arg);
    return res;
}
#endif /* AOT_RT_H */
