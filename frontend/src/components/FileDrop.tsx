import { useId, useRef, useState, type DragEvent } from "react";

type Props = {
  accept: string;
  label: string;
  hint?: string;
  file?: File | null;
  onFile: (file: File) => void;
  compact?: boolean;
};

export default function FileDrop({ accept, label, hint, file, onFile, compact }: Props) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onFile(f);
  };
  return (
    <div
      className={`filedrop ${over ? "over" : ""} ${compact ? "compact" : ""} ${file ? "has-file" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      onClick={() => input.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
      aria-label={label}
    >
      <input id={id} ref={input} type="file" accept={accept} hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
      <div className="filedrop-icon" aria-hidden>{file ? "✓" : "⬆"}</div>
      <div>
        <div className="filedrop-label">{file ? file.name : label}</div>
        <div className="muted small">{file ? `${(file.size / 1024).toFixed(0)} KB · click to change` : hint}</div>
      </div>
    </div>
  );
}
