import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
skill_md = (ROOT / "skills" / "fixed-etl-scheduled-task" / "SKILL.md").read_text(encoding="utf-8")
run_py = (ROOT / "skills" / "templates" / "fixed-etl-notify" / "run.py").read_text(encoding="utf-8")
config_json = (ROOT / "skills" / "templates" / "fixed-etl-notify" / "config.json").read_text(encoding="utf-8")

files = [
    {"path": "SKILL.md", "content": skill_md},
    {"path": "run.py", "content": run_py},
    {"path": "config.json", "content": config_json},
]

db_path = ROOT / "backend" / "skill_agent_loop.db"
con = sqlite3.connect(db_path)
cur = con.cursor()
cur.execute(
    """
    INSERT OR REPLACE INTO general_skills (
        id, tenant_id, slug, name, description, skill_markdown, skill_files_json,
        metadata_json, status, capability_scope, permissions_json, runtime_config_json,
        created_at, updated_at
    ) VALUES (
        'genskill_fixed_etl_notify',
        'tenant_demo',
        'fixed-etl-scheduled-task',
        '固定流程定时任务',
        '固定流程巡检与播报：自动调用查询模板取数、内存清洗比对、SQLite状态指纹去重，并向飞书群推送格式化卡片。',
        ?,
        ?,
        '{"created_by": "admin", "source": "standard_workflow"}',
        'published',
        'general',
        '{"network": true, "python": true}',
        '{"runtime": "python", "timeout_seconds": 60}',
        datetime('now'),
        datetime('now')
    )
""",
    (skill_md, json.dumps(files, ensure_ascii=False)),
)
con.commit()
print("Registered fixed-etl-scheduled-task successfully!")
