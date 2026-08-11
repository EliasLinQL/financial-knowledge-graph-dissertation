# 金融事件情报工作台 / Financial Event Intelligence Dashboard

## 中文说明

### 用途

这是面向金融分析师的只读前端，将知识图谱、新闻事件、证据来源、事件后市场背景和 GDS 共同事件分析转换为可浏览的工作流。界面用于快速筛选公司、追溯事件依据并发现值得进一步研究的公司组合；它不是交易系统，也不提供投资建议。

### 界面视图

界面首次打开时使用英文。右上角的 `EN / 中文` 按钮可即时切换语言，
无需重新加载页面；日期、数值、解释文字和无障碍标签会一起切换。

- **总览**：查看当前研究范围、关键数量和新闻覆盖情况。
- **公司研究**：搜索或选择公司，通过月度事件图和时间线查看事件、证据、来源和发布前后的价格变化。
- **知识图谱**：气泡显示股票代码、公司名和事件数，事件越多，气泡越大；点击后只保留该公司的连线，并把下方事件查询限定为该公司。实线表示达到所选共同事件数量，浅色虚线表示数量较少但仍保留的关系。文章查询会用当前公司和事件制作示例，解释“与所选事件直接相关”和“在同篇报道中，但关联另一个事件”的区别。
- **事件查询**：按事件类型和公司关联分筛选线索，并打开证据句核实。
- **公司比较**：通过 25×25 热力图和明细表查看共同事件数量和占比。
- **数据说明**：了解数据范围、质量检查、方法限制和指标含义。

事件详情还会展示“原始文章 → 证据句 → 独立事件 → 关联公司”的核查路径，以及报道发布前后 1、3、7 个交易日的价格变化。网络连线不代表商业关系，价格变化也不代表事件造成了涨跌。

技术性标识符和 Neo4j 标签不是主要交互对象；用户应从公司、事件、证据和来源进入分析。

### 静态快照数据契约

浏览器只读取 `public/data/dashboard.json`。该文件由仓库根目录下的 `src/build_frontend_snapshot.py` 生成，主要字段为：

- `scope`：快照指纹、生成时间、事件日期范围、新闻来源和排名快照日期；
- `summary`：公司、事件、事件—公司关系、来源文章、市场窗口和评估检查数量；
- `companies`：公司名称、代码、排名、市值及事件数量；
- `events`：去重后的规范事件；
- `impacts`：事件与公司的潜在关联、证据句、NLP 标签及概率；
- `sources`：原始文章、发布日期、URL、证据句和代表性来源标记；
- `market`：事件后市场观察窗口，且 `causalClaim` 固定为 `false`；
- `visualizations`：经构建器校验后生成的 12 个月公司事件序列和共同事件热力图矩阵；
- `network`：公司共同事件边、相似度、中心性、连通分量和社区结果；
- `evaluation`：分析师用例、质量检查和本地性能评估结果；
- `disclaimers`：覆盖范围、非因果解释及其他使用边界。

生成器会校验 GDS 与应用场景评估清单中的 SHA-256、只读约束以及跨文件数量一致性。任一校验失败时不会生成新快照。

### 研究助手（试用版）

研究助手用于解释页面上已有的结果，不会增加新的分析步骤。默认无需 API Key：它会以“已核验数据预览”模式，根据当前 `dashboard.json` 给出简短说明，便于先查看交互效果。

如需启用 OpenAI 生成的自然语言解释，请在 `frontend` 目录创建仅供本机使用的配置文件：

```powershell
Copy-Item .dev.vars.example .dev.vars
```

然后在 `.dev.vars` 中填写 `OPENAI_API_KEY`；可用 `OPENAI_MODEL` 更换模型。密钥只由服务端读取，切勿使用 `NEXT_PUBLIC_`、`VITE_` 等会暴露到浏览器的变量名，也不要提交 `.dev.vars`。

助手只读取已通过校验的静态快照，并通过四个只读工具解释：快照概况、公司、事件和公司连接。它不会连接实时 Neo4j、执行任意 Cypher，也不会给出投资建议或把价格变化解释为事件的因果结果。

可尝试提问：

- “这份快照覆盖了什么？”
- “为什么 Alphabet 和 Microsoft 有连线？”
- “请解释台积电最近的一条事件及其证据。”
- “这个事件前后 1、3、7 个交易日的价格背景是什么？”

完成上述配置后，按下方方式运行前端；不配置密钥也可直接使用预览模式。

### 本地运行

