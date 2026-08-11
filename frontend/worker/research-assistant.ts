type Language = "en" | "zh";

type SnapshotCompany = {
  companyId: string;
  name: string;
  symbol: string;
  eventCount: number;
};

type SnapshotEvent = {
  eventId: string;
  date: string;
  type: string;
  title: string;
  summary: string;
};

type SnapshotImpact = {
  companyId: string;
  eventId: string;
  evidenceSentence: string;
  nlpRelationshipLabel: string;
  nlpPositiveProbability: number;
  relationshipFocusScore: number;
};

type SnapshotSource = {
  companyId: string;
  eventId: string;
  articleId: string;
  articleTitle: string;
  publicationTimestamp: string;
  url: string;
  evidenceSentence: string;
  isRepresentative: boolean;
  isRelationshipSource: boolean;
};

type SnapshotMarket = {
  companyId: string;
  eventId: string;
  windowDays: number;
  cumulativeReturn: number;
  baselineDate: string;
  windowEndDate: string;
  causalClaim: boolean;
};

type SnapshotEdge = {
  company1Id: string;
  company2Id: string;
  sharedEventCount: number;
  meetsSupportThreshold: boolean;
};

type DashboardSnapshot = {
  scope: {
    snapshotId?: string;
    publisher: string;
    eventDateRange: { start: string; end: string };
  };
  summary: {
    companyCount: number;
    eventCount: number;
    impactCount: number;
    sourceArticleCount: number;
    marketWindowCount: number;
  };
  companies: SnapshotCompany[];
  events: SnapshotEvent[];
  impacts: SnapshotImpact[];
  sources: SnapshotSource[];
  market: SnapshotMarket[];
  network: { edges: SnapshotEdge[] };
};

type AssistantRequest = {
  question?: unknown;
  language?: unknown;
  selectedCompanyId?: unknown;
  selectedEventId?: unknown;
};

type EvidenceCompany = Pick<SnapshotCompany, "companyId" | "name" | "symbol" | "eventCount">;

type EvidenceEvent = {
  companyId: string;
  eventId: string;
  title: string;
  summary: string;
  date: string;
  type: string;
  evidenceSentence: string;
  relationshipLabel: string;
  relationshipProbability: number;
  relationshipFocusScore: number;
  sourceTitle: string;
  sourceUrl: string;
  linkedCompanyIds: string[];
  linkedCompanyEvidence: Array<{
    companyId: string;
    evidenceSentence: string;
    relationshipLabel: string;
    relationshipProbability: number;
    relationshipFocusScore: number;
  }>;
  market: Array<Pick<SnapshotMarket, "windowDays" | "cumulativeReturn" | "baselineDate" | "windowEndDate">>;
};

type AssistantEvidence = {
  companies: EvidenceCompany[];
  events: EvidenceEvent[];
  connections: Array<{
    company1Id: string;
    company1: string;
    company2Id: string;
    company2: string;
    sharedEventCount: number;
    meetsSupportThreshold: boolean;
  }>;
};

type ToolName = "explain_snapshot" | "explain_company" | "explain_event" | "explain_connection";

type ToolResult = {
  tool: ToolName;
  facts: Record<string, unknown>;
  evidence: AssistantEvidence;
};

type OpenAIOutputItem = {
  type?: string;
  name?: string;
  arguments?: string;
  call_id?: string;
  content?: Array<{ type?: string; text?: string }>;
  [key: string]: unknown;
};

type OpenAIResponse = {
  status?: string;
  incomplete_details?: { reason?: string } | null;
  output?: OpenAIOutputItem[];
  output_text?: string;
};

type AssistantStatus = "not_configured" | "unavailable" | "ready";
type SafeOpenAIErrorCode =
  | "openai_http_400"
  | "openai_http_401"
  | "openai_http_403"
  | "openai_http_404"
  | "openai_http_429"
  | "openai_http_5xx"
  | "openai_timeout"
  | "openai_network_error"
  | "openai_tool_missing"
  | "openai_tool_args_invalid"
  | "openai_empty_output"
  | "openai_incomplete_output"
  | "openai_unavailable";

class OpenAIRequestError extends Error {
  readonly safeCode: SafeOpenAIErrorCode;

  constructor(safeCode: SafeOpenAIErrorCode) {
    super("OpenAI request unavailable");
    this.name = "OpenAIRequestError";
    this.safeCode = safeCode;
  }
}

export type ResearchAssistantEnv = {
  OPENAI_API_KEY?: string;
  OPENAI_MODEL?: string;
  OPENAI_LOCAL_RELAY_ENABLED?: string;
};

const EMPTY_EVIDENCE = (): AssistantEvidence => ({ companies: [], events: [], connections: [] });
const MAX_QUESTION_LENGTH = 800;
const ASSISTANT_ANALYSIS_EVENT_LIMIT = 6;
const OPENAI_REQUEST_TIMEOUT_MS = 45_000;
const OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses";
const DEFAULT_MAX_OUTPUT_TOKENS = 900;
const GPT5_MINI_MAX_OUTPUT_TOKENS = 8_192;
const GPT5_MINI_RETRY_MAX_OUTPUT_TOKENS = 16_384;
const OPENAI_LOCAL_RELAY_PATH = "/__openai_responses";
const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

const EVENT_TYPE_LABELS: Record<Language, Record<string, string>> = {
  en: {
    corporate_event: "company developments",
    regulatory_event: "regulation and policy",
    market_wide_event: "market movements",
    macroeconomic_event: "macroeconomy",
    geopolitical_event: "geopolitics",
    other_event: "other news",
  },
  zh: {
    corporate_event: "公司动态",
    regulatory_event: "监管与政策",
    market_wide_event: "市场走势",
    macroeconomic_event: "宏观经济",
    geopolitical_event: "地缘政治",
    other_event: "其他新闻",
  },
};

