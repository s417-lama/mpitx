#!/usr/bin/env python3

import os
import sys
import subprocess

def show_usage_and_exit():
    print("Usage: mpitx [OPTIONS]... -- [COMMANDS]...")
    print("")
    print("Delimiter '--' is required between MPI options and commands.")
    print("[OPTIONS]... are passed to mpiexec as-is.")
    exit(1)

def show_error(msg):
    print("\x1b[31m" + "Error: " + msg + "\x1b[39m")

def parse_args(args):
    np_opts = ["-n", "--n", "-np", "--np", "-c"]
    prog_delim = "--"

    np = 0
    options = []
    commands = []
    is_command = False
    for i, arg in enumerate(args):
        if i == 0:
            this_cmd = arg
            continue
        elif arg in np_opts:
            np = int(args[i + 1])
        elif arg == prog_delim:
            is_command = True
            continue

        if is_command:
            commands.append(arg)
        else:
            options.append(arg)

    if not is_command:
        show_error("Please specify commands after '--'.")
        show_usage_and_exit()

    if len(commands) == 0:
        show_error("No commands were specified after '--'.")
        show_usage_and_exit()

    return (this_cmd, np, options, commands)

def get_mpi_rank():
    rank_envs = ["OMPI_COMM_WORLD_RANK", "PMI_RANK", "PMIX_RANK"]
    for rank_env in rank_envs:
        rank = os.environ.get(rank_env)
        if rank:
            return rank
    show_error("Could not get an MPI rank from environment variables.")
    exit(1)

def main():
    mpiexec = "mpiexec"
    token = "mpitx_internal"
    if len(sys.argv) == 1:
        show_usage_and_exit()
    elif sys.argv[1] == token:
        print(get_mpi_rank())
        subprocess.run(sys.argv[2:])
    else:
        (this_cmd, np, options, commands) = parse_args(sys.argv)
        subprocess.run([mpiexec] + options + [this_cmd, token] + commands)

if __name__ == "__main__":
    main()
