#!/usr/bin/env node

/**
 * Social MCP Server
 *
 * Exposes four tools over MCP stdio transport:
 *   1. post_to_x          — post a tweet via X API v2
 *   2. post_to_facebook   — post to a Facebook page feed
 *   3. post_to_instagram  — create + publish an Instagram media post
 *   4. get_post_metrics   — fetch engagement metrics for any posted content
 *
 * All tools:
 *   - Read credentials exclusively from environment variables (no hardcoded secrets)
 *   - Respect DRY_RUN=true (log intent only, no API call made)
 *   - Return structured JSON: { success, platform, post_id, message, raw_response }
 *   - Log every invocation to Vault/Logs/YYYY-MM-DD.json
 *
 * Required env vars:
 *   X:         X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, X_BEARER_TOKEN
 *   Facebook:  FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN
 *   Instagram: IG_USER_ID, IG_ACCESS_TOKEN
 *   Vault:     VAULT_ROOT (optional — auto-detected)
 *   Control:   DRY_RUN=true to skip real API calls
 */

import "dotenv/config";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

import {
  postToX,
  postToFacebook,
  postToInstagram,
  getXMetrics,
  getFacebookMetrics,
  getInstagramMetrics,
  RateLimitError,
} from "./social-client.js";
import { logToVault } from "./logger.js";

// ── DRY_RUN mode ──────────────────────────────────────────────────────────────

const DRY_RUN = process.env.DRY_RUN === "true";
if (DRY_RUN) {
  console.error("[social-mcp] DRY_RUN=true — no real API calls will be made.");
}

// ── Tool definitions ──────────────────────────────────────────────────────────

const TOOLS = [
  {
    name: "post_to_x",
    description:
      "Post a tweet to X (Twitter) via API v2. "
      + "Respects DRY_RUN. Returns post_id on success. "
      + "Max 280 characters enforced by the X API.",
    inputSchema: {
      type: "object",
      properties: {
        text: {
          type: "string",
          description: "Tweet content (max 280 characters).",
        },
      },
      required: ["text"],
    },
  },
  {
    name: "post_to_facebook",
    description:
      "Post a message to the configured Facebook page feed. "
      + "Respects DRY_RUN. Requires FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN.",
    inputSchema: {
      type: "object",
      properties: {
        message: {
          type: "string",
          description: "Post content / caption.",
        },
        link: {
          type: "string",
          description: "Optional URL to attach to the post.",
        },
      },
      required: ["message"],
    },
  },
  {
    name: "post_to_instagram",
    description:
      "Create and publish an Instagram media post (image + caption). "
      + "Instagram requires a publicly accessible image URL — text-only posts are not supported by the API. "
      + "Respects DRY_RUN. Requires IG_USER_ID and IG_ACCESS_TOKEN.",
    inputSchema: {
      type: "object",
      properties: {
        caption: {
          type: "string",
          description: "Post caption / text.",
        },
        image_url: {
          type: "string",
          description: "Publicly accessible URL of the image to post.",
        },
      },
      required: ["caption", "image_url"],
    },
  },
  {
    name: "get_post_metrics",
    description:
      "Fetch engagement metrics for a previously published post. "
      + "Specify platform and post_id. "
      + "X metrics require X_BEARER_TOKEN. "
      + "Meta metrics require the page/user access token.",
    inputSchema: {
      type: "object",
      properties: {
        platform: {
          type: "string",
          enum: ["x", "facebook", "instagram"],
          description: "Platform the post was published on.",
        },
        post_id: {
          type: "string",
          description:
            "Platform post ID returned by the posting tool. "
            + "For Facebook: '{page_id}_{post_id}' format. "
            + "For Instagram: media object ID.",
        },
      },
      required: ["platform", "post_id"],
    },
  },
];

// ── MCP Server setup ──────────────────────────────────────────────────────────

