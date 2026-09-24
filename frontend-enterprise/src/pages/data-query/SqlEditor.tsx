import { useState, useRef, useMemo, type KeyboardEvent, type UIEvent } from 'react';
import { Copy, Check, Sparkles, RotateCcw, Code2 } from 'lucide-react';
import { renderCodeTokens } from '@/components/CodeBlock';

export interface SqlEditorProps {
  queryType: string; // 'sql' | 'http'
  queryContent: string;
  onChange: (content: string) => void;
  className?: string;
  minHeight?: number | string;
}

const CARD_CLASS = 'rounded-[14px] border border-[#eceef1] bg-white shadow-xs';
const CARD_TITLE_CLASS = 'text-[14px] font-medium text-[#18181a] flex items-center gap-2';
const HINT_CLASS = 'text-[12px] leading-[1.55] text-[#858b9c]';

export function formatSql(sql: string): string {
  if (!sql || !sql.trim()) return sql;

  const preserved: string[] = [];
  const placeholderPrefix = '___SQL_STR_';
  let counter = 0;

  const protectedSql = sql.replace(
    /(--.*$|\/\*[\s\S]*?\*\/|'(?:''|\\'|[^'])*'|"(?:""|\\"|[^"])*")/gm,
    (match) => {
      const ph = `${placeholderPrefix}${counter++}___`;
      preserved.push(match);
      return ph;
    }
  );

  const keywordsToBreak = [
    'SELECT',
    'FROM',
    'WHERE',
    'LEFT JOIN',
    'RIGHT JOIN',
    'INNER JOIN',
    'FULL JOIN',
    'CROSS JOIN',
    'JOIN',
    'ON',
    'GROUP BY',
    'HAVING',
    'ORDER BY',
    'LIMIT',
    'OFFSET',
    'UNION ALL',
    'UNION',
    'INSERT INTO',
    'VALUES',
    'UPDATE',
    'SET',
    'DELETE FROM',
  ];

  let normalized = protectedSql.replace(/\s+/g, ' ').trim();

  for (const kw of keywordsToBreak) {
    const pattern = new RegExp(`\\b${kw.replace(/\s+/g, '\\s+')}\\b`, 'gi');
    normalized = normalized.replace(pattern, `\n${kw}`);
  }

  normalized = normalized.replace(/\b(AND|OR)\b/gi, (_, kw) => `\n  ${kw.toUpperCase()}`);

  const restored = normalized.replace(
    new RegExp(`${placeholderPrefix}(\\d+)___`, 'g'),
    (_, idx) => {
      const str = preserved[Number(idx)] || '';
      return str.startsWith('--') ? `\n${str}\n` : str;
    }
  );

  return restored.trim();
}

export default function SqlEditor({
  queryType,
  queryContent,
  onChange,
  className = '',
  minHeight = 260,
}: SqlEditorProps) {
  const isSql = queryType === 'sql';
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  const codeRef = useRef<HTMLElement>(null);
  const [copied, setCopied] = useState(false);
  const [scrollState, setScrollState] = useState({ top: 0, left: 0 });

  const title = isSql ? 'SQL 查询' : 'HTTP 请求路径';
  const hint = isSql
    ? '使用 :param_name 命名参数'
    : '使用 {param_name} 路径参数，未匹配的参数自动作为 query 参数追加';
  const placeholder = isSql
    ? 'SELECT * FROM sales WHERE DATE(date) = :date'
    : '/api/v1/sales?date={date}';

  const lines = useMemo(() => {
    return queryContent ? queryContent.split('\n') : [''];
  }, [queryContent]);

  const detectedParams = useMemo(() => {
    if (!queryContent) return [];
    const pattern = isSql ? /:([A-Za-z_]\w*)/g : /\{([A-Za-z_]\w*)\}/g;
    const matches: string[] = [];
    let m: RegExpExecArray | null;
    while ((m = pattern.exec(queryContent)) !== null) {
      if (m[1] && !matches.includes(m[1])) {
        matches.push(m[1]);
      }
    }
    return matches;
  }, [queryContent, isSql]);

  const handleScroll = (e: UIEvent<HTMLTextAreaElement>) => {
    const top = e.currentTarget.scrollTop;
    const left = e.currentTarget.scrollLeft;
    setScrollState({ top, left });
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const textarea = textareaRef.current;
      if (!textarea) return;

      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const before = queryContent.substring(0, start);
      const after = queryContent.substring(end);
      const nextContent = before + '  ' + after;

      onChange(nextContent);

      setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.selectionStart = textareaRef.current.selectionEnd = start + 2;
        }
      }, 0);
    }
  };

  const handleCopy = async () => {
    if (!queryContent) return;
    try {
      await navigator.clipboard.writeText(queryContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // fallback
    }
  };

  const handleFormat = () => {
    if (!isSql || !queryContent.trim()) return;
    const formatted = formatSql(queryContent);
    onChange(formatted);
  };

  const handleClear = () => {
    onChange('');
  };

  return (
    <section className={`${CARD_CLASS} overflow-hidden ${className}`}>
      {/* Top Header */}
      <div className="flex min-h-[50px] flex-wrap items-center justify-between gap-[12px] border-b border-[#eceef1] px-[16px] py-[8px] bg-[#fbfcfd]">
        <div className={CARD_TITLE_CLASS}>
          <Code2 className="size-[15px] text-[#4f566b]" />
          <span>{title}</span>
          <span className="inline-flex items-center rounded-md bg-[#f1f3f7] px-[6px] py-[1px] text-[11px] font-semibold text-[#5a6275]">
            {isSql ? 'SQL' : 'HTTP'}
          </span>
        </div>

        {/* Toolbar & Hint */}
        <div className="flex items-center gap-[10px]">
          <span className={`hidden sm:inline ${HINT_CLASS}`}>{hint}</span>

          <div className="flex items-center gap-[4px] border-l border-[#eceef1] pl-[10px]">
            {isSql && (
              <button
                type="button"
                onClick={handleFormat}
                disabled={!queryContent.trim()}
                title="格式化 SQL"
                className="inline-flex h-[26px] items-center gap-[4px] rounded-[6px] px-[8px] text-[12px] font-medium text-[#5a6275] transition-colors hover:bg-[#edf1f7] hover:text-[#18181a] disabled:opacity-40 disabled:pointer-events-none"
              >
                <Sparkles className="size-[13px] text-[#2563eb]" />
                <span>格式化</span>
              </button>
            )}

            <button
              type="button"
              onClick={handleCopy}
              disabled={!queryContent.trim()}
              title="复制代码"
              className="inline-flex h-[26px] items-center gap-[4px] rounded-[6px] px-[8px] text-[12px] font-medium text-[#5a6275] transition-colors hover:bg-[#edf1f7] hover:text-[#18181a] disabled:opacity-40 disabled:pointer-events-none"
            >
              {copied ? (
                <>
                  <Check className="size-[13px] text-[#16a34a]" />
                  <span className="text-[#16a34a]">已复制</span>
                </>
              ) : (
                <>
                  <Copy className="size-[13px]" />
                  <span>复制</span>
                </>
              )}
            </button>

            <button
              type="button"
              onClick={handleClear}
              disabled={!queryContent.trim()}
              title="清空内容"
              className="inline-flex h-[26px] items-center gap-[4px] rounded-[6px] px-[8px] text-[12px] font-medium text-[#5a6275] transition-colors hover:bg-[#fee2e2] hover:text-[#dc2626] disabled:opacity-40 disabled:pointer-events-none"
            >
              <RotateCcw className="size-[13px]" />
              <span>清空</span>
            </button>
          </div>
        </div>
      </div>

      {/* Editor Main Canvas */}
      <div
        className="relative flex overflow-hidden border-b border-[#eceef1] bg-[#ffffff] font-mono text-[13px] leading-[22px]"
        style={{ minHeight: typeof minHeight === 'number' ? `${minHeight}px` : minHeight }}
      >
        {/* Line Numbers Gutter */}
        <div
          ref={gutterRef}
          aria-hidden="true"
          className="pointer-events-none shrink-0 select-none overflow-hidden border-r border-[#eceef1] bg-[#f8f9fb] py-[12px] px-[8px] text-right font-mono text-[12px] leading-[22px] text-[#9ea3b5]"
          style={{ width: `${Math.max(38, String(lines.length).length * 9 + 18)}px` }}
        >
          <div style={{ transform: `translateY(${-scrollState.top}px)` }}>
            {lines.map((_, i) => (
              <div key={i} className="h-[22px] leading-[22px]">
                {i + 1}
              </div>
            ))}
          </div>
        </div>

        {/* Code Content Container */}
        <div className="relative flex-1 min-w-0 overflow-hidden bg-white">
          {/* Highlight Layer (Behind) */}
          <pre
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 z-[1] m-0 overflow-hidden whitespace-pre py-[12px] px-[16px] font-mono text-[13px] leading-[22px] tracking-[0.01em] tab-[2]"
          >
            <code
              ref={codeRef}
              className="block w-max min-w-full font-[inherit]"
              style={{
                transform: `translate(${-scrollState.left}px, ${-scrollState.top}px)`,
              }}
            >
              {queryContent ? (
                renderCodeTokens(
                  queryContent.endsWith('\n') ? `${queryContent} ` : queryContent,
                  isSql ? 'sql' : 'plain'
                )
              ) : (
                <span className="text-[#9ea3b5] italic select-none">
                  {placeholder}
                </span>
              )}
            </code>
          </pre>

          {/* Interactive Textarea (Foreground) */}
          <textarea
            ref={textareaRef}
            value={queryContent}
            onChange={(e) => onChange(e.target.value)}
            onScroll={handleScroll}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            spellCheck={false}
            className="absolute inset-0 z-[2] m-0 size-full resize-none overflow-auto border-0 bg-transparent! py-[12px] px-[16px] font-mono text-[13px] leading-[22px] tracking-[0.01em] whitespace-pre text-transparent caret-[#18181a] outline-none tab-[2] placeholder:text-transparent selection:bg-[rgba(0,120,215,0.24)] [-webkit-text-fill-color:transparent]"
          />
        </div>
      </div>

      {/* Bottom Status & Parameter Detection Bar */}
      <div className="flex flex-wrap items-center justify-between gap-[10px] bg-[#fbfcfd] px-[16px] py-[8px] text-[12px] text-[#757f9c]">
        <div className="flex items-center gap-[12px]">
          <span>共 {lines.length} 行</span>
          <span className="text-[#d0d4dc]">·</span>
          <span>{queryContent.length} 字符</span>
          <span className="hidden sm:inline text-[#d0d4dc]">·</span>
          <span className="hidden sm:inline text-[#858b9c]">Tab 自动缩进 · 实时语法高亮</span>
        </div>

        {/* Detected Parameters Badges */}
        {detectedParams.length > 0 && (
          <div className="flex items-center gap-[6px] flex-wrap">
            <span className="text-[#858b9c]">识别参数:</span>
            {detectedParams.map((p) => (
              <span
                key={p}
                className="inline-flex items-center rounded bg-[#eff6ff] px-[6px] py-[1px] font-mono text-[11px] font-medium text-[#2563eb] border border-[#bfdbfe]"
              >
                {isSql ? `:${p}` : `{${p}}`}
              </span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
