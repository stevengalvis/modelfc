import { decodeResearchPerformance, defaultResearchFilters, type ResearchFilters } from "./research-performance";
import type { ShortlistRanking } from "./types";
import { decodeTrackRecord, emptyTrackRecord, decodeStrategyPage, assertPublicationSelection, publicationSelection, type PublicationFilters } from "./track-record";
import { decodeShortlist } from "./shortlist";
import { decodeBttsPerformance, emptyBttsPerformance, type BttsReportingScope } from "./btts-performance";
import { decodeRecordedOffers } from "./recorded-offers";
import { mockRecordedOffers } from "./mock-recorded-offers";
import { decodeRecommendations } from "./recommendations";
import { mockRecommendations } from "./mock-recommendations";
import { decodeBttsResearch, isBttsCompetition } from "./btts-research";
import { mockBttsResearch } from "./mock-btts-research";
import { decodeTeams, decodeTeamProfile, decodeTeamInsights, isTeamId } from "./team-intelligence";
import { isApiErrorBody, ModelFCApiError } from "./errors";
import { mockAnalyze, mockCapabilities, mockPredictions, mockOpportunities, mockOpportunityDetails, mockPerformance } from "./mock";
import { decodePredictions, decodeOpportunities, decodeOpportunityDetail, decodePerformance } from "./prospective";
import { decodeCapabilities } from "./capabilities";
import type { AnalysisRequest, AnalysisResponse, BttsCompetition, BttsResearchComparison, CapabilitiesResponse, ProspectivePrediction, ProspectiveOpportunity, OpportunityDetail, ProspectivePerformance, Recommendation } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_MODELFC_API_URL;
// A deployment must opt into mock mode explicitly. Missing configuration is
// surfaced as an API_CONFIGURATION_ERROR instead of masquerading as a demo.
export const apiMode = process.env.NEXT_PUBLIC_MODELFC_API_MODE ?? "";

async function requestJson<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    ...init,
    headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (isApiErrorBody(body)) {
      throw new ModelFCApiError(body.error.message, body.error.code, body.error.retryable, response.status);
    }
    throw new ModelFCApiError("Zeno FC API request failed.", "NETWORK_ERROR", response.status >= 500, response.status);
  }
  return body as T;
}

