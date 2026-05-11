#!/usr/bin/env python3
"""Create a small self-contained dashboard for rollout JSONL results."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def normalize_answer(value: object) -> str:
    text = "" if value is None else str(value)
    match = re.search(r"<answer>\s*(.*?)\s*</answer>", text, re.I | re.S)
    if match:
        text = match.group(1)
    return re.sub(r"\s+", " ", text).strip().lower()


def clean_question(question: str) -> str:
    text = question.replace("<image>", "").strip()
    match = re.search(r"Question:\s*(.*?)(?:\nAnswer Choices:|\Z)", text, re.S)
    if match:
        text = match.group(1).strip()
    return re.sub(r"\s+", " ", text)


def question_group(question: str) -> str:
    q = clean_question(question).lower()
    if "camera" in q and "move" in q:
        return "camera movement"
    if "moved from their original positions" in q or "object" in q and "moved" in q:
        return "object movement"
    if "relative" in q or "left" in q or "right" in q or "above" in q or "below" in q:
        return "spatial relation"
    if "first image" in q and "second image" in q:
        return "frame comparison"
    return "other"


def extract_choices(question: str) -> list[str]:
    choices = []
    for line in question.splitlines():
        match = re.match(r"\s*[A-Z]\.\s*(.+?)\s*$", line)
        if match:
            choices.append(match.group(1))
    return choices


def thought_text(row: dict) -> str:
    thoughts = row.get("thoughts") or []
    if isinstance(thoughts, list):
        return "\n".join(str(x) for x in thoughts)
    return str(thoughts)


def worker_from_name(path: Path) -> str:
    match = re.search(r"_worker(\d+)_", path.name)
    return match.group(1) if match else "unknown"


def ckpt_from_name(path: Path) -> str:
    match = re.search(r"_(ckpt\d+|final)\.jsonl$", path.name)
    return match.group(1) if match else path.stem


def load_rows(result_dir: Path) -> list[dict]:
    rows_by_id: dict[str, dict] = {}
    for path in sorted(result_dir.glob("*.jsonl")):
        worker = worker_from_name(path)
        ckpt = ckpt_from_name(path)
        for row in read_jsonl(path):
            key = str(row.get("id", f"{path.name}:{len(rows_by_id)}"))
            item = dict(row)
            item["_worker"] = worker
            item["_source_file"] = path.name
            item["_checkpoint"] = ckpt
            item["_clean_question"] = clean_question(str(row.get("question", "")))
            item["_group"] = question_group(str(row.get("question", "")))
            item["_pred_norm"] = normalize_answer(row.get("final_answer"))
            item["_gt_norm"] = normalize_answer(row.get("true_answer"))
            item["_correct"] = float(row.get("judge_score", 0.0)) >= 1.0
            item["_choices"] = extract_choices(str(row.get("question", "")))
            item["_thought_chars"] = len(thought_text(row))
            rows_by_id[key] = item
    return list(rows_by_id.values())


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def bar_svg(items: list[tuple[str, float, str]], width: int = 760, row_h: int = 30) -> str:
    if not items:
        return "<p>No data.</p>"
    label_w = 210
    bar_w = width - label_w - 90
    height = max(40, row_h * len(items) + 8)
    max_value = max(v for _, v, _ in items) or 1.0
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img">']
    for i, (label, value, note) in enumerate(items):
        y = 8 + i * row_h
        w = max(2, value / max_value * bar_w)
        parts.append(f'<text x="0" y="{y + 18}" class="axis">{html.escape(label[:32])}</text>')
        parts.append(f'<rect x="{label_w}" y="{y + 5}" width="{w:.1f}" height="18" rx="3"></rect>')
        parts.append(f'<text x="{label_w + w + 8:.1f}" y="{y + 19}" class="value">{html.escape(note)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def write_csv(rows: list[dict], path: Path) -> None:
    fields = [
        "id",
        "worker",
        "checkpoint",
        "correct",
        "judge_score",
        "true_answer",
        "final_answer",
        "question_group",
        "question",
        "image",
        "source_file",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "id": r.get("id", ""),
                    "worker": r.get("_worker", ""),
                    "checkpoint": r.get("_checkpoint", ""),
                    "correct": int(bool(r.get("_correct"))),
                    "judge_score": r.get("judge_score", ""),
                    "true_answer": r.get("true_answer", ""),
                    "final_answer": r.get("final_answer", ""),
                    "question_group": r.get("_group", ""),
                    "question": r.get("_clean_question", ""),
                    "image": r.get("image", ""),
                    "source_file": r.get("_source_file", ""),
                }
            )


def rel_image_src(image_path: str, html_dir: Path) -> str:
    if not image_path:
        return ""
    path = Path(image_path)
    try:
        return Path(path).resolve().relative_to(html_dir.resolve()).as_posix()
    except ValueError:
        try:
            return Path("../" + Path(path).resolve().relative_to(html_dir.parent.resolve()).as_posix()).as_posix()
        except ValueError:
            return "file://" + str(path.resolve())


def example_cards(rows: list[dict], html_dir: Path, limit: int = 24) -> str:
    cards = []
    for r in rows[:limit]:
        image = rel_image_src(str(r.get("image", "")), html_dir)
        cls = "ok" if r.get("_correct") else "bad"
        cards.append(
            f"""
            <article class="example {cls}">
              <img src="{html.escape(image)}" alt="sample image">
              <div>
                <div class="meta">id {html.escape(str(r.get('id', '')))} | worker {html.escape(str(r.get('_worker', '')))} | {html.escape(str(r.get('_group', '')))}</div>
                <p class="question">{html.escape(str(r.get('_clean_question', '')))}</p>
                <p><b>GT:</b> {html.escape(str(r.get('true_answer', '')))}</p>
                <p><b>Pred:</b> {html.escape(str(r.get('final_answer', '')))}</p>
              </div>
            </article>
            """
        )
    return "\n".join(cards) if cards else "<p>No examples.</p>"


def write_html(rows: list[dict], summary: dict, out_path: Path) -> None:
    out_dir = out_path.parent
    total = len(rows)
    correct = sum(1 for r in rows if r["_correct"])
    wrong = total - correct
    acc = correct / total if total else 0.0

    by_worker = []
    worker_rows = defaultdict(list)
    for r in rows:
        worker_rows[str(r["_worker"])].append(r)
    for worker, items in sorted(worker_rows.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
        w_acc = sum(1 for r in items if r["_correct"]) / len(items)
        by_worker.append((f"worker {worker}", w_acc, f"{pct(w_acc)} ({len(items)})"))

    by_group = []
    group_rows = defaultdict(list)
    for r in rows:
        group_rows[r["_group"]].append(r)
    for group, items in sorted(group_rows.items(), key=lambda x: (-len(x[1]), x[0])):
        g_acc = sum(1 for r in items if r["_correct"]) / len(items)
        by_group.append((group, g_acc, f"{pct(g_acc)} ({len(items)})"))

    answer_counts = Counter(normalize_answer(r.get("final_answer")) or "(empty)" for r in rows)
    top_answers = [(k, v, str(v)) for k, v in answer_counts.most_common(12)]
    wrong_rows = [r for r in rows if not r["_correct"]]
    correct_rows = [r for r in rows if r["_correct"]]

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Rollout Visualization</title>
  <style>
    :root {{
      --ink: #18212f;
      --muted: #647184;
      --line: #d7dee8;
      --paper: #f7f9fc;
      --panel: #ffffff;
      --good: #2f8f6b;
      --bad: #c5534d;
      --blue: #3867b7;
    }}
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--paper); }}
    header {{ padding: 28px 36px 18px; border-bottom: 1px solid var(--line); background: #fff; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    h2 {{ margin: 34px 0 14px; font-size: 19px; letter-spacing: 0; }}
    p {{ margin: 6px 0; line-height: 1.45; }}
    .subtle {{ color: var(--muted); }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .metric {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .metric b {{ display: block; font-size: 28px; margin-top: 8px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; overflow: auto; }}
    .chart rect {{ fill: var(--blue); }}
    .chart .axis {{ font-size: 13px; fill: var(--ink); }}
    .chart .value {{ font-size: 13px; fill: var(--muted); }}
    .examples {{ display: grid; grid-template-columns: 1fr; gap: 12px; }}
    .example {{ display: grid; grid-template-columns: 220px 1fr; gap: 14px; background: var(--panel); border: 1px solid var(--line); border-left: 5px solid var(--line); border-radius: 8px; padding: 12px; }}
    .example.ok {{ border-left-color: var(--good); }}
    .example.bad {{ border-left-color: var(--bad); }}
    .example img {{ width: 220px; max-height: 150px; object-fit: contain; background: #edf1f6; border-radius: 6px; }}
    .meta {{ color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
    .question {{ font-weight: 600; }}
    a {{ color: var(--blue); }}
    @media (max-width: 800px) {{
      .metrics, .grid {{ grid-template-columns: 1fr; }}
      .example {{ grid-template-columns: 1fr; }}
      .example img {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Rollout Visualization</h1>
    <p class="subtle">{html.escape(str(summary["result_dir"]))}</p>
  </header>
  <main>
    <section class="metrics">
      <div class="metric">Accuracy<b>{pct(acc)}</b></div>
      <div class="metric">Correct<b>{correct}</b></div>
      <div class="metric">Wrong<b>{wrong}</b></div>
      <div class="metric">Total<b>{total}</b></div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Accuracy By Worker</h2>
        {bar_svg(by_worker)}
      </div>
      <div class="panel">
        <h2>Accuracy By Question Group</h2>
        {bar_svg(by_group)}
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Top Predicted Answers</h2>
        {bar_svg(top_answers)}
      </div>
      <div class="panel">
        <h2>Run Notes</h2>
        <p>Average thought length: {mean([r["_thought_chars"] for r in rows]):.0f} characters</p>
        <p>Unique images: {len(set(str(r.get("image", "")) for r in rows))}</p>
        <p>CSV export: <a href="predictions.csv">predictions.csv</a></p>
        <p>Summary JSON: <a href="summary.json">summary.json</a></p>
      </div>
    </section>

    <h2>Wrong Examples</h2>
    <section class="examples">
      {example_cards(wrong_rows, out_dir, limit=32)}
    </section>

    <h2>Correct Examples</h2>
    <section class="examples">
      {example_cards(correct_rows, out_dir, limit=12)}
    </section>
  </main>
</body>
</html>
"""
    out_path.write_text(html_text, encoding="utf-8")


