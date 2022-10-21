#!/usr/bin/env python3

import os
import sys
import subprocess
import base64
import pickle
import socket
import secrets
import select
import array
import fcntl
import termios
import signal
import tty
import pty
import threading
import time
import uuid
import shutil

mpiexec_cmd = "mpiexec"
tmux_cmd = "tmux"

# Utils
# -----------------------------------------------------------------------------

def show_usage_and_exit():
    print("Usage: mpitx [OPTIONS]... -- [COMMANDS]...")
    print("")
    print("Delimiter '--' is required between MPI options and commands.")
    print("Options are passed to mpiexec as-is.")
    exit(1)

def show_error(msg):
    print("\x1b[31m" + "Error: " + msg + "\x1b[39m")

def parse_args(args):
    prog_delim = "--"
    this_cmd = args[0]

    try:
        prog_delim_index = args.index(prog_delim)
    except ValueError:
        show_error("Delimiter '--' is mandatory for commands.")
        show_usage_and_exit()

    options = args[1:prog_delim_index]
    commands = args[prog_delim_index + 1:]

    if len(commands) == 0:
        show_error("No commands were specified after '--'.")
        show_usage_and_exit()

    return (this_cmd, options, commands)

def check_commands_installed():
    for cmd in [mpiexec_cmd, tmux_cmd]:
        if shutil.which(cmd) is None:
            show_error("Please install '{}' command.".format(cmd))
            show_usage_and_exit()

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

def is_inside_tmux():
    return "TMUX" in os.environ

def tmux_new_session(socket_name, commands):
    subprocess.run([tmux_cmd, "-L", socket_name, "new-session"] + commands)

def tmux_new_window():
    return subprocess.run([tmux_cmd, "new-window", "-P", "-F", "#{pane_id} #{window_id}"],
                          stdout=subprocess.PIPE, encoding="utf-8", check=True).stdout.strip().split()

def tmux_set_window_option(window_id, options):
    subprocess.run([tmux_cmd, "set-window-option", "-t", window_id] + options,
                   stdout=subprocess.DEVNULL, check=True)

def tmux_split_window(window_id, commands):
    return subprocess.run([tmux_cmd, "split-window", "-P", "-F", "#{pane_id}", "-t", window_id] + commands,
                          stdout=subprocess.PIPE, encoding="utf-8", check=True).stdout.strip()

def tmux_select_layout(pane_id, layout):
    subprocess.run([tmux_cmd, "select-layout", "-t", pane_id, layout],
                   stdout=subprocess.DEVNULL, check=True)

def tmux_kill_pane(pane_id):
    subprocess.run([tmux_cmd, "kill-pane", "-t", pane_id],
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
            conns[mpi_rank] = conn

        return conns

def establish_connection_to_parent(host, port, token, rank, size):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    s.sendall(token.encode())
    send_int(s, rank)
    send_int(s, size)
    return s

# Reverse shell
# -----------------------------------------------------------------------------

def proxy_fd(in_fds, out_fds):
    while True:
        rlist, _wlist, _xlist = select.select(in_fds, [], [])
        for in_fd, out_fd in zip(in_fds, out_fds):
            if in_fd in rlist:
                try:
                    data = os.read(in_fd, 1024)
                except:
                    data = b""
                if data:
                    while data:
                        n = os.write(out_fd, data)
                        data = data[n:]
                else:
                    return

def get_termsize(fd):
    ts = array.array("h", [0] * 4)
    fcntl.ioctl(fd, termios.TIOCGWINSZ, ts, 1)
    return ts

def set_termsize(fd, ts):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, ts, 0)

def get_stable_termsize(fd):
    prev_ts = get_termsize(fd)
    while True:
        time.sleep(0.2)
        ts = get_termsize(fd)
        if ts == prev_ts:
            return ts
        else:
            prev_ts = ts

def send_termsize(s, ts):
    s.sendall(ts.tobytes())

def recv_termsize(s):
    ts = array.array("h")
    try:
        data = recv_exact(s, len(array.array("h", [0] * 4).tobytes()))
    except:
        data = b""
    if data:
        ts.frombytes(data)
        return ts
    else:
        return None

def wait_on_tmux_pane(on_listen_hook):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", 0))
        s.listen()
        (_, port) = s.getsockname()
        token = secrets.token_hex()
        on_listen_hook(port, token)

        with accept_with_token(s, token) as conn1:
            send_termsize(conn1, get_stable_termsize(sys.stdin.fileno()))

            with accept_with_token(s, token) as conn2:
                def send_win_size():
                    nonlocal conn2
                    send_termsize(conn2, get_termsize(sys.stdin.fileno()))

                signal.signal(signal.SIGWINCH, lambda signum, frame: send_win_size())
                send_win_size()

                try:
                    mode = tty.tcgetattr(sys.stdin.fileno())
                    tty.setraw(sys.stdin.fileno())
                except:
                    reset_tty_mode = False
                else:
                    reset_tty_mode = True

                try:
                    proxy_fd([conn1.fileno(), sys.stdin.fileno()], [sys.stdout.fileno(), conn1.fileno()])
                finally:
                    if reset_tty_mode:
                        tty.tcsetattr(sys.stdin.fileno(), tty.TCSAFLUSH, mode)

def launch_reverse_shell(host, port, token, commands):
    def watch_window_size(fd):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
            s.sendall(token.encode())
            while True:
                ts = recv_termsize(s)
                if ts:
                    set_termsize(fd, ts)
                else:
                    os.close(fd)
                    return

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        s.sendall(token.encode())

        ts = recv_termsize(s)

        pid, fd = pty.fork()
        if pid == 0:
            set_termsize(sys.stdin.fileno(), ts)
            os.execlp(commands[0], *commands)

        t = threading.Thread(target=watch_window_size, args=(fd,))
        t.start()

        proxy_fd([s.fileno(), fd], [fd, s.fileno()])
        os.waitpid(pid, 0)

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
            launch_reverse_shell("127.0.0.1", tmux_args["port"], tmux_args["token"], args["commands"])

    elif sys.argv[1] == tmux_subcmd:
        # Process on each tmux pane
        args = deserialize(sys.argv[2])

        def notify_parent(port, token):
            with establish_connection_to_parent("127.0.0.1", args["port"], args["token"],
                                                args["mpi_rank"], args["mpi_size"]) as conn:
                tmux_args = dict(port=port, token=token)
                send_with_size(conn, serialize(tmux_args).encode())

        wait_on_tmux_pane(notify_parent)

    else:
        # Top-level process
        check_commands_installed()

        if not is_inside_tmux():
            socket_name = "mpitx." + str(uuid.uuid4())
            tmux_new_session(socket_name, sys.argv)
            return

        (this_cmd, options, commands) = parse_args(sys.argv)

        mpiexec_process = None

        def launch_mpiexec(port, token):
            nonlocal mpiexec_process
            args = dict(commands=commands, port=port, token=token)
            mpiexec_process = subprocess.Popen([mpiexec_cmd] + options + [this_cmd, child_subcmd, serialize(args)],
                                               start_new_session=True)

        child_conns = establish_connection_on_parent(launch_mpiexec)

        [dummy_pane_id, window_id] = tmux_new_window()
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
            pgid = os.getpgid(mpiexec_process.pid)
            os.killpg(pgid, signal.SIGINT)
            mpiexec_process.wait()
            print("Interrupted.")

        for conn in child_conns.values():
            conn.close()

        for conn in tmux_conns.values():
            conn.close()

if __name__ == "__main__":
    main()
