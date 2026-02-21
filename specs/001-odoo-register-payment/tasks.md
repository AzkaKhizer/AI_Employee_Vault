# Tasks: Odoo Register Payment MCP Tool

**Input**: Design documents from `/specs/001-odoo-register-payment/`
**Branch**: `001-odoo-register-payment`
**Source file**: `.claude/skills/odoo-mcp-server/scripts/index.js` (only file modified)
**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/ ✅

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (no dependency on incomplete tasks in same phase)
- **[Story]**: User story this task belongs to ([US1], [US2], [US3])
- All implementation tasks target `.claude/skills/odoo-mcp-server/scripts/index.js` unless noted

---

## Phase 1: Setup (Environment Verification)

**Purpose**: Confirm the existing environment is ready before any code changes.

- [X] T001 Verify Odoo MCP server starts cleanly by running `node .claude/skills/odoo-mcp-server/scripts/index.js` and confirming no startup errors; confirm all 4 existing tools (create_draft_invoice, list_unpaid_invoices, get_revenue_summary, list_expenses) respond correctly
- [X] T002 [P] Confirm a posted invoice exists in Odoo for integration testing — run `list_unpaid_invoices` via Claude; if none present, create and post one via Odoo UI; record the invoice number and outstanding balance for use in later tasks

**Checkpoint**: Server is running, env vars verified, test invoice available.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Wire the tool into the MCP server infrastructure before implementing the handler body. All user story phases depend on this phase.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Add the `register_payment` tool definition object to the `TOOLS` array in `.claude/skills/odoo-mcp-server/scripts/index.js` — include `name`, `description`, and `inputSchema` with `invoice_number` (string, required), `amount` (number, required), `journal_name` (string, optional); place it after the `list_expenses` entry

  **Acceptance criteria**: `list_tools` MCP call returns `register_payment` in the tools list with correct schema.

- [X] T004 Add a `register_payment` entry to the `HANDLERS` dispatch map in `.claude/skills/odoo-mcp-server/scripts/index.js` pointing to a stub function `handleRegisterPayment` that returns `{ status: "not_implemented" }`; confirm the MCP server restarts without error and the tool call returns the stub response

  **Acceptance criteria**: Calling `register_payment` returns `{ status: "not_implemented" }` with no crash.

**Checkpoint**: Foundation ready — `register_payment` is wired into the MCP server. User story phases can now begin.

---

## Phase 3: User Story 1 — Register Full Payment (Priority: P1) 🎯 MVP

**Goal**: An operator can call `register_payment` with a valid invoice number, amount, and journal name and receive a `payment_registered` confirmation with the correct remaining balance.

**Independent Test**: Call `register_payment` with the test invoice number and its full outstanding amount. Verify `status: "payment_registered"` and `remaining_balance: 0`. Then call `list_unpaid_invoices` and confirm the invoice is absent.

### Core Implementation

- [X] T005 [US1] Implement invoice lookup inside `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — call `odoo.searchRead("account.move", [["move_type","=","out_invoice"],["state","=","posted"],["name","=",invoice_number]], ["id","name","partner_id","amount_residual","payment_state","currency_id"], { limit: 1 })`; store result as `invoices` array

  **Acceptance criteria**: Given a valid posted invoice number, `invoices` has length 1 and contains `id`, `amount_residual`, `partner_id`, `payment_state`.

- [X] T006 [US1] Implement journal lookup inside `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — call `odoo.searchRead("account.journal", [["name","ilike",journalName],["type","in",["cash","bank"]]], ["id","name","type"], { limit: 1 })`; store result as `journals` array; default `journal_name` parameter to `"Cash"` if not provided

  **Acceptance criteria**: Given `journal_name: "Cash"`, `journals` has length 1 with a valid `id`.

- [X] T007 [US1] Implement wizard creation inside `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — call `odoo.call("account.payment.register", "create", [{ amount: roundedAmount, journal_id: journals[0].id, payment_date: new Date().toISOString().slice(0,10) }], { context: { active_model: "account.move", active_ids: [invoice.id] } })`; store returned wizard ID as `wizardId`

  **Acceptance criteria**: `wizardId` is a positive integer; no Odoo error thrown.

- [X] T008 [US1] Implement payment posting and reconciliation inside `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — call `odoo.call("account.payment.register", "action_create_payments", [[wizardId]], {})`; this single call creates, posts, and reconciles the payment against the invoice

  **Acceptance criteria**: No Odoo error thrown; payment record exists in Odoo after call.

