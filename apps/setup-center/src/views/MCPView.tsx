import { useEffect, useState, useCallback } from "react";
import {
  IconRefresh, IconLink, IconPlus, IconTrash, IconCheck, IconX,
  IconChevronDown, IconChevronRight, IconInfo,
  DotGreen, DotGray, DotYellow,
} from "../icons";

type MCPTool = {
  name: string;
  description: string;
};

type MCPServer = {
  name: string;
  description: string;
  transport: string;
  url: string;
  command: string;
  connected: boolean;
  tools: MCPTool[];
  tool_count: number;
  has_instructions: boolean;
  catalog_tool_count: number;
  source: "builtin" | "workspace";
  removable: boolean;
};

type AddServerForm = {
  name: string;
  transport: "stdio" | "streamable_http" | "sse";
  command: string;
  args: string;
  env: string;
  url: string;
  description: string;
  auto_connect: boolean;
};

const API_BASE = "http://127.0.0.1:18900";

const emptyForm: AddServerForm = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  env: "",
  url: "",
  description: "",
  auto_connect: false,
};

/**
 * Parse args string into an array, respecting quoted strings for paths with spaces.
 * Examples:
 *   '-m my_module'           -> ['-m', 'my_module']
 *   '"C:\\Program Files\\s.py"' -> ['C:\\Program Files\\s.py']
 *   '-y @scope/pkg'         -> ['-y', '@scope/pkg']
 *   (one arg per line)      -> each line is one arg
 */
function parseArgs(raw: string): string[] {
  const trimmed = raw.trim();
  if (!trimmed) return [];
  if (trimmed.includes("\n")) {
    return trimmed.split("\n").map(l => l.trim()).filter(Boolean);
  }
  const args: string[] = [];
  let current = "";
  let inQuote: string | null = null;
  for (const ch of trimmed) {
    if (inQuote) {
      if (ch === inQuote) { inQuote = null; }
      else { current += ch; }
    } else if (ch === '"' || ch === "'") {
      inQuote = ch;
    } else if (ch === " " || ch === "\t") {
      if (current) { args.push(current); current = ""; }
    } else {
      current += ch;
    }
  }
  if (current) args.push(current);
  return args;
}