function decodeAnalysisResponse(value: unknown, request: AnalysisRequest): AnalysisResponse {
  const record = (item: unknown): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item));
  const stringOrNull = (item: unknown): item is string | null => item === null || typeof item === "string";
  const finiteNumber = (item: unknown): item is number => typeof item === "number" && Number.isFinite(item);
  const probability = (item: unknown): item is number => finiteNumber(item) && item >= 0 && item <= 1;
  const warning = (item: unknown) => record(item)
    && typeof item.code === "string"
    && typeof item.message === "string";
  const warnings = (item: unknown) => Array.isArray(item) && item.every(warning);
  const configuration = (item: unknown) => record(item)
    && Object.values(item).every((entry) => entry === null || typeof entry === "string" || finiteNumber(entry));
  const sourceHash = (item: unknown) => record(item)
    && typeof item.filename === "string"
    && typeof item.sha256 === "string";
  const marketResult = (item: Record<string, unknown>) => {
    if (item.status === "SUPPORTED") {
      return item.unsupported_reason === null
        && probability(item.model_probability)
        && probability(item.push_probability)
        && probability(item.decisive_model_probability)
        && probability(item.implied_probability)
        && finiteNumber(item.probability_edge)
        && finiteNumber(item.expected_profit)
        && finiteNumber(item.expected_corners);
    }
    if (item.status !== "UNSUPPORTED"
      || typeof item.unsupported_reason !== "string"
      || !item.unsupported_reason) return false;
    if (item.unsupported_reason === "NO_DECISIVE_OUTCOMES") {
      return probability(item.model_probability)
        && probability(item.push_probability)
        && item.decisive_model_probability === null
        && item.implied_probability === null
        && item.probability_edge === null
        && item.expected_profit === null
        && finiteNumber(item.expected_corners);
    }
    return item.model_probability === null
      && item.push_probability === null
      && item.decisive_model_probability === null
      && item.implied_probability === null
      && item.probability_edge === null
      && item.expected_profit === null
      && item.expected_corners === null;
  };
  const market = (item: unknown) => record(item)
    && typeof item.client_market_id === "string"
    && (item.market_type === "TEAM_TOTAL" || item.market_type === "MATCH_TOTAL")
    && (item.team_side === null || item.team_side === "HOME" || item.team_side === "AWAY")
    && (item.side === "OVER" || item.side === "UNDER")
    && finiteNumber(item.line)
    && Number.isSafeInteger(item.american_odds)
    && stringOrNull(item.team)
    && marketResult(item)
    && warnings(item.warnings);
  const requestMarketMatches = (item: Record<string, unknown>, expected: AnalysisRequest["markets"][number]) =>
    item.client_market_id === expected.client_market_id
    && item.market_type === expected.market_type
    && item.team_side === expected.team_side
    && item.side === expected.side
    && item.line === expected.line
    && item.american_odds === expected.american_odds;
  const requestFixtureMatches = (item: Record<string, unknown>, expected: AnalysisRequest["fixture"]) =>
    item.competition === expected.competition
    && item.date === expected.date
    && item.home_team === expected.home_team
    && item.away_team === expected.away_team;
  const marketTeamMatchesFixture = (item: Record<string, unknown>, fixture: unknown) => {
    if (item.market_type !== "TEAM_TOTAL") return true;
    if (!record(fixture)) return false;
    const expectedTeam = item.team_side === "HOME"
      ? fixture.home_team
      : item.team_side === "AWAY" ? fixture.away_team : null;
    if (typeof expectedTeam !== "string") return false;
    if (item.status === "SUPPORTED") return item.team === expectedTeam;
    return item.team === null || item.team === expectedTeam;
  };
  const valid = record(value)
    && typeof value.analysis_id === "string"
    && typeof value.forecast_id === "string"
    && typeof value.created_at === "string"
    && record(value.pick_logging)
    && (value.pick_logging.status === "SUPPORTED" || value.pick_logging.status === "DISABLED")
    && stringOrNull(value.pick_logging.reason)
    && record(value.fixture)
    && typeof value.fixture.competition === "string"
    && typeof value.fixture.date === "string"
    && typeof value.fixture.home_team === "string"
    && typeof value.fixture.away_team === "string"
    && stringOrNull(value.fixture.kickoff_at)
    && (value.pick_logging.status !== "SUPPORTED" || typeof value.fixture.kickoff_at === "string")
    && requestFixtureMatches(value.fixture, request.fixture)
    && record(value.forecast)
    && typeof value.forecast.model === "string"
    && value.forecast.model === request.model
    && typeof value.forecast.model_version === "string"
    && configuration(value.forecast.configuration)
    && finiteNumber(value.forecast.home_expected_corners)
    && finiteNumber(value.forecast.away_expected_corners)
    && finiteNumber(value.forecast.match_expected_corners)
    && typeof value.forecast.latest_history_date === "string"
    && Array.isArray(value.forecast.source_data_hashes)
    && value.forecast.source_data_hashes.every(sourceHash)
    && Array.isArray(value.markets)
    && value.markets.every(market)
    && value.markets.length === request.markets.length
    && value.markets.every((item, index) => record(item)
      && requestMarketMatches(item, request.markets[index])
      && marketTeamMatchesFixture(item, value.fixture))
    && warnings(value.warnings);
  if (!valid) {
    throw new ModelFCApiError(
      "The API returned an analysis response outside the V1 contract.",
      "ANALYSIS_CONTRACT_MISMATCH", false,
    );
  }
  return value as unknown as AnalysisResponse;
}

