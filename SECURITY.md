# Security policy

## Supported versions

Security fixes go into the latest released minor version.

## Reporting a vulnerability

Please email **konkomaji@gmail.com** with "opentoonz-mcp security" in the subject. Do not open a public issue. Include steps to reproduce and the affected version (from the `opentoonz_info` tool). I aim to reply within 7 days.

## What the server does on your machine

Knowing this helps you judge reports and deployments:

- **Runs locally over stdio only.** It opens no network ports.
- **Runs two OpenToonz programs.** It starts `OpenToonz.exe` (with a generated `.toonzscript`, or to open a scene) and `tcomposer.exe` from your OpenToonz install. It also runs `ffmpeg` for MP4/GIF output.
- **Writes files in a fixed set of places:**
  - the OpenToonz sandbox project: `scenes/`, `drawings/<scene>/`
  - its work folder (`OTMCP_WORK`)
- **Reads files you pass to `import_image` / `import_svg`.**
- **Builds scripts safely.** Generated scripts embed file paths as JSON-escaped string literals. Scene and layer names are reduced to `[A-Za-z0-9_-]`.

Treat an MCP client that can call these tools as having the file access listed above.
