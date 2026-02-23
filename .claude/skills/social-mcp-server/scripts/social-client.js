/**
 * social-client.js — Platform API clients for X, Facebook, Instagram.
 *
 * All credentials are read from environment variables only.
 * No hardcoded secrets anywhere in this file.
 *
 * Rate-limit tracking (in-memory, per process):
 *   X API v2 free tier:  17 posts / 24 hours (checked against local counter)
 *   Meta Graph API:      200 posts / 24 hours per page
 *   Counters reset on process restart — conservative, not persistent.
 */

import { TwitterApi } from "twitter-api-v2";
import fetch from "node-fetch";

// ── Rate limit config ─────────────────────────────────────────────────────────

const RATE_LIMITS = {
  x:         { window_ms: 24 * 60 * 60 * 1000, max_posts: 17  },
  facebook:  { window_ms: 24 * 60 * 60 * 1000, max_posts: 200 },
  instagram: { window_ms: 24 * 60 * 60 * 1000, max_posts: 200 },
};

// { platform -> { count: int, window_start: Date } }
const _rate_counters = {};

function _checkRateLimit(platform) {
  const limit = RATE_LIMITS[platform];
  if (!limit) return; // unknown platform — allow through

  const now = Date.now();
  if (!_rate_counters[platform]) {
    _rate_counters[platform] = { count: 0, window_start: now };
  }

  const counter = _rate_counters[platform];
  if (now - counter.window_start > limit.window_ms) {
    // Window expired — reset
    counter.count = 0;
    counter.window_start = now;
  }

  if (counter.count >= limit.max_posts) {
    const resets_in_ms = limit.window_ms - (now - counter.window_start);
    const resets_in_min = Math.ceil(resets_in_ms / 60000);
    throw new RateLimitError(
      platform,
      `Rate limit reached (${limit.max_posts} posts per 24h). Resets in ~${resets_in_min} min.`
    );
  }

  counter.count++;
}

export class RateLimitError extends Error {
  constructor(platform, message) {
    super(message);
    this.name = "RateLimitError";
    this.platform = platform;
  }
}

// ── X (Twitter) ───────────────────────────────────────────────────────────────

function _buildXClient() {
  const required = [
    "X_API_KEY", "X_API_SECRET",
    "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
  ];
  for (const key of required) {
    if (!process.env[key]) {
      throw new Error(`Missing required env var: ${key}`);
    }
  }

  return new TwitterApi({
    appKey:            process.env.X_API_KEY,
    appSecret:         process.env.X_API_SECRET,
    accessToken:       process.env.X_ACCESS_TOKEN,
    accessSecret:      process.env.X_ACCESS_TOKEN_SECRET,
  });
}

/**
 * Post a tweet via X API v2.
 * @param {string} text — Tweet content (max 280 chars enforced by API)
 * @returns {object} Structured result
 */
export async function postToX(text) {
  _checkRateLimit("x");

  const client = _buildXClient();
  const response = await client.v2.tweet(text);

  return {
    success:      true,
    platform:     "x",
    post_id:      response.data?.id ?? null,
    message:      "Tweet posted successfully.",
    raw_response: response.data,
  };
}

/**
 * Fetch tweet metrics by tweet ID.
 * Requires X_BEARER_TOKEN for app-only auth.
 */
export async function getXMetrics(tweet_id) {
  if (!process.env.X_BEARER_TOKEN) {
    throw new Error("Missing required env var: X_BEARER_TOKEN");
  }

  const client = new TwitterApi(process.env.X_BEARER_TOKEN);
  const response = await client.v2.singleTweet(tweet_id, {
    "tweet.fields": ["public_metrics", "created_at", "text"],
  });

  return {
    success:      true,
    platform:     "x",
    post_id:      tweet_id,
    message:      "Metrics fetched successfully.",
    raw_response: response.data,
  };
}

// ── Facebook ──────────────────────────────────────────────────────────────────

const META_GRAPH_VERSION = "v18.0";
const META_GRAPH_BASE    = `https://graph.facebook.com/${META_GRAPH_VERSION}`;

/**
 * Post a message to a Facebook page feed.
 * Required env: FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN
 */
