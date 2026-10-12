# Agent Reliability Investigator — MVP brief v0.1

Owner and first user: SD. The original product scope is a proposal; implementation and validation must be reported separately. This repository is limited to the first local milestone.

## Product decision

Build a local developer tool that investigates discrepancies between an agent's reported actions and independently observed results in a controlled test environment.

> Show me whether my agent actually did what it said, give me the evidence, and help me reproduce a confirmed failure.

The first job is an engineer testing an independently owned, tool-using agent before trusting its state-changing workflow. Start with sending one already-approved synthetic message through a simulated email service and one failure family: false completion claims.

For this sandbox, “sent” means a matching delivery was committed to the controlled local mailbox. It does not mean real email acceptance, real-world delivery, reading, or business impact. No customer data or production access is required for the prototype.

## Required behavior

- Run an explicitly supported local target with synthetic inputs and resettable state.
- Preserve exact requests, responses, target versions, scenario configuration, tool events, runtime identifiers, evidence, and verification results.
- Read independent service-owned delivery records correlated to the task and operation.
- Distinguish claim verification (`PASS`, `FAIL`, `UNVERIFIED`, `NOT_APPLICABLE`) from task outcome (`ACHIEVED`, `NOT_ACHIEVED`, `UNKNOWN`).
- Settle operations and establish record completeness before treating zero deliveries as a confirmed absence. Missing evidence or a timeout alone is insufficient.
- Preserve supporting response spans; ambiguous interpretation must remain unverified.
- Separate confirmed mismatches from explanatory hypotheses. Never invent files, lines, events, or receipts.
- Reproduce through fresh executions from clean state and retain all attempt results.
- Export regression scenarios and assertions through fixed, reviewed templates, without unrestricted generated code.

The canonical acceptance cases are ordinary success, false completion after rejection, honestly reported rejection, lost acknowledgement after commit, unavailable evidence, and delivery to the wrong destination. [README.md](../README.md#acceptance-cases) records their expected claim/task pairs.

## Milestones

**Milestone 1: trustworthy test environment.** Python entry point, pytest, simulated email service, local persisted SQLite evidence, labelled scripted targets, deterministic verification, and cases A–F. Clean replays get fresh state and identifiers. A missing evidence store produces uncertainty. Reports come from actual executions. No LLM, agent framework, hosted service, or external delivery is required.

**Milestone 2: first real agent investigation, deferred.** Add one supported model-backed target and one model-backed investigator. The investigator may read relevant evidence and request allowlisted fresh replays, subject to finite call, replay, and execution budgets. Preserve model identifiers, prompts/configuration, outputs, usage, and tool events. Label real model executions separately from scripted benchmarks. An exhausted investigation is incomplete. Validate exports through fresh execution, and preserve non-reproducing attempts. Do not modify a user's existing repository automatically.

**Milestone 3: narrow three-check MVP, deferred.** Add duplicate-effect and approval-order cases with broken and correctly behaving targets. Duplicate checks compare logical operation identifiers and committed effects, not tool-call counts. Approval checks validate authorization for the exact action and payload before execution. Keep these results separate from claim truthfulness and task completion. Consider a lightweight interface and a second independently owned target only after the checks work.

## Model role and evaluation

Deterministic code owns reset, faults, access controls, evidence records, counts, identifiers, final predicates, and replay limits. A future model interprets response language and selects permitted probes; it cannot rewrite evidence or success criteria.

Maintain separate development and held-out scenarios. Do not expose expected labels, planted-bug names, or fix locations to an investigator. Vary recipients, language, and event ordering. Evaluate claim extraction separately from verification.

Publish case counts and limitations, detection coverage, false positives, appropriate unverified decisions, extraction errors, reproduction frequency, and observed investigation time/tool usage. Report costs only with observed usage and a documented pricing basis. Compare the model investigator to deterministic scripts under identical inputs, permissions, and replay budgets. Extra narration is not improved detection; simplify if the model adds no measurable value.

## Scope and integrity

Use authorized synthetic local targets. Treat responses, traces, and documents as data, not authority to expand access. Validate reproduction requests in the runner. Human review is required for code changes; automatic remediation is outside scope. The local fixture environment is not a hardened sandbox for hostile arbitrary code.

Out of scope: production monitoring, arbitrary uploaded repositories, real email or payments, arbitrary network tools, multiple agent frameworks, a multi-agent investigator team, authentication, billing, polished dashboards, automatic patches/merges, and universal reliability certification.

This may inform a competition entry; it establishes neither competition eligibility, market demand, willingness to pay, nor production readiness.

## Competitive context

LangSmith already documents evaluation, issue diagnosis, proposed fixes, and replay-based validation. Independent action evidence, controlled fault injection, and portable reproduction cases are a proposed product focus, not a verified competitive gap or novelty claim. No LangSmith account or integration is required for Milestone 1.

- [LangSmith Engine: find and fix agent issues](https://docs.langchain.com/langsmith/engine)
- [LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)

These references support competitive context only, not this project's capabilities or results.