function eventTypeLabel(type: string, language: Language) {
  return EVENT_TYPE_LABELS[language][type] ?? (language === "zh" ? "其他新闻" : "other news");
}

function periodLabel(windowDays: number, language: Language) {
  const direction = windowDays < 0
    ? (language === "zh" ? "报道前" : "before publication")
    : (language === "zh" ? "报道后" : "after publication");
  const days = Math.abs(windowDays);
  return language === "zh"
    ? `${direction}${days}个交易日`
    : `${days} trading day${days === 1 ? "" : "s"} ${direction}`;
}

function readerFacingMaterial(result: ToolResult, language: Language) {
  const companyNames = new Map(result.evidence.companies.map((company) => [company.companyId, company.name]));
  const eventTypeCounts = (result.facts.eventTypeCounts ?? {}) as Record<string, number>;
  const themes = Object.fromEntries(Object.entries(eventTypeCounts).map(
    ([type, count]) => [eventTypeLabel(type, language), count],
  ));
  const representativeEvents = result.evidence.events.map((event) => {
    const companies = event.linkedCompanyIds
      .map((companyId) => companyNames.get(companyId))
      .filter((name): name is string => Boolean(name));
    const linkEvidence = event.linkedCompanyEvidence
      .map((row) => {
        const company = companyNames.get(row.companyId);
        if (!company || !row.evidenceSentence) return null;
        return language === "zh"
          ? { 公司: company, 关联依据: row.evidenceSentence }
          : { company, linkingEvidence: row.evidenceSentence };
      })
      .filter((row): row is NonNullable<typeof row> => Boolean(row));
    const marketContext = event.market.map((row) => {
      const percentageChange = row.cumulativeReturn * 100;
      return language === "zh"
      ? {
          时间段: periodLabel(row.windowDays, language),
          累计价格变化: `${percentageChange >= 0 ? "+" : ""}${percentageChange.toFixed(2)}%`,
          起始日期: row.baselineDate,
          截止日期: row.windowEndDate,
        }
      : {
          period: periodLabel(row.windowDays, language),
          cumulativePriceChange: `${percentageChange >= 0 ? "+" : ""}${percentageChange.toFixed(2)}%`,
          startDate: row.baselineDate,
          endDate: row.windowEndDate,
        };
    });

    return language === "zh"
      ? {
          日期: event.date,
          类型: eventTypeLabel(event.type, language),
          事件: event.title,
          摘要: event.summary,
          与所选公司的关联依据: event.evidenceSentence,
          来源报道: event.sourceTitle,
          同一事件涉及的公司: companies,
          各公司的关联依据: linkEvidence,
          报道前后的价格背景: marketContext,
        }
      : {
          date: event.date,
          category: eventTypeLabel(event.type, language),
          event: event.title,
          summary: event.summary,
          evidenceLinkingTheSelectedCompany: event.evidenceSentence,
          sourceArticle: event.sourceTitle,
          companiesInTheSameEvent: companies,
          evidenceForEachCompany: linkEvidence,
          priceContextAroundPublication: marketContext,
        };
  });
  const companies = result.evidence.companies.map((company) => language === "zh"
    ? { 公司名称: company.name, 股票代码: company.symbol, 关联事件数: company.eventCount }
    : { company: company.name, ticker: company.symbol, linkedEvents: company.eventCount });
  const dateRange = result.facts.eventDateRange as { first?: string | null; latest?: string | null } | undefined;

  if (language === "zh") {
    if (result.tool === "explain_snapshot") {
      return {
        数据范围: {
          新闻来源: result.facts.publisher,
          起止日期: result.facts.dateRange,
          公司数量: result.facts.companyCount,
          去重事件数量: result.facts.eventCount,
          公司与事件的关联数量: result.facts.impactCount,
          可追溯的来源文章数量: result.facts.sourceArticleCount,
          价格观察窗口数量: result.facts.marketWindowCount,
        },
      };
    }
    if (result.tool === "explain_company") {
      return {
        公司: companies[0],
        完整统计: {
          关联事件总数: result.facts.linkedEventCount,
          事件时间范围: dateRange ? { 最早: dateRange.first, 最近: dateRange.latest } : undefined,
          事件主题分布: themes,
        },
        代表性事件: representativeEvents,
      };
    }
    if (result.tool === "explain_event") {
      return {
        相关公司: companies,
        找到的相关事件数量: result.facts.matchCount,
        供分析的代表性事件: representativeEvents,
      };
    }
    return {
      比较公司: companies,
      完整统计: {
        共同关联事件总数: result.facts.sharedEventCount,
        在所有有共同事件的公司组合中的排名: result.facts.rankingPosition,
        比较过的公司组合数量: result.facts.comparisonPairCount,
        共同事件时间范围: dateRange ? { 最早: dateRange.first, 最近: dateRange.latest } : undefined,
        共同事件主题分布: themes,
      },
      代表性共同事件: representativeEvents,
    };
  }

  if (result.tool === "explain_snapshot") {
    return {
      coverage: {
        newsSource: result.facts.publisher,
        dateRange: result.facts.dateRange,
        companies: result.facts.companyCount,
        deduplicatedEvents: result.facts.eventCount,
        companyEventLinks: result.facts.impactCount,
        traceableSourceArticles: result.facts.sourceArticleCount,
        priceObservationWindows: result.facts.marketWindowCount,
      },
    };
  }
  if (result.tool === "explain_company") {
    return {
      company: companies[0],
      fullResult: {
        linkedEventTotal: result.facts.linkedEventCount,
        eventDateRange: dateRange,
        eventThemes: themes,
      },
      representativeEvents,
    };
  }
  if (result.tool === "explain_event") {
    return {
      companies,
      matchingEventTotal: result.facts.matchCount,
      representativeEvents,
    };
  }
  return {
    companies,
    fullResult: {
      sharedEventTotal: result.facts.sharedEventCount,
      rankAmongCompanyPairsWithSharedEvents: result.facts.rankingPosition,
      companyPairsCompared: result.facts.comparisonPairCount,
      sharedEventDateRange: dateRange,
      sharedEventThemes: themes,
    },
    representativeSharedEvents: representativeEvents,
  };
}

