"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./dashboard.module.css";

type View = "overview" | "company" | "graph" | "alerts" | "relations" | "notes";
type Language = "en" | "zh";

type Company = {
  companyId: string;
  name: string;
  country: string;
  sourceRank: number;
  marketCapUsd: number;
  rankingSnapshotDate: string;
  symbol: string;
  eventCount: number;
};

type EventRecord = {
  eventId: string;
  date: string;
  type: string;
  title: string;
  summary: string;
  classificationConfidence: string;
  sourceEventCount: number;
  sourceArticleCount: number;
  deduplicationMethod: string;
};

type Impact = {
  companyId: string;
  eventId: string;
  evidenceSentence: string;
  nlpRelationshipLabel: string;
  nlpPositiveProbability: number;
  relationshipFocusScore: number;
  hybridDecisionReason: string;
  sourceUrl: string;
};

type Source = {
  companyId: string;
  eventId: string;
  sourceEventId: string;
  sourceEventDate: string;
  sourceEventTitle: string;
  evidenceSentence: string;
  evidenceSource: string;
  similarityToRepresentative: number;
  isRepresentative: boolean;
  articleId: string;
  articleTitle: string;
  publicationTimestamp: string;
  sectionName: string;
  url: string;
  isRelationshipSource: boolean;
};

type MarketObservation = {
  companyId: string;
  eventId: string;
  symbol: string;
  windowDays: number;
  baselineDate: string;
  windowEndDate: string;
  baselineClose: number;
  windowEndClose: number;
  cumulativeReturn: number;
  anchorRule: string;
  dataSource: string;
  causalClaim: boolean;
};

type Similarity = {
  company1Id: string;
  company1: string;
  company2Id: string;
  company2: string;
  similarity: number;
  sharedEventCount: number;
  eventUnionCount: number;
  meetsSupportThreshold: boolean;
};

type NetworkNode = {
  companyId: string;
  company: string;
  sourceRank: number;
  eventCount: number;
  coeventDegree: number;
  coeventStrength: number;
  isIsolate: boolean;
  wccComponent: string;
  louvainCommunity: string;
  pageRank: number;
};

type NetworkEdge = {
  company1Id: string;
  company1: string;
  company2Id: string;
  company2: string;
  sharedEventCount: number;
  meetsSupportThreshold: boolean;
};

type VisualizationData = {
  schemaVersion: number;
  timeSeries: {
    months: string[];
    portfolio: Array<{ month: string; eventCount: number; impactCount: number }>;
    companies: Array<{ companyId: string; values: number[] }>;
  };
  sharedEventMatrix: {
    companyIds: string[];
    maximumSharedEventCount: number;
    cells: Array<{
      company1Id: string;
      company2Id: string;
      sharedEventCount: number;
      similarity: number;
    }>;
  };
};

type DashboardData = {
  scope: {
    sourceGeneratedAtUtc: string;
    eventDateRange: { start: string; end: string };
    publisher: string;
    rankingSnapshotDate: string;
  };
  summary: {
    companyCount: number;
    eventCount: number;
    impactCount: number;
    sourceArticleCount: number;
    marketWindowCount: number;
    coveredCompanyCount: number;
    multiSourceEventCount: number;
    supportedNetworkEdgeCount: number;
    evaluationChecksPassed: number;
    evaluationChecksTotal: number;
  };
  companies: Company[];
  events: EventRecord[];
  impacts: Impact[];
  sources: Source[];
  market: MarketObservation[];
  visualizations: VisualizationData;
  network: {
    summary: { supportThreshold: number; wccCount: number; isolateCount: number };
    nodes: NetworkNode[];
    edges: NetworkEdge[];
    similarities: Similarity[];
  };
  evaluation: {
    summary: { useCaseCount: number; allTasksSucceeded: boolean; graphStateUnchanged: boolean };
    performance: Array<{
      taskId: string;
      resultRows: number;
      medianClientMs: number;
      p95ClientMs: number;
      resultHashStable: boolean;
    }>;
    tasks: Array<{ taskId: string; titleCn: string; titleEn: string; scope: string }>;
  };
  disclaimers: Array<{
    code: string;
    titleCn: string;
    titleEn: string;
    bodyCn: string;
    bodyEn: string;
  }>;
};

type Detail = { event: EventRecord; impact: Impact };

type AssistantAnalysisRequest = {
  requestId: number;
  companyId: string;
  eventId: string;
  eventTitle: string;
};

type AssistantMarketEvidence = {
  windowDays: number;
  cumulativeReturn: number;
};

type AssistantCompanyEvidence = {
  companyId: string;
  name: string;
  symbol: string;
  eventCount: number;
};

type AssistantEventEvidence = {
  companyId: string;
  eventId: string;
  title: string;
  date: string;
  type: string;
  evidenceSentence: string;
  sourceTitle: string;
  sourceUrl: string;
  market: AssistantMarketEvidence[];
};

type AssistantErrorCode =
  | "openai_http_400"
  | "openai_http_401"
  | "openai_http_403"
  | "openai_http_404"
  | "openai_http_429"
  | "openai_http_5xx"
  | "openai_network_error"
  | "openai_timeout"
  | "openai_tool_missing"
  | "openai_tool_args_invalid"
  | "openai_empty_output"
  | "openai_incomplete_output"
  | "openai_unavailable";

type AssistantResponse = {
  mode: "ai" | "preview";
  status: "not_configured" | "unavailable" | "ready";
  errorCode?: AssistantErrorCode;
  answer: string;
  tool: string;
  note?: string;
  evidence?: {
    companies?: AssistantCompanyEvidence[];
    events?: AssistantEventEvidence[];
    connections?: Array<{
      company1Id?: string;
      company1?: string;
      company2Id?: string;
      company2?: string;
      sharedEventCount?: number;
    }>;
  };
  disclaimer?: string;
};

type DashboardIndexes = {
  events: Map<string, EventRecord>;
  companies: Map<string, Company>;
  sources: Map<string, Source[]>;
  market: Map<string, MarketObservation[]>;
};

