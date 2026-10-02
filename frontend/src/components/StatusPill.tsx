import { CheckCircle2, XCircle } from "lucide-react";

/** 顶栏的状态点。「不知道」(`undefined`) 与「不可用」一样按警告渲染 —— 自检没回来之前不该显示成通过。 */
export function StatusPill({ ok, label }: { ok?: boolean; label: string }) {
  return (
    <span className={`status-pill ${ok ? "is-ok" : "is-warn"}`}>
      {ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
      {label}
    </span>
  );
}