const normalise = (value: string) => value.trim().toLocaleLowerCase().replace(/\s+/g, " ");
const companyFor = (snapshot: DashboardSnapshot, companyId: string) =>
  snapshot.companies.find((company) => company.companyId === companyId);

function representativeSource(snapshot: DashboardSnapshot, companyId: string, eventId: string) {
  const rows = snapshot.sources.filter((row) => row.companyId === companyId && row.eventId === eventId);
  return rows.find((row) => row.isRelationshipSource && row.isRepresentative)
    ?? rows.find((row) => row.isRelationshipSource)
    ?? rows.find((row) => row.isRepresentative)
    ?? rows[0];
}

function eventEvidence(
  snapshot: DashboardSnapshot,
  companyId: string,
  eventId: string,
  linkedCompanyIds: string[] = [companyId],
): EvidenceEvent | null {
  const event = snapshot.events.find((row) => row.eventId === eventId);
  const impact = snapshot.impacts.find((row) => row.companyId === companyId && row.eventId === eventId);
  if (!event || !impact) return null;
  const source = representativeSource(snapshot, companyId, eventId);
  const market = snapshot.market
    .filter((row) => row.companyId === companyId && row.eventId === eventId && row.causalClaim === false)
    .sort((a, b) => a.windowDays - b.windowDays)
    .map(({ windowDays, cumulativeReturn, baselineDate, windowEndDate }) => ({
      windowDays,
      cumulativeReturn,
      baselineDate,
      windowEndDate,
    }));

  return {
    companyId,
    eventId,
    title: event.title,
    summary: event.summary,
    date: event.date,
    type: event.type,
    evidenceSentence: impact.evidenceSentence,
    relationshipLabel: impact.nlpRelationshipLabel,
    relationshipProbability: impact.nlpPositiveProbability,
    relationshipFocusScore: impact.relationshipFocusScore,
    sourceTitle: source?.articleTitle ?? "",
    sourceUrl: source?.url ?? "",
    linkedCompanyIds: [...new Set(linkedCompanyIds)],
    linkedCompanyEvidence: [...new Set(linkedCompanyIds)]
      .map((linkedCompanyId) => snapshot.impacts.find(
        (row) => row.companyId === linkedCompanyId && row.eventId === eventId,
      ))
      .filter((row): row is SnapshotImpact => Boolean(row))
      .map((row) => ({
        companyId: row.companyId,
        evidenceSentence: row.evidenceSentence,
        relationshipLabel: row.nlpRelationshipLabel,
        relationshipProbability: row.nlpPositiveProbability,
        relationshipFocusScore: row.relationshipFocusScore,
      })),
    market,
  };
}

function aggregateEventSet(snapshot: DashboardSnapshot, eventIds: string[]) {
  const events = eventIds
    .map((eventId) => snapshot.events.find((event) => event.eventId === eventId))
    .filter((event): event is SnapshotEvent => Boolean(event));
  const eventTypeCounts = events.reduce<Record<string, number>>((counts, event) => {
    counts[event.type] = (counts[event.type] ?? 0) + 1;
    return counts;
  }, {});
  const dates = events.map((event) => event.date).filter(Boolean).sort();
  return {
    eventTypeCounts,
    eventDateRange: {
      first: dates[0] ?? null,
      latest: dates.at(-1) ?? null,
    },
  };
}

function explainSnapshot(snapshot: DashboardSnapshot): ToolResult {
  return {
    tool: "explain_snapshot",
    facts: {
      publisher: snapshot.scope.publisher,
      dateRange: snapshot.scope.eventDateRange,
      ...snapshot.summary,
      interpretation: "The snapshot supports evidence tracing and descriptive comparison, not causal or investment claims.",
      glossary: {
        companyLinkScore: "Model confidence that the evidence sentence links the event to the company; it is not the probability that the event is true.",
        marketWindow: "Observed return before or after publication; it does not show that the news caused the move.",
        sharedEvent: "A deduplicated news event linked to both companies in this corpus; it is not proof of a business relationship.",
        eventCount: "Coverage inside the selected Guardian corpus and date range, not all real-world company activity.",
      },
    },
    evidence: EMPTY_EVIDENCE(),
  };
}

function explainCompany(snapshot: DashboardSnapshot, companyId: string): ToolResult {
  const company = companyFor(snapshot, companyId);
  if (!company) return explainSnapshot(snapshot);
  const eventIds = [...new Set(snapshot.impacts.filter((row) => row.companyId === company.companyId).map((row) => row.eventId))]
    .sort((a, b) => {
      const eventA = snapshot.events.find((row) => row.eventId === a);
      const eventB = snapshot.events.find((row) => row.eventId === b);
      return (eventB?.date ?? "").localeCompare(eventA?.date ?? "");
    });
  const events = eventIds.slice(0, ASSISTANT_ANALYSIS_EVENT_LIMIT)
    .map((eventId) => eventEvidence(snapshot, company.companyId, eventId))
    .filter((event): event is EvidenceEvent => Boolean(event));

  return {
    tool: "explain_company",
    facts: {
      company: company.name,
      symbol: company.symbol,
      linkedEventCount: eventIds.length,
      latestEventDate: events[0]?.date ?? null,
      suppliedEventExampleCount: events.length,
      ...aggregateEventSet(snapshot, eventIds),
      note: "A lower count can reflect corpus coverage and must not be read as absence of real-world activity.",
    },
    evidence: { companies: [company], events, connections: [] },
  };
}

