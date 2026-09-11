import yaml
from paths import CONFIG_FILE

def load_config():
    """读取并解析 YAML 配置文件"""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:

        # 使用 safe_load 安全地将 YAML 转换为 Python 字典
        return yaml.safe_load(f)
