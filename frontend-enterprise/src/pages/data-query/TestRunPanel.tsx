import { useMemo, useState } from 'react';

import {
  Badge,
  Button,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  notify,
} from '@/components/ui';
import { queryTemplatesApi, type QueryExecuteResult } from '@/api/data-query';
import { Play, Loader2 } from 'lucide-react';
import ResultTable from './ResultTable';
import type { QueryParam } from './ParamsConfigPanel';

const CARD_CLASS = 'rounded-[14px] border border-[#eceef1] bg-white';
const CARD_TITLE_CLASS = 'text-[14px] font-medium text-[#18181a]';
const FIELD_LABEL_CLASS = 'text-[12px] font-medium text-[#464c5e]';

export interface TestRunPanelProps {
  templateId: string | null;
  params: QueryParam[];
}

/**
 * 根据参数类型获取默认值
 */
function getDefaultValue(param: QueryParam): unknown {
  if (param.default !== undefined && param.default !== null && param.default !== '') {
    return param.default;
  }
  switch (param.type) {
    case 'bool':
      return false;
    case 'int':
    case 'float':
    case 'number':
      return '';
    default:
      return '';
  }
}

/**
 * 测试运行面板组件
 * 动态生成参数输入、运行测试、展示结果
 */
export default function TestRunPanel({ templateId, params }: TestRunPanelProps) {
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<QueryExecuteResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 初始化参数值（从默认值）
  const initialValues = useMemo(() => {
    const values: Record<string, unknown> = {};
    params.forEach((p) => {
      values[p.name] = getDefaultValue(p);
    });
    return values;
  }, [params]);

  // 当 params 变化时，合并已有值与默认值
  const mergedParamValues = useMemo(() => {
    const merged: Record<string, unknown> = { ...initialValues };
    // 保留用户已输入的值
    params.forEach((p) => {
      if (p.name in paramValues) {
        merged[p.name] = paramValues[p.name];
      }
    });
    return merged;
  }, [initialValues, paramValues, params]);

  const handleParamChange = (name: string, value: unknown) => {
    setParamValues((prev) => ({ ...prev, [name]: value }));
  };

  const handleRun = async () => {
    if (!templateId) {
      notify.error('请先保存模板');
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const data = await queryTemplatesApi.test(templateId, mergedParamValues);
      setResult(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : '运行失败';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const canRun = templateId !== null && !loading;

  return (
    <section className={CARD_CLASS + ' overflow-hidden'}>
      {/* 标题栏 */}
      <div className="flex min-h-[54px] items-center justify-between gap-[12px] border-b border-[#eceef1] px-[20px] py-[10px]">
        <div className={'min-w-0 ' + CARD_TITLE_CLASS}>测试运行</div>
        <div className="flex shrink-0 items-center gap-[6px]">
          {!templateId ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span tabIndex={0}>
                  <Button variant="default" size="xs" disabled>
                    <Play className="size-3.5" />
                    运行测试
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>请先保存模板</TooltipContent>
            </Tooltip>
          ) : (
            <Button variant="default" size="xs" onClick={handleRun} disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  运行中...
                </>
              ) : (
                <>
                  <Play className="size-3.5" />
                  运行测试
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* 内容区 */}
      <div className="flex flex-col gap-[12px] p-[12px]">
        {/* 参数输入区 */}
        <div className="flex flex-col gap-[8px]">
          {params.length === 0 ? (
            <div className="rounded-[8px] bg-[#f7f8fa] px-[12px] py-[10px] text-[12px] text-[#858b9c]">
              此模板无参数，可直接运行
            </div>
          ) : (
            <div className="flex flex-col gap-[8px]">
              {params.map((param) => (
                <ParamInput
                  key={param.name}
                  param={param}
                  value={mergedParamValues[param.name]}
                  onChange={(val) => handleParamChange(param.name, val)}
                />
              ))}
            </div>
          )}
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="rounded-[8px] border border-[#fecaca] bg-[#fef2f2] px-[12px] py-[10px]">
            <div className="text-[12px] font-medium text-[#b91c1c]">运行失败</div>
            <div className="mt-[2px] break-all text-[11px] text-[#dc2626]">{error}</div>
          </div>
        )}

        {/* 结果信息 + 表格 */}
        {result && (
          <div className="flex flex-col gap-[8px]">
            {/* 结果统计信息 */}
            <div className="flex flex-wrap items-center gap-[10px] text-[12px] text-[#858b9c]">
              <span>
                返回 <span className="font-medium text-[#18181a]">{result.row_count}</span> 行
              </span>
              <span className="text-[#d6d9e0]">|</span>
              <span>
                耗时 <span className="font-medium text-[#18181a]">{result.execution_time_ms}ms</span>
              </span>
              {result.cached && (
                <>
                  <span className="text-[#d6d9e0]">|</span>
                  <Badge variant="outline" className="border-[#bbf7d0] bg-[#f0fdf4] text-[#16a34a]">
                    缓存命中
                  </Badge>
                </>
              )}
            </div>

            {/* 结果表格 */}
            <ResultTable
              columns={result.columns}
              rows={result.rows}
              maxHeight="320px"
            />
          </div>
        )}

        {/* 未运行提示 */}
        {!result && !error && params.length > 0 && (
          <div className="rounded-[8px] bg-[#f7f8fa] px-[12px] py-[16px] text-center text-[12px] text-[#858b9c]">
            填入参数后点击「运行测试」查看结果
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * 单个参数输入控件
 */
function ParamInput({
  param,
  value,
  onChange,
}: {
  param: QueryParam;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const { name, type, required, description, enum: enumValues } = param;

  // 有枚举值时用 Select
  if (enumValues && enumValues.length > 0) {
    return (
      <div className="flex flex-col gap-[4px]">
        <Label className={FIELD_LABEL_CLASS}>
          {name}
          {required && <span className="ml-[2px] text-[#d20b0b]">*</span>}
        </Label>
        <Select value={String(value ?? '')} onValueChange={(v) => onChange(v)}>
          <SelectTrigger className="h-[30px] text-[12px]">
            <SelectValue placeholder="请选择" />
          </SelectTrigger>
          <SelectContent>
            {enumValues.map((val) => (
              <SelectItem key={val} value={val}>
                {val}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {description && (
          <span className="text-[11px] text-[#858b9c]">{description}</span>
        )}
      </div>
    );
  }

  // bool 类型用 Switch
  if (type === 'bool' || type === 'boolean') {
    return (
      <div className="flex flex-col gap-[4px]">
        <Label className={FIELD_LABEL_CLASS}>
          {name}
          {required && <span className="ml-[2px] text-[#d20b0b]">*</span>}
        </Label>
        <div className="flex h-[30px] items-center">
          <Switch
            checked={Boolean(value)}
            onCheckedChange={(checked) => onChange(checked)}
          />
          <span className="ml-[8px] text-[12px] text-[#858b9c]">
            {value ? 'true' : 'false'}
          </span>
        </div>
        {description && (
          <span className="text-[11px] text-[#858b9c]">{description}</span>
        )}
      </div>
    );
  }

  // date 类型
  if (type === 'date') {
    return (
      <div className="flex flex-col gap-[4px]">
        <Label htmlFor={`trp-${name}`} className={FIELD_LABEL_CLASS}>
          {name}
          {required && <span className="ml-[2px] text-[#d20b0b]">*</span>}
        </Label>
        <Input
          id={`trp-${name}`}
          type="date"
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          className="h-[30px] text-[12px]"
        />
        {description && (
          <span className="text-[11px] text-[#858b9c]">{description}</span>
        )}
      </div>
    );
  }

  // 数字类型
  if (type === 'int' || type === 'float' || type === 'number') {
    return (
      <div className="flex flex-col gap-[4px]">
        <Label htmlFor={`trp-${name}`} className={FIELD_LABEL_CLASS}>
          {name}
          {required && <span className="ml-[2px] text-[#d20b0b]">*</span>}
        </Label>
        <Input
          id={`trp-${name}`}
          type="number"
          step={type === 'float' || type === 'number' ? 'any' : '1'}
          value={value as number | string}
          onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
          className="h-[30px] text-[12px]"
          placeholder={`请输入${name}`}
        />
        {description && (
          <span className="text-[11px] text-[#858b9c]">{description}</span>
        )}
      </div>
    );
  }

  // 默认 string 类型
  return (
    <div className="flex flex-col gap-[4px]">
      <Label htmlFor={`trp-${name}`} className={FIELD_LABEL_CLASS}>
        {name}
        {required && <span className="ml-[2px] text-[#d20b0b]">*</span>}
      </Label>
      <Input
        id={`trp-${name}`}
        type="text"
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        className="h-[30px] text-[12px]"
        placeholder={`请输入${name}`}
      />
      {description && (
        <span className="text-[11px] text-[#858b9c]">{description}</span>
      )}
    </div>
  );
}
