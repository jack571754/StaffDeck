import { useEffect, useRef, useState } from 'react';
import {
  Download,
  ExternalLink,
  Eye,
  Package,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { api } from '@/api/client';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { CapabilityScopeBadge } from '@/components/CapabilityScopeControl';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  Tabs,
  TabsList,
  TabsTrigger,
} from '@/components/ui';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { notify } from '@/components/ui/app-toast';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { renderMarkdownBlocks } from '@/pages/chat/chatHelpers';
import { StatusBadge } from '@/pages/scheduled-tasks/StatusBadge';
import type { BadgeTone } from '@/pages/scheduled-tasks/shared';

type MarketItem = {
  slug: string;
  name: string;
  description: string;
  category: string;
  origin?: string;
  installed: boolean;
  // 仅已安装列表返回
  stars?: number | string;
  installs?: number | string;
  version?: string;
  icon_url?: string;
  status?: string;
  capability_scope?: string;
  updated_at?: string;
  files_count?: number;
};

type MarketPreview = {
  slug: string;
  name: string;
  description: string;
  markdown: string;
  files: string[];
};

type MarketResponse = {
  source: string;
  total: number;
  items: MarketItem[];
};

const SOURCES = [
  { value: 'all', label: '全部' },
  { value: 'installed', label: '已安装' },
] as const;

const ORIGIN_LABEL_MAP: Record<string, string> = {
  skills_sh: 'Skills.sh',
};

const STATUS_BADGE_MAP: Record<string, { tone: BadgeTone; text: string }> = {
  draft: { tone: 'blue', text: '草稿' },
  published: { tone: 'green', text: '已启用' },
  archived: { tone: 'gray', text: '已停用' },
};

