"""独立 stdio MCP 适配器；固定只读取数并运行技能，不修改平台源码。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import uuid
from datetime import date
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]
TOOL = 'ecommerce_sales_preview'
SCHEMA = {'type': 'object', 'properties': {'date': {'type': 'string',
          'description': '业务日期，严格YYYY-MM-DD；默认由调用者按北京时间确定'}},
          'required': ['date'], 'additionalProperties': False}
# 平台MCP工具超时为240秒；适配器必须更早给出结论，避免外层先超时后
# 平台把调用记为outcome_unknown并锁死同帧重试。
PREVIEW_DEADLINE_SECONDS = 185.0
PUBLISH_GRACE_SECONDS = 25.0


class PreviewFailure(RuntimeError):
    """可结构化返回给模型的业务失败，走正常工具结果而不是JSON-RPC错误。"""

    def __init__(self, stage: str, message: str, *, status: str = 'error',
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.stage = stage
        self.status = status
        self.retryable = retryable
        self.message = message


class _Journal:
    """记录当前阶段；发布前若已被调用方取消则中止，避免半成品交付。"""

    def __init__(self) -> None:
        self.stage = 'prepare'
        self.cancelled = False

    def enter(self, stage: str) -> None:
        self.stage = stage
        self.check()

    def check(self) -> None:
        if self.cancelled:
            raise PreviewFailure(self.stage, '预览在完成前被超时取消，未发布报告',
                                 status='timeout', retryable=True)


def validate_arguments(arguments: dict) -> str:
    if not isinstance(arguments, dict) or set(arguments) != {'date'}:
        raise ValueError('只允许date参数；本工具没有发送或文件路径参数')
    value = arguments['date']
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('日期必须为YYYY-MM-DD')
    return value


def checked_template_id(tool: dict, expected: str) -> str:
    """在取数前校验ToolExecutor实际会执行的模板，拒绝HTTP/MCP重定向。"""
    config, url = tool['config'], tool['url']
    is_query = (tool['type'] == 'data_query' or config.get('source') == 'data_query'
                or '/api/mock/data-query/' in url)
    if not is_query or tool['type'] in ('mcp', 'a2a'):
        raise ValueError('配置的查询工具不是纯数据查询工具')
    actual = str(config.get('template_id') or '').strip()
    if not actual and '/api/mock/data-query/' in url:
        actual = url.rsplit('/api/mock/data-query/', 1)[-1].split('?')[0].strip('/')
    if actual != expected:
        raise ValueError('查询工具的实际模板与技能配置不一致，已在取数前拒绝')
    return actual


def validate_template_contract(template, contract: dict, tenant: str):
    if not template or template.tenant_id != tenant or template.status != 'active' or {
        'id': template.id, 'source': template.data_source_id,
        'sql': template.query_content, 'params': template.params_json,
        'max_rows': template.max_rows} != contract:
        raise ValueError('查询口径或启用状态在执行期间发生变化，请重新预览')


def preview(day: str, journal: _Journal | None = None) -> dict:
    """受信宿主适配：凭证留在平台服务内，原始行不经模型传递。"""
    journal = journal or _Journal()
    journal.enter('prepare')
    os.chdir(REPO / 'backend')
    sys.path.insert(0, str(REPO / 'backend'))
    sys.path.insert(0, str(REPO / 'backend/src'))
    from staffdeck_harness.distribution.bootstrap import configure_environment
    configure_environment()
    from app.capabilities.local_general_skill import package_from_row
    from app.core.harness_capability_invoker import _materialize_general_skill_package
    from app.data_query.authorization import authorized_data_source_ids
    from app.data_query.models import QueryTemplate
    from app.db.database import engine
    from app.db.models import AgentProfile, GeneralSkill, Tool, UIConfig
    from app.harness.contracts import HarnessToolContext
    from app.harness.skill_script import RunSkillScriptArguments, run_skill_script
    from app.tools.tool_executor import ToolExecutor
    from app.tools.tool_schema import ToolCall
    from sqlmodel import Session

    record = json.loads((PACKAGE / 'state/registration.json').read_text('utf-8'))
    tenant, agent_id = record['tenant_id'], record['agent_id']
    with Session(engine) as db:
        agent = db.get(AgentProfile, agent_id)
        if (not agent or agent.tenant_id != tenant or agent.status != 'active'
                or not agent.metadata_json.get('test_only')):
            raise ValueError('测试员工已失效或不是独立测试副本')
        general_skill = db.get(GeneralSkill, record['skill_id'])
        if not general_skill or general_skill.tenant_id != tenant or general_skill.status != 'published':
            raise ValueError('测试技能不可用')
        package = package_from_row(general_skill)
        from app.db.models import AgentResourceBinding
        from sqlmodel import select
        bound = db.exec(select(AgentResourceBinding).where(
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.resource_type == 'general_skill',
            AgentResourceBinding.resource_id == general_skill.id,
            AgentResourceBinding.status == 'active')).first()
        if not bound:
            raise ValueError('测试员工的技能授权已撤销')
        from history import PreviewHistory
        from platform_delivery import (
            input_fingerprint,
            publish,
            report_workspace,
            reusable,
        )
        frame_id = 'task_sales_preview_' + uuid.uuid4().hex
        workspace = report_workspace(db, record, frame_id)
        root, files = _materialize_general_skill_package(workspace, package)
        config = json.loads((workspace / root / 'config.json').read_text('utf-8'))
        sources = authorized_data_source_ids(db, tenant, agent_id)
        queries = [('summary', config['summary_template_id'], 'query_realtime_sales_summary',
                    {'params': {'end_date': day}}),
                   ('dabo', config['dabo_template_id'], 'query_dabo_realtime_summary',
                    {'start_date': day, 'end_date': day})]
        contracts = []
        for label, template_id, tool_name, params in queries:
            journal.enter(f'{label}_query')
            query_tool = db.exec(select(Tool).where(Tool.tenant_id == tenant,
                                                    Tool.name == tool_name)).first()
            if query_tool is None or not query_tool.enabled:
                raise ValueError('数据查询工具已停用')
            actual = checked_template_id({'type': query_tool.tool_type,
                'config': query_tool.config_json or {}, 'url': query_tool.url or ''}, template_id)
            template = db.get(QueryTemplate, actual)
            if not template or template.tenant_id != tenant or template.status != 'active' or template.data_source_id not in sources:
                raise ValueError('查询模板不在测试员工的数据授权范围')
            contracts.append({'id': actual, 'source': template.data_source_id,
                'sql': template.query_content, 'params': template.params_json,
                'max_rows': template.max_rows})
            response = ToolExecutor(db).execute(tenant, ToolCall(name=tool_name, arguments=params),
                                                 agent_id=agent_id)
            if not response.success:
                # 原始数据库错误可能包含连接细节，不向模型或普通日志回传。
                raise PreviewFailure(journal.stage,
                                     f'{label}查询失败，请在平台检查数据源与查询模板')
            (workspace / f'{label}.json').write_text(
                json.dumps(response.data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            journal.check()
        source_contract = hashlib.sha256(json.dumps({'package': package.digest, 'queries': contracts},
            sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        summary = json.loads((workspace / 'summary.json').read_text('utf-8'))
        dabo = json.loads((workspace / 'dabo.json').read_text('utf-8'))
        total = next(r for r in summary['rows'] if r.get('category') == '大盘' and r.get('item') == '电商整体')
        source_order = str(total.get('数据更新时间') or '')
        scope = '/'.join((tenant, agent_id, record['task_id'], day, source_contract))

        def revalidate():
            # 长查询结束后重新检查已撤销的员工、技能及数据权限。
            db.expire_all()
            current = db.get(AgentProfile, agent_id)
            current_skill = db.get(GeneralSkill, record['skill_id'])
            binding = db.exec(select(AgentResourceBinding).where(
                AgentResourceBinding.agent_id == agent_id,
                AgentResourceBinding.resource_id == record['skill_id'],
                AgentResourceBinding.resource_type == 'general_skill',
                AgentResourceBinding.status == 'active')).first()
            if not current or current.status != 'active' or not current_skill or not binding \
                    or current_skill.status != 'published' or package_from_row(current_skill).digest != package.digest:
                raise ValueError('员工或技能授权/版本已改变，未发布报告')
            if not {c['source'] for c in contracts} <= authorized_data_source_ids(db, tenant, agent_id):
                raise ValueError('数据源授权已撤销，未发布报告')
            for (_, template_id, tool_name, _), contract in zip(queries, contracts, strict=True):
                tool = db.exec(select(Tool).where(Tool.tenant_id == tenant,
                                                  Tool.name == tool_name)).first()
                bound_tool = tool and db.exec(select(AgentResourceBinding).where(
                    AgentResourceBinding.agent_id == agent_id,
                    AgentResourceBinding.resource_type == 'tool',
                    AgentResourceBinding.resource_id == tool.id,
                    AgentResourceBinding.status == 'active')).first()
                if not tool or not tool.enabled or not bound_tool:
                    raise ValueError('查询工具授权已改变，未发布报告')
                checked_template_id({'type': tool.tool_type, 'config': tool.config_json or {},
                                     'url': tool.url or ''}, template_id)
                template = db.get(QueryTemplate, template_id)
                validate_template_contract(template, contract, tenant)

        def generate(baseline):
            revalidate()
            argv = ['--summary', 'summary.json', '--dabo', 'dabo.json', '--date', day,
                    '--task-id', record['task_id'], '--source-contract', source_contract]
            if baseline:
                (workspace / 'baseline.json').write_text(json.dumps(baseline, ensure_ascii=False), encoding='utf-8')
                argv += ['--baseline', 'baseline.json']
            ui = db.get(UIConfig, tenant)
            script = next(p for p in files if p.endswith('/scripts/run.py'))
            journal.enter('script')
            execution = run_skill_script(HarnessToolContext(
                run_id='sales-skill-' + workspace.name, task_frame_id=frame_id,
                tenant_id=tenant, scheduled_task_id=record['task_id'], workspace_root=workspace,
                sandbox_enabled=bool(ui and ui.sandbox_enabled), sandbox_network_mode='deny'),
                RunSkillScriptArguments(script_path=script, argv=argv, timeout_seconds=120))
            if not execution.get('ok'):
                raise PreviewFailure('script', '技能脚本执行失败，未生成成功报告')
            snapshot = json.loads((workspace / 'reports/ecommerce-sales-test/snapshot.json').read_text('utf-8'))
            revalidate()
            journal.enter('publish')
            publication = publish(db, record, frame_id, workspace, snapshot)
            result = {'status': 'success', 'published': True, 'push_status': 'disabled', 'date': day,
                'sources': snapshot['sources'], 'kpis': snapshot['kpis'], 'delta': snapshot['delta'],
                'baseline_at': snapshot['baseline_at'], 'publication': publication,
                'section_counts': {k: len(v) for k, v in snapshot['sections'].items()},
                'report_path': str(workspace / 'reports/ecommerce-sales-test/dashboard.html'),
                'snapshot_path': str(workspace / 'reports/ecommerce-sales-test/snapshot.json'),
                'execution': {'exit_code': execution['exit_code'], 'status': execution['status'],
                              'isolation_mode': execution.get('isolation_mode')},
                'message': '完整技能报告已发布；差值仅比较成功预览，未发送消息。'}
            return snapshot, result

        revalidate()
        result = PreviewHistory(PACKAGE / 'state/preview-history.sqlite').generate(
            scope, input_fingerprint(summary, dabo), source_order, generate,
            reusable=lambda old: reusable(db, record, old))
        (PACKAGE / 'state/adapter-result.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result


def _failure_payload(status: str, stage: str, message: str, *, retryable: bool) -> dict:
    return {'status': status, 'stage': stage, 'retryable': retryable, 'message': message}


def call_preview(day: str) -> dict:
    """在后台线程执行预览；超过截止时间先返回结构化结论，避免外层MCP超时。"""
    journal = _Journal()
    outcome: dict = {}

    def work() -> None:
        try:
            outcome['result'] = preview(day, journal)
        except BaseException as exc:  # noqa: BLE001 -- 线程边界统一转为结构化结果
            outcome['error'] = exc

    worker = threading.Thread(target=work, name='ecommerce-sales-preview', daemon=True)
    worker.start()
    worker.join(PREVIEW_DEADLINE_SECONDS)
    if worker.is_alive() and journal.stage == 'publish':
        # 已进入发布阶段则留出收尾时间，避免在写库中途被取消。
        worker.join(PUBLISH_GRACE_SECONDS)
    if worker.is_alive():
        journal.cancelled = True
        return _failure_payload(
            'timeout', journal.stage,
            f'预览在 {PREVIEW_DEADLINE_SECONDS:g} 秒内未完成，停在该阶段；未发布报告，可重试一次。',
            retryable=True)
    error = outcome.get('error')
    if isinstance(error, PreviewFailure):
        return _failure_payload(error.status, error.stage, error.message,
                                retryable=error.retryable)
    if isinstance(error, ValueError):
        return _failure_payload('error', journal.stage, str(error), retryable=False)
    if error is not None:
        return _failure_payload('error', journal.stage, '技能适配器内部错误，未发布报告',
                                retryable=False)
    return outcome['result']


def dispatch(method: str, params: dict) -> dict:
    if method == 'initialize':
        return {'protocolVersion': params.get('protocolVersion', '2024-11-05'),
                'capabilities': {'tools': {}}, 'serverInfo': {'name': 'ecommerce-sales-skill', 'version': '2.1.0'}}
    if method == 'tools/list':
        return {'tools': [{'name': TOOL, 'description': '销售技能测试：按日期只读取数、确定性计算并生成报告；禁止发送。',
                           'inputSchema': SCHEMA,
                           'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}}]}
    if method == 'tools/call':
        if params.get('name') != TOOL:
            raise ValueError('未知工具')
        try:
            day = validate_arguments(params.get('arguments', {}))
        except ValueError as exc:
            result = _failure_payload('invalid_arguments', 'validate', str(exc), retryable=False)
        else:
            result = call_preview(day)
        return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
                'structuredContent': result, 'isError': False}
    if method == 'ping':
        return {}
    raise ValueError('不支持的方法')


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        response = {'jsonrpc': '2.0', 'id': request['id']}
        try:
            response['result'] = dispatch(request.get('method'), request.get('params', {}))
        except Exception as exc:  # noqa: BLE001 -- 协议边界隐藏宿主连接与凭证诊断细节
            message = str(exc) if isinstance(exc, ValueError) else '技能适配器执行失败，请检查本机测试配置'
            response['error'] = {'code': -32000, 'message': message}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
