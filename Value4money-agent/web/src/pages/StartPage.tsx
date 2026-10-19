import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDropzone } from "react-dropzone";
import { FileText, Play, Upload, X } from "lucide-react";
import { startRun } from "../api/client";
import { cn } from "../lib/utils";
import { staged, totalBytes, withoutFile } from "../lib/staging";
import { Button } from "../components/ui/button";
import { Card, CardBody } from "../components/ui/card";

/**
 * Choosing the bids is not the same as starting a review.
 *
 * A run writes audit rows and spends a model call the moment it begins, so nothing happens until a
 * person has looked at what they dropped and pressed the button. Everything is staged here first,
 * listed by name, and can be taken back out.
 */
export function StartPage() {
  const navigate = useNavigate();
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onDrop = useCallback((incoming: File[]) => {
    setError(null);
    setFiles((current) => staged(current, incoming));
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    multiple: true,
    disabled: busy,
  });

  const begin = async () => {
    if (files.length === 0) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const run = await startRun(files);
      navigate(`/runs/${run.run_id}`);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl px-5 py-10">
      <h1 className="text-2xl font-semibold tracking-tight text-ink-900">Start a tender review</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-700">
        Choose the bid PDFs for one tender. Hasibu reads them, finds comparable awards in the public
        record, and prepares an evidence file for the committee. It does not choose a bidder, and it
        does not name one as better than another.
      </p>

      <Card className="mt-6">
        <CardBody>
          <div
            {...getRootProps()}
            className={cn(
              "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center",
              isDragActive
                ? "border-accent bg-accent-soft"
                : busy
                  ? "cursor-wait border-ink-200 opacity-60"
                  : "cursor-pointer border-ink-300 hover:border-ink-400",
            )}
          >
            <Upload className="size-5 text-ink-400" />
            <p className="text-sm font-medium text-ink-900">
              {isDragActive ? "Drop the bid PDFs here" : "Drop the bid PDFs here, or choose files"}
            </p>
            <p className="text-xs text-ink-500">
              Every PDF must be a bid for the same tender. Two to eight is usual.
            </p>
            <input {...getInputProps()} />
          </div>

          {files.length > 0 && (
            <div className="mt-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-xs font-semibold tracking-wide text-ink-500 uppercase">
                  Ready to analyse · {files.length} {files.length === 1 ? "file" : "files"} ·{" "}
                  {totalBytes(files)}
                </p>
                <button
                  type="button"
                  onClick={() => setFiles([])}
                  className="text-xs text-ink-500 underline underline-offset-4 hover:text-ink-900"
                >
                  Remove all
                </button>
              </div>
              <ul className="mt-2 divide-y divide-ink-200 rounded-md border border-ink-200">
                {files.map((file) => (
                  <li key={file.name} className="flex items-center gap-3 px-3 py-2">
                    <FileText className="size-4 shrink-0 text-ink-400" />
                    <span className="min-w-0 flex-1 truncate text-sm text-ink-700">{file.name}</span>
                    <span className="shrink-0 text-xs tabular-nums text-ink-400">
                      {totalBytes([file])}
                    </span>
                    <button
                      type="button"
                      aria-label={`remove ${file.name}`}
                      onClick={() => setFiles((current) => withoutFile(current, file.name))}
                      className="shrink-0 rounded p-1 text-ink-400 hover:bg-ink-100 hover:text-ink-900"
                    >
                      <X className="size-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {error && (
            <p
              role="alert"
              className="mt-4 rounded-md border border-stop/30 bg-stop/5 px-3 py-2 text-sm text-stop"
            >
              {error}
            </p>
          )}

          <Button
            variant="accent"
            size="lg"
            className="mt-5 w-full"
            disabled={files.length === 0 || busy}
            onClick={begin}
          >
            <Play className="size-4" />
            {busy ? "Starting…" : "Start Analysis"}
          </Button>

          <p className="mt-3 text-xs text-ink-400">
            Bids are read through the same audited tools a terminal run uses. Nothing is filed until a
            named person approves it.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
