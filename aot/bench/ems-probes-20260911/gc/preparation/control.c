#include "aot_rt.h"
#include "gc_probe_metrics.h"
static int failures;
#define CHECK(c,label) do { if (!(c)) { fprintf(stderr,"GC_CONTROL_FAIL %s\n",label); ++failures; } } while(0)
int main(void) {
    Env *env=aot_boot();
    CHECK(!g_arena.active,"heap mode");
    gc_collect_cycles();
    GcProbeStats before,after;
    gc_probe_snapshot(&before);
    /* Live parent, duplicate owned edges, and a numeric-only child. */
    Value *leaf=make_list_heap(64), *root=make_list_heap(2);
    for (int i=0;i<64;++i) list_append_owned(leaf,make_num(i));
    list_append(root,leaf); list_append(root,leaf);
    val_decref(leaf);
    gc_note_possible_root(root);
    /* Unreachable cycle with two distinct owned edges to the same child. */
    Value *a=make_list_heap(2), *b=make_list_heap(1);
    list_append(a,b); list_append(a,b); list_append(b,a);
    val_decref(a); val_decref(b);
    gc_collect_cycles();
    gc_probe_snapshot(&after);
    CHECK(after.completed-before.completed==1,"one collection");
    CHECK(after.aborted==before.aborted,"no accounting abort");
    CHECK(after.universe-before.universe==4,"four nodes");
    CHECK(after.reclaimed-before.reclaimed==2,"duplicate-edge cycle reclaimed");
    CHECK(after.survivors-before.survivors==2,"two live nodes");
    CHECK(after.slots[0][0]-before.slots[0][0]==69,"69 discovery slots");
    CHECK(after.edges[0][0]-before.edges[0][0]==5,"five owned discovery edges");
    CHECK(root->data.list.count==2,"live root intact");
    CHECK(root->data.list.items[0]==root->data.list.items[1],"duplicate references intact");
    CHECK(root->data.list.items[0]->data.list.count==64,"numeric child intact");
    /* Deliberately leave no skip-counter assertion here: the separate
     * validator must reject a zeroed counter despite this output staying green. */
    val_decref(root);
    gc_collect_cycles();
    aot_shutdown(env);
    printf("GC_CONTROL failures=%d\n",failures);
    return failures ? 1 : 0;
}
