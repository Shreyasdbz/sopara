# Sopara web foundation

This is the build-only TanStack Start SPA for Sopara. FastAPI/Python owns all production APIs, authentication enforcement, commands, SSE, persistence, and static delivery.

The production image copies `.output/public` and contains no Node or Bun runtime.

## Component source

To add components to your app, run the following command:

```bash
bunx --bun shadcn@latest add button
```

`components.json` must continue to resolve Base UI and Hugeicons. Radix and mixed primitive bases are prohibited.

## Checks

```bash
bun install --frozen-lockfile
bun run check
bun run build
```