const COPY = {
  en: {
    pageTitle: "Financial Event Intelligence",
    brandName: "Financial Intelligence",
    brandSubtitle: "Company news research",
    workspace: "RESEARCH WORKSPACE",
    primaryNavigation: "Primary navigation",
    nav: {
      overview: { label: "Overview", eyebrow: "Key figures" },
      company: { label: "Company research", eyebrow: "Events by company" },
      graph: { label: "Knowledge graph", eyebrow: "Explore connections" },
      alerts: { label: "Event search", eyebrow: "Filter events" },
      relations: { label: "Company comparison", eyebrow: "Compare companies" },
      notes: { label: "About the data", eyebrow: "What the data means" },
    },
    snapshot: "Current data",
    searchLabel: "Search companies or tickers",
    searchPlaceholder: "Search companies or tickers",
    searchResults: "Company search results",
    eventCount: (count: number) => `${count} ${count === 1 ? "event" : "events"}`,
    noMatchingCompanies: "No matching companies",
    snapshotVerified: "Data checked",
    generatedAt: (date: string) => `Updated ${date}`,
    loadingTitle: "Loading checked research data",
    loadingBody: "The first load may take a few seconds.",
    errorTitle: "The data is temporarily unavailable",
    errorBody: "The research data could not be loaded. Update the data file, then refresh this page.",
    reload: "Reload",
    overview: {
      eyebrow: "12-MONTH OVERVIEW",
      title: "Turn company news into clear research leads.",
      body: "See each event, the source article, the companies involved and price changes before and after publication. Use the result as a lead, then check the original article.",
      corpus: "News source",
      dateRange: (start: string, end: string) => `${start} to ${end}`,
      metricsLabel: "Key figures",
      companies: "Companies covered",
      companiesDetail: "Selected global companies by market value",
      events: "Unique events",
      impactsDetail: (count: string) => `${count} company links`,
      articles: "Source articles",
      multiSourceDetail: (count: number) => `${count} events supported by multiple sources`,
      relationships: "Connected company pairs",
      relationshipsDetail: "At least two shared events",
      coverageEyebrow: "COVERAGE",
      coverageTitle: "Companies appearing in the most events",
      viewAll: "View all",
      compositionEyebrow: "EVENT MIX",
      compositionTitle: "Company links by event type",
      countByLink: "Number of company links",
      compositionCaption: "One event can involve several companies, so the number of company links can be higher than the number of events.",
      recentEyebrow: "RECENT EVENTS",
      recentTitle: "Events to review first",
      openInsights: "Open event insights",
    },
    company: {
      listLabel: "Company list",
      scope: "COMPANIES",
      companyCount: (count: number) => `${count} ${count === 1 ? "company" : "companies"}`,
      marketCapRank: (rank: number) => `Market-cap rank #${rank}`,
      profileLine: (marketCap: string, rank: number) => `Market cap ${marketCap} · Market-cap rank #${rank}`,
      identifiedEvents: "Events found",
      studyWindow: "In this 12-month period",
      coverageWarning: "Limited news coverage",
      coverageBody: (count: number) => `Only ${count} ${count === 1 ? "event appears" : "events appear"} in the selected Guardian articles. Other important company news may exist outside this source.`,
      compositionLabel: "Company event mix",
      timelineEyebrow: "EVENT TIMELINE",
      timelineTitle: "Recent events and source evidence",
      companyLinks: (count: number) => `${count} matching ${count === 1 ? "event" : "events"}`,
      inspect: "View evidence and price context",
      activityEyebrow: "ACTIVITY OVER TIME",
      activityTitle: "Linked events by month",
      activityCaption: "Monthly counts show when this company appeared in the selected Guardian articles.",
    },
    graph: {
      eyebrow: "INTERACTIVE GRAPH VIEW",
      title: "See how companies connect through shared events.",
      body: "A larger bubble means the company appears in more events. A line connects companies that share events; the same colour marks companies with similar event patterns.",
      networkTitle: "Company shared-event network",
      networkEyebrow: "COMPANY NETWORK",
      focus: "Focus company",
      connected: (count: number) => `${count} connected ${count === 1 ? "company" : "companies"}`,
      sharedLinks: (count: number) => `${count} visible ${count === 1 ? "link" : "links"}`,
      qualifiedLinks: (count: number) => `${count} solid ${count === 1 ? "line" : "lines"}`,
      belowThresholdLinks: (count: number) => `${count} dashed ${count === 1 ? "line" : "lines"}`,
      focusMode: "Focused network",
      showAllLinks: "Show all links",
      openCompany: "Open company research",
      legendNode: "More events, larger bubble",
      isolateLane: (count: number) => `${count} ${count === 1 ? "company has" : "companies have"} no visible links`,
      legendEdge: "Thicker line: more shared events",
      legendQualified: "Solid: meets the selected minimum",
      legendBelow: "Dashed: below the selected minimum",
      legendCommunity: "Same colour: similar event group",
      nodeHint: "Each bubble shows a ticker. The company name and event count appear below it.",
      clickHint: "Select a company bubble to filter the event query below.",
      thresholdNote: "The selected minimum changes how lines look. It does not remove any events.",
      caveat: "A line means the companies share news events. It does not confirm a business relationship.",
      queryEyebrow: "TRACE THE SOURCE",
      queryTitle: "Go from a company event to its source article",
      queryBody: "Choose an event and a source article. You will see the other companies and events mentioned through that article.",
      eventSearch: "Search this company’s events",
      eventSearchPlaceholder: "Search event titles, evidence or article titles",
      selectEvent: "Company event",
      selectArticle: "Source article",
      noEvents: "No company events match this search.",
      noArticles: "No source article is available for this event.",
      articleReach: "What this article connects",
      companyReach: (count: number) => `${count} linked ${count === 1 ? "company" : "companies"}`,
      eventReach: (count: number) => `${count} unique ${count === 1 ? "event" : "events"}`,
      linkReach: (count: number) => `${count} company–event ${count === 1 ? "match" : "matches"}`,
      relatedCompanies: "Companies linked through this article",
      sameSelectedEvent: "Directly linked to the selected event",
      sameArticleOtherEvent: "Same article, linked to another event",
      labelGuideTitle: "Why companies have two different labels",
      labelGuideIntro: "One article can contain several separate events. The label shows which event each company is linked to.",
      selectedEventLabel: "Event you selected",
      sameEventExample: (company: string) => `${company} is directly linked to the event you selected.`,
      otherEventExample: (company: string, event: string) => `${company} appears in the same article, but it is linked to another event: “${event}”.`,
      otherEventMissing: "This article has no company linked only through another event.",
      linkedEvents: (count: number) => `${count} linked ${count === 1 ? "event" : "events"}`,
      openArticle: "Open Guardian article",
      reviewEvent: "Review selected event",
      analyzeEvent: "Analyze this event with AI",
      analyzeEventAria: (title: string) => `Ask AI to analyze the selected event: ${title}`,
      queryCompanyNode: "Starting company",
      queryEventNode: "Selected event",
      queryArticleNode: "Source article",
      queryRelatedNode: "Related companies",
      countDefinition: "Each company is counted once, even if the article links it to more than one event.",
      queryScope: (name: string, count: number) => `Showing ${count} ${count === 1 ? "event" : "events"} linked to ${name}`,
    },
    alerts: {
      eyebrow: "EVENT SEARCH",
      title: "Find events that are closely linked to a company.",
      body: "Start with regulation and policy events, then adjust the filters. The company link score shows how clearly the article connects the company to the event; it is not a risk score.",
      eventType: "Event type",
      minimumMatch: "Minimum company link score",
      allTypes: "All event types",
      allScores: "Any score",
      resultCount: (count: number) => `${count} ${count === 1 ? "result matches" : "results match"} the current filters`,
      instruction: "Open an event to check the evidence, original article and price changes before and after publication.",
      textMatch: "Company link score",
      inspectEvidence: "Review evidence",
      emptyTitle: "No events match these filters",
      emptyBody: "Try a lower company link score or choose another event type.",
      resultLimit: (count: number) => `Showing the latest 120 of ${count} results to keep the page responsive.`,
    },
    relations: {
      eyebrow: "COMPARE COMPANIES",
      title: "Which companies appear in the same events?",
      body: "Shared events can point to common industry, policy, supply-chain or competitive themes. They do not confirm a business relationship.",
      minimumShared: "Minimum shared events",
      option: (count: number, recommended = false) => `${count}${recommended ? " (recommended)" : ""}`,
      explanationLabel: "Metric explanation",
      overlap: "Shared-event rate",
      formula: "Shared events ÷ combined unique events",
      explanation: "A higher percentage means the two companies appear in a more similar set of events. Also check the number of shared events, especially when the sample is small.",
      listEyebrow: "PAIR LIST",
      pairCount: (count: number) => `${count} company ${count === 1 ? "pair" : "pairs"}`,
      sort: "Sorted by shared-event rate",
      tableLabel: "Company shared-event relationships",
      companyPair: "Company pair",
      sharedEvents: "Shared events",
      combinedEvents: "All unique events",
      eventOverlap: "Shared-event rate",
      emptyTitle: "No company pairs match",
      emptyBody: "Try reducing the minimum number of shared events to one.",
      heatmapEyebrow: "RELATIONSHIP HEATMAP",
      heatmapTitle: "Where shared-event activity is concentrated",
      heatmapCaption: "Darker cells mean more shared events. The diagonal shows each company’s total events. Hover over a cell for the exact number.",
      heatmapEmpty: "No shared events",
    },
    notes: {
      eyebrow: "READ BEFORE USE",
      title: "Use these results as research leads, not investment advice.",
      body: "Check each result against the original article. Price data only shows what happened around publication.",
      generated: "Data snapshot generated",
      ranking: "Company ranking snapshot",
      quality: "Quality checks",
      passed: (passed: number, total: number) => `${passed} of ${total} passed`,
      validationEyebrow: "DATA CHECKS",
      validationTitle: "Research task checks",
      taskTitles: {
        T1: "Company overview",
        T2: "TSMC evidence check",
        T3: "Regulatory event search",
        T4: "Alphabet event and price context",
        T5: "Companies with shared events",
      },
      allPassed: "All tasks passed",
      issuesFound: "Issues found",
      researchTask: "Research task",
      resultRows: "Result rows",
      medianTime: "Median time",
      p95: "Slower runs (P95)",
      stable: "Same result on repeat",
      pass: "Pass",
      issue: "Issue",
      performanceCaption: "Times were measured on this computer after the data had loaded. They are a guide, not a production speed estimate.",
    },
    drawer: {
      companyEvent: "Company event",
      close: "Close event details",
      extractedEyebrow: "EVENT",
      textMatch: "Company link score",
      focusScore: "Company relevance",
      modelNote: "The company link score shows how clearly the article connects this company to the event. It is not a measure of truth, risk or likely price movement.",
      evidenceEyebrow: "SOURCE EVIDENCE",
      marketEyebrow: "PRICE BEFORE AND AFTER PUBLICATION",
      tradingDays: "1, 3 and 7 trading days",
      beforePublication: "Before publication",
      afterPublication: "After publication",
      descriptive: "Context only",
      day: (count: number) => `${Math.abs(count)}D ${count < 0 ? "before" : "after"}`,
      noMarket: "No market-window data is available for this event.",
      causalWarning: "These returns show price changes before and after publication. They do not prove that the event caused a price move.",
      sourcesEyebrow: "SOURCE TRAIL",
      articleCount: (count: number) => `${count} linked ${count === 1 ? "article" : "articles"}`,
      verify: "Open original article",
      relationshipSource: "Article supporting this company link",
      pathEyebrow: "EVIDENCE PATH",
      pathTitle: "How to check this result",
      sourceNode: "Guardian article",
      evidenceNode: "Evidence sentence",
      eventNode: "Unique event",
      companyNode: "Linked company",
      marketChart: "Price change before and after publication",
    },
    otherEvent: "Another event",
    semanticLink: "Text-based link",
  },
  zh: {
    pageTitle: "金融事件情报工作台",
    brandName: "Financial Intelligence",
    brandSubtitle: "公司新闻研究",
    workspace: "分析师工作台",
    primaryNavigation: "主导航",
    nav: {
      overview: { label: "总览", eyebrow: "关键数据" },
      company: { label: "公司研究", eyebrow: "查看公司事件" },
      graph: { label: "知识图谱", eyebrow: "查看公司关联" },
      alerts: { label: "事件查询", eyebrow: "筛选事件" },
      relations: { label: "公司比较", eyebrow: "比较公司" },
      notes: { label: "数据说明", eyebrow: "理解数据口径" },
    },
    snapshot: "当前数据",
    searchLabel: "搜索公司或股票代码",
    searchPlaceholder: "搜索公司或股票代码",
    searchResults: "公司搜索结果",
    eventCount: (count: number) => `${count} 个事件`,
    noMatchingCompanies: "没有找到匹配的公司",
    snapshotVerified: "数据已检查",
    generatedAt: (date: string) => `更新时间：${date}`,
    loadingTitle: "正在载入已检查的研究数据",
    loadingBody: "首次打开可能需要几秒钟。",
    errorTitle: "暂时无法读取数据",
    errorBody: "未能载入研究数据。请先更新数据文件，然后刷新页面。",
    reload: "重新载入",
    overview: {
      eyebrow: "过去 12 个月",
      title: "把公司新闻整理成清晰、可核查的研究线索。",
      body: "查看事件、来源报道、相关公司和报道发布前后的价格变化。先发现线索，再回到原文核实。",
      corpus: "新闻来源",
      dateRange: (start: string, end: string) => `${start}至${end}`,
      metricsLabel: "关键数据",
      companies: "覆盖公司",
      companiesDetail: "按市值选取的全球公司",
      events: "独立事件",
      impactsDetail: (count: string) => `${count} 条公司关联`,
      articles: "来源文章",
      multiSourceDetail: (count: number) => `${count} 个事件有多篇报道支持`,
      relationships: "有关联的公司组合",
      relationshipsDetail: "至少共同涉及 2 个事件",
      coverageEyebrow: "公司覆盖",
      coverageTitle: "出现事件较多的公司",
      viewAll: "查看全部",
      compositionEyebrow: "事件分布",
      compositionTitle: "不同事件类型的公司关联",
      countByLink: "公司关联数量",
      compositionCaption: "同一事件可能涉及多家公司，因此公司关联数量可能高于事件数量。",
      recentEyebrow: "近期事件",
      recentTitle: "建议优先查看的事件",
      openInsights: "进入事件洞察",
    },
    company: {
      listLabel: "公司列表",
      scope: "公司列表",
      companyCount: (count: number) => `${count} 家公司`,
      marketCapRank: (rank: number) => `市值排名第 ${rank} 位`,
      profileLine: (marketCap: string, rank: number) => `市值 ${marketCap} · 市值排名第 ${rank} 位`,
      identifiedEvents: "找到的事件",
      studyWindow: "过去 12 个月",
      coverageWarning: "新闻覆盖较少",
      coverageBody: (count: number) => `所选 Guardian 报道中只有 ${count} 个事件。其他新闻来源中可能还有该公司的重要信息。`,
      compositionLabel: "公司事件分布",
      timelineEyebrow: "事件时间线",
      timelineTitle: "近期事件与原文证据",
      companyLinks: (count: number) => `${count} 个相关事件`,
      inspect: "查看证据和价格变化",
      activityEyebrow: "事件活跃度",
      activityTitle: "每月关联事件数",
      activityCaption: "按月显示该公司在所选 Guardian 报道中出现的事件数量。",
    },
    graph: {
      eyebrow: "交互式图谱",
      title: "查看公司如何通过共同事件形成关联。",
      body: "气泡越大，代表公司涉及的事件越多。连线表示两家公司有共同事件；相同颜色表示它们的事件分布较相似。",
      networkTitle: "公司共同事件网络",
      networkEyebrow: "公司关联网络",
      focus: "聚焦公司",
      connected: (count: number) => `关联 ${count} 家公司`,
      sharedLinks: (count: number) => `当前显示 ${count} 条连线`,
      qualifiedLinks: (count: number) => `${count} 条实线`,
      belowThresholdLinks: (count: number) => `${count} 条虚线`,
      focusMode: "已聚焦所选公司",
      showAllLinks: "显示全部连线",
      openCompany: "进入公司研究",
      legendNode: "事件越多，气泡越大",
      isolateLane: (count: number) => `${count} 家公司当前没有可见连线`,
      legendEdge: "连线越粗，共同事件越多",
      legendQualified: "实线：达到所选数量",
      legendBelow: "虚线：少于所选数量",
      legendCommunity: "相同颜色：事件分布相似",
      nodeHint: "气泡内显示股票代码，下方显示公司名和事件数。",
      clickHint: "点击公司气泡，下方事件查询将只保留该公司的事件。",
      thresholdNote: "所选数量只会改变连线样式，不会删除任何事件。",
      caveat: "连线只表示两家公司有共同新闻事件，不代表它们存在商业关系。",
      queryEyebrow: "查找新闻来源",
      queryTitle: "从公司事件找到来源报道",
      queryBody: "选择一个事件和一篇来源报道，即可查看这篇报道还涉及哪些公司和事件。",
      eventSearch: "搜索该公司的事件",
      eventSearchPlaceholder: "搜索事件标题、证据句或报道标题",
      selectEvent: "公司事件",
      selectArticle: "来源报道",
      noEvents: "没有找到符合搜索条件的公司事件。",
      noArticles: "该事件暂无可用的来源报道。",
      articleReach: "这篇报道关联了什么",
      companyReach: (count: number) => `关联 ${count} 家公司`,
      eventReach: (count: number) => `涉及 ${count} 个独立事件`,
      linkReach: (count: number) => `形成 ${count} 条公司与事件的关联`,
      relatedCompanies: "这篇报道关联的公司",
      sameSelectedEvent: "与所选事件直接相关",
      sameArticleOtherEvent: "在同篇报道中，但关联另一个事件",
      labelGuideTitle: "为什么公司会有两种标记",
      labelGuideIntro: "一篇报道可能包含多个独立事件。标记用于说明每家公司具体关联的是哪一个事件。",
      selectedEventLabel: "你选择的事件",
      sameEventExample: (company: string) => `${company} 与你选择的事件直接相关。`,
      otherEventExample: (company: string, event: string) => `${company} 也出现在这篇报道中，但它关联的是另一个事件：“${event}”。`,
      otherEventMissing: "这篇报道中没有只关联其他事件的公司。",
      linkedEvents: (count: number) => `关联 ${count} 个事件`,
      openArticle: "打开 Guardian 原始报道",
      reviewEvent: "查看所选事件详情",
      analyzeEvent: "让 AI 分析这个事件",
      analyzeEventAria: (title: string) => `让 AI 分析所选事件：${title}`,
      queryCompanyNode: "起始公司",
      queryEventNode: "所选事件",
      queryArticleNode: "来源报道",
      queryRelatedNode: "关联公司",
      countDefinition: "每家公司只统计一次，即使这篇报道把它关联到多个事件。",
      queryScope: (name: string, count: number) => `当前显示 ${name} 关联的 ${count} 个事件`,
    },
    alerts: {
      eyebrow: "事件查询",
      title: "查找与公司关系较明确的事件。",
      body: "页面默认显示监管与政策事件，可通过筛选查看其他类型。公司关联分越高，表示原文越明确地把公司和事件联系在一起；它不是风险分数。",
      eventType: "事件类型",
      minimumMatch: "最低公司关联分",
      allTypes: "全部类型",
      allScores: "不限",
      resultCount: (count: number) => `当前筛选下有 ${count} 条结果`,
      instruction: "打开事件，即可查看证据句、原始报道和发布前后的价格变化。",
      textMatch: "公司关联分",
      inspectEvidence: "查看证据",
      emptyTitle: "没有符合条件的事件",
      emptyBody: "可以降低公司关联分，或选择其他事件类型。",
      resultLimit: (count: number) => `为保证浏览流畅，目前显示最近 120 条，共 ${count} 条。`,
    },
    relations: {
      eyebrow: "比较公司",
      title: "哪些公司出现在相同事件中？",
      body: "共同事件可能指向相同的行业、监管、供应链或竞争主题，但不能证明公司之间存在商业关系。",
      minimumShared: "最少共同事件数",
      option: (count: number, recommended = false) => `${count} 个${recommended ? "（推荐）" : ""}`,
      explanationLabel: "指标说明",
      overlap: "共同事件占比",
      formula: "共同事件数 ÷ 两家公司涉及事件的并集",
      explanation: "占比越高，两家公司涉及的事件越相似。也请查看共同事件数量，尤其是在样本较少时。",
      listEyebrow: "公司组合",
      pairCount: (count: number) => `${count} 组公司`,
      sort: "按共同事件占比排序",
      tableLabel: "公司共同事件关系",
      companyPair: "公司组合",
      sharedEvents: "共同事件",
      combinedEvents: "两家公司全部事件",
      eventOverlap: "共同事件占比",
      emptyTitle: "没有达到当前标准的公司组合",
      emptyBody: "可以把最少共同事件数调低到 1 个。",
      heatmapEyebrow: "关系热力图",
      heatmapTitle: "共同事件主要集中在哪里",
      heatmapCaption: "颜色越深，共同事件越多。对角线显示每家公司的事件总数；把鼠标移到方格上可查看具体数量。",
      heatmapEmpty: "没有共同事件",
    },
    notes: {
      eyebrow: "使用前请阅读",
      title: "请把结果当作研究线索，而不是投资建议。",
      body: "请结合证据句和原始报道核实每条结果。价格数据只展示报道发布前后的变化。",
      generated: "数据快照生成时间",
      ranking: "公司排名快照日期",
      quality: "质量检查",
      passed: (passed: number, total: number) => `${passed}/${total} 项通过`,
      validationEyebrow: "数据检查",
      validationTitle: "研究任务检查结果",
      taskTitles: {
        T1: "公司概览",
        T2: "台积电事件证据检查",
        T3: "监管事件查询",
        T4: "Alphabet 事件与价格背景",
        T5: "共同事件公司查询",
      },
      allPassed: "全部任务通过",
      issuesFound: "发现异常",
      researchTask: "分析任务",
      resultRows: "结果数",
      medianTime: "中位耗时",
      p95: "较慢情况（P95）",
      stable: "重复运行结果一致",
      pass: "通过",
      issue: "异常",
      performanceCaption: "耗时在本机数据已载入后测得，仅供参考，不代表正式环境的速度。",
    },
    drawer: {
      companyEvent: "公司事件",
      close: "关闭事件详情",
      extractedEyebrow: "事件",
      textMatch: "公司关联分",
      focusScore: "公司相关度",
      modelNote: "公司关联分越高，表示原文越明确地把这家公司和事件联系在一起。它不代表事件真假、风险大小或价格影响。",
      evidenceEyebrow: "原文证据",
      marketEyebrow: "报道发布前后的价格变化",
      tradingDays: "前后 1、3、7 个交易日",
      beforePublication: "报道发布前",
      afterPublication: "报道发布后",
      descriptive: "仅作背景",
      day: (count: number) => `${count < 0 ? "前" : "后"} ${Math.abs(count)} 日`,
      noMarket: "该事件暂无可用的市场区间数据。",
      causalWarning: "这些收益显示报道发布前后的价格变化，不能证明事件导致了价格变动。",
      sourcesEyebrow: "原文来源",
      articleCount: (count: number) => `${count} 篇关联报道`,
      verify: "打开原始报道",
      relationshipSource: "支持这条公司关联的报道",
      pathEyebrow: "证据路径",
      pathTitle: "如何核查这条结果",
      sourceNode: "Guardian 报道",
      evidenceNode: "原文证据句",
      eventNode: "独立事件",
      companyNode: "关联公司",
      marketChart: "报道发布前后的价格变化",
    },
    otherEvent: "其他事件",
    semanticLink: "文本关联",
  },
} as const;