要求 Node.js `>=22.13.0`，并使用已配置好项目依赖的 Python 环境。

从仓库根目录生成或刷新快照：

```powershell
python src/build_frontend_snapshot.py
```

然后启动前端：

```powershell
cd frontend
npm install
npm run dev
```

如果本机访问 OpenAI 必须经过 HTTP(S) 代理，请停止当前开发服务器，并从仓库根目录使用启动脚本传入代理地址：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_frontend_dashboard.ps1 -SkipSnapshot -ProxyUrl "http://127.0.0.1:7890"
```

该参数会在本地开发服务器中启用一条固定的 OpenAI 转发路径，明确通过所给代理访问 Responses API。代理地址和 API Key 都不会发送到浏览器，也无需把代理地址写入 `.dev.vars`。修改代理或 Key 后请完整停止并重新启动开发服务器。

按终端显示的本地地址访问页面。发布或提交前执行构建检查：

```powershell
npm run build
```

### 刷新数据

1. 先运行现有数据管线，使分析师报告、GDS 分析和应用场景评估结果保持最新。
2. 确认以下源产物及其清单存在且匹配：
   - `outputs/analyst_report/analyst_report_data.json`
   - `outputs/gds_analysis/gds_manifest.json` 及对应表格
   - `outputs/analyst_use_case_evaluation/analyst_use_case_manifest.json` 及对应表格
3. 在仓库根目录重新运行 `python src/build_frontend_snapshot.py`。
4. 重新运行 `npm run dev` 查看结果，或运行 `npm run build` 生成可部署版本。

不要手工修改 `dashboard.json`；应修正上游数据并重新生成，以保留完整性检查和可复现性。

### 解读限制与安全边界

- 结果只覆盖选定公司、研究时间窗和 The Guardian 新闻语料，不代表全球新闻总体。低事件数可能表示来源覆盖较少，不能解释为公司没有相关事件。
- 事件—公司关系表示系统识别的潜在相关性。NLP 概率是模型对关系标签的置信程度，不是真实性、风险或价格影响的概率。
- 发布前后 1、3、7 个交易日收益仅用于展示价格背景，不能用于推断新闻造成了价格变化。
- 共同事件、相似度、中心性和社区结果依赖语料与图投影定义，不等同于真实商业关系或系统重要性。
- 证据与来源完整性是图谱内部质量指标，不等同于针对外部现实的精确率或召回率。
- 浏览器不连接 Neo4j，也不接收 Neo4j URI、用户名或密码。数据库访问和快照生成只发生在受控的本地数据处理阶段。

---

## English

The public repository includes a compact synthetic `public/data/dashboard.json`
so that the interface and research-assistant controls can be built and tested
without redistributing licensed article text. Running the repository-root
snapshot builder replaces this demonstration data with the validated research
snapshot available to authorised users.

### Purpose

This read-only interface turns the knowledge graph, news events, provenance, post-event market context, and GDS co-event analysis into an analyst-friendly workflow. It supports company screening, evidence tracing, and discovery of company pairs for further research. It is not a trading system and does not provide investment advice.

### Interface views

English is the default on first load. Use the `EN / 中文` control in the
top-right corner to switch the entire interface immediately, including dates,
numbers, explanations and accessibility labels.

- **Overview**: review the current research scope, headline counts and news coverage.
- **Company research**: search or select a company, then use the monthly chart and timeline to check events, evidence, sources and price changes before and after publication.
- **Knowledge graph**: bubbles show ticker symbols, company names and event counts; more events produce a larger bubble. Selecting one keeps only that company’s lines and limits the query below to that company. Solid lines meet the selected shared-event count; lighter dashed lines show smaller counts that remain in the data. The article query uses the current company and event as an example to explain the difference between a direct link to the selected event and a link to another event in the same article.
- **Event search**: filter leads by event type and company link score, with source evidence one click away.
- **Company comparison**: use a 25×25 heatmap and detailed table to compare shared-event counts and rates.
- **About the data**: understand scope, quality checks, methodological limits and metric definitions.

Event details also show the path from original article to evidence sentence, unique event and linked company, plus price changes for 1, 3 and 7 trading days before and after publication. Network lines do not prove a business relationship, and price changes do not prove that an event caused a move.

Technical identifiers and Neo4j labels are secondary; the main workflow starts from companies, events, evidence, and sources.

### Static snapshot data contract

The browser reads only `public/data/dashboard.json`. The repository-root script `src/build_frontend_snapshot.py` creates this file with these top-level fields:

- `scope`: snapshot fingerprint, generation timestamps, event date range, publisher, and ranking snapshot date;
- `summary`: counts for companies, events, event-company links, source articles, market windows, and evaluation checks;
- `companies`: company identity, symbol, rank, market capitalisation, and event count;
- `events`: deduplicated canonical events;
- `impacts`: potential event-company links, evidence, NLP label, and probability;
- `sources`: source articles, timestamps, URLs, evidence, and representative-source flags;
- `market`: post-event market windows with `causalClaim` always set to `false`;
- `visualizations`: builder-verified 12-month company series and the shared-event heatmap matrix;
- `network`: co-event edges, similarity, centrality, connected components, and communities;
- `evaluation`: analyst use cases, quality checks, and local performance results;
- `disclaimers`: corpus coverage, non-causal interpretation, and other usage boundaries.

The builder verifies manifest SHA-256 hashes, read-only contracts, and cross-artifact counts for the GDS and use-case evaluation packages. It refuses to write a new snapshot when validation fails.

### Research Assistant (preview)

The Research Assistant explains results already shown in the dashboard; it does not add another analysis workflow. No API key is required by default. In this checked-data preview mode, it produces short explanations from the current `dashboard.json` so you can review the interaction first.

To enable OpenAI-generated wording, create a local-only configuration file inside `frontend`:

```powershell
Copy-Item .dev.vars.example .dev.vars
```

Add `OPENAI_API_KEY` to `.dev.vars`. You can change the model with `OPENAI_MODEL`. The key is read only on the server: never use a `NEXT_PUBLIC_` or `VITE_` variable, and never commit `.dev.vars`.

The assistant uses only the validated snapshot through four read-only tools: snapshot, company, event, and connection. It does not query live Neo4j, accept arbitrary Cypher, provide investment advice, or describe market moves as caused by an event.

Suggested questions:

- “What does this snapshot cover?”
- “Why are Alphabet and Microsoft connected?”
- “Explain a recent TSMC event and its evidence.”
- “What was the 1-, 3-, and 7-day market context before and after this event?”

After optional configuration, run the frontend as described below. Preview mode works without a key.

### Run locally

Use Node.js `>=22.13.0` and the Python environment configured for the project.

From the repository root, generate or refresh the snapshot:

```powershell
python src/build_frontend_snapshot.py
```

Then start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

If OpenAI must be reached through an HTTP(S) proxy, stop the current development server and run the launcher from the repository root with the proxy URL:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_frontend_dashboard.ps1 -SkipSnapshot -ProxyUrl "http://127.0.0.1:7890"
```

