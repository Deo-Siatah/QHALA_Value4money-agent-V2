// The chosen bid PDFs, before anything is started.
//
// This is the whole of "do not begin until a person says so". It is plain functions rather than
// component state because the rule that matters is that choosing files changes nothing about the run:
// a run writes audit rows and spends a model call the moment it starts, so a mis-drop must cost
// nothing until someone has looked at it.

/** What one document weighs, in the units a person reads rather than in bytes. */
export function totalBytes(files: File[]): string {
  const bytes = files.reduce((sum, file) => sum + file.size, 0);
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(0)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Add newly chosen files to what is already staged, keeping PDFs and dropping exact repeats. */
export function staged(current: File[], incoming: File[]): File[] {
  const kept = current.filter((file) => file.name.toLowerCase().endsWith(".pdf"));
  const names = new Set(kept.map((file) => file.name));
  for (const file of incoming) {
    if (file.name.toLowerCase().endsWith(".pdf") && !names.has(file.name)) {
      kept.push(file);
      names.add(file.name);
    }
  }
  return kept;
}

/** Take one file away by name. The whole name has to match, so bora.pdf never removes bora-2.pdf. */
export function withoutFile(files: File[], name: string): File[] {
  return files.filter((file) => file.name !== name);
}