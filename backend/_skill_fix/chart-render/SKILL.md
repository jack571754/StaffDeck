---
name: chart-render
description: "图表渲染技能：将统计查询数据 JSON 离线渲染为高质量 PNG 图表（柱状图、折线图、环形图），生成可直接发送至渠道或分享的图片产物。"
---

# 图表离线渲染技能

将结构化数据（JSON）快速渲染为现代化排版的 PNG 统计图表：
- **支持图表类型**：`bar`（柱状图）、`line`（趋势折线图）、`pie`（占比饼图/环形图）。
- **零凭证依赖**：完全本地离线生成，不依赖任何第三方云服务。
- **自适应配色与高清导出**：默认 300 DPI 导出，适配移动端与桌面端高分屏展示。

## 执行方式

通过 `run_skill_script` 执行：

```json
{
  "script_path": "<entrypoint_path>",
  "argv": [
    "--type", "bar",
    "--title", "各部门销售业绩汇总",
    "--data-json", "{\"labels\": [\"美妆\", \"女装\", \"食品\"], \"values\": [120000, 85000, 43000]}",
    "--output", "sales_chart.png"
  ],
  "timeout_seconds": 30
}
```

## 参数说明
- `--type`：图表类型，支持 `bar`（默认）、`line`、`pie`。
- `--title`：图表标题。
- `--data-json` 或 `--data-file`：数据源（包含 labels 与 values）。
- `--output`：输出 PNG 路径（默认 `./chart.png`）。

## 输出规范
- 成功：`{"status":"success","image_path":"sales_chart.png","chart_type":"bar"}`（退出码 0）。
- 失败：`{"status":"error","message":"..."}`（退出码非 0）。
