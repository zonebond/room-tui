# Scope Limitations — Detailed Examples

## Covered Scenarios

### 1. Same-function bound guards (explicit comparison)

```c
void set_index(Track *track, int i, int val) {
    if (i < 0 || i > MAXINDEX) return;  // ← both bounds checked, early return rejects invalid
    track->index[i] = val;              // ← SAFE: all paths to access pass through guard
}
```

### 2. Macro-based guards (invisible to heuristic, visible to AI agent)

```c
#define CHECK_RANGE(idx, lo, hi) (((idx) >= (lo)) && ((idx) < (hi)))

void f(int i, int val) {
    if (CHECK_RANGE(i, 0, 256)) {  // ← macro call — heuristic sees no comparison ops
        g_buf[i] = val;            // ← AI agent looks up macro def → expands to both bounds → SAFE
    }
}
```

### 3. Type-cast guard idiom (invisible to heuristic, visible to AI agent)

```c
void f(int i, int val) {
    if ((unsigned int)i < 256) {   // ← heuristic misreads var as "int", not "i"
        g_buf[i] = val;            // ← AI agent recognizes: negative i wraps to huge unsigned → both bounds → SAFE
    }
}
```

### 4. Equivalent rewrite — early return as guard

```c
void f(int i, int val) {
    if (i >= N) return;            // ← rejects upper OOB (heuristic misses this pattern)
    if (i < 0) return;             // ← rejects lower OOB
    g_buf[i] = val;                // ← SAFE: both invalid ranges rejected before access
}
```

### 5. sizeof-based bounds

```c
void f(int i, int val) {
    if (i < sizeof(g_buf)/sizeof(g_buf[0])) {  // ← derived from array type
        g_buf[i] = val;                         // ← upper bound correct; check lower too
    }
}
```

### 6. Caller-side guards (static function)

```c
static void set_val(int i, int val) {   // ← static: only callers in this file matter
    g_buf[i] = val;                     // ← no guard in this function
}

void public_api(int idx, int val) {
    if (idx >= 0 && idx < 256) {        // ← guard in caller
        set_val(idx, val);              // ← all call sites guarded → SAFE
    }
}
```

### 7. For-loop initialization as implicit lower bound

```c
for (int i = 0; i < count; i++) {  // ← i=0 + i<count recognized as both bounds
    arr[i] = 0;                     // ← SAFE (lower + upper from loop)
}
```

### 8. Static array sizes

```c
#define MAX 256
int buf[MAX];
buf[i] = 0;  // ← size 256 visible via #define → threshold comparison possible
```

### 9. Pointer arithmetic — same as array indexing

```c
int g_buf[256];

void f(int i, int val) {
    if (i >= 0 && i < 256) {
        *(g_buf + i) = val;    // ← recall detects pointer arithmetic, equivalent to g_buf[i]
    }                          // ← SAFE: guard visible, array size known
}
```

The recall layer detects `*(ptr + offset)` patterns. The agent judges them using the
same bounds-check procedure as `ptr[offset]`, plus determining what `ptr` points to
(via `pointer_declaration` in the context).

## NOT Covered Scenarios

### 1. Far-guard (3+ call levels away)

```c
// file1.c
void caller() {
    set_value(9999, 0);  // ← guard is 3 levels up from the actual access
}

// file2.c
void set_value(int i, int val) {
    track_set_index(track, i, val);
}

// file3.c
void track_set_index(Track *track, int i, int val) {
    if (i > MAXINDEX) return;  // ← only upper-bound guard
    track->index[i] = val;      // ← RECALL finds this, but guard is 3 calls away
}
```

The tool only searches ~25 lines within the same function for guards. It won't find `i < 0` checks that exist in `caller()`.

### 2. Complex cross-function pointer flows

```c
void process(Context *ctx) {
    *(ctx->buf + ctx->pos) = value;     // ← recall detects *(ptr+offset), but ctx->buf origin + size unclear
}
```

The tool detects the pointer arithmetic but cannot trace the pointer's origin across
multiple functions or translation units. The agent can judge the offset guard if visible,
but the target array size may be unknown if `ctx->buf` is set in a different file.
Recommend Joern CPG for full dataflow analysis across functions.

### 3. Runtime-sized arrays

```c
void f(int n) {
    int *arr = malloc(n * sizeof(int));
    arr[n] = 0;  // ← n is runtime, tool can't determine array size
}
```

### 4. Pointer arithmetic patterns

```c
*(ptr + offset) = value;  // ← NOT detected by fast recall
```

Only `arr[idx]` subscript expressions are detected. Pointer arithmetic requires Joern CPG (`--recall deep`, not bundled).

## Recommendations for Uncovered Cases

| Scenario | Recommendation |
|----------|---------------|
| Far-guard | CBMC (bounded model checking) or manual review |
| Complex pointer flows | Joern CPG + dataflow analysis |
| Runtime-sized arrays | ASan fuzzing with libFuzzer/AFL |
| Pointer arithmetic | `--recall deep` with Joern, or manual grep |
