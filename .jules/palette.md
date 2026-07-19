## 2024-05-24 - Consistent Feedback Colors in CLI

**Learning:** CLI tools benefit immensely from consistent, color-coded feedback (like rich's formatting) instead of plain stderr output. It makes success and failure states instantly recognizable. Emojis also help users parse logs quickly.
**Action:** When updating CLI output, always prefer styled text (`rprint`) with appropriate semantic colors (green for success, red for errors) and clear symbols over plain `print`.
