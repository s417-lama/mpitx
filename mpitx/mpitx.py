#!/usr/bin/env python3

import os
import sys
import subprocess
import base64
import pickle
import socket
import secrets

mpiexec_cmd = "mpiexec"
tmux_cmd = "tmux"

# Utils
# -----------------------------------------------------------------------------

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

def serialize(obj):
    return base64.b64encode(pickle.dumps(obj)).decode()

def deserialize(obj_str):
    return pickle.loads(base64.b64decode(obj_str.encode()))

def get_mpi_rank():
    rank_envs = ["OMPI_COMM_WORLD_RANK", "PMI_RANK", "PMIX_RANK"]
    for rank_env in rank_envs:
        rank = os.environ.get(rank_env)
        if rank:
            return int(rank)
    show_error("Could not get an MPI rank from environment variables.")
    exit(1)

def get_mpi_size():
    size_envs = ["OMPI_COMM_WORLD_SIZE", "PMI_SIZE", "PMIX_SIZE"]
    for size_env in size_envs:
        size = os.environ.get(size_env)
        if size:
            return int(size)
    show_error("Could not get an MPI size from environment variables.")
    exit(1)

# Tmux
# -----------------------------------------------------------------------------

def tmux_new_window():
    return subprocess.run(["tmux", "new-window", "-P", "-F", "#{pane_id} #{window_id} #{session_id}"],
                          stdout=subprocess.PIPE, encoding="utf-8", check=True).stdout.strip().split()

def tmux_set_window_option(window_id, options):
    subprocess.run(["tmux", "set-window-option", "-t", window_id] + options,
                   stdout=subprocess.DEVNULL, check=True)

def tmux_split_window(window_id, commands):
    return subprocess.run(["tmux", "split-window", "-P", "-F", "#{pane_id}", "-t", window_id] + commands,
                          stdout=subprocess.PIPE, encoding="utf-8", check=True).stdout.strip()

def tmux_select_layout(pane_id, layout):
    subprocess.run(["tmux", "select-layout", "-t", pane_id, layout],
                   stdout=subprocess.DEVNULL, check=True)

def tmux_kill_pane(pane_id):
    subprocess.run(["tmux", "kill-pane", "-t", pane_id],
                   stdout=subprocess.DEVNULL, check=True)

# Socket
# -----------------------------------------------------------------------------

def recv_exact(s, n):
    n_left = n
    bs = []
    while n_left > 0:
        b = s.recv(n_left)
        if len(b) == 0:
            raise EOFError
        n_left -= len(b)
        bs.append(b)
    return b"".join(bs)

def send_int(s, v):
    s.sendall(v.to_bytes(8, "big"))

def recv_int(s):
    return int.from_bytes(recv_exact(s, 8), "big")

def send_with_size(s, msg):
    send_int(s, len(msg))
    s.sendall(msg)

def recv_with_size(s):
    n = recv_int(s)
    return recv_exact(s, n)

def accept_with_token(sock, token):
    while True:
        conn, _addr = sock.accept()
        try:
            token_received = recv_exact(conn, len(token.encode())).decode()
        except:
            conn.close()
        else:
            if token_received == token:
                return conn
            else:
                conn.close()

def establish_connection_on_parent(on_listen_hook):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 0))
        s.listen()
        (_, port) = s.getsockname()
        token = secrets.token_hex()
        on_listen_hook(port, token)

        conns = dict()
        mpi_size = 0
        while mpi_size == 0 or len(conns) < mpi_size:
            conn = accept_with_token(s, token)
            mpi_rank = recv_int(conn)
            mpi_size = recv_int(conn)
            print("{}/{}".format(mpi_rank, mpi_size))
            conns[mpi_rank] = conn

        return conns

def establish_connection_to_parent(host, port, token, rank, size):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    s.sendall(token.encode())
    send_int(s, rank)
    send_int(s, size)
    return s

def establish_connection_on_tmux_pane(on_listen_hook):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 0))
        s.listen()
        (_, port) = s.getsockname()
        token = secrets.token_hex()
        on_listen_hook(port, token)
        return accept_with_token(s, token)

def establish_connection_to_tmux(host, port, token):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    s.sendall(token.encode())
    return s

# Main
# -----------------------------------------------------------------------------

def main():
    child_subcmd = "mpitx_child"
    tmux_subcmd = "mpitx_tmux"

    if len(sys.argv) == 1:
        show_usage_and_exit()

    elif sys.argv[1] == child_subcmd:
        # Child process of mpiexec
        args = deserialize(sys.argv[2])

        with establish_connection_to_parent("127.0.0.1", args["port"], args["token"],
                                            get_mpi_rank(), get_mpi_size()) as parent_conn:
            tmux_args = deserialize(recv_with_size(parent_conn).decode())

            result = subprocess.run(args["commands"], stdout=subprocess.PIPE, encoding="utf-8").stdout.strip()

            with establish_connection_to_tmux("127.0.0.1", tmux_args["port"], tmux_args["token"]) as tmux_conn:
                send_with_size(tmux_conn, result.encode())

    elif sys.argv[1] == tmux_subcmd:
        # Process on each tmux pane
        args = deserialize(sys.argv[2])

        def notify_parent(port, token):
            with establish_connection_to_parent("127.0.0.1", args["port"], args["token"],
                                                args["mpi_rank"], args["mpi_size"]) as conn:
                tmux_args = dict(port=port, token=token)
                send_with_size(conn, serialize(tmux_args).encode())

        with establish_connection_on_tmux_pane(notify_parent) as tmux_conn:
            result = recv_with_size(tmux_conn).decode()
            print(result)

    else:
        # Top-level process
        (this_cmd, np, options, commands) = parse_args(sys.argv)

        mpiexec_process = None

        def launch_mpiexec(port, token):
            nonlocal mpiexec_process
            args = dict(commands=commands, port=port, token=token)
            mpiexec_process = subprocess.Popen([mpiexec_cmd] + options + [this_cmd, child_subcmd, serialize(args)])

        child_conns = establish_connection_on_parent(launch_mpiexec)

        [dummy_pane_id, window_id, session_id] = tmux_new_window()
        tmux_set_window_option(window_id, ["synchronize-panes", "on"])
        tmux_set_window_option(window_id, ["remain-on-exit", "on"])

        def create_tmux_panes(port, token):
            for rank, conn in sorted(child_conns.items()):
                args = dict(mpi_rank=rank, mpi_size=len(child_conns), port=port, token=token)
                pane_id = tmux_split_window(window_id, [this_cmd, tmux_subcmd, serialize(args)])
                tmux_select_layout(pane_id, "tiled")
                if rank == 0:
                    tmux_kill_pane(dummy_pane_id)

        tmux_conns = establish_connection_on_parent(create_tmux_panes)

        for rank, conn in sorted(tmux_conns.items()):
            tmux_msg = recv_with_size(conn)
            send_with_size(child_conns[rank], tmux_msg)

        try:
            mpiexec_process.wait()
        except KeyboardInterrupt:
            mpiexec_process.terminate()
            print("Interrupted.")

        for conn in child_conns.values():
            conn.close()

        for conn in tmux_conns.values():
            conn.close()

if __name__ == "__main__":
    main()
