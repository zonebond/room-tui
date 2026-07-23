---
name: oob-divzero
description: >
  Scan C code for array out-of-bounds and division-by-zero vulnerabilities.
  Use when the user asks to "scan for bugs", "check for OOB", "find vulnerabilities",
  "security review", "code audit", "扫一下越界问题", "查漏洞", "安全检查",
  "找 bug", "越界检查", "除零检查", "静态分析", "代码审计",
  or makes similar C code security-review requests.
  Pipeline: tree-sitter recall → AI agent semantic judgment → ASan verify → three-tier report.
  The agent (you) provides the judgment layer using your own model — no API key needed.
compatibility: Python >= 3.10, oob-divzero CLI available on PATH
metadata:
  author: zonebondx
  version: "0.1.0"
  category: security
  languages: [c]
---

# OOB / Division-by-Zero Vulnerability Scanner for C

`oob-divzero` is a narrow, honest C vulnerability checker. It detects potential
**array out-of-bounds** (both lower and upper) and **division-by-zero** bugs through
a three-layer pipeline:

```
C source → [Recall] → [Judge] → [Verify] → three-tier report
           tree-sitter   you (the agent)   ASan/UBSan
```

| Layer | Who | What |
|-------|-----|------|
| **Recall** | CLI tool (tree-sitter) | Finds every `arr[idx]` access, every `/` and `%` operation |
| **Judge** | **You, the AI agent** | For each uncertain point, decides if an effective bounds/zero check exists. This is the intelligence layer — you recognize guards that the tool's built-in heuristic cannot (macros, type-cast idioms, equivalent rewrites, caller-side checks). The CLI itself has **no model and never calls an LLM**. |
| **Verify** | CLI tool (ASan/UBSan) | Compiles RISKY points with sanitizers and attempts to actually trigger the crash |

The default and intended path is the **agent two-step flow**: the CLI emits uncertain
points, you judge them using your own model (guided by the [judgment procedure](#judgment-procedure)
below), then the CLI resumes to verify and report.

---

## No target provided — show introduction

If the user invokes `/oob-divzero` without specifying a C code target (no file path,
directory, or git URL), do NOT start the scan pipeline. Instead, present the following
brief introduction and ask for a target:

> **oob-divzero** — C 代码越界 & 除零漏洞扫描器
>
> 三层流水线：`tree-sitter 召回 → AI 语义判定 → ASan 验证 → 三级报告`
>
> | 层 | 执行者 | 作用 |
> |---|--------|------|
> | Recall | CLI (tree-sitter) | 找出所有 `arr[idx]` 数组访问、`*(ptr+offset)` 指针运算、`/` 和 `%` 除法 |
> | Judge | 你 (AI agent) | 对每个不确定点判定是否存在有效边界/零值检查——识别宏守卫、类型转换惯用法、等价改写、调用侧检查 |
> | Verify | CLI (ASan/UBSan) | 编译并尝试触发崩溃，产生 Confirmed 级别 |
>
> **报告三级：**
> - 🟥 **Confirmed** — ASan 触发崩溃，真实漏洞，立即修复
> - 🟧 **Likely** — 判定为 RISKY 但未自动触发，需人工审查
> - 🟨 **Defensive** — 缺少守卫但当前调用路径安全，建议加固
>
> **用法：** 提供要扫描的 C 代码目标：
> - 单个 `.c` 文件：`/oob-divzero path/to/main.c`
> - 目录（递归）：`/oob-divzero src/`
> - Git 仓库：`/oob-divzero https://github.com/user/repo`

---

## Primary workflow — task-based pipeline

The pipeline is managed through the host agent's native **task system** (`TaskCreate` /
`TaskUpdate` / `TaskList`). This gives us durable state tracking across sessions,
progress visibility, and resumability — without ad-hoc `/tmp` file handoffs.

### Data files

All intermediate data and reports are stored under `<project>/.oob-divzero/<YYYYMMDD_HHMMSS>/`:

| File | Purpose |
|------|---------|
| `<project>/.oob-divzero/<YYYYMMDD_HHMMSS>/pending.json` | Recall + heuristic pre-judgment results |
| `<project>/.oob-divzero/<YYYYMMDD_HHMMSS>/judged.json` | Your semantic judgments |
| `<project>/.oob-divzero/<YYYYMMDD_HHMMSS>/oob-divzero-report.html` | Final report (self-contained HTML) |

Each complete scan run creates its own datetime-stamped subdirectory — no collisions
between runs. These persist with the project — you can resume an interrupted scan,
compare against previous reports, or commit them as audit artifacts.

### Task structure

Create these tasks before starting work, then update each as the pipeline progresses:

```
┌─ Task "oob-divzero: <target>"  (status: in_progress)
│   metadata: { target, project_dir, task_dir, pending_file, judged_file, report_file, started_at }
│
├── Task "Step 1/4: Recall"       (blockedBy: none)
│     Run oob-divzero scan ... (CLI auto-creates .oob-divzero/<datetime>/)
│     → on complete: update master metadata with task_dir and pending stats
│
├── Task "Step 2/4: Judge"        (blockedBy: Step 1)
│     Read pending.json, judge each point, write judged.json to same task_dir
│     → on complete: update master metadata with verdict counts
│
├── Task "Step 3/4: Verify & Report" (blockedBy: Step 2)
│     Run oob-divzero resume ... (report auto-written to task_dir)
│     → on complete: update master metadata with confirmed/likely/defensive counts
│
└── Task "Step 4/4: Present findings" (blockedBy: Step 3)
      Read report.md from task_dir, present to user per [Presenting results](#presenting-results-to-the-user)
      → on complete: mark master task completed
```

### Detailed steps

### Step 1/4 — Recall

First, determine the project directory:
- If `<target>` is a **file**: project dir = the directory containing that file
- If `<target>` is a **directory**: project dir = that directory
- If `<target>` is a **git URL**: project dir = the cloned repo directory