This option enables a fixed OpenAI relay inside the local development server, ensuring that Responses API requests use the supplied proxy. Neither the proxy URL nor the API key is sent to the browser, and the proxy URL does not belong in `.dev.vars`. Fully stop and restart the development server after changing the proxy or key.

Open the local URL printed by the development server. Before release or hand-off, verify the production build:

```powershell
npm run build
```

### Refresh workflow

1. Run the existing data pipeline so the analyst report, GDS analysis, and analyst use-case evaluation are current.
2. Confirm that these source artifacts and their referenced tables exist and match their manifests:
   - `outputs/analyst_report/analyst_report_data.json`
   - `outputs/gds_analysis/gds_manifest.json`
   - `outputs/analyst_use_case_evaluation/analyst_use_case_manifest.json`
3. Run `python src/build_frontend_snapshot.py` again from the repository root.
4. Use `npm run dev` to review the refreshed data, or `npm run build` to create a deployable build.

Do not edit `dashboard.json` by hand. Correct upstream data and rebuild so integrity validation and reproducibility are preserved.

### Interpretation and security boundaries

- Results cover the selected companies, study window, and The Guardian corpus only; they do not represent the global news universe. A low event count may indicate limited source coverage, not the absence of relevant events.
- An event-company link is a potential relationship identified by the system. NLP probability is model confidence in the relationship label, not the probability that an event is true, risky, or price-moving.
- The 1-, 3-, and 7-trading-day returns before and after publication are descriptive context and cannot establish that the news caused a price change.
- Shared events, similarity, centrality, and communities depend on corpus and graph-projection choices; they are not equivalent to business relationships or systemic importance.
- Evidence and provenance completeness are internal graph-quality measures, not external precision or recall.
- The browser never connects to Neo4j and receives no Neo4j URI, username, or password. Database access and snapshot construction remain in the controlled local data-processing stage.