export async function postToFacebook(message, link = null) {
  _checkRateLimit("facebook");

  const pageId  = _requireEnv("FB_PAGE_ID");
  const token   = _requireEnv("FB_PAGE_ACCESS_TOKEN");
  const url     = `${META_GRAPH_BASE}/${pageId}/feed`;

  const body = { message, access_token: token };
  if (link) body.link = link;

  const res = await fetch(url, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(body),
  });

  const data = await _parseMetaResponse(res, "Facebook feed post");

  return {
    success:      true,
    platform:     "facebook",
    post_id:      data.id ?? null,
    message:      "Facebook post published successfully.",
    raw_response: data,
  };
}

/**
 * Fetch Facebook post insights.
 * post_id format: "{page_id}_{post_id}"
 */
export async function getFacebookMetrics(post_id) {
  const token = _requireEnv("FB_PAGE_ACCESS_TOKEN");
  const url   = `${META_GRAPH_BASE}/${post_id}/insights`
    + `?metric=post_impressions,post_engaged_users,post_clicks`
    + `&access_token=${token}`;

  const res  = await fetch(url);
  const data = await _parseMetaResponse(res, "Facebook insights");

  return {
    success:      true,
    platform:     "facebook",
    post_id,
    message:      "Facebook metrics fetched.",
    raw_response: data,
  };
}

// ── Instagram ─────────────────────────────────────────────────────────────────

/**
 * Create and publish an Instagram media post.
 *
 * Two-step process per Meta docs:
 *   1. POST /{IG_USER_ID}/media        → container_id
 *   2. POST /{IG_USER_ID}/media_publish → post_id
 *
 * Required env: IG_USER_ID, IG_ACCESS_TOKEN
 * Note: Instagram requires a public image_url for IMAGE posts.
 *       For caption-only (text), image_url is required — IG does not support text-only posts.
 */
export async function postToInstagram(caption, image_url) {
  _checkRateLimit("instagram");

  const igUserId = _requireEnv("IG_USER_ID");
  const token    = _requireEnv("IG_ACCESS_TOKEN");

  // Step 1: Create media container
  const containerUrl = `${META_GRAPH_BASE}/${igUserId}/media`;
  const containerRes = await fetch(containerUrl, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      caption,
      image_url,      // must be a publicly accessible URL
      access_token: token,
    }),
  });

  const container = await _parseMetaResponse(containerRes, "Instagram media container");
  const container_id = container.id;

  if (!container_id) {
    throw new Error("Instagram media container creation returned no ID.");
  }

  // Step 2: Publish the container
  const publishUrl = `${META_GRAPH_BASE}/${igUserId}/media_publish`;
  const publishRes = await fetch(publishUrl, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      creation_id:  container_id,
      access_token: token,
    }),
  });

  const published = await _parseMetaResponse(publishRes, "Instagram media_publish");

  return {
    success:      true,
    platform:     "instagram",
    post_id:      published.id ?? null,
    message:      "Instagram post published successfully.",
    raw_response: { container, published },
  };
}

/**
 * Fetch Instagram media insights.
 * Required env: IG_ACCESS_TOKEN
 */
export async function getInstagramMetrics(media_id) {
  const token = _requireEnv("IG_ACCESS_TOKEN");
  const url   = `${META_GRAPH_BASE}/${media_id}/insights`
    + `?metric=impressions,reach,likes,comments,saved`
    + `&access_token=${token}`;

  const res  = await fetch(url);
  const data = await _parseMetaResponse(res, "Instagram insights");

  return {
    success:      true,
    platform:     "instagram",
    post_id:      media_id,
    message:      "Instagram metrics fetched.",
    raw_response: data,
  };
}

// ── Internal helpers ──────────────────────────────────────────────────────────

function _requireEnv(key) {
  const val = process.env[key];
  if (!val) throw new Error(`Missing required env var: ${key}`);
  return val;
}

async function _parseMetaResponse(res, context) {
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw new Error(`${context}: non-JSON response (HTTP ${res.status})`);
  }

  if (!res.ok || data.error) {
    const err = data.error ?? {};
    throw new Error(
      `${context} failed (HTTP ${res.status}): `
      + `[${err.code ?? "?"}] ${err.message ?? JSON.stringify(data)}`
    );
  }

  return data;
}
