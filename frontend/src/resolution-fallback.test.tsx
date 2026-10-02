import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";
import App from "./App";
import {
  automaticResolutionFallback,
  playlistFallbackJobPayload,
  playlistJobPayload,
  resolutionFallback,
  singleFallbackJobPayload,
  unselectableResolutionFallback
} from "./test/appFixtures";
import { installAppHarness, mockState } from "./test/appHarness";

installAppHarness();

describe("App · 清晰度降级提示与重启", () => {
  test("shows single video resolution fallback and restarts with suggested resolution", async () => {
    mockState.jobs = [singleFallbackJobPayload];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Unsupported resolution")).toBeInTheDocument();
    expect(screen.getByText(resolutionFallback.message)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "以 720p 重启任务 Unsupported resolution" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-format-failed/restart",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ resolution: "720p" })
      })
    );
  });

  test("shows playlist item resolution fallback and restarts item with suggested resolution", async () => {
    mockState.jobs = [playlistFallbackJobPayload];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    expect(screen.getByText(resolutionFallback.message)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "以 720p 重启 Part two" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-2/restart",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ resolution: "720p" })
      })
    );
  });

  test("shows automatic fallback without restart action for succeeded playlist item", async () => {
    mockState.jobs = [
      {
        ...playlistJobPayload,
        items: [
          {
            ...playlistJobPayload.items[0],
            requested_resolution: "1080p",
            fallback_resolution: "720p",
            fallback_reason: "requested_resolution_missing",
            resolution_fallback: automaticResolutionFallback
          },
          playlistJobPayload.items[1]
        ]
      }
    ];
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    expect(screen.getByText(automaticResolutionFallback.message)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "以 720p 重启 Part one" })).not.toBeInTheDocument();
  });

  test("shows original resolution retry for succeeded unselectable fallback", async () => {
    mockState.jobs = [
      {
        ...playlistJobPayload,
        items: [
          {
            ...playlistJobPayload.items[0],
            requested_resolution: "1080p",
            fallback_resolution: "720p",
            fallback_reason: "requested_resolution_unselectable",
            resolution_fallback: unselectableResolutionFallback
          },
          playlistJobPayload.items[1]
        ]
      }
    ];
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText("Playlist batch")).toBeInTheDocument();
    expect(screen.getByText(unselectableResolutionFallback.message)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "以 1080p 重试 Part one" }));

    expect(fetch).toHaveBeenCalledWith(
      "/api/jobs/job-playlist/items/item-playlist-1/restart",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ resolution: "1080p" })
      })
    );
  });
});
