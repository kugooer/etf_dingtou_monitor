// Cloudflare Worker - 东方财富API中转
// 部署到: https://dash.cloudflare.com -> Workers & Pages -> 创建 worker

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const targetUrl = "https://push2his.eastmoney.com" + url.pathname + url.search;

    const headers = new Headers(request.headers);
    headers.set("User-Agent", "Mozilla/5.0");
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ray");

    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.body
    });

    const corsHeaders = new Headers(response.headers);
    corsHeaders.set("Access-Control-Allow-Origin", "*");
    corsHeaders.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    corsHeaders.set("Access-Control-Allow-Headers", "Content-Type, User-Agent");

    return new Response(response.body, {
      status: response.status,
      headers: corsHeaders
    });
  }
};