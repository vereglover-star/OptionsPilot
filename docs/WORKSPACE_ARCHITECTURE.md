# Workspace Architecture

Workspace state is projected by `WorkspaceService` as a primitive-only view
model. The server persists selected tab, symbols, timeframe, indicators,
sidebar state, recent symbols, layouts, and runtime preferences. The browser
continues to mirror chart drawings in localStorage; that domain is explicitly
listed as client-trapped until a safe import/migration exists.

Runtime settings control whether the previous workspace is adopted on launch.
The v1 API exposes the same workspace view to desktop, browser, and future
mobile clients. Partial workspace updates are merged server-side so a smaller
client cannot erase fields it does not understand.

The tray and command surfaces use action IDs rather than hard-coded business
logic. A future native client can render the same action/view-model contracts
with different controls.
