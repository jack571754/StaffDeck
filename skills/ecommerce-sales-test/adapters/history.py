"""技能自己的成功预览基准，不读取或更改生产推送状态。"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


class PreviewHistory:
    def __init__(self, path: Path):
        self.path = path
        if path.is_symlink():
            raise ValueError('预览记录不能为符号链接')
        path.parent.mkdir(parents=True, exist_ok=True)

    def generate(self, scope, input_id, source_order, build, *, reusable=lambda result: True):
        """串行读取基准、生成和发布；任何失败均不推进成功基准。"""
        with closing(sqlite3.connect(self.path, timeout=15, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE IF NOT EXISTS latest ('
                       'scope TEXT PRIMARY KEY, input_id TEXT NOT NULL, source_order TEXT NOT NULL,'
                       'snapshot TEXT NOT NULL, result TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            try:
                prior = db.execute('SELECT * FROM latest WHERE scope=?', (scope,)).fetchone()
                if prior and prior['input_id'] == input_id:
                    old_result = json.loads(prior['result'])
                    if reusable(old_result):
                        db.execute('COMMIT')
                        return {**old_result, 'reused': True}
                if prior and source_order < prior['source_order']:
                    raise ValueError('本次上游快照早于上次成功预览，不推进比较基准')
                baseline = json.loads(prior['snapshot']) if prior else None
                snapshot, result = build(baseline)
                if result.get('status') != 'success' or result.get('published') is not True:
                    raise ValueError('报告没有成功发布，不能记录为比较基准')
                db.execute('INSERT OR REPLACE INTO latest VALUES(?,?,?,?,?)',
                           (scope, input_id, source_order,
                            json.dumps(snapshot, ensure_ascii=False, allow_nan=False),
                            json.dumps(result, ensure_ascii=False, allow_nan=False)))
                db.execute('COMMIT')
                return {**result, 'reused': False}
            except BaseException:
                db.execute('ROLLBACK')
                raise
