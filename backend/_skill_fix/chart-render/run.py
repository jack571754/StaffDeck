#!/usr/bin/env python3
"""Chart rendering script for chart-render skill."""

from __future__ import annotations

import argparse
import json
import os
import sys

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _render_matplotlib(
    chart_type: str,
    title: str,
    labels: list[str],
    values: list[float],
    output_path: str,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Setup fonts for Chinese display
    plt.rcParams["font.sans-serif"] = [
        "SimHei",
        "Microsoft YaHei",
        "PingFang SC",
        "WenQuanYi Micro Hei",
        "sans-serif",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    fig.patch.set_facecolor("#fcfcfd")
    ax.set_facecolor("#ffffff")

    colors = ["#2b579a", "#107c41", "#d83b01", "#008272", "#6b69d6", "#8764b8"]

    if chart_type == "line":
        ax.plot(labels, values, marker="o", color="#2b579a", linewidth=2.5, markersize=6)
        for i, txt in enumerate(values):
            ax.annotate(f"{txt:,.0f}", (labels[i], txt), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=9)
    elif chart_type == "pie":
        ax.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            startangle=140,
            colors=colors[: len(values)],
            wedgeprops={"edgecolor": "white", "linewidth": 1.5},
        )
    else:  # default bar
        bars = ax.bar(labels, values, color=colors[: len(values)], width=0.5, edgecolor="none")
        for bar in bars:
            height = bar.get_height()
            ax.annotate(
                f"{height:,.0f}",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=9,
            )

    if chart_type != "pie":
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#d2d5db")
        ax.spines["bottom"].set_color("#d2d5db")

    if title:
        plt.title(title, fontsize=14, fontweight="bold", pad=15, color="#1e293b")

    plt.tight_layout()
    parent_dir = os.path.dirname(output_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render charts from JSON data")
    parser.add_argument("--type", choices=["bar", "line", "pie"], default="bar")
    parser.add_argument("--title", default="")
    parser.add_argument("--data-json", help="Direct JSON string with labels and values")
    parser.add_argument("--data-file", help="Path to JSON file with labels and values")
    parser.add_argument("--output", default="chart_output.png", help="Output PNG path")

    args = parser.parse_args()

    data = None
    if args.data_file:
        try:
            with open(args.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"status": "error", "message": f"读取数据文件失败: {e}"}, ensure_ascii=False))
            return 1
    elif args.data_json:
        try:
            data = json.loads(args.data_json)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"status": "error", "message": f"解析数据 JSON 失败: {e}"}, ensure_ascii=False))
            return 1
    else:
        print(json.dumps({"status": "error", "message": "必须提供 --data-json 或 --data-file"}, ensure_ascii=False))
        return 1

    labels = [str(x) for x in data.get("labels", [])]
    values = [float(x) for x in data.get("values", [])]

    if not labels or not values:
        print(json.dumps({"status": "error", "message": "数据中缺少有效 labels 或 values 列表"}, ensure_ascii=False))
        return 1

    output_path = os.path.abspath(args.output)

    try:
        _render_matplotlib(args.type, args.title, labels, values, output_path)
        print(
            json.dumps(
                {
                    "status": "success",
                    "image_path": output_path,
                    "chart_type": args.type,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except ImportError:
        # Fallback note if matplotlib is not in the execution environment
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "执行环境未安装 matplotlib，请在当前 Python 虚拟环境中运行 pip install matplotlib",
                },
                ensure_ascii=False,
            )
        )
        return 2
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "error", "message": f"渲染图表异常: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
