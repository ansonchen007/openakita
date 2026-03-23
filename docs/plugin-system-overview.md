# OpenAkita 插件系统概览：Skill vs MCP vs Plugin

本文档说明 OpenAkita 三种扩展机制的定位、能力边界和选型指南。

---

## 一、三种机制一览

### Skill — 纯文本指令

Skill 是一段 Markdown 文本（`SKILL.md`），在启动时加载到 LLM 的 system prompt 中，用于引导 LLM 的行为。

- **能做什么**：告诉 LLM 遇到某类任务应该怎么做、优先用什么工具
- **不能做什么**：不能执行代码、不能调用系统服务、不能注册工具、不能挂钩子
- **隔离性**：N/A（只是文本，不存在隔离问题）
- **生命周期**：启动时加载文本，无动态管理

可选的 `scripts/*.py` 可以被 `SkillLoader.run_script()` 执行，但这是单次脚本调用，没有生命周期，没有 API 权限，执行完就结束。

**典型用途**：工具选择指导、格式规范、行为约束。

### MCP Server — 进程隔离的外部工具

MCP (Model Context Protocol) 是独立进程的外部工具服务，通过 JSON-RPC 协议与 OpenAkita 通信。

- **能做什么**：提供工具（tools），语言无关（Node.js / Python / Go 等）
- **不能做什么**：不能注入 prompt、不能挂钩子、不能注册 IM 通道、不能替换记忆、不能访问 OpenAkita 内部服务
- **隔离性**：进程隔离（强隔离，崩溃不影响宿主）
- **生命周期**：手动配置 `mcp_servers.json`，无统一安装/升级/市场

**典型用途**：Notion API、GitHub API、数据库查询等外部工具接入。

### Plugin — 全能力插件

插件是 OpenAkita 的新一代扩展机制，通过 `PluginAPI` 与宿主进行受控交互，可以提供 8 种能力。

- **能做什么**：注册工具、IM 通道、记忆后端、LLM 提供商、RAG 检索源、API 路由、钩子、Skill
- **能访问宿主**：通过 `PluginAPI` 受控访问 Brain、MemoryManager、Settings、MessageGateway
- **隔离性**：同进程 try/except 隔离（弱于 MCP，但能力更强）
- **生命周期**：`on_load` → `on_init` → 运行 → `on_shutdown`，支持 enable/disable/install/uninstall
- **权限管理**：三级权限模型（basic / advanced / system），安装时用户确认

**典型用途**：Obsidian 知识库 RAG、WhatsApp 通道、外部记忆系统、自定义 LLM provider。

---

## 二、能力对比矩阵

| 维度 | Skill | MCP Server | Plugin |
|------|-------|-----------|--------|
| **提供工具 (tools)** | - | YES | YES |
| **提供 IM 通道** | - | - | YES |
| **提供记忆后端** | - | - | YES |
| **提供 LLM provider** | - | - | YES |
| **提供 RAG 检索源** | - | - | YES |
| **提供 API 路由** | - | - | YES |
| **注入 prompt 文本** | YES (唯一能力) | - | YES (via SKILL.md + hooks) |
| **挂钩子** | - | - | YES (10 个钩子点) |
| **替换内置模块** | - | - | YES (记忆/搜索/LLM) |
| **访问宿主服务** | - | - | YES (Brain/Memory/Gateway/Settings) |
| **代发消息** | - | - | YES (`send_message()`) |
| **代码执行** | 仅可选 scripts/ 单次 | 独立进程内 | 同进程运行 |
| **进程隔离** | N/A | YES (强) | NO (try/except 隔离) |
| **生命周期管理** | 无 | 手动 | 完整 (load/init/shutdown/enable/disable) |
| **安装管理** | 放文件夹 | 改 JSON 配置 | 市场/URL/对话/UI |
| **权限模型** | 无 | 无 | 三级 (basic/advanced/system) |
| **多语言** | N/A (Markdown) | YES (任意语言) | Python (type=python) 或 任意 (type=mcp) |

---

## 三、系统接口访问权限

Plugin 与前两者最本质的区别在于：**开放了系统接口给插件，同时允许插件调用系统服务**。

但这种开放是**受控的**，通过 `PluginAPI` 和三级权限模型实现：

### Basic 级（安装即有，无需用户确认）

| 权限 | API 方法 | 说明 |
|------|---------|------|
| `tools.register` | `register_tools()` | 注册工具定义和处理函数 |
| `hooks.basic` | `register_hook("on_init" / "on_shutdown")` | 基础生命周期钩子 |
| `config.read` | `get_config()` | 读取本插件的配置 |
| `config.write` | `set_config()` | 写入本插件的配置 |
| `data.own` | `get_data_dir()` | 访问本插件的持久化数据目录 |
| `log` | `log()` | 写日志 |

### Advanced 级（安装时弹窗提示用户确认授权）

