"""为已有 4K 大屏补充笔记本布局，保留原快照和交互。"""

import re

_STYLE_ID = "staffdeck-notebook-layout"
_CSS = """
@media (max-width:1920px) {
  html,body{overflow:hidden!important;user-select:text}
  .stage{position:relative;width:100vw;height:100vh;max-height:100vh;padding:8px 12px;gap:8px;transform:none!important;box-sizing:border-box;overflow:hidden!important}
  header{height:46px;flex:none;padding:0 4px;border-bottom-width:1px;display:flex;align-items:center;justify-content:space-between}
  .hdr-brand{gap:8px}.logo-badge{width:28px;height:28px;border-radius:6px}.logo-badge svg{width:16px;height:16px}
  .hdr-brand h1{font-size:18px;font-weight:700}.hdr-brand .tag{font-size:9px;margin-top:1px}
  .hdr-right{gap:2px}.countdown{font-size:24px}.countdown small{font-size:10px;margin-right:4px}
  .hdr-meta{gap:6px;font-size:9px}.dot{width:5px;height:5px}
  
  .grid{height:calc(100vh - 62px)!important;flex:1;min-height:0;grid-template-columns:minmax(0,1.25fr) minmax(0,1.25fr) minmax(0,1fr);gap:8px;overflow:hidden}
  .col{gap:8px;min-width:0;min-height:0;height:100%;display:flex;flex-direction:column}
  
  .panel{padding:8px 10px;border-radius:8px;border-width:1px;overflow:hidden;box-shadow:none;display:flex;flex-direction:column}
  .p-hd{height:22px;flex:none;margin-bottom:6px;gap:6px;display:flex;align-items:center;justify-content:space-between}
  .p-hd h2{font-size:13px;gap:6px;white-space:nowrap;font-weight:600}.p-hd h2:before{width:3px;height:13px;border-radius:2px}.p-hd .unit{font-size:10px;white-space:normal;text-align:right}

  /* Left Column: Channels ranking & Shop table */
  .col-left>.p-rank{height:310px;flex:none;overflow:hidden}
  .channel-head,.rank-row{grid-template-columns:18px minmax(0,1fr) 52px 52px 52px;gap:4px;padding:0 4px}
  .channel-head{height:18px;flex:none;font-size:10px;color:var(--text-secondary);border-bottom:1px solid rgba(36,48,73,0.35);padding-bottom:2px}
  .channel-head div:not(:first-child):not(:nth-child(2)){text-align:right}
  .p-rank .p-bd{gap:2px;display:flex;flex-direction:column;min-height:0;flex:1;justify-content:space-between}
  .rank-list{gap:2px;overflow:visible!important;display:flex;flex-direction:column;flex:1;justify-content:space-between}
  .rank-row{flex:1;min-height:22px;max-height:25px;padding:1px 4px;border-bottom:1px solid rgba(36,48,73,0.25);display:grid;align-items:center}
  .rk-no{font-size:11px;text-align:center}
  .rk-name{font-size:11px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .rk-bar{height:2px;margin-top:2px}
  .rk-val{font-size:11px;line-height:1.1;text-align:right}
  .rk-val .r{font-size:9px;margin-top:0;display:block}

  /* Left Column Bottom: Shop Table (Fills full height without void gaps) */
  .col-left>.p-eff{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:space-between;overflow:hidden}
  .eff-table-wrap{flex:1;min-height:0;overflow:hidden!important;display:flex;flex-direction:column}
  .eff{width:100%;border-collapse:collapse;table-layout:fixed;flex:1}
  .eff thead{flex:none}
  .eff th{font-size:10px;padding:0 4px 6px;white-space:nowrap;border-bottom:1px solid rgba(36,48,73,0.5);color:var(--text-tertiary);text-align:right}
  .eff th:first-child,.eff td:first-child{text-align:left;padding-left:6px}
  .eff tbody{display:table-row-group}
  .eff tr{height:auto}
  .eff td{font-size:11px;height:27px;padding:2px 4px;border-bottom:1px solid rgba(36,48,73,0.3);text-align:right;vertical-align:middle}
  .eff td.ch{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:130px;font-weight:600}
  .eff td.time-cell{font-size:10px;white-space:nowrap;text-align:right;padding-right:6px}
  .eff td.time-cell .t-stamp{font-family:var(--font-num);color:var(--text-secondary);margin-right:4px}
  .row-delay{font-size:9px;font-weight:600;padding:1px 4px;border-radius:3px;display:inline-block;white-space:nowrap;vertical-align:middle}
  .eff-pagination{height:32px;flex:none;display:flex;align-items:center;justify-content:space-between;gap:4px;padding:0 8px;margin-top:4px;border-radius:6px;background:var(--surface-raised);border:1px solid var(--border-subtle)}
  .page-info{font-size:10px;gap:4px;color:var(--text-secondary);white-space:nowrap}
  .page-info b{font-size:11px;color:var(--accent-gold)}
  .page-controls{gap:3px;display:flex;align-items:center}
  .page-btn{padding:2px 5px;font-size:10px;border-radius:3px}
  .page-pills{gap:2px;display:flex}
  .page-pill{width:18px;height:18px;font-size:10px;border-radius:3px}

  /* Mid Column: KPI, Trend, Brand */
  .col-mid>.p-kpi{height:190px;flex:none;overflow:hidden}
  .kpi-label{font-size:11px;color:var(--text-secondary);display:flex;align-items:center;justify-content:space-between}
  .kpi-main{gap:6px;margin-top:2px;display:flex;align-items:baseline}
  .kpi-main .big{font-size:42px;font-weight:700;line-height:1.0}
  .kpi-main .u{font-size:14px;color:var(--text-secondary)}
  .kpi-prog{margin-top:4px}
  .kpi-prog .line{margin-bottom:2px;display:flex;align-items:center;justify-content:space-between}
  .kpi-prog .line .lb{font-size:10px}
  .kpi-prog .line .pc{font-size:12px;font-weight:700}
  .bar{height:6px;padding:1px;border-radius:3px}
  .kpi-yoy{gap:4px;margin-top:6px;display:grid;grid-template-columns:repeat(4,1fr)}
  .kpi-yoy div{font-size:9px;padding:4px 6px;border-radius:5px}
  .kpi-yoy b{font-size:12px;margin-top:1px;line-height:1.2}

  .col-mid>.p-trend{height:auto;flex:1;min-height:170px;overflow:hidden}
  .trend-wrap{position:relative;height:100%;min-height:0;overflow:hidden}
  .trend-legend{font-size:10px;gap:6px;margin-bottom:2px;display:flex;flex-wrap:wrap}
  .trend-legend button{padding:1px;background:none;border:0;color:inherit;cursor:pointer}
  .trend-legend i{width:10px;height:3px;margin-right:3px;display:inline-block;border-radius:2px;vertical-align:middle}
  .trend-summary{font-size:9px;gap:8px;margin-bottom:2px;display:flex;flex-wrap:wrap}
  svg.trend{height:calc(100% - 36px);min-height:100px;display:block;width:100%}
  svg.trend text.trend-axis-y{font-size:18px}
  svg.trend text.trend-axis-x{font-size:18px}
  #trendTooltip{font-size:11px;padding:6px 10px;min-width:160px}
  #trendTooltip .tip-hd{font-size:11px;padding-bottom:3px;margin-bottom:3px}
  #trendTooltip .tip-row{font-size:10px;gap:8px}
  #trendTooltip .tip-row b{font-size:11px}
  #trendTooltip .tip-delta{font-size:10px}

  .col-mid>.p-mini{height:105px;flex:none;overflow:hidden}
  .minis{gap:6px;display:grid;grid-template-columns:repeat(3,1fr);height:100%}
  .mini{padding:6px 8px;gap:2px;border-radius:6px;min-width:0;display:flex;flex-direction:column;justify-content:center}
  .mini .lb{font-size:10px}
  .mini .v{font-size:18px;font-weight:700;white-space:nowrap}
  .mini .v small{font-size:9px;margin-left:2px}
  .mini .d{font-size:9px;overflow-wrap:anywhere}

  /* Right Column: Live shops (Aligned at 310px) & Influencers */
  .col-right>.p-live{height:310px;flex:none;overflow:hidden}
  .p-live .p-bd{overflow:visible!important;display:flex;flex-direction:column;height:calc(100% - 28px);justify-content:space-between}
  .live-main{padding:6px 8px;margin-bottom:4px;border-radius:6px}
  .live-top{margin-bottom:2px;gap:4px;display:flex;align-items:center;justify-content:space-between}
  .live-title{font-size:12px;font-weight:700}
  .live-host{font-size:8px;margin-top:1px;color:var(--text-secondary)}
  .badge.live{font-size:8px;padding:1px 4px;white-space:nowrap;border-radius:8px}
  .live-metrics{gap:4px;display:grid;grid-template-columns:repeat(3,1fr);margin-top:2px}
  .live-metrics .m{padding:2px 4px;border-radius:4px;min-width:0}
  .live-metrics .lb{font-size:8px;color:var(--text-secondary)}
  .live-metrics .v{font-size:13px;font-weight:700;margin-top:1px}
  .live-metrics .v small{font-size:8px;margin-left:1px}
  .live-sub{gap:3px;display:flex;flex-direction:column;flex:1;justify-content:space-between}
  .live-sub-row{padding:3px 6px;border-radius:4px;gap:4px;display:flex;align-items:center;justify-content:space-between;min-height:24px;flex:1}
  .live-sub-row .t{font-size:10px;font-weight:600;display:flex;align-items:baseline;gap:4px}
  .live-sub-row .t small{font-size:8px;color:var(--text-tertiary);margin-left:2px;display:inline}
  .live-sub-row .v{font-size:11px;font-weight:700;white-space:nowrap}

  /* Right Column Bottom: Influencer Rankings (Scrollable with Deluxe Cards + Fixed Summary) */
  .col-right>.p-daren{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
  .p-daren .p-bd{display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden}
  .tal-scroll-wrap{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding-right:3px}
  .tal-scroll-wrap::-webkit-scrollbar{width:4px}
  .tal-scroll-wrap::-webkit-scrollbar-track{background:rgba(15,23,42,0.3);border-radius:2px}
  .tal-scroll-wrap::-webkit-scrollbar-thumb{background:rgba(147,197,253,0.3);border-radius:2px}
  .tal-scroll-wrap::-webkit-scrollbar-thumb:hover{background:rgba(147,197,253,0.6)}
  .tal-list{gap:5px;display:flex;flex-direction:column}
  .tal-row{grid-template-columns:22px minmax(0,1fr) 56px 64px;gap:6px;padding:6px 8px;border-radius:6px;min-height:42px;flex:none;display:grid;align-items:center;background:rgba(26,34,52,0.6);border:1px solid rgba(36,48,73,0.5)}
  .tal-no{font-size:11px;font-weight:700;width:20px;height:20px;border-radius:4px;display:flex;align-items:center;justify-content:center;background:rgba(148,163,184,0.15);color:#94A3B8}
  .tal-row:nth-child(1) .tal-no{background:#FFB84D;color:#000;box-shadow:0 0 6px rgba(255,184,77,0.4)}
  .tal-row:nth-child(2) .tal-no{background:#CBD5E1;color:#000}
  .tal-row:nth-child(3) .tal-no{background:#D97706;color:#FFF}
  .tal-name{gap:1px;display:flex;flex-direction:column;min-width:0}
  .tal-name .nm{font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#F8FAFC}
  .tal-name .cg{font-size:8px;color:var(--text-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tal-gmv{font-size:13px;font-weight:700;text-align:right;color:var(--accent-gold)}
  .tal-gmv small{font-size:8px;margin-left:1px;color:var(--text-secondary)}
  .tal-cnt{font-size:9px;color:var(--text-secondary);text-align:right;white-space:nowrap}
  .tal-summary-bar{height:28px;flex:none;margin-top:4px;padding:0 8px;border-radius:5px;background:var(--surface-raised);border:1px solid var(--border-subtle);display:flex;align-items:center;justify-content:space-between;font-size:9px;color:var(--text-secondary)}
  .tal-summary-bar b{color:var(--accent-gold);font-size:11px;font-family:var(--font-num)}
  .tal-summary-bar .sep{color:var(--border-subtle);margin:0 4px}

  .hint{display:none}
}
@media (max-width:1100px) {
  html,body{overflow:auto!important}
  .stage{height:auto!important;min-height:100vh}
  .grid{height:auto!important;grid-template-columns:repeat(2,minmax(0,1fr))}
  .col-mid{grid-column:1/-1;order:-1}.col-mid>.p-trend{height:300px;flex:none}
  .col-left>.p-rank,.col-left>.p-eff,.col-right>.p-live,.col-right>.p-daren{height:340px;flex:none}
}
@media (max-width:700px) {
  .stage{padding:10px}.grid{grid-template-columns:minmax(0,1fr)}header{height:auto;min-height:68px;gap:8px}.hdr-brand h1{font-size:16px}.hdr-meta{font-size:9px;gap:4px}.hdr-brand .tag{font-size:9px}.logo-badge{display:none}.countdown{font-size:22px}
}
"""


