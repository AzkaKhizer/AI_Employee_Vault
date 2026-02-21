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

// ── Dispatch map ────────────────────────────────────────────────────
const HANDLERS = {
  create_draft_invoice: handleCreateDraftInvoice,
  list_unpaid_invoices: handleListUnpaidInvoices,
  get_revenue_summary: handleGetRevenueSummary,
  list_expenses: handleListExpenses,
  register_payment: handleRegisterPayment,
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