function searchScore(query: string, event: SnapshotEvent, impact: SnapshotImpact, source?: SnapshotSource) {
  if (!query) return 1;
  const needle = normalise(query);
  if (normalise(event.eventId) === needle) return 1000;
  const haystack = normalise(`${event.title} ${event.summary} ${impact.evidenceSentence} ${source?.articleTitle ?? ""}`);
  if (haystack.includes(needle)) return 100;
  const tokens = needle.split(/[^\p{L}\p{N}]+/u).filter((token) => token.length >= 3);
  return tokens.reduce((score, token) => score + (haystack.includes(token) ? 1 : 0), 0);
}

function explainEvent(snapshot: DashboardSnapshot, companyId: string | null, query: string): ToolResult {
  const impacts = companyId ? snapshot.impacts.filter((row) => row.companyId === companyId) : snapshot.impacts;
  const matches = impacts
    .map((impact) => {
      const event = snapshot.events.find((row) => row.eventId === impact.eventId);
      if (!event) return null;
      const source = representativeSource(snapshot, impact.companyId, impact.eventId);
      return { impact, event, score: searchScore(query, event, impact, source) };
    })
    .filter((row): row is { impact: SnapshotImpact; event: SnapshotEvent; score: number } => Boolean(row))
    .filter((row) => !query || row.score > 0)
    .sort((a, b) => b.score - a.score || b.event.date.localeCompare(a.event.date));

  const chosen = (matches.length ? matches : impacts.map((impact) => {
    const event = snapshot.events.find((row) => row.eventId === impact.eventId);
    return event ? { impact, event, score: 0 } : null;
  }).filter((row): row is { impact: SnapshotImpact; event: SnapshotEvent; score: number } => Boolean(row))
    .sort((a, b) => b.event.date.localeCompare(a.event.date))).slice(0, 3);

  const events = chosen
    .map(({ impact }) => eventEvidence(snapshot, impact.companyId, impact.eventId))
    .filter((event): event is EvidenceEvent => Boolean(event));
  const companies = [...new Set(events.map((event) => event.companyId))]
    .map((id) => companyFor(snapshot, id))
    .filter((company): company is SnapshotCompany => Boolean(company));

  return {
    tool: "explain_event",
    facts: {
      query: query || null,
      matchCount: matches.length,
      returnedEventCount: events.length,
      note: "Market windows are descriptive observations before and after publication; they do not establish causality.",
    },
    evidence: { companies, events, connections: [] },
  };
}

function explainConnection(snapshot: DashboardSnapshot, companyOneId: string, companyTwoId: string): ToolResult {
  const companyOne = companyFor(snapshot, companyOneId);
  const companyTwo = companyFor(snapshot, companyTwoId);
  if (!companyOne || !companyTwo) return explainSnapshot(snapshot);
  if (companyOne.companyId === companyTwo.companyId) return explainCompany(snapshot, companyOne.companyId);

  const firstEvents = new Set(snapshot.impacts.filter((row) => row.companyId === companyOne.companyId).map((row) => row.eventId));
  const sharedEventIds = [...new Set(snapshot.impacts
    .filter((row) => row.companyId === companyTwo.companyId && firstEvents.has(row.eventId))
    .map((row) => row.eventId))]
    .sort((a, b) => {
      const eventA = snapshot.events.find((row) => row.eventId === a);
      const eventB = snapshot.events.find((row) => row.eventId === b);
      return (eventB?.date ?? "").localeCompare(eventA?.date ?? "");
    });
  const edge = snapshot.network.edges.find((row) =>
    (row.company1Id === companyOne.companyId && row.company2Id === companyTwo.companyId)
    || (row.company1Id === companyTwo.companyId && row.company2Id === companyOne.companyId));
  const events = sharedEventIds.slice(0, ASSISTANT_ANALYSIS_EVENT_LIMIT)
    .map((eventId) => eventEvidence(snapshot, companyOne.companyId, eventId, [companyOne.companyId, companyTwo.companyId]))
    .filter((event): event is EvidenceEvent => Boolean(event));

  return {
    tool: "explain_connection",
    facts: {
      companyOne: companyOne.name,
      companyTwo: companyTwo.name,
      sharedEventCount: sharedEventIds.length,
      displayedNetworkCount: edge?.sharedEventCount ?? sharedEventIds.length,
      meetsSupportThreshold: edge?.meetsSupportThreshold ?? false,
      suppliedEventExampleCount: events.length,
      ...aggregateEventSet(snapshot, sharedEventIds),
      note: "A shared event is a corpus co-occurrence pattern, not proof of a commercial relationship.",
    },
    evidence: {
      companies: [companyOne, companyTwo],
      events,
      connections: [{
        company1Id: companyOne.companyId,
        company1: companyOne.name,
        company2Id: companyTwo.companyId,
        company2: companyTwo.name,
        sharedEventCount: sharedEventIds.length,
        meetsSupportThreshold: edge?.meetsSupportThreshold ?? false,
      }],
    },
  };
}

function explainTopConnection(snapshot: DashboardSnapshot): ToolResult {
  const rankedEdges = snapshot.network.edges
    .filter((edge) =>
      edge.sharedEventCount > 0
      && Boolean(companyFor(snapshot, edge.company1Id))
      && Boolean(companyFor(snapshot, edge.company2Id)))
    .slice()
    .sort((a, b) =>
      b.sharedEventCount - a.sharedEventCount
      || a.company1Id.localeCompare(b.company1Id)
      || a.company2Id.localeCompare(b.company2Id));
  const topEdge = rankedEdges[0];
  if (!topEdge) return explainSnapshot(snapshot);

  const result = explainConnection(snapshot, topEdge.company1Id, topEdge.company2Id);
  const tiedTopPairCount = rankedEdges.filter(
    (edge) => edge.sharedEventCount === topEdge.sharedEventCount,
  ).length;
  return {
    ...result,
    facts: {
      ...result.facts,
      rankingPosition: 1,
      comparisonPairCount: rankedEdges.length,
      tiedTopPairCount,
      rankingMetric: "Number of deduplicated events shared by both companies",
      rankingScope: "All company pairs with a shared event in the checked snapshot",
    },
  };
}

