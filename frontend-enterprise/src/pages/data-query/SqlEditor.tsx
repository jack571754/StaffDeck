import { Textarea } from '@/components/ui';

export interface SqlEditorProps {
  queryType: string; // 'sql' | 'http'
  queryContent: string;
  onChange: (content: string) => void;
}

const CARD_CLASS =
  'rounded-[14px] border border-[#eceef1] bg-white';
const CARD_TITLE_CLASS = 'text-[14px] font-medium text-[#18181a]';
const HINT_CLASS = 'text-[12px] leading-[1.55] text-[#858b9c]';

export default function SqlEditor({
  queryType,
  queryContent,
  onChange,
}: SqlEditorProps) {
  const isSql = queryType === 'sql';

  const title = isSql ? 'SQL 查询' : 'HTTP 请求路径';
  const hint = isSql
    ? '使用 :param_name 命名参数'
    : '使用 {param_name} 路径参数，未匹配的参数自动作为 query 参数追加';
  const placeholder = isSql
    ? 'SELECT * FROM sales WHERE DATE(date) = :date'
    : '/api/v1/sales?date={date}';

  return (
    <section className={CARD_CLASS + ' overflow-hidden'}>
      <div className="flex min-h-[54px] items-center justify-between gap-[12px] border-b border-[#eceef1] px-[20px] py-[10px]">
        <div className={'min-w-0 ' + CARD_TITLE_CLASS}>{title}</div>
        <div className={'shrink-0 ' + HINT_CLASS}>{hint}</div>
      </div>
      <div className="p-[12px]">
        <Textarea
          value={queryContent}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          spellCheck={false}
          className="min-h-[240px] resize-y font-mono text-[13px] leading-[1.6] tracking-[0.01em]"
        />
      </div>
    </section>
  );
}
