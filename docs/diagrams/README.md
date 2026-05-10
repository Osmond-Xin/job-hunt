# Diagrams

Source files for diagrams embedded in the project README. Edit the `.mmd`
source, then regenerate the `.svg` output via the configured MCP server.

## Toolchain

The chosen MCP server is [`mcp-mermaid`](https://github.com/hustcc/mcp-mermaid)
(npm package `mcp-mermaid`, unscoped).
It is wired to this project via `.mcp.json` at the repo root. To activate:

1. Install Node 18+ and ensure `npx` is on `PATH`.
2. Restart Claude Code so the MCP server registered in `.mcp.json` loads.
3. The `mcp-mermaid` tool will appear under the MCP tools list with names like
   `mcp__mermaid__generate` (exact name surfaces at runtime).

## Files

| Source            | Output                  | Embedded in    |
| ----------------- | ----------------------- | -------------- |
| `closed-loop.mmd` | `closed-loop.svg`       | `README.md`    |

## Regenerating an SVG

After editing a `.mmd` file, ask Claude Code:

> "Regenerate `docs/diagrams/closed-loop.svg` from `docs/diagrams/closed-loop.mmd`
> using the mcp-mermaid server, theme=`default`, output=`svg`."

Claude will call the MCP tool with the source content and the agreed theme,
and write the resulting SVG back to disk.

Commit both `.mmd` source and `.svg` output together so the diagram and its
source stay in lockstep.

## Style choices

- Theme: `default` (light, GitHub-friendly). `dark` is acceptable for dark-mode
  README mirrors.
- Output: `svg` — scales cleanly on GitHub, no rasterization artifacts.
- Backgrounds: leave transparent so the diagram works in both light and dark
  GitHub themes.

## Why an MCP server, not raw mermaid in the README

GitHub does render fenced ` ```mermaid ` blocks natively, but:

- A committed SVG is portable to non-GitHub readers (npm, PyPI, internal mirrors).
- The MCP server validates syntax before producing output, so a broken `.mmd`
  never lands in `README.md`.
- Multi-theme exports (light + dark) are one-tool-call away.
