import { useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../utils/chatTypes";
import { SVG_PATHS } from "../utils/chatHelpers";

export function RenderIcon({ icon, size = 14 }: { icon: string; size?: number }) {
  if (icon.startsWith("svg:")) {
    const d = SVG_PATHS[icon.slice(4)];
    if (!d) return <span>{icon}</span>;
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
        <path d={d} />
      </svg>
    );
  }
  return <>{icon}</>;
}

export function SubAgentCards({ tasks }: { tasks: SubAgentTask[] }) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 4;
  const totalPages = Math.ceil(tasks.length / PAGE_SIZE);
  const visible = tasks.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const statusLabel = (s: string) => {
    switch (s) {
      case "starting": return t("chat.subAgentStarting", "启动中");
      case "running": return t("chat.subAgentRunning", "执行中");
      case "completed": return t("chat.subAgentDone", "已完成");
      case "error": return t("chat.subAgentError", "出错");
      case "timeout": return t("chat.subAgentTimeout", "超时");
      case "cancelled": return t("chat.subAgentCancelled", "已取消");
      default: return s;
    }
  };

  const statusClass = (s: string) => {
    switch (s) {
      case "starting":
      case "running": return "sacBadgeRunning";
      case "completed": return "sacBadgeDone";
      case "error": return "sacBadgeError";
      case "timeout": return "sacBadgeTimeout";
      default: return "";
    }
  };

  const formatElapsed = (s: number) => {
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}m${sec > 0 ? sec + "s" : ""}`;
  };

  return (
    <div className="sacContainer">
      <div className="sacHeader">
        <span className="sacTitle">{t("chat.subAgentPanel", "子 Agent 进度")}</span>
        {totalPages > 1 && (
          <div className="sacPager">
            <button className="sacPageBtn" disabled={page <= 0} onClick={() => setPage(p => p - 1)}>‹</button>
            <span className="sacPageInfo">{page + 1}/{totalPages}</span>
            <button className="sacPageBtn" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>›</button>
          </div>
        )}
      </div>
      <div className="sacGrid" ref={scrollRef}>
        {visible.map((task) => (
          <div key={task.agent_id} className={`sacCard ${task.status === "running" || task.status === "starting" ? "sacCardActive" : ""}`}>
            <div className="sacCardTop">
              <span className="sacIcon"><RenderIcon icon={task.icon} size={16} /></span>
              <span className="sacName">{task.name}</span>
              <span className={`sacBadge ${statusClass(task.status)}`}>
                {(task.status === "running" || task.status === "starting") && <span className="sacPulse" />}
                {statusLabel(task.status)}
              </span>
            </div>
            <div className="sacCardMeta">
              <span>{t("chat.subAgentIter", "迭代")} {task.iteration}</span>
              <span className="sacDot">·</span>
              <span>{formatElapsed(task.elapsed_s)}</span>
              <span className="sacDot">·</span>
              <span>{t("chat.subAgentTools", "工具")} ×{task.tools_total}</span>
            </div>
            <div className="sacToolList">
              {task.tools_executed.length === 0 && (
                <div className="sacToolItem sacToolWaiting">…</div>
              )}
              {task.tools_executed.map((tool, idx) => {
                const isCurrent = idx === task.tools_executed.length - 1 && (task.status === "running" || task.status === "starting");
                return (
                  <div key={`${tool}-${idx}`} className={`sacToolItem ${isCurrent ? "sacToolCurrent" : ""}`}>
                    <span className="sacToolArrow">{isCurrent ? "▸" : "▹"}</span>
                    <span className="sacToolName">{tool}</span>
                    {isCurrent && <span className="sacToolBlink" />}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