function mentionedCompanies(snapshot: DashboardSnapshot, question: string) {
  const text = normalise(question);
  const aliases: Record<string, string[]> = {
    C003: ["google", "谷歌"],
    C007: ["tsmc", "台积电", "臺積電"],
    C009: ["facebook", "脸书", "臉書"],
    C017: ["jpmorgan", "摩根大通"],
    C030: ["bank of america", "美国银行", "美國銀行"],
  };
  return snapshot.companies
    .filter((company) => {
      const name = normalise(company.name);
      const shortName = normalise(company.name.replace(/\b(incorporated|inc\.?|corporation|company|holdings?|n\.v\.)\b/gi, "").replace(/[.&]+$/g, ""));
      const symbol = normalise(company.symbol);
      const aliasMatch = (aliases[company.companyId] ?? []).some((alias) => text.includes(normalise(alias)));
      return aliasMatch || text.includes(name) || (shortName.length >= 4 && text.includes(shortName)) || (symbol.length >= 2 && new RegExp(`(^|[^a-z0-9])${symbol.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^a-z0-9]|$)`, "i").test(text));
    })
    .sort((a, b) => b.name.length - a.name.length);
}

function hasTopConnectionIntent(question: string) {
  const text = normalise(question);
  const asksForPair = /哪(?:两|2)家(?:公司)?|哪些两家|哪些公司|两家公司|公司(?:组合|对)|哪(?:一)?对(?:公司)?|哪组公司/.test(text)
    || /\b(?:which|what)\s+(?:two\s+)?companies\b|\btwo\s+companies\b|\b(?:company\s+)?pair\b/.test(text);
  const asksForMaximum = /最多|最高|最大|最强|第一|首位/.test(text)
    || /\b(?:most|highest|maximum|top|largest|strongest)\b/.test(text);
  const mentionsRelationship = /共同|共享|相同|重合|重叠|关联|相关|共现/.test(text)
    || /\b(?:share|shared|sharing|related|linked|connected|common|overlapping|overlap|co-?occur(?:s|red|ring|rence)?)\b/.test(text);
  const mentionsEvent = /事件|新闻|报道|提及/.test(text)
    || /\b(?:events?|news|articles?|mentions?)\b/.test(text);
  const unambiguousOverlap = /共同|共享|重合|重叠|共现/.test(text)
    || /\b(?:share|shared|sharing|overlapping|overlap|co-?occur(?:s|red|ring|rence)?)\b/.test(text);

  // Requiring an explicit pair marker prevents questions such as
  // “Which company has the most related events?” from being mistaken for a
  // request to rank company pairs.
  return asksForPair
    && asksForMaximum
    && (unambiguousOverlap || (mentionsRelationship && mentionsEvent));
}

function choosePreviewTool(
  snapshot: DashboardSnapshot,
  question: string,
  selectedCompanyId: string,
  selectedEventId = "",
) {
  const selectedImpact = selectedEventId
    ? snapshot.impacts.find((impact) =>
      impact.eventId === selectedEventId
      && (!selectedCompanyId || impact.companyId === selectedCompanyId))
    : null;
  if (selectedImpact) {
    return explainEvent(snapshot, selectedImpact.companyId, selectedEventId);
  }
  const mentioned = mentionedCompanies(snapshot, question);
  if (mentioned.length >= 2) {
    return explainConnection(snapshot, mentioned[0].companyId, mentioned[1].companyId);
  }
  const companyId = mentioned[0]?.companyId || (companyFor(snapshot, selectedCompanyId)?.companyId ?? "");
  const eventIntent = /\bevent\b|\bnews\b|\barticle\b|事件|新闻|报道|证据/i.test(question);
  const connectionIntent = /connect|relationship|shared|related|overlap|关联|联系|共同|关系|相关|共享|重叠|重合/i.test(question);
  const topConnectionIntent = hasTopConnectionIntent(question);
  const termIntent = /score|probability|return|caus|mean|line|count|分数|概率|收益|因果|含义|意思|连线|数量/i.test(question);
  if (topConnectionIntent) return explainTopConnection(snapshot);
  if (termIntent && mentioned.length === 0) return explainSnapshot(snapshot);
  if (connectionIntent && mentioned.length === 1) return explainCompany(snapshot, companyId);
  if (eventIntent) return explainEvent(snapshot, companyId || null, "");
  if (companyId) return explainCompany(snapshot, companyId);
  return explainSnapshot(snapshot);
}