export function createApiClient(
  mode: string | undefined, baseUrl?: string, { allowLiveAnalysis = false }: { allowLiveAnalysis?: boolean } = {},
) {
  function checkConfiguration() {
    if (!mode || !["live", "mock"].includes(mode) || (mode === "live" && !baseUrl?.trim())) {
      throw new ModelFCApiError(
        "Set API mode to mock or live. Live mode also requires NEXT_PUBLIC_MODELFC_API_URL.",
        "API_CONFIGURATION_ERROR", false,
      );
    }
  }
  return {
    async teams(signal?: AbortSignal) {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodeTeams(mode === "mock" ? structuredClone((await import("../../../tests/fixtures/team_intelligence/teams.json")).default) : await requestJson<unknown>(baseUrl!, "/teams", { signal }));
    },
    async teamProfile(id: string, signal?: AbortSignal) {
      checkConfiguration();
      signal?.throwIfAborted();
      if (!isTeamId(id)) throw new ModelFCApiError("Team was not found.", "TEAM_NOT_FOUND", false, 404);
      const mockProfiles = mode === "mock" ? (await import("../../../tests/fixtures/team_intelligence/profiles.json")).default : null;
      if (mockProfiles && !Object.hasOwn(mockProfiles, id)) throw new ModelFCApiError("Team profile is not included in the mock dataset.", "TEAM_NOT_FOUND", false, 404);
      return decodeTeamProfile(mockProfiles ? structuredClone(mockProfiles[id as keyof typeof mockProfiles]) : await requestJson<unknown>(baseUrl!, `/teams/${id}`, { signal }), id);
    },
    async teamInsights(signal?: AbortSignal) {
      checkConfiguration();
      const mockTeams = mode === "mock" ? (await import("../../../tests/fixtures/team_intelligence/teams.json")).default : null;
      return decodeTeamInsights(mockTeams ? { metadata: structuredClone(mockTeams.metadata), insights: structuredClone(mockTeams.insights) } : await requestJson<unknown>(baseUrl!, "/team-insights", { signal }));
    },
    async capabilities(signal?: AbortSignal): Promise<CapabilitiesResponse> {
      checkConfiguration();
      const value = mode === "mock" ? structuredClone(mockCapabilities)
        : await requestJson<unknown>(baseUrl!, "/capabilities", { signal });
      return decodeCapabilities(value);
    },
    async shortlist(limit = 5, signal?: AbortSignal, ranking: ShortlistRanking = "best_value") {
      checkConfiguration();
      signal?.throwIfAborted();
      let offers = mockRecordedOffers.fixtures.flatMap(f => f.offers.map(o => ({ ...o, fixture_id: f.fixture_id })));
      if (ranking === "higher_probability") offers = offers.filter(o => o.model_probability !== null && o.model_probability >= 0.65)
        .sort((a, b) => b.model_probability! - a.model_probability! || b.expected_profit! - a.expected_profit!);
      return decodeShortlist(mode === "mock" ? structuredClone({ as_of_utc: mockRecordedOffers.as_of_utc,
        offers: offers.slice(0, limit), total: offers.length, limit })
        : await requestJson<unknown>(baseUrl!, `/recommendations?view=shortlist&limit=${limit}&ranking=${ranking}`, { signal, cache: "no-store" }), ranking);
    },
    async alternatives(fixtureId: string, signal?: AbortSignal) {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodeRecordedOffers(mode === "mock" ? structuredClone({ ...mockRecordedOffers,
        fixtures: mockRecordedOffers.fixtures.filter(f => f.fixture_id === fixtureId) })
        : await requestJson<unknown>(baseUrl!, `/recommendations?view=alternatives&fixture_id=${encodeURIComponent(fixtureId)}`, { signal, cache: "no-store" }));
    },
    async recordedOffers(signal?: AbortSignal) {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodeRecordedOffers(mode === "mock" ? structuredClone(mockRecordedOffers)
        : await requestJson<unknown>(baseUrl!, "/recommendations?view=recorded", { signal, cache: "no-store" }));
    },
    async recommendations(signal?: AbortSignal): Promise<Recommendation[]> {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodeRecommendations(mode === "mock" ? structuredClone(mockRecommendations)
        : await requestJson<unknown>(baseUrl!, "/recommendations", { signal, cache: "no-store" }));
    },
    async bttsResearch(competition: BttsCompetition, signal?: AbortSignal): Promise<BttsResearchComparison[]> {
      checkConfiguration();
      signal?.throwIfAborted();
      if (!isBttsCompetition(competition)) {
        throw new ModelFCApiError("BTTS research competition is not supported.",
          "UNSUPPORTED_COMPETITION", false, 422);
      }
      const value = mode === "mock" ? structuredClone(mockBttsResearch)
        : await requestJson<unknown>(baseUrl!, `/research/btts?competition=${encodeURIComponent(competition)}`,
          { signal, cache: "no-store" });
      return decodeBttsResearch(value, competition);
    },
    async researchPerformance(signal?: AbortSignal, filters: ResearchFilters = defaultResearchFilters) {
      checkConfiguration(); signal?.throwIfAborted();
      if (mode === "mock") throw new ModelFCApiError("Saved research evidence requires a live API.", "RESEARCH_UNAVAILABLE", false);
      if (filters.asOf && (!/(?:Z|[+-]\d\d:\d\d)$/.test(filters.asOf) || !Number.isFinite(Date.parse(filters.asOf)) || Date.parse(filters.asOf)>Date.now()))
        throw new ModelFCApiError("Enter a cutoff with timezone no later than now.", "INVALID_AS_OF", false);
      const query = new URLSearchParams({view:"research", competition:filters.competition, market:filters.market, cohort:filters.cohort});
      if (filters.asOf) query.set("as_of",filters.asOf);
      return decodeResearchPerformance(await requestJson<unknown>(baseUrl!, `/prospective/performance?${query}`, { signal, cache:"no-store" }),filters);
    },
    async bttsPerformance(signal?: AbortSignal, filters: { competition?: BttsReportingScope; asOf?: string } = {}) {
      checkConfiguration();
      signal?.throwIfAborted();
      const competition = filters.competition ?? "E1";
      const params = new URLSearchParams();
      if (filters.competition) params.set("competition", competition);
      if (filters.asOf) params.set("as_of", filters.asOf);
      const query = params.size ? `?${params}` : "";
      const value = decodeBttsPerformance(mode === "mock" ? {...structuredClone(emptyBttsPerformance),
        competition, as_of_utc: filters.asOf || new Date().toISOString(),
        coverage: competition === "E0" ? "UNSUPPORTED" : competition === "ALL" ? "PARTIAL" : "SUPPORTED"}
        : await requestJson<unknown>(baseUrl!, `/research/btts/performance${query}`, { signal, cache: "no-store" }));
      if (value.competition !== competition || (filters.asOf && Date.parse(value.as_of_utc) !== Date.parse(filters.asOf))) {
        throw new ModelFCApiError("BTTS report does not match the requested filters.", "INVALID_RESPONSE", false, 502);
      }
      return value;
    },
    async predictions(signal?: AbortSignal): Promise<ProspectivePrediction[]> {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodePredictions(mode === "mock" ? structuredClone(mockPredictions)
        : await requestJson<unknown>(baseUrl!, "/predictions", { signal }));
    },
    async opportunities(signal?: AbortSignal): Promise<ProspectiveOpportunity[]> {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodeOpportunities(mode === "mock" ? structuredClone(mockOpportunities)
        : await requestJson<unknown>(baseUrl!, "/opportunities", { signal }));
    },
    async opportunityDetail(id: string, signal?: AbortSignal): Promise<OpportunityDetail> {
      checkConfiguration();
      signal?.throwIfAborted();
      if (!/^[0-9a-f]{32}$/.test(id) && !(mode === "mock" && /^demo-offer-[a-z]+$/.test(id))) {
        throw new ModelFCApiError("Opportunity was not found.", "OPPORTUNITY_NOT_FOUND", false, 404);
      }
      if (mode === "mock") {
        const match = mockOpportunityDetails.find((item) => item.opportunity_id === id);
        if (!match) throw new ModelFCApiError("Opportunity was not found.", "OPPORTUNITY_NOT_FOUND", false, 404);
        return decodeOpportunityDetail(structuredClone(match), id);
      }
      return decodeOpportunityDetail(await requestJson<unknown>(baseUrl!, `/opportunities/${id}`, { signal }), id);
    },
    async performance(signal?: AbortSignal): Promise<ProspectivePerformance> {
      checkConfiguration();
      signal?.throwIfAborted();
      return decodePerformance(mode === "mock" ? structuredClone(mockPerformance)
        : await requestJson<unknown>(baseUrl!, "/prospective/performance", { signal }));
    },
    async strategyPicks(query: { strategy: ShortlistRanking; scope: "current" | "history"; market?: string; competition?: string; asOf?: string; offset: number }, signal?: AbortSignal) {
      checkConfiguration();
      signal?.throwIfAborted();
      const params = new URLSearchParams({view: "strategies", strategy: query.strategy, scope: query.scope, offset: String(query.offset), limit: "5"});
      if (query.market) params.set("market", query.market);
      if (query.competition) params.set("competition", query.competition);
      if (query.asOf) params.set("as_of", query.asOf);
      const page = decodeStrategyPage(mode === "mock" ? {
        ...emptyTrackRecord, selection:publicationSelection(query), policy_version:"independent-value-strategies-v1", strategy:query.strategy,
        scope:query.scope, stake_dollars:2, simulated:true, summary_scope:"page", profit_dollars:0,
        totals:emptyTrackRecord.summary, market_totals:[], total_profit_dollars:0,
        total:0,offset:query.offset,limit:5,next_offset:null,
      } : await requestJson<unknown>(baseUrl!, `/prospective/performance?${params}`, {signal,cache:"no-store"}));
      if (page.strategy !== query.strategy || page.scope !== query.scope || page.offset !== query.offset || page.limit !== 5
        || page.publications.some(r => query.market && r.offer.market_type !== query.market || query.competition && r.offer.competition !== query.competition)) {
        throw new ModelFCApiError("Strategy response does not match the request.", "STRATEGY_CONTRACT_MISMATCH", false);
      }
      assertPublicationSelection(page, query);
      return page;
    },
    async trackRecord(signal?: AbortSignal, filters: PublicationFilters = {}) {
      checkConfiguration();
      signal?.throwIfAborted();
      const params = new URLSearchParams({ view: "model_picks" });
      if (filters.asOf) params.set("as_of", filters.asOf);
      if (filters.market) params.set("market", filters.market);
      if (filters.competition) params.set("competition", filters.competition);
      const report = decodeTrackRecord(mode === "mock" ? {...structuredClone(emptyTrackRecord), selection:publicationSelection(filters)}
        : await requestJson<unknown>(baseUrl!, `/prospective/performance?${params}`, { signal, cache: "no-store" }));
      assertPublicationSelection(report, filters);
      return report;
    },
    async analyze(payload: AnalysisRequest, signal?: AbortSignal): Promise<AnalysisResponse> {
      checkConfiguration();
      if (mode === "live" && !allowLiveAnalysis) {
        throw new ModelFCApiError("Interactive analysis is unavailable on this read-only site.",
          "ANALYSIS_UNAVAILABLE", false);
      }
      return mode === "mock" ? mockAnalyze(payload, signal)
        : requestJson<unknown>(baseUrl!, "/analyses", { method: "POST", body: JSON.stringify(payload), signal })
          .then((value) => decodeAnalysisResponse(value, payload));
    },
  };
}

export const api = createApiClient(apiMode, API_BASE);
