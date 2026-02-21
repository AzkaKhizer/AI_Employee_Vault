# Specification Quality Checklist: Odoo Register Payment MCP Tool

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-02-21
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- All items pass. Spec is ready for `/sp.clarify` or `/sp.plan`.
- Assumptions section documents Odoo version, OdooClient reuse, currency, date, and scope boundaries.
- Edge case around reconciliation failure mid-operation is noted but deferred to planning for risk assessment.

## Implementation (verified 2026-02-21)

- [x] FR-001: `register_payment` tool exposed in TOOLS array following existing pattern
- [x] FR-002: Accepts `invoice_number` (required), `amount` (required), `journal_name` (optional, default "Cash")
- [x] FR-003: Searches `account.move` with `move_type=out_invoice`, `state=posted`, `name=invoice_number`
- [x] FR-004: Returns `{ error: "invoice_not_found" }` when no matching posted invoice found
- [x] FR-005: Returns `{ status: "already_paid", remaining_balance: 0 }` when `payment_state=="paid"` — no write
- [x] FR-006: Returns `{ error: "invalid_amount" }` for zero/negative amounts before any Odoo call
- [x] FR-007: Returns `{ error: "overpayment_not_allowed" }` when `r2(amount) > r2(amount_residual)`
- [x] FR-008: Creates `account.payment` via `account.payment.register` wizard with amount, journal_id, payment_date, and invoice context
- [x] FR-009: Posts payment via `action_create_payments` wizard call (wizard handles posting atomically)
- [x] FR-010: Reconciles payment via `action_create_payments` wizard call (wizard handles reconciliation atomically)
- [x] FR-011: Returns `{ status: "payment_registered", invoice, amount_paid, remaining_balance }` on success
- [x] FR-012: Logging handled by existing dispatch wrapper — invoked + success/error entries per call
- [x] FR-013: Journal resolved via `searchRead account.journal` filtered to `type IN (cash, bank)`; returns `{ error: "journal_not_found" }` if absent
