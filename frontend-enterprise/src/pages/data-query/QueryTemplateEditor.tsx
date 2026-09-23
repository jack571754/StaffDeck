import { useEffect, useState } from 'react';

import {
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from '@/components/ui';
import { notify } from '@/components/ui/app-toast';
import {
  dataSourcesApi,
  type DataSource,
  type QueryTemplate,
} from '@/api/data-query';
import SqlEditor from './SqlEditor';
import ParamsConfigPanel, { type QueryParam } from './ParamsConfigPanel';
import TestRunPanel from './TestRunPanel';

export interface QueryTemplateEditorProps {
  template: QueryTemplate | null;
  onChange: (template: QueryTemplate | null) => void;
  isNew: boolean;
}

const CARD_CLASS =
  'rounded-[14px] border border-[#eceef1] bg-white';
const CARD_TITLE_CLASS = 'text-[14px] font-medium text-[#18181a]';
const FIELD_LABEL_CLASS = 'text-[13px] font-medium text-[#18181a]';
const HINT_CLASS = 'text-[12px] leading-[1.55] text-[#858b9c]';

const DEFAULT_TEMPLATE: QueryTemplate = {
  id: '',
  tenant_id: '',
  name: '',
  description: '',
  data_source_id: '',
  query_type: 'sql',
  query_content: '',
  params_json: [],
  output_config_json: {},
  cache_ttl: 300,
  timeout_seconds: 30,
  max_rows: 1000,
  status: 'draft',
  created_at: '',
  updated_at: '',
};

function SectionCard({
  title,
  extra,
  loading,
  children,
  className,
  bodyClassName,
}: {
  title?: React.ReactNode;
  extra?: React.ReactNode;
  loading?: boolean;
  children?: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={CARD_CLASS + ' overflow-hidden ' + (className || '')}>
      {(title || extra) && (
        <div className="flex min-h-[54px] items-center justify-between gap-[12px] border-b border-[#eceef1] px-[20px] py-[10px]">
          <div className={'min-w-0 ' + CARD_TITLE_CLASS}>{title}</div>
          {extra ? <div className="shrink-0">{extra}</div> : null}
        </div>
      )}
      <div className={'p-[20px] ' + (bodyClassName || '')}>
        {loading ? (
          <div className="py-[24px] text-center text-[13px] text-[#858b9c]">
            加载中...
          </div>
        ) : (
          children
        )}
      </div>
    </section>
  );
}

function Field({
  label,
  htmlFor,
  hint,
  className,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={'flex flex-col gap-[6px] ' + (className || '')}>
      <label htmlFor={htmlFor} className={FIELD_LABEL_CLASS}>
        {label}
      </label>
      {children}
      {hint ? <span className={HINT_CLASS}>{hint}</span> : null}
    </div>
  );
}

function OutputConfigSection({
  value,
  onChange,
}: {
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [error, setError] = useState('');

  // 外部值变化（如保存后回填）且与本地文本解析结果不一致时，重置本地文本
  useEffect(() => {
    let current: unknown = null;
    try {
      current = JSON.parse(text);
    } catch {
      current = null;
    }
    if (JSON.stringify(current) !== JSON.stringify(value ?? {})) {
      setText(JSON.stringify(value ?? {}, null, 2));
      setError('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const handleChange = (next: string) => {
    setText(next);
    if (!next.trim()) {
      setError('');
      onChange({});
      return;
    }
    try {
      const parsed = JSON.parse(next) as unknown;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setError('输出配置需要是 JSON 对象（以 { } 包裹）');
        return;
      }
      setError('');
      onChange(parsed as Record<string, unknown>);
    } catch {
      setError('JSON 格式不正确');
    }
  };

  return (
    <SectionCard
      title="输出配置"
      extra={
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          className="flex items-center gap-1 text-[12px] text-[#757f9c] hover:text-[#18181a]"
        >
          {open ? '收起' : '展开'}
        </button>
      }
    >
      {open ? (
        <div className="flex flex-col gap-[8px]">
          <span className={HINT_CLASS}>
            可选。以 JSON 对象格式定义输出列配置，用于查询结果的格式化展示。
          </span>
          <textarea
            value={text}
            onChange={(e) => handleChange(e.target.value)}
            rows={6}
            spellCheck={false}
            placeholder='{"columns": [{"name": "date", "label": "日期"}]}'
            className="w-full resize-y rounded-md border border-input bg-background p-3 font-mono text-[13px] focus:outline-none focus:ring-2 focus:ring-ring"
          />
          {error ? (
            <span className="text-[12px] text-red-600">{error}</span>
          ) : null}
        </div>
      ) : (
        <span className={HINT_CLASS}>
          配置查询结果的输出格式，点击右上角「展开」编辑。
        </span>
      )}
    </SectionCard>
  );
}

export default function QueryTemplateEditor({
  template,
  onChange,
  isNew,
}: QueryTemplateEditorProps) {
  const [formData, setFormData] = useState<QueryTemplate>(DEFAULT_TEMPLATE);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [dsLoading, setDsLoading] = useState(false);

  // 同步父组件 template 到局部 state（编辑模式初次加载）
  useEffect(() => {
    if (template) {
      setFormData(template);
    } else if (isNew) {
      setFormData(DEFAULT_TEMPLATE);
    }
  }, [template, isNew]);

  // 加载数据源列表
  useEffect(() => {
    setDsLoading(true);
    dataSourcesApi
      .list()
      .then((data) => {
        setDataSources(data);
        // 新建模式下，如果还没有选择数据源且列表不为空，不自动选——等用户选
      })
      .catch((error) => {
        notify.error(
          error instanceof Error ? error.message : '加载数据源失败',
        );
      })
      .finally(() => {
        setDsLoading(false);
      });
  }, []);

  const updateField = <K extends keyof QueryTemplate>(
    key: K,
    value: QueryTemplate[K],
  ) => {
    setFormData((prev) => {
      const next = { ...prev, [key]: value };
      onChange(next);
      return next;
    });
  };

  const handleQueryTypeChange = (value: string) => {
    setFormData((prev) => {
      const next = {
        ...prev,
        query_type: value,
        query_content: '', // 切换类型时清空 query_content
      };
      onChange(next);
      return next;
    });
  };

  return (
    <div className="grid grid-cols-1 items-start gap-[20px] lg:grid-cols-[minmax(0,1fr)_400px]">
      {/* 左侧：基础信息 + SQL 编辑器 */}
      <div className="flex flex-col gap-[20px]">
        {/* 基础信息卡片 */}
        <SectionCard title="基础信息">
          <div className="flex flex-col gap-[16px]">
            <Field label="名称" htmlFor="qt-name" hint="必填，模板的显示名称">
              <Input
                id="qt-name"
                placeholder="例如：每日销售数据查询"
                value={formData.name}
                onChange={(e) => updateField('name', e.target.value)}
              />
            </Field>

            <Field label="描述" htmlFor="qt-description">
              <Textarea
                id="qt-description"
                rows={2}
                placeholder="简单说明这个查询模板的用途"
                value={formData.description}
                onChange={(e) => updateField('description', e.target.value)}
              />
            </Field>

            <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
              <Field label="数据源" htmlFor="qt-data-source">
                <Select
                  value={formData.data_source_id || ''}
                  onValueChange={(value) => updateField('data_source_id', value)}
                  disabled={dsLoading}
                >
                  <SelectTrigger id="qt-data-source" className="w-full">
                    <SelectValue placeholder="选择数据源" />
                  </SelectTrigger>
                  <SelectContent>
                    {dataSources.length === 0 && !dsLoading ? (
                      <SelectItem value="__none__" disabled>
                        暂无可用数据源
                      </SelectItem>
                    ) : (
                      dataSources.map((ds) => (
                        <SelectItem key={ds.id} value={ds.id}>
                          {ds.name}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              </Field>

              <Field label="查询类型" htmlFor="qt-query-type">
                <Select
                  value={formData.query_type}
                  onValueChange={handleQueryTypeChange}
                >
                  <SelectTrigger id="qt-query-type" className="w-full">
                    <SelectValue placeholder="选择查询类型" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="sql">SQL</SelectItem>
                    <SelectItem value="http">HTTP</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </div>

            <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
              <Field label="状态" htmlFor="qt-status">
                <Select
                  value={formData.status}
                  onValueChange={(value) => updateField('status', value)}
                >
                  <SelectTrigger id="qt-status" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">草稿</SelectItem>
                    <SelectItem value="active">已启用</SelectItem>
                  </SelectContent>
                </Select>
              </Field>

              <Field
                label="缓存 TTL（秒）"
                htmlFor="qt-cache-ttl"
                hint="查询结果缓存时间，0 表示不缓存"
              >
                <Input
                  id="qt-cache-ttl"
                  type="number"
                  min={0}
                  value={formData.cache_ttl}
                  onChange={(e) =>
                    updateField('cache_ttl', Number(e.target.value) || 0)
                  }
                />
              </Field>
            </div>

            <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
              <Field
                label="超时时间（秒）"
                htmlFor="qt-timeout"
                hint="单次查询最长执行时间"
              >
                <Input
                  id="qt-timeout"
                  type="number"
                  min={1}
                  value={formData.timeout_seconds}
                  onChange={(e) =>
                    updateField(
                      'timeout_seconds',
                      Math.max(1, Number(e.target.value) || 1),
                    )
                  }
                />
              </Field>

              <Field
                label="最大行数"
                htmlFor="qt-max-rows"
                hint="单次查询返回的最大数据行数"
              >
                <Input
                  id="qt-max-rows"
                  type="number"
                  min={1}
                  value={formData.max_rows}
                  onChange={(e) =>
                    updateField(
                      'max_rows',
                      Math.max(1, Number(e.target.value) || 1),
                    )
                  }
                />
              </Field>
            </div>
          </div>
        </SectionCard>

        {/* SQL / HTTP 编辑器 */}
        <SqlEditor
          queryType={formData.query_type}
          queryContent={formData.query_content}
          onChange={(content) => updateField('query_content', content)}
        />

        {/* 输出配置（第一期简化为 JSON 编辑） */}
        <OutputConfigSection
          value={formData.output_config_json ?? {}}
          onChange={(cfg) => updateField('output_config_json', cfg)}
        />
      </div>

      {/* 右侧：参数配置 + 测试运行 */}
      <div className="flex shrink-0 flex-col gap-[20px]">
        <ParamsConfigPanel
          params={formData.params_json as unknown as QueryParam[]}
          onChange={(params) =>
            updateField('params_json', params as unknown as Array<Record<string, unknown>>)
          }
          queryContent={formData.query_content}
          queryType={formData.query_type}
        />
        <TestRunPanel
          templateId={formData.id || null}
          params={formData.params_json as unknown as QueryParam[]}
        />
      </div>
    </div>
  );
}
