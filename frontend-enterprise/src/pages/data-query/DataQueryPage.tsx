import { useState } from 'react';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Database, FileCode2 } from 'lucide-react';
import AppHeader from '@/components/AppHeader';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { EnterpriseRoute } from '@/enums/routes';
import DataSourceList from './DataSourceList';
import QueryTemplateList from './QueryTemplateList';
import type { EnterpriseAuthUser } from '@/auth';

type TabValue = 'data-sources' | 'query-templates';

type DataQueryPageProps = {
  currentUser: EnterpriseAuthUser;
  onLogout: () => void;
};

export default function DataQueryPage({}: DataQueryPageProps) {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const tabFromUrl = (searchParams.get('tab') as TabValue) || 'data-sources';
  const [activeTab, setActiveTab] = useState<TabValue>(tabFromUrl);

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
