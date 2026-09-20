import { useEffect, useState } from 'react';

import { Switch } from '@/components/ui';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Database, FileCode2 } from 'lucide-react';
import AppHeader from '@/components/AppHeader';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { EnterpriseRoute } from '@/enums/routes';
import { api, TENANT_ID } from '@/api/client';
import { isEnterpriseAdmin } from '@/auth';
import type { UIConfigRead } from '@/types';
import DataSourceList from './DataSourceList';
import QueryTemplateList from './QueryTemplateList';
import type { EnterpriseAuthUser } from '@/auth';

type TabValue = 'data-sources' | 'query-templates';

type DataQueryPageProps = {
  currentUser: EnterpriseAuthUser;
  onLogout: () => void;
};

export default function DataQueryPage({ currentUser }: DataQueryPageProps) {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const tabFromUrl = (searchParams.get('tab') as TabValue) || 'data-sources';
  const [activeTab, setActiveTab] = useState<TabValue>(tabFromUrl);
  const [grantAll, setGrantAll] = useState<boolean | null>(null);
  const isAdmin = isEnterpriseAdmin(currentUser);

  useEffect(() => {
    let cancelled = false;
    api
      .get<UIConfigRead>(`/api/enterprise/ui-config?tenant_id=${TENANT_ID}`)
      .then((row) => {
        if (!cancelled) setGrantAll(row.data_query_grant_all === true);
      })
      .catch(() => {
        if (!cancelled) setGrantAll(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleGrantAllChange = async (next: boolean) => {
    const prev = grantAll;
    setGrantAll(next);
    try {
      await api.put<UIConfigRead>('/api/enterprise/ui-config', {
        tenant_id: TENANT_ID,
        data_query_grant_all: next,
      });
    } catch {
      setGrantAll(prev);
    }
  };

  const handleTabChange = (value: string) => {
    const tab = value as TabValue;
    setActiveTab(tab);
    navigate(`${EnterpriseRoute.DataQuery}?tab=${tab}`, { replace: true });
  };

  return (
    <div className="flex h-full flex-col">
      <AppHeader
        title="数据查询中心"
        description="管理数据源与查询模板，让数字员工可以直接调用业务数据。"
      />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mb-6 flex items-center justify-between gap-4 rounded-[14px] border-[0.5px] border-[#e3e7f1] bg-white px-[16px] py-[12px]">
          <div className="flex flex-col gap-[2px]">
            <span className="text-[13px] font-medium text-[#18181a]">默认授权所有员工</span>
            <span className="text-[11px] text-muted-foreground">
              {grantAll
                ? '全部员工默认可查询所有启用的数据源，员工档案中的勾选表示排除。'
                : '按员工档案勾选白名单授权。'}
            </span>
          </div>
          <Switch
            checked={grantAll === true}
            disabled={!isAdmin || grantAll === null}
            aria-label="默认授权所有员工"
            onCheckedChange={(next) => void handleGrantAllChange(next)}
          />
        </div>
        <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
          <TabsList>
            <TabsTrigger value="data-sources" className="flex items-center gap-2">
              <Database size={16} />
              数据源
            </TabsTrigger>
            <TabsTrigger value="query-templates" className="flex items-center gap-2">
              <FileCode2 size={16} />
              查询模板
            </TabsTrigger>
          </TabsList>

          <TabsContent value="data-sources" className="mt-6">
            <DataSourceList />
          </TabsContent>

          <TabsContent value="query-templates" className="mt-6">
            <QueryTemplateList />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