const EVENT_LABELS: Record<Language, Record<string, string>> = {
  en: {
    corporate_event: "Corporate",
    regulatory_event: "Regulation & policy",
    geopolitical_event: "Geopolitics",
    macroeconomic_event: "Macroeconomy",
    market_wide_event: "Market-wide",
    technology_event: "Technology",
    commodity_event: "Commodities",
  },
  zh: {
    corporate_event: "公司动态",
    regulatory_event: "监管与政策",
    geopolitical_event: "地缘政治",
    macroeconomic_event: "宏观经济",
    market_wide_event: "市场动态",
    technology_event: "技术动态",
    commodity_event: "大宗商品",
  },
};

const RELATION_LABELS: Record<Language, Record<string, string>> = {
  en: {
    direct_subject: "Company led the event",
    direct_target: "Event directly affects the company",
    material_context: "Company is important context",
    indirect_exposure: "Indirect link",
  },
  zh: {
    direct_subject: "公司是事件主体",
    direct_target: "事件直接影响公司",
    material_context: "公司是重要背景",
    indirect_exposure: "间接关联",
  },
};

const LANGUAGE_OPTIONS: Array<{ id: Language; label: string; ariaLabel: string }> = [
  { id: "en", label: "EN", ariaLabel: "Switch to English" },
  { id: "zh", label: "中文", ariaLabel: "切换到中文" },
];

const ASSISTANT_COPY = {
  en: {
    launcher: "Explain this data",
    title: "Research assistant",
    eyebrow: "UNDERSTAND THE RESULT",
    close: "Close research assistant",
    intro: "Ask for an evidence-based explanation or comparison. Answers use only this checked data snapshot.",
    preview: "Data preview",
    ai: "AI explanation",
    status: {
      ready: { label: "AI ready", body: "This answer was generated from the checked data shown below." },
      not_configured: { label: "AI not configured", body: "Showing a checked-data preview. Add a server-side API key to enable AI explanations." },
      unavailable: { label: "AI temporarily unavailable", body: "The AI connection failed, so this is a checked-data preview instead." },
    },
    diagnostics: {
      openai_http_400: { label: "Request format rejected", body: "The API rejected the assistant request. The request format needs checking." },
      openai_http_401: { label: "API key not accepted", body: "Check the server-side API key, then restart the website." },
      openai_http_403: { label: "API access denied", body: "This API project or key cannot use the selected model." },
      openai_http_404: { label: "Model not available", body: "The selected model was not found or is not available to this API project." },
      openai_http_429: { label: "API limit reached", body: "The API has no available credit or is receiving requests too quickly." },
      openai_http_5xx: { label: "OpenAI service error", body: "OpenAI returned a temporary server error. Try again shortly." },
      openai_network_error: { label: "Cannot reach OpenAI", body: "The server could not establish a connection to OpenAI. Check the local proxy and try again." },
      openai_timeout: { label: "OpenAI took too long", body: "The request exceeded the waiting time. Try again; the checked-data preview below is still available." },
      openai_tool_missing: { label: "Data tool was not selected", body: "The model did not read the checked data as required. Try again." },
      openai_tool_args_invalid: { label: "Data tool input was invalid", body: "The model returned tool input that the assistant could not read. Try again." },
      openai_empty_output: { label: "No AI answer returned", body: "The model call completed but returned no readable answer. Try again." },
      openai_incomplete_output: { label: "AI answer was cut off", body: "The model reached its response limit twice. Try again; the checked-data preview remains available below." },
      openai_unavailable: { label: "AI request failed", body: "The exact cause is not available. Check the server terminal and try again." },
    },
    inputLabel: "Ask about this data",
    inputPlaceholder: "For example: What stands out about this company?",
    ask: "Ask",
    asking: "Checking the data...",
    suggestionsLabel: "Try one",
    suggestions: (company: string) => [
      `Give me a short overview of ${company}.`,
      `Show me a recent event for ${company} and the source evidence.`,
      `Explain the price context before and after a ${company} event.`,
      "Which two companies share the most events in this snapshot?",
    ],
    eventPrompt: (company: string, title: string, eventId: string) =>
      `Analyze this selected event for ${company} (${eventId}): “${title}”. Explain what the checked evidence says, why it is linked to the company, and how to read the available before-and-after price context.`,
    answerTitle: "Analysis",
    evidenceTitle: "Open the supporting data",
    companyAction: "Open company",
    eventAction: "View event",
    sourceAction: "Open source article",
    marketContext: "Price windows",
    eventFallback: "Event evidence",
    emptyEvidence: "No linked evidence cards were returned for this answer.",
    error: "I could not check the data just now. Please try again.",
  },
  zh: {
    launcher: "帮我读懂数据",
    title: "研究助手",
    eyebrow: "读懂当前结果",
    close: "关闭研究助手",
    intro: "可以要求解释、比较或归纳当前结果。回答只使用这份已检查的数据快照。",
    preview: "数据预览",
    ai: "AI 解读",
    status: {
      ready: { label: "AI 已就绪", body: "这段说明由 AI 根据下方已检查数据生成。" },
      not_configured: { label: "尚未配置 AI", body: "当前显示已检查数据的预览。配置服务端 API Key 后即可启用 AI 解读。" },
      unavailable: { label: "AI 暂时不可用", body: "AI 连接失败，因此当前改为显示已检查数据的预览。" },
    },
    diagnostics: {
      openai_http_400: { label: "请求格式被拒绝", body: "API 拒绝了助手发送的请求，需要检查请求格式。" },
      openai_http_401: { label: "API Key 未通过验证", body: "请检查服务端 API Key，然后重启网站。" },
      openai_http_403: { label: "没有 API 使用权限", body: "当前 API 项目或 Key 无权使用所选模型。" },
      openai_http_404: { label: "模型不可用", body: "找不到所选模型，或当前 API 项目无权访问它。" },
      openai_http_429: { label: "API 额度或频率受限", body: "API 可能没有可用额度，或请求过于频繁。" },
      openai_http_5xx: { label: "OpenAI 服务异常", body: "OpenAI 服务端暂时出错，请稍后重试。" },
      openai_network_error: { label: "无法连接 OpenAI", body: "服务器未能与 OpenAI 建立连接。请检查本地代理后重试。" },
      openai_timeout: { label: "OpenAI 响应超时", body: "请求超过了等待时间。可以稍后重试；下方仍会显示已检查数据的预览。" },
      openai_tool_missing: { label: "模型没有读取数据", body: "模型没有按要求选择数据工具，请重试。" },
      openai_tool_args_invalid: { label: "数据工具参数无效", body: "模型返回的工具参数无法读取，请重试。" },
      openai_empty_output: { label: "模型没有返回回答", body: "模型调用已经完成，但没有可显示的回答，请重试。" },
      openai_incomplete_output: { label: "AI 回答被截断", body: "模型连续两次达到回答上限。请重试；下方仍会显示已检查数据的预览。" },
      openai_unavailable: { label: "AI 请求失败", body: "暂时无法确定具体原因。请查看服务器终端后重试。" },
    },
    inputLabel: "询问当前数据",
    inputPlaceholder: "例如：这家公司的数据有什么值得关注？",
    ask: "提问",
    asking: "正在查看数据…",
    suggestionsLabel: "可以这样问",
    suggestions: (company: string) => [
      `请简要介绍 ${company} 的数据。`,
      `找一条 ${company} 的近期事件，并说明原文证据。`,
      `解释 ${company} 某个事件前后的价格变化。`,
      "当前数据中哪两家公司共同事件最多？",
    ],
    eventPrompt: (company: string, title: string, eventId: string) =>
      `请分析 ${company} 的这个所选事件（${eventId}）：“${title}”。请说明已检查证据讲了什么、为什么它与该公司相关，以及应该怎样理解事件前后的价格背景。`,
    answerTitle: "分析结果",
    evidenceTitle: "查看回答依据",
    companyAction: "打开公司",
    eventAction: "查看事件",
    sourceAction: "打开原始报道",
    marketContext: "价格窗口",
    eventFallback: "事件证据",
    emptyEvidence: "这次回答没有返回可打开的证据卡片。",
    error: "暂时无法读取数据，请稍后重试。",
  },
} as const;

const COUNTRY_LABELS_ZH: Record<string, string> = {
  "United States": "美国",
  Taiwan: "台湾",
  Netherlands: "荷兰",
  China: "中国",
};

const SECTION_LABELS_ZH: Record<string, string> = {
  "Australia news": "澳大利亚新闻",
  Business: "商业",
  Education: "教育",
  Environment: "环境",
  Food: "饮食",
  "GNM press office": "卫报新闻办公室",
  Games: "游戏",
  Global: "全球",
  "Global development": "全球发展",
  Law: "法律",
  Media: "媒体",
  Money: "个人理财",
  News: "新闻",
  Opinion: "评论",
  Politics: "政治",
  "Priceless Experiences With Mastercard": "万事达卡专题",
  Science: "科学",
  Society: "社会",
  Technology: "科技",
  "UK news": "英国新闻",
  "US news": "美国新闻",
  "World news": "国际新闻",
};

const navItemsFor = (language: Language): Array<{ id: View; label: string; eyebrow: string }> => {
  const nav = COPY[language].nav;
  return (["overview", "company", "graph", "alerts", "relations", "notes"] as View[]).map((id) => ({ id, ...nav[id] }));
};

const formatDate = (value: string, language: Language) =>
  new Intl.DateTimeFormat(language === "en" ? "en-GB" : "zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(`${value.slice(0, 10)}T00:00:00`));

const formatCountry = (value: string, language: Language) =>
  language === "zh" ? (COUNTRY_LABELS_ZH[value] ?? value) : value;

const formatSection = (value: string, language: Language) =>
  language === "zh" ? (SECTION_LABELS_ZH[value] ?? value) : value;

const formatCompact = (value: number, language: Language) =>
  new Intl.NumberFormat(language === "en" ? "en-GB" : "zh-CN", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);

const formatMarketCap = (value: number, language: Language) =>
  language === "en"
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 }).format(value)
    : `$${(value / 1_000_000_000_000).toFixed(2)}万亿`;

const formatReturn = (value: number) => {
  const pct = value * 100;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
};

const keyFor = (companyId: string, eventId: string) => `${companyId}::${eventId}`;
const articleKeyFor = (source: Source) => source.articleId || source.url;

function LoadingScreen({ language }: { language: Language }) {
  const copy = COPY[language];
  return (
    <main className={styles.stateScreen} aria-live="polite">
      <div className={styles.loader} aria-hidden="true" />
      <p>{copy.loadingTitle}</p>
      <span>{copy.loadingBody}</span>
    </main>
  );
}

