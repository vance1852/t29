from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


def cmd_init(args):
    from .sample_data import generate
    output_dir = args.output or "./sample_input"
    generate(output_dir)


def cmd_scan(args):
    from .scanner import scan_directory
    from .models import MaterialType

    files = scan_directory(args.input, cutoff_hour=args.cutoff)
    print(f"\n扫描结果: 共 {len(files)} 个文件\n")
    print(f"{'文件名':<45} {'门店':<8} {'日期':<12} {'类型':<20} {'哈希(前8)':<12}")
    print("-" * 100)
    for fi in files:
        h = fi.content_hash[:8] if fi.content_hash else "N/A"
        print(f"{fi.original_filename:<45} {fi.store_id or '?':<8} {fi.date or '?':<12} {fi.material_type.value:<20} {h:<12}")


def cmd_organize(args):
    from .organizer import organize

    result = organize(
        args.input,
        args.output,
        cutoff_hour=args.cutoff,
        force=args.force,
    )
    print(f"\n整理结果: {json.dumps(result, ensure_ascii=False, indent=2)}")


def cmd_exceptions(args):
    from .scanner import scan_directory
    from .rules import run_all_rules
    from .models import Severity

    files = scan_directory(args.input, cutoff_hour=args.cutoff)
    exceptions = run_all_rules(files, cutoff_hour=args.cutoff)

    if not exceptions:
        print("未发现异常")
        return

    severity_order = {Severity.BLOCKING: 0, Severity.REVIEW: 1, Severity.REMINDER: 2}
    exceptions.sort(key=lambda e: (severity_order.get(e.severity, 3), e.rule_id))

    print(f"\n异常清单: 共 {len(exceptions)} 条\n")
    for e in exceptions:
        icon = {"blocking": "🔴", "review": "🟡", "reminder": "🟢"}.get(e.severity.value, "⚪")
        print(f"{icon} [{e.severity.value.upper()}] [{e.rule_id}] {e.description}")
        if e.suggestion:
            print(f"   建议: {e.suggestion}")


def cmd_package(args):
    from .package import generate_package

    result = generate_package(
        args.input,
        args.output,
        cutoff_hour=args.cutoff,
    )
    print(f"\n交接包生成结果: {json.dumps(result, ensure_ascii=False, indent=2)}")


def cmd_rollback(args):
    from .rollback import rollback

    result = rollback(args.output)
    print(f"\n回滚结果: {json.dumps(result, ensure_ascii=False, indent=2)}")


def cmd_explain(args):
    from .explain import explain_file, explain_exception

    if args.file:
        result = explain_file(args.file, args.input, cutoff_hour=args.cutoff)
    elif args.rule:
        result = explain_exception(args.rule, args.input, cutoff_hour=args.cutoff)
    else:
        print("请指定 --file 或 --rule 参数")
        return

    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        prog="night-audit",
        description="连锁门店夜间巡检材料自动归档工具",
    )
    sub = parser.add_subparsers(dest="command", help="可用命令")
    sub.required = True

    p_init = sub.add_parser("init", help="初始化样例数据")
    p_init.add_argument("-o", "--output", default="./sample_input", help="输出目录 (默认: ./sample_input)")
    p_init.set_defaults(func=cmd_init)

    p_scan = sub.add_parser("scan", help="扫描输入目录")
    p_scan.add_argument("-i", "--input", default="./sample_input", help="输入目录")
    p_scan.add_argument("--cutoff", type=int, default=6, help="夜班切分时间(小时, 默认6, 即06:00前算前一天)")
    p_scan.set_defaults(func=cmd_scan)

    p_org = sub.add_parser("organize", help="整理材料到输出目录")
    p_org.add_argument("-i", "--input", default="./sample_input", help="输入目录")
    p_org.add_argument("-o", "--output", default="./output", help="输出目录")
    p_org.add_argument("--cutoff", type=int, default=6, help="夜班切分时间(小时)")
    p_org.add_argument("--force", action="store_true", help="忽略阻塞异常强制执行")
    p_org.set_defaults(func=cmd_organize)

    p_exc = sub.add_parser("exceptions", help="生成异常清单")
    p_exc.add_argument("-i", "--input", default="./sample_input", help="输入目录")
    p_exc.add_argument("--cutoff", type=int, default=6, help="夜班切分时间(小时)")
    p_exc.set_defaults(func=cmd_exceptions)

    p_pkg = sub.add_parser("package", help="生成交接包")
    p_pkg.add_argument("-i", "--input", default="./sample_input", help="输入目录")
    p_pkg.add_argument("-o", "--output", default="./output", help="输出目录")
    p_pkg.add_argument("--cutoff", type=int, default=6, help="夜班切分时间(小时)")
    p_pkg.set_defaults(func=cmd_package)

    p_rb = sub.add_parser("rollback", help="回滚上一次整理")
    p_rb.add_argument("-o", "--output", default="./output", help="输出目录")
    p_rb.set_defaults(func=cmd_rollback)

    p_exp = sub.add_parser("explain", help="解释文件或异常的归类原因")
    p_exp.add_argument("-i", "--input", default="./sample_input", help="输入目录")
    p_exp.add_argument("-f", "--file", help="要解释的文件路径")
    p_exp.add_argument("-r", "--rule", help="要解释的规则ID (如R001)")
    p_exp.add_argument("--cutoff", type=int, default=6, help="夜班切分时间(小时)")
    p_exp.set_defaults(func=cmd_explain)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