function previewAnswer(result: ToolResult, language: Language, question = "") {
  if (result.tool === "explain_company") {
    const company = result.evidence.companies[0];
    const latest = result.evidence.events[0];
    if (language === "zh") {
      return `${company?.name ?? "该公司"}在这份已检查的 12 个月新闻快照中关联了 ${result.facts.linkedEventCount ?? 0} 个事件。${latest ? `最近一条是 ${latest.date} 的“${latest.title}”。` : "目前没有可展示的事件。"}你可以打开下方依据，查看证据句、原始报道和报道前后 1、3、7 个交易日的价格背景。`;
    }
    return `${company?.name ?? "This company"} is linked to ${result.facts.linkedEventCount ?? 0} events in the checked 12-month news snapshot. ${latest ? `The latest item is “${latest.title}” (${latest.date}). ` : "No event is available to display. "}Open the evidence below to inspect the sentence, original article, and 1-, 3-, and 7-trading-day price context before and after publication.`;
  }
  if (result.tool === "explain_event") {
    const event = result.evidence.events[0];
    if (!event) return language === "zh" ? "没有找到与这个问题匹配的事件。请换一个公司或更短的关键词。" : "No matching event was found. Try another company or a shorter keyword.";
    if (language === "zh") {
      return `最接近的结果是 ${event.date} 的“${event.title}”。系统依据下方证据句把它与公司关联；来源按钮可以返回原始报道。价格窗口只用于补充市场背景，不能说明该事件造成了涨跌。`;
    }
    return `The closest result is “${event.title}” (${event.date}). The company link is supported by the evidence sentence below, and the source button returns to the original report. Market windows add context only; they do not show that the event caused a price move.`;
  }
  if (result.tool === "explain_connection") {
    const [one, two] = result.evidence.companies;
    const isTopPair = result.facts.rankingPosition === 1;
    if (language === "zh") {
      return `${one?.name ?? "公司一"}与${two?.name ?? "公司二"}在当前语料中共有 ${result.facts.sharedEventCount ?? 0} 个去重事件。${isTopPair ? "这是当前已检查公司组合中的最高数量。" : ""}下方列出最近的共同事件供核查。这表示两家公司出现在相同新闻事件中，不代表已经确认的商业关系。`;
    }
    return `${one?.name ?? "Company one"} and ${two?.name ?? "company two"} share ${result.facts.sharedEventCount ?? 0} deduplicated events in this corpus. ${isTopPair ? "This is the highest count among the checked company pairs. " : ""}The latest examples appear below. This shows shared news-event coverage, not a confirmed commercial relationship.`;
  }
  const facts = result.facts;
  if (/score|probability|分数|概率/i.test(question)) {
    return language === "zh"
      ? "公司关联分表示模型认为证据句与公司有关的把握程度。它不是事件真实、风险发生或价格上涨的概率；请打开证据句和原始报道核查。"
      : "The company link score is the model's confidence that the evidence sentence links the event to the company. It is not the probability that the event is true, risky, or price-moving; open the evidence and original report to check it.";
  }
  if (/return|caus|price|收益|因果|价格/i.test(question)) {
    return language === "zh"
      ? "价格窗口展示报道发布前后 1、3、7 个交易日的实际变化，只用于补充背景。它不能说明新闻导致了这次涨跌。"
      : "Market windows show observed price changes 1, 3, and 7 trading days before and after publication. They add context only and cannot show that the news caused the move.";
  }
  if (/line|shared|relationship|连线|共同|关系/i.test(question)) {
    return language === "zh"
      ? "图上的连线表示两家公司在当前语料中关联了共同的去重新闻事件。它用于发现研究线索，不代表两家公司存在已确认的商业关系。"
      : "A network line means two companies are linked to the same deduplicated news events in this corpus. It is a research lead, not proof of a confirmed business relationship.";
  }
  if (language === "zh") {
    return `这份已检查的快照覆盖 ${facts.companyCount ?? 0} 家公司、${facts.eventCount ?? 0} 个去重事件和 ${facts.sourceArticleCount ?? 0} 篇来源文章。你可以询问某家公司、某个事件或两家公司的共同事件。答案用于理解和追溯数据，不构成投资建议。`;
  }
  return `This checked snapshot covers ${facts.companyCount ?? 0} companies, ${facts.eventCount ?? 0} deduplicated events, and ${facts.sourceArticleCount ?? 0} source articles. Ask about a company, an event, or events shared by two companies. The answers support interpretation and evidence tracing, not investment advice.`;
}

function outputText(response: OpenAIResponse) {
  if (typeof response.output_text === "string" && response.output_text.trim()) return response.output_text.trim();
  return (response.output ?? [])
    .flatMap((item) => item.content ?? [])
    .filter((item) => item.type === "output_text" && typeof item.text === "string")
    .map((item) => item.text?.trim())
    .filter(Boolean)
    .join("\n")
    .trim();
}

const INTERNAL_ANSWER_TERMS = /\b(?:CHECKED_DATA|sharedEventCount|meetsSupportThreshold|rankingPosition|eventTypeCounts|displayedNetworkCount|suppliedEventExampleCount|relationshipProbability|relationshipFocusScore|companyId|eventId)\b|(?:sharedEventCount|meetsSupportThreshold)\s*=\s*(?:true|false|\d+)/i;
const REPORT_STYLE_HEADING = /(?:^|\n)\s*(?:#{1,6}\s*)?(?:结论|主要依据|研究解读|关系性质与限制|Conclusion|Main evidence|Research interpretation|Relationship limitations)\s*[:：]?\s*(?:\n|$)/im;

function needsReaderFacingRewrite(answer: string) {
  return INTERNAL_ANSWER_TERMS.test(answer) || REPORT_STYLE_HEADING.test(answer);
}

function isGPT5MiniModel(model: string) {
  return /^gpt-5-mini(?:-|$)/i.test(model);
}

function isOutputTokenLimit(response: OpenAIResponse) {
  return response.status === "incomplete" && response.incomplete_details?.reason === "max_output_tokens";
}

function ensureCompleteResponse(response: OpenAIResponse) {
  if (response.status === "incomplete") throw new OpenAIRequestError("openai_incomplete_output");
  if (response.status === "failed") throw new OpenAIRequestError("openai_unavailable");
}

function safeOpenAIErrorCode(status: number): SafeOpenAIErrorCode {
  if (status === 400) return "openai_http_400";
  if (status === 401 || status === 403 || status === 404 || status === 429) {
    return `openai_http_${status}` as SafeOpenAIErrorCode;
  }
  if (status >= 500 && status <= 599) return "openai_http_5xx";
  return "openai_unavailable";
}

function safeCodeFrom(error: unknown): SafeOpenAIErrorCode {
  return error instanceof OpenAIRequestError ? error.safeCode : "openai_unavailable";
}

function openAIEndpoint(requestUrl: string, env: ResearchAssistantEnv) {
  const request = new URL(requestUrl);
  if (env.OPENAI_LOCAL_RELAY_ENABLED === "1" && LOCAL_HOSTNAMES.has(request.hostname)) {
    return new URL(OPENAI_LOCAL_RELAY_PATH, request).toString();
  }
  return OPENAI_RESPONSES_URL;
}

function isAbortError(error: unknown) {
  return Boolean(error && typeof error === "object" && "name" in error && error.name === "AbortError");
}

async function openAIRequest(apiKey: string, body: Record<string, unknown>, endpoint: string) {
  let response: Response;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), OPENAI_REQUEST_TIMEOUT_MS);
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiKey}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted || isAbortError(error)) {
      throw new OpenAIRequestError("openai_timeout");
    }
    throw new OpenAIRequestError("openai_network_error");
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) throw new OpenAIRequestError(safeOpenAIErrorCode(response.status));
  try {
    return await response.json() as OpenAIResponse;
  } catch {
    throw new OpenAIRequestError("openai_unavailable");
  }
}