export function Dashboard() {
  const [language, setLanguage] = useState<Language>("en");
  const [data, setData] = useState<DashboardData | null>(null);
  const [hasError, setHasError] = useState(false);
  const [view, setView] = useState<View>("overview");
  const [query, setQuery] = useState("");
  const [selectedCompanyId, setSelectedCompanyId] = useState("");
  const [eventFilter, setEventFilter] = useState("regulatory_event");
  const [minimumProbability, setMinimumProbability] = useState("0.80");
  const [relationSupport, setRelationSupport] = useState(2);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [assistantRequest, setAssistantRequest] = useState<AssistantAnalysisRequest | null>(null);
  const assistantRequestSequence = useRef(0);
  const copy = COPY[language];
  const navItems = navItemsFor(language);

  useEffect(() => {
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
    document.title = COPY[language].pageTitle;
  }, [language]);

  useEffect(() => {
    let alive = true;
    fetch("/data/dashboard.json")
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<DashboardData>;
      })
      .then((snapshot) => {
        if (!alive) return;
        setData(snapshot);
        setSelectedCompanyId(snapshot.companies[0]?.companyId ?? "");
      })
      .catch(() => alive && setHasError(true));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!detail) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setDetail(null);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [detail]);

  const indexes = useMemo(() => {
    if (!data) return null;
    const events = new Map(data.events.map((event) => [event.eventId, event]));
    const companies = new Map(data.companies.map((company) => [company.companyId, company]));
    const sources = new Map<string, Source[]>();
    const market = new Map<string, MarketObservation[]>();
    for (const source of data.sources) {
      const key = keyFor(source.companyId, source.eventId);
      sources.set(key, [...(sources.get(key) ?? []), source]);
    }
    for (const row of data.market) {
      const key = keyFor(row.companyId, row.eventId);
      market.set(key, [...(market.get(key) ?? []), row]);
    }
    return { events, companies, sources, market };
  }, [data]);

  const filteredCompanies = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toLocaleLowerCase();
    return [...data.companies]
      .filter((company) =>
        !needle
          ? true
          : `${company.name} ${company.symbol} ${company.country}`.toLocaleLowerCase().includes(needle),
      )
      .sort((a, b) => b.eventCount - a.eventCount);
  }, [data, query]);

  const selectedCompany = indexes?.companies.get(selectedCompanyId) ?? null;

  const selectedImpacts = useMemo(() => {
    if (!data || !indexes || !selectedCompanyId) return [];
    return data.impacts
      .filter((impact) => impact.companyId === selectedCompanyId && indexes.events.has(impact.eventId))
      .sort((a, b) =>
        (indexes.events.get(b.eventId)?.date ?? "").localeCompare(indexes.events.get(a.eventId)?.date ?? ""),
      );
  }, [data, indexes, selectedCompanyId]);

  const alertRows = useMemo(() => {
    if (!data || !indexes) return [];
    const threshold = Number(minimumProbability);
    return data.impacts
      .map((impact) => ({ impact, event: indexes.events.get(impact.eventId), company: indexes.companies.get(impact.companyId) }))
      .filter(
        (row): row is { impact: Impact; event: EventRecord; company: Company } =>
          Boolean(row.event && row.company) &&
          (eventFilter === "all" || row.event?.type === eventFilter) &&
          row.impact.nlpPositiveProbability >= threshold,
      )
      .sort((a, b) => b.event.date.localeCompare(a.event.date) || b.impact.nlpPositiveProbability - a.impact.nlpPositiveProbability);
  }, [data, indexes, eventFilter, minimumProbability]);

  const openCompany = (companyId: string) => {
    setSelectedCompanyId(companyId);
    setQuery("");
    setView("company");
  };

  const openAssistantEvent = (companyId: string, eventId: string) => {
    const event = indexes?.events.get(eventId);
    const impact = data?.impacts.find(
      (row) => row.companyId === companyId && row.eventId === eventId,
    );
    if (event && impact) setDetail({ event, impact });
  };

  const analyzeEventWithAssistant = (request: Omit<AssistantAnalysisRequest, "requestId">) => {
    assistantRequestSequence.current += 1;
    setSelectedCompanyId(request.companyId);
    setAssistantRequest({ ...request, requestId: assistantRequestSequence.current });
  };

  if (hasError) {
    return (
      <main className={styles.stateScreen} role="alert">
        <strong>{copy.errorTitle}</strong>
        <p>{copy.errorBody}</p>
        <button type="button" onClick={() => window.location.reload()} className={styles.primaryButton}>
          {copy.reload}
        </button>
      </main>
    );
  }

  if (!data || !indexes) return <LoadingScreen language={language} />;

  const generatedAt = new Intl.DateTimeFormat(language === "en" ? "en-GB" : "zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(data.scope.sourceGeneratedAtUtc));

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">FI</span>
          <div>
            <strong>{copy.brandName}</strong>
            <span>{copy.brandSubtitle}</span>
          </div>
        </div>

        <nav className={styles.navigation} aria-label={copy.primaryNavigation}>
          {navItems.map((item) => (
            <button
              type="button"
              key={item.id}
              className={view === item.id ? styles.navActive : styles.navButton}
              onClick={() => setView(item.id)}
              aria-current={view === item.id ? "page" : undefined}
            >
              <span className={styles.navDot} aria-hidden="true" />
              <span>
                <strong>{item.label}</strong>
                <small>{item.eyebrow}</small>
              </span>
            </button>
          ))}
        </nav>

        <div className={styles.sidebarNote}>
          <span className={styles.liveDot} aria-hidden="true" />
          <div>
            <strong>{copy.snapshot}</strong>
            <p>{data.scope.eventDateRange.start} — {data.scope.eventDateRange.end}</p>
          </div>
        </div>
      </aside>

      <main className={styles.main}>
        <header className={styles.topbar}>
          <div>
            <p className={styles.eyebrow}>{copy.workspace}</p>
            <h1>{navItems.find((item) => item.id === view)?.label}</h1>
          </div>
          <div className={styles.topActions}>
            <div className={styles.languageToggle} role="group" aria-label={language === "en" ? "Language" : "语言"}>
              {LANGUAGE_OPTIONS.map((option) => (
                <button
                  type="button"
                  key={option.id}
                  className={`${styles.languageOption} ${language === option.id ? styles.languageOptionActive : ""}`}
                  onClick={() => setLanguage(option.id)}
                  aria-label={option.ariaLabel}
                  aria-pressed={language === option.id}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <div className={styles.searchBox}>
              <span aria-hidden="true">⌕</span>
              <label className={styles.srOnly} htmlFor="global-company-search">{copy.searchLabel}</label>
              <input
                id="global-company-search"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={copy.searchPlaceholder}
                autoComplete="off"
              />
              {query && (
                <div className={styles.searchResults} aria-label={copy.searchResults}>
                  {filteredCompanies.slice(0, 6).map((company) => (
                    <button type="button" key={company.companyId} onClick={() => openCompany(company.companyId)}>
                      <span>{company.name}</span>
                      <small>{company.symbol} · {copy.eventCount(company.eventCount)}</small>
                    </button>
                  ))}
                  {filteredCompanies.length === 0 && <p>{copy.noMatchingCompanies}</p>}
                </div>
              )}
            </div>
            <div className={styles.snapshotBadge} title={copy.generatedAt(generatedAt)}>
              <span aria-hidden="true" />
              {copy.snapshotVerified}
            </div>
          </div>
        </header>

        {view === "overview" && (
          <Overview
            data={data}
            indexes={indexes}
            onCompany={openCompany}
            onEvent={(value) => setDetail(value)}
            onNavigate={setView}
            language={language}
          />
        )}

        {view === "company" && selectedCompany && (
          <CompanyResearch
            companies={filteredCompanies}
            selectedCompany={selectedCompany}
            impacts={selectedImpacts}
            events={indexes.events}
            timeSeries={data.visualizations.timeSeries}
            onSelect={setSelectedCompanyId}
            onDetail={setDetail}
            language={language}
          />
        )}

        {view === "graph" && selectedCompany && (
          <GraphExplorer
            data={data}
            selectedCompany={selectedCompany}
            support={relationSupport}
            onSupport={setRelationSupport}
            onFocus={setSelectedCompanyId}
            onCompany={openCompany}
            onDetail={setDetail}
            onAnalyzeEvent={analyzeEventWithAssistant}
            language={language}
          />
        )}

        {view === "alerts" && (
          <EventAlerts
            rows={alertRows}
            eventFilter={eventFilter}
            minimumProbability={minimumProbability}
            onEventFilter={setEventFilter}
            onProbability={setMinimumProbability}
            onDetail={setDetail}
            language={language}
          />
        )}

        {view === "relations" && (
          <Relations
            similarities={data.network.similarities}
            companies={data.companies}
            matrix={data.visualizations.sharedEventMatrix}
            support={relationSupport}
            onSupport={setRelationSupport}
            onCompany={openCompany}
            language={language}
          />
        )}

        {view === "notes" && <DataNotes data={data} generatedAt={generatedAt} language={language} />}
      </main>

      {detail && (
        <EventDrawer
          detail={detail}
          company={indexes.companies.get(detail.impact.companyId)}
          sources={indexes.sources.get(keyFor(detail.impact.companyId, detail.event.eventId)) ?? []}
          market={indexes.market.get(keyFor(detail.impact.companyId, detail.event.eventId)) ?? []}
          onClose={() => setDetail(null)}
          language={language}
        />
      )}

      {selectedCompany && (
        <ResearchAssistant
          key={`${selectedCompany.companyId}-${language}`}
          language={language}
          selectedCompany={selectedCompany}
          analysisRequest={assistantRequest}
          onCompany={openCompany}
          onEvent={openAssistantEvent}
        />
      )}
    </div>
  );
}

function Overview({
  data,
  indexes,
  onCompany,
  onEvent,
  onNavigate,
  language,
}: {
  data: DashboardData;
  indexes: DashboardIndexes;
  onCompany: (id: string) => void;
  onEvent: (detail: Detail) => void;
  onNavigate: (view: View) => void;
  language: Language;
}) {
  const copy = COPY[language];
  const impactTypeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const impact of data.impacts) {
      const type = indexes.events.get(impact.eventId)?.type ?? "other";
      counts.set(type, (counts.get(type) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [data.impacts, indexes.events]);

  const recentHighConfidence = useMemo(
    () =>
      data.impacts
        .filter((impact) => impact.nlpPositiveProbability >= 0.8)
        .map((impact) => ({ impact, event: indexes.events.get(impact.eventId), company: indexes.companies.get(impact.companyId) }))
        .filter((row): row is { impact: Impact; event: EventRecord; company: Company } => Boolean(row.event && row.company))
        .sort((a, b) => b.event.date.localeCompare(a.event.date))
        .slice(0, 5),
    [data.impacts, indexes],
  );

  const topCompanies = [...data.companies].sort((a, b) => b.eventCount - a.eventCount).slice(0, 7);
  const maximum = topCompanies[0]?.eventCount || 1;
  const maxType = impactTypeCounts[0]?.[1] || 1;

  return (
    <div className={styles.view}>
      <section className={styles.heroPanel}>
        <div>
          <p className={styles.sectionEyebrow}>{copy.overview.eyebrow}</p>
          <h2>{copy.overview.title}</h2>
          <p>{copy.overview.body}</p>
        </div>
        <div className={styles.heroMeta}>
          <span>{copy.overview.corpus}</span>
          <strong>{data.scope.publisher}</strong>
          <small>{copy.overview.dateRange(formatDate(data.scope.eventDateRange.start, language), formatDate(data.scope.eventDateRange.end, language))}</small>
        </div>
      </section>

      <section className={styles.metricGrid} aria-label={copy.overview.metricsLabel}>
        <Metric label={copy.overview.companies} value={String(data.summary.companyCount)} detail={copy.overview.companiesDetail} accent="teal" />
        <Metric label={copy.overview.events} value={formatCompact(data.summary.eventCount, language)} detail={copy.overview.impactsDetail(formatCompact(data.summary.impactCount, language))} accent="ink" />
        <Metric label={copy.overview.articles} value={formatCompact(data.summary.sourceArticleCount, language)} detail={copy.overview.multiSourceDetail(data.summary.multiSourceEventCount)} accent="gold" />
        <Metric label={copy.overview.relationships} value={String(data.summary.supportedNetworkEdgeCount)} detail={copy.overview.relationshipsDetail} accent="blue" />
      </section>

      <div className={styles.twoColumn}>
        <section className={styles.panel}>
          <div className={styles.panelHeader}>
            <div>
              <p className={styles.sectionEyebrow}>{copy.overview.coverageEyebrow}</p>
              <h3>{copy.overview.coverageTitle}</h3>
            </div>
            <button type="button" className={styles.textButton} onClick={() => onNavigate("company")}>{copy.overview.viewAll} →</button>
          </div>
          <div className={styles.barList}>
            {topCompanies.map((company, index) => (
              <button type="button" className={styles.barRow} key={company.companyId} onClick={() => onCompany(company.companyId)}>
                <span className={styles.barRank}>{String(index + 1).padStart(2, "0")}</span>
                <span className={styles.barLabel}>
                  <strong>{company.name}</strong>
                  <small>{company.symbol}</small>
                </span>
                <span className={styles.barTrack} aria-hidden="true">
                  <span style={{ width: `${Math.max(5, (company.eventCount / maximum) * 100)}%` }} />
                </span>
                <strong className={styles.barValue}>{company.eventCount}</strong>
              </button>
            ))}
          </div>
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}>
            <div>
              <p className={styles.sectionEyebrow}>{copy.overview.compositionEyebrow}</p>
              <h3>{copy.overview.compositionTitle}</h3>
            </div>
            <span className={styles.softTag}>{copy.overview.countByLink}</span>
          </div>
          <div className={styles.typeList}>
            {impactTypeCounts.map(([type, count]) => (
              <div className={styles.typeRow} key={type}>
                <div>
                  <span>{EVENT_LABELS[language][type] ?? copy.otherEvent}</span>
                  <strong>{count}</strong>
                </div>
                <span className={styles.typeTrack} aria-hidden="true">
                  <span style={{ width: `${(count / maxType) * 100}%` }} />
                </span>
              </div>
            ))}
          </div>
          <p className={styles.caption}>{copy.overview.compositionCaption}</p>
        </section>
      </div>

      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <div>
            <p className={styles.sectionEyebrow}>{copy.overview.recentEyebrow}</p>
            <h3>{copy.overview.recentTitle}</h3>
          </div>
          <button type="button" className={styles.textButton} onClick={() => onNavigate("alerts")}>{copy.overview.openInsights} →</button>
        </div>
        <div className={styles.eventTable}>
          {recentHighConfidence.map(({ impact, event, company }) => (
            <button type="button" key={`${company.companyId}-${event.eventId}`} className={styles.eventTableRow} onClick={() => onEvent({ impact, event })}>
              <time>{formatDate(event.date, language)}</time>
              <span className={`${styles.eventPill} ${event.type === "regulatory_event" ? styles.alertPill : ""}`}>
                {EVENT_LABELS[language][event.type] ?? copy.otherEvent}
              </span>
              <span className={styles.companyCell}>{company.name}</span>
              <strong>{event.title}</strong>
              <span className={styles.probability}>{Math.round(impact.nlpPositiveProbability * 100)}%</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function Metric({ label, value, detail, accent }: { label: string; value: string; detail: string; accent: string }) {
  return (
    <article className={`${styles.metric} ${styles[`metric_${accent}`]}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function CompanyResearch({
  companies,
  selectedCompany,
  impacts,
  events,
  timeSeries,
  onSelect,
  onDetail,
  language,
}: {
  companies: Company[];
  selectedCompany: Company;
  impacts: Impact[];
  events: Map<string, EventRecord>;
  timeSeries: VisualizationData["timeSeries"];
  onSelect: (id: string) => void;
  onDetail: (detail: Detail) => void;
  language: Language;
}) {
  const copy = COPY[language];
  const types = useMemo(() => {
    const values = new Map<string, number>();
    for (const impact of impacts) {
      const type = events.get(impact.eventId)?.type ?? "other";
      values.set(type, (values.get(type) ?? 0) + 1);
    }
    return [...values.entries()].sort((a, b) => b[1] - a[1]);
  }, [impacts, events]);

  return (
    <div className={`${styles.view} ${styles.companyLayout}`}>
      <aside className={`${styles.panel} ${styles.companyList}`} aria-label={copy.company.listLabel}>
        <div className={styles.panelHeader}>
          <div>
            <p className={styles.sectionEyebrow}>{copy.company.scope}</p>
            <h3>{copy.company.companyCount(companies.length)}</h3>
          </div>
        </div>
        <div className={styles.companyScroll}>
          {companies.map((company) => (
            <button
              type="button"
              key={company.companyId}
              className={company.companyId === selectedCompany.companyId ? styles.companyActive : styles.companyButton}
              onClick={() => onSelect(company.companyId)}
            >
              <span className={styles.companyMonogram}>{company.symbol.slice(0, 2)}</span>
              <span>
                <strong>{company.name}</strong>
                <small>{company.symbol} · {copy.company.marketCapRank(company.sourceRank)}</small>
              </span>
              <b>{company.eventCount}</b>
            </button>
          ))}
        </div>
      </aside>

      <div className={styles.companyContent}>
        <section className={styles.companyHero}>
          <div className={styles.companyIdentity}>
            <span className={styles.largeMonogram}>{selectedCompany.symbol.slice(0, 2)}</span>
            <div>
              <p>{selectedCompany.symbol} · {formatCountry(selectedCompany.country, language)}</p>
              <h2>{selectedCompany.name}</h2>
              <span>{copy.company.profileLine(formatMarketCap(selectedCompany.marketCapUsd, language), selectedCompany.sourceRank)}</span>
            </div>
          </div>
          <div className={styles.companyScore}>
            <span>{copy.company.identifiedEvents}</span>
            <strong>{selectedCompany.eventCount}</strong>
            <small>{copy.company.studyWindow}</small>
          </div>
        </section>

        {selectedCompany.eventCount < 5 && (
          <aside className={styles.coverageWarning} role="note">
            <span aria-hidden="true">!</span>
            <div>
              <strong>{copy.company.coverageWarning}</strong>
              <p>{copy.company.coverageBody(selectedCompany.eventCount)}</p>
            </div>
          </aside>
        )}

        <section className={styles.companyStats} aria-label={copy.company.compositionLabel}>
          {types.slice(0, 4).map(([type, count]) => (
            <article key={type}>
              <span>{EVENT_LABELS[language][type] ?? copy.otherEvent}</span>
              <strong>{count}</strong>
              <small>{selectedCompany.eventCount ? Math.round((count / selectedCompany.eventCount) * 100) : 0}%</small>
            </article>
          ))}
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}>
            <div>
              <p className={styles.sectionEyebrow}>{copy.company.activityEyebrow}</p>
              <h3>{copy.company.activityTitle}</h3>
            </div>
            <span className={styles.softTag}>{copy.company.companyLinks(impacts.length)}</span>
          </div>
          <MonthlyActivityChart
            months={timeSeries.months}
            values={timeSeries.companies.find((row) => row.companyId === selectedCompany.companyId)?.values ?? []}
            language={language}
          />
          <p className={styles.caption}>{copy.company.activityCaption}</p>
        </section>

        <section className={styles.panel}>
          <div className={styles.panelHeader}>
            <div>
              <p className={styles.sectionEyebrow}>{copy.company.timelineEyebrow}</p>
              <h3>{copy.company.timelineTitle}</h3>
            </div>
            <span className={styles.softTag}>{copy.company.companyLinks(impacts.length)}</span>
          </div>
          <div className={styles.timeline}>
            {impacts.map((impact) => {
              const event = events.get(impact.eventId);
              if (!event) return null;
              return (
                <article className={styles.timelineItem} key={event.eventId}>
                  <div className={styles.timelineDate}>
                    <span aria-hidden="true" />
                    <time>{formatDate(event.date, language)}</time>
                  </div>
                  <button type="button" className={styles.timelineCard} onClick={() => onDetail({ event, impact })}>
                    <span className={`${styles.eventPill} ${event.type === "regulatory_event" ? styles.alertPill : ""}`}>
                      {EVENT_LABELS[language][event.type] ?? copy.otherEvent}
                    </span>
                    <h4>{event.title}</h4>
                    <p>“{impact.evidenceSentence}”</p>
                    <footer>
                      <span>{RELATION_LABELS[language][impact.nlpRelationshipLabel] ?? copy.semanticLink}</span>
                      <b>{copy.company.inspect} →</b>
                    </footer>
                  </button>
                </article>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}

function GraphExplorer({
  data,
  selectedCompany,
  support,
  onSupport,
  onFocus,
  onCompany,
  onDetail,
  onAnalyzeEvent,
  language,
}: {
  data: DashboardData;
  selectedCompany: Company;
  support: number;
  onSupport: (value: number) => void;
  onFocus: (id: string) => void;
  onCompany: (id: string) => void;
  onDetail: (detail: Detail) => void;
  onAnalyzeEvent: (request: Omit<AssistantAnalysisRequest, "requestId">) => void;
  language: Language;
}) {
  const copy = COPY[language];
  const [focusOnly, setFocusOnly] = useState(false);
  const displayedEdges = useMemo(
    () => focusOnly
      ? data.network.edges.filter((edge) => edge.company1Id === selectedCompany.companyId || edge.company2Id === selectedCompany.companyId)
      : data.network.edges,
    [data.network.edges, focusOnly, selectedCompany.companyId],
  );
  const qualifiedEdges = useMemo(
    () => displayedEdges.filter((edge) => edge.sharedEventCount >= support),
    [displayedEdges, support],
  );
  const belowThresholdEdges = useMemo(
    () => displayedEdges.filter((edge) => edge.sharedEventCount < support),
    [displayedEdges, support],
  );
  const connectedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const edge of qualifiedEdges) {
      if (edge.company1Id === selectedCompany.companyId) ids.add(edge.company2Id);
      if (edge.company2Id === selectedCompany.companyId) ids.add(edge.company1Id);
    }
    return ids;
  }, [qualifiedEdges, selectedCompany.companyId]);
  const focusFromBubble = (companyId: string) => {
    onFocus(companyId);
    setFocusOnly(true);
    window.requestAnimationFrame(() => {
      document.getElementById("news-path-query")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  return (
    <div className={styles.view}>
      <section className={styles.relationHero}>
        <div>
          <p className={styles.sectionEyebrow}>{copy.graph.eyebrow}</p>
          <h2>{copy.graph.title}</h2>
          <p>{copy.graph.body}</p>
        </div>
        <div className={styles.graphControls}>
          <label>
            {copy.graph.focus}
            <select value={selectedCompany.companyId} onChange={(event) => { onFocus(event.target.value); setFocusOnly(true); }}>
              {[...data.companies].sort((a, b) => a.sourceRank - b.sourceRank).map((company) => (
                <option value={company.companyId} key={company.companyId}>{company.name}</option>
              ))}
            </select>
          </label>
          <label>
            {copy.relations.minimumShared}
            <select value={support} onChange={(event) => onSupport(Number(event.target.value))}>
              {[1, 2, 3, 5].map((value) => <option value={value} key={value}>{copy.relations.option(value, value === 2)}</option>)}
            </select>
          </label>
        </div>
      </section>

      <section className={`${styles.panel} ${styles.graphPanel}`}>
        <div className={styles.panelHeader}>
          <div>
            <p className={styles.sectionEyebrow}>{copy.graph.networkEyebrow}</p>
            <h3>{copy.graph.networkTitle}</h3>
          </div>
          <div className={styles.graphSummary}>
            <strong>{selectedCompany.name} · {selectedCompany.symbol}</strong>
            {focusOnly && <span className={styles.focusBadge}>{copy.graph.focusMode}</span>}
            <span>{copy.graph.connected(connectedIds.size)}</span>
            <span>{copy.graph.qualifiedLinks(qualifiedEdges.length)}</span>
            <span>{copy.graph.belowThresholdLinks(belowThresholdEdges.length)}</span>
            {focusOnly && <button type="button" onClick={() => setFocusOnly(false)}>{copy.graph.showAllLinks}</button>}
          </div>
        </div>
        <CompanyNetwork
          nodes={data.network.nodes}
          companies={data.companies}
          edges={displayedEdges}
          support={support}
          selectedId={selectedCompany.companyId}
          connectedIds={connectedIds}
          onSelect={focusFromBubble}
          language={language}
        />
        <div className={styles.graphFooter}>
          <div className={styles.graphLegend} aria-label={language === "en" ? "Network legend" : "网络图例"}>
            <span className={styles.nodeScaleLegend}>
              <i className={styles.legendNodeSmall} />
              <i className={styles.legendNodeMedium} />
              <i className={styles.legendNodeLarge} />
              {copy.graph.legendNode}
            </span>
            <span><i className={styles.legendEdgeQualified} />{copy.graph.legendQualified}</span>
            <span><i className={styles.legendEdgeBelow} />{copy.graph.legendBelow}</span>
            <span><i className={styles.legendCommunity} />{copy.graph.legendCommunity}</span>
          </div>
          <button type="button" className={styles.primaryButton} onClick={() => onCompany(selectedCompany.companyId)}>
            {copy.graph.openCompany} →
          </button>
        </div>
        <p className={styles.graphInstruction}>{copy.graph.nodeHint} {copy.graph.clickHint} {copy.graph.thresholdNote}</p>
        <p className={styles.causalWarning}><span aria-hidden="true">i</span>{copy.graph.caveat}</p>
      </section>

      <NewsPathQuery
        key={selectedCompany.companyId}
        data={data}
        company={selectedCompany}
        onCompany={onCompany}
        onDetail={onDetail}
        onAnalyzeEvent={onAnalyzeEvent}
        language={language}
      />
    </div>
  );
}

function NewsPathQuery({
  data,
  company,
  onCompany,
  onDetail,
  onAnalyzeEvent,
  language,
}: {
  data: DashboardData;
  company: Company;
  onCompany: (id: string) => void;
  onDetail: (detail: Detail) => void;
  onAnalyzeEvent: (request: Omit<AssistantAnalysisRequest, "requestId">) => void;
  language: Language;
}) {
  const copy = COPY[language];
  const [eventSearch, setEventSearch] = useState("");
  const [selectedEventId, setSelectedEventId] = useState("");
  const [selectedArticleKey, setSelectedArticleKey] = useState("");
  const eventById = useMemo(() => new Map(data.events.map((event) => [event.eventId, event])), [data.events]);
  const companyById = useMemo(() => new Map(data.companies.map((item) => [item.companyId, item])), [data.companies]);
  const articleCompanyCounts = useMemo(() => {
    const grouped = new Map<string, Set<string>>();
    for (const source of data.sources) {
      const companies = grouped.get(articleKeyFor(source)) ?? new Set<string>();
      companies.add(source.companyId);
      grouped.set(articleKeyFor(source), companies);
    }
    return new Map([...grouped.entries()].map(([key, companyIds]) => [key, companyIds.size]));
  }, [data.sources]);

  const eventOptions = useMemo(() => {
    const needle = eventSearch.trim().toLocaleLowerCase();
    return data.impacts
      .filter((impact) => impact.companyId === company.companyId)
      .map((impact) => {
        const event = eventById.get(impact.eventId);
        const articleText = data.sources
          .filter((source) => source.companyId === company.companyId && source.eventId === impact.eventId)
          .map((source) => source.articleTitle)
          .join(" ");
        return event ? { impact, event, searchable: `${event.title} ${event.summary} ${impact.evidenceSentence} ${articleText}`.toLocaleLowerCase() } : null;
      })
      .filter((row): row is { impact: Impact; event: EventRecord; searchable: string } => Boolean(row) && (!needle || row!.searchable.includes(needle)))
      .sort((a, b) => b.event.date.localeCompare(a.event.date));
  }, [company.companyId, data.impacts, data.sources, eventById, eventSearch]);

  const resolvedEventId = eventOptions.some((row) => row.event.eventId === selectedEventId)
    ? selectedEventId
    : (eventOptions[0]?.event.eventId ?? "");

  const articleOptions = useMemo(() => {
    const unique = new Map<string, Source>();
    for (const source of data.sources) {
      if (source.companyId === company.companyId && source.eventId === resolvedEventId) {
        unique.set(articleKeyFor(source), source);
      }
    }
    return [...unique.values()].sort((a, b) => Number(b.isRelationshipSource) - Number(a.isRelationshipSource) || b.publicationTimestamp.localeCompare(a.publicationTimestamp));
  }, [company.companyId, data.sources, resolvedEventId]);

  const resolvedArticleKey = articleOptions.some((source) => articleKeyFor(source) === selectedArticleKey)
    ? selectedArticleKey
    : (articleOptions[0] ? articleKeyFor(articleOptions[0]) : "");

  const selectedEventRow = eventOptions.find((row) => row.event.eventId === resolvedEventId) ?? null;
  const selectedArticle = articleOptions.find((source) => articleKeyFor(source) === resolvedArticleKey) ?? null;
  const articleRows = useMemo(
    () => resolvedArticleKey ? data.sources.filter((source) => articleKeyFor(source) === resolvedArticleKey) : [],
    [data.sources, resolvedArticleKey],
  );
  const relatedCompanies = useMemo(() => {
    const grouped = new Map<string, Set<string>>();
    for (const source of articleRows) {
      const events = grouped.get(source.companyId) ?? new Set<string>();
      events.add(source.eventId);
      grouped.set(source.companyId, events);
    }
    return [...grouped.entries()]
      .map(([companyId, eventIds]) => ({
        company: companyById.get(companyId),
        eventIds: [...eventIds],
        sharesSelectedEvent: eventIds.has(resolvedEventId),
      }))
      .filter((row): row is { company: Company; eventIds: string[]; sharesSelectedEvent: boolean } => Boolean(row.company))
      .sort((a, b) => Number(b.sharesSelectedEvent) - Number(a.sharesSelectedEvent) || b.eventIds.length - a.eventIds.length || a.company.sourceRank - b.company.sourceRank);
  }, [articleRows, companyById, resolvedEventId]);
  const sameEventExample = relatedCompanies.find((row) => row.sharesSelectedEvent);
  const otherEventExample = relatedCompanies.find((row) => !row.sharesSelectedEvent);
  const otherEventId = otherEventExample?.eventIds.find(
    (eventId) => eventId !== resolvedEventId,
  );
  const otherEventRecord = otherEventId ? eventById.get(otherEventId) : undefined;
  const reachedEventIds = new Set(articleRows.map((source) => source.eventId));
  const reachedLinks = new Set(articleRows.map((source) => keyFor(source.companyId, source.eventId)));

  return (
    <section id="news-path-query" className={`${styles.panel} ${styles.tracePanel}`}>
      <div className={styles.panelHeader}>
        <div>
          <p className={styles.sectionEyebrow}>{copy.graph.queryEyebrow}</p>
          <h3>{copy.graph.queryTitle}</h3>
        </div>
        <span className={styles.softTag}>{company.symbol}</span>
      </div>
      <p className={styles.traceIntro}>{copy.graph.queryBody}</p>
      <p className={styles.traceScope} aria-live="polite">{copy.graph.queryScope(company.name, eventOptions.length)}</p>

      <div className={styles.traceControls}>
        <label className={styles.traceSearch}>
          {copy.graph.eventSearch}
          <input
            type="search"
            value={eventSearch}
            onChange={(event) => {
              setEventSearch(event.target.value);
              setSelectedEventId("");
              setSelectedArticleKey("");
            }}
            placeholder={copy.graph.eventSearchPlaceholder}
          />
        </label>
        <label>
          {copy.graph.selectEvent}
          <select value={resolvedEventId} onChange={(event) => { setSelectedEventId(event.target.value); setSelectedArticleKey(""); }} disabled={eventOptions.length === 0}>
            {eventOptions.map(({ event }) => (
              <option value={event.eventId} key={event.eventId}>{formatDate(event.date, language)} · {event.title}</option>
            ))}
          </select>
        </label>
        <label>
          {copy.graph.selectArticle}
          <select value={resolvedArticleKey} onChange={(event) => setSelectedArticleKey(event.target.value)} disabled={articleOptions.length === 0}>
            {articleOptions.map((source) => (
              <option value={articleKeyFor(source)} key={articleKeyFor(source)}>
                {source.articleTitle} · {copy.graph.companyReach(articleCompanyCounts.get(articleKeyFor(source)) ?? 0)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {eventOptions.length === 0 ? (
        <EmptyState title={copy.graph.noEvents} body={copy.graph.eventSearchPlaceholder} />
      ) : articleOptions.length === 0 || !selectedArticle || !selectedEventRow ? (
        <EmptyState title={copy.graph.noArticles} body={selectedEventRow?.event.title ?? copy.graph.noArticles} />
      ) : (
        <div className={styles.traceResult} aria-live="polite">
          <div className={styles.articleBrief}>
            <div>
              <span>{formatSection(selectedArticle.sectionName || "News", language)} · {formatDate(selectedArticle.publicationTimestamp, language)}</span>
              <h4>{selectedArticle.articleTitle}</h4>
              <p>{selectedArticle.evidenceSentence}</p>
            </div>
            <div className={styles.traceActions}>
              <a href={selectedArticle.url} target="_blank" rel="noreferrer">{copy.graph.openArticle} ↗</a>
              <button type="button" onClick={() => onDetail({ event: selectedEventRow.event, impact: selectedEventRow.impact })}>{copy.graph.reviewEvent} →</button>
              <button
                type="button"
                className={styles.eventAiAction}
                aria-haspopup="dialog"
                aria-controls="research-assistant-dialog"
                aria-label={copy.graph.analyzeEventAria(selectedEventRow.event.title)}
                onClick={() => onAnalyzeEvent({
                  companyId: company.companyId,
                  eventId: selectedEventRow.event.eventId,
                  eventTitle: selectedEventRow.event.title,
                })}
              >
                <span aria-hidden="true">AI</span>
                {copy.graph.analyzeEvent} →
              </button>
            </div>
          </div>

          <div className={styles.traceMetrics} aria-label={copy.graph.articleReach}>
            <article><strong>{relatedCompanies.length}</strong><span>{copy.graph.companyReach(relatedCompanies.length)}</span></article>
            <article><strong>{reachedEventIds.size}</strong><span>{copy.graph.eventReach(reachedEventIds.size)}</span></article>
            <article><strong>{reachedLinks.size}</strong><span>{copy.graph.linkReach(reachedLinks.size)}</span></article>
          </div>

          <EvidencePath
            labels={[copy.graph.queryCompanyNode, copy.graph.queryEventNode, copy.graph.queryArticleNode, copy.graph.queryRelatedNode]}
            details={[company.name, selectedEventRow.event.title, selectedArticle.articleTitle, copy.graph.companyReach(relatedCompanies.length)]}
          />

          <div className={styles.relatedCompanies}>
            <p className={styles.sectionEyebrow}>{copy.graph.relatedCompanies}</p>
            <div className={styles.relatedLegend}>
              <span className={styles.sameEventKey}>{copy.graph.sameSelectedEvent}</span>
              <span className={styles.articleOnlyKey}>{copy.graph.sameArticleOtherEvent}</span>
            </div>
            <div className={styles.articleLabelGuide}>
              <div>
                <p>{copy.graph.labelGuideTitle}</p>
                <span>{copy.graph.labelGuideIntro}</span>
              </div>
              <div className={styles.selectedEventExample}>
                <small>{copy.graph.selectedEventLabel}</small>
                <strong>{selectedEventRow.event.title}</strong>
              </div>
              <div className={styles.labelExampleGrid}>
                <article className={styles.sameEventExample}>
                  <span>{copy.graph.sameSelectedEvent}</span>
                  <p>{copy.graph.sameEventExample(sameEventExample?.company.name ?? company.name)}</p>
                </article>
                <article className={styles.otherEventExample}>
                  <span>{copy.graph.sameArticleOtherEvent}</span>
                  <p>{otherEventExample && otherEventRecord
                    ? copy.graph.otherEventExample(otherEventExample.company.name, otherEventRecord.title)
                    : copy.graph.otherEventMissing}</p>
                </article>
              </div>
            </div>
            <div className={styles.relatedCompanyGrid}>
              {relatedCompanies.map((row) => (
                <button
                  type="button"
                  className={row.sharesSelectedEvent ? styles.relatedCompanySameEvent : styles.relatedCompanyArticleOnly}
                  key={row.company.companyId}
                  onClick={() => onCompany(row.company.companyId)}
                >
                  <span className={styles.companyMonogram}>{row.company.symbol.slice(0, 2)}</span>
                  <span>
                    <strong>{row.company.name}</strong>
                    <small>{row.sharesSelectedEvent ? copy.graph.sameSelectedEvent : copy.graph.sameArticleOtherEvent} · {copy.graph.linkedEvents(row.eventIds.length)}</small>
                  </span>
                </button>
              ))}
            </div>
          </div>
          <p className={styles.traceDefinition}>{copy.graph.countDefinition}</p>
        </div>
      )}
    </section>
  );
}

function CompanyNetwork({
  nodes,
  companies,
  edges,
  support,
  selectedId,
  connectedIds,
  onSelect,
  language,
}: {
  nodes: NetworkNode[];
  companies: Company[];
  edges: NetworkEdge[];
  support: number;
  selectedId: string;
  connectedIds: Set<string>;
  onSelect: (id: string) => void;
  language: Language;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const companyById = useMemo(() => new Map(companies.map((company) => [company.companyId, company])), [companies]);
  const orderedNodes = useMemo(
    () => [...nodes].sort((a, b) => a.louvainCommunity.localeCompare(b.louvainCommunity) || a.sourceRank - b.sourceRank),
    [nodes],
  );
  const layout = useMemo(
    () => buildNetworkLayout(orderedNodes, edges, selectedId),
    [edges, orderedNodes, selectedId],
  );
  const positions = layout.positions;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(rect.width * scale));
      canvas.height = Math.max(1, Math.round(rect.height * scale));
      const context = canvas.getContext("2d");
      if (!context) return;
      context.setTransform(scale, 0, 0, scale, 0, 0);
      context.clearRect(0, 0, rect.width, rect.height);
      const maximum = Math.max(...edges.map((edge) => edge.sharedEventCount), 1);
      const orderedEdges = [...edges].sort(
        (a, b) => Number(a.sharedEventCount >= support) - Number(b.sharedEventCount >= support),
      );
      for (const edge of orderedEdges) {
        const start = positions.get(edge.company1Id);
        const end = positions.get(edge.company2Id);
        if (!start || !end) continue;
        const highlighted = edge.company1Id === selectedId || edge.company2Id === selectedId;
        const qualified = edge.sharedEventCount >= support;
        context.beginPath();
        context.moveTo((start.x / 100) * rect.width, (start.y / 100) * rect.height);
        context.lineTo((end.x / 100) * rect.width, (end.y / 100) * rect.height);
        context.setLineDash(qualified ? [] : [5, 5]);
        context.lineWidth = qualified ? 0.9 + (edge.sharedEventCount / maximum) * 4 : 0.75;
        context.strokeStyle = qualified
          ? (highlighted ? "rgba(181, 141, 51, 0.92)" : "rgba(62, 105, 88, 0.24)")
          : (highlighted ? "rgba(181, 141, 51, 0.46)" : "rgba(112, 124, 117, 0.13)");
        context.stroke();
      }
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [edges, positions, selectedId, support]);

  const minimumEvents = Math.min(...orderedNodes.map((node) => node.eventCount));
  const maximumEvents = Math.max(...orderedNodes.map((node) => node.eventCount), 1);
  const minimumRoot = Math.sqrt(minimumEvents);
  const rootSpan = Math.max(Math.sqrt(maximumEvents) - minimumRoot, 1);
  return (
    <div className={styles.networkStage}>
      {layout.isolateIds.size > 0 && (
        <div className={styles.networkIsolateRail} aria-hidden="true">
          <span>{COPY[language].graph.isolateLane(layout.isolateIds.size)}</span>
        </div>
      )}
      <canvas ref={canvasRef} className={styles.networkCanvas} aria-hidden="true" />
      {orderedNodes.map((node) => {
        const company = companyById.get(node.companyId);
        const position = positions.get(node.companyId) ?? { x: 50, y: 50 };
        const selected = node.companyId === selectedId;
        const connected = connectedIds.has(node.companyId);
        const normalisedCoverage = (Math.sqrt(node.eventCount) - minimumRoot) / rootSpan;
        const size = 34 + normalisedCoverage * 44;
        return (
          <div
            key={node.companyId}
            className={`${styles.networkNodeWrap} ${selected ? styles.networkNodeWrapSelected : ""}`}
            style={{ left: `${position.x}%`, top: `${position.y}%` }}
          >
            <button
              type="button"
              className={`${styles.networkNode} ${selected ? styles.networkNodeSelected : ""} ${connected ? styles.networkNodeConnected : ""}`}
              style={{ width: size, height: size, "--community": communityColour(node.louvainCommunity) } as React.CSSProperties}
              onClick={() => onSelect(node.companyId)}
              title={`${company?.name ?? node.company} · ${company?.symbol ?? node.companyId} · ${node.eventCount} ${language === "en" ? "events" : "个事件"}`}
              aria-label={`${company?.name ?? node.company}, ${company?.symbol ?? node.companyId}, ${node.eventCount} ${language === "en" ? "linked events; select to filter the event query" : "个关联事件；点击后筛选事件查询"}`}
            >
              {company?.symbol ?? node.companyId}
            </button>
            <span className={styles.networkNodeLabel}>{company?.name ?? node.company} · {node.eventCount}</span>
          </div>
        );
      })}
    </div>
  );
}

type NetworkPosition = { x: number; y: number };

function buildNetworkLayout(nodes: NetworkNode[], edges: NetworkEdge[], selectedId: string) {
  const nodeById = new Map(nodes.map((node) => [node.companyId, node]));
  const adjacency = new Map(nodes.map((node) => [node.companyId, new Set<string>()]));
  const edgeWeight = new Map<string, number>();
  const visibleIds = new Set<string>();

  for (const edge of edges) {
    if (!nodeById.has(edge.company1Id) || !nodeById.has(edge.company2Id)) continue;
    adjacency.get(edge.company1Id)?.add(edge.company2Id);
    adjacency.get(edge.company2Id)?.add(edge.company1Id);
    visibleIds.add(edge.company1Id);
    visibleIds.add(edge.company2Id);
    edgeWeight.set(keyFor(edge.company1Id, edge.company2Id), edge.sharedEventCount);
    edgeWeight.set(keyFor(edge.company2Id, edge.company1Id), edge.sharedEventCount);
  }

  const focusedWithoutLinks = edges.length === 0 && nodeById.has(selectedId);
  if (focusedWithoutLinks) visibleIds.add(selectedId);

  const activeNodes = nodes.filter((node) => visibleIds.has(node.companyId));
  const isolateNodes = nodes
    .filter((node) => !visibleIds.has(node.companyId))
    .sort((a, b) => a.sourceRank - b.sourceRank);
  const positions = new Map<string, NetworkPosition>();

  placeIsolateLane(isolateNodes, positions);

  if (activeNodes.length === 1) {
    positions.set(activeNodes[0].companyId, { x: 66, y: 50 });
  } else if (activeNodes.length > 1) {
    const selectedEdges = edges.filter(
      (edge) => edge.company1Id === selectedId || edge.company2Id === selectedId,
    );
    const focusedStar = selectedEdges.length > 0 && selectedEdges.length === edges.length;
    if (focusedStar) {
      placeFocusedStar(activeNodes, selectedId, edgeWeight, positions);
    } else {
      placeConnectedComponents(activeNodes, adjacency, edgeWeight, positions);
    }
  }

  return {
    positions,
    isolateIds: new Set(isolateNodes.map((node) => node.companyId)),
  };
}

function placeIsolateLane(nodes: NetworkNode[], positions: Map<string, NetworkPosition>) {
  if (nodes.length === 0) return;
  const columns = nodes.length > 12 ? 3 : nodes.length > 5 ? 2 : 1;
  const rows = Math.ceil(nodes.length / columns);
  const xValues = columns === 1 ? [15] : columns === 2 ? [9, 21] : [6, 16, 26];
  nodes.forEach((node, index) => {
    const column = Math.floor(index / rows);
    const row = index % rows;
    const y = rows === 1 ? 50 : 15 + (row / (rows - 1)) * 70;
    positions.set(node.companyId, { x: xValues[column] ?? 15, y });
  });
}

function placeFocusedStar(
  nodes: NetworkNode[],
  selectedId: string,
  edgeWeight: Map<string, number>,
  positions: Map<string, NetworkPosition>,
) {
  const centre = { x: 66, y: 50 };
  positions.set(selectedId, centre);
  const neighbours = nodes
    .filter((node) => node.companyId !== selectedId)
    .sort((a, b) => {
      const weightDifference = (edgeWeight.get(keyFor(selectedId, b.companyId)) ?? 0)
        - (edgeWeight.get(keyFor(selectedId, a.companyId)) ?? 0);
      return weightDifference || a.sourceRank - b.sourceRank;
    });
  neighbours.forEach((node, index) => {
    const angle = (index / neighbours.length) * Math.PI * 2 - Math.PI / 2;
    positions.set(node.companyId, {
      x: centre.x + Math.cos(angle) * 25,
      y: centre.y + Math.sin(angle) * 34,
    });
  });
}

function placeConnectedComponents(
  nodes: NetworkNode[],
  adjacency: Map<string, Set<string>>,
  edgeWeight: Map<string, number>,
  positions: Map<string, NetworkPosition>,
) {
  const nodeById = new Map(nodes.map((node) => [node.companyId, node]));
  const remaining = new Set(nodes.map((node) => node.companyId));
  const components: NetworkNode[][] = [];

  while (remaining.size > 0) {
    const first = remaining.values().next().value as string;
    const queue = [first];
    const component: NetworkNode[] = [];
    remaining.delete(first);
    while (queue.length > 0) {
      const companyId = queue.shift()!;
      const node = nodeById.get(companyId);
      if (node) component.push(node);
      for (const neighbour of adjacency.get(companyId) ?? []) {
        if (remaining.delete(neighbour)) queue.push(neighbour);
      }
    }
    components.push(component);
  }

  components.sort((a, b) => b.length - a.length || a[0].sourceRank - b[0].sourceRank);
  const [largest, ...smaller] = components;
  placeRadialComponent(largest, adjacency, edgeWeight, positions, { x: 67, y: smaller.length ? 40 : 50, radiusX: 27, radiusY: smaller.length ? 30 : 36 });

  smaller.forEach((component, index) => {
    const spacing = 54 / Math.max(smaller.length, 1);
    const centreX = 39 + spacing * (index + 0.5);
    placeRadialComponent(component, adjacency, edgeWeight, positions, { x: centreX, y: 83, radiusX: Math.min(8, spacing * 0.32), radiusY: 8 });
  });
}

function placeRadialComponent(
  nodes: NetworkNode[],
  adjacency: Map<string, Set<string>>,
  edgeWeight: Map<string, number>,
  positions: Map<string, NetworkPosition>,
  region: { x: number; y: number; radiusX: number; radiusY: number },
) {
  if (nodes.length === 0) return;
  if (nodes.length === 1) {
    positions.set(nodes[0].companyId, { x: region.x, y: region.y });
    return;
  }
  if (nodes.length === 2) {
    positions.set(nodes[0].companyId, { x: region.x - region.radiusX, y: region.y });
    positions.set(nodes[1].companyId, { x: region.x + region.radiusX, y: region.y });
    return;
  }

  const componentIds = new Set(nodes.map((node) => node.companyId));
  const weightedDegree = (companyId: string) => [...(adjacency.get(companyId) ?? [])]
    .filter((neighbour) => componentIds.has(neighbour))
    .reduce((total, neighbour) => total + (edgeWeight.get(keyFor(companyId, neighbour)) ?? 1), 0);
  const anchor = [...nodes].sort(
    (a, b) => weightedDegree(b.companyId) - weightedDegree(a.companyId) || a.sourceRank - b.sourceRank,
  )[0];
  positions.set(anchor.companyId, { x: region.x, y: region.y });

  const ring = nodes
    .filter((node) => node.companyId !== anchor.companyId)
    .sort((a, b) => a.louvainCommunity.localeCompare(b.louvainCommunity) || weightedDegree(b.companyId) - weightedDegree(a.companyId) || a.sourceRank - b.sourceRank);
  const scale = Math.min(1, 0.52 + ring.length * 0.045);
  ring.forEach((node, index) => {
    const angle = (index / ring.length) * Math.PI * 2 - Math.PI / 2;
    positions.set(node.companyId, {
      x: region.x + Math.cos(angle) * region.radiusX * scale,
      y: region.y + Math.sin(angle) * region.radiusY * scale,
    });
  });
}

function communityColour(value: string) {
  const palette = ["#2a7c62", "#4f7690", "#b38a36", "#8d665f", "#6f7f50", "#7e6c91"];
  const number = Number(value.replace(/\D/g, "")) || 0;
  return palette[number % palette.length];
}

function MonthlyActivityChart({ months, values, language }: { months: string[]; values: number[]; language: Language }) {
  const maximum = Math.max(...values, 1);
  return (
    <div className={styles.monthChart} role="img" aria-label={language === "en" ? "Monthly linked-event bar chart" : "每月关联事件柱状图"}>
      {months.map((month, index) => {
        const value = values[index] ?? 0;
        const label = new Intl.DateTimeFormat(language === "en" ? "en-GB" : "zh-CN", { month: "short" }).format(new Date(`${month}-01T00:00:00`));
        return (
          <div className={styles.monthColumn} key={month} title={`${month}: ${value}`}>
            <strong>{value}</strong>
            <span aria-hidden="true"><i style={{ height: `${Math.max(value ? 8 : 2, (value / maximum) * 100)}%` }} /></span>
            <small>{label}</small>
          </div>
        );
      })}
    </div>
  );
}

function EvidencePath({ labels, details }: { labels: string[]; details?: string[] }) {
  return (
    <div className={styles.evidencePath}>
      {labels.map((label, index) => (
        <div className={styles.evidenceStep} key={`${label}-${index}`}>
          <article>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{label}</strong>
            {details?.[index] && <small>{details[index]}</small>}
          </article>
          {index < labels.length - 1 && <i aria-hidden="true">→</i>}
        </div>
      ))}
    </div>
  );
}

function MarketReturnChart({ rows, language }: { rows: MarketObservation[]; language: Language }) {
  const maximum = Math.max(...rows.map((row) => Math.abs(row.cumulativeReturn)), 0.0001);
  const copy = COPY[language].drawer;
  return (
    <div className={styles.marketReturnChart} role="img" aria-label={copy.marketChart}>
      <div className={styles.marketReturnChartInner}>
        <div className={styles.marketReturnGroupLabels} aria-hidden="true">
          <span>{copy.beforePublication}</span>
          <span>{copy.afterPublication}</span>
        </div>
        <div className={styles.marketReturnColumns}>
          {rows.map((row) => {
            const height = Math.max(4, (Math.abs(row.cumulativeReturn) / maximum) * 45);
            return (
              <div className={styles.marketReturnColumn} key={`chart-${row.windowDays}`}>
                <strong className={row.cumulativeReturn >= 0 ? styles.returnPositive : styles.returnNegative}>{formatReturn(row.cumulativeReturn)}</strong>
                <div className={styles.marketReturnVerticalTrack} aria-hidden="true">
                  <i
                    className={row.cumulativeReturn >= 0 ? styles.marketReturnPositive : styles.marketReturnNegative}
                    style={{ height: `${height}%` }}
                  />
                </div>
                <span>{copy.day(row.windowDays)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function EventAlerts({
  rows,
  eventFilter,
  minimumProbability,
  onEventFilter,
  onProbability,
  onDetail,
  language,
}: {
  rows: Array<{ impact: Impact; event: EventRecord; company: Company }>;
  eventFilter: string;
  minimumProbability: string;
  onEventFilter: (value: string) => void;
  onProbability: (value: string) => void;
  onDetail: (detail: Detail) => void;
  language: Language;
}) {
  const copy = COPY[language];
  return (
    <div className={styles.view}>
      <section className={styles.introRow}>
        <div>
          <p className={styles.sectionEyebrow}>{copy.alerts.eyebrow}</p>
          <h2>{copy.alerts.title}</h2>
          <p>{copy.alerts.body}</p>
        </div>
        <div className={styles.filterGroup}>
          <label>
            {copy.alerts.eventType}
            <select value={eventFilter} onChange={(event) => onEventFilter(event.target.value)}>
              <option value="regulatory_event">{EVENT_LABELS[language].regulatory_event}</option>
              <option value="corporate_event">{EVENT_LABELS[language].corporate_event}</option>
              <option value="geopolitical_event">{EVENT_LABELS[language].geopolitical_event}</option>
              <option value="macroeconomic_event">{EVENT_LABELS[language].macroeconomic_event}</option>
              <option value="market_wide_event">{EVENT_LABELS[language].market_wide_event}</option>
              <option value="all">{copy.alerts.allTypes}</option>
            </select>
          </label>
          <label>
            {copy.alerts.minimumMatch}
            <select value={minimumProbability} onChange={(event) => onProbability(event.target.value)}>
              <option value="0">{copy.alerts.allScores}</option>
              <option value="0.60">60%</option>
              <option value="0.70">70%</option>
              <option value="0.80">80%</option>
              <option value="0.90">90%</option>
            </select>
          </label>
        </div>
      </section>

      <div className={styles.alertSummary}>
        <strong>{rows.length}</strong>
        <span>{copy.alerts.resultCount(rows.length)}</span>
        <p><i aria-hidden="true" /> {copy.alerts.instruction}</p>
      </div>

      <section className={styles.alertGrid}>
        {rows.slice(0, 120).map(({ impact, event, company }) => (
          <article className={styles.alertCard} key={`${company.companyId}-${event.eventId}`}>
            <header>
              <span className={`${styles.eventPill} ${event.type === "regulatory_event" ? styles.alertPill : ""}`}>
                {EVENT_LABELS[language][event.type] ?? copy.otherEvent}
              </span>
              <time>{formatDate(event.date, language)}</time>
            </header>
            <p className={styles.alertCompany}>{company.name} <small>{company.symbol}</small></p>
            <h3>{event.title}</h3>
            <blockquote>{impact.evidenceSentence}</blockquote>
            <footer>
              <span className={styles.relevanceMeter}>
                <i style={{ width: `${impact.nlpPositiveProbability * 100}%` }} />
              </span>
              <strong>{copy.alerts.textMatch} {Math.round(impact.nlpPositiveProbability * 100)}%</strong>
              <button type="button" onClick={() => onDetail({ event, impact })}>{copy.alerts.inspectEvidence} →</button>
            </footer>
          </article>
        ))}
      </section>
      {rows.length === 0 && <EmptyState title={copy.alerts.emptyTitle} body={copy.alerts.emptyBody} />}
      {rows.length > 120 && <p className={styles.resultLimit}>{copy.alerts.resultLimit(rows.length)}</p>}
    </div>
  );
}

function Relations({
  similarities,
  companies,
  matrix,
  support,
  onSupport,
  onCompany,
  language,
}: {
  similarities: Similarity[];
  companies: Company[];
  matrix: VisualizationData["sharedEventMatrix"];
  support: number;
  onSupport: (value: number) => void;
  onCompany: (id: string) => void;
  language: Language;
}) {
  const copy = COPY[language];
  const rows = [...similarities]
    .filter((row) => row.sharedEventCount >= support)
    .sort((a, b) => b.similarity - a.similarity || b.sharedEventCount - a.sharedEventCount);

  return (
    <div className={styles.view}>
      <section className={styles.relationHero}>
        <div>
          <p className={styles.sectionEyebrow}>{copy.relations.eyebrow}</p>
          <h2>{copy.relations.title}</h2>
          <p>{copy.relations.body}</p>
        </div>
        <label className={styles.supportControl}>
          {copy.relations.minimumShared}
          <select value={support} onChange={(event) => onSupport(Number(event.target.value))}>
            <option value={1}>{copy.relations.option(1)}</option>
            <option value={2}>{copy.relations.option(2, true)}</option>
            <option value={3}>{copy.relations.option(3)}</option>
            <option value={5}>{copy.relations.option(5)}</option>
          </select>
        </label>
      </section>

      <section className={styles.relationExplainer} aria-label={copy.relations.explanationLabel}>
        <div>
          <span>{copy.relations.overlap}</span>
          <strong>{copy.relations.formula}</strong>
        </div>
        <p>{copy.relations.explanation}</p>
      </section>

      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <div>
            <p className={styles.sectionEyebrow}>{copy.relations.heatmapEyebrow}</p>
            <h3>{copy.relations.heatmapTitle}</h3>
          </div>
          <span className={styles.softTag}>{copy.relations.sharedEvents}</span>
        </div>
        <SharedEventHeatmap companies={companies} matrix={matrix} language={language} onCompany={onCompany} />
        <p className={styles.caption}>{copy.relations.heatmapCaption}</p>
      </section>

      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <div>
            <p className={styles.sectionEyebrow}>{copy.relations.listEyebrow}</p>
            <h3>{copy.relations.pairCount(rows.length)}</h3>
          </div>
          <span className={styles.softTag}>{copy.relations.sort}</span>
        </div>
        <div className={styles.relationTable} role="table" aria-label={copy.relations.tableLabel}>
          <div className={styles.relationHead} role="row">
            <span role="columnheader">{copy.relations.companyPair}</span>
            <span role="columnheader">{copy.relations.sharedEvents}</span>
            <span role="columnheader">{copy.relations.combinedEvents}</span>
            <span role="columnheader">{copy.relations.eventOverlap}</span>
          </div>
          {rows.map((row) => (
            <div className={styles.relationRow} role="row" key={`${row.company1Id}-${row.company2Id}`}>
              <div className={styles.relationCompanies} role="cell">
                <button type="button" onClick={() => onCompany(row.company1Id)}>{row.company1}</button>
                <span>×</span>
                <button type="button" onClick={() => onCompany(row.company2Id)}>{row.company2}</button>
              </div>
              <strong role="cell">{row.sharedEventCount}</strong>
              <span role="cell">{row.eventUnionCount}</span>
              <div className={styles.similarityCell} role="cell">
                <span aria-hidden="true"><i style={{ width: `${row.similarity * 100}%` }} /></span>
                <strong>{(row.similarity * 100).toFixed(1)}%</strong>
              </div>
            </div>
          ))}
        </div>
        {rows.length === 0 && <EmptyState title={copy.relations.emptyTitle} body={copy.relations.emptyBody} />}
      </section>
    </div>
  );
}

function SharedEventHeatmap({
  companies,
  matrix,
  language,
  onCompany,
}: {
  companies: Company[];
  matrix: VisualizationData["sharedEventMatrix"];
  language: Language;
  onCompany: (id: string) => void;
}) {
  const ordered = useMemo(
    () => [...companies].sort((a, b) => b.eventCount - a.eventCount || a.sourceRank - b.sourceRank),
    [companies],
  );
  const values = useMemo(() => {
    const map = new Map<string, { count: number; similarity: number }>();
    for (const cell of matrix.cells) {
      map.set(keyFor(cell.company1Id, cell.company2Id), { count: cell.sharedEventCount, similarity: cell.similarity });
      map.set(keyFor(cell.company2Id, cell.company1Id), { count: cell.sharedEventCount, similarity: cell.similarity });
    }
    return map;
  }, [matrix.cells]);
  const sharedMaximum = Math.max(matrix.maximumSharedEventCount, 1);
  const eventMaximum = Math.max(...ordered.map((company) => company.eventCount), 1);

  return (
    <div className={styles.heatmapScroll}>
      <div className={styles.heatmap} style={{ gridTemplateColumns: `86px repeat(${ordered.length}, 30px)` }} role="table" aria-label={COPY[language].relations.heatmapTitle}>
        <span className={styles.heatmapCorner} />
        {ordered.map((company) => (
          <button type="button" className={styles.heatmapColumnLabel} onClick={() => onCompany(company.companyId)} key={`head-${company.companyId}`} title={company.name}>
            {company.symbol}
          </button>
        ))}
        {ordered.map((rowCompany) => (
          <div className={styles.heatmapRow} style={{ gridColumn: `1 / span ${ordered.length + 1}`, gridTemplateColumns: `86px repeat(${ordered.length}, 30px)` }} role="row" key={rowCompany.companyId}>
            <button type="button" className={styles.heatmapRowLabel} onClick={() => onCompany(rowCompany.companyId)} title={rowCompany.name}>{rowCompany.symbol}</button>
            {ordered.map((columnCompany) => {
              const diagonal = rowCompany.companyId === columnCompany.companyId;
              const cell = values.get(keyFor(rowCompany.companyId, columnCompany.companyId));
              const count = diagonal ? rowCompany.eventCount : (cell?.count ?? 0);
              const alpha = count === 0 ? 0.035 : 0.14 + Math.sqrt(count / (diagonal ? eventMaximum : sharedMaximum)) * 0.78;
              const title = diagonal
                ? `${rowCompany.name}: ${count} ${language === "en" ? "linked events" : "个关联事件"}`
                : `${rowCompany.name} × ${columnCompany.name}: ${count} ${language === "en" ? "shared events" : "个共同事件"}${cell ? ` · ${(cell.similarity * 100).toFixed(1)}%` : ""}`;
              return (
                <span
                  className={`${styles.heatmapCell} ${diagonal ? styles.heatmapDiagonal : ""}`}
                  style={{ backgroundColor: diagonal ? `rgba(181, 141, 51, ${alpha})` : `rgba(11, 107, 80, ${alpha})` }}
                  role="cell"
                  tabIndex={0}
                  title={title}
                  aria-label={title}
                  key={columnCompany.companyId}
                >
                  {count > 0 ? count : ""}
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function ResearchAssistant({
  language,
  selectedCompany,
  analysisRequest,
  onCompany,
  onEvent,
}: {
  language: Language;
  selectedCompany: Company;
  analysisRequest: AssistantAnalysisRequest | null;
  onCompany: (companyId: string) => void;
  onEvent: (companyId: string, eventId: string) => void;
}) {
  const copy = ASSISTANT_COPY[language];
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [questionEventId, setQuestionEventId] = useState("");
  const [result, setResult] = useState<AssistantResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const handledAnalysisRequestRef = useRef(0);
  const requestRef = useRef<AbortController | null>(null);
  const suggestions = copy.suggestions(selectedCompany.name);

  const close = () => {
    requestRef.current?.abort();
    setLoading(false);
    setOpen(false);
    const returnTarget = returnFocusRef.current;
    returnFocusRef.current = null;
    window.setTimeout(() => {
      if (returnTarget?.isConnected) returnTarget.focus();
      else launcherRef.current?.focus();
    }, 0);
  };

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", handleKeyDown);
    window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  useEffect(() => () => requestRef.current?.abort(), []);

  const ask = useCallback(async (
    nextQuestion = question,
    options: { selectedCompanyId?: string; selectedEventId?: string; replace?: boolean } = {},
  ) => {
    const trimmed = nextQuestion.trim();
    if (!trimmed || (loading && !options.replace)) return;
    const selectedEventId = options.selectedEventId ?? questionEventId;
    setQuestion(trimmed);
    setQuestionEventId(selectedEventId);
    setError("");
    setResult(null);
    setLoading(true);
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const response = await fetch("/api/research-assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: trimmed,
          language,
          selectedCompanyId: options.selectedCompanyId ?? selectedCompany.companyId,
          selectedEventId: selectedEventId || undefined,
        }),
        signal: controller.signal,
      });
      const payload = await response.json() as AssistantResponse & { error?: string };
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      setResult(payload);
    } catch (requestError) {
      if ((requestError as Error).name !== "AbortError") setError(copy.error);
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  }, [copy.error, language, loading, question, questionEventId, selectedCompany.companyId]);

  useEffect(() => {
    if (!analysisRequest || handledAnalysisRequestRef.current === analysisRequest.requestId) return;
    handledAnalysisRequestRef.current = analysisRequest.requestId;
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const prompt = copy.eventPrompt(
      selectedCompany.name,
      analysisRequest.eventTitle,
      analysisRequest.eventId,
    ).slice(0, 600);
    setOpen(true);
    void ask(prompt, {
      selectedCompanyId: analysisRequest.companyId,
      selectedEventId: analysisRequest.eventId,
      replace: true,
    });
  }, [analysisRequest, ask, copy, selectedCompany.name]);

  const companyEvidence = result?.evidence?.companies ?? [];
  const eventEvidence = result?.evidence?.events ?? [];
  const connectionEvidence = result?.evidence?.connections ?? [];
  const statusCopy = result ? copy.status[result.status] : null;
  const diagnosticCopy = result?.errorCode ? copy.diagnostics[result.errorCode] : statusCopy;

  const openCompanyEvidence = (companyId: string) => {
    close();
    onCompany(companyId);
  };

  const openEventEvidence = (companyId: string, eventId: string) => {
    close();
    onEvent(companyId, eventId);
  };

  return (
    <>
      <button
        ref={launcherRef}
        type="button"
        className={styles.assistantLauncher}
        onClick={() => {
          returnFocusRef.current = launcherRef.current;
          setOpen(true);
        }}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="research-assistant-dialog"
      >
        <span aria-hidden="true">AI</span>
        {copy.launcher}
      </button>

      {open && (
        <div className={styles.assistantBackdrop} onMouseDown={(event) => event.currentTarget === event.target && close()}>
          <aside
            id="research-assistant-dialog"
            className={styles.assistantDrawer}
            role="dialog"
            aria-modal="true"
            aria-labelledby="research-assistant-title"
          >
            <header className={styles.assistantHeader}>
              <div>
                <p>{copy.eyebrow}</p>
                <h2 id="research-assistant-title">{copy.title}</h2>
              </div>
              <button type="button" onClick={close} aria-label={copy.close}>×</button>
            </header>

            <div className={styles.assistantBody}>
              <p className={styles.assistantIntro}>{copy.intro}</p>

              <section className={styles.assistantSuggestions} aria-labelledby="assistant-suggestions-title">
                <h3 id="assistant-suggestions-title">{copy.suggestionsLabel}</h3>
                <div>
                  {suggestions.map((suggestion) => (
                    <button type="button" key={suggestion} onClick={() => ask(suggestion, { selectedEventId: "" })} disabled={loading}>
                      {suggestion}
                    </button>
                  ))}
                </div>
              </section>

              <form
                className={styles.assistantForm}
                onSubmit={(event) => {
                  event.preventDefault();
                  void ask();
                }}
              >
                <label htmlFor="research-assistant-question">{copy.inputLabel}</label>
                <textarea
                  ref={inputRef}
                  id="research-assistant-question"
                  rows={3}
                  maxLength={600}
                  value={question}
                  onChange={(event) => {
                    setQuestion(event.target.value);
                    setQuestionEventId("");
                  }}
                  placeholder={copy.inputPlaceholder}
                />
                <button type="submit" disabled={loading || !question.trim()}>
                  {loading ? copy.asking : copy.ask}
                </button>
              </form>

              <div className={styles.assistantStatus} role="status" aria-live="polite">
                {loading && <span>{copy.asking}</span>}
                {error && <p className={styles.assistantError}>{error}</p>}
              </div>

              {result && (
                <section className={styles.assistantAnswer} aria-labelledby="assistant-answer-title">
                  <div className={styles.assistantAnswerHeading}>
                    <h3 id="assistant-answer-title">{copy.answerTitle}</h3>
                    <span data-status={result.status}>{statusCopy?.label ?? (result.mode === "ai" ? copy.ai : copy.preview)}</span>
                  </div>
                  {diagnosticCopy && (
                    <div className={styles.assistantDiagnostic} data-status={result.status}>
                      <strong>{diagnosticCopy.label}</strong>
                      <p>{diagnosticCopy.body}</p>
                      {result.errorCode && <code>{result.errorCode}</code>}
                    </div>
                  )}
                  <p>{result.answer}</p>
                  {result.note && <small>{result.note}</small>}

                  <div className={styles.assistantEvidence}>
                    <h3>{copy.evidenceTitle}</h3>

                    {companyEvidence.length > 0 && (
                      <div className={styles.assistantCompanyGrid}>
                        {companyEvidence.map((company) => (
                          <button
                            type="button"
                            key={company.companyId}
                            onClick={() => openCompanyEvidence(company.companyId)}
                          >
                            <span>{company.symbol || company.name.slice(0, 2)}</span>
                            <strong>{company.name}</strong>
                            <small>{copy.companyAction} · {company.eventCount}</small>
                          </button>
                        ))}
                      </div>
                    )}

                    {connectionEvidence.length > 0 && (
                      <div className={styles.assistantConnections}>
                        {connectionEvidence.map((connection, index) => (
                          <p key={`${connection.company1Id ?? connection.company1}-${connection.company2Id ?? connection.company2}-${index}`}>
                            <strong>{connection.company1}</strong>
                            <span aria-hidden="true">↔</span>
                            <strong>{connection.company2}</strong>
                            {typeof connection.sharedEventCount === "number" && <small>{connection.sharedEventCount}</small>}
                          </p>
                        ))}
                      </div>
                    )}

                    {eventEvidence.map((event) => (
                      <article className={styles.assistantEventCard} key={`${event.companyId}-${event.eventId}`}>
                        <div>
                          <span>{event.date ? formatDate(event.date, language) : copy.eventFallback}</span>
                          <span>{EVENT_LABELS[language][event.type] ?? copy.eventFallback}</span>
                        </div>
                        <h4>{event.title}</h4>
                        {event.evidenceSentence && <blockquote>{event.evidenceSentence}</blockquote>}
                        {event.market?.length > 0 && (
                          <div className={styles.assistantMarket} aria-label={copy.marketContext}>
                            {event.market
                              .slice()
                              .sort((a, b) => a.windowDays - b.windowDays)
                              .map((row) => (
                                <span key={row.windowDays}>
                                  {row.windowDays > 0 ? "+" : ""}{row.windowDays}d {formatReturn(row.cumulativeReturn)}
                                </span>
                              ))}
                          </div>
                        )}
                        <div className={styles.assistantEventActions}>
                          <button type="button" onClick={() => openEventEvidence(event.companyId, event.eventId)}>
                            {copy.eventAction}
                          </button>
                          {event.sourceUrl && (
                            <a href={event.sourceUrl} target="_blank" rel="noreferrer">
                              {copy.sourceAction} ↗
                            </a>
                          )}
                        </div>
                      </article>
                    ))}

                    {companyEvidence.length === 0 && eventEvidence.length === 0 && connectionEvidence.length === 0 && (
                      <p className={styles.assistantEmptyEvidence}>{copy.emptyEvidence}</p>
                    )}
                  </div>
                  {result.disclaimer && <p className={styles.assistantDisclaimer}>{result.disclaimer}</p>}
                </section>
              )}
            </div>
          </aside>
        </div>
      )}
    </>
  );
}

function DataNotes({ data, generatedAt, language }: { data: DashboardData; generatedAt: string; language: Language }) {
  const copy = COPY[language];
  return (
    <div className={styles.view}>
      <section className={styles.notesHero}>
        <p className={styles.sectionEyebrow}>{copy.notes.eyebrow}</p>
        <h2>{copy.notes.title}</h2>
        <p>{copy.notes.body}</p>
        <dl>
          <div><dt>{copy.notes.generated}</dt><dd>{generatedAt}</dd></div>
          <div><dt>{copy.notes.ranking}</dt><dd>{formatDate(data.scope.rankingSnapshotDate, language)}</dd></div>
          <div><dt>{copy.notes.quality}</dt><dd>{copy.notes.passed(data.summary.evaluationChecksPassed, data.summary.evaluationChecksTotal)}</dd></div>
        </dl>
      </section>

      <section className={styles.disclaimerGrid}>
        {data.disclaimers.map((item, index) => (
          <article key={item.code}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <h3>{language === "en" ? item.titleEn : item.titleCn}</h3>
            <p>{language === "en" ? item.bodyEn : item.bodyCn}</p>
          </article>
        ))}
      </section>

      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <div>
            <p className={styles.sectionEyebrow}>{copy.notes.validationEyebrow}</p>
            <h3>{copy.notes.validationTitle}</h3>
          </div>
          <span className={styles.validBadge}>{data.evaluation.summary.allTasksSucceeded ? copy.notes.allPassed : copy.notes.issuesFound}</span>
        </div>
        <div className={styles.performanceTable}>
          <div className={styles.performanceHead}>
            <span>{copy.notes.researchTask}</span><span>{copy.notes.resultRows}</span><span>{copy.notes.medianTime}</span><span>{copy.notes.p95}</span><span>{copy.notes.stable}</span>
          </div>
          {data.evaluation.performance.map((row) => (
            <div className={styles.performanceRow} key={row.taskId}>
              <span><strong>{row.taskId}</strong>{copy.notes.taskTitles[row.taskId as keyof typeof copy.notes.taskTitles] ?? row.taskId}</span>
              <span>{row.resultRows}</span>
              <span>{row.medianClientMs.toFixed(1)} ms</span>
              <span>{row.p95ClientMs.toFixed(1)} ms</span>
              <span className={row.resultHashStable ? styles.pass : styles.fail}>{row.resultHashStable ? copy.notes.pass : copy.notes.issue}</span>
            </div>
          ))}
        </div>
        <p className={styles.caption}>{copy.notes.performanceCaption}</p>
      </section>
    </div>
  );
}

function EventDrawer({
  detail,
  company,
  sources,
  market,
  onClose,
  language,
}: {
  detail: Detail;
  company?: Company;
  sources: Source[];
  market: MarketObservation[];
  onClose: () => void;
  language: Language;
}) {
  const copy = COPY[language];
  const orderedMarket = [...market].sort((a, b) => {
    const directionOrder = Number(a.windowDays > 0) - Number(b.windowDays > 0);
    return directionOrder || Math.abs(a.windowDays) - Math.abs(b.windowDays);
  });
  const marketGroups = [
    {
      key: "before",
      title: copy.drawer.beforePublication,
      rows: orderedMarket.filter((row) => row.windowDays < 0),
    },
    {
      key: "after",
      title: copy.drawer.afterPublication,
      rows: orderedMarket.filter((row) => row.windowDays > 0),
    },
  ].filter((group) => group.rows.length > 0);
  const orderedSources = [...sources].sort(
    (a, b) => Number(b.isRelationshipSource) - Number(a.isRelationshipSource) || Number(b.isRepresentative) - Number(a.isRepresentative),
  );
  const { event, impact } = detail;

  return (
    <div className={styles.drawerBackdrop} onMouseDown={(event) => event.currentTarget === event.target && onClose()}>
      <aside className={styles.drawer} role="dialog" aria-modal="true" aria-labelledby="event-detail-title">
        <header className={styles.drawerHeader}>
          <div>
            <p>{company?.name ?? copy.drawer.companyEvent} · {formatDate(event.date, language)}</p>
            <span className={`${styles.eventPill} ${event.type === "regulatory_event" ? styles.alertPill : ""}`}>
              {EVENT_LABELS[language][event.type] ?? copy.otherEvent}
            </span>
          </div>
          <button type="button" onClick={onClose} aria-label={copy.drawer.close}>×</button>
        </header>

        <div className={styles.drawerBody}>
          <section>
            <p className={styles.sectionEyebrow}>{copy.drawer.extractedEyebrow}</p>
            <h2 id="event-detail-title">{event.title}</h2>
            <div className={styles.confidenceLine}>
              <span>{RELATION_LABELS[language][impact.nlpRelationshipLabel] ?? copy.semanticLink}</span>
              <span>{copy.drawer.textMatch} <strong>{Math.round(impact.nlpPositiveProbability * 100)}%</strong></span>
              <span>{copy.drawer.focusScore} <strong>{impact.relationshipFocusScore}/10</strong></span>
            </div>
            <p className={styles.modelNote}>{copy.drawer.modelNote}</p>
          </section>

          <section>
            <p className={styles.sectionEyebrow}>{copy.drawer.pathEyebrow}</p>
            <h3>{copy.drawer.pathTitle}</h3>
            <EvidencePath
              labels={[copy.drawer.sourceNode, copy.drawer.evidenceNode, copy.drawer.eventNode, copy.drawer.companyNode]}
              details={[
                orderedSources[0]?.articleTitle ?? copy.drawer.relationshipSource,
                impact.evidenceSentence,
                event.title,
                company?.name ?? copy.drawer.companyEvent,
              ]}
            />
          </section>

          <section className={styles.evidenceBlock}>
            <p className={styles.sectionEyebrow}>{copy.drawer.evidenceEyebrow}</p>
            <blockquote>“{impact.evidenceSentence}”</blockquote>
          </section>

          <section>
            <div className={styles.drawerSectionTitle}>
              <div><p className={styles.sectionEyebrow}>{copy.drawer.marketEyebrow}</p><h3>{copy.drawer.tradingDays}</h3></div>
              <span>{copy.drawer.descriptive}</span>
            </div>
            {orderedMarket.length > 0 ? (
              <>
                <MarketReturnChart rows={orderedMarket} language={language} />
                <div className={styles.marketPeriods}>
                  {marketGroups.map((group) => (
                    <div className={styles.marketPeriod} key={group.key}>
                      <h4>{group.title}</h4>
                      <div className={styles.marketGrid}>
                        {group.rows.map((row) => (
                          <article key={row.windowDays} className={row.cumulativeReturn >= 0 ? styles.marketUp : styles.marketDown}>
                            <span>{copy.drawer.day(row.windowDays)}</span>
                            <strong>{formatReturn(row.cumulativeReturn)}</strong>
                            <small>{formatDate(row.baselineDate, language)} → {formatDate(row.windowEndDate, language)}</small>
                          </article>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className={styles.mutedBox}>{copy.drawer.noMarket}</p>
            )}
            <p className={styles.causalWarning}><span aria-hidden="true">i</span> {copy.drawer.causalWarning}</p>
          </section>

          <section>
            <div className={styles.drawerSectionTitle}>
              <div><p className={styles.sectionEyebrow}>{copy.drawer.sourcesEyebrow}</p><h3>{copy.drawer.articleCount(orderedSources.length || 1)}</h3></div>
            </div>
            <div className={styles.sourceList}>
              {orderedSources.length ? orderedSources.map((source, index) => (
                <article key={`${source.url}-${index}`}>
                  <span>{formatSection(source.sectionName || "News", language)} · {formatDate(source.publicationTimestamp, language)}</span>
                  <h4>{source.articleTitle}</h4>
                  <p>{source.evidenceSentence}</p>
                  <a href={source.url} target="_blank" rel="noreferrer">{copy.drawer.verify} ↗</a>
                </article>
              )) : (
                <article>
                  <h4>{copy.drawer.relationshipSource}</h4>
                  <p>{impact.evidenceSentence}</p>
                  <a href={impact.sourceUrl} target="_blank" rel="noreferrer">{copy.drawer.verify} ↗</a>
                </article>
              )}
            </div>
          </section>
        </div>
      </aside>
    </div>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className={styles.emptyState}>
      <span aria-hidden="true">—</span>
      <strong>{title}</strong>
      <p>{body}</p>
    </div>
  );
}
