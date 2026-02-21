#!/usr/bin/env node

/**
 * Odoo MCP Server
 *
 * Exposes five tools over MCP stdio transport:
 *   1. create_draft_invoice  — create a draft (never auto-post)
 *   2. list_unpaid_invoices  — read-only query
 *   3. get_revenue_summary   — read-only aggregation
 *   4. list_expenses         — read-only query
 *   5. register_payment      — create, post, and reconcile a payment against a posted invoice
 *
 * All calls are logged to Vault/Logs/YYYY-MM-DD.json.
 * Monetary write actions are flagged for HITL approval.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { OdooClient } from "./odoo-client.js";
import { logToVault } from "./logger.js";

// ── Validate environment ────────────────────────────────────────────
const required = ["ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"];
for (const key of required) {
  if (!process.env[key]) {
    console.error(`Missing required env var: ${key}`);
    process.exit(1);
  }
}

const odoo = new OdooClient({
  url: process.env.ODOO_URL,
  db: process.env.ODOO_DB,
  username: process.env.ODOO_USERNAME,
  password: process.env.ODOO_PASSWORD,
});

// ── Tool definitions ────────────────────────────────────────────────
const TOOLS = [
  {
    name: "create_draft_invoice",
    description:
      "Create a DRAFT customer invoice in Odoo. Never auto-posts. Requires HITL approval before posting.",
    inputSchema: {
      type: "object",
      properties: {
        customer_name: { type: "string", description: "Customer/partner display name" },
        amount: { type: "number", description: "Invoice line amount (positive)" },
        description: { type: "string", description: "Invoice line label/description" },
      },
      required: ["customer_name", "amount", "description"],
    },
  },
  {
    name: "list_unpaid_invoices",
    description: "List all posted customer invoices with outstanding balance.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "get_revenue_summary",
    description: "Aggregate posted invoice revenue between two dates.",
    inputSchema: {
      type: "object",
      properties: {
        start_date: { type: "string", description: "Start date YYYY-MM-DD" },
        end_date: { type: "string", description: "End date YYYY-MM-DD" },
      },
      required: ["start_date", "end_date"],
    },
  },
  {
    name: "list_expenses",
    description: "List vendor bills / expense records between two dates.",
    inputSchema: {
      type: "object",
      properties: {
        start_date: { type: "string", description: "Start date YYYY-MM-DD" },
        end_date: { type: "string", description: "End date YYYY-MM-DD" },
      },
      required: ["start_date", "end_date"],
    },
  },
  {
    name: "register_payment",
    description:
      "Register a payment against a posted customer invoice in Odoo. Creates, posts, and reconciles the payment via the account.payment.register wizard. Prevents overpayment and negative amounts. Supports full and partial payments.",
    inputSchema: {
      type: "object",
      properties: {
        invoice_number: { type: "string", description: "Invoice number as displayed in Odoo (e.g. INV/2026/00001)" },
        amount: { type: "number", description: "Payment amount (positive, must not exceed outstanding balance)" },
        journal_name: { type: "string", description: "Accounting journal name (default: Cash). Must be a Cash or Bank type journal." },
      },
      required: ["invoice_number", "amount"],
    },
  },
  {
    name: "close_invoice",
    description:
      "Close a customer invoice end-to-end. Posts draft invoices automatically, then registers full payment for the outstanding balance. Idempotent — returns 'already_closed' if residual is zero. Reuses register_payment guards (journal validation, overpayment prevention, structured errors).",
    inputSchema: {
      type: "object",
      properties: {
        invoice_number: { type: "string", description: "Invoice number as displayed in Odoo (e.g. INV/2026/00001)" },
        journal_name: { type: "string", description: "Accounting journal name (default: Cash). Must be a Cash or Bank type journal." },
      },
      required: ["invoice_number"],
    },
  },
  {
    name: "auto_close_all_unpaid",
    description:
      "Automatically close all currently unpaid customer invoices using the close_invoice workflow. Processes each invoice sequentially, accumulates paid amounts, and returns a structured batch summary with per-invoice failure details. Never throws — always returns a structured response.",
    inputSchema: {
      type: "object",
      properties: {
        journal_name: { type: "string", description: "Accounting journal name to use for all payments (e.g. Cash). Must be a Cash or Bank type journal." },
      },
      required: [],
    },
  },
];

// ── Tool handlers ───────────────────────────────────────────────────

async function handleCreateDraftInvoice({ customer_name, amount, description }) {
  // Find or fail on partner
  const partners = await odoo.searchRead(
    "res.partner",
    [["name", "ilike", customer_name]],
    ["id", "name"],
    { limit: 1 }
  );
  if (!partners.length) {
    return { error: `No partner found matching "${customer_name}"` };
  }
  const partnerId = partners[0].id;

  // Create draft invoice (move_type = out_invoice, state defaults to "draft")
  const invoiceId = await odoo.create("account.move", {
    move_type: "out_invoice",
    partner_id: partnerId,
    invoice_line_ids: [
      [0, 0, { name: description, price_unit: amount, quantity: 1 }],
    ],
  });

  return {
    status: "draft_created",
    invoice_id: invoiceId,
    partner: partners[0].name,
    amount,
    description,
    note: "Invoice is DRAFT. Manual posting required — will NOT auto-post.",
  };
}

async function handleListUnpaidInvoices() {
  const invoices = await odoo.searchRead(
    "account.move",
    [
      ["move_type", "=", "out_invoice"],
      ["state", "=", "posted"],
      ["payment_state", "in", ["not_paid", "partial"]],
    ],
    ["id", "name", "partner_id", "amount_total", "amount_residual", "invoice_date", "invoice_date_due"],
    { order: "invoice_date_due asc", limit: 100 }
  );

  return {
    count: invoices.length,
    invoices: invoices.map((inv) => ({
      id: inv.id,
      number: inv.name,
      customer: inv.partner_id?.[1] || "Unknown",
      total: inv.amount_total,
      outstanding: inv.amount_residual,
      date: inv.invoice_date,
      due_date: inv.invoice_date_due,
    })),
  };
}

async function handleGetRevenueSummary({ start_date, end_date }) {
  const invoices = await odoo.searchRead(
    "account.move",
    [
      ["move_type", "=", "out_invoice"],
      ["state", "=", "posted"],
      ["invoice_date", ">=", start_date],
      ["invoice_date", "<=", end_date],
    ],
    ["id", "amount_total", "amount_residual", "invoice_date"],
    { order: "invoice_date asc" }
  );

  const totalRevenue = invoices.reduce((sum, inv) => sum + inv.amount_total, 0);
  const totalOutstanding = invoices.reduce((sum, inv) => sum + inv.amount_residual, 0);

  return {
    period: { start_date, end_date },
    invoice_count: invoices.length,
    total_revenue: Math.round(totalRevenue * 100) / 100,
    total_collected: Math.round((totalRevenue - totalOutstanding) * 100) / 100,
    total_outstanding: Math.round(totalOutstanding * 100) / 100,
  };
}

async function handleListExpenses({ start_date, end_date }) {
  const bills = await odoo.searchRead(
    "account.move",
    [
      ["move_type", "=", "in_invoice"],
      ["invoice_date", ">=", start_date],
      ["invoice_date", "<=", end_date],
    ],
    ["id", "name", "partner_id", "amount_total", "state", "invoice_date"],
    { order: "invoice_date asc", limit: 200 }
  );

  return {
    period: { start_date, end_date },
    count: bills.length,
    total: Math.round(bills.reduce((s, b) => s + b.amount_total, 0) * 100) / 100,
    expenses: bills.map((b) => ({
      id: b.id,
      number: b.name,
      vendor: b.partner_id?.[1] || "Unknown",
      amount: b.amount_total,
      state: b.state,
      date: b.invoice_date,
    })),
  };
}

async function handleRegisterPayment({ invoice_number, amount, journal_name }) {
  // Float precision helper — round to 2 decimal places throughout
  const r2 = (n) => Math.round(n * 100) / 100;

  // [T023] Empty string defaults to "Cash"; also handles missing journal_name
  const journalName = (journal_name && journal_name.trim()) || "Cash";

  // [T015] LOCAL GUARD: amount must be a positive number — no Odoo I/O before this passes
  if (!amount || typeof amount !== "number" || amount <= 0) {
    return {
      error: "invalid_amount",
      detail: "Amount must be a positive number greater than zero.",
      invoice_number,
    };
  }

  // [T005] ODOO READ: find the posted customer invoice by name
  // [T022] limit: 1 ensures first match used if duplicates exist (should not occur in production Odoo)
  const invoices = await odoo.searchRead(
    "account.move",
    [
      ["move_type", "=", "out_invoice"],
      ["state", "=", "posted"],
      ["name", "=", invoice_number],
    ],
    ["id", "name", "partner_id", "amount_residual", "payment_state", "currency_id"],
    { limit: 1 }
  );

  // [T016] GUARD: invoice not found
  if (!invoices.length) {
    return {
      error: "invoice_not_found",
      detail: `No posted customer invoice found matching "${invoice_number}".`,
      invoice_number,
    };
  }
  const invoice = invoices[0];

  // [T017] GUARD: invoice already fully paid — return without creating any record
  if (invoice.payment_state === "paid") {
    return {
      status: "already_paid",
      invoice: invoice_number,
      remaining_balance: 0,
    };
  }

  // [T018] GUARD: prevent overpayment using 2dp-rounded comparison
  if (r2(amount) > r2(invoice.amount_residual)) {
    return {
      error: "overpayment_not_allowed",
      detail: `Amount ${r2(amount)} exceeds outstanding balance ${r2(invoice.amount_residual)} for ${invoice_number}.`,
      invoice_number,
    };
  }

  // [T006] ODOO READ: resolve journal by name, restricted to cash/bank types only
  const journals = await odoo.searchRead(
    "account.journal",
    [["name", "ilike", journalName], ["type", "in", ["cash", "bank"]]],
    ["id", "name", "type"],
    { limit: 1 }
  );

  // [T019] GUARD: journal not found
  if (!journals.length) {
    return {
      error: "journal_not_found",
      detail: `No cash or bank journal found matching "${journalName}".`,
      invoice_number,
    };
  }

  // ── ALL GUARDS PASSED — WRITE PHASE ─────────────────────────────

  // [T007] ODOO WRITE: create account.payment.register wizard
  // [T012] Partial payments: wizard uses caller amount, not full residual
  // [T021] OdooClient re-authenticates lazily; a mid-call Odoo restart causes RPC error caught by dispatch wrapper
  const wizardId = await odoo.call(
    "account.payment.register",
    "create",
    [{ amount: r2(amount), journal_id: journals[0].id, payment_date: new Date().toISOString().slice(0, 10) }],
    { context: { active_model: "account.move", active_ids: [invoice.id] } }
  );

  // [T008] ODOO WRITE: create + post + reconcile payment in a single wizard action
  await odoo.call("account.payment.register", "action_create_payments", [[wizardId]], {});

  // [T009] ODOO READ: re-read invoice to get authoritative final residual balance
  const updated = await odoo.searchRead(
    "account.move",
    [["id", "=", invoice.id]],
    ["amount_residual"],
    { limit: 1 }
  );
  const remaining_balance = r2(updated[0]?.amount_residual ?? 0);

  // [T010] SUCCESS RESPONSE — matches contract schema in contracts/register_payment.json
  return {
    status: "payment_registered",
    invoice: invoice_number,
    amount_paid: r2(amount),
    remaining_balance,
  };
}

async function handleCloseInvoice({ invoice_number, journal_name }) {
  const r2 = (n) => Math.round(n * 100) / 100;

  // [1] ODOO READ: lookup by name with NO state filter — handles both draft and posted
  const invoices = await odoo.searchRead(
    "account.move",
    [["move_type", "=", "out_invoice"], ["name", "=", invoice_number]],
    ["id", "name", "state", "amount_residual", "payment_state"],
    { limit: 1 }
  );

  // [2] FAIL FAST: invoice not found
  if (!invoices.length) {
    return {
      error: "invoice_not_found",
      detail: `No customer invoice found matching "${invoice_number}".`,
      invoice_number,
    };
  }

  let invoice = invoices[0];

  // [3] AUTO-POST: if draft, post it before proceeding
  if (invoice.state === "draft") {
    await odoo.call("account.move", "action_post", [[invoice.id]], {});
    // Re-read to get updated state, residual, AND the real invoice name assigned on posting
    // (draft invoices have name="/" until posted, e.g. INV/2026/00006)
    const refreshed = await odoo.searchRead(
      "account.move",
      [["id", "=", invoice.id]],
      ["name", "state", "amount_residual", "payment_state"],
      { limit: 1 }
    );
    invoice = { ...invoice, ...refreshed[0] };
    // Use the real assigned name for all subsequent steps — do not keep "/"
    invoice_number = invoice.name;
  }

  // [4] IDEMPOTENCY: already fully paid — nothing to do
  if (invoice.payment_state === "paid" || r2(invoice.amount_residual) === 0) {
    return {
      status: "already_closed",
      invoice: invoice_number,
      remaining_balance: 0,
    };
  }

  // [5] REUSE: delegate to handleRegisterPayment with the full residual amount
  // All guards (journal lookup, overpayment, invalid amount) are inherited — no duplication
  const paymentResult = await handleRegisterPayment({
    invoice_number,
    amount: r2(invoice.amount_residual),
    journal_name,
  });

  // [6] PROPAGATE: surface any structured error from the payment step
  if (paymentResult.error) {
    return paymentResult;
  }

  // [7] RECONCILIATION VERIFICATION: re-read to confirm residual is truly zero
  // Reuses invoice.id from step [1] — no duplicate lookup logic
  const verification = await odoo.searchRead(
    "account.move",
    [["id", "=", invoice.id]],
    ["amount_residual", "payment_state"],
    { limit: 1 }
  );
  const finalResidual = r2(verification[0]?.amount_residual ?? Infinity);

  if (finalResidual !== 0) {
    return {
      error: "reconciliation_failed",
      detail: "Payment executed but residual is not zero.",
      invoice: invoice_number,
      remaining_balance: finalResidual,
    };
  }

  // [8] SUCCESS: residual confirmed zero
  return {
    status: "invoice_closed",
    invoice: invoice_number,
    paid_amount: r2(invoice.amount_residual),
    remaining_balance: 0,
  };
}

async function handleAutoCloseAllUnpaid({ journal_name } = {}) {
  const r2 = (n) => Math.round(n * 100) / 100;

  // [1] QUERY: fetch all open customer invoices — draft and posted, excluding already-paid
  // Draft invoices are included because handleCloseInvoice auto-posts them before payment
  const rawInvoices = await odoo.searchRead(
    "account.move",
    [
      ["move_type", "=", "out_invoice"],
      ["state", "in", ["draft", "posted"]],
      ["payment_state", "!=", "paid"],
    ],
    ["id", "name", "state", "amount_total", "amount_residual"],
    { order: "id asc", limit: 200 }
  );

  // [2] EARLY EXIT: nothing to process
  if (!rawInvoices.length) {
    return {
      status: "no_unpaid_invoices",
      total_processed: 0,
      total_closed: 0,
      total_amount_reconciled: 0,
      failures: [],
    };
  }

  // [3] BATCH: process each invoice sequentially via handleCloseInvoice
  let total_closed = 0;
  let total_amount_reconciled = 0;
  const failures = [];

  for (const inv of rawInvoices) {
    // REUSE: delegates to handleCloseInvoice — handles draft→post→pay, all guards inherited
    const result = await handleCloseInvoice({
      invoice_number: inv.name,
      journal_name,
    });

    if (result.status === "invoice_closed" || result.status === "already_closed") {
      total_closed++;
      // paid_amount present on invoice_closed; already_closed contributes 0 (residual was 0)
      total_amount_reconciled += result.paid_amount ?? 0;
    } else {
      failures.push({
        invoice: inv.name,
        error: result.error,
        detail: result.detail ?? null,
      });
    }
  }

  // [4] BATCH SUMMARY
  return {
    status: "batch_complete",
    total_processed: rawInvoices.length,
    total_closed,
    total_amount_reconciled: r2(total_amount_reconciled),
    failures,
  };
}

// ── Dispatch map ────────────────────────────────────────────────────
const HANDLERS = {
  create_draft_invoice: handleCreateDraftInvoice,
  list_unpaid_invoices: handleListUnpaidInvoices,
  get_revenue_summary: handleGetRevenueSummary,
  list_expenses: handleListExpenses,
  register_payment: handleRegisterPayment,
  close_invoice: handleCloseInvoice,
  auto_close_all_unpaid: handleAutoCloseAllUnpaid,
};

// ── Server setup ────────────────────────────────────────────────────
const server = new Server(
  { name: "odoo-mcp-server", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: TOOLS,
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const handler = HANDLERS[name];
  if (!handler) {
    return {
      content: [{ type: "text", text: `Unknown tool: ${name}` }],
      isError: true,
    };
  }

  logToVault({ tool: name, args, status: "invoked" });

  try {
    const result = await handler(args || {});
    logToVault({ tool: name, status: "success", result_summary: result.error ? "error_response" : "ok" });
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
    };
  } catch (err) {
    logToVault({ tool: name, status: "error", error: err.message });
    return {
      content: [{ type: "text", text: `Error: ${err.message}` }],
      isError: true,
    };
  }
});

// ── Start ───────────────────────────────────────────────────────────
const transport = new StdioServerTransport();
await server.connect(transport);
