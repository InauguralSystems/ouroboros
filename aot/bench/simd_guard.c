/* Spike: can EigenScript's num_guard (the no-NaN/Inf guarantee) be implemented
 * as a packed, VECTORIZABLE guard instead of a per-op branch?
 *
 * Compares scalar per-op num_guard vs an AVX2 packed guard (cmp+and for NaN->0,
 * min/max for the +/-1e308 clamp) on an element-wise map, and verifies the
 * packed guard is semantically IDENTICAL to num_guard, including NaN/Inf.
 *
 *   gcc -O3 -march=native aot/bench/simd_guard.c -o sp -lm && ./sp
 *
 * Measured:
 *   SSE2 (2-wide, Goldmont dev box):  packed-guard 1.37x over scalar-guard
 *   AVX2 (4-wide, cloud EPYC 7763):   packed-guard 3.67x; raw(unsound) ceiling 11.15x
 *   NaN/Inf semantics match num_guard exactly.
 * See aot/DESIGN_no_nan_simd.md.
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <immintrin.h>

static inline double ng(double x){ if(x!=x)return 0; if(x>1e308)return 1e308; if(x<-1e308)return -1e308; return x; }

#ifdef __AVX2__
static inline __m256d vg(__m256d x){
    __m256d nn = _mm256_cmp_pd(x, x, _CMP_EQ_OQ);     /* not-NaN mask */
    x = _mm256_and_pd(x, nn);                         /* NaN lane -> 0 */
    x = _mm256_min_pd(x, _mm256_set1_pd(1e308));
    x = _mm256_max_pd(x, _mm256_set1_pd(-1e308));
    return x;
}
__attribute__((noinline)) static void simd(double*o,const double*in,long n){
    __m256d a=_mm256_set1_pd(1.3),b=_mm256_set1_pd(2.1),c=_mm256_set1_pd(1.7),d=_mm256_set1_pd(0.4);
    long i=0;
    for(;i+4<=n;i+=4){ __m256d x=_mm256_loadu_pd(in+i);
        x=vg(_mm256_add_pd(vg(_mm256_mul_pd(x,a)),b)); x=vg(_mm256_sub_pd(vg(_mm256_mul_pd(x,c)),d));
        _mm256_storeu_pd(o+i,x); }
    for(;i<n;i++){ double x=in[i]; x=ng(ng(x*1.3)+2.1); x=ng(ng(x*1.7)-0.4); o[i]=x; }
}
#else
__attribute__((noinline)) static void simd(double*o,const double*in,long n){
    for(long i=0;i<n;i++){ double x=in[i]; x=ng(ng(x*1.3)+2.1); x=ng(ng(x*1.7)-0.4); o[i]=x; } }
#endif

__attribute__((noinline)) static void scal(double*o,const double*in,long n){
    for(long i=0;i<n;i++){ double x=in[i]; x=ng(ng(x*1.3)+2.1); x=ng(ng(x*1.7)-0.4); o[i]=x; } }
__attribute__((noinline)) static void raw(double*o,const double*in,long n){
    for(long i=0;i<n;i++){ double x=in[i]; o[i]=(x*1.3+2.1)*1.7-0.4; } }

static double now(){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
int main(){
    long n=65536; int R=8000; double*in=malloc(n*8),*o1=malloc(n*8),*o2=malloc(n*8),*o3=malloc(n*8);
    for(long i=0;i<n;i++) in[i]=(double)(i%997)*0.01;
    double t=now(); for(int k=0;k<R;k++) scal(o1,in,n); double ts=now()-t;
    t=now(); for(int k=0;k<R;k++) simd(o2,in,n); double tv=now()-t;
    t=now(); for(int k=0;k<R;k++) raw(o3,in,n);  double tr=now()-t;
    int same=1; for(long i=0;i<n;i++) if(o1[i]!=o2[i]) same=0;
    printf("scalar-guard %.3fs  simd-guard %.3fs  raw(unsound) %.3fs\n", ts, tv, tr);
    printf("simd-guard vs scalar-guard: %.2fx   (raw ceiling %.2fx)   same-output=%d\n", ts/tv, ts/tr, same);
    double bad[4]={0.0/0.0, 1e300*1e300, -1e300*1e300, 5.0}, go[4]; simd(go,bad,4);
    int ok=1; for(int i=0;i<4;i++){ double r=ng(ng(ng(bad[i]*1.3)+2.1)*1.7-0.4); if(go[i]!=r) ok=0; }
    printf("NaN/Inf semantics match num_guard: %d\n", ok);
    return 0;
}
