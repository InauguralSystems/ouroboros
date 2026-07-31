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
static inline aot_vec aot_vdiv(aot_vec a, aot_vec b){   /* b==0 -> 0, else clamp(a/b) */
    aot_vec nz = _mm256_cmp_pd(b, _mm256_setzero_pd(), _CMP_NEQ_OQ);
    return aot_vguard(_mm256_and_pd(_mm256_div_pd(a, b), nz));
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
static inline aot_vec aot_vdiv(aot_vec a, aot_vec b){   /* b==0 -> 0, else clamp(a/b) */
    aot_vec nz = _mm_cmpneq_pd(b, _mm_setzero_pd());
    return aot_vguard(_mm_and_pd(_mm_div_pd(a, b), nz));
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
static inline aot_vec aot_vdiv(aot_vec a, aot_vec b){ return b == 0.0 ? 0.0 : num_guard(a / b); }
static inline double aot_vhsum(aot_vec x){ return x; }
#endif

/* ---- lifecycle ---- */
static Env *aot_boot(void) {
    EigsState *st = eigs_open();           /* new + attach + init runtime + builtins */
    if (!st) { fprintf(stderr, "aot: eigs_open failed\n"); exit(1); }
    return g_global_env;
}
static void aot_shutdown(Env *g) { (void)g; /* process exit reclaims (slice 1) */ }

/* ---- variable access ---- */
static Value *aot_get(Env *g, const char *name) {
    Value *v = env_get(g, name);
    if (!v) return make_null();
    val_incref(v);
    return v;
}
static void aot_set(Env *g, const char *name, Value *val) {
    env_set_local_owned(g, name, val);     /* adopts val's birth ref */
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
AOT_NUMOP(aot_div, (y == 0.0 ? 0.0 : x / y))
AOT_NUMOP(aot_mod, (y == 0.0 ? 0.0 : fmod(x, y)))

/* Specialized (unboxed double) div/mod, matching the VM (b==0 -> 0). Single-eval
   helpers so the emitter doesn't have to duplicate the operand expressions. */
static inline double aot_ddiv(double a, double b) { return b == 0.0 ? 0.0 : num_guard(a / b); }
static inline double aot_dmod(double a, double b) { return b == 0.0 ? 0.0 : num_guard(fmod(a, b)); }

#define AOT_CMP(NAME, EXPR) \
    static Value *NAME(Value *a, Value *b) { \
        int res = 0; \
        if (a->type == VAL_NUM && b->type == VAL_NUM) { \
            double x = a->data.num, y = b->data.num; (void)x; (void)y; res = (EXPR); } \
        Value *r = make_num(res ? 1.0 : 0.0); \
        val_decref(a); val_decref(b); return r; }
AOT_CMP(aot_lt, x < y)
AOT_CMP(aot_gt, x > y)
AOT_CMP(aot_le, x <= y)
AOT_CMP(aot_ge, x >= y)

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
static void aot_buf_expect(Value *b) {
    if (!b || b->type != VAL_BUFFER) { fprintf(stderr, "buffer op on a non-buffer value\n"); exit(1); }
}
static long aot_idx(double d, int count) {
    long i = (long)d;
    if ((double)i != d) { fprintf(stderr, "index must be an integer, got %g\n", d); exit(1); }
    if (i < 0) i += count;
    if (i < 0 || i >= count) { fprintf(stderr, "buffer index %ld out of range (length %d)\n", (long)d, count); exit(1); }
    return i;
}
static double aot_buf_get(Value *b, double idx) { aot_buf_expect(b); return b->data.buffer.data[aot_idx(idx, b->data.buffer.count)]; }
static void   aot_buf_set(Value *b, double idx, double v) { aot_buf_expect(b); b->data.buffer.data[aot_idx(idx, b->data.buffer.count)] = v; }
/* Integer-index fast path: the index was computed in native `long` arithmetic
   (provably-integer induction vars + dimensions), so skip the float integer
   check. Negative-resolve + bounds-check still mirror the VM. */
static long aot_idx_i(long i, int count) {
    if (i < 0) i += count;
    if (i < 0 || i >= count) { fprintf(stderr, "buffer index %ld out of range (length %d)\n", i, count); exit(1); }
    return i;
}
static double aot_buf_get_i(Value *b, long idx) { aot_buf_expect(b); return b->data.buffer.data[aot_idx_i(idx, b->data.buffer.count)]; }
static void   aot_buf_set_i(Value *b, long idx, double v) { aot_buf_expect(b); b->data.buffer.data[aot_idx_i(idx, b->data.buffer.count)] = v; }
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
static double aot_buf_len(Value *b) {
    /* the buf class is a string/buffer union (checksum's `_blen of "abc"`,
     * EigenMiniSat's `_is_int_token`, #86): mirror the VM's `len` on both. */
    if (b && b->type == VAL_STR) return (double)strlen(b->data.str);
    aot_buf_expect(b);
    return (double)b->data.buffer.count;
}
/* Raw element pointer for the proven-safe (in-bounds, non-negative) loop path. */
static double *aot_buf_data(Value *b) { aot_buf_expect(b); return b->data.buffer.data; }

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

/* ---- ranged reductions over a slice `buf[lo:hi]` (zero-copy) ----
 * `dot of [A[sa:ea], B[sb:eb]]` / `sum of A[s:e]` / `norm of A[s:e]` lower to
 * these instead of materializing the slice. Bound resolution mirrors the VM's
 * OP_SLICE_GET exactly (vm.c): integer-only, negatives count from len, then
 * 0<=start<=end<=len (<= upper end; out-of-range errors like the VM). */
static inline long aot_sbound(double x, long len) {
    long i = (long)x;
    if ((double)i != x) { fprintf(stderr, "slice bound must be an integer, got %g\n", x); exit(1); }
    if (i < 0) i += len;
    if (i < 0 || i > len) { fprintf(stderr, "slice bound %ld out of range (length %ld)\n", (long)x, len); exit(1); }
    return i;
}
static inline double aot_dot_range(Value *A, double sa, double ea, Value *B, double sb, double eb) {
    long la = A->data.buffer.count, lb = B->data.buffer.count;
    long s1 = aot_sbound(sa, la), e1 = aot_sbound(ea, la);
    long s2 = aot_sbound(sb, lb), e2 = aot_sbound(eb, lb);
    if (s1 > e1 || s2 > e2) { fprintf(stderr, "slice start > end\n"); exit(1); }
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
    long s1 = aot_sbound(sa, la), e1 = aot_sbound(ea, la);
    if (s1 > e1) { fprintf(stderr, "slice start > end\n"); exit(1); }
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
    long s1 = aot_sbound(sa, la), e1 = aot_sbound(ea, la);
    if (s1 > e1) { fprintf(stderr, "slice start > end\n"); exit(1); }
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

/* ---- observer system ----
 * An observed numeric variable lives in the env (so it has a tracked slot) and
 * its assignment mirrors the VM's OBSERVE_NAME_POST: store, resolve the slot,
 * update its entropy/dH window, and mark it the global last-observer. Bare
 * predicates (converged/stable/...) read that last-observer's slot — exactly
 * CASE(PREDICATE). Both call the runtime's observer fns, so the VM stays the
 * oracle. (Unboxing is off for observed programs — observation needs the slot.) */
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
static int aot_predicate(int kind) {
    if (g_last_obs_slot_idx >= 0 && g_last_obs_slot_env &&
        g_last_obs_slot_idx < g_last_obs_slot_env->obs_cap) {
        const ObserverSlot *s = &g_last_obs_slot_env->obs[g_last_obs_slot_idx];
        switch (kind) {
        case 0: return observer_slot_converged(s);
        case 1: return observer_slot_stable(s);
        case 2: return observer_slot_improving(s);
        case 3: return observer_slot_oscillating(s);
        case 4: return observer_slot_diverging(s);
        case 5: return observer_slot_equilibrium(s);
        }
    }
    return 0;
}
/* `report of x` — the most-specific predicate of x's observed slot, as a string
 * (mirrors CASE(REPORT_NAME)): resolve the binding's slot, classify it, else
 * "equilibrium" for an unobserved binding. */
static Value *aot_report(Env *e, const char *name) {
    int oidx = -1, odepth = 0;
    Env *oe = env_resolve_chain(e, name, env_hash_name(name), &oidx, &odepth);
    if (oe && oidx >= 0 && oidx < oe->obs_cap && oe->obs[oidx].used)
        return make_str(observer_slot_report(&oe->obs[oidx]));
    return make_str("equilibrium");
}
/* ---- temporal interrogatives (the trace tape) ----
 * `prev of x`, `what is x at L`. trace_assign feeds the per-name prev-map +
 * (line, value) history independent of the env (so temporal vars stay unboxed)
 * and independent of any flag, stamping each entry with g_trace_current_line —
 * which the emitter sets per source line, mirroring OP_LINE. Numeric values
 * only for now; an unknown/one-assignment name yields 0 (as the VM's miss). */
static void aot_trace_assign(const char *name, double v) {
    EigsSlot s; s.d = v;
    trace_assign(name, s);
}
/* The query result is polymorphic — a number, a string (`who`), or `null` on a
 * miss — so route the slot through slot_to_value (exactly like the VM's
 * INTERROGATE_NAMED_AT) rather than assuming a double, then drop the slot's ref. */
static Value *aot_prev_val(const char *name) {
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
    if (a.cols != b.rows) return o;            /* shape mismatch -> null tensor */
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

static AotTensor aot_tensor_add(AotTensor a, AotTensor b) {
    AotTensor o = a;                           /* inherit rows/cols/is1d/kind */
    o.owns = 1;
    long n = a.rows * a.cols;
    o.data = (double*)calloc((size_t)n, sizeof(double));
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
 * READ (OP-get-field, vm.c ~1020): dict -> the field's value (missing ->
 * null); a NULL-typed target yields null SILENTLY; any other type raises
 * EK_TYPE "cannot access field". SET (vm.c ~1127): dict -> set; a
 * NULL-typed target is a silent NO-OP; any other type raises EK_TYPE
 * "cannot set field". Both consume the owned target (emit_val results are
 * owned, same convention as aot_index_get); dot_get returns owned. */
static Value *aot_dot_get(Value *target, const char *key) {
    Value *result = NULL;
    if (target && target->type == VAL_DICT) {
        Value *v = dict_get(target, key);
        if (v) { result = v; val_incref(v); }
    } else if (target && target->type != VAL_NULL) {
        rt_error(EK_TYPE, 0, "cannot access field '%s' on %s",
                 key, val_type_name(target->type));
    }
    if (target) val_decref(target);
    return result ? result : make_null();
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

/* ---- for-loop iteration over a list/buffer (range materializes to a list) --- */
static long aot_iter_len(Value *v) {
    if (!v) return 0;
    if (v->type == VAL_LIST)   return v->data.list.count;
    if (v->type == VAL_BUFFER) return v->data.buffer.count;
    return 0;
}
static Value *aot_iter_get(Value *v, long k) {   /* owned element k */
    if (v->type == VAL_LIST)   { Value *e = v->data.list.items[k]; val_incref(e); return e; }
    if (v->type == VAL_BUFFER) return make_num(v->data.buffer.data[k]);
    return make_null();
}

static Value *aot_call_name(Env *g, const char *name, Value *arg) {
    Value *fn = env_get(g, name);
    if (!fn) { fprintf(stderr, "aot: undefined function '%s'\n", name); exit(1); }
    Value *res;
    if (fn->type == VAL_BUILTIN) res = fn->data.builtin(arg);
    else                         res = call_eigs_fn(fn, arg);
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
