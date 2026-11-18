"""插件系统 — 自动发现并加载 plugins/ 目录下的所有工具

每个插件模块只需暴露一个 get_tools() -> list[BaseTool] 函数，
系统启动时自动收集，无需修改 graph.py 或任何其他文件。

添加新工具的步骤：
  1. 在 plugins/ 下创建一个 .py 文件（不要以 _ 开头）
  2. 实现 get_tools() 返回 LangChain BaseTool 列表
  3. 重启程序 — 自动生效
"""
import importlib
import os

from langchain_core.tools import BaseTool


def discover_tools() -> list[BaseTool]:
    """遍历 plugins/ 目录，加载所有插件模块中的工具。

    发现规则：
    - 所有不以 _ 开头的 .py 文件
    - 模块中必须有 get_tools() 函数
    - get_tools() 返回 list[BaseTool]
    """
    tools: list[BaseTool] = []
    plugins_dir = os.path.dirname(os.path.abspath(__file__))

    for filename in sorted(os.listdir(plugins_dir)):
        if not filename.endswith('.py') or filename.startswith('_'):
            continue

        module_name = filename[:-3]  # 去掉 .py 后缀

        try:
            module = importlib.import_module(f".{module_name}", package=__name__)
        except ImportError:
            print(f"  ⚠️ 无法加载插件: {module_name}")
            continue

        if not hasattr(module, 'get_tools'):
            continue

        plugin_tools = module.get_tools()
        tools.extend(plugin_tools)
        print(f"  📦 已加载插件: {module_name} → {[t.name for t in plugin_tools]}")

    return tools
