"""消费平台授权查询结果的销售技能：仅计算和生成文件，无网络及发送能力。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Any

PACKAGE = Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))


def amount(value: Any) -> Decimal:
    """拒绝缺失和非有限金额，允许退款引起的负数。"""
    if value is None or isinstance(value, bool):
        raise ValueError('金额不能为空或布尔值')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('非法金额') from exc
    if not result.is_finite():
        raise ValueError('金额必须为有限数')
    return result


def rounded(value: Decimal) -> float:
    return float(value.quantize(Decimal('0.01')))


def payload_rows(payload: dict, template_id: str, limit: int) -> list[dict]:
    if not isinstance(payload, dict) or payload.get('success') is False:
        raise ValueError('查询结果失败或格式错误')
    if 'data' in payload:
        payload = payload['data']
    if not isinstance(payload, dict) or payload.get('template_id') != template_id:
        raise ValueError('查询模板不匹配')
    rows = payload.get('rows')
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise ValueError('查询结果为空或行格式错误')
    if payload.get('truncated') or int(payload.get('row_count', len(rows))) >= limit:
        raise ValueError('查询可能被截断，拒绝生成完整性不明的报告')
    if int(payload.get('row_count', len(rows))) != len(rows):
        raise ValueError('返回行数与明细不一致')
    return rows


def build_snapshot(summary: dict, dabo: dict, config: dict, *, as_of: str,
                   task_id: str, baseline: dict | None = None,
                   query_date: str | None = None, source_contract: str = '') -> dict:
    generated = datetime.fromisoformat(as_of)
    if generated.tzinfo is None:
        raise ValueError('生成时间必须包含时区')
    rows = payload_rows(summary, config['summary_template_id'], config['summary_row_limit'])
    live = payload_rows(dabo, config['dabo_template_id'], config['dabo_row_limit'])
    totals = [r for r in rows if r.get('category') == '大盘' and r.get('item') == '电商整体']
    if len(totals) != 1:
        raise ValueError('缺少唯一的电商整体汇总行')
    total = totals[0]
    raw_kpis = {key: amount(total.get(column)) for key, column in (
        ('net', '净销_万'), ('ops', '运营净销_万'), ('live', '达播净销_万'))}
    profile_digest = hashlib.sha256(json.dumps(
        config, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    day = date.fromisoformat(query_date or generated.astimezone(CST).date().isoformat()).isoformat()
    if day > generated.astimezone(CST).date().isoformat():
        raise ValueError('不支持未来业务日期')
    for row in live:
        source_day = str(row.get('日期') or '')
        if source_day and any(value != day for value in source_day.split('~')):
            raise ValueError('达播数据日期与所选业务日期不一致')
    delta = None
    if baseline is not None:
        checks = {'date': day, 'task_id': task_id, 'profile_id': config['profile_id'],
                  'source_contract': source_contract,
                  'config_digest': profile_digest}
        if any(baseline.get(key) != value for key, value in checks.items()):
            raise ValueError('比较基准的日期、任务或口径不同')
        old_time = datetime.fromisoformat(baseline['generated_at'])
        if old_time.tzinfo is None or old_time > generated:
            raise ValueError('比较基准时间无效或晚于当前快照')
        delta = {key: rounded(value - amount(baseline['kpis'][key]))
                 for key, value in raw_kpis.items()}
    aliases = config.get('influencer_aliases', {})
    influencers: dict[str, dict] = {}
    for row in live:
        if row.get('统计维度') != '3_达人TOP18':
            continue
        name = str(row.get('达人') or '').strip()
        name = aliases.get(name, name)
        if name in ('', '-', '通用', '未知', '其他'):
            continue
        entry = influencers.setdefault(name, {'name': name, 'net': Decimal(0),
                                              'gmv': Decimal(0)})
        entry['net'] += amount(row.get('净销')) / 10000
        entry['gmv'] += amount(row.get('成交金额')) / 10000
    ranked = sorted(influencers.values(), key=lambda r: (-r['net'], r['name']))
    ranked = [{'name': r['name'], 'net': rounded(r['net']), 'gmv': rounded(r['gmv'])}
              for r in ranked[:int(config.get('top_n', 5))]]
    snapshot = {
        'schema_version': 1, 'profile_id': config['profile_id'],
        'config_digest': profile_digest, 'task_id': task_id, 'date': day,
        'title': config['title'], 'generated_at': as_of,
        'source_updated_at': str(total.get('数据更新时间') or '上游未提供'),
        'kpis': {key: rounded(value) for key, value in raw_kpis.items()}, 'delta': delta,
        'source_contract': source_contract,
        'baseline_kind': 'successful_preview' if baseline else 'none',
        'baseline_at': baseline['generated_at'] if baseline else None,
        'push_status': 'disabled', 'influencers': ranked,
        'scope': '达人排名仅覆盖查询模板返回范围，源模板先截取TOP18再归并，非全量排名。',
        'sources': [{'template_id': config['summary_template_id'], 'rows': len(rows)},
                    {'template_id': config['dabo_template_id'], 'rows': len(live)}],
        'summary_rows': rows, 'dabo_rows': live,
    }
    snapshot['sections'] = build_sections(snapshot, config, baseline)
    return snapshot


def build_sections(snapshot: dict, config: dict, baseline: dict | None) -> dict:
    """基于原始模板分区，保持小时、店铺快照差值和预览差值的语义分离。"""
    result = {k: [] for k in ('brands', 'channels', 'positive_shops', 'negative_shops',
                              'hourly', 'platforms', 'products')}
    previous = {(r.get('category'), r.get('item')): r
                for r in (baseline or {}).get('summary_rows', [])}
    def optional(value):
        return None if value is None else rounded(amount(value))

    generated = datetime.fromisoformat(snapshot['generated_at']).astimezone(CST)
    cutoff = generated.hour if generated.date().isoformat() == snapshot['date'] else 23
    matched = re.fullmatch(r'(\d{2}-\d{2}) (\d{2}):\d{2}', snapshot['source_updated_at'])
    if matched and snapshot['date'].endswith(matched[1]):
        cutoff = min(cutoff, int(matched[2]))
    hourly_fields = config.get('hourly_fields', {'today': '净销_万', 'yesterday': '运营增量_万'})
    for row in snapshot['summary_rows']:
        category, item = row.get('category'), row.get('item')
        if not category or category == '大盘':
            continue
        if category in ('24小时环比', 'hourly_trend'):
            matched_hour = re.fullmatch(r'(\d{1,2})(?:时)?', str(item or ''))
            if not matched_hour or not 0 <= int(matched_hour[1]) <= 23:
                raise ValueError('小时走势图包含非法时段')
            hour = int(matched_hour[1])
            result['hourly'].append({'hour': hour,
                'today': optional(row.get(hourly_fields['today'])) if hour <= cutoff else None,
                'yesterday': optional(row.get(hourly_fields['yesterday']))})
            continue
        entry = {'name': str(category if item == '整体' else item or category),
                 'category': category, 'net': optional(row.get('净销_万')),
                 'ops': optional(row.get('运营净销_万')), 'live': optional(row.get('达播净销_万')),
                 'source_delta': optional(row.get('环比增量_万')), 'preview_delta': None}
        old = previous.get((category, item))
        if old and old.get('净销_万') is not None and entry['net'] is not None:
            entry['preview_delta'] = rounded(amount(row['净销_万']) - amount(old['净销_万']))
        if category in ('店铺正增量Top5', '正增量Top5'):
            result['positive_shops'].append(entry)
        elif category in ('店铺负增量Top5', '负增量Top5'):
            result['negative_shops'].append(entry)
        elif item == '整体' or category == '其他品牌':
            result['brands'].append(entry)
        else:
            result['channels'].append(entry)
    order = {brand: i for i, brand in enumerate(config.get('brand_order', []))}
    result['brands'].sort(key=lambda r: (order.get(r['name'], 999), r['name']))
    result['hourly'].sort(key=lambda r: r['hour'])
    if len({r['hour'] for r in result['hourly']}) != len(result['hourly']):
        raise ValueError('小时走势图包含重复时段')
    for row in snapshot['dabo_rows']:
        dimension = row.get('统计维度')
        if dimension not in ('1_平台汇总', '4_商品TOP18'):
            continue
        if not row.get('平台' if dimension == '1_平台汇总' else '商品名称'):
            continue
        entry = {'name': str(row.get('平台') if dimension == '1_平台汇总' else row.get('商品名称')),
                 'platform': str(row.get('平台') or ''), 'brand': str(row.get('品牌') or ''),
                 'shop': str(row.get('平台店铺') or ''), 'author': str(row.get('达人') or ''),
                 'gmv': rounded(amount(row['成交金额']) / 10000),
                 'net': rounded(amount(row['净销']) / 10000)}
        result['platforms' if dimension == '1_平台汇总' else 'products'].append(entry)
    result['products'] = sorted(result['products'], key=lambda r: (-r['gmv'], r['name']))[:config.get('top_n', 5)]
    return result


def render_html(snapshot: dict, template: str) -> str:
    def text(value: Any) -> str:
        return escape(str(value), quote=True)

    def fmt(value: Any) -> str:
        return f'{amount(value):,.2f}'

    kpis = ''.join(
        f'<div class="kpi"><span>{label}</span><strong>{fmt(snapshot["kpis"][key])}'
        f'<small> 万元</small></strong></div>'
        for key, label in [('net', '电商整体净销'), ('ops', '运营净销'), ('live', '达播净销')])
    ranking = ''.join(f'<tr><td>{text(r["name"])}</td><td>{fmt(r["net"])}</td>'
                      f'<td>{fmt(r["gmv"])}</td></tr>' for r in snapshot['influencers'])
    details = ''.join(
        f'<tr><td>{text(r.get("category", ""))}</td><td>{text(r.get("item", ""))}</td>'
        f'<td>{text(r.get("净销_万", "未提供"))}</td>'
        f'<td>{text(r.get("运营净销_万", "未提供"))}</td>'
        f'<td>{text(r.get("达播净销_万", "未提供"))}</td></tr>'
        for r in snapshot['summary_rows'] if r.get('category') not in ('24小时环比', 'hourly_trend'))
    delta = '无比较基准；本次未计算推送增量。'
    if snapshot['delta'] is not None:
        delta = f'较上次成功预览（{snapshot["baseline_at"]}）：' + '，'.join(
            f'{label} {snapshot["delta"][key]:+.2f} 万元'
            for key, label in [('net', '整体'), ('ops', '运营'), ('live', '达播')])
    def table(title, rows, columns, note=''):
        if not rows:
            return ''
        head = ''.join(f'<th>{text(label)}</th>' for _, label in columns)
        body = ''.join('<tr>' + ''.join(
            f'<td>{text("—" if row.get(key) is None else fmt(row[key]) if isinstance(row[key], (int, float)) else row[key])}</td>'
            for key, _ in columns) + '</tr>' for row in rows)
        return f'<section><h2>{text(title)}</h2><p class="note">{text(note)}</p><div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>'

    sections = snapshot['sections']
    metrics = [('name', '项目'), ('net', '净销（万元）'), ('ops', '运营净销'),
               ('live', '达播净销'), ('preview_delta', '较上次预览')]
    panels = table('品牌净销', sections['brands'], metrics)
    panels += table('品牌及渠道', sections['channels'], [('category', '品牌'), *metrics])
    for key, title in [('positive_shops', '店铺正增量榜'), ('negative_shops', '店铺负增量榜')]:
        panels += table(title, sections[key], [('name', '店铺'), ('net', '累计净销'),
            ('source_delta', '上游快照增量'), ('preview_delta', '较上次预览')],
            '仅覆盖查询模板返回范围；上游相邻快照增量与成功预览增量分别列示，单位万元。')
    panels += table('达播平台汇总', sections['platforms'], [('name', '平台'), ('brand', '品牌'),
                    ('gmv', '成交金额（万元）'), ('net', '净销（万元）')])
    panels += table('达人销售榜', snapshot['influencers'], [('name', '达人'), ('net', '净销（万元）'),
                    ('gmv', '成交金额（万元）')], snapshot['scope'])
    panels += table('商品销售榜', sections['products'], [('name', '商品'), ('platform', '平台'),
        ('author', '达人'), ('gmv', '成交金额（万元）'), ('net', '净销（万元）')],
        '按模板商品前18条中的成交金额排列，保留各平台及达人来源，不代表全量商品排名。')
    panels += render_hourly_chart(sections['hourly'])
    values = {'title': text(snapshot['title']), 'generated_at': text(snapshot['generated_at']),
              'date': text(snapshot['date']), 'panels': panels,
              'source_time': text(snapshot['source_updated_at']), 'kpis': kpis,
              'ranking': ranking, 'details': details, 'delta': text(delta),
              'scope': text(snapshot['scope'])}
    return re.sub(r'\{\{([a-z_]+)\}\}', lambda m: values[m.group(1)], template)


def render_hourly_chart(rows: list[dict]) -> str:
    if not rows:
        return ''
    values = [row[key] for row in rows for key in ('today', 'yesterday') if row[key] is not None]
    if not values:
        return ''
    low, high = min(0, min(values)), max(0, max(values))
    span = high - low or 1
    def point(hour, value):
        return f'{60 + hour * 820 / 23:.2f},{205 - (value - low) * 170 / span:.2f}'
    marks = ''
    for key, css in [('today', 'today'), ('yesterday', 'yesterday')]:
        segments, segment = [], []
        previous_hour = -2
        for row in rows:
            if row[key] is None or row['hour'] != previous_hour + 1:
                if segment:
                    segments.append(segment)
                segment = []
            if row[key] is not None:
                segment.append(point(row['hour'], row[key]))
            previous_hour = row['hour']
        if segment:
            segments.append(segment)
        marks += ''.join(f'<polyline class="{css}" points="{" ".join(segment)}"/>' for segment in segments)
        marks += ''.join(f'<circle class="{css}" cx="{point(row["hour"],row[key]).split(",")[0]}" cy="{point(row["hour"],row[key]).split(",")[1]}" r="2.5"/>'
                         for row in rows if row[key] is not None)
    labels = ''.join(f'<text x="{60 + h * 820 / 23:.2f}" y="230" text-anchor="middle">{h:02d}时</text>'
                     for h in (0, 6, 12, 18, 23))
    for value in (low, low + span / 2, low + span):
        y = point(0, value).split(',')[1]
        labels += f'<line class="gridline" x1="60" x2="880" y1="{y}" y2="{y}"/><text x="52" y="{y}" text-anchor="end">{value:.1f}</text>'
    return ('<section class="trend"><h2>整体净销小时对比</h2>'
            '<p class="note">今日 / 昨日，单位万元。来源为所选汇总模板；缺失时段留空，今日未来时段不绘制，保留上游值及真实下降。</p>'
            '<div class="legend"><span class="today">今日整体净销</span><span class="yesterday">昨日整体净销</span></div>'
            '<div class="scroll"><svg role="img" aria-label="整体净销小时对比，横轴小时，纵轴万元" viewBox="0 0 920 245">'
            '<title>整体净销小时对比（万元）</title><text x="60" y="20">净销（万元）</text>'
            + labels + marks + '</svg></div></section>')


def confined_path(workspace: Path, path: str) -> Path:
    root = workspace.resolve(strict=True)
    raw = Path(path.removeprefix('/workspace/'))
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('文件路径越过当前工作区')
    # 解析前逐段检查，拒绝工作区内部也指向其他位置的符号链接。
    current = candidate
    while current != root and current != current.parent:
        if current.is_symlink():
            raise ValueError('不允许符号链接路径')
        current = current.parent
    return resolved


def load_payload(workspace: Path, path: str) -> dict:
    file = confined_path(workspace, path)
    if file.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('输入文件超出8MB上限')
    return json.loads(file.read_text(encoding='utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', required=True)
    parser.add_argument('--dabo', required=True)
    parser.add_argument('--baseline')
    parser.add_argument('--task-id', default='ecommerce-sales-skill-test')
    parser.add_argument('--workspace')
    parser.add_argument('--as-of')
    parser.add_argument('--date', required=True, help='两个查询工具实际使用的业务日期')
    parser.add_argument('--source-contract', default='')
    args = parser.parse_args()
    try:
        workspace = next((p.parent for p in PACKAGE.parents if p.name == '.harness'), None)
        if workspace is None:
            if not args.workspace:
                raise ValueError('非物化技能调用必须显式提供工作区')
            workspace = Path(args.workspace).resolve(strict=True)
        elif args.workspace and Path(args.workspace).resolve() != workspace.resolve():
            raise ValueError('不允许覆盖物化技能的工作区')
        config = json.loads((PACKAGE / 'config.json').read_text('utf-8'))
        snapshot = build_snapshot(
            load_payload(workspace, args.summary), load_payload(workspace, args.dabo), config,
            as_of=args.as_of or datetime.now(CST).isoformat(timespec='seconds'),
            task_id=args.task_id, query_date=args.date, source_contract=args.source_contract,
            baseline=load_payload(workspace, args.baseline) if args.baseline else None)
        output = confined_path(workspace, 'reports/ecommerce-sales-test')
        output.mkdir(parents=True, exist_ok=True)
        html_file = confined_path(workspace, 'reports/ecommerce-sales-test/dashboard.html')
        json_file = confined_path(workspace, 'reports/ecommerce-sales-test/snapshot.json')
        html_file.write_text(render_html(snapshot, (PACKAGE / 'assets/dashboard.html').read_text('utf-8')),
                             encoding='utf-8')
        json_file.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        print(json.dumps({'status': 'success', 'push_status': 'disabled',
                          'task_id': args.task_id, 'kpis': snapshot['kpis'],
                          'total_checked': sum(s['rows'] for s in snapshot['sources']),
                          'source_updated_at': snapshot['source_updated_at'],
                          'artifacts': ['reports/ecommerce-sales-test/dashboard.html',
                                        'reports/ecommerce-sales-test/snapshot.json'],
                          'message': '技能测试报告已生成，未发送消息；达人仅为模板返回范围。'},
                         ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'failed', 'push_status': 'disabled',
                          'message': f'报告未生成：{exc}'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
