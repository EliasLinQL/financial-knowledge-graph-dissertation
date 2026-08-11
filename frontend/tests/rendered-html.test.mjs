import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("assistant-test", `${process.pid}-${Date.now()}-${Math.random()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker;
}

test("server-renders the analyst dashboard shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html[^>]+lang=["']en["']/i);
  assert.match(html, /<title>Financial Event Intelligence<\/title>/i);
  assert.match(html, /Loading checked research data/);
  assert.doesNotMatch(html, /正在载入已检查的研究数据/);
  assert.match(html, /og-financial-intelligence\.png/);
});

test("defaults to English and ships a complete language switch", async () => {
  const [source, css] = await Promise.all([
    readFile(new URL("../app/dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/dashboard.module.css", import.meta.url), "utf8"),
  ]);

  assert.match(source, /useState<Language>\("en"\)/);
  assert.match(source, /document\.documentElement\.lang/);
  assert.match(source, /aria-pressed=\{language === option\.id\}/);
  assert.match(source, /Turn company news into clear research leads\./);
  assert.match(source, /把公司新闻整理成清晰、可核查的研究线索。/);
  assert.match(source, /Knowledge graph/);
  assert.match(source, /知识图谱/);
  assert.match(source, /CompanyNetwork/);
  assert.match(source, /NewsPathQuery/);
  assert.match(source, /Analyze this event with AI/);
  assert.match(source, /Ask AI to analyze the selected event/);
  assert.match(source, /让 AI 分析这个事件/);
  assert.match(source, /让 AI 分析所选事件/);
  assert.match(source, /aria-controls="research-assistant-dialog"/);
  assert.match(source, /onAnalyzeEvent\(\{[\s\S]*?companyId:\s*company\.companyId,[\s\S]*?eventId:\s*selectedEventRow\.event\.eventId,[\s\S]*?eventTitle:\s*selectedEventRow\.event\.title,[\s\S]*?\}\)/);
  assert.match(source, /selectedCompanyId:\s*analysisRequest\.companyId/);
  assert.match(source, /selectedEventId:\s*analysisRequest\.eventId/);
  assert.match(source, /handledAnalysisRequestRef\.current = analysisRequest\.requestId;[\s\S]*?void ask/);
  assert.equal((source.match(/fetch\("\/api\/research-assistant"/g) ?? []).length, 1);
  assert.match(source, /Which two companies share the most events in this snapshot\?/);
  assert.match(source, /当前数据中哪两家公司共同事件最多？/);
  assert.match(source, /answerTitle: "Analysis"/);
  assert.match(source, /answerTitle: "分析结果"/);
  assert.match(source, /openai_incomplete_output/);
  assert.match(source, /AI answer was cut off/);
  assert.match(source, /AI 回答被截断/);
  assert.match(source, /key=\{selectedCompany\.companyId\}/);
  assert.match(source, /company\?\.symbol \?\? node\.companyId/);
  assert.match(source, /scrollIntoView/);
  assert.match(source, /setFocusOnly\(true\)/);
  assert.match(source, /setLineDash\(qualified \? \[\] : \[5, 5\]\)/);
  assert.match(source, /34 \+ normalisedCoverage \* 44/);
  assert.match(source, /legendNodeSmall/);
  assert.match(source, /legendNodeMedium/);
  assert.match(source, /legendNodeLarge/);
  assert.match(source, /More events, larger bubble/);
  assert.match(source, /事件越多，气泡越大/);
  assert.match(source, /buildNetworkLayout/);
  assert.match(source, /placeIsolateLane/);
  assert.match(source, /placeFocusedStar/);
  assert.match(source, /const ring = nodes/);
  assert.doesNotMatch(source, /const layers = new Map<number, NetworkNode\[]>/);
  assert.match(css, /\.networkIsolateRail/);
  assert.match(source, /sameSelectedEvent/);
  assert.match(source, /sameArticleOtherEvent/);
  assert.doesNotMatch(source, /styles\.graphGuide/);
  assert.match(source, /Why companies have two different labels/);
  assert.match(source, /为什么公司会有两种标记/);
  assert.match(source, /otherEventExample/);
  assert.match(source, /Before publication/);
  assert.match(source, /报道发布前/);
  assert.match(source, /Go from a company event to its source article/);
  assert.match(source, /从公司事件找到来源报道/);
  assert.match(source, /Company link score/);
  assert.match(source, /公司关联分/);
  assert.doesNotMatch(source, /"GDS NETWORK"|"GDS 网络"|"Relationship confidence"|"关联置信度"|"公司聚焦分"/);
  assert.match(source, /articleKeyFor/);
  assert.match(source, /SharedEventHeatmap/);
  assert.match(source, /MarketReturnChart/);
  assert.match(source, /marketReturnVerticalTrack/);
  assert.doesNotMatch(source, /marketReturnRow/);
  assert.doesNotMatch(source, /localStorage|navigator\.language/);
  assert.match(css, /\.languageToggle/);
  assert.match(css, /\.eventAiAction/);
  assert.match(css, /html\[lang="zh-CN"\]/);
  assert.match(css, /text-wrap:\s*balance/);
  assert.match(css, /grid-template-columns:\s*repeat\(6,/);
});

test("ships a credential-free dashboard snapshot", async () => {
  const raw = await readFile(
    new URL("../public/data/dashboard.json", import.meta.url),
    "utf8",
  );
  const snapshot = JSON.parse(raw);

  assert.ok(snapshot.summary.companyCount >= 2);
  assert.equal(snapshot.summary.validationFailureCount, 0);
  assert.ok(snapshot.events.length > 0);
  assert.ok(snapshot.impacts.length > 0);
  assert.doesNotMatch(raw, /neo4j:\/\/|bolt:\/\/|password/i);
  assert.ok(snapshot.market.every((row) => row.causalClaim === false));
  assert.deepEqual(
    [...new Set(snapshot.market.map((row) => row.windowDays))].sort((a, b) => a - b),
    [-7, -3, -1, 1, 3, 7],
  );
  assert.equal(snapshot.visualizations.schemaVersion, 1);
  assert.ok(snapshot.visualizations.timeSeries.months.length > 0);
  assert.equal(
    snapshot.visualizations.timeSeries.companies.length,
    snapshot.summary.companyCount,
  );
  assert.ok(snapshot.visualizations.sharedEventMatrix.cells.length > 0);
  assert.ok(snapshot.visualizations.sharedEventMatrix.maximumSharedEventCount > 0);
  const articleCompanies = new Map();
  for (const source of snapshot.sources) {
    const key = source.articleId || source.url;
    if (!articleCompanies.has(key)) articleCompanies.set(key, new Set());
    articleCompanies.get(key).add(source.companyId);
  }
  assert.ok([...articleCompanies.values()].some((companies) => companies.size > 1));
});

test("research assistant gives a checked-data preview without an API key", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const response = await worker.fetch(
    new Request("http://localhost/api/research-assistant", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        language: "en",
        question: "Summarise the selected company.",
        selectedCompanyId: "C001",
      }),
    }),
    {
      ASSETS: {
        fetch: async (request) => {
          assert.equal(new URL(request.url).pathname, "/data/dashboard.json");
          return new Response(raw, { headers: { "content-type": "application/json" } });
        },
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const result = await response.json();
  assert.equal(result.mode, "preview");
  assert.equal(result.status, "not_configured");
  assert.equal(result.tool, "explain_company");
  assert.equal(result.evidence.companies[0].companyId, "C001");
  assert.ok(result.evidence.events.length > 0);
  assert.ok(result.evidence.events.some((event) =>
    JSON.stringify(event.market.map((row) => row.windowDays))
      === JSON.stringify([-7, -3, -1, 1, 3, 7])));
  assert.doesNotMatch(JSON.stringify(result), /OPENAI_API_KEY|neo4j:\/\/|bolt:\/\//i);
});

test("research assistant analyses the exact event selected in the graph", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const snapshot = JSON.parse(raw);
  const target = snapshot.impacts.find((impact) =>
    snapshot.events.some((event) => event.eventId === impact.eventId));
  assert.ok(target, "the checked snapshot should contain at least one company-event impact");

  const response = await worker.fetch(
    new Request("http://localhost/api/research-assistant", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        language: "en",
        question: "Analyze the event selected in the knowledge graph.",
        selectedCompanyId: target.companyId,
        selectedEventId: target.eventId,
      }),
    }),
    {
      ASSETS: {
        fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }),
      },
    },
    { waitUntil() {}, passThroughOnException() {} },
  );

  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.mode, "preview");
  assert.equal(result.tool, "explain_event");
  assert.equal(result.evidence.events[0].companyId, target.companyId);
  assert.equal(result.evidence.events[0].eventId, target.eventId);
  assert.equal(typeof result.evidence.events[0].summary, "string");
  assert.equal(typeof result.evidence.events[0].relationshipProbability, "number");
  assert.equal(result.evidence.events[0].linkedCompanyEvidence[0].companyId, target.companyId);
  assert.deepEqual(
    result.evidence.events[0].market.map((row) => row.windowDays),
    [-7, -3, -1, 1, 3, 7],
  );
});

test("research assistant finds the global top shared-event pair in English and Chinese", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const snapshot = JSON.parse(raw);
  const topEdge = snapshot.network.edges
    .slice()
    .sort((a, b) =>
      b.sharedEventCount - a.sharedEventCount
      || a.company1Id.localeCompare(b.company1Id)
      || a.company2Id.localeCompare(b.company2Id))[0];
  const topPairIds = [topEdge.company1Id, topEdge.company2Id].sort();
  const unrelatedCompany = snapshot.companies.find(
    (company) => !topPairIds.includes(company.companyId),
  ) ?? snapshot.companies[0];
  assert.ok(unrelatedCompany);

  for (const [language, question] of [
    ["en", "Which two companies share the most events? Explain their relationship."],
    ["en", "What company pair has the highest number of related news events?"],
    ["en", "Analyze the two companies with the largest event overlap."],
    ["en", "Which pair co-occurs most in the checked news?"],
    ["zh", "当前数据中哪两家公司之间关联事件最多，分析他们之间的关系。"],
    ["zh", "当前数据中哪两家公司相关事件最多，能不能分析这两家公司的关系？"],
    ["zh", "共同新闻最多的公司组合是哪一对？"],
    ["zh", "哪些公司共同事件最多？"],
    ["zh", "公司之间共现最多的是哪一对？"],
  ]) {
    const response = await worker.fetch(
      new Request("http://localhost/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          language,
          question,
          selectedCompanyId: unrelatedCompany.companyId,
        }),
      }),
      {
        ASSETS: {
          fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }),
        },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );

    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.tool, "explain_connection");
    assert.deepEqual(
      result.evidence.companies.map((company) => company.companyId).sort(),
      topPairIds,
    );
    assert.equal(result.evidence.connections[0].sharedEventCount, topEdge.sharedEventCount);
    assert.equal(result.evidence.connections[0].meetsSupportThreshold, topEdge.meetsSupportThreshold);
    assert.equal(result.evidence.events.length, Math.min(6, topEdge.sharedEventCount));
    assert.ok(result.evidence.events.every((event) =>
      topPairIds.every((companyId) => event.linkedCompanyIds.includes(companyId))));
    assert.ok(result.evidence.events.every((event) =>
      topPairIds.every((companyId) =>
        event.linkedCompanyEvidence.some((row) => row.companyId === companyId))));
  }
});

test("research assistant does not treat a single-company event ranking as a pair ranking", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);

  for (const [language, question] of [
    ["en", "Which company has the most related events?"],
    ["en", "Top two companies by event count"],
    ["zh", "哪家公司相关事件最多？"],
    ["zh", "哪两家公司事件最多？"],
  ]) {
    const response = await worker.fetch(
      new Request("http://localhost/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ language, question, selectedCompanyId: "" }),
      }),
      {
        ASSETS: {
          fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }),
        },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );

    assert.equal(response.status, 200);
    const result = await response.json();
    assert.notEqual(result.tool, "explain_connection");
  }
});

test("research assistant sends the ranked top-pair evidence to OpenAI", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const snapshot = JSON.parse(raw);
  const topEdge = snapshot.network.edges
    .slice()
    .sort((a, b) => b.sharedEventCount - a.sharedEventCount)[0];
  const topCompanies = [topEdge.company1Id, topEdge.company2Id]
    .map((companyId) => snapshot.companies.find((company) => company.companyId === companyId));
  const originalFetch = globalThis.fetch;
  let requestBody;
  globalThis.fetch = async (_request, init) => {
    requestBody = JSON.parse(String(init?.body ?? "{}"));
    return new Response(JSON.stringify({ output_text: "Checked top-pair explanation." }), {
      headers: { "content-type": "application/json" },
    });
  };

  try {
    for (const model of ["gpt-4.1-mini", "gpt-5-mini", "gpt-5-mini-2025-08-07"]) {
      requestBody = undefined;
      const response = await worker.fetch(
        new Request("https://research.example/api/research-assistant", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            language: "zh",
            question: "当前数据中哪两家公司相关事件最多，能不能分析这两家公司的关系？",
            selectedCompanyId: "C001",
          }),
        }),
        {
          OPENAI_API_KEY: "test-secret-key",
          OPENAI_MODEL: model,
          ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
        },
        { waitUntil() {}, passThroughOnException() {} },
      );
      assert.equal(response.status, 200);
      const result = await response.json();
      assert.equal(result.status, "ready");
      assert.equal(result.tool, "explain_connection");
      assert.equal(requestBody.model, model);
      if (model.startsWith("gpt-5-mini")) {
        assert.deepEqual(requestBody.reasoning, { effort: "low" });
        assert.deepEqual(requestBody.text, { verbosity: "medium" });
        assert.equal(requestBody.max_output_tokens, 8_192);
      } else {
        assert.equal(requestBody.reasoning, undefined);
        assert.equal(requestBody.text, undefined);
        assert.equal(requestBody.max_output_tokens, 900);
      }
      assert.match(requestBody.instructions, /为什么会一起出现.*代表性事件.*怎么理解/);
      assert.match(requestBody.instructions, /不要使用“结论”“主要依据”“研究解读”“关系性质与限制”/);
      assert.match(requestBody.instructions, /区分直接互动与仅仅被同一主题覆盖/);
      assert.match(requestBody.instructions, /不要写“CHECKED_DATA 中 sharedEventCount=19”/);
      const readerMaterial = String(requestBody.input[0].content);
      assert.match(readerMaterial, /已核验研究材料/);
      assert.doesNotMatch(readerMaterial, /CHECKED_DATA|"tool"|sharedEventCount|meetsSupportThreshold|rankingPosition|companyId|eventId/);
      const suppliedData = JSON.parse(readerMaterial.split("已核验研究材料:\n")[1]);
      assert.equal(suppliedData["完整统计"]["共同关联事件总数"], topEdge.sharedEventCount);
      assert.equal(suppliedData["完整统计"]["在所有有共同事件的公司组合中的排名"], 1);
      assert.equal(suppliedData["代表性共同事件"].length, Math.min(6, topEdge.sharedEventCount));
      assert.equal(
        Object.values(suppliedData["完整统计"]["共同事件主题分布"]).reduce((sum, count) => sum + count, 0),
        topEdge.sharedEventCount,
      );
      assert.ok(suppliedData["完整统计"]["共同事件时间范围"]["最早"]);
      assert.ok(suppliedData["完整统计"]["共同事件时间范围"]["最近"]);
      for (const company of topCompanies) {
        assert.ok(readerMaterial.includes(company.name));
      }
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant reports safe diagnostics when OpenAI is unavailable", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  const cases = new Map([
    [400, "openai_http_400"],
    [401, "openai_http_401"],
    [403, "openai_http_403"],
    [404, "openai_http_404"],
    [429, "openai_http_429"],
    [500, "openai_http_5xx"],
    [503, "openai_http_5xx"],
  ]);
  let status = 401;
  globalThis.fetch = async () => new Response(
    JSON.stringify({ error: { message: "sensitive upstream response body" } }),
    { status, headers: { "content-type": "application/json" } },
  );

  try {
    for (const [httpStatus, safeCode] of cases) {
      status = httpStatus;
      const response = await worker.fetch(
        new Request("http://localhost/api/research-assistant", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ question: "Summarise the selected company", language: "en", selectedCompanyId: "C001" }),
        }),
        {
          OPENAI_API_KEY: "test-secret-key",
          ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
        },
        { waitUntil() {}, passThroughOnException() {} },
      );
      assert.equal(response.status, 200);
      const result = await response.json();
      assert.equal(result.mode, "preview");
      assert.equal(result.status, "unavailable");
      assert.equal(result.errorCode, safeCode);
      assert.doesNotMatch(JSON.stringify(result), /test-secret-key|sensitive upstream response body/i);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant reports ready only after an AI answer succeeds", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  let callCount = 0;
  const requestBodies = [];
  const requestedUrls = [];
  globalThis.fetch = async (request, init) => {
    callCount += 1;
    requestedUrls.push(typeof request === "string" || request instanceof URL ? request.toString() : request.url);
    requestBodies.push(JSON.parse(String(init?.body ?? "{}")));
    return new Response(JSON.stringify({ output_text: "Alphabet has several checked event links. Open the evidence cards to verify them." }), {
      headers: { "content-type": "application/json" },
    });
  };

  try {
    const response = await worker.fetch(
      new Request("http://localhost/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: "Summarise the selected company", language: "en", selectedCompanyId: "C001" }),
      }),
      {
        OPENAI_API_KEY: "test-secret-key",
        ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.mode, "ai");
    assert.equal(result.status, "ready");
    assert.equal(result.errorCode, undefined);
    assert.equal(callCount, 1);
    assert.deepEqual(requestedUrls, ["https://api.openai.com/v1/responses"]);
    assert.equal(requestBodies[0].tool_choice, undefined);
    assert.equal(requestBodies[0].tools, undefined);
    assert.equal(requestBodies[0].store, false);
    assert.equal(requestBodies[0].max_output_tokens, 900);
    assert.match(requestBodies[0].instructions, /research colleague helping a financial analyst/i);
    assert.match(requestBodies[0].instructions, /Aggregate figures describe the full result/i);
    assert.match(requestBodies[0].instructions, /representative examples/i);
    assert.match(requestBodies[0].instructions, /Use short sentences and reader-facing terms/i);
    assert.match(requestBodies[0].instructions, /Never reveal internal field names, IDs, booleans, JSON/i);
    assert.match(requestBodies[0].instructions, /Rewrite every technical value for the reader/i);
    assert.match(requestBodies[0].instructions, /two or three dated examples/i);
    assert.match(requestBodies[0].instructions, /Why they appear together, Representative events, and How to read this/);
    assert.match(requestBodies[0].instructions, /160 to 240 words/);
    assert.match(requestBodies[0].instructions, /Avoid generic disclaimers, stock endings/i);
    assert.doesNotMatch(requestBodies[0].instructions, /no more than 120 words|Tell the user to open the evidence/i);
    assert.match(JSON.stringify(requestBodies[0].input), /VERIFIED RESEARCH MATERIAL/);
    assert.doesNotMatch(JSON.stringify(requestBodies[0].input), /CHECKED_DATA|explain_company|companyId|eventId/);
    assert.equal(result.tool, "explain_company");
    assert.equal(result.evidence.companies[0].companyId, "C001");
    assert.doesNotMatch(JSON.stringify(result), /test-secret-key/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant rewrites an answer that exposes internal fields or report-style headings", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  const requestBodies = [];
  globalThis.fetch = async (_request, init) => {
    requestBodies.push(JSON.parse(String(init?.body ?? "{}")));
    const outputText = requestBodies.length === 1
      ? "结论\nCHECKED_DATA 中 sharedEventCount=19，meetsSupportThreshold=true。"
      : "在这组新闻中，Alphabet 与 Amazon 共同出现在19个去重事件里，是所有公司组合中最多的一组。它们经常同时出现，主要因为都受到人工智能投入、科技公司财报和监管议题的关注。";
    return new Response(JSON.stringify({ status: "completed", output_text: outputText }), {
      headers: { "content-type": "application/json" },
    });
  };

  try {
    const response = await worker.fetch(
      new Request("https://research.example/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          language: "zh",
          question: "当前数据中哪两家公司共同事件最多？",
          selectedCompanyId: "C001",
        }),
      }),
      {
        OPENAI_API_KEY: "test-secret-key",
        OPENAI_MODEL: "gpt-4.1-mini",
        ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(requestBodies.length, 2);
    assert.equal(result.mode, "ai");
    assert.equal(result.status, "ready");
    assert.match(result.answer, /Alphabet.*Amazon.*19/);
    assert.doesNotMatch(result.answer, /CHECKED_DATA|sharedEventCount|meetsSupportThreshold|^结论/m);
    assert.match(requestBodies[1].instructions, /上一版没有满足面向读者的表达要求/);
    assert.equal(requestBodies[1].max_output_tokens, 4_096);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant never exposes a second schema-leaking draft", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  let callCount = 0;
  globalThis.fetch = async () => {
    callCount += 1;
    return new Response(JSON.stringify({
      status: "completed",
      output_text: callCount === 1
        ? "Conclusion\nCHECKED_DATA sharedEventCount=19"
        : "Main evidence\nmeetsSupportThreshold=true",
    }), { headers: { "content-type": "application/json" } });
  };

  try {
    const response = await worker.fetch(
      new Request("https://research.example/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: "Which companies share the most events?", language: "en" }),
      }),
      {
        OPENAI_API_KEY: "test-secret-key",
        ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(callCount, 2);
    assert.equal(result.mode, "preview");
    assert.equal(result.status, "unavailable");
    assert.equal(result.errorCode, "openai_unavailable");
    assert.doesNotMatch(result.answer, /CHECKED_DATA|sharedEventCount|meetsSupportThreshold/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant retries one token-limited GPT-5 mini response without exposing partial text", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  const requestBodies = [];
  globalThis.fetch = async (_request, init) => {
    requestBodies.push(JSON.parse(String(init?.body ?? "{}")));
    if (requestBodies.length === 1) {
      return new Response(JSON.stringify({
        status: "incomplete",
        incomplete_details: { reason: "max_output_tokens" },
        output_text: "PARTIAL_SENTINEL",
      }), { headers: { "content-type": "application/json" } });
    }
    return new Response(JSON.stringify({
      status: "completed",
      output_text: "COMPLETE_SENTINEL",
    }), { headers: { "content-type": "application/json" } });
  };

  try {
    const response = await worker.fetch(
      new Request("https://research.example/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: "Summarise the selected company", language: "en", selectedCompanyId: "C001" }),
      }),
      {
        OPENAI_API_KEY: "test-secret-key",
        OPENAI_MODEL: "gpt-5-mini",
        ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.mode, "ai");
    assert.equal(result.status, "ready");
    assert.equal(result.answer, "COMPLETE_SENTINEL");
    assert.equal(requestBodies.length, 2);
    assert.equal(requestBodies[0].max_output_tokens, 8_192);
    assert.equal(requestBodies[1].max_output_tokens, 16_384);
    assert.deepEqual(requestBodies[0].reasoning, { effort: "low" });
    assert.deepEqual(requestBodies[1].reasoning, { effort: "low" });
    assert.doesNotMatch(JSON.stringify(result), /PARTIAL_SENTINEL/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant safely falls back when the GPT-5 mini retry is still incomplete", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  let callCount = 0;
  globalThis.fetch = async () => {
    callCount += 1;
    return new Response(JSON.stringify({
      status: "incomplete",
      incomplete_details: { reason: "max_output_tokens" },
      output_text: `PARTIAL_SENTINEL_${callCount}`,
    }), { headers: { "content-type": "application/json" } });
  };

  try {
    const response = await worker.fetch(
      new Request("https://research.example/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: "Summarise the selected company", language: "en", selectedCompanyId: "C001" }),
      }),
      {
        OPENAI_API_KEY: "test-secret-key",
        OPENAI_MODEL: "gpt-5-mini",
        ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(callCount, 2);
    assert.equal(result.mode, "preview");
    assert.equal(result.status, "unavailable");
    assert.equal(result.errorCode, "openai_incomplete_output");
    assert.equal(result.tool, "explain_company");
    assert.ok(result.evidence.companies.length > 0);
    assert.doesNotMatch(JSON.stringify(result), /PARTIAL_SENTINEL/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant uses the fixed local relay only for localhost development", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  const requestedUrls = [];
  globalThis.fetch = async (request) => {
    requestedUrls.push(typeof request === "string" || request instanceof URL ? request.toString() : request.url);
    return new Response(JSON.stringify({ output_text: "Checked explanation." }), {
      headers: { "content-type": "application/json" },
    });
  };

  try {
    for (const origin of ["http://localhost", "https://research.example"]) {
      const response = await worker.fetch(
        new Request(`${origin}/api/research-assistant`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ question: "Summarise the selected company", language: "en", selectedCompanyId: "C001" }),
        }),
        {
          OPENAI_API_KEY: "test-secret-key",
          OPENAI_LOCAL_RELAY_ENABLED: "1",
          ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
        },
        { waitUntil() {}, passThroughOnException() {} },
      );
      assert.equal(response.status, 200);
      assert.equal((await response.json()).status, "ready");
    }
    assert.deepEqual(requestedUrls, [
      "http://localhost/__openai_responses",
      "https://api.openai.com/v1/responses",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant maps fetch failures to a safe network diagnostic", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new TypeError("simulated network detail that must not be returned");
  };

  try {
    const response = await worker.fetch(
      new Request("http://localhost/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: "Summarise the selected company", language: "en", selectedCompanyId: "C001" }),
      }),
      {
        OPENAI_API_KEY: "test-secret-key",
        ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.mode, "preview");
    assert.equal(result.status, "unavailable");
    assert.equal(result.errorCode, "openai_network_error");
    assert.doesNotMatch(JSON.stringify(result), /test-secret-key|simulated network detail/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant distinguishes an OpenAI timeout from a connection failure", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new DOMException("simulated timeout detail that must not be returned", "AbortError");
  };

  try {
    const response = await worker.fetch(
      new Request("http://localhost/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: "Summarise the selected company", language: "en", selectedCompanyId: "C001" }),
      }),
      {
        OPENAI_API_KEY: "test-secret-key",
        ASSETS: { fetch: async () => new Response(raw, { headers: { "content-type": "application/json" } }) },
      },
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.mode, "preview");
    assert.equal(result.status, "unavailable");
    assert.equal(result.errorCode, "openai_timeout");
    assert.doesNotMatch(JSON.stringify(result), /test-secret-key|simulated timeout detail/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant loads the public snapshot in dev without an ASSETS binding", async () => {
  const [worker, raw] = await Promise.all([
    loadWorker(),
    readFile(new URL("../public/data/dashboard.json", import.meta.url), "utf8"),
  ]);
  const originalFetch = globalThis.fetch;
  let snapshotFetches = 0;
  globalThis.fetch = async (request, options) => {
    const url = new URL(typeof request === "string" || request instanceof URL ? request : request.url);
    if (url.pathname === "/data/dashboard.json") {
      snapshotFetches += 1;
      return new Response(raw, { headers: { "content-type": "application/json" } });
    }
    return originalFetch(request, options);
  };

  try {
    const response = await worker.fetch(
      new Request("http://localhost/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          language: "zh",
          question: "公司关联分是不是事件真实的概率？",
          selectedCompanyId: "C001",
        }),
      }),
      {},
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(snapshotFetches, 1);
    assert.equal(result.mode, "preview");
    assert.match(result.answer, /不是事件真实/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("research assistant returns a JSON 503 when the checked snapshot cannot be read", async () => {
  const worker = await loadWorker();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (request, options) => {
    const url = new URL(typeof request === "string" || request instanceof URL ? request : request.url);
    if (url.pathname === "/data/dashboard.json") throw new Error("simulated local asset failure");
    return originalFetch(request, options);
  };

  try {
    const response = await worker.fetch(
      new Request("http://localhost/api/research-assistant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: "Explain this snapshot", language: "en" }),
      }),
      {},
      { waitUntil() {}, passThroughOnException() {} },
    );
    assert.equal(response.status, 503);
    assert.match(response.headers.get("content-type") ?? "", /^application\/json\b/i);
    assert.deepEqual(await response.json(), { error: "Checked dashboard data is unavailable" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
