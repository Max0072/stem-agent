# Stem Agent — SQL Bug Detection

## Experiment Knowledge Base

## 1. Domain & Task Selection

Task class: SQL Correctness Bug Detection. Given a SQL query with intentionally introduced bugs, the agent must find all of them.

Why this domain:

- Clear ground truth — each bug has an unambiguous correct answer
- Real difficulty at baseline — the model knows SQL syntax but misses ~30% of bugs on first run; the problem is not lack of knowledge but failure to apply rules to specific patterns
- Representative of a class — SQL bug detection is a special case of code review; the approach transfers to Python, Rust, any language with formal semantics

## 2. Benchmark

30 SQL queries, each containing exactly 3 intentionally introduced bugs. Total: 90 evaluation points.

Bug categories covered:

- WHERE vs HAVING (aggregate functions in WHERE clause)
- NULL handling — NOT IN with NULLs, IS NULL vs = NULL, COALESCE
- Window functions — RANK() in HAVING, wrong OVER() partition, frame specification
- Integer division truncation
- JOIN type errors — INNER vs LEFT JOIN, self-join without deduplication
- Set operations — UNION instead of EXCEPT
- Alias scope — referencing aliases in WHERE/HAVING
- Correlated subqueries converting LEFT JOIN to INNER JOIN
- Temporal logic — missing year filter, sargability, MONTH() without YEAR()
- Idempotency and transaction safety in UPDATE/DELETE

Evaluation metric: LLM-as-judge (gpt-4o-mini, temperature=0). For each bug, the judge checks whether the agent's response identified it. Score = bugs found / total bugs.

Note on judge noise: The judge itself runs on gpt-4o-mini and can make errors — particularly when the agent correctly identifies a bug but phrases it differently from the ground truth. This adds noise on top of scraping noise.

## 3. Architecture

Two components: Scraper and StemAgent.

### 3.1 Scraper

- Accepts task class name and current weak areas (failed tasks from last evaluation)
- Generates targeted search queries via LLM — queries focus on specific failure patterns, not general overviews
- Searches DuckDuckGo, fetches pages via Jina Reader (up to 10,000 chars per page)
- Summarizes raw text into actionable insights — concrete rules the agent can apply directly
- Existing knowledge passed in to avoid redundant queries

### 3.2 StemAgent

Stores: knowledge_base, system prompt, step-by-step reasoning workflow, score history, weak areas.

Evolution loop per iteration:

- Scrape — gather new knowledge targeted at current failures
- Update prompt — rewrite system prompt from scratch using full knowledge base
- Update workflow — regenerate structured chain-of-thought steps
- Evaluate — score on full benchmark, record failed tasks as weak_areas
- Early stopping — stop if score hasn't improved in 3 iterations (patience=3) or reaches 1.0
- Checkpoint — save last and best checkpoints separately

### 3.3 Key Design Properties

- Agent accepts only the task class name as input — no pre-wired logic, no hand-crafted rules
- Only feedback: pass/fail per task. No correct answer shown, no explanation of what was missed
- Agent self-directs research based on its own failures — generates queries, scrapes, summarizes without human involvement
- Knowledge base is append-only — each iteration adds a new summary on top; nothing is deleted
- Prompt is rewritten from scratch each iteration from the full knowledge base
- Workflow regenerated each iteration as structured CoT
- Architecture is domain-agnostic — task_class is just a string; same code runs on any domain
- After early stopping, agent is fixed — does not continue changing during task execution

## 4. Experimental Results

### 4.1 Score Trajectories

Run 1 (30 tasks): 0.66 → 0.62 → 0.63 → 0.59, early stop. Best: 0.66 (baseline)

Run 2 (30 tasks): 0.70 → 0.60 → 0.66 → 0.63 → 0.59, early stop. Best: 0.70 (baseline)

In both runs, the best score was achieved before any iteration — the agent never exceeded its starting point.

### 4.2 What Failed First: Preloading Context

Before building the evolution loop, an attempt was made to simply preload the model with general domain context. Score did not increase. The model already knew general SQL rules. The problem was not absence of knowledge but failure to apply it to specific error patterns. This led to the current approach: show the model which specific tasks it failed, and iteratively build knowledge around those failures.

### 4.3 Web Scraping as Active Source of Degradation

Web scraping is not just an unreliable knowledge source — it is an active source of prompt degradation. The agent has no control over what the search returns.

Specific irrelevant URLs that contaminated the knowledge base in Run 2:

- gitnation.com/events/react-advanced-conference-2021 (React conference)
- docs.datadoghq.com/synthetics (Datadog monitoring)
- persana.ai/blogs/sales-conversion-rate (marketing blog about conversion rates)
- theb2bmarketer.pro/5-common-roi-mistakes (B2B marketing)

