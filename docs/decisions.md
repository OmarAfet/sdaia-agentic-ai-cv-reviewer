# Architecture Decision Records

Format: Nygard (2011). Dates: ISO 8601.

## ADR-001: LangGraph + Groq, the course stack (2026-11-18)
**Context.** The course labs use LangGraph and `langchain-groq`. The lab model
`llama-3.3-70b-versatile` is no longer listed by the Groq `/models` endpoint.
**Decision.** LangGraph `StateGraph` for orchestration; `openai/gpt-oss-120b` as
the primary model.
**Consequence.** The free tier allows 8,000 tokens/min on gpt-oss-120b. One CV uses
about 5,000 tokens, so runs back to back hit HTTP 429 (see ADR-003).

## ADR-002: Structured output method per model (2026-11-18)
**Context.** A probe with the MatchResult schema (2 calls per cell):
gpt-oss-120b `json_schema` returned `parsed=None` once; gpt-oss-20b
`function_calling` failed 2/2 (`output_parse_failed`) and `json_schema` passed 2/2;
qwen3.8-27b `function_calling` passed 2/2.
**Decision.** Model chain: 120b/function_calling, 20b/json_schema, qwen/function_calling.
**Consequence.** Small sample. Re-probe if the models change.

## ADR-003: Retry, rate-limit wait and model fallback in our code (2026-11-18)
**Context.** ChatGroq can retry internally, but then the attempts are not logged.
**Decision.** `max_retries=0` in ChatGroq. `GroqLLM` retries 2 times per model.
On 429 it waits the time Groq returns (up to 30 s), else it moves to the next
model (each model has its own rate-limit bucket). When all models fail, the node
sets `status=system_error` and the run ends with a report, not a crash.

## ADR-004: Three-layer injection detection; quarantine, do not sanitise (2026-11-18)
**Context.** In the live test, Prompt Guard 2 scored a polite, rephrased
instruction (`data/cvs/injection_subtle.txt`) at 0.0038. gpt-oss-safeguard-20b
with a CV policy labelled it 1. It labelled 4/4 clean sample CVs 0 (n=6, our own
files, not a benchmark).
**Decision.** Layer 1 regex, layer 2 Prompt Guard, layer 3 safeguard policy. Any
hit sends the CV to human review; the scoring agents never see it. Hidden text
(white, under 3 pt, invisible render mode, Word hidden runs) is never given to
the agents.
**Why quarantine.** Removing the attack text and scoring the rest still rewards a
candidate who tried to cheat, and a removal step can miss part of the attack.
**Consequence.** Layers 2 and 3 fail open (the report shows "unavailable"); the
regex layer always runs. Safeguard allows 3 requests/min on the free tier.

## ADR-005: Mask PII and protected attributes before the first LLM call (2026-11-18)
**Decision.** Regex masking of name, email, phone, Saudi national ID/Iqama, IBAN,
URLs, and labelled protected attributes (gender, age/DOB, nationality, religion,
marital status). The report is masked again on output.
**Why.** The model cannot discriminate on, or leak, data it never receives.
**Consequence.** Regex masking can miss unlabelled data (for example a name in
the middle of a sentence). The bias output filter in the Reviewer is the second
control.

## ADR-006: Deterministic anchor score (2026-11-18)
**Decision.** A keyword skill-match tool computes `tool_score`. The Reviewer
rejects a Matcher score that differs by more than 25 points without a reason.
**Why.** It limits how far a persuasive CV or a hallucination can move the score.
**Consequence.** The tool is keyword-based ("Flask API" is not "REST API"). The
prompts allow the Matcher to drop a tool gap if it cites evidence (seen in a
live run on 2026-11-18, where the Reviewer contradicted itself without this rule).

## ADR-007: Human approval for every final decision (2026-11-18)
**Decision.** LangGraph `interrupt()` in `hr_review`. Shortlist, reject, quarantine
and escalation all wait for a human. The human can override; the override is logged.
**Why.** Automated hiring decisions are high-risk; a person must own them.
