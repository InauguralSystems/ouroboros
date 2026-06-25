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
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
static long aot_idx(double d, int count) {
    long i = (long)d;
    if ((double)i != d) { fprintf(stderr, "index must be an integer, got %g\n", d); exit(1); }
    if (i < 0) i += count;
    if (i < 0 || i >= count) { fprintf(stderr, "buffer index %ld out of range (length %d)\n", (long)d, count); exit(1); }
    return i;
}
static double aot_buf_get(Value *b, double idx) { return b->data.buffer.data[aot_idx(idx, b->data.buffer.count)]; }
static void   aot_buf_set(Value *b, double idx, double v) { b->data.buffer.data[aot_idx(idx, b->data.buffer.count)] = v; }
static double aot_buf_len(Value *b) { return (double)b->data.buffer.count; }
/* Raw element pointer for the proven-safe (in-bounds, non-negative) loop path. */
static double *aot_buf_data(Value *b) { return b->data.buffer.data; }

/* ---- call a global/builtin by name, single arg (consumes arg) ---- */
static Value *aot_call_name(Env *g, const char *name, Value *arg) {
    Value *fn = env_get(g, name);
    if (!fn) { fprintf(stderr, "aot: undefined function '%s'\n", name); exit(1); }
    Value *res;
    if (fn->type == VAL_BUILTIN) res = fn->data.builtin(arg);
    else                         res = call_eigs_fn(fn, arg);
    val_decref(arg);
    return res ? res : make_null();
}
#endif /* AOT_RT_H */
