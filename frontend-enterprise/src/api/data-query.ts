import { api, TENANT_ID } from './client';

const BASE = '/api/enterprise/data-query';
const tenantParam = () => `tenant_id=${encodeURIComponent(TENANT_ID)}`;

// --- Types ---

export interface DataSource {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  type: string;
  read_only: boolean;
  status: string;
  allowed_tables_json?: string[];
  schema_cache_json?: Record<string, unknown>;
  schema_refreshed_at?: string | null;
  last_test_at: string | null;
  created_at: string;
  updated_at: string;
}


export interface DataSourceCreate {
  name: string;
  description?: string;
  type: string;
  config_json: Record<string, unknown>;
  read_only?: boolean;
  status?: string;
  allowed_tables_json?: string[];
}


export interface DataSourceUpdate {
  name?: string;
  description?: string;
  type?: string;
  config_json?: Record<string, unknown>;
  read_only?: boolean;
  status?: string;
  allowed_tables_json?: string[];
}

export interface TableSummary {
  name: string;
  comment: string;
  row_count_estimate: number;
}

export interface ColumnMeta {
  name: string;
  data_type: string;
  column_type: string;
  is_nullable: boolean;
  comment: string;
}

export interface TablePreviewResult {
  table: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  row_count: number;
}

export interface AdhocTestRequest {
  data_source_id: string;
  query_content: string;
  query_type?: string;
  params?: Record<string, unknown>;
}

export interface QueryTemplateVersion {
  id: string;
  tenant_id: string;
  template_id: string;
  version: number;
  snapshot_json: Record<string, unknown>;
  change_reason: string;
  created_by?: string | null;
  created_at: string;
}


export interface QueryTemplate {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  data_source_id: string;
  query_type: string;
  query_content: string;
  params_json: Array<Record<string, unknown>>;
  output_config_json: Record<string, unknown>;
  cache_ttl: number;
  timeout_seconds: number;
  max_rows: number;
  status: string;
  tool_id?: string | null;
  origin_nl?: string | null;
  business_notes?: string;
  dimensions_json?: string[];
  metrics_json?: string[];
  example_questions_json?: string[];
  evolution_version?: number;
  generated_by?: string;
  created_at: string;
  updated_at: string;
}

export interface QueryTemplateCreate {
  name: string;
  description?: string;
  data_source_id: string;
  query_type?: string;
  query_content?: string;
  params_json?: Array<Record<string, unknown>>;
  output_config_json?: Record<string, unknown>;
  cache_ttl?: number;
  timeout_seconds?: number;
  max_rows?: number;
  status?: string;
  tool_id?: string | null;
  origin_nl?: string | null;
  business_notes?: string;
  dimensions_json?: string[];
  metrics_json?: string[];
  example_questions_json?: string[];
  evolution_version?: number;
  generated_by?: string;
}

export interface QueryTemplateUpdate {
  name?: string;
  description?: string;
  data_source_id?: string;
  query_type?: string;
  query_content?: string;
  params_json?: Array<Record<string, unknown>>;
  output_config_json?: Record<string, unknown>;
  cache_ttl?: number;
  timeout_seconds?: number;
  max_rows?: number;
  status?: string;
  tool_id?: string | null;
  origin_nl?: string | null;
  business_notes?: string;
  dimensions_json?: string[];
  metrics_json?: string[];
  example_questions_json?: string[];
  evolution_version?: number;
  generated_by?: string;
}


export interface QueryExecuteResult {
  template_id: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  row_count: number;
  execution_time_ms: number;
  cached: boolean;
}

// --- Data Sources API ---