This content enters the knowledge base, gets summarized together with relevant pages, and the prompt is rewritten incorporating it. Direct evidence: the prompt after iteration 1 contains an entire section on "Incremental View Maintenance (IVM)" with subsections on refresh strategies, query fingerprinting, materialized views — a concept with no relevance to SQL bug detection. It arrived from irrelevant pages and stays permanently because the knowledge base is append-only.

Score after iteration 1 drops from 0.70 to 0.60 — this is not variance, this is a direct consequence of a noised prompt.

### 4.4 Score Oscillation as Diagnostic Signal

Score drop after scraping = irrelevant content entered the knowledge base. Partial recovery on the next iteration = the next scrape happened to return more relevant pages. But accumulated noise never leaves, and the trend is downward. This pattern reproduces in both runs.

### 4.5 Tasks Persistently Unsolved Across All Iterations

The following tasks were never solved in either run across all 4 iterations:

- Conversion rate query — alias 'u' referenced but table 'users' never joined
- UNION instead of EXCEPT — should use EXCEPT for January-not-February logic
- Window function in HAVING — RANK() in HAVING is a syntax error in standard SQL
- Salesperson percentage with wrong OVER() — should be OVER(PARTITION BY region)
- WHERE instead of HAVING for aggregates — COUNT(*) used in WHERE clause

Scraping does not help with these specific patterns — the web does not return pinpoint insights about these edge cases, and the prompt degrades from noise.

### 4.6 Why Prompt Rewriting Amplifies the Problem

The prompt is rewritten from scratch every iteration using the full knowledge base. This means noise from the knowledge base is guaranteed to appear in every subsequent prompt. A good prompt structure from iteration N can be replaced by a worse one in N+1 — with no way to detect this because the evaluation is also noised.

## 5. Prompt Evolution (Run 2)

**Baseline prompt (before iteration 1)**

"You are a general assistant. Answer questions as best you can."

**After iteration 1 (score dropped 0.70 → 0.60)**

Prompt grew to include three structured sections. The damage: a full "Incremental View Maintenance (IVM)" section appeared with subsections on refresh strategies, query fingerprinting, and materialized views — none of which relate to finding bugs in SQL queries. This came from irrelevant scraped pages.

Workflow after iteration 1 focused on: checking = NULL usage, aggregate function NULL handling, JOIN analysis, schema default values, testing framework implementation. All generic — none targeted at the specific patterns the agent was failing.

**After iteration 2 (score recovered partially to 0.66)**

Prompt expanded further with AST analysis, SQL injection checks, multi-plan execution validation. Increasingly abstract and disconnected from the task of reading a SQL query and listing concrete bugs. The IVM section persisted.

**After iterations 3–4 (score fell to 0.63, then 0.59)**

Structure stabilized but the noise accumulated. Workflow remained generic (check = NULL, validate schema, run incremental checks) while the specific bugs the agent kept missing required targeted pattern knowledge that the web never provided.

## 6. What Would Fix It

### 6.1 Replace Web Scraping with Self-Reflection (primary fix)

After each failure: ask the model "you produced this response, you missed this bug — what general rule would have caught it?" The knowledge source becomes precise, noise-free, and directly targeted at the specific failure pattern.

This is not experimentally verified but the logic is direct: the problem is the knowledge source, not the learning mechanism.

### 6.2 Accumulative Prompt Instead of Full Rewrite

Instead of rewriting the prompt from scratch each iteration, append new rules to a stable base. This prevents good prompt structure from being overwritten by noise-contaminated rewrites.

### 6.3 Knowledge Base Pruning

Track correlation between knowledge chunks and correct answers. Remove chunks that do not correlate with any improvement over multiple iterations. This would prevent permanent accumulation of irrelevant content.

### 6.4 Load Best Checkpoint After Early Stopping

Currently, early stopping saves the last checkpoint. Since score trends downward after degradation, the last checkpoint is the worst. After stopping, the system should automatically load checkpoint_best.pkl.

### 6.5 Separate Evaluation from Knowledge Noise

When both the agent and the judge run on the same model family, judge noise is correlated with agent noise. A stronger, different judge model would give cleaner signal on whether a given iteration actually improved.

## 7. Practical Notes

- Approximate runtime: ~10 minutes per full run (4 iterations) with parallelized evaluation (ThreadPoolExecutor, max_workers=10)
- Model: gpt-4o-mini for all components — agent, scraper, summarizer, judge
- Parallelism: evaluate() runs all 30 benchmark items concurrently
- Checkpointing: saves checkpoint.pkl (last) and checkpoint_best.pkl (best score) after each iteration
- Evaluation log: eval_log.json accumulates all judge outputs across all iterations for post-hoc analysis