# Changelog

Each release is a git tag, `vX.Y.Z`. Issue numbers refer to this repo unless they name another.

## 0.1.0

- **Its own repo.** opcua-mcp, with the Water Treatment Plant simulator, moved out of smslavin/mcp-servers, with its history, so it can be versioned and tagged. The code is unchanged from mcp-servers c37ae66.
- Already there before the move: tools declared read-only, the simulator's fault injection HTTP port (`FAULT_HTTP_PORT`), and `opcua_discover.py` for galaxy onboarding.