export function MCPView({ serviceRunning }: { serviceRunning: boolean }) {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [mcpEnabled, setMcpEnabled] = useState(true);
  const [loading, setLoading] = useState(false);
  const [expandedServer, setExpandedServer] = useState<string | null>(null);
  const [instructions, setInstructions] = useState<Record<string, string>>({});
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<AddServerForm>({ ...emptyForm });
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);

  const fetchServers = useCallback(async () => {
    if (!serviceRunning) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/mcp/servers`);
      if (res.ok) {
        const data = await res.json();
        setServers(data.servers || []);
        setMcpEnabled(data.mcp_enabled !== false);
      }
    } catch { /* ignore */ }
    setLoading(false);
  }, [serviceRunning]);

  useEffect(() => { fetchServers(); }, [fetchServers]);

  const showMsg = (text: string, ok: boolean) => {
    setMessage({ text, ok });
    setTimeout(() => setMessage(null), ok ? 4000 : 8000);
  };

  const connectServer = async (name: string) => {
    setBusy(name);
    try {
      const res = await fetch(`${API_BASE}/api/mcp/connect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_name: name }),
      });
      const data = await res.json();
      if (data.status === "connected" || data.status === "already_connected") {
        showMsg(`已连接 ${name}`, true);
        await fetchServers();
      } else {
        showMsg(`连接失败: ${data.error || "未知错误"}`, false);
      }
    } catch (e) {
      showMsg(`连接异常: ${e}`, false);
    }
    setBusy(null);
  };

  const disconnectServer = async (name: string) => {
    setBusy(name);
    try {
      await fetch(`${API_BASE}/api/mcp/disconnect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server_name: name }),
      });
      showMsg(`已断开 ${name}`, true);
      await fetchServers();
    } catch (e) {
      showMsg(`断开异常: ${e}`, false);
    }
    setBusy(null);
  };

  const removeServer = async (name: string) => {
    if (!confirm(`确定删除 MCP 服务器 "${name}"？`)) return;
    setBusy(name);
    try {
      const res = await fetch(`${API_BASE}/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      const data = await res.json();
      if (data.status === "ok") {
        showMsg(`已删除 ${name}`, true);
        await fetchServers();
      } else {
        showMsg(`删除失败: ${data.message || "未知错误"}`, false);
      }
    } catch (e) {
      showMsg(`删除失败: ${e}`, false);
    }
    setBusy(null);
  };

  const addServer = async () => {
    const name = form.name.trim();
    if (!name) { showMsg("请输入服务器名称", false); return; }
    if (!/^[a-zA-Z0-9_-]+$/.test(name)) { showMsg("名称只能含字母、数字、连字符、下划线", false); return; }
    if (form.transport === "stdio" && !form.command.trim()) { showMsg("stdio 模式需要填写启动命令", false); return; }
    if ((form.transport === "streamable_http" || form.transport === "sse") && !form.url.trim()) { showMsg(`${form.transport === "sse" ? "SSE" : "HTTP"} 模式需要填写 URL`, false); return; }
    setBusy("add");
    try {
      const envObj: Record<string, string> = {};
      if (form.env.trim()) {
        for (const line of form.env.trim().split("\n")) {
          const idx = line.indexOf("=");
          if (idx > 0) envObj[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        }
      }
      const parsedArgs = parseArgs(form.args);
      const res = await fetch(`${API_BASE}/api/mcp/servers/add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          transport: form.transport,
          command: form.command.trim(),
          args: parsedArgs,
          env: envObj,
          url: form.url.trim(),
          description: form.description.trim(),
          auto_connect: form.auto_connect,
        }),
      });
      const data = await res.json();
      if (res.ok && data.status === "ok") {
        const cr = data.connect_result;
        let connMsg = "";
        if (cr) {
          if (cr.connected) {
            connMsg = `，已连接（发现 ${cr.tool_count ?? 0} 个工具）`;
          } else {
            connMsg = `\n⚠️ 自动连接失败: ${cr.error || "未知原因"}，可稍后在列表中手动连接`;
          }
        }
        showMsg(`✅ 已添加 ${name}${connMsg}`, !cr || cr.connected !== false);
        setForm({ ...emptyForm });
        setShowAdd(false);
        await fetchServers();
      } else {
        showMsg(`添加失败: ${data.message || data.error || "未知错误"}`, false);
      }
    } catch (e) {
      showMsg(`添加异常: ${e}`, false);
    }
    setBusy(null);
  };

  const loadInstructions = async (name: string) => {
    if (instructions[name]) return;
    try {
      const res = await fetch(`${API_BASE}/api/mcp/instructions/${encodeURIComponent(name)}`);
      if (res.ok) {
        const data = await res.json();
        setInstructions(prev => ({ ...prev, [name]: data.instructions || "无使用说明" }));
      }
    } catch { /* ignore */ }
  };

  const toggleExpand = (name: string) => {
    if (expandedServer === name) {
      setExpandedServer(null);
    } else {
      setExpandedServer(name);
      loadInstructions(name);
    }
  };

  if (!serviceRunning) {
    return (
      <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
        <IconLink size={32} style={{ marginBottom: 12, opacity: 0.5 }} />
        <p style={{ fontSize: 15 }}>服务未运行，请先启动 OpenAkita 服务</p>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <IconLink size={20} />
          <span style={{ fontSize: 16, fontWeight: 600 }}>MCP 服务器管理</span>
          {!mcpEnabled && (
            <span style={{
              background: "var(--warn-bg, #fef3c7)", color: "var(--warn, #d97706)",
              fontSize: 12, padding: "2px 8px", borderRadius: 4,
            }}>
              MCP 已禁用
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btnSecondary"
            onClick={() => setShowAdd(!showAdd)}
            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13, padding: "4px 12px" }}
          >
            <IconPlus size={14} /> 添加服务器
          </button>
          <button
            className="btnSecondary"
            onClick={fetchServers}
            disabled={loading}
            style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13, padding: "4px 12px" }}
          >
            <IconRefresh size={14} /> 刷新
          </button>
        </div>
      </div>

      {/* Message bar */}
      {message && (
        <div style={{
          padding: "8px 14px", borderRadius: 6, marginBottom: 12, fontSize: 13,
          background: message.ok ? "var(--ok-bg, #dcfce7)" : "var(--err-bg, #fee2e2)",
          color: message.ok ? "var(--ok, #16a34a)" : "var(--err, #dc2626)",
          display: "flex", alignItems: "flex-start", gap: 6, whiteSpace: "pre-line",
        }}>
          <span style={{ marginTop: 1, flexShrink: 0 }}>{message.ok ? <IconCheck size={14} /> : <IconX size={14} />}</span>
          <span>{message.text}</span>
        </div>
      )}

      {/* Add server form */}
      {showAdd && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>添加 MCP 服务器</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 16px" }}>
            <div>
              <label className="label">服务器名称 *</label>
              <input className="input" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="如: web-search" />
            </div>
            <div>
              <label className="label">描述</label>
              <input className="input" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="服务器用途说明" />
            </div>
            <div>
              <label className="label">传输协议</label>
              <select className="input" value={form.transport} onChange={e => setForm({ ...form, transport: e.target.value as "stdio" | "streamable_http" | "sse" })}>
                <option value="stdio">stdio (标准输入输出)</option>
                <option value="streamable_http">Streamable HTTP</option>
                <option value="sse">SSE (Server-Sent Events)</option>
              </select>
            </div>
            {form.transport === "stdio" ? (
              <>
                <div>
                  <label className="label">启动命令 *</label>
                  <input className="input" value={form.command} onChange={e => setForm({ ...form, command: e.target.value })} placeholder="如: python, npx, node" />
                </div>
                <div style={{ gridColumn: "1 / -1" }}>
                  <label className="label">参数 (空格分隔，或每行一个；路径含空格请用引号包裹)</label>
                  <textarea
                    className="input"
                    value={form.args}
                    onChange={e => setForm({ ...form, args: e.target.value })}
                    placeholder={'如: -m openakita.mcp_servers.web_search\n或每行一个参数:\n-y\n@anthropic/mcp-server-filesystem\n"C:\\My Path\\dir"'}
                    rows={2}
                    style={{ resize: "vertical", fontFamily: "monospace", fontSize: 12 }}
                  />
                </div>
              </>
            ) : (
              <div>
                <label className="label">URL *</label>
                <input className="input" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })}
                  placeholder={form.transport === "sse" ? "如: http://127.0.0.1:8080/sse" : "如: http://127.0.0.1:12306/mcp"} />
              </div>
            )}
            <div style={{ gridColumn: "1 / -1" }}>
              <label className="label">环境变量 (每行一个，格式 KEY=VALUE)</label>
              <textarea
                className="input"
                value={form.env}
                onChange={e => setForm({ ...form, env: e.target.value })}
                placeholder={"API_KEY=sk-xxx\nMY_VAR=hello"}
                rows={3}
                style={{ resize: "vertical", fontFamily: "monospace", fontSize: 12 }}
              />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 14, justifyContent: "space-between", alignItems: "center" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--muted)", cursor: "pointer" }}>
              <input type="checkbox" checked={form.auto_connect} onChange={e => setForm({ ...form, auto_connect: e.target.checked })} />
              启动时自动连接
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btnSecondary" onClick={() => { setShowAdd(false); setForm({ ...emptyForm }); }} style={{ fontSize: 13, padding: "6px 16px" }}>
                取消
              </button>
              <button className="btnPrimary" onClick={addServer} disabled={busy === "add"} style={{ fontSize: 13, padding: "6px 16px" }}>
                {busy === "add" ? "添加中..." : "添加"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Server list */}
      {loading && servers.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: 30, color: "var(--muted)" }}>
          加载中...
        </div>
      ) : servers.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
          <p style={{ fontSize: 15, marginBottom: 8 }}>暂无 MCP 服务器配置</p>
          <p style={{ fontSize: 13 }}>
            在项目 <code>mcps/</code> 目录下添加服务器配置，或点击上方"添加服务器"按钮
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {servers.map(s => (
            <div key={s.name} className="card" style={{ padding: 0 }}>
              {/* Server header */}
              <div
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "12px 16px", cursor: "pointer",
                }}
                onClick={() => toggleExpand(s.name)}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  {expandedServer === s.name ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                  {s.connected ? <DotGreen /> : <DotGray />}
                  <span style={{ fontWeight: 600, fontSize: 14 }}>{s.name}</span>
                  <span style={{ fontSize: 12, color: "var(--muted)", background: "var(--bg-subtle, #f1f5f9)", padding: "1px 6px", borderRadius: 3 }}>
                    {s.transport === "streamable_http" ? "HTTP" : s.transport === "sse" ? "SSE" : "stdio"}
                  </span>
                  <span style={{
                    fontSize: 11, padding: "1px 6px", borderRadius: 3,
                    background: s.source === "workspace" ? "var(--ok-bg, #dcfce7)" : "var(--bg-subtle, #f1f5f9)",
                    color: s.source === "workspace" ? "var(--ok, #16a34a)" : "var(--muted)",
                  }}>
                    {s.source === "workspace" ? "工作区" : "内置"}
                  </span>
                  {s.description && (
                    <span style={{ fontSize: 12, color: "var(--muted)" }}>— {s.description}</span>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }} onClick={e => e.stopPropagation()}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>
                    {s.connected ? `${s.tool_count} 工具` : `${s.catalog_tool_count} 工具 (目录)`}
                  </span>
                  {s.connected ? (
                    <button
                      className="btnSecondary"
                      onClick={() => disconnectServer(s.name)}
                      disabled={busy === s.name}
                      style={{ fontSize: 12, padding: "3px 10px", color: "var(--warn, #d97706)" }}
                    >
                      断开
                    </button>
                  ) : (
                    <button
                      className="btnPrimary"
                      onClick={() => connectServer(s.name)}
                      disabled={busy === s.name}
                      style={{ fontSize: 12, padding: "3px 10px" }}
                    >
                      {busy === s.name ? "连接中..." : "连接"}
                    </button>
                  )}
                  {s.removable && (
                    <button
                      className="btnSecondary"
                      onClick={() => removeServer(s.name)}
                      disabled={busy === s.name}
                      style={{ fontSize: 12, padding: "3px 8px", color: "var(--err, #dc2626)" }}
                      title="删除服务器"
                    >
                      <IconTrash size={13} />
                    </button>
                  )}
                </div>
              </div>

              {/* Expanded details */}
              {expandedServer === s.name && (
                <div style={{ borderTop: "1px solid var(--line, #e5e7eb)", padding: "12px 16px" }}>
                  {/* Connection info */}
                  <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10 }}>
                    {s.transport === "streamable_http" || s.transport === "sse" ? (
                      <span>{s.transport === "sse" ? "SSE" : "HTTP"} URL: <code>{s.url}</code></span>
                    ) : (
                      <span>命令: <code>{s.command}</code></span>
                    )}
                  </div>

                  {/* Tools */}
                  {s.tools.length > 0 ? (
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>
                        可用工具 ({s.tools.length})
                      </div>
                      <div style={{ display: "grid", gap: 6 }}>
                        {s.tools.map(t => (
                          <div key={t.name} style={{
                            background: "var(--bg-subtle, #f8fafc)", borderRadius: 6, padding: "8px 12px",
                          }}>
                            <div style={{ fontWeight: 500, fontSize: 13 }}>{t.name}</div>
                            {t.description && (
                              <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
                                {t.description}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : !s.connected ? (
                    <div style={{ fontSize: 13, color: "var(--muted)" }}>
                      <DotYellow /> 连接后可查看可用工具
                    </div>
                  ) : (
                    <div style={{ fontSize: 13, color: "var(--muted)" }}>此服务器未暴露任何工具</div>
                  )}

                  {/* Instructions */}
                  {s.has_instructions && instructions[s.name] && (
                    <details style={{ marginTop: 12 }}>
                      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600, color: "var(--primary, #3b82f6)" }}>
                        <IconInfo size={13} style={{ verticalAlign: "middle", marginRight: 4 }} />
                        使用说明
                      </summary>
                      <pre style={{
                        marginTop: 8, padding: 12, background: "var(--bg-subtle, #f8fafc)",
                        borderRadius: 6, fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word",
                        maxHeight: 300, overflow: "auto",
                      }}>
                        {instructions[s.name]}
                      </pre>
                    </details>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Help text */}
      <div style={{ marginTop: 16, fontSize: 12, color: "var(--muted)", lineHeight: 1.8 }}>
        <strong>MCP (Model Context Protocol)</strong> 让 Agent 通过标准化协议调用外部工具和服务。
        <br />
        支持三种传输协议：<code>stdio</code>（本地进程）、<code>Streamable HTTP</code>（远程服务）和 <code>SSE</code>（兼容旧版 MCP 服务器）。
        <br />
        内置配置位于 <code>mcps/</code> 目录，用户/AI 添加的配置保存在 <code>data/mcp/servers/</code> 目录，每个服务器一个子目录。
      </div>
    </div>
  );
}
