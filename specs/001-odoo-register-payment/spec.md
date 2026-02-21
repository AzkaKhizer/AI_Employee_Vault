# Feature Specification: Odoo Register Payment MCP Tool

**Feature Branch**: `001-odoo-register-payment`
**Created**: 2026-02-21
**Status**: Draft
**Input**: User description: "Add a new MCP tool named register_payment to the Odoo MCP server."

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Register Full Payment via MCP (Priority: P1)

An operator interacting with Claude asks it to register a payment against a posted Odoo invoice. Claude calls the `register_payment` MCP tool with an invoice number, amount, and optionally a journal name. The tool validates the invoice exists and is posted, ensures no overpayment occurs, creates and posts the payment in Odoo, reconciles it against the invoice, and returns a structured confirmation with the remaining balance.

**Why this priority**: This is the core purpose of the feature. Without it, users must leave Claude and manually post payments in Odoo — breaking conversational workflow continuity.

**Independent Test**: Can be fully tested by calling `register_payment` with a valid posted invoice number and amount matching the invoice total, and verifying the returned `status: "payment_registered"` and `remaining_balance: 0`.

**Acceptance Scenarios**:

1. **Given** a posted invoice `INV/2026/00001` with outstanding balance 5000, **When** `register_payment` is called with `invoice_number="INV/2026/00001"`, `amount=5000`, `journal_name="Cash"`, **Then** the tool returns `{ status: "payment_registered", invoice: "INV/2026/00001", amount_paid: 5000, remaining_balance: 0 }`.
2. **Given** a posted invoice with outstanding balance 5000, **When** `register_payment` is called without `journal_name`, **Then** the tool defaults to the "Cash" journal and succeeds.
3. **Given** any successful payment, **Then** the action is logged to the Vault log file for the current date.

---

### User Story 2 - Partial Payment (Priority: P2)

An operator registers a partial payment against an invoice that has a larger outstanding balance. The tool allows amounts less than the outstanding balance and returns the correct remaining balance after reconciliation.

**Why this priority**: Partial payments are a common real-world accounting scenario. Blocking them would reduce the tool's practical utility.

**Independent Test**: Call `register_payment` with an amount less than the invoice total; verify the returned `remaining_balance` equals `outstanding - amount_paid`.

**Acceptance Scenarios**:

1. **Given** an invoice with outstanding balance 5000, **When** `register_payment` is called with `amount=2000`, **Then** the response includes `amount_paid: 2000` and `remaining_balance: 3000`.

---

### User Story 3 - Rejection of Invalid Inputs (Priority: P3)

The tool enforces safety guards, preventing overpayments, negative amounts, and payments against non-existent or unpaid invoices.

**Why this priority**: Prevents irreversible financial data corruption in Odoo.

**Independent Test**: Each rejection case can be tested independently by providing the triggering bad input and verifying a structured error response is returned without any Odoo write occurring.

**Acceptance Scenarios**:

1. **Given** a valid invoice with outstanding 5000, **When** `register_payment` is called with `amount=6000`, **Then** the tool returns a structured error: `{ error: "overpayment_not_allowed", ... }` and no payment record is created.
2. **Given** any invoice, **When** `register_payment` is called with `amount=-100`, **Then** the tool returns `{ error: "invalid_amount", ... }`.
3. **Given** an invoice number that does not exist in Odoo, **When** `register_payment` is called, **Then** the tool returns `{ error: "invoice_not_found", ... }`.
4. **Given** an invoice that is in draft state (not posted), **When** `register_payment` is called, **Then** the tool returns `{ error: "invoice_not_posted", ... }`.
5. **Given** an invoice that is already fully paid, **When** `register_payment` is called, **Then** the tool returns `{ status: "already_paid", remaining_balance: 0 }`.

---

### Edge Cases

- What happens when the specified journal name does not exist in Odoo?
- What happens if Odoo creates the payment but reconciliation fails mid-operation?
- What happens when `amount` is 0?
- What happens if the same invoice number appears in multiple records (e.g., duplicates)?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a new MCP tool named `register_payment` following the same tool definition and handler pattern as existing tools in the server.
- **FR-002**: System MUST accept `invoice_number` (string, required), `amount` (number, required), and `journal_name` (string, optional, default: `"Cash"`) as inputs.
- **FR-003**: System MUST search `account.move` records filtered by `move_type = "out_invoice"`, `state = "posted"`, and `name = invoice_number` to locate the target invoice.
- **FR-004**: System MUST return a structured error response if no matching posted invoice is found.
- **FR-005**: System MUST return a structured response indicating `already_paid` if the invoice's `payment_state` is `"paid"` without creating any new records.
- **FR-006**: System MUST reject and return an error if `amount` is negative or zero, without contacting Odoo's write API.
- **FR-007**: System MUST reject and return an error if `amount` exceeds the invoice's `amount_residual` (outstanding balance).
- **FR-008**: System MUST create an `account.payment` record linked to the partner, with the correct amount, currency, payment date, and resolved journal.
- **FR-009**: System MUST post the payment (transition from draft to posted state) in Odoo.
- **FR-010**: System MUST reconcile the payment against the invoice so the invoice's outstanding balance is reduced.
- **FR-011**: System MUST return a structured success response containing `status`, `invoice`, `amount_paid`, and `remaining_balance` after successful registration.
- **FR-012**: System MUST log every invocation (inputs, outcome, errors) to the Vault daily log file, consistent with existing tool logging behavior.
- **FR-013**: System MUST resolve the journal by name from Odoo's `account.journal` model and return a structured error if the journal is not found.

### Key Entities

- **Invoice (`account.move`)**: A posted customer invoice identified by its `name` (e.g., `INV/2026/00001`). Key attributes: `id`, `name`, `partner_id`, `amount_residual`, `payment_state`, `currency_id`.
- **Payment (`account.payment`)**: A financial transaction that settles an invoice. Key attributes: `amount`, `partner_id`, `journal_id`, `payment_date`, `payment_type`, `partner_type`.
- **Journal (`account.journal`)**: The accounting journal used for the payment (e.g., Cash, Bank). Resolved by name at runtime.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A valid payment request completes and returns a structured response in under 5 seconds under normal Odoo load.
- **SC-002**: 100% of overpayment, negative-amount, not-found, and not-posted rejection cases return structured error responses without creating any Odoo records.
- **SC-003**: After a successful payment, the invoice's outstanding balance in Odoo matches the `remaining_balance` field in the tool response.
- **SC-004**: 100% of tool invocations (success and failure) are captured in the Vault daily log with tool name, inputs, and outcome.
- **SC-005**: Operators can complete a full "list unpaid → register payment → confirm balance" workflow without leaving the Claude conversational interface.

---

## Assumptions

- The Odoo instance is version 19+ and accessible via the existing `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD` environment variables already configured for the MCP server.
- The `OdooClient` class (used by existing tools) already supports `searchRead` and `create` operations; payment posting and reconciliation will use the same JSON-RPC `execute_kw` pattern with Odoo's `action_post` and `js_assign_outstanding_credit` (or equivalent) model methods.
- Currency defaults to the invoice's currency; no multi-currency conversion is required in this scope.
- Payment date defaults to the current date (server-side).
- Only customer invoices (`out_invoice`) are in scope; vendor bill payments are explicitly out of scope.
- The `journal_name` match against Odoo is case-insensitive and uses an exact name lookup; fuzzy matching is out of scope.
