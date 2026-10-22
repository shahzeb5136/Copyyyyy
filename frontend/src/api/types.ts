// Hand-written aliases over the generated OpenAPI types (src/api/types.gen.ts, produced by `npm run gen:types`
// from the back end's OpenAPI document). Nothing here is typed by hand - the back end is the source of truth.
import type { components } from "./types.gen";

export type Schemas = components["schemas"];
export type DatasetInfo = Schemas["DatasetInfo"];
export type SemanticModel = Schemas["SemanticModel"];
export type ColumnSemantics = Schemas["ColumnSemantics"];
export type DashboardSpec = Schemas["DashboardSpec"];
export type Widget = Schemas["Widget"];
export type DashboardResult = Schemas["DashboardResult"];
export type WidgetResult = Schemas["WidgetResult"];
export type DashboardPayload = Schemas["DashboardPayload"];
export type ReplayResponse = Schemas["ReplayResponse"];
export type CompatibilityReport = Schemas["CompatibilityReport"];
export type JobRecord = Schemas["JobRecord"];
export type JobSubmitted = Schemas["JobSubmitted"];
export type InferResult = Schemas["InferResult"];
export type AskResult = Schemas["AskResult"];
export type ChatTurn = Schemas["ChatTurn"];
export type AskHistoryItem = Schemas["AskHistoryItem"];

export const DATA_TYPES = ["number", "integer", "date", "datetime", "boolean", "text"] as const;
export const ROLES = ["measure", "dimension", "time", "identifier", "attribute", "ignore"] as const;
export const AGGREGATIONS = ["sum", "mean", "median", "min", "max", "count", "count_distinct", "first", "last"] as const;