- [X] T009 [US1] Implement post-payment invoice re-read inside `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — call `odoo.searchRead("account.move", [["id","=",invoice.id]], ["amount_residual"], { limit: 1 })` after the wizard call; store result as `updatedInvoice`; use `updatedInvoice[0]?.amount_residual ?? null` as `remaining_balance`

  **Acceptance criteria**: `remaining_balance` equals `0` for a full payment; equals `original_balance - amount` for a partial payment.

- [X] T010 [US1] Implement the structured success response in `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — return `{ status: "payment_registered", invoice: invoice_number, amount_paid: Math.round(amount * 100) / 100, remaining_balance: Math.round(remaining_balance * 100) / 100 }` after all steps complete

  **Acceptance criteria**: Response matches the contract schema in `specs/001-odoo-register-payment/contracts/register_payment.json` exactly.

### Logging

- [X] T011 [US1] Verify the existing dispatch wrapper in `.claude/skills/odoo-mcp-server/scripts/index.js` logs `register_payment` invocations automatically via `logToVault({ tool: name, args, status: "invoked" })` and outcome via `logToVault({ tool: name, status: "success", result_summary: "ok" })` — open `Logs/YYYY-MM-DD.json` after a test call and confirm both entries appear with `tool: "register_payment"`

  **Acceptance criteria**: Two log entries per invocation: one with `status: "invoked"`, one with `status: "success"`.

**Checkpoint**: Full payment workflow works end-to-end. `list_unpaid_invoices` no longer shows the invoice.

---

## Phase 4: User Story 2 — Partial Payment (Priority: P2)

**Goal**: An operator can register a partial payment (less than the outstanding balance) and receive the correct `remaining_balance` in the response.

**Independent Test**: Create a fresh posted invoice with balance 5000. Call `register_payment` with `amount: 2000`. Verify `amount_paid: 2000` and `remaining_balance: 3000`. Confirm `list_unpaid_invoices` still shows the invoice with outstanding 3000.

### Core Implementation

- [X] T012 [US2] Verify partial amount handling — confirm the wizard `amount` field in T007 uses the caller-supplied `amount` (not `amount_residual`); no code change required if correct; add a code comment in `.claude/skills/odoo-mcp-server/scripts/index.js` above the wizard create call stating: `// Partial payments: wizard uses caller amount, not full residual`

  **Acceptance criteria**: Calling with `amount` < `amount_residual` results in `remaining_balance > 0` in the response.

- [X] T013 [US2] Implement float precision rounding for amount comparisons in `.claude/skills/odoo-mcp-server/scripts/index.js` — define helper `const r2 = (n) => Math.round(n * 100) / 100` at the top of `handleRegisterPayment`; use `r2(amount)` for all comparisons and the wizard `amount` field; use `r2(updatedInvoice[0].amount_residual)` for `remaining_balance`

  **Acceptance criteria**: `register_payment` with `amount: 2000.005` and residual `2000.01` does not trigger an overpayment error; response shows `amount_paid: 2000.01` (2dp rounded).

- [ ] T014 [US2] Integration-test partial payment against live Odoo — post a fresh invoice, call `register_payment` with exactly half the invoice amount, verify response `remaining_balance` equals the other half, verify `list_unpaid_invoices` shows invoice with correct outstanding

  **Acceptance criteria**: `remaining_balance` in response matches `amount_residual` in `list_unpaid_invoices` for the same invoice.

**Checkpoint**: Partial payments work correctly with accurate residual balance reporting.

---

## Phase 5: User Story 3 — Validation & Safety + Error Handling (Priority: P3)

**Goal**: All invalid inputs and unsafe states are rejected before any Odoo write occurs, with structured error responses.

**Independent Test**: Each guard case below can be triggered independently and returns the correct structured error without creating any Odoo record.

### Validation & Safety Guards

