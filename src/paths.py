import sys
from pathlib import Path

# 1. 定位 src 目录的绝对路径 (__file__ 的父目录)
SRC_DIR = Path(__file__).resolve().parent

# 2. 定位配置文件 config.yml 的绝对路径
CONFIG_FILE = SRC_DIR / "config" / "config.yml"

# 3. 自动将 src 目录注入到 sys.path 中，一劳永逸
if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))
