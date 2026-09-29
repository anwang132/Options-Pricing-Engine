import { useId, type ReactNode } from "react";
import { ApiError } from "../api/client";

export function Field(props: {
  label: string;
  unit?: string;
  hint?: string;
  error?: string;
  children: (id: string, describedBy: string | undefined) => ReactNode;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const describedBy = [props.hint ? hintId : null, props.error ? errId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`field${props.error ? " field--error" : ""}`}>
      <label htmlFor={id}>
        {props.label}
        {props.unit && <span className="unit"> ({props.unit})</span>}
      </label>
      {props.children(id, describedBy)}
      {props.hint && (
        <small id={hintId} className="hint">
          {props.hint}
        </small>
      )}
      {props.error && (
        <small id={errId} className="error-text" role="alert">
          {props.error}
        </small>
      )}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error instanceof ApiError ? error : new ApiError(0, "client_error", String(error));
  const supported = e.details?.engines_supporting_request as string[] | undefined;
  const minSteps = e.details?.minimum_valid_steps as number | undefined;
  return (
    <div className="callout callout--error" role="alert">
      <strong>{humanCode(e.code)}</strong>
      <p>{e.message}</p>
      {supported && <p>Engines that support this request: {supported.length ? supported.join(", ") : "none"}.</p>}
      {minSteps !== undefined && <p>Minimum valid tree steps for these inputs: {minSteps}.</p>}
      {Array.isArray(e.details?.errors) && (
        <ul>
          {(e.details.errors as { loc: string[]; msg: string }[]).map((x, i) => (
            <li key={i}>
              <code>{x.loc.slice(1).join(".")}</code>: {x.msg}
            </li>
          ))}
        </ul>
      )}
      <small>
        Error code <code>{e.code}</code>
        {e.status ? ` · HTTP ${e.status}` : ""}
      </small>
    </div>
  );
}

const CODE_TEXT: Record<string, string> = {
  unsupported_combination: "Unsupported engine for this contract",
  invalid_tree_probability: "Tree setup rejected: invalid risk-neutral probability",
  work_limit_exceeded: "Work limit exceeded",
  invalid_request: "Invalid input",
  expired_contract: "Contract has expired",
  network_error: "API unreachable",
};

export function humanCode(code: string): string {
  return CODE_TEXT[code] ?? code.replace(/_/g, " ");
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Loading({ label }: { label: string }) {
  return (
    <p className="loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" /> {label}
    </p>
  );
}

export function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "ok"
      ? "good"
      : status === "one_sided" || status === "unstable_low_vega"
        ? "warn"
        : status === "not_supported" || status === "not_applicable_at_expiry"
          ? "muted"
          : "bad";
  const icon = tone === "good" ? "✓" : tone === "warn" ? "!" : tone === "muted" ? "–" : "✕";
  return (
    <span className={`badge badge--${tone}`}>
      <span aria-hidden="true">{icon}</span> {status.replace(/_/g, " ")}
    </span>
  );
}