export const dataSourcesApi = {
  list: (limit = 100): Promise<DataSource[]> =>
    api.get<DataSource[]>(`${BASE}/data-sources?${tenantParam()}&limit=${limit}`),

  get: (id: string): Promise<DataSource> =>
    api.get<DataSource>(`${BASE}/data-sources/${id}?${tenantParam()}`),

  create: (data: DataSourceCreate): Promise<DataSource> =>
    api.post<DataSource>(`${BASE}/data-sources?${tenantParam()}`, data),

  update: (id: string, data: DataSourceUpdate): Promise<DataSource> =>
    api.put<DataSource>(`${BASE}/data-sources/${id}?${tenantParam()}`, data),

  delete: (id: string): Promise<void> =>
    api.delete(`${BASE}/data-sources/${id}?${tenantParam()}`),

  test: (id: string): Promise<{ success: boolean; message: string }> =>
    api.post(`${BASE}/data-sources/${id}/test?${tenantParam()}`),

  getTables: (id: string): Promise<TableSummary[]> =>
    api.get<TableSummary[]>(`${BASE}/data-sources/${id}/tables?${tenantParam()}`),

  getColumns: (id: string, table: string): Promise<ColumnMeta[]> =>
    api.get<ColumnMeta[]>(`${BASE}/data-sources/${id}/tables/${encodeURIComponent(table)}?${tenantParam()}`),

  previewTable: (id: string, table: string, limit = 20): Promise<TablePreviewResult> =>
    api.get<TablePreviewResult>(`${BASE}/data-sources/${id}/tables/${encodeURIComponent(table)}/preview?${tenantParam()}&limit=${limit}`),

  refreshSchema: (id: string): Promise<Record<string, unknown>> =>
    api.post<Record<string, unknown>>(`${BASE}/data-sources/${id}/schema/refresh?${tenantParam()}`),
};


// --- Query Templates API ---

export const queryTemplatesApi = {
  list: (dataSourceId?: string, limit = 100): Promise<QueryTemplate[]> => {
    const params = [tenantParam(), `limit=${limit}`];
    if (dataSourceId) params.push(`data_source_id=${encodeURIComponent(dataSourceId)}`);
    return api.get<QueryTemplate[]>(`${BASE}/query-templates?${params.join('&')}`);
  },

  get: (id: string): Promise<QueryTemplate> =>
    api.get<QueryTemplate>(`${BASE}/query-templates/${id}?${tenantParam()}`),

  create: (data: QueryTemplateCreate): Promise<QueryTemplate> =>
    api.post<QueryTemplate>(`${BASE}/query-templates?${tenantParam()}`, data),

  update: (id: string, data: QueryTemplateUpdate): Promise<QueryTemplate> =>
    api.put<QueryTemplate>(`${BASE}/query-templates/${id}?${tenantParam()}`, data),

  delete: (id: string): Promise<void> =>
    api.delete(`${BASE}/query-templates/${id}?${tenantParam()}`),

  test: (id: string, params: Record<string, unknown> = {}): Promise<QueryExecuteResult> =>
    api.post<QueryExecuteResult>(
      `${BASE}/query-templates/${id}/test?${tenantParam()}`,
      { params }
    ),

  listByTool: (toolId: string, status?: string): Promise<QueryTemplate[]> => {
    const params = [tenantParam()];
    if (status) params.push(`status=${encodeURIComponent(status)}`);
    return api.get<QueryTemplate[]>(`/api/enterprise/tools/${encodeURIComponent(toolId)}/skills?${params.join('&')}`);
  },

  adhocTest: (data: AdhocTestRequest): Promise<QueryExecuteResult> =>
    api.post<QueryExecuteResult>(`${BASE}/query-templates/adhoc-test?${tenantParam()}`, data),

  getVersions: (id: string): Promise<QueryTemplateVersion[]> =>
    api.get<QueryTemplateVersion[]>(`${BASE}/query-templates/${id}/versions?${tenantParam()}`),

  rollbackVersion: (id: string, version: number): Promise<QueryTemplate> =>
    api.post<QueryTemplate>(`${BASE}/query-templates/${id}/versions/${version}/rollback?${tenantParam()}`),
};


// --- Execute API ---

export const executeApi = {
  execute: (templateId: string, params: Record<string, unknown> = {}): Promise<QueryExecuteResult> =>
    api.post<QueryExecuteResult>(`${BASE}/execute?${tenantParam()}`, {
      template_id: templateId,
      params,
    }),
};