async function generatedAnswer(
  result: ToolResult,
  question: string,
  language: Language,
  apiKey: string,
  model: string,
  endpoint: string,
) {
  const focusByLanguage: Record<Language, Record<ToolName, string>> = {
    zh: {
      explain_snapshot: "先说明这份数据覆盖什么，再指出一两个最明显的研究线索，最后给出一个实际使用方式。",
      explain_company: "先说明这家公司关联多少事件以及报道集中在哪些时期，再归纳两到三个主要话题，并用近期事件解释这些话题。",
      explain_event: "先用普通语言说明发生了什么，再说明哪句话把事件与公司联系起来；有价格数据时，简要描述报道前后的变化，但不要把它说成因果关系。",
      explain_connection: "先说出两家公司及共同事件总数，再解释它们为什么经常出现在同类新闻中。区分直接互动与仅仅被同一主题覆盖，并用代表性事件说明判断。",
    },
    en: {
      explain_snapshot: "Explain the coverage first, then highlight one or two useful patterns and one practical way an analyst could use them.",
      explain_company: "State the event count and timing first, group the coverage into two or three themes, and explain those themes with recent examples.",
      explain_event: "Explain what happened, which evidence connects it to the company, and—when available—what the price data shows before and after publication without implying causation.",
      explain_connection: "Name the companies and shared-event total first. Explain the themes behind their repeated co-appearance, distinguish direct interaction from shared coverage, and support the interpretation with representative events.",
    },
  };
  const instructions = language === "zh"
    ? [
        "你是一名帮助金融分析师理解新闻数据的研究助理。请像熟悉业务的同事一样解释结果，直接、自然、易懂，不要写成数据库报告或学术论文。",
        "只根据下方已核验材料回答。统计数字代表完整结果，事件列表只是代表性例子；例子少于总数时，要明确称为“代表性例子”。资料不足时，直接说明能判断什么、不能判断什么，不要补充外部事实。",
        "第一段直接回答用户问题，并给出最关键的数字。随后先归纳主题，再按需要使用不超过三个自然标题：“为什么会一起出现”“代表性事件”“怎么理解”。不要使用“结论”“主要依据”“研究解读”“关系性质与限制”等报告式标题。",
        "使用自然中文、短句和中文标点。公司第一次出现时写全名，后文可以使用常见简称。日期写成中文常用格式。必要时引用两到三个带日期的事件，并说明每个例子为什么与判断有关。",
        "不得输出任何内部字段名、ID、布尔值、JSON、代码、模型或数据库术语，也不得提及承载材料的内部名称。把类别、数量和技术值都改写成普通读者能理解的语言。不要写“CHECKED_DATA 中 sharedEventCount=19”，应该写“在这组新闻中，两家公司共同出现在19个事件里”。",
        "两家公司共同出现在新闻事件中，只说明它们受到相同主题影响或在同一报道中出现。除非材料明确描述合作、合同、投资或交易，否则不要把它解释成商业关系。价格变化只用于描述报道前后的市场背景，不能说新闻导致了涨跌。",
        "不要逐项复述卡片，不要写套话、通用免责声明、投资建议或“请查看界面”。只在可能造成误解时，用一句自然的话说明具体限制。证据充足时写约280至450个中文字符；清楚比凑字数更重要。",
        focusByLanguage.zh[result.tool],
      ].join("\n")
    : [
        "You are a research colleague helping a financial analyst understand verified news data. Write directly and naturally for a reader who has never seen the graph schema; do not sound like a database report or academic paper.",
        "Use only the verified material below. Aggregate figures describe the full result and listed events are representative examples. If examples are fewer than the total, say so. If the material cannot answer part of the question, state exactly what can and cannot be concluded.",
        "Open with the direct answer and the most important number. Then summarise the themes before examples. When useful, use no more than three natural headings: Why they appear together, Representative events, and How to read this. Do not use report headings such as Conclusion, Main evidence, Research interpretation, or Relationship limitations.",
        "Use short sentences and reader-facing terms. Name a company in full the first time, then use its natural short name. Include two or three dated examples when they add distinct evidence, and explain why each example matters.",
        "Never reveal internal field names, IDs, booleans, JSON, schema, model, or database terminology, and never name the container holding the material. Rewrite every technical value for the reader. Do not write 'CHECKED_DATA has sharedEventCount=19'; write 'In this set of news, the two companies appeared together in 19 events.'",
        "Shared news events show overlapping coverage or exposure to the same topic. Do not describe them as a business relationship unless the evidence explicitly mentions cooperation, a contract, an investment, or a transaction. Treat price windows as market context only, never as proof that an article caused a move.",
        "Do not list every card. Avoid generic disclaimers, stock endings, investment advice, and instructions to inspect the interface. Include one specific caution only when it prevents a likely misunderstanding. Aim for 160 to 240 words when the evidence supports it; clarity matters more than length.",
        focusByLanguage.en[result.tool],
      ].join("\n");
  const isGPT5Mini = isGPT5MiniModel(model);
  const materialLabel = language === "zh" ? "已核验研究材料" : "VERIFIED RESEARCH MATERIAL";
  const requestBody: Record<string, unknown> = {
    model,
    instructions,
    input: [{
      role: "user",
      content: `${language === "zh" ? "用户问题" : "USER QUESTION"}:\n${question}\n\n${materialLabel}:\n${JSON.stringify(readerFacingMaterial(result, language))}`,
    }],
    max_output_tokens: isGPT5Mini ? GPT5_MINI_MAX_OUTPUT_TOKENS : DEFAULT_MAX_OUTPUT_TOKENS,
    store: false,
  };
  if (isGPT5Mini) {
    requestBody.reasoning = { effort: "low" };
    requestBody.text = { verbosity: "medium" };
  }

  let response = await openAIRequest(apiKey, requestBody, endpoint);
  if (isOutputTokenLimit(response)) {
    response = await openAIRequest(apiKey, {
      ...requestBody,
      max_output_tokens: isGPT5Mini
        ? GPT5_MINI_RETRY_MAX_OUTPUT_TOKENS
        : Math.max(DEFAULT_MAX_OUTPUT_TOKENS * 2, 4_096),
    }, endpoint);
  }
  ensureCompleteResponse(response);
  let answer = outputText(response);
  if (!answer) throw new OpenAIRequestError("openai_empty_output");
  if (needsReaderFacingRewrite(answer)) {
    const rewriteInstructions = language === "zh"
      ? `${instructions}\n上一版没有满足面向读者的表达要求。请从头重写，只保留结论、自然解释和代表性例子；不要提及上一版，也不要输出任何内部字段或报告式标题。`
      : `${instructions}\nThe previous draft did not meet the reader-facing writing standard. Rewrite it from scratch with only the answer, natural explanation, and representative examples. Do not mention the earlier draft or print any internal fields or report-style headings.`;
    const rewritten = await openAIRequest(apiKey, {
      ...requestBody,
      instructions: rewriteInstructions,
      max_output_tokens: isGPT5Mini ? GPT5_MINI_RETRY_MAX_OUTPUT_TOKENS : Math.max(DEFAULT_MAX_OUTPUT_TOKENS * 2, 4_096),
    }, endpoint);
    ensureCompleteResponse(rewritten);
    answer = outputText(rewritten);
    if (!answer) throw new OpenAIRequestError("openai_empty_output");
    if (needsReaderFacingRewrite(answer)) throw new OpenAIRequestError("openai_unavailable");
  }
  return answer;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

export async function handleResearchAssistant(
  request: Request,
  snapshot: DashboardSnapshot,
  env: ResearchAssistantEnv,
) {
  if (request.method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);
  let payload: AssistantRequest;
  try {
    payload = await request.json() as AssistantRequest;
  } catch {
    return jsonResponse({ error: "Invalid JSON body" }, 400);
  }
  const question = typeof payload.question === "string" ? payload.question.trim() : "";
  if (!question) return jsonResponse({ error: "Question is required" }, 400);
  if (question.length > MAX_QUESTION_LENGTH) return jsonResponse({ error: "Question is too long" }, 400);
  const language: Language = payload.language === "zh" ? "zh" : "en";
  const selectedCompanyId = typeof payload.selectedCompanyId === "string" ? payload.selectedCompanyId : "";
  const selectedEventId = typeof payload.selectedEventId === "string" ? payload.selectedEventId : "";
  const disclaimer = language === "zh"
    ? "仅用于解释当前已检查快照，不构成投资建议；价格窗口只提供背景，不表示因果关系。"
    : "Explains the current checked snapshot only. It is not investment advice, and market windows do not imply causality.";

  const result = choosePreviewTool(snapshot, question, selectedCompanyId, selectedEventId);
  const apiKey = env.OPENAI_API_KEY?.trim();
  if (apiKey) {
    try {
      const answer = await generatedAnswer(
        result,
        question,
        language,
        apiKey,
        env.OPENAI_MODEL?.trim() || "gpt-4.1-mini",
        openAIEndpoint(request.url, env),
      );
      return jsonResponse({
        mode: "ai",
        status: "ready" satisfies AssistantStatus,
        answer,
        tool: result.tool,
        evidence: result.evidence,
        disclaimer,
      });
    } catch (error) {
      return jsonResponse({
        mode: "preview",
        status: "unavailable" satisfies AssistantStatus,
        errorCode: safeCodeFrom(error),
        answer: previewAnswer(result, language, question),
        tool: result.tool,
        evidence: result.evidence,
        note: language === "zh"
          ? "已检测到 AI 配置，但当前无法连接。下面显示基于已检查数据的预览结果。"
          : "AI is configured but currently unavailable. The answer below is a preview built from checked data.",
        disclaimer,
      });
    }
  }

  return jsonResponse({
    mode: "preview",
    status: "not_configured" satisfies AssistantStatus,
    answer: previewAnswer(result, language, question),
    tool: result.tool,
    evidence: result.evidence,
    note: language === "zh"
      ? "当前为无 Key 预览：答案由已检查数据按固定模板生成。配置服务端 OpenAI API Key 后会改为模型解释。"
      : "No-key preview: this answer uses a fixed template over checked data. Add a server-side OpenAI API key to enable model-generated explanations.",
    disclaimer,
  });
}
