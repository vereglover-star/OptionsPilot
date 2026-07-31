# OptionsPilot V0.8 Platform Foundation

The application layer is the reusable center of the product. `services/`,
runtime contracts, notification routing, synchronization metadata, and view
models do not import FastAPI, Starlette, pywebview, tray libraries, or browser
assets.

The desktop shell is one host adapter. The v1 API is one transport adapter. A
future Flutter/native client consumes the same DTOs and capability negotiation
without importing either adapter.

V0.8 provides:

- versioned REST and WebSocket contracts;
- standard envelopes and request IDs;
- anonymous local request context with future authentication seams;
- host/client capability negotiation;
- durable notification events and platform-neutral action models;
- a local synchronization provider and classified sync inventory;
- one background runtime coordinator and explicit shutdown ownership;
- architecture tests preventing transport/UI coupling.

Cloud synchronization, login, remote push, and mobile code remain future work.
