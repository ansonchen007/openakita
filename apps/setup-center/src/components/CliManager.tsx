import { useEffect, useState } from "react";
import { invoke } from "../platform";

export function CliManager() {
  const [cliStatus, setCliStatus] = useState<{
    registeredCommands: string[];
    inPath: boolean;
    binDir: string;
  } | null>(null);
  const [cliLoading, setCliLoading] = useState(false);
  const [cliMsg, setCliMsg] = useState("");
  const [cliRegOpenakita, setCliRegOpenakita] = useState(true);
  const [cliRegOa, setCliRegOa] = useState(true);
  const [cliRegPath, setCliRegPath] = useState(true);

  useEffect(() => {
    loadCliStatus();
  }, []);

  async function loadCliStatus() {
    try {
      const status = await invoke<{ registeredCommands: string[]; inPath: boolean; binDir: string }>("get_cli_status");
      setCliStatus(status);
      setCliRegOpenakita(status.registeredCommands.includes("openakita"));
      setCliRegOa(status.registeredCommands.includes("oa"));
      setCliRegPath(status.inPath);
    } catch (e) {
      setCliMsg(`查询 CLI 状态失败: ${String(e)}`);
    }
  }

  async function doRegister() {
    const cmds: string[] = [];
    if (cliRegOpenakita) cmds.push("openakita");
    if (cliRegOa) cmds.push("oa");
    if (cmds.length === 0) {
      setCliMsg("请至少选择一个命令名称");
      return;
    }
    setCliLoading(true);
    setCliMsg("");
    try {
      const result = await invoke<string>("register_cli", { commands: cmds, addToPath: cliRegPath });
      setCliMsg(`✓ ${result}`);
      await loadCliStatus();
    } catch (e) {
      setCliMsg(`✗ 注册失败: ${String(e)}`);
    } finally {
      setCliLoading(false);
    }
  }

  async function doUnregister() {
    setCliLoading(true);
    setCliMsg("");
    try {
      const result = await invoke<string>("unregister_cli");
      setCliMsg(`✓ ${result}`);
      await loadCliStatus();
    } catch (e) {
      setCliMsg(`✗ 注销失败: ${String(e)}`);
    } finally {
      setCliLoading(false);
    }
  }

  const hasRegistered = cliStatus && cliStatus.registeredCommands.length > 0;

  return (
    <div style={{ padding: "0 0 8px" }}>
      {cliStatus && hasRegistered && (
        <div style={{ background: "rgba(34,197,94,0.08)", borderRadius: 8, padding: "10px 14px", marginBottom: 12 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>已注册命令</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13 }}>
            {cliStatus.registeredCommands.map(cmd => (
              <code key={cmd} style={{ padding: "2px 8px", background: "rgba(0,0,0,0.08)", borderRadius: 4, fontSize: 12 }}>{cmd}</code>
            ))}
            {cliStatus.inPath ? (
              <span style={{ color: "#22c55e", fontSize: 12 }}>(已在 PATH 中)</span>
            ) : (
              <span style={{ color: "#f59e0b", fontSize: 12 }}>(未在 PATH 中)</span>
            )}
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>目录: {cliStatus.binDir}</div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 12 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
          <input type="checkbox" checked={cliRegOpenakita} onChange={() => setCliRegOpenakita(!cliRegOpenakita)} style={{ width: 16, height: 16, flexShrink: 0 }} />
          <span><strong>openakita</strong> — 完整命令</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
          <input type="checkbox" checked={cliRegOa} onChange={() => setCliRegOa(!cliRegOa)} style={{ width: 16, height: 16, flexShrink: 0 }} />
          <span><strong>oa</strong> — 简短别名</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
          <input type="checkbox" checked={cliRegPath} onChange={() => setCliRegPath(!cliRegPath)} style={{ width: 16, height: 16, flexShrink: 0 }} />
          <span>添加到系统 PATH</span>
        </label>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button className="btnPrimary" onClick={doRegister} disabled={cliLoading} style={{ fontSize: 13 }}>
          {cliLoading ? "处理中..." : hasRegistered ? "更新注册" : "注册"}
        </button>
        {hasRegistered && (
          <button onClick={doUnregister} disabled={cliLoading} style={{ fontSize: 13 }}>
            注销全部
          </button>
        )}
      </div>

      {cliMsg && (
        <div style={{
          marginTop: 8, padding: "6px 10px", borderRadius: 6, fontSize: 12,
          background: cliMsg.startsWith("✓") ? "rgba(34,197,94,0.1)" : cliMsg.startsWith("✗") ? "rgba(239,68,68,0.1)" : "rgba(245,158,11,0.1)",
          color: cliMsg.startsWith("✓") ? "#22c55e" : cliMsg.startsWith("✗") ? "#ef4444" : "#f59e0b",
        }}>
          {cliMsg}
        </div>
      )}
    </div>
  );
}
