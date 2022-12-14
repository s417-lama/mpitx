# mpitx

Run MPI programs over tmux

![](https://raw.githubusercontent.com/s417-lama/mpitx/images/mpitx_gdb.gif)

With multiple nodes:

![](https://raw.githubusercontent.com/s417-lama/mpitx/images/mpitx_remote.gif)

Features:
- Interactive shell for each MPI process by spawning a tmux pane for each MPI process
- Duplicated keyboard input to all MPI processes
- Support for execution on multiple nodes connected by ethernet

## Setup

First make sure that `tmux` and `mpiexec` commands are available on your system.

`mpitx` is implemented as just one Python3 script file.
We have two install options.

### 1. Install via `pip`

```sh
pip3 install git+https://github.com/s417-lama/mpitx.git
```

Check your installation with:
```sh
mpitx -- bash
```

### 2. Download a script file

You can simply download a Python script file: [mpitx/mpitx.py](https://github.com/s417-lama/mpitx/blob/main/mpitx/mpitx.py)

```sh
wget https://raw.githubusercontent.com/s417-lama/mpitx/main/mpitx/mpitx.py
chmod +x mpitx.py
```

Make sure that `mpitx.py` is made executable.

Check your installation with:
```sh
./mpitx.py -- bash
```

## Usage

```sh
mpitx [OPTIONS]... -- [COMMANDS]...
```

- `[OPTIONS]...` are passed to the installed `mpiexec` command as-is
- `[COMMANDS]...` after the delimiter `--` are executed by each MPI process on each tmux pane
- The delimiter `--` is **required**

### Examples

Debug an MPI program with 4 processes:
```sh
mpitx -n 4 -- gdb ./a.out
```

More args for gdb:
```sh
mpitx -n 4 -- gdb --args ./a.out arg1 arg2
```

With some MPI implementation-specific options:
```sh
mpitx --mca mpi_show_mca_params 1 -- gdb ./a.out
```

## Environment Variables

- `MPITX_MPIEXEC`: custom path to `mpiexec` command
- `MPITX_TMUX`: custom path to `tmux` command

When these variables are unset, the default `mpiexec` and `tmux` commands are used.

## Tips on tmux

Useful tmux shortcuts (`<prefix>` = `Ctrl-b` by default):
- `<prefix> + z`: individually operate on each pane without duplicating keyboard input
- `<prefix> + &`: close all panes in a window

## References

- [Azrael3000/tmpi](https://github.com/Azrael3000/tmpi)
    - `mpitx` was inspired by `tmpi`
    - The main advantage of `mpitx` over `tmpi` is the support for multiple nodes

## License

Copyright (c) 2022 Shumpei Shiina

Released under the MIT License. See [LICENSE](./LICENSE).
