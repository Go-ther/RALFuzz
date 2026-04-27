from __future__ import annotations

import argparse
import os
import subprocess
import time

from ctitanfuzz.util import util


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--car", type=str, default="ctitanfuzz.torch2cuda")
    parser.add_argument("--target", type=str, default="generic", choices=["generic", "auto"])
    parser.add_argument("--target_root", type=str, default=None)
    parser.add_argument("--mode", type=str, default="race")
    parser.add_argument("--input", type=str, default=None)
    parser.add_argument("--output", type=str, default="trace.txt")
    parser.add_argument("--cont", action="store_true", default=False)
    parser.add_argument("--compiler", type=str, default="gcc")
    parser.add_argument("--compile_timeout", type=int, default=20)
    parser.add_argument("--test_timeout", type=int, default=10)
    parser.add_argument("--enable_sanitizer", action="store_true", default=False)
    args = parser.parse_args()

    tasks = util.read_all_tasks_from_dir(args.input, extension=".c")
    n_tasks = len(tasks)

    if not args.cont:
        with open(args.output, "w", encoding="utf-8", errors="replace") as trace:
            trace.write(args.input + " " + str(n_tasks) + "\n")

    cur = 0
    with open(args.output, "r", encoding="utf-8", errors="replace") as trace:
        for line in trace.readlines():
            task_id, _, _, _ = util.parse_result_summary(line)
            if task_id is None:
                continue
            cur = task_id + 1

    trace = open(args.output, "a", encoding="utf-8", errors="replace")
    while cur < n_tasks:
        proc = None
        try:
            print("\nDriver: Car Reboot", file=trace)
            trace.flush()

            cmd_line = [
                "python",
                "-u",
                "-m",
                args.car,
                "--mode",
                args.mode,
                "--input",
                args.input,
                "--start",
                str(cur),
                "--singleapi",
                "--target",
                args.target,
                "--compiler",
                args.compiler,
                "--compile_timeout",
                str(args.compile_timeout),
                "--test_timeout",
                str(args.test_timeout),
            ]
            if args.target_root:
                cmd_line.extend(["--target_root", args.target_root])
            if args.enable_sanitizer:
                cmd_line.append("--enable_sanitizer")

            proc = subprocess.Popen(
                cmd_line,
                stdin=subprocess.PIPE,
                stdout=trace,
                stderr=subprocess.STDOUT,
            )

            no_new_output_secs = 0
            old_file_size = 0
            while proc.poll() is None and no_new_output_secs < 20:
                time.sleep(1)
                file_size = os.path.getsize(args.output)
                if old_file_size != file_size:
                    no_new_output_secs = 0
                else:
                    no_new_output_secs += 1
                old_file_size = file_size

            met_timeout = no_new_output_secs >= 20
            while proc.poll() is None:
                proc.kill()

            trace.close()
            with open(args.output, "r", encoding="utf-8", errors="replace") as trace_reader:
                last_id = 0
                for line in trace_reader.readlines():
                    task_id, _, _, _ = util.parse_result_summary(line)
                    if task_id is None:
                        continue
                    last_id = task_id
            trace = open(args.output, "a", encoding="utf-8", errors="replace")

            if met_timeout:
                cur = max(last_id + 2, cur + 1)
                reason = "TimeoutFail"
            elif proc.returncode != 233:
                cur = max(last_id + 2, cur + 1)
                reason = "FrameworkCrashCatch"
            else:
                cur = last_id + 1
                continue

            if cur >= n_tasks:
                break
            fail_id = cur - 1
            fail_api, fail_label, _ = util.parse_task(tasks[fail_id])
            print(
                "\nTitanFuzzTestcase",
                fail_id,
                fail_api,
                fail_label,
                reason,
                "no detail",
                file=trace,
            )
            trace.flush()
        except KeyboardInterrupt:
            if proc is not None:
                proc.kill()
            raise SystemExit(-1)

    trace.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