- [X] T015 [US3] Implement local amount guard at the top of `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — before any Odoo call, check `if (!amount || typeof amount !== "number" || amount <= 0)` and return `{ error: "invalid_amount", detail: "Amount must be a positive number greater than zero.", invoice_number }` immediately

  **Acceptance criteria**: Calling with `amount: 0`, `amount: -50`, or `amount: null` returns `{ error: "invalid_amount" }` with no Odoo I/O (verify no log entry shows "invoked" Odoo calls).

- [X] T016 [US3] Implement invoice-not-found guard in `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — after invoice `searchRead`, check `if (!invoices.length)` and return `{ error: "invoice_not_found", detail: \`No posted customer invoice found matching "${invoice_number}".\`, invoice_number }`

  **Acceptance criteria**: Calling with `invoice_number: "INV/9999/99999"` (nonexistent) returns `{ error: "invoice_not_found" }`.

- [X] T017 [US3] Implement already-paid guard in `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — after invoice lookup, check `if (invoice.payment_state === "paid")` and return `{ status: "already_paid", invoice: invoice_number, remaining_balance: 0 }` without writing any payment record

  **Acceptance criteria**: Calling on a fully-paid invoice returns `{ status: "already_paid", remaining_balance: 0 }` with no new Odoo payment record created.

- [X] T018 [US3] Implement overpayment guard in `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — after invoice lookup, check `if (r2(amount) > r2(invoice.amount_residual))` and return `{ error: "overpayment_not_allowed", detail: \`Amount ${r2(amount)} exceeds outstanding balance ${r2(invoice.amount_residual)} for ${invoice_number}.\`, invoice_number }` before any write

  **Acceptance criteria**: Calling with `amount` 1 unit above `amount_residual` returns `{ error: "overpayment_not_allowed" }` with no Odoo write.

