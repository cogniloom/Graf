import { FileText, ChevronRight } from "lucide-react";
function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
export function Relationship({
  record,
  documentId,
  names,
  onSelect,
}: {
  record: unknown;
  documentId: string;
  names: Map<string, string>;
  onSelect: (id: string) => void;
}) {
  const item = object(record);
  let derivation = object(item.derivation_json);
  let invalid = false;
  if (typeof item.derivation_json === "string") {
    try {
      derivation = object(JSON.parse(item.derivation_json));
    } catch {
      invalid = true;
    }
  }
  const type =
    typeof item.relation_type === "string"
      ? item.relation_type
      : typeof item.type === "string"
        ? item.type
        : "Recorded relationship";
  const from =
    typeof item.from_node === "string"
      ? item.from_node
      : typeof item.source === "string"
        ? item.source
        : undefined;
  const to =
    typeof item.to_node === "string"
      ? item.to_node
      : typeof item.target === "string"
        ? item.target
        : undefined;
  const connected = from === documentId ? to : (from ?? to);
  const label = connected ? names.get(connected) || connected : "";
  const observed =
    typeof derivation.observed_value === "string"
      ? derivation.observed_value
      : typeof derivation.literal === "string"
        ? derivation.literal
        : null;
  return (
    <article className="relationship-record">
      <div className="row between">
        <h4>{type.replaceAll("_", " ").toLowerCase()}</h4>
        {typeof item.status === "string" && (
          <span className="relationship-status">{item.status}</span>
        )}
      </div>
      {observed && (
        <p>
          <small>Observed reference</small>
          <span className="observed-reference">{observed}</span>
        </p>
      )}
      {typeof derivation.basis === "string" && (
        <p className="muted">Basis: {derivation.basis.replaceAll("_", " ")}</p>
      )}
      {connected && connected !== documentId ? (
        <button
          className="connected-document"
          onClick={() => onSelect(connected)}
          title={label}
        >
          <FileText size={18} />
          <span>{label.startsWith("/") ? label.split("/").at(-1) : label}</span>
          <ChevronRight size={16} />
        </button>
      ) : (
        <p className="muted">No resolved document returned.</p>
      )}
      {Array.isArray(derivation.does_not_establish) && (
        <p className="muted">
          Does not establish:{" "}
          {derivation.does_not_establish
            .filter((x) => typeof x === "string")
            .join(", ")}
          .
        </p>
      )}
      {invalid && (
        <p className="warning">Reference details could not be read.</p>
      )}
      <details>
        <summary>Evidence record</summary>
        <pre>{JSON.stringify(record, null, 2)}</pre>
      </details>
    </article>
  );
}
