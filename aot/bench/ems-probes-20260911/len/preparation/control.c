#include "len_probe.h"
extern Value *builtin_buffer(Value *arg);
static Value *other_builtin(Value *arg) { (void)arg; return make_num(23); }
#ifdef LEN_PROBE_CONTROL_BASELINE
#define invoke aot_call_dispatch
#else
#define invoke len_probe_dispatch
#endif
static int check(Value *fn, Value *arg, double expected) {
    Value *r = invoke(fn, arg);
    int ok = r && r->type == VAL_NUM && r->data.num == expected;
    if (r) val_decref(r);
    return ok;
}
int main(void) {
    Env *g = aot_boot();
    Value *xs = make_list(1);
    /* Include value 1 == len(xs), exercising any runtime numeric singleton. */
    list_append_owned(xs, make_num(1));
    int ok = check(make_builtin(builtin_len), xs, 1);
    Value *n = make_num(3);
    Value *buf = builtin_buffer(n);
    val_decref(n);
    ok &= check(make_builtin(builtin_len), buf, 3);
    ok &= check(make_builtin(builtin_len), make_str("ab"), 2);
    /* Same supported argument tag, different actual function identity. */
    xs = make_list(0);
    ok &= check(make_builtin(other_builtin), xs, 23);
    puts(ok ? "LEN_CONTROL 4/4" : "LEN_CONTROL FAIL");
    aot_shutdown(g);
    return ok ? 0 : 1;
}
