## Tuning Agent Limits: Amala Test Generation

### The Problem
During test generation, the `amala` agent may occasionally fail to output the mandatory `<state_update>` block at the end of its response. This is typically caused by **context overflow**: when the agent writes too many tests (e.g., 50+) in a single iteration, the combination of source code, test code, and error logs fills the context window, causing the model to "forget" the formatting instruction.

### The Default Limit
By default, the `amala.md` prompt instructs the agent to write a maximum of **30-40 tests per iteration**. This limit is calibrated for the most capable *free* models currently available in the OpenCode zen-pool.

### Adjusting for More Capable Models
If you are using a paid tier or a locally hosted model with a significantly larger context window and stronger instruction-following capabilities, this limit may be unnecessarily restrictive. 

**How to adjust:**
1. Open `agents/amala.md`.
2. Locate the "Work Pacing (Critical)" section.
3. Increase the `maximum of 30-40 tests` to a higher value (e.g., `60-80 tests`).
4. Monitor the execution logs. If `amala` consistently completes iterations with the new limit and reliably outputs the `<state_update>` block, the new limit is safe for your model.