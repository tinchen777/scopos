# -*- coding: utf-8 -*-
# Python version: 3.9
# @TianZhen
r"""
Scopos
Monitor GPU memory usage.
NOTE: Display shell script details for a specified user.
"""

import pynvml as pn
import psutil
import time
import os
import re


USER_NAME = "tianzhen"
REFRESH_INTERVAL = 5

free_tag = "\033[2m\u2591\033[0m"
version = "v1.1.0"


def limit_str(s: str, length: int):
    return s if len(s) <= length else s[:length - 1] + "\u2026"


pn.nvmlInit()
os.system("clear")

while True:
    # display current time
    time_tuple = time.localtime(time.time())
    space = 10
    print(r"  ___   ___  _____  ____  _____  ___ ", " " * space, )
    print(r" / __) / __)(  _  )(  _ \(  _  )/ __) ", " " * space, time.strftime(" %Y-%m-%d", time_tuple))
    print(r" \__ \( (__  )(_)(  )___/ )(_)( \__ \ ", " " * space, time.strftime(" %H:%M:%S", time_tuple))
    print(r" (___/ \___)(_____)(__)  (_____)(___/ ", version, sep="")
    print()

    # init
    user_codes: dict[str, int] = {}
    next_code = 31
    if USER_NAME:
        user_codes[USER_NAME] = next_code
        next_code += 1
    ppids_dict: dict[str, list[int]] = {}
    pp_counts: dict[int, list[int]] = {}

    for gpu_id in range(pn.nvmlDeviceGetCount()):
        # for each GPU

        handle = pn.nvmlDeviceGetHandleByIndex(gpu_id)
        # general
        gpu_name = str(pn.nvmlDeviceGetName(handle).decode('utf-8'))
        gpu_info = pn.nvmlDeviceGetMemoryInfo(handle)
        used = float(gpu_info.used)
        remain = float(gpu_info.free)
        total = used + remain
        idle_rate = remain / total
        if idle_rate <= 0.15:
            remain_code = 35  # red
        elif idle_rate <= 0.5:
            remain_code = 33  # yellow
        else:
            remain_code = 32  # green
        used_str = "%.2f" % float(used/(1024**3))
        remain_str = "%.2f" % float(remain/(1024**3))
        print(f"\033[1m#{gpu_id}\033[0m [{gpu_name}]  \033[1mUSED: {used_str}(GB)\033[0m  \033[1;{remain_code}mREMAIN: {remain_str}(GB)\033[0m")
        # process
        processes = pn.nvmlDeviceGetComputeRunningProcesses_v2(handle)

        # display header
        print(f"\033[4m{'PID'.ljust(8)}\u2502{'PROCESS'.ljust(8)}\u258F{'USER'.ljust(12)}\u258FNO.  \u2502{'MEMORY'.ljust(9)}\u2502{'CT'.ljust(17)} \u2502{'RT'.ljust(10)} \u2502{'DETAIL'.ljust(15)}\033[0m")

        dev_user_mems: dict[str, float] = {}
        for process in processes:
            # for each process

            try:
                pid = eval(str(process))["pid"]
                p = psutil.Process(pid)  # the current process
            except Exception:
                continue

            ppid = p.ppid()
            pp = psutil.Process(ppid)  # the current parent process
            # Parent process creation time
            t = time.strftime("%y-%m-%d %H:%M:%S", time.localtime(pp.create_time()))
            # Current process execution time
            p_spend = int(time.time()-p.create_time())
            h = str(p_spend//3600).rjust(3)
            m = str((p_spend % 3600)//60).rjust(2, "0")
            s = str((p_spend % 3600) % 60).rjust(2, "0")
            cur_t = f"{h}:{m}:{s}"
            # user
            user_name = p.username()
            user_code = user_codes.setdefault(user_name, next_code)
            if user_code == next_code and next_code < 37:
                next_code += 1
            # user process number
            user_ppids = ppids_dict.setdefault(user_name, [])
            if ppid not in user_ppids:
                user_ppids.append(ppid)
            pp_no = str(user_ppids.index(ppid) + 1).zfill(2)
            pp_counts.setdefault(ppid, [0])[0] += 1
            p_no = str(pp_counts[ppid][0]).zfill(2)
            # user memory
            cur_mem = process.usedGpuMemory
            p_mem = "%.2f" % float(cur_mem/(1024**3))
            dev_user_mems[user_name] = dev_user_mems.get(user_name, 0.0) + cur_mem

            # display shell script details for a specified user
            if user_name == USER_NAME:
                try:
                    pp_file_path = pp.open_files()[0].path
                    pp_file_name = pp_file_path.rsplit("/", maxsplit=1)[-1]
                    cur_cmd_list = p.cmdline()
                    cur_cmd = " ".join(cur_cmd_list)
                    total_task = 0
                    cur_task = -1
                    bash_file_args_dict: dict[str, str] = {}

                    def replace_bash_args(__cmd: str):
                        for arg, arg_val in bash_file_args_dict.items():
                            BASH_ARGS_REGEXP = re.compile(r"\$(\{" + arg + r"\}|" + arg + r"(?!_))")
                            __cmd = BASH_ARGS_REGEXP.sub(arg_val, __cmd)
                        __cmd = __cmd.replace('"', "")

                        return __cmd

                    with open(pp_file_path, "r", newline=None) as file:
                        for cmd_id, cmd in enumerate(file, start=1):
                            # for each command in script file

                            if not cmd.startswith("#"):
                                cmd = cmd.strip("\n")

                                if cmd.startswith(p.name()):
                                    # main cmd
                                    total_task += 1
                                    cmd = replace_bash_args(cmd)
                                    if cmd == cur_cmd:
                                        cur_task = total_task

                                elif "=" in cmd:
                                    # collect variables
                                    cmd_split = cmd.split("=", maxsplit=1)
                                    if "$" in cmd_split[1]:
                                        args_val = replace_bash_args(cmd_split[1])
                                        if "$" in args_val:
                                            raise NotImplementedError()
                                    else:
                                        args_val = cmd_split[1]
                                    bash_file_args_dict[cmd_split[0]] = args_val.strip('"')

                    task_str = f"{pp_file_name.rjust(10)} |{str(cur_task).rjust(len(str(total_task)), ' ')}/{total_task}"

                except Exception:
                    task_str = "    ?"
            else:
                task_str = "    \033[1m-\033[0m"
            # display
            tag_str = f"\033[{user_code}m\u2589\033[0m"

            print(f"{str(pid).ljust(8)}\u2502{limit_str(p.name(), 8).ljust(8)}\u258F{tag_str}{limit_str(user_name, 11).ljust(11)}\u258F{pp_no}-{p_no}\u2502{p_mem.ljust(5)}(GB)\u2502{t} \u2502{cur_t.ljust(10)} \u2502{task_str.ljust(25)}")

        # bar
        print()
        try:
            code_rates = {(user, user_codes[user]): 100 * mem / total for user, mem in dev_user_mems.items()}
            sorted_code_rates = dict(sorted(code_rates.items(), key=lambda item: item[1], reverse=True))
            first_line: list[str] = []
            second_line: list[str] = []
            next_line: list[str] = []
            cur_first_line = True
            is_full = False
            for (_, code), rate in sorted_code_rates.items():
                # int tag
                int_rate = int(rate)
                user_int_tag = f"\033[{code}m\u2588\033[0m"
                for _ in range(int_rate):
                    line = first_line if cur_first_line else second_line
                    line.append(user_int_tag)
                    # update
                    cur_first_line = not cur_first_line
                    next_line = first_line if cur_first_line else second_line
                    if len(next_line) == 50:
                        is_full = True
                        break
                if is_full:
                    break
                # decimal tag
                decimal_rate = rate - int_rate
                if decimal_rate <= 0.05:
                    continue
                elif decimal_rate <= 0.25:
                    user_decimal_code = "\u2591"
                elif decimal_rate <= 0.50:
                    user_decimal_code = "\u2592"
                elif decimal_rate <= 0.75:
                    user_decimal_code = "\u2593"
                else:
                    user_decimal_code = "\u2588"
                next_line.append(f"\033[{code}m{user_decimal_code}\033[0m")
                # update again
                cur_first_line = not cur_first_line
                next_line = first_line if cur_first_line else second_line
                if len(next_line) == 50:
                    break
            first_line.append(free_tag * (50 - len(first_line)))
            second_line.append(free_tag * (50 - len(second_line)))
            # MVP
            mvp_name, mvp_code = list(sorted_code_rates)[0]
            print("".join(first_line), f"  🏆 \033[{mvp_code}m\u2589\033[0m\033[1m{mvp_name}\033[0m 🏆", sep="")
            print("".join(second_line), sep="")
        except Exception:
            print(free_tag * 50, sep="")
            print(free_tag * 50, sep="")

        print("\u2501" * 50, sep="", end="\n\n")

    time.sleep(REFRESH_INTERVAL)
    os.system("clear")

pn.nvmlShutdown()
