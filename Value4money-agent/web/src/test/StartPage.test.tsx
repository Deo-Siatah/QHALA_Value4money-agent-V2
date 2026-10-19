import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

/**
 * The rule this page exists to enforce: choosing files is not the same as starting a run.
 *
 * A run writes audit rows and spends a model call the moment it begins, so a mis-drop must not cost
 * anything until a person has looked at what was dropped and pressed the button.
 *
 * Everything here waits for the staged list to appear rather than asserting straight after the change
 * event: the dropzone reads the files it was given asynchronously, so an immediate assertion would be
 * asserting on a race.
 */

const startRun = vi.fn();

vi.mock("../api/client", () => ({
  startRun: (...args: unknown[]) => startRun(...args),
  decide: vi.fn(),
  getRun: vi.fn(),
  listRuns: vi.fn(() => Promise.resolve([])),
  getReportMarkdown: vi.fn(() => Promise.resolve("")),
  reportUrl: (id: string, format: string) => `/api/runs/${id}/report.${format}`,
  subscribe: vi.fn(() => () => {}),
}));

const { StartPage } = await import("../pages/StartPage");

function pdfFile(name: string) {
  return new File([new Uint8Array(2048)], name, { type: "application/pdf" });
}

async function choose(name: string) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [pdfFile(name)] } });
  await screen.findByText(name);
}

function startButton() {
  return screen.getByRole("button", { name: /start analysis/i });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <StartPage />
    </MemoryRouter>,
  );
}

describe("starting a review", () => {
  beforeEach(() => {
    startRun.mockReset();
    startRun.mockResolvedValue({ run_id: "run-1", status: "queued" });
  });

  it("does not start anything the moment files are chosen", async () => {
    renderPage();

    await choose("apex.pdf");

    expect(startRun).not.toHaveBeenCalled();
  });

  it("keeps the button out of reach until there is something to start", async () => {
    renderPage();
    expect(startButton()).toBeDisabled();

    await choose("apex.pdf");

    expect(startButton()).toBeEnabled();
  });

  it("starts only when the button is pressed, with exactly what was staged", async () => {
    renderPage();
    await choose("apex.pdf");
    await choose("bora.pdf");

    fireEvent.click(startButton());

    await waitFor(() => expect(startRun).toHaveBeenCalledTimes(1));
    expect(startRun.mock.calls[0][0].map((file: File) => file.name)).toEqual(["apex.pdf", "bora.pdf"]);
  });

  it("names the files it is about to start on, so a mis-drop is caught before the click", async () => {
    renderPage();

    await choose("apex.pdf");

    expect(screen.getByText("apex.pdf")).toBeInTheDocument();
  });

  it("takes the file back out again, and stops offering to start once nothing is left", async () => {
    renderPage();
    await choose("apex.pdf");
    await choose("bora.pdf");

    fireEvent.click(screen.getByRole("button", { name: /remove bora\.pdf/i }));
    await waitFor(() => expect(screen.queryByText("bora.pdf")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /remove apex\.pdf/i }));
    await waitFor(() => expect(screen.queryByText("apex.pdf")).not.toBeInTheDocument());

    expect(startButton()).toBeDisabled();
  });

  it("says what went wrong in the server's own words rather than a bare failure", async () => {
    startRun.mockRejectedValue(new Error("apex.pdf is not a PDF"));
    renderPage();
    await choose("apex.pdf");

    fireEvent.click(startButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("apex.pdf is not a PDF");
  });
});