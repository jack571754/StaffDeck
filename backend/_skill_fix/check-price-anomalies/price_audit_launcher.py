# -*- coding: utf-8 -*-
"""
价格检查调度入口 (price_audit_launcher.py)

把这个脚本挂到系统定时器（Windows 任务计划 / Linux cron / systemd timer）即可，
不必再依赖 LLM agent 充当执行器。职责：
  1. 以确定性子进程调用 run.py --mode audit；
  2. 捕获退出码 + stdout/stderr + 耗时，写入固定日志目录（滚动保留）；
  3. 判定成败（退出码=0 且 stdout 为 status:success）；
  4. 失败时向飞书群推送一张「巡检失败」兜底运维卡（webhook 未配置则跳过），
     并返回非零退出码供调度器自身的失败通知机制使用。

环境变量（除 run.py 的 PRICE_* / FEISHU_* 外）：
  PRICE_LOG_DIR   日志目录（默认 <本脚本目录>/logs）
  PRICE_LOG_KEEP  保留日志天数（默认 30）
  FEISHU_ALERT_WEBHOOK  复用告警群 webhook；失败时也推运维卡（为空则跳过）

用法：
  py price_audit_launcher.py [--mode audit]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).resolve().parent
RUN_PY = DIR / "run.py"
LOG_DIR = Path(os.getenv("PRICE_LOG_DIR", "") or "").resolve() or (DIR / "logs")
LOG_KEEP_DAYS = int(os.getenv("PRICE_LOG_KEEP", "30"))


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str, stream=None) -> None:
    (stream or sys.stderr).write(f"[{_timestamp()}] {msg}\n")


def _run(args) -> tuple[int, str, str]:
    """子进程执行 run.py，返回 (exit_code, stdout, stderr)。"""
    cmd = [sys.executable, str(RUN_PY), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def _stdout_status(stdout: str) -> str:
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            return str(data.get("status") or "")
    except Exception:
        return ""
    return ""


def _push_ops_alert(webhook: str, exit_code: int, rc: int, stderr: str) -> None:
    """巡检失败时向群推一张运维兜底卡；失败不阻断主流程（仅记录）。"""
    if not webhook or "xxx" in webhook:
        _log("未配置 FEISHU_ALERT_WEBHOOK，跳过失败运维告警。")
        return
    import requests  # noqa: PLC0415

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"tag": "plain_text", "content": "⚠️ 价格巡检执行失败"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content":
                    f"时间 {_timestamp()} ｜ 退出码 {exit_code} ｜ 运行状态 {rc}\n\n```\n{(stderr or '')[:800]}```"}},
            ],
        },
    }
    try:
        resp = requests.post(webhook, json=payload, timeout=8)
        if resp.status_code != 200:
            _log(f"失败运维卡推送失败: {resp.status_code}")
        else:
            _log("已推送『巡检失败』运维告警卡。")
    except Exception as exc:  # noqa: BLE001
        _log(f"失败运维卡推送异常: {exc}")


def _roll_logs() -> None:
    """清理超过 LOG_KEEP_DAYS 的日志文件。"""
    if LOG_KEEP_DAYS <= 0:
        return
    cutoff = time.time() - LOG_KEEP_DAYS * 86400
    for p in LOG_DIR.glob("price_audit_*.log"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="价格巡检调度入口")
    parser.add_argument("--mode", choices=["audit"], default="audit")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"price_audit_{datetime.now().strftime('%Y%m%d')}.log"

    webhook = (os.getenv("FEISHU_ALERT_WEBHOOK") or "").strip()
    start = time.time()
    rc, stdout, stderr = _run(["--mode", args.mode])
    duration = time.time() - start
    status = _stdout_status(stdout)

    lines = [
        f"[{_timestamp()}] mode={args.mode} exit={rc} status={status} "
        f"duration={duration:.1f}s",
    ]
    if stderr.strip():
        lines.append("[stderr]\n" + stderr.rstrip()[:2000])
    try:
        with log_file.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        _log(f"写日志失败: {exc}")

    _roll_logs()

    ok = rc == 0 and status == "success"
    _log(f"巡检{'成功' if ok else '失败'}: exit={rc} status={status} "
         f"duration={duration:.1f}s 日志={log_file}")
    if ok:
        return 0

    _push_ops_alert(webhook, rc, status, stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "message": f"调度入口异常: {exc}"}, ensure_ascii=False))
        raise SystemExit(2)