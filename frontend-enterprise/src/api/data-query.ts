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
}

export interface DataSourceUpdate {
  name?: string;
  description?: string;
  type?: string;
  config_json?: Record<string, unknown>;
  read_only?: boolean;
  status?: string;
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
};

// --- Execute API ---

export const executeApi = {
  execute: (templateId: string, params: Record<string, unknown> = {}): Promise<QueryExecuteResult> =>
    api.post<QueryExecuteResult>(`${BASE}/execute?${tenantParam()}`, {
      template_id: templateId,
      params,
    }),
};