| 权限 | API 方法 | 风险提示 |
|------|---------|---------|
| `memory.read` | `get_memory_manager()` 只读 | "此插件可以读取你的对话记忆" |
| `memory.write` | `get_memory_manager()` 写入 | "此插件可以修改记忆数据" |
| `channel.register` | `register_channel()` | "此插件将添加新的消息通道" |
| `channel.send` | `send_message()` | "此插件可以通过 IM 通道发送消息" |
| `hooks.message` | `register_hook("on_message_*")` | "此插件可以拦截和修改消息" |
| `hooks.retrieve` | `register_hook("on_retrieve" / "on_prompt_build")` | "此插件可以向 AI 注入上下文" |
| `retrieval.register` | `register_retrieval_source()` | "此插件将添加知识检索来源" |
| `search.register` | `register_search_backend()` | "此插件将添加搜索引擎后端" |
| `routes.register` | `register_api_routes()` | "此插件将添加网络接口" |
| `brain.access` | `get_brain()` | "此插件可以直接调用 AI 模型" |
| `vector.access` | `get_vector_store()` | "此插件可以访问向量数据库" |
| `settings.read` | `get_settings()` | "此插件可以读取系统配置" |

### System 级（仅内置插件或需手动确认）

| 权限 | API 方法 | 说明 |
|------|---------|------|
| `llm.register` | `register_llm_provider()` | 注册新的 LLM 提供商 |
| `hooks.all` | 全部 10 个钩子 | 完整钩子访问权 |
| `memory.replace` | `register_memory_backend()` 替换模式 | 完全替换内置记忆系统 |
| `system.config.write` | — | 写入全局系统配置 |

---

## 四、架构关系图

```
┌─────────────────────────────────────────────────────┐
│                OpenAkita 宿主 (Host)                 │
│                                                     │
│  ┌──────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │  Brain   │ │MemoryManager│ │ MessageGateway   │  │
│  │  (LLM)   │ │  (记忆系统)  │ │  (IM 消息路由)   │  │
│  └────▲─────┘ └──────▲──────┘ └────────▲─────────┘  │
│       │              │                 │             │
│  ┌────┴──────────────┴─────────────────┴──────────┐  │
│  │              PluginAPI (受控接口)                │  │
│  │  ┌────────────────────────────────────────┐    │  │
│  │  │ 权限检查: basic / advanced / system     │    │  │
│  │  └────────────────────────────────────────┘    │  │
│  └──────────┬──────────────┬──────────────────────┘  │
│             │              │                         │
└─────────────┼──────────────┼─────────────────────────┘
              │              │
     ┌────────▼───┐   ┌──────▼──────┐   ┌─────────────┐
     │  Plugin A  │   │  Plugin B   │   │   Skill     │
     │ (Python)   │   │  (MCP 包装) │   │ (纯文本)     │
     │            │   │            │   │             │
     │ 能调:      │   │ 能调:      │   │ 不能调任何   │
     │ register_* │   │ 仅 tools   │   │ 系统服务     │
     │ get_*      │   │            │   │             │
     │ send_*     │   │ 进程隔离    │   │ 注入 prompt │
     └────────────┘   └────────────┘   └─────────────┘
```

---

## 五、选型指南

### 什么时候用 Skill

- 只需要引导 LLM 的行为（如 "遇到图片编辑优先用 GIMP CLI"）
- 不需要执行任何代码
- 不需要访问系统服务

### 什么时候用 MCP

- 已有现成的 MCP server（如 Notion 官方 MCP、GitHub MCP）
- 需要强进程隔离（不信任的第三方工具）
- 只需要提供工具，不需要其他能力
- 工具用非 Python 语言实现（Node.js / Go 等）

### 什么时候用 Plugin

- 需要访问 OpenAkita 内部服务（记忆、Brain、消息网关）
- 需要添加 IM 通道（WhatsApp、Line、Matrix 等）
- 需要替换内置模块（换记忆系统、换搜索后端、加 LLM provider）
- 需要 RAG 功能（向对话注入知识库内容）
- 需要挂钩子（拦截消息、在 prompt 中注入内容、定时任务）
- 需要统一的安装管理和市场分发

### 组合使用

Plugin 是 Skill 和 MCP 的**超集**，一个 Plugin 可以同时包含：

- `provides.skill: "SKILL.md"` — 内含 Skill 文本，引导 LLM
- `type: "mcp"` — 包装一个 MCP server，享有插件管理和市场分发
- `type: "python"` + `provides.tools` + `provides.hooks` — 完整的代码插件

---

## 六、向后兼容性

| 现有机制 | 插件化后的状态 |
|---------|-------------|
| `skills/` 目录下的 SKILL.md | 继续工作，不受影响 |
| `data/mcp_servers.json` 中配置的 MCP | 继续工作，不受影响 |
| `_init_handlers()` 中注册的内置工具 | 继续工作，插件工具与内置工具并存 |
| `channels/registry.py` 中的内置通道 | 继续工作，插件通道与内置通道并存 |

插件系统是**增量添加**，不会破坏任何现有功能。