def build_summary(rows: list[dict], result_dir: Path) -> dict:
    total = len(rows)
    correct = sum(1 for r in rows if r["_correct"])
    by_worker = {}
    for r in rows:
        by_worker.setdefault(str(r["_worker"]), [0, 0])
        by_worker[str(r["_worker"])][0] += int(r["_correct"])
        by_worker[str(r["_worker"])][1] += 1
    by_group = {}
    for r in rows:
        by_group.setdefault(r["_group"], [0, 0])
        by_group[r["_group"]][0] += int(r["_correct"])
        by_group[r["_group"]][1] += 1
    return {
        "result_dir": str(result_dir),
        "total": total,
        "correct": correct,
        "wrong": total - correct,
        "accuracy": correct / total if total else 0.0,
        "by_worker": {k: {"correct": v[0], "total": v[1], "accuracy": v[0] / v[1]} for k, v in sorted(by_worker.items())},
        "by_question_group": {k: {"correct": v[0], "total": v[1], "accuracy": v[0] / v[1]} for k, v in sorted(by_group.items())},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    result_dir = args.result_dir.resolve()
    out_dir = (args.output_dir or result_dir / "visualization").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_rows(result_dir)
    if not rows:
        raise SystemExit(f"No JSONL rollout rows found in {result_dir}")

    summary = build_summary(rows, result_dir)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(rows, out_dir / "predictions.csv")
    write_html(rows, summary, out_dir / "index.html")

    print(f"Wrote {out_dir / 'index.html'}")
    print(f"Accuracy: {pct(summary['accuracy'])} ({summary['correct']}/{summary['total']})")


if __name__ == "__main__":
    main()
