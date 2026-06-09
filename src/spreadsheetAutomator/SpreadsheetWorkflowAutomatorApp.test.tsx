import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

describe("Spreadsheet Workflow Automator route", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/workflows");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/workflows/interview")) {
        return Response.json({
          status: "needs_interview",
          ready_to_generate: false,
          required_questions: ["Which source spreadsheet files are required for this recurring workflow?"],
        });
      }
      if (path.endsWith("/api/workflows/generate")) {
        return Response.json({
          status: "generated",
          ready_to_generate: true,
          filename: "spreadsheet-workflow-automator.html",
          content_type: "text/html",
          html: "<!doctype html><html><body>Spreadsheet Workflow Automator</body></html>",
        });
      }
      return Response.json({ detail: "unexpected request" }, { status: 500 });
    }));
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:spreadsheet-workflow-automator"),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.pushState({}, "", "/");
  });

  it("renders a separate workflow app instead of the Filechat chat UI", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Spreadsheet Workflow Automator" })).toBeInTheDocument();
    expect(screen.queryByText("Attach files")).not.toBeInTheDocument();
  });

  it("shows interview questions for vague workflow descriptions", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    fireEvent.change(screen.getByLabelText("Workflow description"), {
      target: { value: "automate my spreadsheets" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Interview" }));

    expect(await screen.findByText("Which source spreadsheet files are required for this recurring workflow?")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/workflows/interview");
  });

  it("offers a local HTML download when generation succeeds", async () => {
    render(<App />);

    fireEvent.change(screen.getByLabelText("Workflow description"), {
      target: { value: "turn my weekly spreadsheet copy/paste/edit reconciliation into a local HTML app" },
    });
    fireEvent.change(screen.getByLabelText("Source file summaries JSON"), {
      target: {
        value: JSON.stringify([
          { file_id: "inventory", file_name: "inventory.csv", text: "sku,qty\nA-1,10\n" },
          { file_id: "orders", file_name: "orders.csv", text: "sku,qty\nA-1,12\n" },
        ]),
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    const link = await screen.findByRole("link", { name: "Download local HTML app" });
    await waitFor(() => expect(link).toHaveAttribute("href", "blob:spreadsheet-workflow-automator"));
    expect(link).toHaveAttribute("download", "spreadsheet-workflow-automator.html");
  });
});
