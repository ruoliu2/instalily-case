# Agent Behavior Challenges And Resolutions

This document summarizes the major agent-behavior issues encountered during implementation, how each issue was diagnosed, and what changes were made to resolve it.

## 1) Multi-turn memory was effectively missing
- What happened:
  - Follow-up turns behaved as if they were new chats.
  - The model asked for information that had already been provided in prior turns.
- Root cause:
  - Backend endpoints accepted `history` but did not pass it into the agent execution path.
  - Agent message construction only used the current user message.
- How we identified it:
  - Repro with short multi-turn flows (`model -> follow-up -> clarification`) consistently reset context.
  - Code inspection in `instalily-case-backend/app/main.py` and `instalily-case-backend/app/agent.py`.
- Resolution:
  - Passed `payload.history` into both `/chat` and `/chat/stream`.
  - Added normalized history handling and safe message construction in agent.
  - Added duplicate-current-user protection in message builder.

## 2) Forced live-check overrode model control
- What happened:
  - Even when model had enough evidence, backend still triggered a hard-coded live crawl.
- Root cause:
  - A forced branch after main loop (`_wants_live_lookup` + `not used_live_tool`) executed regardless of model finalization.
- How we identified it:
  - Trace showed mandatory "Live-check requirement" step after model had drafted final response.
- Resolution:
  - Removed forced live-check branch.
  - Tool usage is now model-decided inside the loop.

## 3) Thinking stream became too verbose and speculative
- What happened:
  - Long planning text with speculative tool signatures/params and invented URL patterns.
- Root cause:
  - Decision/tool-step thinking used model-generated reasoning calls that were unconstrained.
- How we identified it:
  - Streamed traces included incorrect pseudo-signatures (e.g., non-existent params) and noisy reasoning blocks.
- Resolution:
  - Converted decision-step visibility to deterministic debug tokens.
  - Kept tool-step observability but removed speculative heavy reasoning.
  - Kept "thinking token" UX while minimizing latency and hallucinated debug text.

## 4) Thinking text cutoff/truncation
- What happened:
  - Reasoning text cut off mid-sentence.
- Root cause:
  - `max_output_tokens` cap on step-thinking stream.
- How we identified it:
  - Repro in repeated runs: sentence endings clipped around same length.
- Resolution:
  - Removed token cap per user preference.
  - Kept timeout guard to avoid hangs.

## 5) Final answer looked non-streaming
- What happened:
  - Response rendered in large chunks or near all-at-once.
- Root cause:
  - Final response was generated non-streaming and chunked afterward.
- How we identified it:
  - Backend event pattern: no model delta events, only post-hoc token chunks.
- Resolution:
  - Restored true streaming final synthesis via `responses.create(..., stream=True)`.
  - Forwarded `response.output_text.delta` directly to frontend as token events.

## 6) MCP live crawl blocked by target (`Access Denied`)
- What happened:
  - Live check returned "Access Denied" snapshots.
- Root cause:
  - Browser launch profile/flags were rejected by site protection.
- How we identified it:
  - Direct MCP runner tests confirmed page title and snapshot content showed explicit block.
  - Controlled flag permutations identified working profiles.
- Resolution:
  - Updated MCP launch config and validated by direct runner tests.
  - Added clearer blocked-state handling in agent traces/messages.

## 7) Wrong/irrelevant crawl seeds and speculative URLs
- What happened:
  - Agent used unrelated hub URLs or invented deep/pagination paths.
- Root cause:
  - Weak runtime URL anchoring; model speculation in loops.
- How we identified it:
  - Tool traces showed seeds like non-canonical categories and guessed `?page=N` paths.
- Resolution:
  - Hardened live-crawl arg normalization:
    - enforce in-domain PartSelect URL,
    - when model is known, anchor to canonical model page (`/Models/<MODEL>/`) unless already model/part detail page.

## 8) Crawl depth too shallow caused repeated loops
- What happened:
  - Model repeatedly invoked crawl to discover next pages.
- Root cause:
  - Runner originally captured only initial page and one search result.
- How we identified it:
  - Loop traces showed same narrow result shape (`pages=1/2`) across many loops.
- Resolution:
  - Implemented in-run multi-page link discovery (BFS) in MCP runner.
  - Added visited/frontier dedupe and in-domain filtering.
  - Increased max crawl cap to 6 end-to-end.

## 9) Malformed and duplicate discovered links
- What happened:
  - Result set included malformed fragment links and duplicate URLs.
- Root cause:
  - Raw snapshot link extraction without canonicalization.
- How we identified it:
  - Discovery output contained malformed `%22#...` style links.
- Resolution:
  - Added URL canonicalization and fragment cleanup.
  - Avoided duplicate document insertion when URL already seen.

## 10) Loop convergence failures (repeated crawl calls)
- What happened:
  - Agent repeated tool calls until hitting step limit for some prompts.
- Root cause:
  - Model had history but weak progress signal; no explicit convergence cue.
- How we identified it:
  - Long runs with repeated same-intent crawl calls and low incremental gain.
- Resolution (mini-swe style):
  - Kept full action-observation trajectory (no hard tool dedupe by default).
  - Added explicit progress signals in loop context:
    - `repeated_no_new_info`
    - `repeated_same_crawl_call`
  - Added finalize-on-stall behavior when progress counters hit threshold.

## 11) Link-only fallback responses for "list all parts"
- What happened:
  - Responses returned source links without concrete part data.
- Root cause:
  - Fallback synthesis did not extract identifiers from retrieved content.
- How we identified it:
  - User prompt requesting part list returned only URLs and generic guidance.
- Resolution:
  - Added extraction/tracking of PartSelect IDs (`PSxxxxx`) from crawled text and URLs.
  - Updated fallback to return concrete part IDs when list-like query intent is detected.

## 12) Fabricated part details risk in final synthesis
- What happened:
  - Some answers included values not clearly grounded in retrieved data.
- Root cause:
  - Unconstrained synthesis can over-generalize.
- How we identified it:
  - Output values occasionally lacked direct evidence from tool payload.
- Resolution:
  - Added synthesis guardrails and stronger grounding context in synthesis prompt.
  - Included allowed/retrieved identifiers in context and explicit anti-fabrication instruction.

## 13) Thinking panel UX behavior mismatch
- What happened:
  - Thinking collapsed too early during streaming.
- Root cause:
  - Frontend collapse was triggered on first answer token.
- How we identified it:
  - UI behavior inconsistent with desired "expand while streaming, collapse on done."
- Resolution:
  - Updated frontend logic:
    - auto-expand during run,
    - auto-collapse on done,
    - preserve manual collapse capability during stream.

## 14) Regression risk from rapid iteration
- What happened:
  - Frequent behavior changes caused reintroductions of similar bugs.
- Root cause:
  - Missing targeted loop tests and prompt coverage.
- How we identified it:
  - Repeated incidents across memory, loop, and streaming behavior.
- Resolution:
  - Added backend tests under `instalily-case-backend/tests/`.
  - Added requirement-derived question set for repeatable validation.

---

## Current design principles after fixes
- Model-driven loop (no unconditional forced tool branch).
- Full trajectory retained (assistant/tool messages appended each turn).
- Rich but deterministic observability in step traces.
- Crawl runner performs discovery in one call instead of relying on model URL guessing.
- Convergence protections focus on "no new information" signals instead of hard blocking all repeated calls.

