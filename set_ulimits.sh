#!/bin/bash

# 设置文件描述符的软限制和硬限制
ulimit -n 32768
ulimit -Hn 32768

# 执行原来的 ENTRYPOINT 或 CMD 指令
exec "$@"