function formatDateTime(value?: string): string {
  if (!value) return '-';
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes}`;
  } catch {
    return value;
  }
}

export function SkillMarketDialog({
  open,
  onOpenChange,
  tenantId,
  agentId,
  onInstalled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tenantId: string;
  agentId?: string;
  onInstalled: () => void;
}) {
  const navigate = useNavigate();
  const [source, setSource] = useState<string>('all');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<MarketItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<string>('');
  const [preview, setPreview] = useState<MarketPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [uninstallTarget, setUninstallTarget] = useState<MarketItem | null>(null);
  const [uninstalling, setUninstalling] = useState(false);
  const requestSeq = useRef(0);

  const isInstalledTab = source === 'installed';
  const effectiveSource = isInstalledTab ? 'installed' : source;

  const loadItems = (nextSource: string, keyword: string) => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setLoadError('');
    const params = new URLSearchParams({ tenant_id: tenantId, source: nextSource });
    if (agentId) params.set('agent_id', agentId);
    if (keyword.trim()) params.set('q', keyword.trim());
    api
      .get<MarketResponse>(`/api/enterprise/general-skills/market/items?${params.toString()}`)
      .then((data) => {
        if (seq !== requestSeq.current) return;
        const rawList = data.items || [];
        const seen = new Set<string>();
        const uniqueList = rawList.filter((item) => {
          if (!item.slug || seen.has(item.slug)) return false;
          seen.add(item.slug);
          return true;
        });
        setItems(uniqueList);
        setTotal(typeof data.total === 'number' ? data.total : uniqueList.length);
      })
      .catch((error: unknown) => {
        if (seq !== requestSeq.current) return;
        setLoadError(error instanceof Error ? error.message : '加载技能市场失败');
        setItems([]);
        setTotal(0);
      })
      .finally(() => {
        if (seq === requestSeq.current) setLoading(false);
      });
  };

  const loadPreview = async (slug: string) => {
    setPreviewLoading(true);
    setPreview(null);
    try {
      const data = await api.get<MarketPreview>(
        `/api/enterprise/general-skills/market/skills/${encodeURIComponent(slug)}/preview?tenant_id=${tenantId}`,
      );
      setPreview(data);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '加载技能详情失败');
    } finally {
      setPreviewLoading(false);
    }
  };

  const install = async (slug: string) => {
    if (installing) return;
    setInstalling(true);
    try {
      await api.post('/api/enterprise/general-skills/market/install', {
        tenant_id: tenantId,
        slug,
        agent_id: agentId || undefined,
      });
      notify.success('已添加到技能库');
      setItems((current) => current.map((item) => (item.slug === slug ? { ...item, installed: true } : item)));
      onInstalled();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '添加失败');
    } finally {
      setInstalling(false);
    }
  };

  const confirmUninstall = async () => {
    if (!uninstallTarget || uninstalling) return;
    setUninstalling(true);
    try {
      const agentSuffix = agentId ? `&agent_id=${encodeURIComponent(agentId)}` : '';
      await api.delete(
        `/api/enterprise/general-skills/${encodeURIComponent(uninstallTarget.slug)}?tenant_id=${tenantId}${agentSuffix}`,
      );
      notify.success(`已移除技能「${uninstallTarget.name}」`);
      setItems((current) => current.filter((item) => item.slug !== uninstallTarget.slug));
      setTotal((prev) => Math.max(0, prev - 1));
      if (selected === uninstallTarget.slug) {
        setSelected('');
        setPreview(null);
      }
      setUninstallTarget(null);
      onInstalled();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : '移除技能失败');
    } finally {
      setUninstalling(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(
      () => {
        loadItems(effectiveSource, query);
        setSelected('');
        setPreview(null);
      },
      query.trim() ? 300 : 0,
    );
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source, query]);

  useEffect(() => {
    if (!open) return;
    if (selected) void loadPreview(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, open]);

  const selectSlug = (slug: string) => {
    setSelected(slug === selected ? '' : slug);
  };

  const trimmedQuery = query.trim();

  return (
    <>
      <Dialog open={open} onOpenChange={(next) => !next && onOpenChange(false)}>
        <DialogContent
          aria-describedby={undefined}
          className="flex h-[min(720px,calc(100dvh-3rem))] w-[calc(100%-2rem)] flex-col gap-[14px] overflow-hidden rounded-[16px] px-[20px] py-[16px] sm:max-w-[1000px]"
        >
          <div className="flex items-center gap-[6px] px-[12px] text-[#757f9c]">
            <Package className="size-[15px] shrink-0" />
            <DialogTitle className="text-[14px] font-normal leading-none text-[#757f9c]">
              技能市场（Skills.sh）
            </DialogTitle>
          </div>

          <Tabs value={source} onValueChange={setSource} className="flex shrink-0 flex-col gap-[10px]">
            <div className="flex items-center justify-between gap-[10px] px-[12px]">
              <TabsList className="bg-[#f3f4f7]">
                {SOURCES.map((item) => (
                  <TabsTrigger key={item.value} value={item.value}>
                    {item.label}
                  </TabsTrigger>
                ))}
              </TabsList>
              <div className="relative w-[240px]">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-[14px] -translate-y-1/2 text-[#a3aabf]" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={isInstalledTab ? '搜索已安装技能' : '搜索 skills.sh 技能'}
                  className="h-[32px] rounded-[10px] border-[#e3e7f1] bg-white pl-[30px] text-[12px] placeholder:text-[#c0c6d4] focus-visible:ring-0"
                />
                {query && (
                  <button
                    type="button"
                    aria-label="清空搜索"
                    onClick={() => setQuery('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-[2px] text-[#a3aabf] hover:text-[#18181a]"
                  >
                    <X className="size-[13px]" />
                  </button>
                )}
              </div>
            </div>
          </Tabs>

          <div className="flex min-h-0 flex-1 gap-[14px]">
            <div className="flex min-w-0 flex-1 flex-col gap-[10px]">
              <div className="flex items-center justify-between px-[12px] text-[11px] text-[#858b9c]">
                <span>
                  {isInstalledTab
                    ? trimmedQuery
                      ? `“${trimmedQuery}” 的已安装技能搜索结果（${items.length}）`
                      : `共 ${total} 个已安装技能`
                    : trimmedQuery
                      ? `“${trimmedQuery}” 的搜索结果（${total}）`
                      : `共 ${total} 个技能`}
                </span>
              </div>

              {isInstalledTab ? (
                /* 已安装技能 Table 表格视图 */
                <ScrollArea className="min-h-0 flex-1">
                  {loading ? (
                    <div className="flex flex-col gap-[12px] p-[16px]">
                      {Array.from({ length: 5 }).map((_, index) => (
                        <div key={index} className="flex items-center gap-[12px]">
                          <Skeleton className="size-[32px] shrink-0 rounded-[8px]" />
                          <Skeleton className="h-[14px] w-[20%] rounded" />
                          <Skeleton className="h-[14px] w-[40%] rounded" />
                          <Skeleton className="h-[14px] w-[15%] rounded" />
                        </div>
                      ))}
                    </div>
                  ) : loadError ? (
                    <div className="flex flex-col items-center gap-[10px] py-[40px] text-[#858b9c]">
                      <p className="text-[12px]">{loadError}</p>
                      <Button variant="outline" size="sm" onClick={() => loadItems('installed', query)}>
                        重试
                      </Button>
                    </div>
                  ) : items.length === 0 ? (
                    <div className="flex flex-col items-center justify-center gap-[12px] py-[60px] text-[#858b9c]">
                      <Package className="size-[32px] text-[#d4d9e3]" />
                      <p className="text-[12px]">
                        {trimmedQuery ? '未找到匹配的已安装技能' : '暂未安装任何技能，可从市场中挑选添加'}
                      </p>
                      {!trimmedQuery && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setSource('all')}
                          className="h-[30px] rounded-[8px] text-[12px]"
                        >
                          浏览市场技能
                        </Button>
                      )}
                    </div>
                  ) : (
                    <Table className="border-separate border-spacing-y-[6px] px-[2px]">
                      <TableHeader className="sticky top-0 z-10 bg-white">
                        <TableRow className="border-none hover:bg-transparent">
                          <TableHead className="w-[190px] border-none text-[12px] text-[#757f9c]">技能名称</TableHead>
                          <TableHead className="border-none text-[12px] text-[#757f9c]">描述</TableHead>
                          <TableHead className="w-[85px] border-none text-[12px] text-[#757f9c]">状态</TableHead>
                          <TableHead className="w-[85px] border-none text-[12px] text-[#757f9c]">作用域</TableHead>
                          <TableHead className="w-[130px] border-none text-[12px] text-[#757f9c]">更新时间</TableHead>
                          <TableHead className="w-[110px] border-none text-right text-[12px] text-[#757f9c]">操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {items.map((item) => {
                          const statusPreset =
                            STATUS_BADGE_MAP[item.status || 'published'] || STATUS_BADGE_MAP.published;
                          const isSelected = selected === item.slug;
                          return (
                            <TableRow
                              key={item.slug}
                              onClick={() => selectSlug(item.slug)}
                              className={cn(
                                'group cursor-pointer bg-white transition-colors hover:bg-white',
                                isSelected
                                  ? '[&>td]:border-[#18181a]'
                                  : '[&>td]:border-[#eef0f4] hover:[&>td]:border-[#cbd3e6]',
                              )}
                            >
                              <TableCell className="rounded-l-[12px] border-y border-l bg-white py-[10px]">
                                <div className="flex items-center gap-[10px]">
                                  {item.icon_url ? (
                                    <img
                                      src={item.icon_url}
                                      alt=""
                                      loading="lazy"
                                      className="size-[30px] shrink-0 rounded-[8px] object-cover"
                                    />
                                  ) : (
                                    <div className="flex size-[30px] shrink-0 items-center justify-center rounded-[8px] bg-[#f3f4f7] text-[#a3aabf]">
                                      <Package className="size-[15px]" />
                                    </div>
                                  )}
                                  <div className="flex min-w-0 flex-col">
                                    <div className="flex items-center gap-[4px]">
                                      <span className="truncate text-[13px] font-medium text-[#18181a]">
                                        {item.name}
                                      </span>
                                      {item.version && (
                                        <span className="text-[10px] text-[#a3aabf]">v{item.version}</span>
                                      )}
                                    </div>
                                    <span className="truncate text-[11px] text-[#858b9c]">{item.slug}</span>
                                  </div>
                                </div>
                              </TableCell>
                              <TableCell className="max-w-[220px] border-y bg-white py-[10px]">
                                <p className="line-clamp-2 text-[12px] leading-[1.45] text-[#757f9c]">
                                  {item.description || '-'}
                                </p>
                              </TableCell>
                              <TableCell className="border-y bg-white py-[10px]">
                                <StatusBadge tone={statusPreset.tone}>{statusPreset.text}</StatusBadge>
                              </TableCell>
                              <TableCell className="border-y bg-white py-[10px]">
                                <CapabilityScopeBadge value={item.capability_scope} />
                              </TableCell>
                              <TableCell className="border-y bg-white py-[10px] text-[11px] text-[#858b9c]">
                                <span>{item.files_count || 1} 个文件</span>
                                <div className="text-[10px] text-[#a3aabf]">
                                  {formatDateTime(item.updated_at)}
                                </div>
                              </TableCell>
                              <TableCell className="rounded-r-[12px] border-y border-r bg-white py-[10px] text-right">
                                <div
                                  className="flex items-center justify-end gap-[2px]"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    title="查看详情"
                                    onClick={() => selectSlug(item.slug)}
                                    className="h-[28px] w-[28px] p-0 text-[#757f9c] hover:bg-[#f0f2f6] hover:text-[#18181a]"
                                  >
                                    <Eye className="size-[14px]" />
                                  </Button>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    title="去编辑"
                                    onClick={() => {
                                      onOpenChange(false);
                                      const scopeQuery = !agentId ? '?scope=gallery' : '';
                                      navigate(`/enterprise/general-skills/${encodeURIComponent(item.slug)}/edit${scopeQuery}`);
                                    }}
                                    className="h-[28px] w-[28px] p-0 text-[#757f9c] hover:bg-[#f0f2f6] hover:text-[#18181a]"
                                  >
                                    <ExternalLink className="size-[14px]" />
                                  </Button>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    title="卸载"
                                    onClick={() => setUninstallTarget(item)}
                                    className="h-[28px] w-[28px] p-0 text-[#757f9c] hover:bg-[#fee2e2] hover:text-[#ef4444]"
                                  >
                                    <Trash2 className="size-[14px]" />
                                  </Button>
                                </div>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  )}
                </ScrollArea>
              ) : (
                /* 市场卡片列表视图 */
                <ScrollArea className="min-h-0 flex-1">
                  <div className="flex flex-col gap-[8px] pr-[8px]">
                    {loading ? (
                      Array.from({ length: 6 }).map((_, index) => (
                        <div
                          key={`skeleton-${index}`}
                          className="flex items-center gap-[12px] rounded-[12px] border border-[#eef0f4] p-[12px]"
                        >
                          <Skeleton className="size-[38px] shrink-0 rounded-[10px]" />
                          <div className="flex flex-1 flex-col gap-[8px]">
                            <Skeleton className="h-[12px] w-[40%] rounded" />
                            <Skeleton className="h-[10px] w-[80%] rounded" />
                          </div>
                        </div>
                      ))
                    ) : loadError ? (
                      <div className="flex flex-col items-center gap-[10px] py-[40px] text-[#858b9c]">
                        <p className="text-[12px]">{loadError}</p>
                        <Button variant="outline" size="sm" onClick={() => loadItems(effectiveSource, query)}>
                          重试
                        </Button>
                      </div>
                    ) : items.length === 0 ? (
                      <div className="py-[40px] text-center text-[12px] text-[#858b9c]">
                        没有匹配的技能
                      </div>
                    ) : (
                      items.map((item) => (
                        <button
                          type="button"
                          key={item.slug}
                          onClick={() => selectSlug(item.slug)}
                          className={cn(
                            'flex items-start gap-[12px] rounded-[12px] border bg-white p-[12px] text-left transition-colors',
                            selected === item.slug
                              ? 'border-[#18181a] ring-1 ring-[#18181a]'
                              : 'border-[#eef0f4] hover:border-[#cbd3e6]',
                          )}
                        >
                          {item.icon_url ? (
                            <img
                              src={item.icon_url}
                              alt=""
                              loading="lazy"
                              className="mt-[2px] size-[38px] shrink-0 rounded-[10px] object-cover"
                            />
                          ) : (
                            <div className="mt-[2px] flex size-[38px] shrink-0 items-center justify-center rounded-[10px] bg-[#f3f4f7] text-[#a3aabf]">
                              <Package className="size-[18px]" />
                            </div>
                          )}
                          <div className="flex min-w-0 flex-1 flex-col gap-[4px]">
                            <div className="flex items-center gap-[6px]">
                              <span className="truncate text-[13px] font-medium text-[#18181a]">
                                {item.name}
                              </span>
                              {item.installed && (
                                <Badge className="shrink-0 rounded-[6px] bg-[#18181a] px-[6px] py-[1px] text-[10px] font-normal text-white">
                                  已添加
                                </Badge>
                              )}
                            </div>
                            {item.description && (
                              <p className="line-clamp-2 text-[11px] leading-[1.5] text-[#757f9c]">
                                {item.description}
                              </p>
                            )}
                            <div className="mt-[2px] flex items-center gap-[10px] text-[11px] text-[#a3aabf]">
                              {item.origin && (
                                <span>{ORIGIN_LABEL_MAP[item.origin] || item.origin}</span>
                              )}
                              {item.category && <span className="text-[#c0c6d4]">· {item.category}</span>}
                            </div>
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                </ScrollArea>
              )}
            </div>

            {/* 右侧详情抽屉 */}
            <div className="hidden w-[340px] shrink-0 flex-col overflow-hidden rounded-[14px] border border-[#eef0f4] bg-white sm:flex">
              {previewLoading ? (
                <div className="flex flex-col gap-[12px] p-[16px]">
                  <Skeleton className="h-[14px] w-[50%] rounded" />
                  <Skeleton className="h-[10px] w-[30%] rounded" />
                  {Array.from({ length: 6 }).map((_, index) => (
                    <Skeleton key={index} className="h-[10px] w-full rounded" />
                  ))}
                </div>
              ) : preview ? (
                <>
                  <div className="flex flex-col gap-[6px] border-b border-[#eef0f4] p-[16px]">
                    <div className="flex items-center justify-between gap-[8px]">
                      <span className="truncate text-[14px] font-medium text-[#18181a]">{preview.name}</span>
                      <button
                        type="button"
                        onClick={() => setSelected('')}
                        title="收起详情"
                        className="rounded p-[2px] text-[#a3aabf] hover:text-[#18181a]"
                      >
                        <X className="size-[14px]" />
                      </button>
                    </div>
                    {preview.description && (
                      <p className="line-clamp-2 text-[11px] leading-[1.5] text-[#757f9c]">
                        {preview.description}
                      </p>
                    )}
                    <p className="mt-[2px] text-[11px] text-[#a3aabf]">
                      {preview.files.length} 个文件 · {preview.slug}
                    </p>
                    <div className="mt-[8px] flex items-center gap-[8px]">
                      {items.find((item) => item.slug === preview.slug)?.installed || isInstalledTab ? (
                        <>
                          <Badge className="h-[30px] rounded-[8px] bg-[#f3f4f7] px-[12px] text-[12px] font-normal text-[#757f9c]">
                            已安装
                          </Badge>
                          <Button
                            variant="outline"
                            onClick={() => {
                              onOpenChange(false);
                              const scopeQuery = !agentId ? '?scope=gallery' : '';
                              navigate(`/enterprise/general-skills/${encodeURIComponent(preview.slug)}/edit${scopeQuery}`);
                            }}
                            className="h-[30px] gap-[4px] rounded-[8px] border-[#e3e7f1] px-[12px] text-[12px] text-[#18181a] hover:bg-[#f6f6f6]"
                          >
                            <ExternalLink className="size-[12px]" />
                            去编辑
                          </Button>
                        </>
                      ) : (
                        <Button
                          disabled={installing}
                          onClick={() => void install(preview.slug)}
                          className="h-[32px] gap-[6px] rounded-[10px] bg-[#18181a] px-[16px] text-[13px] font-normal text-white hover:bg-[#303030]"
                        >
                          <Download className="size-[14px]" />
                          添加到企业
                        </Button>
                      )}
                    </div>
                  </div>
                  <ScrollArea className="min-h-0 flex-1">
                    <div className="prose-sm max-w-none p-[16px] text-[12px] leading-[1.7] text-[#3a4050]">
                      {renderMarkdownBlocks(preview.markdown)}
                    </div>
                  </ScrollArea>
                </>
              ) : (
                <div className="flex flex-1 flex-col items-center justify-center gap-[8px] p-[16px] text-center text-[12px] text-[#858b9c]">
                  <Package className="size-[28px] text-[#d4d9e3]" />
                  <span>点击左侧技能查看详情</span>
                </div>
              )}
            </div>
          </div>

          <div className="px-[12px]">
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              className="h-[32px] w-full rounded-[10px] border-[#e3e7f1] bg-white text-[13px] font-normal text-[#464c5e] hover:bg-[#f6f6f6] sm:w-auto sm:px-[24px]"
            >
              关闭
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 卸载二次确认弹窗 */}
      <ConfirmDialog
        open={Boolean(uninstallTarget)}
        onOpenChange={(open) => !open && setUninstallTarget(null)}
        loading={uninstalling}
        title={uninstallTarget ? `确认卸载技能「${uninstallTarget.name}」？` : ''}
        description="卸载后该技能将从企业技能库中移除，依赖该技能的 SOP 节点可能受到影响。"
        confirmText="卸载"
        onConfirm={() => void confirmUninstall()}
      />
    </>
  );
}