Run recall — the CLI auto-creates `.oob-divzero/<YYYYMMDD_HHMMSS>/` and
writes `pending.json` there:

```bash
oob-divzero scan <target> --judge agent --verify off
```

The CLI prints the task directory path and pending file location to stderr.
Capture these — you'll need them for subsequent steps.

Mark task "Step 1/4: Recall" as **completed**. Read the pending file header to get
summary counts, then update task "Step 2/4: Judge" to **in_progress**.

### Step 2/4 — Judge

Read `<task-dir>/pending.json` (from the path printed by Step 1). For **each** entry under
`pending_judgments`, apply the full [Judgment Procedure](#judgment-procedure) below.

**Pending.json structure:**

```json
{
  "version": "0.1.0",
  "target": "<scan target path>",
  "config": { "recall": "fast", "verify": "auto", "format": "md" },
  "summary": {
    "total_points": 6,
    "confident_pre_judged": 1,
    "pending_judgment": 5
  },
  "confident_points": [ ... ],
  "pending_judgments": [
    {
      "id": 1,
      "file": "/path/to/main.c",
      "line": 34,
      "col": 9,
      "expression": "g_buf[i]",
      "function": "macro_guarded_write",
      "kind": "array_access",
      "heuristic_verdict": "DEFENSIVE",
      "heuristic_reason": "...",
      "heuristic_confidence": "medium",
      "needs_judgment": true,
      "context": {
        "function_source": "void macro_guarded_write(int i, int val) { ... }",
        "relevant_macros": ["#define CHECK_RANGE(idx, lo, hi) ..."],
        "array_declaration": "Line 24: static int g_buf[256];",
        "pointer_declaration": null,
        "guards_found_by_heuristic": ["L47: i < 256 (guard)"],
        "index_variable": "i",
        "question": "Does the array access 'g_buf[i]' at line 34 have effective bounds checking on the index variable?..."
      }
    }
  ]
}
```

**Key fields per pending point:**

| Field | What it tells you |
|-------|-------------------|
| `id` | Unique point ID — use this in judged.json to match verdict → point |
| `file` / `line` / `col` | Exact location of the access |
| `expression` | The actual access expression (e.g., `g_buf[i]`, `a / b`, `*(buf+off)`) |
| `function` | Function name containing the access |
| `kind` | `"array_access"`, `"pointer_arithmetic"`, `"division"`, or `"modulo"` |
| `heuristic_verdict` | What the built-in heuristic thought — **may be wrong!** Override it. |
| `heuristic_confidence` | `"high"` → trusted pre-judgment; `"medium"`/`"low"` → must judge yourself |
| `heuristic_reason` | Heuristic's rationale — best-effort label, not ground truth |
| `context.function_source` | **Full function source code** (up to 8000 chars). Your main material for judgment. |
| `context.relevant_macros` | All `#define` lines from the file. **Crucial for macro guard detection.** |
| `context.array_declaration` | How the array is declared (line number + source). For comparing guard thresholds. |
| `context.pointer_declaration` | For pointer arithmetic: the pointer's assignment/declaration. Used to determine target size. |
| `context.guards_found_by_heuristic` | Guards the heuristic pattern matcher already found. Verify — they may be spurious or incomplete. |
| `context.index_variable` | The index/divisor variable name the tool thinks is critical. |
| `context.question` | A generated question summarizing what you need to decide. |

#### ⚠️ MANDATORY: Always use deep mode — never use shallow pattern-matching

The `pending.json` context fields (`heuristic_verdict`, `heuristic_confidence`,
`heuristic_reason`, `guards_found_by_heuristic`) are **starting hints, not answers**.
You MUST NOT make judgments based solely on these metadata fields without reading
actual source code. The heuristic is wrong on 30-50% of uncertain points — trusting
it causes missed bugs.

**The following shortcuts are FORBIDDEN and will produce false negatives:**

| Forbidden shortcut | Why it fails | What to do instead |
|--------------------|-------------|-------------------|
| Trusting `heuristic_verdict: SAFE` on `low` confidence | "low confidence SAFE" means "found nothing suspicious but unsure" — often the heuristic missed a real bug because it can't see macros, type guards, equivalent rewrites, or caller-side checks | Read the actual source file at `context.function_source` location. Apply Steps A-D of the Judgment Procedure. |
| Trusting `heuristic_verdict: DEFENSIVE` from bulk pattern-matching | DEFENSIVE can mask genuine RISKY bugs — e.g., array access with only a lower-bound guard but no upper bound, or `arr[length()-1]` with no empty check | Read the actual source. Check whether the guard truly covers ALL needed bounds. A single missing bound makes it RISKY. |
| Trusting `guards_found_by_heuristic` as complete | The heuristic only sees literal `var OP value` in `if`/`for`/`while`. It misses: macros, type casts, equivalent rewrites, and caller-side guards (see [Step C](#step-c-find-guards-the-heuristic-cannot-see)) | Apply ALL five categories of Step C. Expand every macro. Recognize `(unsigned)` guards. Spot `if(x>=N)return` patterns. |
| Pattern-matching on `heuristic_reason` strings (e.g., regex `constant divisor` → SAFE) | The heuristic's reason field is a best-effort label. A division by `AXIS_X_MAX` is safe (compile-time constant), but a division by `axisCount` where axisCount comes from user input may not be. You must read the source to tell the difference. | Read the function source to check whether the divisor is a literal constant, a `#define`, or a runtime variable. |
| Making judgments from `expression` string alone | `m_points[segmentIndex]` — the expression says nothing about whether `segmentIndex` is bounds-checked. | Read the source to trace where `segmentIndex` comes from and whether it's verified against array size. |
| Judging ALL points in a single bulk pass without reading files | Large projects have different patterns per file. `joystickchart.cpp` and `videomonitor.cpp` have different bug patterns. | Group points by file. Read each file's relevant sections. Cross-reference: where does the index come from? |

**Minimum bar for every pending point before you write judged.json:**
- For every `array_access` point with `heuristic_confidence: low` or `medium`: you have read the actual source file around that line and traced the index variable's origin.
- For every `DEFENSIVE` point: you have verified there is no hidden guard (macro, type cast, equivalent rewrite) that would make it SAFE, and no missing bound that would make it RISKY.
- For every point involving a function call result as an index (e.g., `m_points[getIndex(x)]`): you have read the called function's source to understand its return value range.
- For every point where the index is a member variable (e.g., `rList[m_rIndex]`): you have searched for bounds checks on that member variable anywhere reachable before the access.

Write your judgments to `<task-dir>/judged.json` (same directory as `pending.json`):

```json
{
  "version": "0.1.0",
  "judged_by": "<your name>",
  "judgments": [
    {"id": 1, "verdict": "SAFE",  "reasoning": "L33: CHECK_RANGE(i,0,256) macro expands to ((i)>=(0)&&(i)<(256)) per L25 #define — covers both bounds. L24: array g_buf[256], threshold matches."},
    {"id": 2, "verdict": "SAFE",  "reasoning": "L47: (unsigned int)i < 256 — classic C combined-bounds idiom, single expression covers both upper and lower bounds."},
    {"id": 3, "verdict": "RISKY", "reasoning": "L58: no guard of any kind on index i within function scope. i comes directly from parameter."},
    {"id": 5, "verdict": "DEFENSIVE", "reasoning": "L77: division a/b with no zero check, but b is set from a trusted internal constant earlier in the call chain."}
  ]
}
```

**Rules for judged.json:**

- `id` must match the `id` from pending.json exactly — the CLI matches by ID.
- `verdict` must be one of: **`SAFE`**, **`RISKY`**, **`DEFENSIVE`**, **`NEEDS_VERIFICATION`** (uppercase).
- `reasoning` must cite **specific line numbers** from the source (e.g., "L33: CHECK_RANGE(...)" not "there's a check somewhere").
- `judged_by` is any identifier for you (the agent).
- You only need to include entries for points where `needs_judgment: true`. Confident pre-judged points are kept as-is.

Mark task "Step 2/4: Judge" as **completed**, start "Step 3/4: Verify & Report".

### Step 3/4 — Verify & Report

```bash
oob-divzero resume \
  --pending <task-dir>/pending.json \
  --judged <task-dir>/judged.json \
  --verify auto
```

The report is automatically written to `<task-dir>/oob-divzero-report.html` (same
directory as the pending/judged files). Use the `-o` flag to override.

Mark task "Step 3/4: Verify & Report" as **completed**, start "Step 4/4: Present findings".

### Step 4/4 — Present findings ⚠️ MANDATORY

**Read the report file** at `<task-dir>/oob-divzero-report.html` and present the
full results to the user. Do NOT simply say "scan complete" or "report written"
without showing the actual findings.

1. Start with a concise summary: total points, Confirmed / Likely / Defensive counts.
2. Detail each Confirmed finding with full source context, reasoning, ASan evidence, and fix.
3. List Likely points with reasoning and manual review checklist.
4. Summarize Defensive items.
5. Always mention the [scope limitations](#scope-boundaries).
6. Tell the user where the report file is saved.

If the resume command failed or the report file was not created, check stderr and
inform the user. Do NOT silently skip presenting results.

Mark all remaining tasks as **completed**.

---

## Judgment Procedure

This is the heart of the skill. Apply it to **every** entry in `pending_judgments`.
Read this entire section before judging your first point — each sub-section builds
on the previous one.

### Red flags — these patterns MUST trigger reading the actual source file

Before you start judging, scan the pending list for these patterns. Any point
matching one of these is a **mandatory source-reading trigger** — you must
open the actual `.c`/`.cpp`/`.h` file and read around the access line.

| Red flag pattern | What to look for in source | Real bug example missed by shallow mode |
|------------------|---------------------------|----------------------------------------|
| Array access with `heuristic_confidence: low` and any `SAFE`/`DEFENSIVE` verdict | The heuristic found no guard but marked it "probably safe" — it's guessing. Read the source to find or deny guards. | `m_points[segmentIndex]` where `segmentIndex` comes from a function that can return `length()` |
| Pointer arithmetic `*(ptr + var)` with a variable offset | The recall layer detects `*(ptr + offset)` patterns. The agent must determine what `ptr` points to (size) and whether the offset is bounds-checked. Read `pointer_declaration` in the context to find the pointer's assignment. | `*(buf + i)` where `buf` is a function parameter with unknown size and `i` is unchecked |
| Array access where index is `something.length() - 1` or `something.size() - 1` | Check if there's an **empty-container guard** before the expression. `length()-1` on an empty container is `-1` → OOB. | `m_values[m_values.length()-1]` after a loop that skips when empty but the access is OUTSIDE the loop |
| Array access where index is a member variable (e.g., `arr[m_foo]`, `list[m_idx]`) | Search the function (and entire file if needed) for any bounds check on that member variable. Member indices are often set elsewhere without re-validation. | `rList[m_rIndex]` — `m_rIndex` never checked against `rList.size()` |
| Array access where index comes from a function call result | Read the called function. What's its return range? Can it return values outside `[0, size-1]`? | `getPointIndexByX(x)` can return `m_points.length()` when `x` is past all points |
| Division where divisor is NOT a literal constant (`2`, `360`, `1E7`, etc.) | Read the source to determine: is it a `#define` constant? A function parameter? A member variable? `const` local? | `w_deg / w` — `w` is a parameter, caller could pass 0 |
| Any point where `guards_found_by_heuristic` has exactly 1 entry that only checks lower bound (`>= 0`) | Read the source — is there ALSO an upper bound check (maybe as a macro, type cast, or equivalent rewrite)? If only lower bound exists, it's likely RISKY. | `dragPointIndex >= 0` but no `< m_values.size()` |
| Array access inside a `paintEvent` / `mouseMoveEvent` / UI callback | These are event-driven — the container state may change between events. Read for empty-container guards and index validation. | `m_values[dragPointIndex]` where `dragPointIndex` is set in `mousePressEvent` but not re-validated in `mouseMoveEvent` |

### Core question

> From this access point, is there an **effective check** on the critical variable
> (the array index for `arr[idx]`, the divisor for `/` and `%`) that prevents an
> out-of-bounds access or division by zero?

"Effective" means: the check must (a) actually exist in the code, (b) be reachable
on all paths that lead to this access, and (c) have a correct threshold relative to
the array size or the zero-value.

### Step-by-step judgment process

Follow these steps in order for each pending point. Don't skip.

#### Step A: Read the full context — including the actual source file

Open the point's `context` object and read every field. Then, **for any point
matching a [red flag pattern](#red-flags--these-patterns-must-trigger-reading-the-actual-source-file),
also read the actual source file** on disk at `pending_point.file`.
The `context.function_source` is truncated to 8000 chars and only includes the
immediate function — it may not show called functions, member variable assignments,
or cross-function data flow that are critical to judgment.

Read these fields from the context:

1. **`function_source`** — The complete function body containing this access. This is your primary material. Read it from top to bottom. Understand the control flow: which branches lead to this access?

2. **`relevant_macros`** — All `#define` directives from the file. These are your key to finding macro-based guards. For example:
   ```c
   #define CHECK_RANGE(idx, lo, hi) (((idx) >= (lo)) && ((idx) < (hi)))
   #define ASSERT_VALID(p)          assert((p) != NULL)
   ```
   If `CHECK_RANGE(i, 0, 256)` appears in the function source, look up its definition here — does it expand to bounds checks?

3. **`array_declaration`** — How the array is declared (with line number). Use this to verify that guard thresholds are correct. Example: `"Line 24: static int g_buf[256];"` → the array has 256 elements, so valid indices are `0` through `255`. A guard of `i < 256` is correct; `i <= 255` is also correct; `i <= 256` is an off-by-one bug.

4. **`guards_found_by_heuristic`** — The built-in pattern matcher's findings. These are starting points, not final answers. The heuristic only recognizes literal `var OP value` in `if`/`for`/`while` conditions and often misses or misidentifies guards.

5. **`index_variable`** — The variable name the tool identified as the critical index or divisor. Verify this is correct — sometimes the tool picks the wrong variable.

#### Step B: Verify the heuristic's findings

For each guard in `guards_found_by_heuristic`:

- **Is it real?** Read the cited line in `function_source`. Does the guard actually exist there?
- **Is it on the path to the access?** A guard in an `else` branch that's never taken doesn't protect a later access. Check control flow.
- **Does it check the right variable?** The heuristic might flag a guard on `j` when the access uses `i`.
- **Is the operator direction correct?** `i > N` before the access doesn't prevent the access — it allows large values through. Only `i < N` or `i <= N` are upper-bound guards.

If the heuristic found valid guards, note them. If not, don't trust them. The heuristic has blind spots (see Step C).

#### Step C: Find guards the heuristic cannot see

The built-in heuristic only matches literal `var OP value` patterns in `if`/`for`/`while`
condition expressions. You must find what it misses. The following five categories are
systematically invisible to the heuristic — and systematically visible to you.

##### C1. Macro-based guards

The heuristic sees raw source text. When it encounters `CHECK_RANGE(i, 0, 256)`, it
sees a function-like macro call — no comparison operators in plain text — so it finds
**nothing**. But you can look up the macro definition in `context.relevant_macros`.

**Example — macro wrapping both bounds (SAFE):**
```c
#define CHECK_RANGE(idx, lo, hi) (((idx) >= (lo)) && ((idx) < (hi)))

void f(int i) {
    if (CHECK_RANGE(i, 0, 256)) {    // ← heuristic sees NO guard
        g_buf[i] = 42;               // ← you find: macro expands to both bounds → SAFE
    }
}
```
Look up `CHECK_RANGE` in `relevant_macros`: it expands to `((i) >= (0)) && ((i) < (256))`.
This covers lower bound (`i >= 0`) AND upper bound (`i < 256`). Array `g_buf[256]` —
threshold matches. → **SAFE**.

**Example — macro wrapping zero check (SAFE):**
```c
#define CHECK_NONZERO(v) ((v) != 0)

int div(int a, int b) {
    if (CHECK_NONZERO(b)) {          // ← heuristic sees NO guard
        return a / b;                // ← you find: macro expands to (b) != 0 → SAFE
    }
    return 0;
}
```
Look up `CHECK_NONZERO`: expands to `((b) != 0)`. The division at L4 is inside the
if-branch, only reached when `b != 0`. → **SAFE**.

**Example — macro with wrong semantics (RISKY):**
```c
#define ASSERT_NONNULL(p)  // ← empty! Debug-only, undefined in release

void f(int i) {
    ASSERT_NONNULL(ptr);
    g_buf[i] = 42;                   // ← macro is a no-op → no actual guard → RISKY
}
```
If you see a macro like `assert()`, `ASSERT()`, `DCHECK()` — check whether it compiles
to an actual check or is a no-op in release builds. If the macro definition is empty or
`#ifdef DEBUG`-gated, it is **not** an effective guard.

##### C2. Type-cast guard idiom — `(unsigned)var < N`

This is a classic C idiom that the heuristic completely fails on:
```c
void f(int i) {
    if ((unsigned int)i < 256) {     // ← heuristic sees: var="int", not var="i" → misses guard
        g_buf[i] = 42;               // ← you recognize: combined bounds check → SAFE
    }
}
```

**Why it works:** When `i` is negative (e.g., `-1`), casting to `unsigned int` produces
a very large value (e.g., `4294967295` on 32-bit), which fails the `< 256` check.
When `i >= 256`, it also fails. So `(unsigned)i < N` is logically equivalent to
`i >= 0 && i < N` — a single expression covering BOTH lower and upper bounds.

**Always treat `(unsigned)var < N` as a complete bounds guard** (both bounds) when:
- The cast is to an unsigned type of the same width (`(unsigned int)`, `(size_t)`, `(uint32_t)`).
- The comparison is `<` or `<=` against a constant or variable.
- The comparison threshold (N) is ≤ the array size. If the array is `g_buf[128]` and
  the guard is `(unsigned)i < 256`, the guard is too loose → still **RISKY**.

**When it's NOT a complete guard:** If the cast is `(unsigned char)i < 256`, negative
`i` values like `-1` become `255` (not huge), so the lower-bound protection is lost
on some platforms. For narrow unsigned types used as guards, mark as **DEFENSIVE**
unless the array is smaller than the type's range.

##### C3. Equivalent rewrites — rejection instead of admission

The heuristic only looks for `if (condition) { access; }` — a guard that admits valid
values. But C programmers often write the inverse: reject invalid values, then proceed.

**Example — early return as guard (SAFE):**
```c
void f(int i, int val) {
    if (i >= N) return;              // ← heuristic: this is NOT a guard pattern → misses it
    if (i < 0) return;               // ← heuristic: misses this too
    g_buf[i] = val;                  // ← you find: both invalid ranges rejected → SAFE
}
```
The `if (i >= N) return;` rejects out-of-range values before the access. The code after
these early returns is equivalent to `if (i >= 0 && i < N) { g_buf[i] = val; }`.
→ **SAFE** — both bounds protected.

**Equivalent rejection patterns you should recognize:**
- `if (i >= N) return;` — rejects upper OOB.
- `if (i < 0) return;` — rejects lower OOB.
- `if (i < 0 || i >= N) goto error;` — goto error path rejects both.
- `if (b == 0) return -EINVAL;` — rejects divisor zero.
- `if (i >= N || i < 0) { err = -1; goto out; }` — any error-jump that skips the access.

The key question: **do ALL paths that reach the access first pass through a check that
would have rejected invalid values?**

**Watch out — partial rejection (RISKY):**
```c
void f(int i, int val) {
    if (i >= N) return;              // ← only rejects upper OOB
    g_buf[i] = val;                  // ← i could still be negative → RISKY (missing lower bound)
}
```
Only the upper bound is rejected. `i = -1` still reaches the access. → **RISKY**.

##### C4. sizeof-based bounds

The heuristic can match `i < sizeof(arr)/sizeof(arr[0])` but may not understand the
semantics. You should recognize this as an upper-bound guard derived from the array's
own type:

```c
void f(int i, int val) {
    if (i < sizeof(g_buf)/sizeof(g_buf[0])) {   // ← derived upper bound
        g_buf[i] = val;                          // ← upper bound present; check lower bound too
    }
}
```
This is correct for the upper bound. But note: it only covers the upper bound.
If there's no lower bound check (`i >= 0` or `(unsigned)i`), mark **DEFENSIVE**
(or **RISKY** if `i` is externally controlled).

##### C5. Caller-side guards

If the function containing the access is `static` (file-scope only), and all its
callers are visible in the same file, check whether the callers validate the index
before calling.

**When caller-side guards are effective:**
```c
static void set_val(int i, int val) {    // ← static: only called within this file
    g_buf[i] = val;                      // ← no guard in this function
}

void public_api(int idx, int val) {
    if (idx >= 0 && idx < 256) {         // ← guard in the caller
        set_val(idx, val);               // ← all calls to set_val are guarded → SAFE
    }
}
```
`set_val` is `static`, its only caller is `public_api`, and `public_api` checks bounds
before calling. The guard in the caller is effective. → **SAFE**.

**When caller-side guards are NOT effective:**
```c
void set_val(int i, int val) {           // ← NOT static — could be called from anywhere
    g_buf[i] = val;                      // ← no guard, caller-side checks unknown → RISKY
}
```
If the function is not `static`, you cannot assume all callers are visible. Other
translation units may call it with arbitrary values. → **RISKY** unless you can prove
otherwise (you almost never can for non-static functions).

**When caller-side guards are uncertain:**
Even for `static` functions, if there are **multiple callers** and you can only see
guards in some of them, the unguarded caller makes the access vulnerable. → **RISKY**
or **NEEDS_VERIFICATION**.

##### C6. Pointer arithmetic — `*(ptr + offset)`

Pointer arithmetic `*(ptr + offset)` is semantically equivalent to `ptr[offset]`.
The recall layer now detects these patterns (kind: `pointer_arithmetic`). The
judgment follows the same logic as array access, but with an additional step:
you must determine what the base pointer points to and how large that target is.

**How to judge pointer arithmetic:**

1. **Identify the base pointer.** Read `func_source` and `pointer_declaration`
   (if present in the context). Where is the pointer assigned? Does it point to a
   static array (`int *p = g_buf`), a parameter (`void f(int *buf, ...)`), or
   a malloc'd block (`int *p = malloc(n * sizeof(int))`)?

2. **Determine target size.**
   - If the pointer is assigned from a named static/local array → you know the size.
   - If the pointer is a function parameter → size is unknown (DEFENSIVE or RISKY,
     depending on visibility of callers).
   - If from `malloc(n)` → size is `n`, but `n` may be runtime (DEFENSIVE).

3. **Check for bounds guards on the offset.** Same as array access — look for
   `offset >= 0 && offset < SIZE` or equivalent patterns (macro, type cast, early return).

4. **Watch for chained arithmetic.** `int *p = buf + n; *(p + idx) = 0;` — the
   effective index into `buf` is `n + idx`. Both need to be bounded.

**Example — safe (same function guard + known array):**
```c
int g_buf[256];

void f(int i, int val) {
    if (i >= 0 && i < 256) {
        *(g_buf + i) = val;    // ← pointer arithmetic, equivalent to g_buf[i]
    }                          // ← SAFE: guard visible, array size known
}
```
→ **SAFE** — `i >= 0 && i < 256` covers both bounds, `g_buf` is `int[256]`.

**Example — risky (no guard):**
```c
int g_buf[256];

void f(int i, int val) {
    *(g_buf + i) = val;        // ← no guard → RISKY
}
```
→ **RISKY** — `i` comes from parameter, no bounds check, array has visible size.

**Example — unknown target (pointer from parameter):**
```c
void f(int *buf, int idx, int val) {
    *(buf + idx) = val;        // ← buf is a parameter, target size unknown
}                              // ← DEFENSIVE (if internal-only) or RISKY
```
→ **DEFENSIVE** (if the function is `static` or internal-use) or **RISKY** (if
it's a public API with unknown callers). The pointer target size is invisible.

**Example — chained arithmetic (DEFENSIVE):**
```c
void f(int *buf, int n, int idx, int val) {
    int *p = buf + n;          // ← p now points n elements into buf
    *(p + idx) = val;          // ← effective index into buf is n + idx
}                              // ← DEFENSIVE: neither n nor idx is bounded
```
→ **DEFENSIVE** — both `n` and `idx` are parameters, no guards. If callers control
both, the effective offset `n + idx` could OOB the original buffer.

**Pointer arithmetic that is NOT detected by the recall layer:**
- `*ptr = val` (simple dereference without arithmetic — no index) — caught only
  if it's `ptr[index]` form
- `ptr->field = val` (struct member access — not array access)
- `**(ptr + i) = val` (double pointer dereference)

For these patterns, recommend manual grep or Joern CPG (`--recall deep`).

#### Step D: Compare guard thresholds to array size

Once you've identified all guards, check if their thresholds are correct against the
array from `context.array_declaration`.

| Guard pattern | Array size | Correct? | Verdict impact |
|---------------|-----------|----------|----------------|
| `i < 256` | `g_buf[256]` | ✓ Correct (0-255 valid) | SAFE (if both bounds covered) |
| `i <= 255` | `g_buf[256]` | ✓ Correct (0-255 valid) | SAFE (if both bounds covered) |
| `i <= 256` | `g_buf[256]` | ✗ Off-by-one — `i=256` is valid per guard but out of array bounds | **RISKY** — this is a real bug |
| `i < 17` | `g_buf[16]` | ✗ Guard 17 > array size 16 | **RISKY** — guard threshold exceeds array size |
| `i < sizeof(other)` | `g_buf[256]` | ✗ Wrong-guard — threshold from a different array | **RISKY** — wrong guard |

**Real-world wrong-guard example (stb pattern):**
```c
// From stb_image.h (simplified from CVE-2021-28021)
unsigned char buffer[16];
if (i < 17) {          // ← guard says 0-16 valid
    buffer[i] = x;     // ← but buffer only has 16 elements (0-15) → off-by-one!
}
```
The guard uses `17` but the array has `16` elements. `i=16` passes the check but
writes past the buffer end. This is a **real bug** — mark it **RISKY**, and it
should become **Confirmed** if ASan can trigger it.

---

### Verdict table — use these exact criteria

| Verdict | When to use | Example |
|---------|-------------|---------|
| **SAFE** | An effective guard exists that covers ALL required bounds: for arrays, both lower AND upper bound; for division, a non-zero check on the divisor. Guard threshold correctly matches array size. | `CHECK_RANGE(i,0,256)` → both bounds, correct size. `(unsigned)i < N` → both bounds via type system. `if (b==0) return -EINVAL; ... a / b` → zero check via early return. |
| **RISKY** | No guard exists, OR the guard is incomplete (missing one bound, wrong variable, wrong threshold), AND the critical variable could plausibly be attacker-controlled or come from external input. | No guard on `i` at all. Only upper bound check, no lower bound. Guard `i < 17` on array of size 16. Guard checks wrong variable. |
| **DEFENSIVE** | No explicit guard, but exploitation is unlikely in practice. Use this when the variable is set from a trusted/internal source, is a small constant offset on a verified-safe base, or the access is deep in an internal call chain with implicit invariants. "Missing check, but probably safe — hardening note." | Division by a variable set to `rand() % 100 + 1` (guaranteed non-zero by construction). Index derived from a `for (i=0; i<N; i++)` loop counter (implicitly bounded). |
| **NEEDS_VERIFICATION** | Context is insufficient for a confident judgment. For example: you'd need to see a caller that is not included in the file; the array size is from an extern declaration not visible; complex pointer arithmetic beyond what the context provides. Explain what extra context would help. The ASan verify layer acts as a tiebreaker. | `extern int g_buf[];` — array size unknown. Non-static function with unknown callers. Complex expression like `arr[fn(x)-1]` where the return range of `fn()` is unknown. |

---

### Anti-hallucination rules

These are **mandatory** — violations produce wrong results.

1. **Do NOT invent guards.** If you cannot see a guard in the source code, do not assume one exists. "The developer probably checked it" is not a valid judgment. If the source doesn't show a guard, the point is at minimum DEFENSIVE, likely RISKY.

2. **When torn between RISKY and DEFENSIVE, choose RISKY.** A false positive (flagging safe code) costs a developer 2 minutes of review. A false negative (missing a real bug) costs an exploit. Err on the side of caution.

3. **Cite specific line numbers in every reasoning.** Write `"L47: if ((unsigned int)i < 256) covers both bounds via unsigned cast"` — not `"there's an unsigned check"`. If the code changes, precise citations make your judgment auditable.

4. **If you genuinely cannot decide, use NEEDS_VERIFICATION.** Don't guess. Say what extra information would help (e.g., "Need to see the caller at file2.c:130 to determine if `idx` is bounds-checked before calling this function"). The ASan verify layer can serve as a tiebreaker.

5. **Read the macro definitions yourself — don't trust the name.** `CHECK_RANGE` could expand to bounds checks, or it could be `#define CHECK_RANGE(x,lo,hi) ((void)0)`. Always look at the actual definition in `context.relevant_macros`.

6. **Control flow matters.** A guard in an unrelated `if` branch that doesn't dominate the access is not a guard. A guard after the access is not a guard. Only checks that are on ALL paths from function entry to this access count.

7. **Never use shallow pattern-matching. Always read the actual source.** Do NOT judge points by regex-matching `heuristic_reason` strings (e.g., "constant divisor" → SAFE, "constant index" → SAFE), by trusting `heuristic_verdict` on low confidence, or by counting `guards_found_by_heuristic`. These metadata fields are heuristic best-effort labels that are WRONG 30-50% of the time on uncertain points. You MUST open the actual `.c`/`.cpp`/`.h` file and read the source around the access line for every point that matches any [red flag pattern](#red-flags--these-patterns-must-trigger-reading-the-actual-source-file). Shallow mode misses bugs (e.g., `m_values[length()-1]` with no empty check, `rList[m_rIndex]` with no bounds check, `m_points[segmentIndex]` where segmentIndex can equal length). Each missed bug is a potential exploit.

---

## Three-tier report

The report is an **AI code review, not a tool log**. Every finding should read like
a colleague reviewing your code. The CLI renders the structural skeleton; you, the
agent, provide the intelligence that fills it.

### Report structure

```
## 1. 扫描概览
   - 扫描了多少文件、多少检查点（数组访问 + 除/取模 + 指针算术）
   - 判定模式（agent/heuristic/ai）+ 判定者身份
   - 验证是否开启
   - 各阶段耗时

## 2. 🔴 Confirmed — X 个 ASan 验证的真实漏洞
   每一条：
   ├── 我看到了什么（源码片段 + 行号标注 + 箭头指向漏洞行）
   ├── 为什么这是漏洞（AI 根因分析，完整推理链路，不截断）
   ├── ASan 怎么验证的（crash 原始输出，不截断）
   ├── 怎么修（可粘贴的代码 diff / before-after）
   └── 严重度 + CWE 编号

## 3. 🟠 Likely — Y 个我判定可疑但无法复现的
   每一条：
   ├── 我看到了什么
   ├── 为什么我觉得有问题（AI 判定理由全文）
   ├── 为什么 ASan 没触发（缺入口 / 需特定输入 / 编译失败 / …）
   └── 人工审查清单（按漏洞种类给 checkbox）

## 4. 🟡 Defensive — Z 个缺守卫但暂时安全的
   - 汇总表：位置、表达式、缺了什么
   - 建议加固，但优先级低

## 5. ✅ SAFE — N 个我确认安全的
   - 数量 + 挑 2-3 个典型例子展示 AI 判断能力
   - 例如："L34 `g_buf[i]` — CHECK_RANGE(i,0,256) macro 展开覆盖了上下界"

## 6. 我看不到的盲区
   - 跨文件远守卫、malloc 数组、C++、双重指针
   - 如果这里出问题该用什么工具补查
```

### Presenting results to the user

This is the core output of the skill. Write every finding as if you're talking to
a colleague — **this is a conversation, not a log dump**.

**When presenting each Confirmed finding, include ALL of:**

1. **Source context**: The actual code around the vulnerability line, with line numbers, with `→` marker pointing at the vulnerable line. Never just give `file:line` and make the developer open the file themselves.
2. **Your reasoning**: Why you believe this is a bug. Your full judgment from judged.json, not a one-line summary. Cite specific line numbers. Reference the guard patterns you checked (macro, type cast, equivalent rewrite, caller-side).
3. **ASan evidence**: The raw ASan/UBSan output, un-truncated. This is the proof — show it.
4. **Fix**: Concrete code. `before → after` diff, or the exact lines to add. Not "add a bounds check" — write the bounds check.
5. **Severity + CWE**: How bad this is and which CWE it maps to.

**For Likely findings:**
- Show the source, your full reasoning, and a checklist of what the developer should look for during manual review.
- Explain WHY ASan couldn't verify (missing `main()`, needs specific input, compilation error, etc.).

**For SAFE examples:**
- Pick 2-3 representative cases and briefly explain what guard you found. This builds trust — the developer sees you actually understood the code.

**End every presentation with the scope limitations.** Never imply you found "all" bugs.

Example of the right level of detail:

> ---
> ### C-1: `cd.c:347` — 数组下界越界写入
>
> **源码上下文**:
> ```c
>  342  void track_set_index(Track *track, int i, int val) {
>  343      if (i > MAXINDEX) return;    // ← 只检查了上界
>  344      // 缺少 i < 0 的检查
> →345      track->index[i] = val;       // ← i 可为负，越界写入
>  346  }
> ```
>
> **我的判断**: L343 的 `if (i > MAXINDEX) return` 只覆盖了上界。文件里没有 macro、没有 `(unsigned)i` 惯用法、没有 `i < 0` 的等价改写。`i` 从函数参数直接进入，调用者可能传入负值。
>
> **ASan 验证了我的判断**:
> ```
> ==12345==ERROR: AddressSanitizer: heap-buffer-overflow
> WRITE of size 4 at 0x... thread T0
>     #0 track_set_index cd.c:345
> ```
>
> **修它**:
> ```c
> // 改 L343:
> -    if (i > MAXINDEX) return;
> +    if (i < 0 || i > MAXINDEX) return;
> ```
>
> **严重度**: 🔴 高危 — 越界写入可导致任意代码执行 | **CWE-787**
>
> ---

### Report file location

All outputs for a scan run are stored under:
`<project-dir>/.oob-divzero/<YYYYMMDD_HHMMSS>/`

| File | Description |
|------|-------------|
| `pending.json` | Points awaiting judgment |
| `judged.json` | Your verdicts |
| `oob-divzero-report.html` | Final report (self-contained, open in browser) |

**After Step 4, tell the user where the task directory is.** The user can
open the report, commit the directory as an audit artifact, or share it.

---

## Scope boundaries

**You MUST mention these limitations when presenting results.** Do not overstate coverage.

### Covers

- Array accesses (`arr[idx]`) where the bounds check is within the same function (~25 lines up).
- Division/modulo operations (`/`, `%`) with same-function zero checks.
- Pointer arithmetic (`*(ptr + offset)`) — equivalent to `ptr[offset]`. The recall layer
  detects these patterns and the agent judges them using the same bounds-check logic (see
  [C6. Pointer arithmetic](#c6-pointer-arithmetic--ptr--offset)).
- Arrays whose size is visible in the same source file (static arrays, `#define`-sized arrays, local arrays).
- Simple index/divisor variables (not complex expressions involving function calls).
- Macro-based guards (via your judgment of macro definitions).
- Type-cast guards (`(unsigned)i < N`).
- Equivalent rewrites (early return, goto error).
- Caller-side guards for `static` functions with visible callers.

### Does NOT cover

- **Far-guard scenarios** — guards more than ~3 call levels away from the access. The tool only searches ~25 lines within the same function for heuristics, and the context only includes the immediate function. If the guard is in a grandparent caller, it won't be visible.
- **Complex cross-function pointer flows** — when a pointer is passed through multiple functions and the original array size is lost across translation units, the agent cannot determine the target size (though it can still judge the offset guard).
- **Runtime-sized arrays** — arrays allocated with `malloc(n)` or declared `extern` with unknown size. The tool can find the access but cannot determine the array size, so threshold comparison (Step D) is impossible.
- **Double-pointer arithmetic** — `**(ptr + i)` patterns are not detected by the recall layer.
- **C++ code** — tree-sitter-c can parse C++ syntax for recall, but ASan verification requires C compilation and is not available for C++.

### Recommendations for uncovered cases

| Scenario | Recommendation |
|----------|---------------|
| Far-guard / deep call chains | CBMC (bounded model checking), manual review |
| Complex pointer flows | Joern CPG + dataflow analysis |
| Runtime-sized arrays | ASan fuzzing with libFuzzer or AFL |
| Pointer arithmetic | `--recall deep` with Joern (if available), or manual grep for `*(ptr+` |
| General symbolic verification | KLEE (symbolic execution) for path coverage |

---

## Fallback modes (only when no agent judgment is available)

These modes run without your semantic judgment. They are fast but will miss macro guards,
type-cast guards, and equivalent rewrites. Use only when you (the agent) are unavailable.

```bash
# Heuristic only — ~13ms/point, no model, no API key.
# Misses: macros, type guards, equivalent rewrites, caller-side guards.
oob-divzero scan <target> --judge fast --verify off

# CLI calls an LLM itself — requires OPENAI_API_KEY.
# This is a fallback for non-agent command-line use.
# NOT the primary path — the primary path borrows your judgment (no key needed).
oob-divzero scan <target> --judge ai
```

The default `--judge auto` detects the environment: agent context → agent mode,
`OPENAI_API_KEY` set → ai mode, otherwise → fast mode.

---

## CLI reference — complete

### `scan` subcommand

```bash
oob-divzero scan <target> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `<target>` | required | File (`.c`), directory (recursive), or git URL |
| `--judge auto` | `auto` | **Judge mode.** `auto` = detect environment; `fast` = heuristic only; `ai` = CLI calls LLM (needs `OPENAI_API_KEY`); `agent` = agent two-step flow (main path); `hybrid` = heuristic + AI for uncertain |
| `--recall fast` | `fast` | **Recall depth.** `fast` = tree-sitter only; `deep` = +Joern CPG (needs `joern` on PATH) |
| `--verify auto` | `auto` | **Verification mode.** `auto` = ASan when possible; `off` = skip compilation |
| `--emit-pending <file>` | auto | With `--judge agent`: write pending.json to `<file>` and exit. Defaults to `<project>/.oob-divzero/<datetime>/pending.json` |
| `--format html` | `html` | **Report format.** `html` = self-contained HTML (default); `md` = markdown; `json` = JSON |
| `-o <file>` | auto | Write report to `<file>`. Defaults to `<task-dir>/oob-divzero-report.html` |
| `--confirmed-only` | off | Only output ASan-confirmed results (suppress Likely/Defensive) |
| `--exclude <glob>` | — | Exclude files/directories matching glob pattern (repeatable) |

### `resume` subcommand

```bash
oob-divzero resume --pending <file> --judged <file> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--pending <file>` | required | Pending JSON from `scan --judge agent` |
| `--judged <file>` | required | Your judged.json from Step 2 |
| `--verify auto` | `auto` | Verification mode (same as scan) |
| `--format html` | `html` | Report format (`html`, `md`, or `json`) |
| `-o <file>` | auto | Write report to `<file>`. Defaults to same directory as `--pending` |

### Large-project workflow

For projects with hundreds of `.c` files, `--verify auto` can be slow because it
compiles each file with sanitizers. Recommended approach:

```bash
# Phase 1: Quick scan — find RISKY points (no compilation)
oob-divzero scan <project-dir> --verify off -o phase1.md

# Phase 2: Focused verification — only compile files that have RISKY points
# (Identify those files from phase1.md, then re-scan them individually)
oob-divzero scan <risky-file>.c --verify auto -o phase2_report.md
```

---

## Installation

If `oob-divzero` is not on PATH:

```bash
git clone <repo-url> oob-divzero
cd oob-divzero/cli
uv tool install -e .      # installs global `oob-divzero` command
```

Requires Python >= 3.10. For verification (`--verify auto`), a C compiler (`cc`/`gcc`/`clang`) is needed.

---

## Reference files

| File | Purpose |
|------|---------|
| `references/scope-limitations.md` | Detailed scope boundary examples with code snippets |