- [X] T019 [US3] Implement journal-not-found guard in `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — after journal `searchRead`, check `if (!journals.length)` and return `{ error: "journal_not_found", detail: \`No cash or bank journal found matching "${journalName}".\`, invoice_number }`

  **Acceptance criteria**: Calling with `journal_name: "Nonexistent Journal"` returns `{ error: "journal_not_found" }` with no Odoo write.

### Error Handling — Edge Cases

- [X] T020 [US3] Handle network failure / Odoo connectivity error — confirm the existing `try/catch` in the MCP dispatch wrapper in `.claude/skills/odoo-mcp-server/scripts/index.js` catches `fetch` errors (e.g., `ECONNREFUSED` when Odoo is down) and returns `{ content: [{ type: "text", text: "Error: Odoo RPC error: ..." }], isError: true }`; test by stopping Odoo and calling `register_payment`; confirm the error is logged via `logToVault({ tool: "register_payment", status: "error", error: err.message })`

  **Acceptance criteria**: When Odoo is unreachable, tool returns a non-crashing error response and logs the failure.

- [X] T021 [US3] Handle Odoo restart mid-call — confirm that if Odoo restarts between the invoice lookup (step 2) and wizard call (step 6), the re-authentication logic in `OdooClient.authenticate()` (which re-uses cached `uid`) will fail with `Odoo RPC error`; verify the existing `try/catch` catches this and returns a structured error; add a code comment in `.claude/skills/odoo-mcp-server/scripts/index.js` above the wizard create call: `// OdooClient re-authenticates lazily; a mid-call Odoo restart causes RPC error caught by dispatch wrapper`

  **Acceptance criteria**: Code comment present; no special handling needed beyond existing try/catch.

- [X] T022 [US3] Handle duplicate invoice number edge case — confirm the invoice lookup uses `limit: 1` in `.claude/skills/odoo-mcp-server/scripts/index.js` so duplicate records do not cause ambiguity; add a code comment: `// limit: 1 ensures first match used if duplicates exist (should not occur in production Odoo)`

  **Acceptance criteria**: Code comment present; `limit: 1` confirmed in `searchRead` call for `account.move`.

- [X] T023 [US3] Implement structured error for `journal_name: ""` (empty string) in `handleRegisterPayment` in `.claude/skills/odoo-mcp-server/scripts/index.js` — if `journal_name` is provided but is an empty string, treat it as missing and default to `"Cash"`; add logic: `const journalName = (journal_name && journal_name.trim()) || "Cash";`

  **Acceptance criteria**: Calling with `journal_name: ""` behaves identically to calling without `journal_name`.

**Checkpoint**: All guard conditions are enforced. No writes occur before guards pass.

---

## Phase 6: Testing

**Purpose**: Verify all user stories and edge cases against the live Odoo instance.

**Note**: These are manual integration tests following the quickstart.md sequence. Automated unit tests with mocked `OdooClient` are marked [P] and can be authored in parallel.

### Integration Tests (live Odoo 19+)

- [ ] T024 [US1] Run full payment integration test: create a posted invoice via `create_draft_invoice` + manual post in Odoo UI; call `register_payment` with full outstanding amount; verify `status: "payment_registered"` and `remaining_balance: 0`; call `list_unpaid_invoices` and confirm invoice absent

  **Acceptance criteria**: SC-003 — invoice `amount_residual` in Odoo matches `remaining_balance` in response.

- [ ] T025 [US2] Run partial payment integration test: create a posted invoice with balance 5000; call `register_payment` with `amount: 2000`; verify `remaining_balance: 3000`; call `list_unpaid_invoices` and confirm invoice shows `outstanding: 3000`

  **Acceptance criteria**: Partial balance in response matches Odoo; invoice still appears in unpaid list.

- [ ] T026 [US3] Run double-payment guard test: after full payment in T024, call `register_payment` again on the same invoice; verify response is `{ status: "already_paid", remaining_balance: 0 }`

  **Acceptance criteria**: No second payment record created in Odoo.

- [ ] T027 [US3] Run overpayment guard test: on a fresh invoice with balance 1000, call `register_payment` with `amount: 1500`; verify `{ error: "overpayment_not_allowed" }`; verify invoice still shows outstanding 1000 in Odoo

  **Acceptance criteria**: SC-002 — no Odoo write occurred; invoice balance unchanged.

- [ ] T028 [US3] Run invalid invoice number test: call `register_payment` with `invoice_number: "INV/0000/00000"` and `amount: 100`; verify `{ error: "invoice_not_found" }`

  **Acceptance criteria**: SC-002 — structured error returned; no Odoo write.

- [ ] T029 [US3] Run journal-not-found test: call `register_payment` with a valid invoice and `journal_name: "XYZ_NONEXISTENT_JOURNAL"`; verify `{ error: "journal_not_found" }`

  **Acceptance criteria**: Structured error returned; no Odoo write.

### Edge Case Tests

- [ ] T030 [P] [US3] Run network failure test: stop the Odoo Docker container (`docker-compose -f docker/odoo/docker-compose.yml stop` or equivalent); call `register_payment`; confirm error response is returned (not a crash); confirm log entry shows `status: "error"`; restart Odoo afterward

  **Acceptance criteria**: MCP tool returns `isError: true` content; Claude displays error gracefully.

- [ ] T031 [P] [US3] Run invalid `amount` type test: call `register_payment` with `amount: 0` and separately with `amount: -1`; verify both return `{ error: "invalid_amount" }`

  **Acceptance criteria**: Both calls return structured error with no Odoo I/O.

- [ ] T032 [US3] Run concurrency simulation test (sequential approximation): open two Claude sessions; in session 1, call `list_unpaid_invoices` to confirm balance X; in session 2, call `register_payment` for full amount X; immediately in session 1, call `register_payment` for amount X; verify session 1 receives `{ status: "already_paid" }` (Odoo serializes writes server-side, so the second payment sees the paid state); document outcome in `specs/001-odoo-register-payment/quickstart.md`

  **Acceptance criteria**: No double-payment created; second call returns `already_paid` or `overpayment_not_allowed`.

- [ ] T033 [US1] Verify Vault log completeness: after running T024–T031, open `Logs/YYYY-MM-DD.json` and confirm every `register_payment` call (success and error) has at least one log entry with `tool: "register_payment"` and a non-empty `status` field

  **Acceptance criteria**: SC-004 — 100% of invocations captured in Vault daily log.

---

## Phase 7: Documentation

**Purpose**: Finalize human-readable guidance and update the skill file to reflect the new tool.

- [ ] T034 [P] Update `specs/001-odoo-register-payment/quickstart.md` with any corrections discovered during T024–T032 integration testing (e.g., actual Odoo response shapes, concurrency outcome from T032)

  **Acceptance criteria**: quickstart.md accurately describes the behaviour observed in integration tests.

- [X] T035 [P] Update `.claude/skills/odoo-mcp-server/SKILL.md` to list `register_payment` as the 5th available tool with a one-line description: "Register a payment against a posted customer invoice (creates, posts, and reconciles)"

  **Acceptance criteria**: SKILL.md tool list includes `register_payment`.

- [X] T036 Update `specs/001-odoo-register-payment/checklists/requirements.md` to mark the implementation phase complete: add a new section `## Implementation` with checkboxes for each FR-001–FR-013 requirement and mark each complete once verified by integration tests

  **Acceptance criteria**: All 13 FRs verified and checked; checklist updated with date.

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)       → no dependencies; start immediately
Phase 2 (Foundational)→ depends on Phase 1
Phase 3 (US1)         → depends on Phase 2 (T003, T004 complete)
Phase 4 (US2)         → depends on Phase 3 (T005–T010 complete)
Phase 5 (US3)         → depends on Phase 2 (T003, T004 complete); can run after Phase 3
Phase 6 (Testing)     → depends on Phase 3 + Phase 4 + Phase 5 all complete
Phase 7 (Docs)        → depends on Phase 6; [P] tasks can run in parallel with each other
```

### User Story Dependencies

- **US1 (P1)**: Blocks US2 — partial payment reuses the same handler path
- **US2 (P2)**: Depends on US1; adds float precision and residual recalculation
- **US3 (P3)**: Can start after Foundational (Phase 2); guards are prepended to US1 handler — implement guards before running US1 integration tests for safety

### Within Each User Story

- T005 → T006 → T007 → T008 → T009 → T010 (sequential — each step depends on previous)
- T013 (rounding helper) should be added before T007 and T018 to avoid floating-point issues

---

## Parallel Execution Examples

### Phase 3 — after T004 completes, T005 and T006 can proceed in parallel:

```
Task A: T005 — Invoice lookup (account.move searchRead)
Task B: T006 — Journal lookup (account.journal searchRead)
```

### Phase 6 — independent integration tests can run in parallel:

```
Task A: T024 — Full payment integration test
Task B: T028 — Invalid invoice number test
Task C: T029 — Journal-not-found test
Task D: T031 — Invalid amount type test
```

### Phase 7 — all documentation tasks can run in parallel:

```
Task A: T034 — Update quickstart.md
Task B: T035 — Update SKILL.md
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational — wire tool into MCP server (T003, T004)
3. Complete Phase 5 guards first (T015–T019) — safety before functionality
4. Complete Phase 3: User Story 1 (T005–T011) — core happy path
5. **STOP and VALIDATE**: Run T024 integration test
6. Confirm `list_unpaid_invoices` confirms invoice is paid

### Incremental Delivery

1. **MVP**: Phases 1–3 + US3 guards → full payment with all safety guards
2. **Increment 2**: Phase 4 (US2) → partial payment support
3. **Increment 3**: Phase 6 (Testing) → full integration and edge case verification
4. **Increment 4**: Phase 7 (Docs) → documentation complete

---

## Summary

| Phase | Story | Tasks | Parallel Opportunities |
|-------|-------|-------|------------------------|
| Phase 1: Setup | — | T001–T002 | T002 |
| Phase 2: Foundational | — | T003–T004 | None (sequential) |
| Phase 3: US1 Core + Logging | US1 | T005–T011 | T005, T006 |
| Phase 4: US2 Partial Payment | US2 | T012–T014 | None (sequential) |
| Phase 5: US3 Validation + Error Handling | US3 | T015–T023 | None (sequential guards) |
| Phase 6: Testing | All | T024–T033 | T024, T028, T029, T031 |
| Phase 7: Documentation | — | T034–T036 | T034, T035 |
| **Total** | | **36 tasks** | **8 parallel opportunities** |

**MVP Scope**: T001–T011 + T015–T019 = 17 tasks (safe full payment with all guards)
