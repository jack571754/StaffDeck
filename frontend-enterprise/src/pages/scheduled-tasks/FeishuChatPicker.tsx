import { useId, useMemo, useState } from 'react';
import { ChevronDown, Search, Users, X } from 'lucide-react';

import { Checkbox, Input } from '@/components/ui';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

type Chat = { chat_id: string; name: string };

type Props = {
  chats: Chat[];
  selected: Chat[];
  onToggle: (id: string, name: string) => void;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
};

export default function FeishuChatPicker({
  chats, selected, onToggle, disabled = false, loading = false, error,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const fieldId = useId();
  const filtered = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    return chats.filter((chat) => chat.name.toLocaleLowerCase().includes(keyword));
  }, [chats, query]);

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Popover open={open && !disabled && !loading} onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery('');
      }}>
        <PopoverTrigger asChild>
          <Button type="button" variant="outline" role="combobox" aria-label="选择目标群聊"
            aria-expanded={open && !disabled && !loading} disabled={disabled || loading}
            className="h-9 w-full justify-between gap-2 bg-white text-[13px] font-normal">
            <span className="flex min-w-0 items-center gap-2 text-[#858b9c]">
              <Search className="size-3.5 shrink-0" />
              {loading ? '正在加载群聊…' : '按群名称搜索并选择'}
            </span>
            <ChevronDown className="size-3.5 shrink-0 text-[#858b9c]" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] min-w-[240px] max-w-[calc(100vw-32px)] gap-2 p-2">
          <Input type="search" aria-label="搜索群名称" placeholder="输入群名称"
            value={query} onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') event.preventDefault(); }}
            className="h-8 text-[13px]" />
          <div className="max-h-[220px] overflow-y-auto" aria-label="可选群聊">
            {filtered.map((chat) => (
              <label key={chat.chat_id} htmlFor={`${fieldId}-${chat.chat_id}`}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-[13px] hover:bg-[#f0f4ff]">
                <Checkbox id={`${fieldId}-${chat.chat_id}`}
                  checked={selected.some((item) => item.chat_id === chat.chat_id)}
                  onCheckedChange={() => onToggle(chat.chat_id, chat.name)} />
                <Users className="size-3.5 shrink-0 text-[#858b9c]" aria-hidden="true" />
                <span className="min-w-0 truncate" title={chat.name}>{chat.name}</span>
              </label>
            ))}
            {filtered.length === 0 && <p className="px-2 py-5 text-center text-xs text-[#858b9c]">
              {chats.length ? '没有匹配的群聊' : '暂无可选群聊，请先将应用机器人加入目标群。'}
            </p>}
          </div>
        </PopoverContent>
      </Popover>
      {selected.length > 0 && <div className="flex flex-wrap gap-1.5" aria-label="已选群聊">
        {selected.map((chat) => (
          <span key={chat.chat_id} className="inline-flex max-w-full items-center gap-1.5 rounded-md bg-[#e8f0ff] px-2 py-1 text-xs text-[#3370ff]">
            <span className="truncate" title={chat.name}>{chat.name}</span>
            <button type="button" aria-label="移除群聊" disabled={disabled}
              onClick={() => onToggle(chat.chat_id, chat.name)}
              className="shrink-0 rounded-sm p-0.5 hover:bg-[#d0e2ff] focus-visible:outline-2 disabled:opacity-50">
              <X className="size-3" />
            </button>
          </span>
        ))}
      </div>}
      {error && <p role="alert" className="text-xs leading-5 text-[#d20b0b]">{error}</p>}
      {!error && !loading && chats.length === 0 && <p className="text-xs leading-5 text-[#858b9c]">
        暂无可选群聊，请先将应用机器人加入目标群。
      </p>}
    </div>
  );
}