def adapt_dashboard_viewport(html: str) -> str:
    """可重复用于生成结果和已发布快照，不改动已有数据。"""
    style = f'<style id="{_STYLE_ID}">{_CSS}</style>'
    pattern = rf'<style id="{_STYLE_ID}">.*?</style>'
    if re.search(pattern, html, flags=re.DOTALL):
        html = re.sub(pattern, lambda _: style, html, count=1, flags=re.DOTALL)
    else:
        html = html.replace("</head>", style + "\n</head>", 1)
    html = re.sub(r"\n  // staffdeck-notebook-pagination\n.*?\n  \}\);", "", html, flags=re.DOTALL)
    html = html.replace("var pageSize = 10;", """var pageSize = 10;
  // staffdeck-notebook-pagination
  function viewportShopPageSize() {
    if (window.innerWidth > 1920) return 10;
    var wrap = document.querySelector(".eff-table-wrap");
    var h = wrap && wrap.clientHeight > 100 ? wrap.clientHeight : (window.innerHeight - 440);
    var count = Math.floor((h - 26) / 29);
    return Math.max(6, Math.min(14, count));
  }
  pageSize = viewportShopPageSize();
  window.addEventListener('resize', function () {
    var nextSize = viewportShopPageSize();
    if (nextSize !== pageSize) { pageSize = nextSize; renderShopsPage(1); }
  });""", 1)
    return html