const server = new Server(
  { name: "social-mcp-server", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

// ── Tool list handler ─────────────────────────────────────────────────────────

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

// ── Tool call handler ─────────────────────────────────────────────────────────

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const startedAt = new Date().toISOString();

  logToVault({
    tool:    name,
    event:   "invoked",
    status:  "invoked",
    dry_run: DRY_RUN,
    args:    _sanitizeArgs(name, args),
  });

  try {
    const result = await _dispatch(name, args);

    logToVault({
      tool:           name,
      event:          "success",
      status:         "success",
      platform:       result.platform,
      post_id:        result.post_id,
      dry_run:        DRY_RUN,
      result_summary: result.message,
    });

    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };

  } catch (err) {
    const isRateLimit = err instanceof RateLimitError;
    const errorResult = {
      success:      false,
      platform:     _platformFromTool(name),
      post_id:      null,
      message:      err.message,
      raw_response: null,
      error_type:   isRateLimit ? "rate_limit" : "api_error",
    };

    logToVault({
      tool:        name,
      event:       "error",
      status:      "error",
      platform:    errorResult.platform,
      dry_run:     DRY_RUN,
      error:       err.message,
      error_type:  errorResult.error_type,
    });

    // Return error as structured JSON — not a thrown MCP error — so callers get the full payload
    return { content: [{ type: "text", text: JSON.stringify(errorResult, null, 2) }] };
  }
});

// ── Tool dispatcher ───────────────────────────────────────────────────────────

async function _dispatch(toolName, args) {
  switch (toolName) {

    // ── post_to_x ──────────────────────────────────────────────────────────
    case "post_to_x": {
      const { text } = args;
      _requireArg("text", text);

      if (text.length > 280) {
        throw new Error(`Tweet exceeds 280 characters (got ${text.length}). Shorten the content.`);
      }

      if (DRY_RUN) {
        return _dryRunResult("x", "tweet", { text_length: text.length });
      }

      return postToX(text);
    }

    // ── post_to_facebook ───────────────────────────────────────────────────
    case "post_to_facebook": {
      const { message, link } = args;
      _requireArg("message", message);

      if (DRY_RUN) {
        return _dryRunResult("facebook", "page_post", {
          message_length: message.length,
          has_link: !!link,
        });
      }

      return postToFacebook(message, link ?? null);
    }

    // ── post_to_instagram ──────────────────────────────────────────────────
    case "post_to_instagram": {
      const { caption, image_url } = args;
      _requireArg("caption",   caption);
      _requireArg("image_url", image_url);

      if (!image_url.startsWith("https://")) {
        throw new Error(
          "Instagram image_url must be a publicly accessible HTTPS URL. "
          + "The Meta API cannot access local paths or HTTP URLs."
        );
      }

      if (DRY_RUN) {
        return _dryRunResult("instagram", "media_post", {
          caption_length: caption.length,
          image_url,
        });
      }

      return postToInstagram(caption, image_url);
    }

    // ── get_post_metrics ───────────────────────────────────────────────────
    case "get_post_metrics": {
      const { platform, post_id } = args;
      _requireArg("platform", platform);
      _requireArg("post_id",  post_id);

      if (DRY_RUN) {
        return _dryRunResult(platform, "metrics_fetch", { post_id });
      }

      switch (platform) {
        case "x":
          return getXMetrics(post_id);
        case "facebook":
          return getFacebookMetrics(post_id);
        case "instagram":
          return getInstagramMetrics(post_id);
        default:
          throw new Error(`Unknown platform: "${platform}". Valid: x, facebook, instagram.`);
      }
    }

    default:
      throw new Error(`Unknown tool: "${toolName}"`);
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _requireArg(name, value) {
  if (!value || (typeof value === "string" && value.trim() === "")) {
    throw new Error(`Required argument "${name}" is missing or empty.`);
  }
}

function _dryRunResult(platform, action_type, preview = {}) {
  return {
    success:      true,
    platform,
    post_id:      `dry-run-${Date.now()}`,
    message:      `DRY_RUN=true — ${action_type} on ${platform} would have been submitted. No API call made.`,
    raw_response: { dry_run: true, action_type, ...preview },
  };
}

function _platformFromTool(toolName) {
  if (toolName.includes("x"))         return "x";
  if (toolName.includes("facebook"))  return "facebook";
  if (toolName.includes("instagram")) return "instagram";
  return "unknown";
}

/**
 * Remove credential values from logged args.
 * Arg keys are safe; values are only safe for non-credential fields.
 */
function _sanitizeArgs(toolName, args) {
  const safe = { ...args };
  // image_url and link are fine to log; message/text are content — log length only
  if (safe.text)    { safe.text    = `[${safe.text.length} chars]`; }
  if (safe.message) { safe.message = `[${safe.message.length} chars]`; }
  if (safe.caption) { safe.caption = `[${safe.caption.length} chars]`; }
  return safe;
}

// ── Start server ──────────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("[social-mcp] Server running on stdio.");
}

main().catch((err) => {
  console.error("[social-mcp] Fatal error:", err.message);
  process.exit(1);
});
